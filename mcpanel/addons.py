"""Addon discovery, loading and command registration.

The CLI *is* MCPanel's backend, so a command added here is immediately callable
from the desktop app and the WebUI as `mcpanel api <group> <cmd>` — an addon
never needs a second integration. See ADDONS.md for the public contract.

Everything in this module is written defensively. Addons are third-party code
running inside the process that manages people's Minecraft servers, so a broken
one must degrade to "that addon is unavailable, here is why" and never to "the
CLI won't start". Each record therefore carries a `status` and, on failure, the
traceback that produced it — surfaced by `mcpanel addons list|info` rather than
raised.

Loading is deliberately lazy-once: `load()` runs on first use and caches, so
importing this module costs nothing until a command tree is actually built.
"""

import importlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
import zipfile

from . import paths

# The addon API contract number. An addon declaring anything else is rejected
# rather than imported, because a mismatch means its `register()` may expect an
# `api` object shaped differently from the one we hand it.
ADDON_API_VERSION = 1

ENTRY_POINT_GROUP = "mcpanel.addons"

# Status values a record can carry.
ST_LOADED = "loaded"
ST_DISABLED = "disabled"
ST_ERROR = "error"
ST_API_MISMATCH = "api-mismatch"
ST_INVALID = "invalid"
ST_DUPLICATE = "duplicate"


# ─── record ──────────────────────────────────────────────────────────────────
class Addon:
    """One discovered addon, loaded or not."""

    def __init__(self, name, source, location):
        self.name = name
        self.source = source          # "bundled" | "pip" | "user"
        self.location = location      # file/dir path, or entry-point spec
        self.version = ""
        self.description = ""
        self.author = ""
        self.url = ""
        self.api_version = None
        self.status = ST_LOADED
        self.error = ""               # traceback, when status is an error
        self.module = None
        self.actions = []             # action strings this addon registered
        self.declared_name = ""       # ADDON["name"], when it differs from name

    def to_dict(self):
        d = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "url": self.url,
            "apiVersion": self.api_version,
            "source": self.source,
            "location": self.location,
            "enabled": self.status != ST_DISABLED,
            "status": self.status,
            "actions": list(self.actions),
        }
        if self.error:
            d["error"] = self.error
        if self.declared_name and self.declared_name != self.name:
            d["declaredName"] = self.declared_name
        return d


# ─── enable/disable state ────────────────────────────────────────────────────
def _read_state():
    try:
        with open(paths.ADDONS_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"disabled": []}


def _write_state(state):
    os.makedirs(paths.USER_DATA, exist_ok=True)
    tmp = paths.ADDONS_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, paths.ADDONS_STATE_FILE)


def _disabled_names():
    state = _read_state()
    names = state.get("disabled")
    return {str(n) for n in names} if isinstance(names, list) else set()


# ─── discovery ───────────────────────────────────────────────────────────────
def _bundled_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "bundled_addons")


def _discover_bundled():
    """Addons shipped inside the mcpanel package itself."""
    out = []
    root = _bundled_dir()
    if not os.path.isdir(root):
        return out
    for entry in sorted(os.listdir(root)):
        if entry.startswith("_") or entry.startswith("."):
            continue
        pkg = os.path.join(root, entry)
        if os.path.isfile(os.path.join(pkg, "__init__.py")):
            out.append((entry, "bundled", pkg))
    return out


def _iter_entry_points(group):
    """importlib.metadata.entry_points() changed shape across 3.8→3.12; this
    covers both the old dict form and the modern selectable form."""
    try:
        from importlib import metadata
    except Exception:
        return []
    try:
        eps = metadata.entry_points()
    except Exception:
        return []
    select = getattr(eps, "select", None)
    if callable(select):
        try:
            return list(select(group=group))
        except Exception:
            return []
    try:
        return list(eps.get(group, []))  # 3.8 / 3.9
    except Exception:
        return []


def _discover_pip():
    out = []
    for ep in _iter_entry_points(ENTRY_POINT_GROUP):
        try:
            out.append((ep.name, "pip", getattr(ep, "value", str(ep))))
        except Exception:
            continue
    return out


