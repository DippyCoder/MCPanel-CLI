"""Profile controllers — ports of the profile IPC handlers in main.js.

Profiles are server presets: a folder of files (plugins/, config/, ...) plus a
profile.json metadata file. They can restrict to specific software/versions and
are copied into a server's directory on creation.
"""

import json
import os
import shutil
import subprocess
import time

from . import paths, util
from .config import load_config, find_server


def _now_ms():
    return int(time.time() * 1000)


def _split_list(value):
    """Accept '' / None / 'a,b' / ['a','b'] → list."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [v.strip() for v in str(value).split(",") if v.strip()]


def get_profiles(args=None, progress=None):
    profiles = []
    try:
        for d in os.listdir(paths.PROFILES_DIR):
            meta_file = os.path.join(paths.PROFILES_DIR, d, "profile.json")
            if os.path.exists(meta_file):
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    meta["id"] = d
                    profiles.append(meta)
                except Exception:
                    pass
    except OSError:
        pass
    return profiles


def list_profiles(args=None, progress=None):
    return {"profiles": get_profiles(args)}


def fetch_profile(args, progress=None):
    for p in get_profiles():
        if p["id"] == args.id:
            return p
    return {"error": "Profile not found"}


def create_profile(args, progress=None):
    try:
        pid = "profile_" + str(_now_ms())
        profile_dir = os.path.join(paths.PROFILES_DIR, pid)
        os.makedirs(profile_dir, exist_ok=True)
        meta = {
            "id": pid,
            "name": args.name,
            "description": getattr(args, "desc", None) or "",
            "software": _split_list(getattr(args, "software", None)),
            "versions": _split_list(getattr(args, "versions", None)),
            "created": _now_ms(),
        }
        with open(os.path.join(profile_dir, "profile.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        return {"success": True, "profile": {**meta, "dir": profile_dir}}
    except Exception as e:
        return {"error": str(e)}


def delete_profile(args, progress=None):
    try:
        profile_dir = os.path.join(paths.PROFILES_DIR, args.id)
        if os.path.exists(profile_dir):
            shutil.rmtree(profile_dir, ignore_errors=True)
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


def open_profile_folder(args, progress=None):
    import sys
    profile_dir = os.path.join(paths.PROFILES_DIR, args.id)
    os.makedirs(profile_dir, exist_ok=True)
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", profile_dir])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", profile_dir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", profile_dir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    return {"success": True, "dir": profile_dir}


def import_profile(args, progress=None):
    try:
        pid = "profile_" + str(_now_ms())
        profile_dir = os.path.join(paths.PROFILES_DIR, pid)
        os.makedirs(profile_dir, exist_ok=True)
        util.copy_dir(args.path, profile_dir)
        meta = {
            "id": pid,
            "name": args.name,
            "description": getattr(args, "desc", None) or "",
            "software": _split_list(getattr(args, "software", None)),
            "versions": _split_list(getattr(args, "versions", None)),
            "created": _now_ms(),
        }
        with open(os.path.join(profile_dir, "profile.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        return {"success": True, "profile": {**meta, "dir": profile_dir}}
    except Exception as e:
        return {"error": str(e)}


def scan_profile_folder(args, progress=None):
    try:
        meta_file = os.path.join(args.path, "profile.json")
        if os.path.exists(meta_file):
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            return {
                "name": meta.get("name", ""),
                "description": meta.get("description", ""),
                "software": meta.get("software", []),
                "versions": meta.get("versions", []),
            }
        return {}
    except Exception:
        return {}


def create_profile_from_server(args, progress=None):
    try:
        cfg = load_config()
        srv = find_server(cfg, args.id)
        if not srv:
            return {"error": "Server not found"}
        pid = "profile_" + str(_now_ms())
        profile_dir = os.path.join(paths.PROFILES_DIR, pid)
        os.makedirs(profile_dir, exist_ok=True)
        for rel in _split_list(args.paths):
            src = os.path.join(srv["dir"], rel)
            dst = os.path.join(profile_dir, rel)
            if not os.path.exists(src):
                continue
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            else:
                util.copy_dir(src, dst)
        meta = {
            "id": pid,
            "name": args.name,
            "description": getattr(args, "desc", None) or "",
            "software": _split_list(getattr(args, "software", None)),
            "versions": _split_list(getattr(args, "versions", None)),
            "created": _now_ms(),
        }
        with open(os.path.join(profile_dir, "profile.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        return {"success": True, "profile": {**meta, "dir": profile_dir}}
    except Exception as e:
        return {"error": str(e)}
