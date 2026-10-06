"""Backup operations — create/list/delete/restore server backups.

Backups are stored as ZIP files in:
  <userData>/backups/<server_id>/backup_<timestamp>.zip

The logs/ directory is excluded to keep archive sizes reasonable.
"""

import json
import os
import time
import zipfile

from . import paths, runstate
from .config import load_config, find_server
from .errors import fail


def _backups_dir(server_id):
    return os.path.join(paths.USER_DATA, "backups", server_id)


def _now_ms():
    return int(time.time() * 1000)


def _check_name(name):
    """Backup names come straight from the API; only plain .zip file names
    inside the server's backup folder are acceptable."""
    if (not name or ".." in name or "/" in name or "\\" in name
            or name.startswith(".") or not name.endswith(".zip")):
        return fail("invalid_backup_name")
    return None


def _progress_line(pct, status, extra=None):
    obj = {"progress": pct, "status": status}
    if extra:
        obj.update(extra)
    print(json.dumps(obj), flush=True)


def create_backup(args, progress=None):
    try:
        cfg = load_config()
        srv = find_server(cfg, args.id)
        if not srv:
            return fail("server_not_found")
        if not paths.is_valid_id(args.id):
            return fail("invalid_id")
        if not os.path.isdir(srv["dir"]):
            return fail("server_dir_missing", f"Server folder is missing: {srv['dir']}")

        backup_dir = _backups_dir(args.id)
        os.makedirs(backup_dir, exist_ok=True)

        # Two backups in the same second used to overwrite each other.
        ts = int(time.time())
        backup_name = f"backup_{ts}.zip"
        n = 1
        while os.path.exists(os.path.join(backup_dir, backup_name)):
            n += 1
            backup_name = f"backup_{ts}_{n}.zip"
        backup_path = os.path.join(backup_dir, backup_name)
        # Written under a temporary name and renamed when complete, so an
        # interrupted backup never shows up in `backup list` as a valid one.
        part_path = backup_path + ".part"

        is_api = getattr(args, "json", False)

        def _prog(pct, status, extra=None):
            if is_api:
                _progress_line(pct, status, extra)
            elif progress:
                progress(pct, status)

        _prog(5, "Starting backup…")

        server_dir = srv["dir"]
        all_files = []
        for root, dirs, files in os.walk(server_dir):
            dirs[:] = [d for d in dirs if d != "logs"]
            for fname in files:
                all_files.append(os.path.join(root, fname))

        _prog(10, f"Compressing {len(all_files)} file(s)…")

        skipped = []
        try:
            with zipfile.ZipFile(part_path, "w", compression=zipfile.ZIP_DEFLATED,
                                 allowZip64=True) as zf:
                for i, fpath in enumerate(all_files):
                    rel = os.path.relpath(fpath, server_dir)
                    if os.path.islink(fpath):
                        continue  # never archive what a symlink points at
                    try:
                        zf.write(fpath, rel)
                    except OSError:
                        skipped.append(rel)
                    if i % 50 == 0 or i == len(all_files) - 1:
                        pct = 10 + int(85 * i / max(len(all_files), 1))
                        _prog(pct, f"Compressing… {i + 1}/{len(all_files)}")
            os.replace(part_path, backup_path)
        except BaseException as e:
            try:
                os.remove(part_path)
            except OSError:
                pass
            if not isinstance(e, Exception):
                raise
            return fail("backup_failed", f"Backup failed: {e}")

        size = os.path.getsize(backup_path)
        result = {
            "success": True,
            "backup": {
                "name": backup_name,
                "size": size,
                "created": ts * 1000,
            },
        }
        if skipped:
            # Silently dropping unreadable files made a backup look complete
            # when it wasn't.
            result["skipped"] = skipped
        _prog(100, "Backup complete!", result)

        from .cli import _Streamed
        return _Streamed() if is_api else result

    except Exception as e:
        return fail("operation_failed", str(e))


def list_backups(args, progress=None):
    try:
        if not paths.is_valid_id(args.id):
            return fail("invalid_id")
        backup_dir = _backups_dir(args.id)
        backups = []
        if os.path.isdir(backup_dir):
            for fname in sorted(os.listdir(backup_dir), reverse=True):
                if not fname.endswith(".zip"):
                    continue
                fpath = os.path.join(backup_dir, fname)
                try:
                    stat = os.stat(fpath)
                    backups.append({
                        "name": fname,
                        "size": stat.st_size,
                        "created": int(stat.st_mtime * 1000),
                    })
                except OSError:
                    pass
        return {"backups": backups}
    except Exception as e:
        return fail("operation_failed", str(e))


def delete_backup(args, progress=None):
    try:
        name = args.backup_name
        if not paths.is_valid_id(args.id):
            return fail("invalid_id")
        err = _check_name(name)
        if err:
            return err
        path = os.path.join(_backups_dir(args.id), name)
        if not os.path.exists(path):
            return fail("backup_not_found")
        os.remove(path)
        return {"success": True}
    except Exception as e:
        return fail("operation_failed", str(e))


def restore_backup(args, progress=None):
    try:
        cfg = load_config()
        srv = find_server(cfg, args.id)
        if not srv:
            return fail("server_not_found")

        name = args.backup_name
        err = _check_name(name)
        if err:
            return err

        backup_path = os.path.join(_backups_dir(args.id), name)
        if not os.path.exists(backup_path):
            return fail("backup_not_found")

        # Overwriting world files under a live server corrupts them, and the
        # server would save its in-memory state over the restore anyway.
        if runstate.is_running(srv["id"]):
            return {"error": "Server is running — stop it before restoring a backup",
                    "code": "server_running"}

        is_api = getattr(args, "json", False)

        def _prog(pct, status, extra=None):
            if is_api:
                _progress_line(pct, status, extra)
            elif progress:
                progress(pct, status)

        _prog(10, "Opening backup…")

        server_dir = srv["dir"]
        with zipfile.ZipFile(backup_path, "r") as zf:
            # Verify every member's CRC *before* touching the server: a
            # truncated or corrupt archive used to fail halfway through,
            # leaving the server a mix of old and restored files.
            _prog(5, "Verifying backup…")
            bad = zf.testzip()
            if bad is not None:
                return {"error": f"Backup is corrupt (first bad file: {bad}) — nothing was restored",
                        "code": "backup_corrupt"}
            # (zipfile.extract already strips absolute paths and ".." parts.)
            members = zf.infolist()
            os.makedirs(server_dir, exist_ok=True)
            for i, member in enumerate(members):
                zf.extract(member, server_dir)
                if i % 50 == 0 or i == len(members) - 1:
                    pct = 10 + int(85 * i / max(len(members), 1))
                    _prog(pct, f"Restoring… {i + 1}/{len(members)}")

        result = {"success": True}
        _prog(100, "Restore complete!", result)

        from .cli import _Streamed
        return _Streamed() if is_api else result

    except Exception as e:
        return fail("operation_failed", str(e))