def _discover_user():
    out = []
    root = paths.ADDONS_DIR
    if not os.path.isdir(root):
        return out
    for entry in sorted(os.listdir(root)):
        if entry.startswith("_") or entry.startswith("."):
            continue
        full = os.path.join(root, entry)
        if os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
            out.append((entry, "user", full))
        elif entry.endswith(".py"):
            out.append((entry[:-3], "user", full))
    return out


# ─── importing ───────────────────────────────────────────────────────────────
def _safe_modname(name):
    safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in name)
    return "mcpanel._addon_" + safe


def _import_addon(rec):
    """Imports the addon module. Bundled addons go through the normal package
    machinery (so their own relative imports just work); user addons are loaded
    from an explicit file location under a private module name, so a file
    called `json.py` can never shadow the stdlib."""
    if rec.source == "bundled":
        return importlib.import_module("mcpanel.bundled_addons." + rec.name)

    if rec.source == "pip":
        for ep in _iter_entry_points(ENTRY_POINT_GROUP):
            if ep.name == rec.name:
                return ep.load()
        raise ImportError("entry point disappeared during load: " + rec.name)

    modname = _safe_modname(rec.name)
    if os.path.isdir(rec.location):
        init = os.path.join(rec.location, "__init__.py")
        spec = importlib.util.spec_from_file_location(
            modname, init, submodule_search_locations=[rec.location])
    else:
        spec = importlib.util.spec_from_file_location(modname, rec.location)
    if spec is None or spec.loader is None:
        raise ImportError("could not build an import spec for " + rec.location)

    module = importlib.util.module_from_spec(spec)
    # Registered before exec so the addon's own submodule imports resolve.
    sys.modules[modname] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(modname, None)
        raise
    return module


def _validate_meta(rec, module):
    meta = getattr(module, "ADDON", None)
    if not isinstance(meta, dict):
        rec.status = ST_INVALID
        rec.error = "module does not define an ADDON metadata dict"
        return False

    rec.version = str(meta.get("version", "") or "")
    rec.description = str(meta.get("description", "") or "")
    rec.author = str(meta.get("author", "") or "")
    rec.url = str(meta.get("url", "") or "")
    rec.api_version = meta.get("api_version")

    # The directory / entry-point name is what the user actually types, so it
    # stays authoritative; a declared name that disagrees is recorded so
    # `addons info` can show the discrepancy instead of hiding it.
    declared = meta.get("name")
    rec.declared_name = str(declared) if declared else ""

    if not rec.version:
        rec.status = ST_INVALID
        rec.error = "ADDON metadata is missing a 'version'"
        return False
    if rec.api_version != ADDON_API_VERSION:
        rec.status = ST_API_MISMATCH
        rec.error = (
            "addon targets addon API v{} but this CLI implements v{}".format(
                rec.api_version, ADDON_API_VERSION)
        )
        return False
    if not callable(getattr(module, "register", None)):
        rec.status = ST_INVALID
        rec.error = "module does not define a register(api) function"
        return False
    return True


# ─── registry ────────────────────────────────────────────────────────────────
_records = None       # list[Addon] once load() has run
_startup_done = False


def disabled_by_env():
    return os.environ.get("MCPANEL_NO_ADDONS", "") not in ("", "0", "false", "False")


def load(force=False):
    """Discovers and imports every enabled addon. Idempotent."""
    global _records
    if _records is not None and not force:
        return _records

    _records = []
    if disabled_by_env():
        return _records

    disabled = _disabled_names()
    seen = {}

    for name, source, location in (_discover_bundled() + _discover_pip() + _discover_user()):
        rec = Addon(name, source, location)

        if name in seen:
            rec.status = ST_DUPLICATE
            rec.error = "another addon named '{}' was already loaded from {}".format(
                name, seen[name].source)
            _records.append(rec)
            continue
        seen[name] = rec

        if name in disabled:
            rec.status = ST_DISABLED
            _records.append(rec)
            continue

        try:
            module = _import_addon(rec)
        except BaseException:
            rec.status = ST_ERROR
            rec.error = traceback.format_exc()
            _records.append(rec)
            continue

        if not _validate_meta(rec, module):
            _records.append(rec)
            continue

        rec.module = module
        on_load = getattr(module, "on_load", None)
        if callable(on_load):
            try:
                on_load()
            except BaseException:
                rec.status = ST_ERROR
                rec.error = traceback.format_exc()
                rec.module = None
                _records.append(rec)
                continue

        _records.append(rec)

    return _records


