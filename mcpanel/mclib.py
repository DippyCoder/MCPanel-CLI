"""`mcpanel mclib` — install addons from addon libraries such as MCLib.

    mcpanel mclib <library|repo-url> <list|install|update|downgrade|remove> [name] [version]
    mclib         <library|repo-url> <list|install|update|downgrade|remove> [name] [version]

A *library* is a JSON index of addons (name, description, project URL,
authors, which MCPanel product it's for). MCLib is the default one; more can be
added to `<addons dir>/libraries.json`. With a repository URL instead of a
library name, the library lookup is skipped and the name argument with it:
`mcpanel mclib https://github.com/o/r install [version]`.

Code is only ever pulled from **GitHub or Codeberg releases** — a library entry
or URL pointing anywhere else is refused. A specific release can be pinned by
its tag (`install foo v1.2.0`), otherwise the newest stable release is used.

Every addon, whichever product it targets (CLI, MCPanel desktop, WebUI), is
installed into the CLI's own addons directory and runs inside the CLI: the
desktop app and the WebUI reach it through `mcpanel api ...` like everything
else.

Before anything is downloaded from an addon's repository, the user must accept
the third-party disclaimer: interactively (y/N), or with `--yes`. In `api`
mode an unaccepted install fails with code `disclaimer_required`, carrying the
disclaimer text and the library's terms URL so a UI can show them and retry
with `--yes`.
"""

import datetime
import json
import os
import re
import shutil
import sys
import tempfile
import time
from urllib.parse import quote, urlparse

from . import addons, paths
from .errors import fail, CLIError

LIBRARIES_FILE = "libraries.json"
INSTALLED_FILE = "mclib-installed.json"

DEFAULT_LIBRARIES = {
    "mclib": {
        "url": "https://raw.githubusercontent.com/DippyCoder/MCLib/main/index.json",
        "description": "The official MCPanel addon library",
    },
}

ACTIONS = ("list", "install", "update", "downgrade", "remove")
PRODUCTS = ("cli", "mcpanel", "webui")
PRODUCT_NAMES = {"cli": "MCPanel-CLI", "mcpanel": "MCPanel", "webui": "MCPanel-WebUI"}

# The only code hosts addons may be pulled from. Enforced here, not just in
# a library's terms, so a library (or a pasted URL) can't point elsewhere.
ALLOWED_HOSTS = {"github.com": "github", "codeberg.org": "codeberg"}

DISCLAIMER = (
    "You are about to download and run THIRD-PARTY software. Addons are not "
    "written, reviewed or endorsed by the MCPanel developers or the maintainers "
    "of the library listing them. An addon runs with the same permissions as "
    "MCPanel-CLI itself and can read, change or delete anything your user "
    "account can, including your servers and their worlds. The creators of "
    "MCPanel and of the library are not liable for any harm, damage or data "
    "loss caused by an addon. Only install addons from authors you trust."
)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


# ─── files in the addons directory ───────────────────────────────────────────

def _addons_path(name):
    return os.path.join(paths.ADDONS_DIR, name)


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else default
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def libraries():
    """{name: {"url": ..., "description": ...}} from libraries.json, created
    with the defaults on first use so users have a file to extend."""
    path = _addons_path(LIBRARIES_FILE)
    if not os.path.exists(path):
        try:
            _write_json(path, {
                "_comment": "Addon libraries `mcpanel mclib <name> ...` can pull from. "
                            "Add an entry pointing at another library's index.json.",
                "libraries": DEFAULT_LIBRARIES,
            })
        except OSError:
            pass
    data = _read_json(path, {})
    libs = data.get("libraries")
    if not isinstance(libs, dict):
        return dict(DEFAULT_LIBRARIES)
    out = {}
    for name, spec in libs.items():
        if isinstance(spec, str):
            spec = {"url": spec}
        if isinstance(spec, dict) and isinstance(spec.get("url"), str):
            out[str(name)] = {"url": spec["url"], "description": str(spec.get("description", ""))}
    return out


