"""JDK detection, system info, version + update check — ports of detect-jdk,
get-system-info, get-version and check-update in main.js."""

import os
import re
import shutil
import subprocess

from . import paths
from .http import fetch_json
from .util import compare_versions
from . import __version__


def detect_jdk():
    """Find installed Java executables. Mirrors detect-jdk's intent but tuned
    for Linux (the original also listed Windows paths)."""
    candidates = [
        shutil.which("java") or "java",
        "/usr/bin/java",
        "/usr/local/bin/java",
        "/usr/lib/jvm",
    ]
    # Expand JVM install roots
    expanded = []
    for c in candidates:
        if c and os.path.isdir(c):
            for name in sorted(os.listdir(c)):
                jbin = os.path.join(c, name, "bin", "java")
                if os.path.isfile(jbin):
                    expanded.append(jbin)
        elif c:
            expanded.append(c)

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
            found.append({"path": p, "version": m.group(1) if m else "Unknown"})
    return found


def get_system_info():
    total_ram = None
    try:
        total_ram = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError):
        pass
    available_storage = None
    total_storage = None
    try:
        st = os.statvfs(paths.SERVERS_DIR if os.path.isdir(paths.SERVERS_DIR) else paths.USER_DATA)
        available_storage = st.f_bavail * st.f_frsize
        total_storage = st.f_blocks * st.f_frsize
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
