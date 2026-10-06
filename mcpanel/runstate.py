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
import re
import socket
import sys
import time

from . import paths

# Unix-domain sockets are used on POSIX; TCP loopback on Windows.
_USE_UNIX_SOCKET = sys.platform != "win32" and hasattr(socket, "AF_UNIX")


def _run_file(server_id, suffix):
    # Ids arrive straight from the API (`stop -id ...`); one containing a path
    # separator would address files outside run/.
    if not paths.is_valid_id(server_id):
        raise ValueError(f"Invalid server id: {server_id!r}")
    return os.path.join(paths.RUN_DIR, server_id + suffix)


def state_path(server_id):
    return _run_file(server_id, ".json")


def sock_path(server_id):
    return _run_file(server_id, ".sock")


def port_path(server_id):
    """Path to the TCP control port file (Windows only)."""
    return _run_file(server_id, ".port")


def log_path(server_id):
    return _run_file(server_id, ".log.jsonl")


def boot_err_path(server_id):
    return _run_file(server_id, ".boot.err")


def lock_path(server_id):
    """Held by the supervisor for its whole lifetime (contains its pid), so a
    second supervisor for the same server can't start while one is alive —
    including one that is still tearing down after java exited."""
    return _run_file(server_id, ".lock")


def _pid_alive(pid):
    if not pid:
        return False
    try:
        if sys.platform == "win32":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return False
            code = ctypes.c_ulong(0)
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return code.value == STILL_ACTIVE
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def read_state(server_id):
    try:
        with open(state_path(server_id), "r", encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else None
    except Exception:
        return None


def write_state(server_id, state):
    """Atomic, so a reader never sees a half-written state file."""
    path = state_path(server_id)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def supervisor_pid(server_id):
    try:
        with open(lock_path(server_id), "r", encoding="utf-8") as f:
            return int(f.read().strip() or 0) or None
    except (OSError, ValueError):
        return None


def supervisor_alive(server_id):
    return _pid_alive(supervisor_pid(server_id))


def wait_supervisor_exit(server_id, timeout=10.0):
    """Block until the previous supervisor (if any) has finished tearing down.
    Returns False if it is still alive after `timeout`."""
    deadline = time.time() + timeout
    while supervisor_alive(server_id):
        if time.time() >= deadline:
            return False
        time.sleep(0.15)
    return True


def is_running(server_id):
    st = read_state(server_id)
    if not st:
        return False
    if _pid_alive(st.get("javaPid")):
        return True
    # Java is gone. If its supervisor is still alive it is mid-teardown and
    # will clean up after itself — deleting its files from here raced with
    # that and, on restart, with the *next* supervisor's freshly written ones.
    if not _pid_alive(st.get("supervisorPid")):
        cleanup_state(server_id)
    return False


def cleanup_state(server_id):
    if not paths.is_valid_id(server_id):
        return
    cpu_sample = _run_file(server_id, ".cpu")
    for p in (state_path(server_id), sock_path(server_id), port_path(server_id), cpu_sample):
        try:
            os.remove(p)
        except OSError:
            pass


def purge(server_id):
    """Remove every run/<id>.* file (state, logs, archived sessions, boot
    error, lock) — for a server that has been deleted."""
    if not paths.is_valid_id(server_id):
        return
    # Exact suffixes, so purging "a" can't take "a.b"'s files with it.
    own = re.compile(re.escape(server_id)
                     + r"\.(json|sock|port|cpu|lock|boot\.err|log(\.\d+)?\.jsonl)$")
    try:
        names = os.listdir(paths.RUN_DIR)
    except OSError:
        return
    for name in names:
        if own.match(name):
            try:
                os.remove(os.path.join(paths.RUN_DIR, name))
            except OSError:
                pass


def kill_orphan(server_id):
    """Last resort for a java process whose supervisor died (e.g. kill -9):
    nothing is listening on the control socket any more, so signal java
    directly. Returns True if a kill was sent."""
    st = read_state(server_id)
    if not st or _pid_alive(st.get("supervisorPid")):
        return False
    pid = st.get("javaPid")
    if not _pid_alive(pid):
        return False
    try:
        if sys.platform == "win32":
            import ctypes
            PROCESS_TERMINATE = 0x0001
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
            if not handle:
                return False
            ctypes.windll.kernel32.TerminateProcess(handle, 1)
            ctypes.windll.kernel32.CloseHandle(handle)
        else:
            import signal
            os.kill(int(pid), signal.SIGKILL)
    except (OSError, ValueError):
        return False
    deadline = time.time() + 5
    while _pid_alive(pid) and time.time() < deadline:
        time.sleep(0.1)
    cleanup_state(server_id)
    return True


def send_request(server_id, obj, timeout=5.0):
    """Send a single JSON request to the supervisor and return its JSON reply."""
    try:
        if _USE_UNIX_SOCKET:
            sp = sock_path(server_id)
            if not os.path.exists(sp):
                return {"ok": False, "error": "Not running"}
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            addr = sp
        else:
            pp = port_path(server_id)
            if not os.path.exists(pp):
                return {"ok": False, "error": "Not running"}
            with open(pp, "r", encoding="utf-8") as f:
                addr = ("127.0.0.1", int(f.read().strip()))
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.settimeout(timeout)
            s.connect(addr)
            s.sendall((json.dumps(obj) + "\n").encode("utf-8"))
            data = b""
            while b"\n" not in data:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        finally:
            s.close()
        if not data:
            return {"ok": False, "error": "No response"}
        return json.loads(data.decode("utf-8", "replace").splitlines()[0])
    except Exception as e:
        return {"ok": False, "error": str(e)}


def read_log_file(path):
    """Read a .log.jsonl file and return a list of {time, text, type} records."""
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
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


def read_log(server_id):
    """Return the full structured log for the current/last run."""
    return read_log_file(log_path(server_id))


_SESSION_KEEP = 5


def session_log_paths(server_id):
    """Return archived session log paths sorted newest-first."""
    prefix = server_id + ".log."
    suffix = ".jsonl"
    result = []
    try:
        for name in os.listdir(paths.RUN_DIR):
            if name.startswith(prefix) and name.endswith(suffix):
                mid = name[len(prefix):-len(suffix)]
                if mid.isdigit():
                    result.append((int(mid), os.path.join(paths.RUN_DIR, name)))
    except OSError:
        pass
    return [p for _, p in sorted(result, reverse=True)]


def rotate_log(server_id):
    """Archive current log and prune old sessions, keeping _SESSION_KEEP total."""
    current = log_path(server_id)
    if os.path.exists(current):
        ts = int(time.time() * 1000)
        archived = os.path.join(paths.RUN_DIR, f"{server_id}.log.{ts}.jsonl")
        try:
            os.rename(current, archived)
        except OSError:
            pass
    # Keep at most SESSION_KEEP-1 archives (the new active session will be the Nth)
    for excess in session_log_paths(server_id)[_SESSION_KEEP - 1:]:
        try:
            os.remove(excess)
        except OSError:
            pass
