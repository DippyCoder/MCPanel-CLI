"""Client-side helpers for talking to running server supervisors.

In the Electron app the main process stays alive and keeps a `runningServers`
map in memory. A CLI invocation is short-lived, so instead each running server
is owned by a detached supervisor daemon (see supervisor.py). State lives on
disk under run/:

    run/<id>.json        ← {supervisorPid, javaPid, port, started}
    run/<id>.sock        ← unix control socket (cmd / kill / status)
    run/<id>.log.jsonl   ← one JSON log record per line {time,text,type}
"""

import json
import os
import socket
import time

from . import paths


def state_path(server_id):
    return os.path.join(paths.RUN_DIR, server_id + ".json")


def sock_path(server_id):
    return os.path.join(paths.RUN_DIR, server_id + ".sock")


def log_path(server_id):
    return os.path.join(paths.RUN_DIR, server_id + ".log.jsonl")


def boot_err_path(server_id):
    return os.path.join(paths.RUN_DIR, server_id + ".boot.err")


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def read_state(server_id):
    try:
        with open(state_path(server_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def is_running(server_id):
    st = read_state(server_id)
    if not st:
        return False
    if _pid_alive(st.get("javaPid")):
        return True
    # Stale state from a crashed supervisor — clean it up.
    cleanup_state(server_id)
    return False


def cleanup_state(server_id):
    for p in (state_path(server_id), sock_path(server_id)):
        try:
            os.remove(p)
        except OSError:
            pass


def send_request(server_id, obj, timeout=5.0):
    """Send a single JSON request to the supervisor and return its JSON reply."""
    sp = sock_path(server_id)
    if not os.path.exists(sp):
        return {"ok": False, "error": "Not running"}
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(sp)
        s.sendall((json.dumps(obj) + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
        if not data:
            return {"ok": False, "error": "No response"}
        return json.loads(data.decode("utf-8", "replace").splitlines()[0])
    except Exception as e:
        return {"ok": False, "error": str(e)}


def read_log(server_id):
    """Return the full structured log for the current run as a list of
    {time, text, type} records (mirrors get-server-log)."""
    out = []
    try:
        with open(log_path(server_id), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    out.append({"time": int(time.time() * 1000), "text": line, "type": "out"})
    except FileNotFoundError:
        pass
    return out
