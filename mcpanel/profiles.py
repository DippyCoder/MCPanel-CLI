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
from .errors import fail


def _now_ms():
    return int(time.time() * 1000)


def _new_profile_dir():
    """Fresh `profile_<ms>` id + directory. Two creations in the same
    millisecond used to share (and overwrite) one folder."""
    paths.ensure_dirs()
    while True:
        pid = "profile_" + str(_now_ms())
        profile_dir = os.path.join(paths.PROFILES_DIR, pid)
        try:
            os.makedirs(profile_dir)
            return pid, profile_dir
        except FileExistsError:
            time.sleep(0.002)


def _profile_dir(profile_id):
    """Directory for a profile id, or None if the id isn't a plain name.
    `-id ..` used to resolve to the userData folder itself — and
    `delete profile` would then rmtree every server."""
    if not paths.is_valid_id(profile_id):
        return None
    return os.path.join(paths.PROFILES_DIR, profile_id)


def _invalid_id(profile_id):
    return fail("invalid_id", f"Invalid profile id: {profile_id!r}")


def _write_meta(profile_dir, meta):
    util.atomic_write_text(os.path.join(profile_dir, "profile.json"), json.dumps(meta, indent=2))


def _meta_from_args(pid, args):
    return {
        "id": pid,
        "name": args.name,
        "description": getattr(args, "desc", None) or "",
        "software": _split_list(getattr(args, "software", None)),
        "versions": _split_list(getattr(args, "versions", None)),
        "created": _now_ms(),
    }


def _check_import_source(path):
    src = os.path.abspath(os.path.expanduser(path or ""))
    if not os.path.isdir(src):
        return None, fail("invalid_path", f"Not a folder: {path}")
    # Importing a folder that contains the profiles dir would copy the copy
    # into itself until the disk is full.
    if paths.is_within(paths.PROFILES_DIR, src):
        return None, fail("invalid_path", "Cannot import a folder that contains MCPanel's own data directory")
    return src, None


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
                    if not isinstance(meta, dict):
                        continue
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
    return fail("profile_not_found")


def create_profile(args, progress=None):
    profile_dir = None
    try:
        pid, profile_dir = _new_profile_dir()
        meta = _meta_from_args(pid, args)
        _write_meta(profile_dir, meta)
        return {"success": True, "profile": {**meta, "dir": profile_dir}}
    except Exception as e:
        if profile_dir:
            shutil.rmtree(profile_dir, ignore_errors=True)
        return fail("operation_failed", str(e))


def delete_profile(args, progress=None):
    try:
        profile_dir = _profile_dir(args.id)
        if profile_dir is None:
            return _invalid_id(args.id)
        if not os.path.isdir(profile_dir):
            return fail("profile_not_found")
        shutil.rmtree(profile_dir, ignore_errors=True)
        return {"success": True}
    except Exception as e:
        return fail("operation_failed", str(e))


def open_profile_folder(args, progress=None):
    import sys
    profile_dir = _profile_dir(args.id)
    if profile_dir is None:
        return _invalid_id(args.id)
    if not os.path.isdir(profile_dir):
        return fail("profile_not_found")
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
    profile_dir = None
    try:
        src, err = _check_import_source(args.path)
        if err:
            return err
        pid, profile_dir = _new_profile_dir()
        util.copy_dir(src, profile_dir)
        meta = _meta_from_args(pid, args)
        _write_meta(profile_dir, meta)
        return {"success": True, "profile": {**meta, "dir": profile_dir}}
    except BaseException as e:
        # Don't leave a half-copied profile behind (copy error, Ctrl-C, …).
        if profile_dir:
            shutil.rmtree(profile_dir, ignore_errors=True)
        if not isinstance(e, Exception):
            raise
        return fail("operation_failed", str(e))


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
    profile_dir = None
    try:
        cfg = load_config()
        srv = find_server(cfg, args.id)
        if not srv:
            return fail("server_not_found")
        rels = _split_list(args.paths)
        for rel in rels:
            # `-paths ../../.ssh` must not be able to copy files from outside
            # the server folder into a profile.
            full = os.path.join(srv["dir"], rel)
            if (os.path.isabs(rel) or os.path.normpath(rel) == "."
                    or not paths.is_within(full, srv["dir"])):
                return fail("invalid_path", f"Path must be inside the server folder: {rel}")
        pid, profile_dir = _new_profile_dir()
        for rel in rels:
            src = os.path.join(srv["dir"], rel)
            dst = os.path.join(profile_dir, rel)
            if not os.path.exists(src):
                continue
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            else:
                util.copy_dir(src, dst)
        meta = _meta_from_args(pid, args)
        _write_meta(profile_dir, meta)
        return {"success": True, "profile": {**meta, "dir": profile_dir}}
    except BaseException as e:
        if profile_dir:
            shutil.rmtree(profile_dir, ignore_errors=True)
        if not isinstance(e, Exception):
            raise
        return fail("operation_failed", str(e))
