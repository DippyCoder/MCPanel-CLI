"""config.json load/save — mirrors loadConfig/saveConfig in main.js.

Also owns each server's `mcpanel.json` manifest — a copy of its config.json
entry written into its own directory (everything except the machine-specific
`dir` path). Two things fall out of that:
  - Portability: drop (or restore from backup) a server folder into another
    install's `servers/` directory and it re-registers itself automatically.
  - Resilience: config.json can be rebuilt from the manifests if it's ever
    lost or corrupted.
"""

import json
import os
import sys
import time
from contextlib import contextmanager

from . import paths

MANIFEST_FILENAME = "mcpanel.json"

# Last config.json that parsed cleanly, refreshed on every save. Used to
# recover when config.json itself is truncated or corrupted (power loss, disk
# full, a hand edit gone wrong).
BACKUP_SUFFIX = ".bak"


def _default_config():
    return {"servers": [], "jdkPaths": [], "activeTheme": None}


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("config root is not an object")
    if not isinstance(data.get("servers", []), list):
        raise ValueError("'servers' is not a list")
    data.setdefault("servers", [])
    # Entries without an id or dir can't be addressed or started and would
    # crash every caller that indexes s["id"] / s["dir"].
    data["servers"] = [s for s in data["servers"]
                       if isinstance(s, dict) and s.get("id") and s.get("dir")]
    return data


def load_config():
    """Reads config.json. A *missing* file is a fresh install; a *corrupt*
    one is never silently treated as empty — the next save would then wipe
    every server. Instead the broken file is set aside and the last good copy
    (config.json.bak) is used; discover_servers() re-registers anything that
    copy is missing from the per-server manifests."""
    try:
        return _read_json(paths.CONFIG_FILE)
    except FileNotFoundError:
        return _default_config()
    except (OSError, ValueError) as e:
        err = e
    _quarantine_corrupt(err)
    try:
        cfg = _read_json(paths.CONFIG_FILE + BACKUP_SUFFIX)
    except (OSError, ValueError):
        return _default_config()
    # Put the good copy back in place; otherwise the next process finds no
    # config.json at all and starts from an empty server list.
    try:
        _atomic_write_json(paths.CONFIG_FILE, cfg)
    except OSError:
        pass
    return cfg


def _quarantine_corrupt(err):
    from . import applog
    dest = f"{paths.CONFIG_FILE}.corrupt-{int(time.time())}"
    try:
        os.replace(paths.CONFIG_FILE, dest)
        applog.error(f"config.json was unreadable ({err}); moved it to {dest} "
                     "and fell back to the last good copy")
    except OSError:
        applog.error(f"config.json is unreadable ({err})")


def _atomic_write_json(path, data):
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def save_config(cfg):
    """Atomic: a crash or full disk mid-write leaves the previous config.json
    intact instead of a truncated one."""
    paths.ensure_dirs()
    _atomic_write_json(paths.CONFIG_FILE, cfg)
    try:
        _atomic_write_json(paths.CONFIG_FILE + BACKUP_SUFFIX, cfg)
    except OSError:
        pass


@contextmanager
def _file_lock(path):
    """Exclusive advisory lock across processes. The desktop app and WebUI
    run several `mcpanel api ...` processes at once; without this two
    concurrent load→modify→save cycles silently drop one of the changes."""
    paths.ensure_dirs()
    fh = open(path, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            fh.seek(0)
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:  # LK_LOCK gives up after ~10s; keep waiting
                    continue
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if sys.platform == "win32":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


@contextmanager
def locked_config():
    """`with locked_config() as cfg:` — loads the current config under an
    exclusive lock and saves it on a clean exit. Nothing is saved if the block
    raises. Keep the block short: never download or build inside it."""
    with _file_lock(paths.CONFIG_FILE + ".lock"):
        cfg = load_config()
        yield cfg
        save_config(cfg)


def find_server(cfg, server_id):
    for s in cfg.get("servers", []):
        if s.get("id") == server_id:
            return s
    return None


def write_server_manifest(server):
    """Persist `server`'s config entry into <dir>/mcpanel.json — everything
    except `dir` itself, so the manifest stays valid if the folder is moved
    or copied elsewhere. Best-effort: a write failure here shouldn't break
    the caller, it just means this server won't self-register elsewhere."""
    manifest = {k: v for k, v in server.items() if k != "dir"}
    try:
        _atomic_write_json(os.path.join(server["dir"], MANIFEST_FILENAME), manifest)
    except OSError:
        pass


def _read_server_manifest(server_dir):
    try:
        with open(os.path.join(server_dir, MANIFEST_FILENAME), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data.get("id") else None
    except (OSError, ValueError):
        return None


def discover_servers():
    """Scan paths.SERVERS_DIR for folders carrying a mcpanel.json manifest
    whose id isn't already registered, and auto-register them — this is what
    makes a server folder "just show up" after being dropped into place.
    Returns the list of newly-registered server dicts (empty if none)."""
    if not os.path.isdir(paths.SERVERS_DIR):
        return []
    try:
        entries = sorted(os.listdir(paths.SERVERS_DIR))
    except OSError:
        return []

    candidates = []
    for name in entries:
        server_dir = os.path.join(paths.SERVERS_DIR, name)
        if not os.path.isdir(server_dir):
            continue
        manifest = _read_server_manifest(server_dir)
        if manifest and paths.is_valid_id(manifest["id"]):
            candidates.append((server_dir, manifest))
    if not candidates:
        return []

    known_ids = {s.get("id") for s in load_config().get("servers", [])}
    if all(m["id"] in known_ids for _, m in candidates):
        return []  # the common case: nothing new, so don't take the lock

    added = []
    with locked_config() as cfg:
        known_ids = {s.get("id") for s in cfg.get("servers", [])}
        known_dirs = {os.path.realpath(s["dir"]) for s in cfg.get("servers", [])}
        for server_dir, manifest in candidates:
            # A copied folder still carries its source's manifest; registering
            # it would give two entries the same id.
            if manifest["id"] in known_ids or os.path.realpath(server_dir) in known_dirs:
                continue
            server = dict(manifest)
            server["dir"] = server_dir
            # Discovered folders live in MCPanel's own servers dir, so they're
            # owned, even if the manifest came from a linked original.
            server.pop("linked", None)
            cfg.setdefault("servers", []).append(server)
            known_ids.add(server["id"])
            added.append(server)
    return added
