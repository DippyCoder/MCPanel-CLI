"""Server controllers — 1:1 ports of the server-related IPC handlers in
main.js. Each returns a plain dict in the same shape the handler returned, so
the `api` command family can emit it verbatim as JSON.

Signatures take the parsed argparse namespace plus an optional `progress`
callback `progress(percent, status)` used only for human-mode feedback.
"""

import os
import re
import subprocess
import sys
import time

from . import paths, runstate
from .config import load_config, save_config, find_server
from .versions import resolve_download_url, SOFTWARE
from .http import download_file
from .ping import ping_server
from . import util

# Parent directory of the mcpanel package — used to set PYTHONPATH when
# spawning the supervisor subprocess so it can import mcpanel regardless of cwd.
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _now_ms():
    return int(time.time() * 1000)


def _unique_id(prefix, base_dir):
    sid = f"{prefix}_{_now_ms()}"
    while os.path.exists(os.path.join(base_dir, sid)):
        time.sleep(0.002)
        sid = f"{prefix}_{_now_ms()}"
    return sid


def _write_port(props_file, port):
    props = ""
    if os.path.exists(props_file):
        with open(props_file, "r", encoding="utf-8") as f:
            props = f.read()
    line = f"server-port={port}"
    if re.search(r"^server-port=\d+", props, re.M):
        props = re.sub(r"^server-port=\d+", line, props, count=1, flags=re.M)
    else:
        props = (props.rstrip() + "\n" + line + "\n") if props else line + "\n"
    with open(props_file, "w", encoding="utf-8") as f:
        f.write(props)


# ─── listing / fetching ─────────────────────────────────────────────────────
def get_config(args, progress=None):
    return load_config()


def list_servers(args, progress=None):
    cfg = load_config()
    servers = []
    for s in cfg.get("servers", []):
        item = dict(s)
        item["running"] = runstate.is_running(s["id"])
        servers.append(item)
    return {"servers": servers}


def fetch_server(args, progress=None):
    cfg = load_config()
    srv = find_server(cfg, args.id)
    if not srv:
        return {"error": "Server not found"}
    out = dict(srv)
    out["running"] = runstate.is_running(srv["id"])
    return out


# ─── create / delete / update ───────────────────────────────────────────────
def create_server(args, progress=None):
    try:
        software = args.software
        if software not in SOFTWARE:
            return {"error": f"Unknown software '{software}'. One of: {', '.join(SOFTWARE)}"}
        if not args.version:
            return {"error": "Version is required (-v)"}

        cfg = load_config()
        sid = _unique_id("srv", paths.SERVERS_DIR)
        server_dir = os.path.join(paths.SERVERS_DIR, sid)
        os.makedirs(server_dir, exist_ok=True)

        server = {
            "id": sid,
            "name": args.name,
            "port": int(args.port),
            "ram": util.normalize_ram(args.ram),
            "storageLimit": args.storage or None,
            "software": software,
            "version": args.version,
            "profileId": args.profile or None,
            "javaPath": args.java or "java",
            "javaArgs": args.jargs or util.default_java_args(),
            "created": _now_ms(),
            "dir": server_dir,
        }

        if args.profile:
            profile_dir = os.path.join(paths.PROFILES_DIR, args.profile)
            if os.path.exists(profile_dir):
                util.copy_dir(profile_dir, server_dir)

        props_file = os.path.join(server_dir, "server.properties")
        if not os.path.exists(props_file):
            with open(props_file, "w", encoding="utf-8") as f:
                f.write(f"server-port={server['port']}\nquery.port={server['port']}\n")
        else:
            _write_port(props_file, server["port"])

        if getattr(args, "accept_eula", False):
            with open(os.path.join(server_dir, "eula.txt"), "w", encoding="utf-8") as f:
                f.write("eula=true\n")

        if software != "spigot":
            if progress:
                progress(0, "Resolving download URL...")
            url = resolve_download_url(software, args.version, getattr(args, "unstable", False))
            if progress:
                progress(0, "Downloading server jar...")
            download_file(url, os.path.join(server_dir, "server.jar"),
                          (lambda p: progress(p, f"Downloading... {p}%")) if progress else None)

        cfg.setdefault("servers", []).append(server)
        save_config(cfg)
        if progress:
            progress(100, "Done!")
        return {"success": True, "server": server}
    except Exception as e:
        return {"error": str(e)}


