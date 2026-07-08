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

from . import paths

MANIFEST_FILENAME = "mcpanel.json"


def load_config():
    try:
        with open(paths.CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"servers": [], "jdkPaths": [], "activeTheme": None}


def save_config(cfg):
    paths.ensure_dirs()
    with open(paths.CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


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
    path = os.path.join(server["dir"], MANIFEST_FILENAME)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        os.replace(tmp, path)
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

    cfg = load_config()
    known_ids = {s.get("id") for s in cfg.get("servers", [])}
    added = []
    for name in entries:
        server_dir = os.path.join(paths.SERVERS_DIR, name)
        if not os.path.isdir(server_dir):
            continue
        manifest = _read_server_manifest(server_dir)
        if not manifest or manifest["id"] in known_ids:
            continue
        server = dict(manifest)
        server["dir"] = server_dir
        cfg.setdefault("servers", []).append(server)
        known_ids.add(server["id"])
        added.append(server)

    if added:
        save_config(cfg)
    return added
