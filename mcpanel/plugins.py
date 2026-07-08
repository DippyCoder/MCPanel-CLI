"""Plugin / Mod search and install via Modrinth, Hangar, and SpigotMC (Spiget)."""

import os

from .http import fetch_json, fetch_json_with_headers, download_file
from .config import load_config, find_server

MODDED_SOFTWARES = {"fabric"}

# Maps software names to Modrinth loader tags
MODRINTH_LOADERS = {
    "fabric": ["fabric"],
    "velocity": ["velocity"],
    "paper": ["paper", "purpur", "spigot", "bukkit"],
    "purpur": ["paper", "purpur", "spigot", "bukkit"],
    "folia": ["folia", "paper"],
    "leaf": ["paper", "purpur", "spigot", "bukkit"],
    "spigot": ["spigot", "bukkit"],
}


def _dest_dir(software):
    return "mods" if software in MODDED_SOFTWARES else "plugins"


def _safe_filename(name):
    return "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip()


def _html_to_text(raw_html):
    """Best-effort plain-text rendering of a description that may contain
    markdown or raw HTML (Spiget resource descriptions are base64-encoded
    HTML) — good enough for a details popup, no need for a real markdown/HTML
    dependency just to display body text."""
    import re
    import html as _html_mod
    text = re.sub(r"(?is)<br\s*/?>", "\n", raw_html)
    text = re.sub(r"(?is)</(p|li|div|h[1-6])>", "\n\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = _html_mod.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ─── Modrinth ─────────────────────────────────────────────────────────────────

def search_modrinth(query, software="paper", mc_version=None, limit=20, offset=0):
    import json as _json
    from urllib.parse import quote
    facets = []
    if software in MODDED_SOFTWARES:
        facets.append(["project_type:mod"])
        facets.append(["categories:fabric"])
    else:
        facets.append(["project_type:plugin"])
        loaders = MODRINTH_LOADERS.get(software, ["paper"])
        facets.append([f"categories:{loaders[0]}"])
    if mc_version:
        facets.append([f"versions:{mc_version}"])
    encoded_facets = quote(_json.dumps(facets))
    url = (
        "https://api.modrinth.com/v2/search"
        f"?query={quote(query)}&limit={limit}&offset={offset}&facets={encoded_facets}"
    )
    data = fetch_json(url)
    results = [
        {
            "slug": h["slug"],
            "name": h["title"],
            "author": h["author"],
            "downloads": h.get("downloads", 0),
            "updatedAt": h.get("date_modified", ""),
            "latestVersion": h.get("latest_version", ""),
            "description": h.get("description", ""),
            "platform": "modrinth",
            "iconUrl": h.get("icon_url"),
        }
        for h in data.get("hits", [])
    ]
    # Modrinth silently clamps `limit` to 100 per request regardless of what's
    # asked, so "hasMore" can't just check len(results) == limit — use the
    # real total instead.
    has_more = offset + len(results) < data.get("total_hits", 0)
    return results, has_more


def get_modrinth_download(slug, software="paper", mc_version=None):
    import json as _json
    from urllib.parse import quote
    loaders = MODRINTH_LOADERS.get(software, ["paper"])
    params = f"loaders={quote(_json.dumps(loaders))}"
    if mc_version:
        params += f"&game_versions={quote(_json.dumps([mc_version]))}"
    url = f"https://api.modrinth.com/v2/project/{slug}/version?{params}"
    versions = fetch_json(url)
    if not versions:
        raise RuntimeError(f"No compatible version found for {slug} on MC {mc_version}")
    files = versions[0].get("files", [])
    primary = next((f for f in files if f.get("primary")), files[0] if files else None)
    if not primary:
        raise RuntimeError("No download file found")
    return {"url": primary["url"], "filename": primary["filename"]}


def get_modrinth_version_download(version_id):
    """Fetch the exact file for a version the user picked from the version-
    history list — no loader/mc_version compatibility filtering, since they
    already chose this version deliberately."""
    v = fetch_json(f"https://api.modrinth.com/v2/version/{version_id}")
    files = v.get("files", [])
    primary = next((f for f in files if f.get("primary")), files[0] if files else None)
    if not primary:
        raise RuntimeError("No download file found for that version")
    return {"url": primary["url"], "filename": primary["filename"]}


def get_modrinth_info(slug, limit=25, offset=0):
    all_versions = fetch_json(f"https://api.modrinth.com/v2/project/{slug}/version")
    page = all_versions[offset:offset + limit]
    result = {
        "websiteUrl": f"https://modrinth.com/project/{slug}",
        "versions": [
            {
                "id": v["id"],
                "name": v.get("version_number") or "",
                "date": v.get("date_published", ""),
                "changelog": v.get("changelog") or "",
            }
            for v in page
        ],
        "hasMoreVersions": offset + limit < len(all_versions),
    }
    if offset == 0:
        # Only needed once — skip on "load more versions" pages.
        project = fetch_json(f"https://api.modrinth.com/v2/project/{slug}")
        result["longDescription"] = project.get("body") or ""
    return result


# ─── Hangar ───────────────────────────────────────────────────────────────────

def search_hangar(query, mc_version=None, limit=20, offset=0):
    url = (
        f"https://hangar.papermc.io/api/v1/projects"
        f"?query={query}&platform=PAPER&limit={limit}&offset={offset}"
    )
    if mc_version:
        url += f"&version={mc_version}"
    data = fetch_json(url)
    results = [
        {
            "slug": p.get("namespace", {}).get("slug", p["name"]),
            "name": p["name"],
            "author": p.get("namespace", {}).get("owner", ""),
            "downloads": p.get("stats", {}).get("downloads", 0),
            "updatedAt": p.get("lastUpdated", ""),
            "latestVersion": "",
            "description": p.get("description", ""),
            "platform": "hangar",
            "ownerName": p.get("namespace", {}).get("owner", ""),
            "iconUrl": p.get("avatarUrl"),
        }
        for p in data.get("result", [])
    ]
    # Hangar silently clamps `limit` to 50 per request regardless of what's
    # asked, so "hasMore" can't just check len(results) == limit — use the
    # real total instead.
    has_more = offset + len(results) < data.get("pagination", {}).get("count", 0)
    return results, has_more


def get_hangar_download(slug, owner, mc_version=None, pin_version=None):
    if pin_version:
        version = pin_version
    else:
        url = f"https://hangar.papermc.io/api/v1/projects/{owner}/{slug}/versions?platform=PAPER&limit=1"
        data = fetch_json(url)
        results = data.get("result", [])
        if not results:
            raise RuntimeError(f"No versions found for {slug} on Hangar")
        version = results[0]["name"]
    dl_url = (
        f"https://hangar.papermc.io/api/v1/projects/{owner}/{slug}"
        f"/versions/{version}/PAPER/download"
    )
    return {"url": dl_url, "filename": f"{slug}-{version}.jar"}


def get_hangar_info(slug, owner, limit=25, offset=0):
    url = (
        f"https://hangar.papermc.io/api/v1/projects/{owner}/{slug}/versions"
        f"?limit={limit}&offset={offset}"
    )
    data = fetch_json(url)
    results = data.get("result", [])
    total = data.get("pagination", {}).get("count", 0)
    result = {
        "websiteUrl": f"https://hangar.papermc.io/{owner}/{slug}",
        "versions": [
            {
                "id": v["name"],
                "name": v["name"],
                "date": v.get("createdAt", ""),
                "changelog": v.get("description") or "",
            }
            for v in results
        ],
        "hasMoreVersions": offset + len(results) < total,
    }
    if offset == 0:
        # Only needed once — skip on "load more versions" pages.
        project = fetch_json(f"https://hangar.papermc.io/api/v1/projects/{owner}/{slug}")
        result["longDescription"] = project.get("mainPageContent") or ""
    return result


# ─── SpigotMC (Spiget) ────────────────────────────────────────────────────────

def _resolve_spiget_authors(author_ids):
    """Spiget's search/browse results only ever embed {"id": N} for the
    author — the real username needs a separate per-author call. Resolve the
    unique ids in this batch concurrently instead of N sequential requests."""
    import concurrent.futures
    names = {}

    def _fetch(author_id):
        try:
            data = fetch_json(f"https://api.spiget.org/v2/authors/{author_id}?fields=name")
            return author_id, data.get("name")
        except Exception:
            return author_id, None

    unique_ids = {i for i in author_ids if i is not None}
    if not unique_ids:
        return names
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        for author_id, name in pool.map(_fetch, unique_ids):
            names[author_id] = name
    return names


def search_spiget(query, limit=20, offset=0):
    import datetime as _dt
    from urllib.parse import quote

    page = offset // limit + 1 if limit else 1
    # Explicit `fields` both trims the (otherwise base64-icon-bloated) default
    # payload and makes sure `external`/`file` are there — Spiget's own docs
    # warn to check `external` before downloading: externally-hosted resources
    # (a large share of popular plugins, e.g. EssentialsX) redirect their
    # `/download` endpoint to a human-facing release page, not a jar.
    # `icon.url` (dot-notation) pulls just the relative image path — the bare
    # `icon` field would also embed a base64-encoded copy of the same image.
    fields = "id,name,tag,downloads,updateDate,external,file,premium,author,version,icon.url"
    if query:
        # Real text search — Spiget has no "free only" search variant, so
        # premium resources are filtered out below instead.
        url = (
            f"https://api.spiget.org/v2/search/resources/{quote(query)}"
            f"?field=name&size={limit}&page={page}&sort=-downloads&fields={fields}"
        )
    else:
        # No search term: browse the free-resources list directly (real
        # pagination, premium already excluded) instead of faking a "popular"
        # text search, which only ever matched literal name/tag hits.
        url = (
            f"https://api.spiget.org/v2/resources/free"
            f"?size={limit}&page={page}&sort=-downloads&fields={fields}"
        )
    data, headers = fetch_json_with_headers(url)
    total = int(headers.get("X-Total") or 0)

    author_names = _resolve_spiget_authors(r.get("author", {}).get("id") for r in data)

    results = []
    for r in data:
        if query and r.get("premium"):
            continue
        ts = r.get("updateDate", 0)
        updated = ""
        if ts:
            try:
                updated = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            except Exception:
                pass
        author_id = r.get("author", {}).get("id")
        icon_path = (r.get("icon") or {}).get("url")
        results.append({
            "slug": str(r["id"]),
            "name": r.get("name", ""),
            "author": author_names.get(author_id) or "Unknown",
            "downloads": r.get("downloads", 0),
            "updatedAt": updated,
            "latestVersion": "",
            "description": r.get("tag", ""),
            "platform": "spigotmc",
            "external": r.get("external", False),
            "externalUrl": (r.get("file") or {}).get("externalUrl"),
            "iconUrl": f"https://www.spigotmc.org/{icon_path}" if icon_path else None,
        })
    has_more = offset + len(data) < total
    return results, has_more


def get_spiget_download(resource_id, pin_version=None):
    """Resolve the download for a Spiget resource — raises with a clear,
    actionable message for externally-hosted resources instead of silently
    saving whatever the redirect target actually is (often an HTML release
    page, not a jar). Some external resources do link straight to a .jar
    file though (e.g. a GitHub release asset) — those are still safe to
    download directly from that URL, so only the rest get refused."""
    detail = fetch_json(f"https://api.spiget.org/v2/resources/{resource_id}")
    name = detail.get("name") or f"spigot-{resource_id}"
    if detail.get("external"):
        ext_url = (detail.get("file") or {}).get("externalUrl")
        if ext_url and ext_url.lower().split("?")[0].endswith(".jar"):
            return {"url": ext_url, "filename": f"{_safe_filename(name)}.jar"}
        hint = f" Download it manually from: {ext_url}" if ext_url else (
            f" Download it manually from: https://www.spigotmc.org/resources/{resource_id}/"
        )
        raise RuntimeError(f"'{name}' is hosted externally and can't be auto-installed.{hint}")
    url = (
        f"https://api.spiget.org/v2/resources/{resource_id}/versions/{pin_version}/download"
        if pin_version else
        f"https://api.spiget.org/v2/resources/{resource_id}/download"
    )
    return {"url": url, "filename": f"{_safe_filename(name)}.jar"}


def get_spiget_info(resource_id, limit=25, offset=0):
    import datetime as _dt
    page = offset // limit + 1 if limit else 1
    url = (
        f"https://api.spiget.org/v2/resources/{resource_id}/versions"
        f"?size={limit}&page={page}&sort=-releaseDate"
    )
    data, headers = fetch_json_with_headers(url)
    total = int(headers.get("X-Total") or 0)
    versions = []
    for v in data:
        ts = v.get("releaseDate", 0)
        date = ""
        if ts:
            try:
                date = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            except Exception:
                pass
        # Spiget's version-list/detail endpoints don't expose a changelog field.
        versions.append({"id": v["id"], "name": v.get("name", ""), "date": date, "changelog": ""})
    result = {
        "websiteUrl": f"https://www.spigotmc.org/resources/{resource_id}/",
        "versions": versions,
        "hasMoreVersions": offset + len(data) < total,
    }
    if offset == 0:
        # Only needed once — skip on "load more versions" pages. Spiget's
        # description is base64-encoded HTML; decode + strip tags for display.
        import base64
        detail = fetch_json(f"https://api.spiget.org/v2/resources/{resource_id}?fields=description")
        raw = detail.get("description") or ""
        try:
            html_text = base64.b64decode(raw).decode("utf-8", "replace")
            result["longDescription"] = _html_to_text(html_text)
        except Exception:
            result["longDescription"] = ""
    return result


# ─── Unified search / install ─────────────────────────────────────────────────

def search_plugins(args, progress=None):
    platform = args.platform.lower()
    query = getattr(args, "query", "") or ""
    mc_version = getattr(args, "mc_version", None)
    limit = getattr(args, "limit", 20)
    offset = getattr(args, "offset", 0) or 0

    cfg = load_config()
    if getattr(args, "id", None):
        srv = find_server(cfg, args.id)
        if srv:
            if not mc_version:
                mc_version = srv.get("version")

    if platform == "modrinth":
        software = getattr(args, "software", "paper") or "paper"
        results, has_more = search_modrinth(query, software, mc_version, limit, offset)
    elif platform == "hangar":
        results, has_more = search_hangar(query, mc_version, limit, offset)
    elif platform in ("spigotmc", "spiget"):
        results, has_more = search_spiget(query, limit, offset)
    else:
        return {"error": f"Unknown platform '{platform}'. One of: modrinth, hangar, spigotmc"}
    return {"results": results, "hasMore": has_more}


def get_plugin_info(args, progress=None):
    platform = args.platform.lower()
    slug = args.slug
    limit = getattr(args, "limit", 25)
    offset = getattr(args, "offset", 0) or 0

    try:
        if platform == "modrinth":
            return get_modrinth_info(slug, limit, offset)
        elif platform == "hangar":
            owner = getattr(args, "owner", None) or (slug.split("/")[0] if "/" in slug else slug)
            return get_hangar_info(slug, owner, limit, offset)
        elif platform in ("spigotmc", "spiget"):
            return get_spiget_info(slug, limit, offset)
        else:
            return {"error": f"Unknown platform '{platform}'. One of: modrinth, hangar, spigotmc"}
    except Exception as e:
        return {"error": str(e)}


def install_plugin(args, progress=None):
    platform = args.platform.lower()
    slug = args.slug
    server_id = getattr(args, "id", None)
    profile_id = getattr(args, "profile_id", None)
    mc_version = getattr(args, "mc_version", None)
    pin_version = getattr(args, "version", None)

    cfg = load_config()
    srv = None
    dest_base = None

    if server_id:
        srv = find_server(cfg, server_id)
        if not srv:
            return {"error": f"Server not found: {server_id}"}
        dest_base = srv["dir"]
        if not mc_version:
            mc_version = srv.get("version")
    elif profile_id:
        from . import paths
        dest_base = os.path.join(paths.PROFILES_DIR, profile_id)
        if not os.path.exists(dest_base):
            return {"error": f"Profile directory not found: {profile_id}"}
    else:
        return {"error": "Either --id (server) or --profile-id is required"}

    software = srv.get("software", "paper") if srv else "paper"
    plugin_dir = _dest_dir(software)

    try:
        if platform == "modrinth":
            info = get_modrinth_version_download(pin_version) if pin_version \
                else get_modrinth_download(slug, software, mc_version)
        elif platform == "hangar":
            owner = getattr(args, "owner", None) or slug.split("/")[0] if "/" in slug else slug
            info = get_hangar_download(slug, owner, mc_version, pin_version)
        elif platform in ("spigotmc", "spiget"):
            info = get_spiget_download(slug, pin_version)
        else:
            return {"error": f"Unknown platform '{platform}'"}
    except Exception as e:
        return {"error": str(e)}

    dest_dir = os.path.join(dest_base, plugin_dir)
    os.makedirs(dest_dir, exist_ok=True)
    dest_file = os.path.join(dest_dir, info["filename"])

    if progress:
        progress(0, f"Downloading {info['filename']}…")

    try:
        download_file(
            info["url"],
            dest_file,
            (lambda p: progress(p, f"Downloading… {p}%")) if progress else None,
        )
    except Exception as e:
        return {"error": f"Download failed: {e}"}

    if progress:
        progress(100, "Done!")

    return {
        "success": True,
        "filename": info["filename"],
        "path": dest_file,
        "platform": platform,
        "slug": slug,
    }