def delete_server(args, progress=None):
    try:
        cfg = load_config()
        srv = find_server(cfg, args.id)
        if srv and os.path.exists(srv["dir"]):
            import shutil
            shutil.rmtree(srv["dir"], ignore_errors=True)
        cfg["servers"] = [s for s in cfg.get("servers", []) if s["id"] != args.id]
        save_config(cfg)
        runstate.cleanup_state(args.id)
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


def update_server(args, progress=None):
    try:
        cfg = load_config()
        idx = next((i for i, s in enumerate(cfg.get("servers", [])) if s["id"] == args.id), -1)
        if idx == -1:
            return {"error": "Server not found"}

        updates = {}
        if args.name is not None:
            updates["name"] = args.name
        if args.port is not None:
            updates["port"] = int(args.port)
        if args.ram is not None:
            updates["ram"] = util.normalize_ram(args.ram)
        if args.software is not None:
            updates["software"] = args.software
        if args.version is not None:
            updates["version"] = args.version
        if args.java is not None:
            updates["javaPath"] = args.java
        if args.jargs is not None:
            updates["javaArgs"] = args.jargs
        if args.storage is not None:
            updates["storageLimit"] = args.storage or None

        cfg["servers"][idx] = {**cfg["servers"][idx], **updates}

        if "port" in updates:
            _write_port(os.path.join(cfg["servers"][idx]["dir"], "server.properties"), updates["port"])

        save_config(cfg)
        return {"success": True, "server": cfg["servers"][idx]}
    except Exception as e:
        return {"error": str(e)}


def duplicate_server(args, progress=None):
    try:
        cfg = load_config()
        src = find_server(cfg, args.id)
        if not src:
            return {"error": "Server not found"}
        new_id = _unique_id("srv", paths.SERVERS_DIR)
        new_dir = os.path.join(paths.SERVERS_DIR, new_id)
        os.makedirs(new_dir, exist_ok=True)
        if progress:
            progress(0, "Copying server files…")
        # copy_dir skips profile.json only; servers have none, so this is a full copy
        util.copy_dir(src["dir"], new_dir)
        if progress:
            progress(100, "Done!")
        new_server = {**src, "id": new_id, "name": args.name, "dir": new_dir, "created": _now_ms()}
        cfg["servers"].append(new_server)
        save_config(cfg)
        return {"success": True, "server": new_server}
    except Exception as e:
        return {"error": str(e)}


def import_server(args, progress=None):
    try:
        cfg = load_config()
        sid = _unique_id("srv", paths.SERVERS_DIR)
        server_dir = os.path.join(paths.SERVERS_DIR, sid)
        if progress:
            progress(0, "Copying server files...")
        os.makedirs(server_dir, exist_ok=True)
        util.copy_dir(args.path, server_dir)
        server = {
            "id": sid,
            "name": args.name,
            "port": int(args.port) if args.port else 25565,
            "ram": util.normalize_ram(args.ram) if args.ram else "2G",
            "storageLimit": None,
            "software": args.software or "paper",
            "version": args.version or "Unknown",
            "profileId": None,
            "javaPath": args.java or "java",
            "javaArgs": args.jargs or util.default_java_args(),
            "created": _now_ms(),
            "dir": server_dir,
        }
        cfg.setdefault("servers", []).append(server)
        save_config(cfg)
        if progress:
            progress(100, "Done!")
        return {"success": True, "server": server}
    except Exception as e:
        return {"error": str(e)}


