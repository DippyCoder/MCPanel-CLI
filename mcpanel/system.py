"""JDK detection, system info, version + update check — ports of detect-jdk,
get-system-info, get-version and check-update in main.js."""

import os
import re
import shutil
import subprocess
import sys

from . import paths
from .http import fetch_json
from .util import compare_versions
from . import __version__


def _java_exe():
    return "java.exe" if sys.platform == "win32" else "java"


def _javac_exe():
    return "javac.exe" if sys.platform == "win32" else "javac"


def has_compiler(java_path):
    """Whether `java_path` is part of a full JDK (has javac alongside it) as
    opposed to a JRE-only install. Many distros (Fedora/Debian/Ubuntu) split
    packages this way — e.g. Fedora's `java-21-openjdk` is JRE-only; the
    compiler lives in the separate `java-21-openjdk-devel` package."""
    real = os.path.realpath(java_path) if os.path.exists(java_path) else java_path
    return os.path.isfile(os.path.join(os.path.dirname(real), _javac_exe()))


def _windows_jdk_roots():
    """Return candidate JVM install root directories on Windows."""
    roots = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(env)
        if not base:
            continue
        for vendor in ("Java", "Eclipse Adoptium", "Eclipse Foundation",
                       "Microsoft", "Amazon Corretto", "Azul Systems", "BellSoft",
                       "SapMachine", "ojdkbuild"):
            roots.append(os.path.join(base, vendor))
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        roots.append(java_home)
    return roots


def detect_jdk():
    """Find installed Java executables."""
    exe = _java_exe()
    which = shutil.which(exe)
    if sys.platform == "win32":
        search_roots = _windows_jdk_roots()
        candidates = [which or exe]
    elif sys.platform == "darwin":
        search_roots = ["/Library/Java/JavaVirtualMachines", "/usr/local/lib/jvm"]
        candidates = [which or exe, "/usr/bin/java"]
    else:
        search_roots = ["/usr/lib/jvm", "/usr/local/lib/jvm", "/opt/jdk", "/opt/java"]
        candidates = [which or exe, "/usr/bin/java", "/usr/local/bin/java"]

    # Expand JVM install roots
    expanded = []
    for c in candidates:
        if c:
            expanded.append(c)
    for root in search_roots:
        if root and os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                # macOS: <root>/<name>/Contents/Home/bin/java
                # Linux/Windows: <root>/<name>/bin/java
                for rel in (os.path.join("Contents", "Home", "bin", exe), os.path.join("bin", exe)):
                    jbin = os.path.join(root, name, rel)
                    if os.path.isfile(jbin):
                        expanded.append(jbin)
                        break

    found, seen = [], set()
    for p in expanded:
        rp = os.path.realpath(p) if os.path.exists(p) else p
        if rp in seen:
            continue
        try:
            out = subprocess.run([p, "-version"], capture_output=True, text=True, timeout=10)
        except Exception:
            continue
        if out.returncode == 0:
            seen.add(rp)
            m = re.search(r'version "([^"]+)"', (out.stderr or "") + (out.stdout or ""))
            found.append({
                "path": p,
                "version": m.group(1) if m else "Unknown",
                "hasCompiler": has_compiler(p),
            })
    return found


def _feature_version(version_str):
    """Parse a `java -version` string into a single feature-version int.
    Handles both modern ("17.0.9", "25.0.3") and legacy ("1.8.0_392") forms."""
    s = str(version_str or "")
    m = re.match(r"^1\.(\d+)", s)  # legacy "1.8.0_392" style (Java 8 and older)
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d+)", s)
    return int(m.group(1)) if m else None


# The most broadly recommended/tested LTS release for compiling Spigot across
# the range of MC versions still commonly built — preferred over other
# in-range JDKs (including newer non-LTS ones) when both satisfy the
# requirement, since those get far less real-world BuildTools mileage.
_PREFERRED_JDK_MAJOR = 21


