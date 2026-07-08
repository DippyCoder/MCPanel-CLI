"""SpigotMC BuildTools integration.

Spigot ships no prebuilt server jars — the only official way to get one is to
compile it locally with SpigotMC's BuildTools.jar. This module keeps a copy
of BuildTools.jar in the CLI's own install directory (NOT the shared
MCPanel/mcpanel-cli user-data folder — this is CLI-internal tooling, not
server or panel data) and drives it to produce a `server.jar` when a Spigot
server is created.

Fail-safe contract:
  - If BuildTools.jar already exists on disk, we never touch it again. Keeping
    it current is BuildTools' own job (it re-downloads/re-execs itself when
    Jenkins publishes a newer build) — we only need to get it there once.
  - If it's missing (fresh install, or wiped by a CLI reinstall since it lives
    inside the installed package), we try to download it up to 3 times.
  - If all 3 attempts fail, we give up and report version "none" so callers
    (the `buildtools version`/`update` commands, and the MCPanel desktop app)
    can surface a clear "BuildTools could not be loaded" state instead of
    silently retrying forever.
"""

import os
import shutil
import subprocess
import tempfile
import time

from .http import download_file, fetch_json

# The mcpanel package's own directory — "the CLI app folder", as opposed to
# paths.USER_DATA which is shared with the MCPanel desktop app.
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
MODULES_DIR = os.path.join(_PKG_DIR, "modules")
BUILDTOOLS_JAR = os.path.join(MODULES_DIR, "BuildTools.jar")

_DOWNLOAD_URL = "https://hub.spigotmc.org/jenkins/job/BuildTools/lastSuccessfulBuild/artifact/target/BuildTools.jar"
_VERSION_INFO_URL = "https://hub.spigotmc.org/versions/{version}.json"
_HELP_URL = "https://www.spigotmc.org/wiki/buildtools/"

_ATTEMPTS = 3
_RETRY_BACKOFF = (1, 2)  # seconds to wait between attempts 1→2 and 2→3

# Don't let the automatic startup hook re-attempt 3 downloads on every single
# command while offline — a manual `buildtools version`/`update` always retries
# immediately regardless of this cooldown.
_STARTUP_RETRY_COOLDOWN = 3600


def _jar_ok():
    try:
        return os.path.getsize(BUILDTOOLS_JAR) > 0
    except OSError:
        return False


def _last_failure_path():
    return os.path.join(MODULES_DIR, ".last_failure")