# ─── EULA ────────────────────────────────────────────────────────────────────
def accept_eula(args, progress=None):
    try:
        cfg = load_config()
        srv = find_server(cfg, args.id)
        if not srv:
            return {"error": "Server not found"}
        with open(os.path.join(srv["dir"], "eula.txt"), "w", encoding="utf-8") as f:
            f.write("eula=true\n")
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


# ─── lifecycle ────────────────────────────────────────────────────────────────
def _eula_accepted(srv):
    p = os.path.join(srv["dir"], "eula.txt")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return "eula=true" in f.read()
    except OSError:
        return False


def _spawn_supervisor(server_id, timeout=8.0):
    """Launch the detached supervisor and wait for it to report running."""
    try:
        os.remove(runstate.boot_err_path(server_id))
    except OSError:
        pass
    env = os.environ.copy()
    pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _PKG_PARENT + (":" + pp if pp else "")
    subprocess.Popen(
        [sys.executable, "-m", "mcpanel.supervisor", server_id],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if runstate.is_running(server_id):
            return {"success": True}
        if os.path.exists(runstate.boot_err_path(server_id)):
            try:
                with open(runstate.boot_err_path(server_id), "r", encoding="utf-8") as f:
                    return {"error": f.read().strip() or "Failed to start"}
            except OSError:
                return {"error": "Failed to start"}
        time.sleep(0.15)
    # Last chance — boot error may have appeared right at the deadline
    if os.path.exists(runstate.boot_err_path(server_id)):
        with open(runstate.boot_err_path(server_id), "r", encoding="utf-8") as f:
            return {"error": f.read().strip() or "Failed to start"}
    return {"error": "Timed out waiting for server to start"}


def start_server(args, progress=None):
    try:
        cfg = load_config()
        srv = find_server(cfg, args.id)
        if not srv:
            return {"error": "Server not found"}
        if runstate.is_running(srv["id"]):
            return {"error": "Already running"}

        if not _eula_accepted(srv):
            if getattr(args, "accept_eula", False):
                with open(os.path.join(srv["dir"], "eula.txt"), "w", encoding="utf-8") as f:
                    f.write("eula=true\n")
            else:
                return {"needsEula": True}

        if srv.get("storageLimit"):
            limit = util.parse_storage_limit(srv["storageLimit"])
            if limit is not None:
                current = util.get_dir_size(srv["dir"])
                if current > limit:
                    used_mb = round(current / 1048576)
                    return {"error": f"Storage limit exceeded: {used_mb} MB used, "
                                     f"limit is {srv['storageLimit']}"}

        _, err = util.resolve_jar(srv)
        if err:
            return {"error": err}

        return _spawn_supervisor(srv["id"])
    except Exception as e:
        return {"error": str(e)}


def stop_server(args, progress=None):
    if not runstate.is_running(args.id):
        return {"error": "Not running"}
    r = runstate.send_request(args.id, {"op": "cmd", "text": "stop"})
    return {"success": True} if r.get("ok") else {"error": r.get("error", "stop failed")}


def kill_server(args, progress=None):
    if not runstate.is_running(args.id):
        return {"error": "Not running"}
    r = runstate.send_request(args.id, {"op": "kill"})
    return {"success": True} if r.get("ok") else {"error": r.get("error", "kill failed")}


def restart_server(args, progress=None):
    if runstate.is_running(args.id):
        runstate.send_request(args.id, {"op": "cmd", "text": "stop"})
        deadline = time.time() + 15
        while time.time() < deadline and runstate.is_running(args.id):
            time.sleep(0.25)
        if runstate.is_running(args.id):
            runstate.send_request(args.id, {"op": "kill"})
            time.sleep(0.5)
    return start_server(args, progress)


def send_command(args, progress=None):
    if not runstate.is_running(args.id):
        return {"error": "Not running"}
    r = runstate.send_request(args.id, {"op": "cmd", "text": args.command})
    return {"success": True} if r.get("ok") else {"error": r.get("error", "send failed")}


def get_server_log(args, progress=None):
    if getattr(args, "session", None) is not None:
        return read_session_log(args, progress)
    return runstate.read_log(args.id)


def list_session_logs(args, progress=None):
    log_paths = runstate.session_log_paths(args.id)
    sessions = []
    for i, p in enumerate(log_paths):
        fname = os.path.basename(p)
        # filename: <id>.log.<ts>.jsonl
        try:
            ts = int(fname[len(args.id) + 5:-6])  # strip "<id>.log." and ".jsonl"
        except Exception:
            ts = 0
        sessions.append({"n": i + 1, "timestamp": ts, "path": p})
    return {"sessions": sessions}


def read_session_log(args, progress=None):
    n = getattr(args, "session", 1) or 1
    log_paths = runstate.session_log_paths(args.id)
    if not log_paths:
        return [{"time": int(time.time() * 1000),
                 "text": "No archived sessions found.", "type": "err"}]
    if n < 1 or n > len(log_paths):
        count = len(log_paths)
        return [{"time": int(time.time() * 1000),
                 "text": f"Session {n} not found ({count} archived session{'s' if count != 1 else ''} available).",
                 "type": "err"}]
    return runstate.read_log_file(log_paths[n - 1])


def is_server_running(args, progress=None):
    return runstate.is_running(args.id)


# ─── ping / stats / files ─────────────────────────────────────────────────────
def ping(args, progress=None):
    host = getattr(args, "host", None)
    port = getattr(args, "port", None)
    if getattr(args, "id", None):
        cfg = load_config()
        srv = find_server(cfg, args.id)
        if not srv:
            return {"error": "Server not found"}
        host = host or "127.0.0.1"
        port = port or srv.get("port", 25565)
    if not host:
        host = "127.0.0.1"
    if not port:
        port = 25565
    return ping_server(host, int(port))


def get_server_dir_stats(args, progress=None):
    cfg = load_config()
    srv = find_server(cfg, args.id)
    if not srv or not os.path.exists(srv["dir"]):
        return {"size": 0}
    result = {"size": util.get_dir_size(srv["dir"])}
    st = runstate.read_state(srv["id"])
    if st:
        pid = st.get("javaPid")
        if pid:
            try:
                with open(f"/proc/{pid}/status", "r") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            kb = int(line.split()[1])
                            result["ramBytes"] = kb * 1024
                            break
            except (OSError, ValueError):
                pass
    return result


def get_server_file_tree(args, progress=None):
    cfg = load_config()
    srv = find_server(cfg, args.id)
    if not srv:
        return {"error": "Server not found"}
    return {"tree": util.build_file_tree(srv["dir"], srv["dir"])}


def scan_server_folder(args, progress=None):
    folder = args.path
    try:
        result = {"port": 25565, "software": None, "version": None}
        props_file = os.path.join(folder, "server.properties")
        if os.path.exists(props_file):
            with open(props_file, "r", encoding="utf-8") as f:
                m = re.search(r"^server-port=(\d+)", f.read(), re.M)
                if m:
                    result["port"] = int(m.group(1))
        keys = ["paper", "purpur", "folia", "leaf", "fabric", "velocity", "spigot", "vanilla"]
        jars = [f for f in os.listdir(folder) if f.endswith(".jar")] if os.path.isdir(folder) else []
        for jar in jars:
            lc = jar.lower()
            for k in keys:
                if k in lc:
                    result["software"] = k
                    break
            if result["software"]:
                break
        return result
    except Exception:
        return {"port": 25565}


def open_server_folder(args, progress=None):
    cfg = load_config()
    srv = find_server(cfg, args.id)
    if not srv:
        return {"error": "Server not found"}
    try:
        subprocess.Popen(["xdg-open", srv["dir"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    return {"success": True, "dir": srv["dir"]}
