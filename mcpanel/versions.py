"""Version listing + download-URL resolution for every supported server
software. Direct port of the version fetchers and resolveDownloadUrl in
main.js."""

import re

from .http import fetch_json

SOFTWARE = ["paper", "purpur", "velocity", "fabric", "vanilla", "leaf", "folia", "spigot"]

_PRE_RE = re.compile(r"-(pre|rc|alpha|beta|snapshot)\d*", re.I)


# ─── Version listers ────────────────────────────────────────────────────────
def fetch_paper_versions(unstable=False):
    data = fetch_json("https://api.papermc.io/v2/projects/paper")
    vs = data["versions"] if unstable else [v for v in data["versions"] if not _PRE_RE.search(v)]
    return list(reversed(vs))


def fetch_purpur_versions(unstable=False):
    data = fetch_json("https://api.purpurmc.org/v2/purpur")
    return list(reversed(data["versions"]))


def fetch_velocity_versions(unstable=False):
    data = fetch_json("https://api.papermc.io/v2/projects/velocity")
    vs = data["versions"] if unstable else [v for v in data["versions"] if "SNAPSHOT" not in v]
    return list(reversed(vs))


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


def fetch_spigot_versions(unstable=False):
    return ['1.21.4', '1.21.3', '1.21.1', '1.21', '1.20.6', '1.20.4', '1.20.2', '1.20.1',
            '1.19.4', '1.19.3', '1.19.2', '1.19.1', '1.19', '1.18.2', '1.18.1', '1.18',
            '1.17.1', '1.17', '1.16.5', '1.16.4', '1.16.3', '1.15.2', '1.14.4', '1.13.2',
            '1.12.2', '1.11.2', '1.10.2', '1.9.4', '1.8.8']


def fetch_folia_versions(unstable=False):
    data = fetch_json("https://api.papermc.io/v2/projects/folia")
    vs = data["versions"] if unstable else [v for v in data["versions"] if not _PRE_RE.search(v)]
    return list(reversed(vs))


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
def _papermc_url(project, version, unstable):
    data = fetch_json(f"https://api.papermc.io/v2/projects/{project}/versions/{version}/builds")
    stable = [b for b in data["builds"] if b["channel"] == "STABLE"]
    pool = stable if (not unstable and stable) else data["builds"]
    latest = pool[-1]
    name = latest["downloads"]["application"]["name"]
    return f"https://api.papermc.io/v2/projects/{project}/versions/{version}/builds/{latest['build']}/downloads/{name}"


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
