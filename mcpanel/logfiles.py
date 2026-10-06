"""A server's own log files (`<server dir>/logs/`) — listing, reading, and
sharing them on mclo.gs.

These are the files the Minecraft server itself writes (latest.log plus the
rotated, gzipped `YYYY-MM-DD-N.log.gz` archives), not MCPanel's captured
console sessions in the run dir (see runstate.py / `mcpanel sessions`).

Every path a caller hands in is resolved against the logs folder and refused if
it lands outside it: MCPanel-WebUI forwards these calls from remote users, so a
`-file ../../server.properties` must not read anything but logs.
"""

import gzip
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .config import load_config, find_server
from .errors import fail
from .http import USER_AGENT

MCLOGS_API = "https://api.mclo.gs/1/log"

# mclo.gs upload budget. Anything beyond it is cut from the START of the log,
# so the paste always ends with the most recent output (where a crash is).
UPLOAD_MAX_LINES = 10_000
UPLOAD_MAX_BYTES = 25 * 1024 * 1024

# What `fetch logfile` returns to a GUI — same window as an upload, so the
# viewer shows exactly what the upload button would send.
READ_MAX_LINES = UPLOAD_MAX_LINES

_LOG_EXTS = (".log", ".log.gz", ".txt", ".txt.gz")


def _logs_dir(server_id):
    """(logs_dir, None) or (None, error document)."""
    srv = find_server(load_config(), server_id)
    if not srv:
        return None, fail("server_not_found")
    if not os.path.isdir(srv["dir"]):
        return None, fail("server_dir_missing", f"Server folder is missing: {srv['dir']}")
    return os.path.join(srv["dir"], "logs"), None


def _resolve(logs_dir, name):
    """The real path of `name` inside logs_dir, or None if it escapes the
    folder, isn't a regular file, or isn't a log."""
    if not name or "\0" in name:
        return None
    root = os.path.realpath(logs_dir)
    full = os.path.realpath(os.path.join(root, name))
    if os.path.commonpath([root, full]) != root or full == root:
        return None
    if not os.path.isfile(full) or not full.lower().endswith(_LOG_EXTS):
        return None
    return full


def _read_lines(path):
    opener = gzip.open if path.lower().endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r", "").splitlines()


def _tail_within(lines, max_lines, max_bytes):
    """The newest lines that fit both limits (oldest dropped first)."""
    kept = lines[-max_lines:] if len(lines) > max_lines else list(lines)
    size = sum(len(l.encode("utf-8")) + 1 for l in kept)
    start = 0
    while size > max_bytes and start < len(kept):
        size -= len(kept[start].encode("utf-8")) + 1
        start += 1
    return kept[start:]


def list_log_files(args, progress=None):
    logs_dir, err = _logs_dir(args.id)
    if err:
        return err
    files = []
    for dirpath, _dirs, names in os.walk(logs_dir):
        for name in names:
            if not name.lower().endswith(_LOG_EXTS):
                continue
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, logs_dir).replace(os.sep, "/")
            files.append({"name": rel, "size": st.st_size, "modified": int(st.st_mtime * 1000)})
    # latest.log first, then newest archive first.
    files.sort(key=lambda f: (f["name"] != "latest.log", -f["modified"]))
    return {"files": files}


def read_log_file(args, progress=None):
    logs_dir, err = _logs_dir(args.id)
    if err:
        return err
    name = getattr(args, "file", None) or "latest.log"
    path = _resolve(logs_dir, name)
    if not path:
        return fail("log_not_found", f"Log file not found: {name}")
    try:
        lines = _read_lines(path)
    except (OSError, EOFError, gzip.BadGzipFile) as e:
        return fail("log_unreadable", f"Could not read {name}: {e}")
    total = len(lines)
    if total > READ_MAX_LINES:
        lines = lines[-READ_MAX_LINES:]
    return {"name": name, "lines": lines, "totalLines": total, "truncated": total > len(lines)}


def upload_log(args, progress=None):
    logs_dir, err = _logs_dir(args.id)
    if err:
        return err
    name = getattr(args, "file", None) or "latest.log"
    path = _resolve(logs_dir, name)
    if not path:
        return fail("log_not_found", f"Log file not found: {name}")
    try:
        lines = _read_lines(path)
    except (OSError, EOFError, gzip.BadGzipFile) as e:
        return fail("log_unreadable", f"Could not read {name}: {e}")
    if not any(l.strip() for l in lines):
        return fail("log_empty", f"{name} is empty — nothing to upload.")

    kept = _tail_within(lines, UPLOAD_MAX_LINES, UPLOAD_MAX_BYTES)
    content = "\n".join(kept)
    body = urllib.parse.urlencode({"content": content}).encode("utf-8")
    req = urllib.request.Request(MCLOGS_API, data=body, headers={
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            data = json.loads(res.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8", "replace")).get("error")
        except Exception:
            msg = None
        return fail("upload_failed", f"mclo.gs rejected the upload: {msg or f'HTTP {e.code}'}")
    except Exception as e:
        return fail("upload_failed", f"Could not reach mclo.gs: {e}")
    if not data.get("success") or not data.get("url"):
        return fail("upload_failed", f"mclo.gs rejected the upload: {data.get('error') or 'unknown error'}")

    return {
        "success": True,
        "url": data["url"],
        "id": data.get("id"),
        "raw": data.get("raw"),
        "file": name,
        "lines": len(kept),
        "totalLines": len(lines),
        "truncated": len(kept) < len(lines),
    }
