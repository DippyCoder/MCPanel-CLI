"""Version listing + download-URL resolution for every supported server
software. Direct port of the version fetchers and resolveDownloadUrl in
main.js."""

import re

from .http import fetch_json, fetch_text, post_json

_FILL_GQL = "https://fill.papermc.io/graphql"
_GQL_VERSIONS = '{{ project(key: "{project}") {{ versions(last: 100) {{ nodes {{ key support {{ status }} }} }} }} }}'

def _papermc_versions(project, unstable=False):
    """Fetch versions from fill.papermc.io GraphQL. Returns newest-first list."""
    query = _GQL_VERSIONS.format(project=project)
    data = post_json(_FILL_GQL, {"query": query})
    nodes = data["data"]["project"]["versions"]["nodes"]
    if unstable:
        vs = [n["key"] for n in nodes]
    else:
        vs = [n["key"] for n in nodes if not _PRE_RE.search(n["key"])]
    return list(reversed(vs))

SOFTWARE = ["paper", "purpur", "velocity", "fabric", "vanilla", "leaf", "folia", "spigot"]

_PRE_RE = re.compile(r"-(pre|rc|alpha|beta|snapshot)\d*", re.I)


# ─── Version listers ────────────────────────────────────────────────────────
def fetch_paper_versions(unstable=False):
    return _papermc_versions("paper", unstable)


def fetch_purpur_versions(unstable=False):
    data = fetch_json("https://api.purpurmc.org/v2/purpur")
    return list(reversed(data["versions"]))


def fetch_velocity_versions(unstable=False):
    # Velocity uses SNAPSHOTs as normal releases; always return all SUPPORTED versions.
    return _papermc_versions("velocity", unstable=True)


def fetch_fabric_versions(pre_release=False):
    data = fetch_json("https://meta.fabricmc.net/v2/versions/game")
    src = data if pre_release else [v for v in data if v.get("stable")]
    return [v["version"] for v in src][:60]


def fetch_vanilla_versions(pre_release=False):
    data = fetch_json("https://launchermeta.mojang.com/mc/game/version_manifest_v2.json")
    allowed = ["release", "snapshot", "old_beta", "old_alpha"] if pre_release else ["release"]
    return [v["id"] for v in data["versions"] if v["type"] in allowed][:80]


_LEAF_FALLBACK = ['1.21.11', '1.21.8', '1.21.7', '1.21.6', '1.21.5', '1.21.4', '1.21.3',
                  '1.21.1', '1.21', '1.20.6', '1.20.4', '1.20.2', '1.20.1', '1.19.4']


def fetch_leaf_versions(unstable=False):
    try:
        releases = fetch_json("https://api.github.com/repos/Winds-Studio/Leaf/releases?per_page=50")
        seen, versions = set(), []
        for r in releases:
            if r.get("draft"):
                continue
            ver = re.sub(r"^ver-", "", r["tag_name"])
            ver = re.sub(r"^v", "", ver)
            if re.match(r"^\d+\.\d+(\.\d+)?$", ver) and ver not in seen \
                    and any(a["name"].endswith(".jar") for a in r.get("assets", [])):
                seen.add(ver)
                versions.append(ver)
        if versions:
            return versions
    except Exception:
        pass
    return list(_LEAF_FALLBACK)


_SPIGOT_VERSIONS_INDEX = "https://hub.spigotmc.org/versions/"
_SPIGOT_VERSION_NAME_RE = re.compile(r"^\d+(\.\d+)+(-[a-zA-Z0-9]+)?$")

# Last-resort fallback if hub.spigotmc.org can't be reached — same idea as
# _LEAF_FALLBACK below. The real fetcher below hits the same metadata
# directory BuildTools itself reads, so this list only matters when offline.
_SPIGOT_FALLBACK = ['1.21.11', '1.21.8', '1.21.4', '1.21.1', '1.21', '1.20.6', '1.20.4',
                     '1.20.2', '1.20.1', '1.19.4', '1.19.3', '1.19.2', '1.19.1', '1.19',
                     '1.18.2', '1.18.1', '1.18', '1.17.1', '1.17', '1.16.5', '1.16.4',
                     '1.16.3', '1.15.2', '1.14.4', '1.13.2', '1.12.2', '1.11.2', '1.10.2',
                     '1.9.4', '1.8.8']


def _spigot_version_sort_key(v):
    m = re.match(r"^(\d+(?:\.\d+)*)(-.*)?$", v)
    nums = tuple(int(x) for x in m.group(1).split(".")) if m else (0,)
    is_final = 0 if (m and m.group(2)) else 1  # a version's final release ranks above its own pre-releases
    return (nums, is_final)