def _installed():
    data = _read_json(_addons_path(INSTALLED_FILE), {})
    recs = data.get("addons")
    if not isinstance(recs, dict):
        return {}
    # Drop records for addons removed some other way (`mcpanel addons remove`,
    # deleting the folder by hand), so nothing claims a version that's gone.
    live = {k: r for k, r in recs.items()
            if isinstance(r, dict) and (os.path.isdir(_addons_path(r.get("folder") or "\0"))
                                        or os.path.isfile(_addons_path((r.get("folder") or "\0") + ".py")))}
    if len(live) != len(recs):
        try:
            _save_installed(live)
        except OSError:
            pass
    return live


def _save_installed(recs):
    _write_json(_addons_path(INSTALLED_FILE), {
        "_comment": "Managed by `mcpanel mclib` — which addons came from which library/release.",
        "addons": recs,
    })


# ─── projects & releases ─────────────────────────────────────────────────────

def parse_project(url):
    """(platform, owner, repo) for a GitHub/Codeberg repository URL. Raises
    CLIError for any other host — the hard platform rule."""
    if not isinstance(url, str) or not url.strip():
        raise CLIError("mclib_invalid_project", "No project URL given.")
    u = urlparse(url.strip())
    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if u.scheme != "https" or host not in ALLOWED_HOSTS:
        raise CLIError(
            "mclib_platform_not_allowed",
            f"'{url}' is not allowed: addons may only be installed from "
            "https://github.com or https://codeberg.org repositories.")
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        raise CLIError("mclib_invalid_project", f"'{url}' does not point at a repository (owner/name).")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return ALLOWED_HOSTS[host], owner, repo


def project_url(platform, owner, repo):
    host = next(h for h, p in ALLOWED_HOSTS.items() if p == platform)
    return f"https://{host}/{owner}/{repo}"


def fetch_releases(project):
    """Newest-first [{version, title, prerelease, published, zip}] for a
    project URL. Drafts are skipped; `zip` prefers an attached .zip asset and
    falls back to the release's source archive."""
    from . import http
    platform, owner, repo = parse_project(project)
    o, r = quote(owner, safe=""), quote(repo, safe="")
    if platform == "github":
        api = f"https://api.github.com/repos/{o}/{r}/releases?per_page=100"
    else:
        api = f"https://codeberg.org/api/v1/repos/{o}/{r}/releases?limit=50"
    try:
        data = http.fetch_json(api)
    except Exception as e:
        raise CLIError("mclib_fetch_failed", f"Could not read the releases of {project}: {e}")
    if not isinstance(data, list):
        raise CLIError("mclib_fetch_failed", f"Unexpected releases response from {project}")
    out = []
    for rel in data:
        if not isinstance(rel, dict) or rel.get("draft") or not rel.get("tag_name"):
            continue
        assets = [a for a in (rel.get("assets") or []) if isinstance(a, dict)]
        asset = next((a for a in assets
                      if str(a.get("name", "")).lower().endswith(".zip")
                      and a.get("browser_download_url")), None)
        out.append({
            "version": str(rel["tag_name"]),
            "title": str(rel.get("name") or rel["tag_name"]),
            "prerelease": bool(rel.get("prerelease")),
            "published": rel.get("published_at") or rel.get("created_at") or "",
            "zip": asset["browser_download_url"] if asset else rel.get("zipball_url"),
        })
    out.sort(key=lambda x: x["published"] or "", reverse=True)
    return out


def _norm_version(v):
    return str(v or "").strip().lstrip("vV")


def _find_release(releases, version):
    want = _norm_version(version)
    for rel in releases:
        if rel["version"] == version or _norm_version(rel["version"]) == want:
            return rel
    return None


def _latest(releases):
    stable = [r for r in releases if not r["prerelease"]]
    return (stable or releases or [None])[0]


def _index_of(releases, version):
    for i, rel in enumerate(releases):
        if _norm_version(rel["version"]) == _norm_version(version):
            return i
    return None


# ─── libraries ───────────────────────────────────────────────────────────────