def find_compatible_jdk(min_major, max_major=None, jdks=None, require_compiler=False,
                         prefer=_PREFERRED_JDK_MAJOR):
    """Fetch all detected JDKs and filter down to the ones actually usable
    for this requirement — feature version within [min_major, max_major],
    and (if `require_compiler`) not a JRE-only install. None if nothing
    detected qualifies.

    Among whatever survives that filter, `prefer` (default: JDK 21, the LTS
    most BuildTools setups are built/tested against) is picked over other
    in-range versions when present; otherwise falls back to the highest
    in-range version. Pass `prefer=None` to always just take the highest."""
    candidates = jdks if jdks is not None else detect_jdk()
    compatible = []
    for jdk in candidates:
        if require_compiler and not jdk.get("hasCompiler"):
            continue
        v = _feature_version(jdk.get("version"))
        if v is None or v < min_major:
            continue
        if max_major is not None and v > max_major:
            continue
        compatible.append((v, jdk["path"]))

    if not compatible:
        return None
    if prefer is not None:
        preferred = [c for c in compatible if c[0] == prefer]
        if preferred:
            return preferred[0][1]
    return max(compatible, key=lambda c: c[0])[1]


def required_java_range(software, version):
    """Best-effort (min, max) feature-version range needed for `software`
    `version`. `max` is None when only a minimum is known — running an
    already-compiled jar just needs a JDK >= min; Spigot is the one case
    with a hard upper bound too, because BuildTools enforces it itself at
    compile time. Returns None if the requirement can't be determined."""
    try:
        if software == "spigot":
            from . import buildtools
            return buildtools.required_java_range(version)
        if software == "velocity":
            return None
        manifest = fetch_json("https://launchermeta.mojang.com/mc/game/version_manifest_v2.json")
        info = next((v for v in manifest["versions"] if v["id"] == version), None)
        if not info:
            return None
        vdata = fetch_json(info["url"])
        major = vdata.get("javaVersion", {}).get("majorVersion")
        return (major, None) if major else None
    except Exception:
        return None


def jdk_compatibility(software, version):
    """Detected JDKs annotated with whether each can actually be used for
    `software` `version` — backs an explicit JDK picker (CLI wizard and the
    MCPanel dropdown) instead of leaving the choice to silent auto-detection.
    This matters most for Spigot: BuildTools enforces an exact compile-time
    Java range that also happens to be what the compiled result needs to
    run, so picking the wrong JDK fails loudly and only after minutes of
    build time — better to show the user which ones actually work upfront."""
    rng = required_java_range(software, version)
    require_compiler = (software == "spigot")
    jdks = detect_jdk()

    annotated = []
    for jdk in jdks:
        v = _feature_version(jdk.get("version"))
        compatible, reason = True, None
        if require_compiler and not jdk.get("hasCompiler"):
            compatible, reason = False, "JRE only — no compiler (javac)"
        elif rng and v is not None:
            lo, hi = rng
            if v < lo or (hi is not None and v > hi):
                compatible = False
                reason = f"needs Java {lo}" + (f"–{hi}" if hi else "+")
        annotated.append({**jdk, "compatible": compatible, "reason": reason})

    recommended = None
    if rng:
        recommended = find_compatible_jdk(*rng, jdks=jdks, require_compiler=require_compiler)

    return {
        "range": {"min": rng[0], "max": rng[1]} if rng else None,
        "jdks": annotated,
        "recommended": recommended,
    }


def _total_ram_bytes():
    if sys.platform == "win32":
        import ctypes
        class _MEMSTATUS(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        stat = _MEMSTATUS()
        stat.dwLength = ctypes.sizeof(stat)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return stat.ullTotalPhys
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None


def get_system_info():
    total_ram = None
    try:
        total_ram = _total_ram_bytes()
    except Exception:
        pass
    available_storage = None
    total_storage = None
    try:
        import shutil
        target = paths.SERVERS_DIR if os.path.isdir(paths.SERVERS_DIR) else paths.USER_DATA
        usage = shutil.disk_usage(target)
        available_storage = usage.free
        total_storage = usage.total
    except Exception:
        pass
    return {"totalRam": total_ram, "availableStorage": available_storage, "totalStorage": total_storage}


def get_version():
    return __version__


def check_update():
    current = __version__
    try:
        data = fetch_json("https://api.github.com/repos/DippyCoder/MCPanel/releases/latest")
        latest = re.sub(r"^v", "", str(data.get("tag_name", "")))
        return {
            "current": current,
            "latest": latest,
            "hasUpdate": compare_versions(latest, current) > 0,
            "url": data.get("html_url"),
        }
    except Exception:
        return {"current": current, "latest": None, "hasUpdate": False}
