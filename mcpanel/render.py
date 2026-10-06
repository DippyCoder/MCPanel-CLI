"""Human-friendly renderers for command results. The `api` family bypasses all
of this and prints raw JSON; these functions only run in human mode."""

import datetime
import re

from . import util

# ─── tiny ANSI helpers (auto-disabled when not a TTY) ────────────────────────
import sys
_TTY = sys.stdout.isatty()

if sys.platform == "win32" and _TTY:
    try:
        import ctypes
        _k32 = ctypes.windll.kernel32
        _mode = ctypes.c_ulong(0)
        _handle = _k32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if _k32.GetConsoleMode(_handle, ctypes.byref(_mode)):
            _k32.SetConsoleMode(_handle, _mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


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
    verb = "Linked" if s.get("linked") else ("Imported" if "detected" in result else "Created")
    print(green("✓ ") + f"{verb} server {bold(s.get('name'))} "
          + dim(f"({s.get('id')})  {s.get('software')} {s.get('version')}  {s.get('ram')} RAM  port {s.get('port')}"))
    if s.get("linked"):
        print(dim(f"  using {s.get('dir')} in place (remove it with: mcpanel delete server -id {s.get('id')} --keep-files)"))


def render_success(result, args):
    if _err(result):
        return
    if isinstance(result, dict) and result.get("needsEula"):
        print(yellow("⚠ EULA not accepted.") + " Run: mcpanel accept-eula server -id " + getattr(args, "id", "<id>")
              + dim("   (or add --accept-eula)"))
        return
    if isinstance(result, dict) and result.get("message"):
        print(green("✓ ") + result["message"])
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
    # Only present while the server is running (read from the java process).
    if result.get("ramBytes") is not None:
        print(dim("RAM  ") + util.human_size(result["ramBytes"]))
    if result.get("cpuPct") is not None:
        print(dim("CPU  ") + f"{result['cpuPct']}%")


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


def render_sessions(result, args):
    if _err(result):
        return
    sessions = result.get("sessions", [])
    if not sessions:
        print(dim("No archived sessions. Sessions are saved at the start of each new run."))
        return
    print(bold(f"{'N':<4} {'STARTED':<20}"))
    for s in sessions:
        print(f"  {s['n']:<3} {_ts(s.get('timestamp', 0))}")
    sid = getattr(args, "id", "<id>")
    print(dim(f"\nView a session: mcpanel logs server -id {sid} -n <N>"))


def render_log_files(result, args):
    if _err(result):
        return
    files = result.get("files", [])
    if not files:
        print(dim("No log files yet — the server writes them to its logs/ folder once it has run."))
        return
    print(bold(f"{'FILE':<32} {'SIZE':>10}  MODIFIED"))
    for f in files:
        print(f"{f['name']:<32} {util.human_size(f.get('size', 0)):>10}  {_ts(f.get('modified', 0))}")
    sid = getattr(args, "id", "<id>")
    print(dim(f"\nRead one:   mcpanel fetch logfile -id {sid} -file <name>"))
    print(dim(f"Share one:  mcpanel upload-log -id {sid} -file <name>"))


def render_log_file(result, args):
    if _err(result):
        return
    if result.get("truncated"):
        print(dim(f"… showing the last {len(result['lines'])} of {result['totalLines']} lines"))
    for line in result.get("lines", []):
        if re.search(r"\b(ERROR|SEVERE|FATAL)\b", line):
            print(red(line))
        elif re.search(r"\bWARN(ING)?\b", line):
            print(yellow(line))
        else:
            print(line)


def render_upload_log(result, args):
    if _err(result):
        return
    print(green("✓ ") + f"Uploaded {result.get('file', 'log')}: " + bold(result["url"]))
    if result.get("truncated"):
        print(dim(f"  Only the newest {result['lines']} of {result['totalLines']} lines fit mclo.gs' limit."))


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


def render_jdk_compat(result, args):
    rng = result.get("range")
    if rng:
        hi = rng.get("max")
        print(f"  required: Java {rng.get('min')}" + (f"–{hi}" if hi else "+"))
    else:
        print(dim("  required: unknown (couldn't determine a Java requirement)"))
    jdks = result.get("jdks", [])
    if not jdks:
        print(dim("  No Java installations detected."))
        return
    recommended = result.get("recommended")
    for j in jdks:
        mark = green("✓") if j.get("compatible") else red("✗")
        tag = green(" (recommended)") if j.get("path") == recommended else ""
        line = f"  {mark} {j.get('version', '?'):<10} {j.get('path')}{tag}"
        print(line)
        if not j.get("compatible") and j.get("reason"):
            print(dim(f"      {j['reason']}"))
    if not recommended:
        print(yellow("\n  ⚠ No detected JDK satisfies this requirement — install one and re-run."))


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


def render_buildtools_version(result, args):
    if result.get("version") == "installed":
        print(green("  ✓ BuildTools installed"))
        if result.get("path"):
            print(dim(f"  {result['path']}"))
        return
    print(red("  ✗ BuildTools version: none"))
    if result.get("error"):
        print(f"  {result['error']}")
    if result.get("helpUrl"):
        print(dim(f"  See: {result['helpUrl']}"))


def render_config(result, args):
    import json
    print(json.dumps(result, indent=2, default=str))


def render_discover(result, args):
    added = result.get("added", [])
    if not added:
        print(dim("No unregistered servers found."))
        return
    print(green(f"✓ Found {len(added)} server{'s' if len(added) != 1 else ''} on disk:"))
    for s in added:
        detail = f"[{s.get('id')}]  {s.get('software')} {s.get('version')}"
        print(f"  {bold(s.get('name', '(unnamed)'))}  {dim(detail)}")


def render_shutdown(result, args):
    if _err(result):
        return
    stopped = result.get("stopped", [])
    failed  = result.get("failed", [])
    if not stopped and not failed:
        print(dim("No servers were running."))
        return
    for sid in stopped:
        print(green("✓ ") + f"stopped  {sid}")
    for f in failed:
        print(yellow("⚠ ") + f"could not stop {f['id']}: {f.get('error','')}")
    print(dim(f"Shutdown complete — {len(stopped)} server{'s' if len(stopped) != 1 else ''} stopped."))


# ─── plugin / mod renderers ──────────────────────────────────────────────────
def render_plugin_search(result, args, kind="plugin"):
    if _err(result):
        return
    items = result.get("results", [])
    if not items:
        print(dim("No results found."))
        return
    print(bold(f"{'NAME':<28} {'AUTHOR':<18} {'DOWNLOADS':<11} {'UPDATED':<12} VERSION"))
    for r in items:
        name = (r.get("name") or "")[:27]
        author = (r.get("author") or "")[:17]
        dl = r.get("downloads", 0)
        dl_str = f"{dl/1e6:.1f}M" if dl >= 1e6 else (f"{dl/1e3:.1f}K" if dl >= 1e3 else str(dl))
        updated = (r.get("updatedAt") or "")[:10]
        version = str(r.get("latestVersion") or "")[:12]
        premium = yellow(" [PREMIUM]") if r.get("isPremium") else ""
        external = yellow(" [EXTERNAL]") if r.get("external") else ""
        print(f"{name:<28} {author:<18} {dl_str:<11} {updated:<12} {version}{premium}{external}")
        if r.get("description"):
            print(dim("  " + r["description"][:80]))
        if r.get("external") and r.get("externalUrl"):
            print(dim(f"  hosted externally — can't auto-install: {r['externalUrl']}"))
    # Inside the TUI the same command is spelled /install, not mcpanel install.
    prefix = "/" if getattr(args, "tui", False) else "mcpanel "
    print(dim(f"\nInstall: {prefix}install {kind} <platform> <slug> -id <serverid>"))


def render_install_plugin(result, args):
    if _err(result):
        return
    print(green("✓ ") + f"Installed {bold(result.get('filename', result.get('slug', '?')))} "
          + dim(f"→ {result.get('path', '')}"))


def render_plugin_info(result, args):
    if _err(result):
        return
    versions = result.get("versions", [])
    if not versions:
        print(dim("No version history found."))
    else:
        print(bold(f"{'VERSION':<20} {'DATE':<12} CHANGELOG"))
        for v in versions:
            name = str(v.get("name") or "")[:19]
            date = (v.get("date") or "")[:10]
            changelog = (v.get("changelog") or "").replace("\n", " ")[:60]
            print(f"{name:<20} {date:<12} {changelog}")
    if result.get("hasMoreVersions"):
        print(dim("(more versions available — increase -n or use -o to page)"))
    if result.get("websiteUrl"):
        print(dim(f"\nWebsite: {result['websiteUrl']}"))


# ─── proxy (Velocity) ─────────────────────────────────────────────────────────
def render_proxy_info(result, args):
    if _err(result):
        return
    registered = result.get("servers", {})
    if not registered:
        print(dim("No servers registered in this proxy's velocity.toml."))
        return
    try_list = result.get("tryList", [])
    print(bold(f"{'NAME':<24} ADDRESS"))
    for name, addr in registered.items():
        print(f"  {name:<24} {addr}")
    if try_list:
        print(dim(f"\ntry: {', '.join(try_list)}"))


def render_proxy_link(result, args):
    if _err(result):
        return
    print(green("✓ ") + f"Linked as {bold(result.get('serverName', '?'))} "
          + dim(f"→ {result.get('address', '')}"))


def render_errors(result, args):
    if _err(result):
        return
    items = result.get("errors", [])
    width = max((len(e.get("code", "")) for e in items), default=10)
    print(bold(f"{'CODE':<{width}}  MESSAGE"))
    for e in items:
        src = "" if e.get("source") == "cli" else dim(f"  [{e.get('source')}]")
        print(f"{e.get('code', ''):<{width}}  {e.get('message', '')}{src}")


def render_completion(result, args):
    print(result.get("_raw", result.get("script", "")), end="")


# ─── backups ──────────────────────────────────────────────────────────────────
def render_backup_list(result, args):
    if _err(result):
        return
    backups = result.get("backups", [])
    if not backups:
        print(dim("No backups found. Create one with: mcpanel backup create -id <id>"))
        return
    print(bold(f"{'NAME':<34} {'SIZE':<10} CREATED"))
    for b in backups:
        created = _ts(b.get("created", 0))
        size = util.human_size(b.get("size", 0))
        print(f"  {b['name']:<34} {size:<10} {created}")


def render_backup_create(result, args):
    if _err(result):
        return
    b = result.get("backup", {})
    print(green("✓ ") + f"Backup created: {bold(b.get('name', '?'))} "
          + dim(f"({util.human_size(b.get('size', 0))})"))


# ─── dispatch table ──────────────────────────────────────────────────────────
# ─── addons ──────────────────────────────────────────────────────────────────
_ADDON_STATUS_STYLE = {
    "loaded": lambda: green("● loaded"),
    "disabled": lambda: dim("○ disabled"),
    "error": lambda: red("✗ error"),
    "api-mismatch": lambda: yellow("! api mismatch"),
    "invalid": lambda: yellow("! invalid"),
    "duplicate": lambda: yellow("! duplicate"),
}
_ADDON_STATUS_RAW = {
    "loaded": "● loaded", "disabled": "○ disabled", "error": "✗ error",
    "api-mismatch": "! api mismatch", "invalid": "! invalid", "duplicate": "! duplicate",
}


def render_addons_list(result, args):
    if _err(result):
        return
    if not result.get("loaded", True):
        print(yellow("! ") + result.get("reason", "addons are disabled"))
        return
    items = result.get("addons", [])
    if not items:
        print(dim("No addons installed. See ADDONS.md, or: mcpanel addons install <path|url>"))
        return
    print(bold(f"{'NAME':<20} {'VERSION':<10} {'SOURCE':<9} {'STATUS':<15} DESCRIPTION"))
    for a in items:
        status = a.get("status", "")
        shown = _ADDON_STATUS_STYLE.get(status, lambda: status)()
        raw = _ADDON_STATUS_RAW.get(status, status)
        pad = " " * max(0, 15 - len(raw))
        print(f"{a.get('name', ''):<20} {a.get('version', ''):<10} "
              f"{a.get('source', ''):<9} {shown}{pad} {a.get('description', '')}")
    broken = [a for a in items if a.get("error")]
    if broken:
        print()
        print(dim("Run 'mcpanel addons info <name>' to see why an addon failed."))


def render_addons_info(result, args):
    if _err(result):
        return
    a = result.get("addon", {})
    status = a.get("status", "")
    print(bold(a.get("name", "")) + dim(f"  v{a.get('version', '?')}"))
    if a.get("description"):
        print("  " + a["description"])
    print()
    rows = [
        ("status", _ADDON_STATUS_STYLE.get(status, lambda: status)()),
        ("source", a.get("source", "")),
        ("location", a.get("location", "")),
        ("api version", str(a.get("apiVersion", ""))),
        ("author", a.get("author", "") or dim("—")),
        ("url", a.get("url", "") or dim("—")),
    ]
    if a.get("declaredName"):
        rows.append(("declared name", yellow(a["declaredName"]) + dim("  (differs from install name)")))
    for key, value in rows:
        print(f"  {dim(key.ljust(12))}  {value}")
    actions = a.get("actions", [])
    if actions:
        print()
        print(bold("commands"))
        for act in actions:
            print(f"  • {act}")
    if a.get("error"):
        print()
        print(red("error"))
        for line in str(a["error"]).rstrip().splitlines():
            print("  " + dim(line))


def render_addons_toggle(result, args):
    if _err(result):
        return
    verb = "Enabled" if result.get("enabled") else "Disabled"
    print(green("✓ ") + f"{verb} addon {bold(result.get('name', ''))}")
    if result.get("note"):
        print(dim("  " + result["note"]))


def render_addons_install(result, args):
    if _err(result):
        return
    a = result.get("addon") or {}
    print(green("✓ ") + "Installed addon " + bold(result.get("name", "")))
    if a and a.get("status") != "loaded":
        print(yellow("! ") + f"but it did not load ({a.get('status')}) — "
              f"run: mcpanel addons info {result.get('name', '')}")


def render_addons_remove(result, args):
    if _err(result):
        return
    print(green("✓ ") + "Removed addon " + bold(result.get("name", "")))
    print(dim("  " + str(result.get("removed", ""))))
    print(dim("  Its data in addon-data/ was left untouched."))


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
    "list-sessions": render_sessions,
    "log-files": render_log_files,
    "log-file": render_log_file,
    "upload-log": render_upload_log,
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
    "jdk-compat": render_jdk_compat,
    "system": render_system,
    "app-version": render_app_version,
    "check-update": render_check_update,
    "debug-first-start": render_success,
    "shutdown": render_shutdown,
    "discover": render_discover,
    "search-plugins": lambda r, a: render_plugin_search(r, a, "plugin"),
    "search-mods": lambda r, a: render_plugin_search(r, a, "mod"),
    "install-plugin": render_install_plugin,
    "install-mod": render_install_plugin,
    "info-plugin": render_plugin_info,
    "proxy-info": render_proxy_info,
    "proxy-link": render_proxy_link,
    "errors": render_errors,
    "completion-bash": render_completion,
    "completion-zsh": render_completion,
    "backup-create": render_backup_create,
    "backup-list": render_backup_list,
    "backup-delete": render_success,
    "backup-restore": render_success,
    "buildtools-version": render_buildtools_version,
    "buildtools-update": render_buildtools_version,
    "addons-list": render_addons_list,
    "addons-info": render_addons_info,
    "addons-enable": render_addons_toggle,
    "addons-disable": render_addons_toggle,
    "addons-install": render_addons_install,
    "addons-remove": render_addons_remove,
}


