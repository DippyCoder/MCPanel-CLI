"""Backup operations — create/list/delete/restore server backups.

Backups are stored as ZIP files in:
  <userData>/backups/<server_id>/backup_<timestamp>.zip

The logs/ directory is excluded to keep archive sizes reasonable.
"""

import json
import os
import time
import zipfile

from . import paths
from .config import load_config, find_server


def _backups_dir(server_id):
    return os.path.join(paths.USER_DATA, "backups", server_id)


def _now_ms():
    return int(time.time() * 1000)


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
            return {"error": "Server not found"}

        backup_dir = _backups_dir(args.id)
        os.makedirs(backup_dir, exist_ok=True)

        ts = int(time.time())
        backup_name = f"backup_{ts}.zip"
        backup_path = os.path.join(backup_dir, backup_name)

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

        try:
            with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for i, fpath in enumerate(all_files):
                    rel = os.path.relpath(fpath, server_dir)
                    try:
                        zf.write(fpath, rel)
                    except (OSError, PermissionError):
                        pass
                    if i % 50 == 0 or i == len(all_files) - 1:
                        pct = 10 + int(85 * i / max(len(all_files), 1))
                        _prog(pct, f"Compressing… {i + 1}/{len(all_files)}")
        except Exception as e:
            try:
                os.remove(backup_path)
            except OSError:
                pass
            return {"error": str(e)}

        size = os.path.getsize(backup_path)
        result = {
            "success": True,
            "backup": {
                "name": backup_name,
                "size": size,
                "created": ts * 1000,
            },
        }
        _prog(100, "Backup complete!", result)

        from .cli import _Streamed
        return _Streamed() if is_api else result

    except Exception as e:
        return {"error": str(e)}


def list_backups(args, progress=None):
    try:
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
        return {"error": str(e)}


def delete_backup(args, progress=None):
    try:
        name = args.backup_name
        if ".." in name or "/" in name or "\\" in name:
            return {"error": "Invalid backup name"}
        path = os.path.join(_backups_dir(args.id), name)
        if not os.path.exists(path):
            return {"error": "Backup not found"}
        os.remove(path)
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


def restore_backup(args, progress=None):
    try:
        cfg = load_config()
        srv = find_server(cfg, args.id)
        if not srv:
            return {"error": "Server not found"}

        name = args.backup_name
        if ".." in name or "/" in name or "\\" in name:
            return {"error": "Invalid backup name"}

        backup_path = os.path.join(_backups_dir(args.id), name)
        if not os.path.exists(backup_path):
            return {"error": "Backup not found"}

        is_api = getattr(args, "json", False)

        def _prog(pct, status, extra=None):
            if is_api:
                _progress_line(pct, status, extra)
            elif progress:
                progress(pct, status)

        _prog(10, "Opening backup…")

        server_dir = srv["dir"]
        with zipfile.ZipFile(backup_path, "r") as zf:
            members = zf.infolist()
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
        return {"error": str(e)}