def _normalize_entry(raw):
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name", "")).strip()
    if not _NAME_RE.match(name):
        return None
    products = raw.get("products", raw.get("product", []))
    if isinstance(products, str):
        products = [products]
    authors = raw.get("authors", raw.get("author", []))
    if isinstance(authors, str):
        authors = [authors]
    return {
        "name": name,
        "description": str(raw.get("description", "")),
        "project": str(raw.get("project", "")),
        "authors": [str(a) for a in authors if a],
        "products": [str(p).lower() for p in products if str(p).lower() in PRODUCTS],
    }


def load_library(name):
    """(library spec, index dict with normalized `addons`) for a library name."""
    from . import http
    libs = libraries()
    if name not in libs:
        known = ", ".join(sorted(libs)) or "none"
        raise CLIError("mclib_unknown_library",
                       f"Unknown library '{name}'. Known libraries: {known}. "
                       f"Add more in {_addons_path(LIBRARIES_FILE)}, or pass a "
                       "GitHub/Codeberg repository URL instead.")
    spec = libs[name]
    try:
        index = http.fetch_json(spec["url"])
    except Exception as e:
        raise CLIError("mclib_fetch_failed", f"Could not load library '{name}' from {spec['url']}: {e}")
    if not isinstance(index, dict) or not isinstance(index.get("addons"), list):
        raise CLIError("mclib_index_invalid", f"Library '{name}' at {spec['url']} is not a valid index.")
    entries = [e for e in (_normalize_entry(a) for a in index["addons"]) if e]
    index = dict(index)
    index["addons"] = entries
    return spec, index


def _find_entry(index, name):
    for e in index["addons"]:
        if e["name"].lower() == str(name).lower():
            return e
    return None


# ─── install / remove ────────────────────────────────────────────────────────