def records():
    return load()


def find(name):
    for rec in load():
        if rec.name == name:
            return rec
    return None


def run_startup_hooks():
    """Called once by main(), after the CLI's own startup scan."""
    global _startup_done
    if _startup_done:
        return
    _startup_done = True
    for rec in load():
        if rec.status != ST_LOADED or rec.module is None:
            continue
        hook = getattr(rec.module, "on_startup", None)
        if not callable(hook):
            continue
        try:
            hook()
        except BaseException:
            rec.status = ST_ERROR
            rec.error = traceback.format_exc()
            rec.module = None


# ─── the object handed to register() ─────────────────────────────────────────
class _Helpers:
    """The CLI's own modules, so an addon never has to import private paths."""

    def __init__(self):
        from . import paths as _paths, config as _config, runstate as _runstate
        from . import render as _render, util as _util
        self.paths = _paths
        self.config = _config
        self.runstate = _runstate
        self.render = _render
        self.util = _util


class AddonAPI:
    """Passed to an addon's `register()`. One instance per parser tree, so it is
    constructed twice per process — once for the human tree and once for `api`."""

    def __init__(self, sub, record):
        self._sub = sub
        self._record = record
        self.helpers = _Helpers()

    # ── commands ──
    def group(self, name, help=None, parent=None):
        target = parent if parent is not None else self._sub
        existing = getattr(target, "_name_parser_map", {})
        if name in existing:
            raise ValueError(
                "command group '{}' already exists — pick another name".format(name))
        p = target.add_parser(name, help=help)
        return p.add_subparsers(dest="noun", metavar="<command>", required=True)

    def command(self, parent, name, func, action, help=None, progress_ok=False):
        p = parent.add_parser(name, help=help)
        # Same defaults cli.leaf() sets, so addon commands flow through main()
        # exactly like built-ins do.
        p.set_defaults(func=func, action=action, progress_ok=progress_ok)
        if action not in self._record.actions:
            self._record.actions.append(action)
        from . import render as _render
        _render.ADDON_ACTIONS.add(action)
        return p

    def renderer(self, action, fn):
        from . import render as _render
        _render.ADDON_RENDERERS[action] = fn

    # ── storage ──
    def data_dir(self, name=None):
        d = os.path.join(paths.ADDON_DATA_DIR, name or self._record.name)
        os.makedirs(d, exist_ok=True)
        return d


def register_all(sub):
    """Mounts every loaded addon's commands into one parser tree. Called by
    cli.add_commands() at the end of the built-ins, once per tree."""
    for rec in load():
        if rec.status != ST_LOADED or rec.module is None:
            continue
        try:
            rec.module.register(AddonAPI(sub, rec))
        except BaseException:
            rec.status = ST_ERROR
            rec.error = traceback.format_exc()
            rec.module = None


# ─── `mcpanel addons ...` command handlers ───────────────────────────────────
def cmd_list(args=None, progress=None):
    if disabled_by_env():
        return {
            "addons": [],
            "loaded": False,
            "reason": "MCPANEL_NO_ADDONS is set — addon loading is disabled",
        }
    return {"addons": [r.to_dict() for r in load()], "loaded": True}


def cmd_info(args, progress=None):
    rec = find(args.name)
    if rec is None:
        return {"error": "No addon named '{}'".format(args.name)}
    return {"addon": rec.to_dict()}


def _set_enabled(name, enabled):
    rec = find(name)
    if rec is None:
        return {"error": "No addon named '{}'".format(name)}
    state = _read_state()
    disabled = [str(n) for n in state.get("disabled", []) if isinstance(state.get("disabled"), list)]
    if enabled:
        disabled = [n for n in disabled if n != name]
    elif name not in disabled:
        disabled.append(name)
    state["disabled"] = disabled
    _write_state(state)
    return {
        "success": True,
        "name": name,
        "enabled": enabled,
        # The change lands on the next invocation: commands were already
        # mounted for this process before we got here.
        "note": "takes effect on the next mcpanel command",
    }