def _record_failure():
    try:
        os.makedirs(MODULES_DIR, exist_ok=True)
        with open(_last_failure_path(), "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def _time_since_last_failure():
    try:
        with open(_last_failure_path(), "r", encoding="utf-8") as f:
            return time.time() - float(f.read().strip())
    except (OSError, ValueError):
        return float("inf")


def _clear_failure():
    try:
        os.remove(_last_failure_path())
    except OSError:
        pass


def _download_with_retries(progress=None):
    """Attempt the download up to _ATTEMPTS times. Returns None on success,
    or an error string on total failure."""
    os.makedirs(MODULES_DIR, exist_ok=True)
    tmp_path = BUILDTOOLS_JAR + ".part"
    last_err = None
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            if progress:
                progress(0, f"Downloading BuildTools… (attempt {attempt}/{_ATTEMPTS})")
            download_file(
                _DOWNLOAD_URL, tmp_path,
                (lambda p, a=attempt: progress(p, f"Downloading BuildTools… {p}% (attempt {a}/{_ATTEMPTS})"))
                if progress else None,
            )
            if os.path.getsize(tmp_path) == 0:
                raise RuntimeError("downloaded file was empty")
            os.replace(tmp_path, BUILDTOOLS_JAR)
            _clear_failure()
            return None
        except Exception as e:
            last_err = e
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            if attempt < _ATTEMPTS:
                time.sleep(_RETRY_BACKOFF[min(attempt - 1, len(_RETRY_BACKOFF) - 1)])

    _record_failure()
    msg = f"Could not download BuildTools after {_ATTEMPTS} attempts: {last_err}"
    if progress:
        progress(100, msg)
    return msg


def ensure_buildtools(progress=None, force=False):
    """Make sure BuildTools.jar is on disk.

    If it already exists, this is a no-op (updating it is BuildTools' own
    job) unless `force=True`. Returns None on success or {"error": ...}.
    """
    if not force and _jar_ok():
        return None
    err = _download_with_retries(progress)
    if err:
        return {"error": err}
    return None


def startup_check():
    """Best-effort auto-install hook — call once at CLI startup.

    No-op if BuildTools is already present. If it's missing, retries are
    cooled down so a persistently offline machine doesn't retry 3 downloads
    on every single command.
    """
    try:
        if _jar_ok():
            return
        if _time_since_last_failure() < _STARTUP_RETRY_COOLDOWN:
            return
        ensure_buildtools()
    except Exception:
        pass


def buildtools_version(progress=None):
    """Backs `mcpanel buildtools version` (and the MCPanel desktop app's
    status check). Always attempts an install if missing — this is an
    explicit, user-facing check, so the startup cooldown doesn't apply."""
    if _jar_ok():
        return {"version": "installed", "path": BUILDTOOLS_JAR}
    err = ensure_buildtools(progress)
    if err:
        return {"version": "none", "error": err["error"], "helpUrl": _HELP_URL}
    return {"version": "installed", "path": BUILDTOOLS_JAR}


def buildtools_update(progress=None):
    """Backs `mcpanel buildtools update` — force a fresh download regardless
    of whether a jar is already present."""
    err = ensure_buildtools(progress, force=True)
    if err:
        return {"version": "none", "error": err["error"], "helpUrl": _HELP_URL}
    return {"version": "installed", "path": BUILDTOOLS_JAR}


_OUTPUT_TAIL = 25  # lines of BuildTools output to keep for the error message on failure


def required_java_range(version):
    """Query SpigotMC's per-version metadata for the Java feature-version
    range BuildTools enforces for `version`. Returns (min, max) or None if
    unavailable (e.g. offline, or a version with no published metadata)."""
    try:
        data = fetch_json(_VERSION_INFO_URL.format(version=version))
        lo, hi = data["javaVersions"]
        return (lo - 44, hi - 44)  # class-file major version -> JDK feature version
    except Exception:
        return None


def _java_home_from_path(java_path):
    """Best-effort: derive a JDK's home directory from its `java` executable
    path (".../bin/java" or "...\\bin\\java.exe" -> the dir above "bin").
    Resolves symlinks first since e.g. /usr/bin/java is typically a chain of
    `alternatives` symlinks to the real JDK, not the JDK's own bin dir.
    Returns None for a bare command name like "java" (PATH-resolved — we
    don't know its real location without invoking it)."""
    if not java_path or not os.path.isabs(java_path):
        return None
    real = os.path.realpath(java_path)
    bin_dir = os.path.dirname(real)
    if os.path.basename(bin_dir) != "bin":
        return None
    return os.path.dirname(bin_dir)


def _build_env(java_path):
    """BuildTools shells out to its own bundled Maven to actually compile,
    and that subprocess resolves its OWN JDK via JAVA_HOME/PATH rather than
    inheriting whichever `java` binary launched BuildTools.jar. Without this,
    picking a specific/auto-detected JDK for BuildTools itself can still
    silently compile with a different (often older, incompatible) JDK found
    on the ambient JAVA_HOME/PATH — surfacing as a confusing Maven failure
    like "release version 17 not supported" instead of BuildTools' own clear
    Java-version-mismatch message."""
    env = os.environ.copy()
    home = _java_home_from_path(java_path)
    if home:
        env["JAVA_HOME"] = home
        env["PATH"] = os.path.join(home, "bin") + os.pathsep + env.get("PATH", "")
    return env


def build_spigot(version, server_dir, progress=None, java_path=None):
    """Compile Spigot `version` with BuildTools and drop the result at
    `<server_dir>/server.jar`. Returns None on success, or {"error": ...}.

    `java_path` lets callers pin a specific JDK. When it's left as the
    default ("java" — i.e. no explicit choice was made), we try to find an
    installed JDK that actually satisfies this version's required Java range
    ourselves, since each MC version needs a specific range (e.g. 17–21 for
    1.20.x) and the system's default `java` on PATH often isn't compatible.
    """
    err = ensure_buildtools(progress)
    if err:
        return err

    from . import system

    rng = required_java_range(version)
    java = java_path if java_path and java_path != "java" else None
    if not java and rng:
        match = system.find_compatible_jdk(*rng, require_compiler=True)
        if match:
            java = match
            if progress:
                progress(3, f"Auto-selected JDK {rng[0]}–{rng[1]} for Spigot {version}: {match}")
    java = java or java_path or shutil.which("java")
    if not java:
        return {"error": "Java not found on PATH — BuildTools requires a JDK to compile Spigot"}

    # Fail fast and clearly rather than let a JRE-only install (no javac —
    # common on Fedora/Debian/Ubuntu, which split the compiler into a
    # separate `-devel` package) run for minutes before Maven produces a
    # cryptic "release version N not supported" deep in the build log.
    if not system.has_compiler(java):
        if rng:
            lo, hi = rng
            range_hint = f" (needs Java {lo}" + (f"–{hi}" if hi else "+") + ")"
            # An example version for the install command below — prefer 21 if
            # it's actually in range, else the top of the range, matching the
            # same preference find_compatible_jdk uses.
            example_major = 21 if (hi is None or lo <= 21 <= hi) and lo <= 21 else (hi or lo)
        else:
            range_hint = ""
            example_major = 21
        return {"error": (
            f"'{java}' has no compiler (javac) — it looks like a JRE, not a full JDK{range_hint}. "
            "BuildTools needs a full JDK to compile Spigot. On Fedora/RHEL install the matching "
            f"'-devel' package (e.g. `sudo dnf install java-{example_major}-openjdk-devel`); on "
            f"Debian/Ubuntu install a JDK package (e.g. `sudo apt install openjdk-{example_major}-jdk`)."
        )}

    work_dir = tempfile.mkdtemp(prefix="mcpanel-buildtools-")
    try:
        shutil.copy2(BUILDTOOLS_JAR, os.path.join(work_dir, "BuildTools.jar"))

        cmd = [java, "-jar", "BuildTools.jar", "--rev", version, "--compile", "spigot"]
        if progress:
            progress(5, f"Building Spigot {version} with BuildTools (this can take a while)…")

        try:
            proc = subprocess.Popen(
                cmd, cwd=work_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=_build_env(java),
            )
        except OSError as e:
            return {"error": f"Failed to launch '{java}': {e}"}

        pct = 5
        tail = []
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                tail.append(line)
                if len(tail) > _OUTPUT_TAIL:
                    tail.pop(0)
            if progress and line:
                pct = min(pct + 1, 95)
                progress(pct, line[:100])
        proc.wait()

        if proc.returncode != 0:
            detail = "\n".join(tail) or "(no output captured)"
            return {"error": f"BuildTools exited with code {proc.returncode}:\n{detail}"}

        candidates = sorted(
            f for f in os.listdir(work_dir) if f.startswith("spigot-") and f.endswith(".jar")
        )
        if not candidates:
            return {"error": "BuildTools finished but produced no spigot-*.jar"}

        shutil.copy2(os.path.join(work_dir, candidates[-1]), os.path.join(server_dir, "server.jar"))
        if progress:
            progress(100, "Build complete!")
        return None
    except Exception as e:
        return {"error": str(e)}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
