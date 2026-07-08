"""Pure helpers ported from main.js (copyDirSync, parseStorageLimit,
getDirSize, parsJavaArgs, xmsFromRam, compareVersions, buildFileTree) plus a
couple of CLI conveniences (RAM normalisation, human sizes)."""

import os
import re
import shutil

from .config import MANIFEST_FILENAME

_DEFAULT_JAVA_ARGS = "-XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200"


def default_java_args():
    return _DEFAULT_JAVA_ARGS


def copy_dir(src, dest):
    """Recursive copy that skips profile.json and mcpanel.json — matches
    copyDirSync. The manifest is skipped so a duplicated/imported server gets
    its own fresh one instead of inheriting the source's id."""
    if not os.path.exists(src):
        return
    os.makedirs(dest, exist_ok=True)
    for entry in os.scandir(src):
        if entry.name in ("profile.json", MANIFEST_FILENAME):
            continue
        sp = os.path.join(src, entry.name)
        dp = os.path.join(dest, entry.name)
        if entry.is_dir():
            copy_dir(sp, dp)
        else:
            shutil.copy2(sp, dp)


def parse_storage_limit(value):
    """'20G' / '500MB' / '1024' → bytes, or None if unparseable."""
    if value is None:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB|K|M|G|T)?$", str(value).strip(), re.I)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "B").upper().rstrip("B")
    mult = {"": 1, "K": 1024, "M": 1048576, "G": 1073741824, "T": 1099511627776}
    return num * mult.get(unit, 1)


def get_dir_size(path):
    size = 0
    try:
        for entry in os.scandir(path):
            full = os.path.join(path, entry.name)
            if entry.is_dir():
                size += get_dir_size(full)
            else:
                try:
                    size += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return size


def parse_java_args(args_str):
    return [a for a in (args_str or "").split() if a]


def normalize_ram(value):
    """Accept 20480 (MB), '20480', '20480M', '20G' → canonical '<n>M'/'<n>G'.

    The MCPanel UI/examples pass plain megabytes (e.g. -ram 20480), which we
    store as an Xmx-ready string."""
    if value is None:
        return None
    s = str(value).strip()
    m = re.match(r"^(\d+)\s*(M|MB|G|GB)?$", s, re.I)
    if not m:
        return s
    num = m.group(1)
    unit = (m.group(2) or "M").upper()[0]  # M or G
    return f"{num}{unit}"


def xms_from_ram(ram):
    m = re.match(r"^(\d+)(M|G)$", str(ram or ""), re.I)
    if not m:
        return "512M"
    mb = int(m.group(1)) * 1024 if m.group(2).upper() == "G" else int(m.group(1))
    return f"{min(512, mb)}M"


def compare_versions(a, b):
    pa = [int(x or 0) for x in re.sub(r"^v", "", str(a)).split(".") if x.isdigit() or x == ""]
    pb = [int(x or 0) for x in re.sub(r"^v", "", str(b)).split(".") if x.isdigit() or x == ""]
    for i in range(max(len(pa), len(pb))):
        diff = (pa[i] if i < len(pa) else 0) - (pb[i] if i < len(pb) else 0)
        if diff != 0:
            return diff
    return 0


def build_file_tree(dir_path, root_path, depth=0):
    if depth > 10:
        return []
    try:
        entries = list(os.scandir(dir_path))
    except OSError:
        return []
    items = []
    for entry in entries:
        full = os.path.join(dir_path, entry.name)
        rel = os.path.relpath(full, root_path)
        if entry.is_dir():
            items.append({
                "name": entry.name, "type": "dir", "path": rel,
                "children": build_file_tree(full, root_path, depth + 1),
            })
        else:
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            items.append({"name": entry.name, "type": "file", "path": rel, "size": size})
    items.sort(key=lambda a: (0 if a["type"] == "dir" else 1, a["name"].lower()))
    return items


def resolve_jar(srv):
    """Pick the server jar — server.jar, else the only .jar present.
    Returns (jar_path, error_message)."""
    d = srv["dir"]
    jar = os.path.join(d, "server.jar")
    if os.path.exists(jar):
        return jar, None
    try:
        jars = [f for f in os.listdir(d) if f.endswith(".jar")]
    except OSError:
        jars = []
    if len(jars) == 1:
        return os.path.join(d, jars[0]), None
    if len(jars) == 0:
        return None, "No .jar found in server directory"
    return None, "Multiple .jars found. Please rename one to server.jar"


def build_java_command(srv, jar):
    java_path = srv.get("javaPath") or "java"
    ram = srv.get("ram")
    return [
        java_path,
        *parse_java_args(srv.get("javaArgs") or ""),
        f"-Xmx{ram}",
        f"-Xms{xms_from_ram(ram)}",
        "-jar", jar, "nogui",
    ]


def human_size(num):
    num = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"