def cmd_enable(args, progress=None):
    return _set_enabled(args.name, True)


def cmd_disable(args, progress=None):
    return _set_enabled(args.name, False)


def _extract_zip(zip_path, dest_root):
    """Unpacks an addon zip into dest_root, returning the installed name."""
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist()
                 if not n.startswith("__MACOSX") and not n.startswith("._")]
        if not names:
            raise ValueError("archive is empty")

        # Reject absolute paths and traversal before writing anything.
        for n in names:
            if os.path.isabs(n) or ".." in n.replace("\\", "/").split("/"):
                raise ValueError("archive contains an unsafe path: " + n)

        tops = {n.replace("\\", "/").split("/")[0] for n in names}
        rooted = len(tops) == 1 and any(
            n.replace("\\", "/").endswith("/__init__.py") for n in names)

        if rooted:
            name = tops.pop()
            target = os.path.join(dest_root, name)
            if os.path.exists(target):
                shutil.rmtree(target)
            zf.extractall(dest_root)
            return name

        # Flat archive: everything belongs under a directory named for the zip.
        name = os.path.splitext(os.path.basename(zip_path))[0]
        target = os.path.join(dest_root, name)
        if os.path.exists(target):
            shutil.rmtree(target)
        os.makedirs(target, exist_ok=True)
        zf.extractall(target)
        return name


def cmd_install(args, progress=None):
    source = args.source
    os.makedirs(paths.ADDONS_DIR, exist_ok=True)
    tmpdir = None

    try:
        if source.startswith("http://") or source.startswith("https://"):
            from . import http as _http
            tmpdir = tempfile.mkdtemp(prefix="mcpanel-addon-")
            local = os.path.join(tmpdir, "addon.zip")
            if progress:
                progress(10, "Downloading addon…")
            _http.download_file(source, local)
            name = _extract_zip(local, paths.ADDONS_DIR)

        elif os.path.isdir(source):
            if not os.path.isfile(os.path.join(source, "__init__.py")):
                return {"error": "That directory is not a Python package (no __init__.py)"}
            name = os.path.basename(os.path.abspath(source.rstrip("/\\")))
            target = os.path.join(paths.ADDONS_DIR, name)
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.copytree(source, target)

        elif source.endswith(".zip") and os.path.isfile(source):
            name = _extract_zip(source, paths.ADDONS_DIR)

        elif source.endswith(".py") and os.path.isfile(source):
            name = os.path.basename(source)[:-3]
            shutil.copy2(source, os.path.join(paths.ADDONS_DIR, name + ".py"))

        else:
            return {"error": "Not an addon: expected a .py file, a package directory, "
                             "a .zip, or an https:// URL to a zip"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    if progress:
        progress(100, "Installed")

    # Re-run discovery so the caller learns whether the new addon actually loads.
    load(force=True)
    rec = find(name)
    return {
        "success": True,
        "name": name,
        "addon": rec.to_dict() if rec else None,
    }


def cmd_remove(args, progress=None):
    rec = find(args.name)
    if rec is None:
        return {"error": "No addon named '{}'".format(args.name)}
    if rec.source != "user":
        return {"error": "'{}' is a {} addon — it is not installed in {} and cannot be "
                         "removed this way. Disable it instead: mcpanel addons disable {}".format(
                             rec.name, rec.source, paths.ADDONS_DIR, rec.name)}
    try:
        if os.path.isdir(rec.location):
            shutil.rmtree(rec.location)
        elif os.path.isfile(rec.location):
            os.remove(rec.location)
    except Exception as e:
        return {"error": str(e)}

    # Dropping it from `disabled` too, so a later reinstall isn't silently off.
    state = _read_state()
    if isinstance(state.get("disabled"), list):
        state["disabled"] = [n for n in state["disabled"] if n != rec.name]
        _write_state(state)

    load(force=True)
    return {"success": True, "name": rec.name, "removed": rec.location}