# ─── addon renderers ─────────────────────────────────────────────────────────
# Populated by mcpanel.addons.AddonAPI. ADDON_ACTIONS tracks every action an
# addon mounted, so an addon command with no renderer of its own falls back to
# render_generic rather than to the raw-JSON dump built-ins use — without
# changing what any built-in prints.
ADDON_RENDERERS = {}
ADDON_ACTIONS = set()


def render_generic(result, args):
    """Readable default for addon results: flat scalars as a key/value block,
    lists of dicts as a table, anything deeper as indented JSON."""
    if _err(result):
        return
    if not isinstance(result, dict):
        print(result)
        return

    scalars, complex_ = [], []
    for key, value in result.items():
        (scalars if isinstance(value, (str, int, float, bool, type(None))) else complex_).append((key, value))

    if scalars:
        width = max(len(k) for k, _ in scalars)
        for key, value in scalars:
            if isinstance(value, bool):
                shown = green("yes") if value else dim("no")
            elif value is None or value == "":
                shown = dim("—")
            else:
                shown = str(value)
            print(f"  {dim(key.ljust(width))}  {shown}")

    for key, value in complex_:
        print()
        print(bold(key))
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            cols = list(value[0].keys())
            widths = {c: max(len(str(c)), *(len(str(row.get(c, ""))) for row in value)) for c in cols}
            print("  " + dim("  ".join(str(c).upper().ljust(widths[c]) for c in cols)))
            for row in value:
                print("  " + "  ".join(str(row.get(c, "")).ljust(widths[c]) for c in cols))
        elif isinstance(value, list) and all(not isinstance(v, (dict, list)) for v in value):
            if not value:
                print("  " + dim("(none)"))
            for item in value:
                print(f"  • {item}")
        else:
            import json
            for line in json.dumps(value, indent=2, default=str).splitlines():
                print("  " + line)


# ─── dispatch ────────────────────────────────────────────────────────────────
def render(action, result, args):
    fn = ADDON_RENDERERS.get(action)
    if fn is None and action in ADDON_ACTIONS:
        fn = render_generic
    if fn is None:
        fn = RENDERERS.get(action, render_config)
    fn(result, args)
