"""Human-friendly renderers for command results. The `api` family bypasses all
of this and prints raw JSON; these functions only run in human mode."""

import datetime

from . import util

# ─── tiny ANSI helpers (auto-disabled when not a TTY) ────────────────────────
import sys
_TTY = sys.stdout.isatty()


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _TTY else str(text)


def bold(t):
    return _c("1", t)


def dim(t):
    return _c("2", t)


def green(t):
    return _c("32", t)


def red(t):
    return _c("31", t)


def yellow(t):
    return _c("33", t)


def cyan(t):
    return _c("36", t)


def _ts(ms):
    if not ms:
        return ""
    try:
        return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ms)


def _err(result):
    if isinstance(result, dict) and result.get("error"):
        print(red("✗ ") + str(result["error"]))
        return True
    return False


# ─── servers ─────────────────────────────────────────────────────────────────
def render_list_servers(result, args):
    servers = result.get("servers", [])
    if not servers:
        print(dim("No servers. Create one with: mcpanel create server -t <name> -sw paper -v <version>"))
        return
    print(bold(f"{'ID':<22} {'NAME':<22} {'STATUS':<9} {'SOFTWARE':<9} {'VERSION':<10} PORT"))
    for s in servers:
        status = green("● online") if s.get("running") else dim("○ offline")
        # pad accounting for ANSI invisible chars
        raw_status = "● online" if s.get("running") else "○ offline"
        pad = " " * max(0, 9 - len(raw_status))
        print(f"{s['id']:<22} {(s.get('name') or '')[:22]:<22} {status}{pad} "
              f"{(s.get('software') or ''):<9} {(s.get('version') or ''):<10} {s.get('port')}")


def render_server(result, args):
    if _err(result):
        return
    s = result
    print(bold(s.get("name", "(unnamed)")) + dim(f"  [{s.get('id')}]"))
    running = s.get("running")
    print(f"  status     : " + (green("online") if running else dim("offline")))
    print(f"  software   : {s.get('software')}  {s.get('version')}")
    print(f"  port       : {s.get('port')}")
    print(f"  ram        : {s.get('ram')}")
    print(f"  storage    : {s.get('storageLimit') or 'unlimited'}")
    print(f"  java       : {s.get('javaPath')}")
    print(f"  java args  : {s.get('javaArgs')}")
    print(f"  profile    : {s.get('profileId') or '-'}")
    print(f"  created    : {_ts(s.get('created'))}")
    print(f"  dir        : {s.get('dir')}")


def render_create_server(result, args):
    if _err(result):
        return
    s = result.get("server", {})
    print(green("✓ ") + f"Created server {bold(s.get('name'))} "
          + dim(f"({s.get('id')})  {s.get('software')} {s.get('version')}  {s.get('ram')} RAM  port {s.get('port')}"))


def render_success(result, args):
    if _err(result):
        return
    if isinstance(result, dict) and result.get("needsEula"):
        print(yellow("⚠ EULA not accepted.") + " Run: mcpanel accept-eula server -id " + getattr(args, "id", "<id>")
              + dim("   (or add --accept-eula)"))
        return
    print(green("✓ done"))


def render_started(result, args):
    if isinstance(result, dict) and result.get("needsEula"):
        print(yellow("⚠ EULA not accepted.") + " Run: mcpanel start server -id "
              + getattr(args, "id", "<id>") + " --accept-eula" + dim("  (accepts the Minecraft EULA)"))
        return
    if _err(result):
        return
    print(green("✓ ") + "server starting — follow output with: "
          + cyan(f"mcpanel logs server -id {getattr(args, 'id', '')} -f"))


def render_ping(result, args):
    if not result.get("online"):
        print(red("○ offline"))
        return
    print(green("● online"))
    print(f"  version  : {result.get('version')}")
    print(f"  players  : {result.get('players')}/{result.get('maxPlayers')}")
    if result.get("playerList"):
        print(f"  online   : {', '.join(result['playerList'])}")
    if result.get("motd"):
        print(f"  motd     : {result.get('motd')}")


def render_status(result, args):
    print(green("● running") if result else dim("○ stopped"))


def render_stats(result, args):
    print(f"{util.human_size(result.get('size', 0))}  ({result.get('size', 0)} bytes)")


def _print_tree(items, indent=0):
    for it in items:
        pad = "  " * indent
        if it["type"] == "dir":
            print(pad + cyan(it["name"] + "/"))
            _print_tree(it.get("children", []), indent + 1)
        else:
            print(pad + it["name"] + dim(f"  ({util.human_size(it.get('size', 0))})"))


def render_file_tree(result, args):
    if _err(result):
        return
    tree = result.get("tree", [])
    if not tree:
        print(dim("(empty)"))
    else:
        _print_tree(tree)


def render_logs(result, args):
    for rec in result:
        text = rec.get("text", "")
        if rec.get("type") == "err":
            print(red(text))
        else:
            print(text)


