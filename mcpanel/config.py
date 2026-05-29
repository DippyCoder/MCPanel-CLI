"""config.json load/save — mirrors loadConfig/saveConfig in main.js."""

import json

from . import paths


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