def fetch_spigot_versions(unstable=False):
    """Every version BuildTools can compile, fetched from SpigotMC's own
    metadata directory (hub.spigotmc.org/versions/) — the same source
    BuildTools itself consults — instead of a hardcoded list that inevitably
    goes stale the moment a new MC version ships."""
    try:
        html = fetch_text(_SPIGOT_VERSIONS_INDEX)
        names = re.findall(r'href="([^"/]+)\.json"', html)
        versions = [n for n in names if n != "latest" and _SPIGOT_VERSION_NAME_RE.match(n)]
        if not unstable:
            versions = [v for v in versions if not _PRE_RE.search(v)]
        if versions:
            versions.sort(key=_spigot_version_sort_key, reverse=True)
            return versions
    except Exception:
        pass
    return list(_SPIGOT_FALLBACK)


def fetch_folia_versions(unstable=False):
    return _papermc_versions("folia", unstable)


def fetch_versions(software, pre_release=False, unstable=False):
    """Returns {'versions': [...]} or {'error': msg} — matches fetch-versions IPC."""
    fetchers = {
        "paper": lambda: fetch_paper_versions(unstable),
        "purpur": lambda: fetch_purpur_versions(unstable),
        "velocity": lambda: fetch_velocity_versions(unstable),
        "fabric": lambda: fetch_fabric_versions(pre_release),
        "vanilla": lambda: fetch_vanilla_versions(pre_release),
        "leaf": lambda: fetch_leaf_versions(unstable),
        "folia": lambda: fetch_folia_versions(unstable),
        "spigot": lambda: fetch_spigot_versions(),
    }
    if software not in fetchers:
        return {"error": "Unknown software"}
    try:
        return {"versions": fetchers[software]()}
    except Exception as e:
        return {"error": str(e)}


# ─── Download URL resolution ─────────────────────────────────────────────────
_GQL_DOWNLOAD = '{{ project(key: "{project}") {{ version(key: "{version}") {{ builds(last: 1) {{ nodes {{ downloads {{ url }} }} }} }} }} }}'

def _papermc_url(project, version, unstable):
    query = _GQL_DOWNLOAD.format(project=project, version=version)
    data = post_json(_FILL_GQL, {"query": query})
    nodes = data["data"]["project"]["version"]["builds"]["nodes"]
    if not nodes:
        raise RuntimeError(f"No builds found for {project} {version}")
    return nodes[-1]["downloads"][0]["url"]


def resolve_download_url(software, version, unstable=False):
    if software == "paper":
        return _papermc_url("paper", version, unstable)
    if software == "folia":
        return _papermc_url("folia", version, unstable)
    if software == "velocity":
        return _papermc_url("velocity", version, unstable)
    if software == "purpur":
        return f"https://api.purpurmc.org/v2/purpur/{version}/latest/download"
    if software == "fabric":
        loaders = fetch_json(f"https://meta.fabricmc.net/v2/versions/loader/{version}")
        loader = loaders[0]["loader"]["version"]
        installers = fetch_json("https://meta.fabricmc.net/v2/versions/installer")
        installer = installers[0]["version"]
        return f"https://meta.fabricmc.net/v2/versions/loader/{version}/{loader}/{installer}/server/jar"
    if software == "vanilla":
        manifest = fetch_json("https://launchermeta.mojang.com/mc/game/version_manifest_v2.json")
        info = next((v for v in manifest["versions"] if v["id"] == version), None)
        if not info:
            raise RuntimeError(f"No vanilla version found: {version}")
        vdata = fetch_json(info["url"])
        return vdata["downloads"]["server"]["url"]
    if software == "leaf":
        releases = fetch_json("https://api.github.com/repos/Winds-Studio/Leaf/releases?per_page=50")
        tag_variants = [f"ver-{version}", f"v{version}", version]
        rel = next((r for r in releases if r["tag_name"] in tag_variants), None)
        if not rel:
            raise RuntimeError(f"No Leaf release found for version {version}")
        assets = rel.get("assets", [])
        jar = (next((a for a in assets if a["name"].endswith(".jar")
                     and "mojmap" not in a["name"] and "reobf" not in a["name"]), None)
               or next((a for a in assets if "reobf" in a["name"] and a["name"].endswith(".jar")), None)
               or next((a for a in assets if a["name"].endswith(".jar")), None))
        if not jar:
            raise RuntimeError(f"No JAR asset found for Leaf {version}")
        return jar["browser_download_url"]
    if software == "spigot":
        raise RuntimeError("Spigot requires BuildTools. Download from "
                           "https://www.spigotmc.org/wiki/buildtools/")
    raise RuntimeError("Unknown software: " + str(software))