# ─── versions ─────────────────────────────────────────────────────────────────
def render_versions(result, args):
    if _err(result):
        return
    versions = result.get("versions", [])
    print(bold(f"{getattr(args, 'software', '')} ") + dim(f"({len(versions)} versions)"))
    # columns
    width = max((len(v) for v in versions), default=8) + 2
    cols = max(1, 100 // width)
    for i in range(0, len(versions), cols):
        print("  " + "".join(v.ljust(width) for v in versions[i:i + cols]))


# ─── profiles ─────────────────────────────────────────────────────────────────
def render_list_profiles(result, args):
    profiles = result.get("profiles", [])
    if not profiles:
        print(dim("No profiles. Create one with: mcpanel create profile -t <name>"))
        return
    for p in profiles:
        sw = ", ".join(p.get("software") or []) or "any"
        vs = ", ".join(p.get("versions") or []) or "any"
        print(bold(p.get("name", "(unnamed)")) + dim(f"  [{p.get('id')}]"))
        if p.get("description"):
            print("  " + p["description"])
        print(dim(f"  software: {sw}   versions: {vs}"))


def render_profile(result, args):
    if _err(result):
        return
    render_list_profiles({"profiles": [result]}, args)


def render_create_profile(result, args):
    if _err(result):
        return
    p = result.get("profile", {})
    print(green("✓ ") + f"Created profile {bold(p.get('name'))} {dim('(' + p.get('id', '') + ')')}")
    print(dim("  Add files (plugins/, config/, ...) under: ")
          + (p.get("dir") or f"<themes>/{p.get('id')}"))


# ─── themes ─────────────────────────────────────────────────────────────────
def render_list_themes(result, args):
    themes = result.get("themes", [])
    if not themes:
        print(dim("No themes installed."))
        return
    for t in themes:
        marker = green(" (active)") if t.get("active") else ""
        print(bold(t.get("name", "(unnamed)")) + marker + dim(f"  [{t.get('id')}]"))
        if t.get("description"):
            print("  " + t["description"])
        meta = []
        if t.get("creator"):
            meta.append("by " + t["creator"])
        if t.get("version"):
            meta.append("v" + str(t["version"]))
        if meta:
            print(dim("  " + "  ".join(meta)))


def render_github_themes(result, args):
    themes = result.get("themes", [])
    if result.get("error"):
        print(yellow("⚠ " + result["error"]))
    if not themes:
        print(dim("No community themes found."))
        return
    for t in themes:
        print(bold(t.get("name", "(unnamed)")) + dim(f"  {t.get('url', '')}"))
        if t.get("description"):
            print("  " + t["description"])


# ─── jdk / system ─────────────────────────────────────────────────────────────
def render_jdk(result, args):
    jdks = result.get("jdks", [])
    if not jdks:
        print(dim("No Java installations found."))
        return
    for j in jdks:
        print(f"  {bold(j.get('version', '?')):<12} {j.get('path')}")


def render_system(result, args):
    print(f"  total RAM        : {util.human_size(result.get('totalRam'))}")
    print(f"  available storage: {util.human_size(result.get('availableStorage'))}")


def render_app_version(result, args):
    print("MCPanel CLI v" + result.get("version", "?"))


def render_check_update(result, args):
    print(f"  current : {result.get('current')}")
    print(f"  latest  : {result.get('latest') or 'unknown'}")
    if result.get("hasUpdate"):
        print(yellow("  ⚠ Update available: ") + (result.get("url") or ""))
    else:
        print(green("  ✓ Up to date"))


def render_config(result, args):
    import json
    print(json.dumps(result, indent=2, default=str))


# ─── dispatch table ──────────────────────────────────────────────────────────
RENDERERS = {
    "list-servers": render_list_servers,
    "fetch-server": render_server,
    "create-server": render_create_server,
    "delete-server": render_success,
    "update-server": lambda r, a: render_server(r.get("server", r), a) if not _err(r) else None,
    "duplicate-server": render_create_server,
    "import-server": render_create_server,
    "accept-eula": render_success,
    "start-server": render_started,
    "stop-server": render_success,
    "kill-server": render_success,
    "restart-server": render_started,
    "send-command": render_success,
    "get-server-log": render_logs,
    "logs": render_logs,
    "status": render_status,
    "ping": render_ping,
    "stats": render_stats,
    "file-tree": render_file_tree,
    "scan-server": render_config,
    "open-server": render_success,
    "versions": render_versions,
    "list-profiles": render_list_profiles,
    "fetch-profile": render_profile,
    "create-profile": render_create_profile,
    "create-profile-from-server": render_create_profile,
    "delete-profile": render_success,
    "import-profile": render_create_profile,
    "scan-profile": render_config,
    "open-profile": render_success,
    "list-themes": render_list_themes,
    "theme-css": render_config,
    "apply-theme": render_success,
    "install-theme": lambda r, a: render_success(r, a) if _err(r) or not r.get("theme")
        else print(green("✓ ") + "Installed theme " + bold(r["theme"].get("name", ""))),
    "delete-theme": render_success,
    "github-themes": render_github_themes,
    "config": render_config,
    "jdk": render_jdk,
    "system": render_system,
    "app-version": render_app_version,
    "check-update": render_check_update,
}


def render(action, result, args):
    fn = RENDERERS.get(action, render_config)
    fn(result, args)