def _download_and_install(release):
    """Downloads a release archive and installs the addon package inside it.
    Returns the installed addon's folder name."""
    from . import http
    url = release.get("zip")
    if not url:
        raise CLIError("mclib_no_archive", f"Release {release['version']} has no downloadable archive.")
    tmpdir = tempfile.mkdtemp(prefix="mcpanel-mclib-")
    try:
        archive = os.path.join(tmpdir, "release.zip")
        try:
            http.download_file(url, archive)
        except Exception as e:
            raise CLIError("download_failed", f"Could not download {release['version']}: {e}")
        unpacked = os.path.join(tmpdir, "unpacked")
        os.makedirs(unpacked)
        try:
            addons._safe_extract_zip(archive, unpacked)
            root = addons._find_addon_root(unpacked)
        except Exception as e:
            raise CLIError("addon_install_failed", f"Release {release['version']} is not a usable archive: {e}")
        if root is None:
            raise CLIError("addon_install_failed",
                           f"Release {release['version']} contains no addon package "
                           "(a folder with an __init__.py).")
        os.makedirs(paths.ADDONS_DIR, exist_ok=True)
        try:
            return addons._install_tree(root)
        except Exception as e:
            raise CLIError("addon_install_failed", f"Could not install the addon: {e}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _addon_state(folder):
    addons.load(force=True)
    rec = addons.find(folder)
    return rec.to_dict() if rec else None


def _remove_folder(folder):
    target = os.path.join(paths.ADDONS_DIR, folder)
    if not paths.is_valid_id(folder) or not paths.is_within(target, paths.ADDONS_DIR):
        raise CLIError("invalid_path", f"Refusing to remove {target}")
    if os.path.isdir(target):
        shutil.rmtree(target)
    elif os.path.isfile(target + ".py"):
        os.remove(target + ".py")


# ─── the command ─────────────────────────────────────────────────────────────

def _disclaimer_doc(tos=None):
    return fail("disclaimer_required",
                "Installing third-party addons requires accepting the disclaimer — "
                "rerun with --yes to accept it.",
                disclaimer=DISCLAIMER, tos=tos)


def _refused(confirm, tos):
    """Error for a disclaimer that wasn't accepted: the full text for API
    callers (so a UI can show it), a short note when a human just said no."""
    if confirm is None:
        return _disclaimer_doc(tos)
    return fail("disclaimer_declined", "Cancelled — the third-party disclaimer was not accepted.")


def _require_disclaimer(args, confirm, tos):
    """True when accepted. `confirm(text)` asks interactively (None in API
    mode / without a terminal)."""
    if getattr(args, "yes", False):
        return True
    if confirm is None:
        return False
    text = DISCLAIMER + (f"\n\nLibrary terms: {tos}" if tos else "")
    return bool(confirm(text))


def _resolve_target(args):
    """(record key, entry, library name, tos) for the command's target."""
    source = args.source
    if "://" in source:
        platform, owner, repo = parse_project(source)
        project = project_url(platform, owner, repo)
        entry = {"name": repo, "description": "", "project": project, "authors": [owner], "products": []}
        return repo.lower(), entry, None, None
    if not args.name:
        raise CLIError("invalid_arguments", f"Which addon? Usage: mcpanel mclib {source} {args.op} <name> [version]")
    _, index = load_library(source)
    entry = _find_entry(index, args.name)
    if entry is None:
        raise CLIError("mclib_addon_not_found", f"'{args.name}' is not listed in library '{source}'.")
    parse_project(entry["project"])  # platform rule, before any repo request
    return entry["name"].lower(), entry, source, index.get("tos")


def _do_list(args):
    installed = _installed()
    if "://" in args.source:
        rels = fetch_releases(args.source)
        rec = next((r for r in installed.values() if r.get("project") == project_url(*parse_project(args.source))), None)
        return {"mode": "releases", "project": args.source, "releases": rels,
                "installedVersion": rec.get("version") if rec else None}
    spec, index = load_library(args.source)
    if args.name:
        entry = _find_entry(index, args.name)
        if entry is None:
            raise CLIError("mclib_addon_not_found", f"'{args.name}' is not listed in library '{args.source}'.")
        rec = installed.get(entry["name"].lower())
        return {"mode": "releases", "library": args.source, "addon": entry,
                "releases": fetch_releases(entry["project"]),
                "installedVersion": rec.get("version") if rec else None}
    out = []
    for e in index["addons"]:
        rec = installed.get(e["name"].lower())
        out.append({**e, "installedVersion": rec.get("version") if rec else None})
    return {"mode": "library", "library": args.source, "title": index.get("name", args.source),
            "description": index.get("description", ""), "tos": index.get("tos"),
            "issues": index.get("issues"), "addons": out}


def _install_release(key, entry, library, release, previous=None):
    folder = _download_and_install(release)
    recs = _installed()
    # The same addon installed earlier under another key (by URL vs. from a
    # library) shares this folder: keep one record, or removing either would
    # pull the folder out from under the other.
    for other in [k for k, r in recs.items()
                  if k != key and (r.get("project") == entry["project"] or r.get("folder") == folder)]:
        recs.pop(other)
    recs[key] = {
        "name": entry["name"],
        "folder": folder,
        "library": library,
        "project": entry["project"],
        "products": entry.get("products", []),
        "version": release["version"],
        "installedAt": int(time.time() * 1000),
    }
    _save_installed(recs)
    # Different package folder than the version it replaced: drop the old one.
    if previous and previous.get("folder") and previous["folder"] != folder:
        try:
            _remove_folder(previous["folder"])
        except Exception:
            pass
    return {
        "success": True,
        "name": entry["name"],
        "version": release["version"],
        "previousVersion": previous.get("version") if previous else None,
        "folder": folder,
        "products": entry.get("products", []),
        "addon": _addon_state(folder),
    }


def _do_install(args, confirm, mode):
    key, entry, library, tos = _resolve_target(args)
    recs = _installed()
    current = recs.get(key)

    if mode in ("update", "downgrade") and current is None:
        raise CLIError("mclib_not_installed", f"'{entry['name']}' is not installed from a library — install it first.")

    # Disclaimer before ANY request to the addon's own repository.
    if not _require_disclaimer(args, confirm, tos):
        return _refused(confirm, tos)

    releases = fetch_releases(entry["project"])
    if not releases:
        raise CLIError("mclib_no_releases", f"{entry['project']} has no releases to install.")

    if args.version:
        target = _find_release(releases, args.version)
        if target is None:
            raise CLIError("mclib_version_not_found",
                           f"{entry['project']} has no release '{args.version}'. "
                           f"Available: {', '.join(r['version'] for r in releases[:10])}")
    elif mode == "downgrade":
        idx = _index_of(releases, current.get("version"))
        if idx is None or idx + 1 >= len(releases):
            raise CLIError("mclib_version_not_found",
                           f"No older release than {current.get('version')} to downgrade to.")
        target = releases[idx + 1]
    else:
        target = _latest(releases)

    if current is not None:
        cur_idx = _index_of(releases, current.get("version"))
        tgt_idx = _index_of(releases, target["version"])
        if cur_idx == tgt_idx and cur_idx is not None:
            return {"success": True, "name": entry["name"], "version": target["version"],
                    "unchanged": True, "message": f"{entry['name']} is already at {target['version']}."}
        if cur_idx is not None and tgt_idx is not None:
            if mode == "update" and tgt_idx > cur_idx:
                raise CLIError("invalid_arguments",
                               f"{target['version']} is older than the installed {current['version']} — "
                               "use downgrade.")
            if mode == "downgrade" and tgt_idx < cur_idx:
                raise CLIError("invalid_arguments",
                               f"{target['version']} is newer than the installed {current['version']} — "
                               "use update.")
    return _install_release(key, entry, library, target, previous=current)


def _do_update_all(args, confirm):
    recs = {k: r for k, r in _installed().items() if r.get("library") == args.source}
    if not recs:
        return {"success": True, "updated": [], "message": f"Nothing from '{args.source}' is installed."}
    _, index = load_library(args.source)
    if not _require_disclaimer(args, confirm, index.get("tos")):
        return _refused(confirm, index.get("tos"))
    results = []
    for key, rec in recs.items():
        sub = argparse_ns(source=args.source, op="update", name=rec["name"], version=None, yes=True)
        try:
            results.append(_do_install(sub, None, "update"))
        except CLIError as e:
            results.append({**e.to_dict(), "name": rec["name"]})
    failed = [r for r in results if r.get("error")]
    out = {"success": not failed, "updated": results}
    if failed:
        out.update(fail("mclib_update_failed", f"{len(failed)} addon(s) could not be updated."))
    return out


def _do_remove(args):
    recs = _installed()
    if "://" in args.source:
        project = project_url(*parse_project(args.source))
        key = next((k for k, r in recs.items() if r.get("project") == project), None)
    else:
        if not args.name:
            raise CLIError("invalid_arguments", f"Which addon? Usage: mcpanel mclib {args.source} remove <name>")
        key = args.name.lower() if args.name.lower() in recs else None
    if key is None:
        raise CLIError("mclib_not_installed", "That addon is not installed from a library.")
    rec = recs.pop(key)
    _remove_folder(rec.get("folder") or rec["name"])
    _save_installed(recs)
    addons.load(force=True)
    return {"success": True, "name": rec["name"], "removedVersion": rec.get("version")}


class argparse_ns:  # tiny stand-in for argparse.Namespace without the import
    def __init__(self, **kw):
        self.__dict__.update(kw)


def run(args, confirm=None):
    """Core entry point. `confirm(text) -> bool` is the interactive prompt, or
    None when no human is there to answer (API mode)."""
    action = (args.op or "").lower()
    if action not in ACTIONS:
        raise CLIError("invalid_arguments",
                       f"Unknown action '{args.op}'. Use one of: {', '.join(ACTIONS)}")
    # URL mode skips the name: `mclib <url> install [version]`.
    if "://" in args.source and args.name and not args.version:
        args.version, args.name = args.name, None
    args.op = action
    if action == "list":
        return _do_list(args)
    if action == "remove":
        return _do_remove(args)
    if action == "update" and not args.name and "://" not in args.source:
        return _do_update_all(args, confirm)
    return _do_install(args, confirm, action)


def _interactive_confirm(text):
    import textwrap
    print()
    print("  ⚠  THIRD-PARTY SOFTWARE")
    for para in text.split("\n"):
        for line in textwrap.wrap(para, 76) or [""]:
            print("  " + line)
    print()
    try:
        answer = input("  Accept and continue? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def cmd_mclib(args, progress=None):
    """argparse handler for `mcpanel [api] mclib ...`."""
    rest = list(getattr(args, "rest", []) or [])
    args.name = rest[0] if len(rest) > 0 else None
    args.version = rest[1] if len(rest) > 1 else None
    if len(rest) > 2:
        return fail("invalid_arguments", "Too many arguments. Usage: mcpanel mclib "
                    "<library|url> <list|install|update|downgrade|remove> [name] [version]")
    interactive = not getattr(args, "json", False) and sys.stdin.isatty()
    return run(args, _interactive_confirm if interactive else None)


# ─── human output ────────────────────────────────────────────────────────────

def _date(iso):
    try:
        return datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return str(iso)[:10]


def render_mclib(result, args):
    from . import render as r
    if isinstance(result, dict) and result.get("error"):
        print(r.red("✗ ") + result["error"])
        if result.get("disclaimer"):
            print(r.dim("\n" + result["disclaimer"]))
            if result.get("tos"):
                print(r.dim(f"Terms: {result['tos']}"))
        return
    mode = result.get("mode")
    if mode == "library":
        print(r.bold(result.get("title", "")) + r.dim(f"  ({result.get('library')})"))
        if result.get("description"):
            print(r.dim(result["description"]))
        print()
        for a in result.get("addons", []):
            products = ", ".join(PRODUCT_NAMES.get(p, p) for p in a["products"]) or "—"
            inst = r.green(f"  ✓ installed {a['installedVersion']}") if a.get("installedVersion") else ""
            print(r.bold(a["name"]) + inst)
            if a["description"]:
                print("  " + a["description"])
            print(r.dim(f"  for: {products} · by {', '.join(a['authors']) or 'unknown'} · {a['project']}"))
            print()
        if not result.get("addons"):
            print(r.dim("This library lists no addons."))
        if result.get("tos"):
            print(r.dim(f"Terms: {result['tos']}"))
        return
    if mode == "releases":
        addon = result.get("addon")
        head = addon["name"] if addon else result.get("project", "")
        print(r.bold(head) + r.dim(f"  {addon['project']}" if addon else ""))
        if addon and addon.get("description"):
            print(r.dim(addon["description"]))
        inst = result.get("installedVersion")
        for rel in result.get("releases", []):
            tag = rel["version"] + (r.yellow(" (pre-release)") if rel["prerelease"] else "")
            mark = r.green("  ← installed") if inst and _norm_version(inst) == _norm_version(rel["version"]) else ""
            print(f"  {tag:<28} {r.dim(_date(rel['published']))}{mark}")
        if not result.get("releases"):
            print(r.dim("  No releases."))
        return
    if "updated" in result:
        if result.get("message"):
            print(r.dim(result["message"]))
        for u in result["updated"]:
            render_mclib(u, args)
        return
    if result.get("unchanged"):
        print(r.dim(result.get("message", "Already up to date.")))
        return
    if result.get("removedVersion") is not None or ("removedVersion" in result):
        print(r.green("✓ ") + f"Removed {result['name']}" + r.dim(f" ({result.get('removedVersion')})"))
        return
    if result.get("success"):
        prev = result.get("previousVersion")
        verb = f"{prev} → {result['version']}" if prev else result["version"]
        print(r.green("✓ ") + f"{result['name']} {verb}" + r.dim(f"  → addons/{result.get('folder')}"))
        state = (result.get("addon") or {}).get("status")
        if state and state != "loaded":
            print(r.yellow(f"  ⚠ installed, but the addon reports '{state}' — see: mcpanel addons info {result.get('folder')}"))
        products = [p for p in result.get("products", []) if p != "cli"]
        if products:
            print(r.dim("  For " + ", ".join(PRODUCT_NAMES[p] for p in products)
                        + " — runs inside MCPanel-CLI; restart the app/WebUI to pick it up."))
