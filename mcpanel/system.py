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
            found.append({"path": p, "version": m.group(1) if m else "Unknown"})
    return found


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
