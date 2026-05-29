"""Theme controllers — ports of the theme IPC handlers in main.js, using the
stdlib zipfile module in place of the extract-zip npm dependency.

A theme is a folder containing theme.json (metadata) and theme.css. Bundled
default themes (Dark Slate / Bright Slate) ship inside the package and are
installed into the user themes dir on first use.
"""

import json
import os
import re
import shutil
import tempfile
import time
import zipfile

from . import paths, util
from .config import load_config, save_config
from .http import download_file, fetch_text

_BUNDLED_DIR = os.path.join(os.path.dirname(__file__), "bundled_themes")


def install_bundled_themes():
    if not os.path.isdir(_BUNDLED_DIR):
        return
    paths.ensure_dirs()
    for name in os.listdir(_BUNDLED_DIR):
        src = os.path.join(_BUNDLED_DIR, name)
        dst = os.path.join(paths.THEMES_DIR, name)
        if os.path.isdir(src) and not os.path.exists(dst):
            os.makedirs(dst, exist_ok=True)
            util.copy_dir(src, dst)
            # copy_dir never skips theme.json, but be explicit/safe
            tj = os.path.join(src, "theme.json")
            if os.path.exists(tj):
                shutil.copy2(tj, os.path.join(dst, "theme.json"))


def _find_theme_json(root):
    direct = os.path.join(root, "theme.json")
    if os.path.exists(direct):
        return direct
    for entry in os.scandir(root):
        if entry.is_dir():
            nested = os.path.join(root, entry.name, "theme.json")
            if os.path.exists(nested):
                return nested
    return None


def _install_from_zip(zip_path):
    tid = "theme_" + str(int(time.time() * 1000))
    theme_dir = os.path.join(paths.THEMES_DIR, tid)
    with tempfile.TemporaryDirectory(prefix="mcpanel_theme_") as tmp:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        meta_path = _find_theme_json(tmp)
        if not meta_path:
            raise RuntimeError("theme.json not found in archive")
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if not meta.get("name"):
            raise RuntimeError('theme.json must include a "name" field')
        os.makedirs(theme_dir, exist_ok=True)
        util.copy_dir(os.path.dirname(meta_path), theme_dir)
        shutil.copy2(meta_path, os.path.join(theme_dir, "theme.json"))
    return {"success": True, "theme": {**meta, "id": tid, "dir": theme_dir}}


def get_themes(args=None, progress=None):
    install_bundled_themes()
    themes = []
    try:
        for d in os.listdir(paths.THEMES_DIR):
            if d.startswith("_tmp_") or d.startswith("_download_"):
                continue
            meta_file = os.path.join(paths.THEMES_DIR, d, "theme.json")
            if os.path.exists(meta_file):
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    meta["id"] = d
                    meta["dir"] = os.path.join(paths.THEMES_DIR, d)
                    cfg = load_config()
                    meta["active"] = (cfg.get("activeTheme") == d)
                    themes.append(meta)
                except Exception:
                    pass
    except OSError:
        pass
    return themes


def list_themes(args=None, progress=None):
    return {"themes": get_themes(args)}


def get_theme_css(args, progress=None):
    tid = args.id
    if not tid:
        return {"css": None}
    css_file = os.path.join(paths.THEMES_DIR, tid, "theme.css")
    if not os.path.exists(css_file):
        return {"css": None}
    with open(css_file, "r", encoding="utf-8") as f:
        css = f.read()
    theme_dir = os.path.join(paths.THEMES_DIR, tid).replace("\\", "/")

    def repl(m):
        rel = m.group(1)
        return f"url('file:///{theme_dir}/{rel}')"

    css = re.sub(r"url\(\s*['\"]?(?!https?:|data:|file:)([^'\")\s]+)['\"]?\s*\)", repl, css)
    return {"css": css}


def set_active_theme(args, progress=None):
    cfg = load_config()
    tid = args.id
    if tid and tid.lower() in ("none", "default", "null", ""):
        tid = None
    cfg["activeTheme"] = tid
    save_config(cfg)
    return {"success": True, "activeTheme": tid}


def install_theme_url(args, progress=None):
    tmp_zip = os.path.join(paths.THEMES_DIR, "_download_" + str(int(time.time() * 1000)) + ".zip")
    paths.ensure_dirs()
    try:
        download_file(args.url, tmp_zip, (lambda p: progress(p, f"Downloading... {p}%")) if progress else None)
        result = _install_from_zip(tmp_zip)
        return result
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            os.remove(tmp_zip)
        except OSError:
            pass


def install_theme_file(args, progress=None):
    try:
        return _install_from_zip(args.file)
    except Exception as e:
        return {"error": str(e)}


def delete_theme(args, progress=None):
    try:
        theme_dir = os.path.join(paths.THEMES_DIR, args.id)
        if os.path.exists(theme_dir):
            shutil.rmtree(theme_dir, ignore_errors=True)
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


def fetch_github_themes(args=None, progress=None):
    try:
        raw = fetch_text("https://raw.githubusercontent.com/DippyCoder/MCPanel/themes/themes-index.json")
        data = json.loads(raw)
        return {"themes": data.get("themes", [])}
    except Exception as e:
        return {"themes": [], "error": str(e)}
