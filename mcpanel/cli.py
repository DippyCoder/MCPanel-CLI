"""Command-line entry point.

Builds one command tree and mounts it twice:

  * at the root      → human-readable output
  * under `api`      → identical commands, but raw JSON on stdout
                       (this is the "terminal as backend" surface)

Plus `mcpanel cli` for the interactive TUI (WIP).
"""

import argparse
import json
import sys
import time

from . import paths, servers, profiles, system, versions, runstate, render
from . import __version__


# ─── adapters for controllers that don't take (args, progress) ───────────────
def _versions(args, progress=None):
    return versions.fetch_versions(args.software, args.prerelease, args.unstable)


def _detect_jdk(args=None, progress=None):
    return {"jdks": system.detect_jdk()}


def _system_info(args=None, progress=None):
    return system.get_system_info()


def _app_version(args=None, progress=None):
    return {"version": system.get_version()}


def _check_update(args=None, progress=None):
    return system.check_update()


def _config_path(args=None, progress=None):
    return {
        "userData": paths.USER_DATA,
        "config": paths.CONFIG_FILE,
        "servers": paths.SERVERS_DIR,
        "profiles": paths.PROFILES_DIR,
        "themes": paths.THEMES_DIR,
        "run": paths.RUN_DIR,
    }



def _shutdown(args=None, progress=None):
    cfg = runstate  # just to trigger import; use load from servers
    from .config import load_config
    import time
    config = load_config()
    running = [s for s in config.get("servers", []) if runstate.is_running(s["id"])]
    if not running:
        return {"success": True, "stopped": []}
    stopped, failed = [], []
    for srv in running:
        r = runstate.send_request(srv["id"], {"op": "kill"})
        if r.get("ok"):
            stopped.append(srv["id"])
        else:
            failed.append({"id": srv["id"], "error": r.get("error")})
    time.sleep(0.8)
    for srv in running:
        runstate.cleanup_state(srv["id"])
    return {"success": True, "stopped": stopped, "failed": failed}


def _cli_tui(args=None, progress=None):
    from . import tui
    tui.run()
    return None


def _debug_first_start(args=None, progress=None):
    import os
    flag = os.path.join(paths.USER_DATA, "debug_first_start")
    with open(flag, "w") as f:
        f.write("")
    return {"success": True, "message": "First-start UI will show on the next MCPanel launch."}


# ─── flag helpers ────────────────────────────────────────────────────────────
def f_id(p, required=True):
    p.add_argument("-id", "--id", dest="id", required=required, metavar="<id>", help="server/profile/theme id")


def f_name(p, required=True):
    p.add_argument("-t", "--name", dest="name", required=required, metavar="<name>", help="display name")


def f_server_create(p):
    f_name(p)
    p.add_argument("-ram", "--ram", dest="ram", default="2048", metavar="<MB|nG>",
                   help="memory, megabytes or e.g. 4G (default 2048)")
    p.add_argument("-p", "-port", "--port", dest="port", type=int, default=25565, metavar="<port>")
    p.add_argument("-sw", "--software", dest="software", required=True, metavar="<software>",
                   help="paper|purpur|velocity|fabric|vanilla|leaf|folia|spigot")
    p.add_argument("-v", "--version", dest="version", required=True, metavar="<version>")
    p.add_argument("-profile", "--profile", dest="profile", default=None, metavar="<profileId>")
    p.add_argument("-java", "--java", dest="java", default=None, metavar="<path>")
    p.add_argument("-jargs", "--jargs", dest="jargs", default=None, metavar="<args>")
    p.add_argument("-storage", "--storage", dest="storage", default=None, metavar="<limit>",
                   help="storage limit e.g. 20G")
    p.add_argument("--unstable", dest="unstable", action="store_true", help="allow unstable/experimental builds")
    p.add_argument("--accept-eula", dest="accept_eula", action="store_true", help="write eula=true")


def noun(p, default="server"):
    p.add_argument("noun", nargs="?", default=default, help=argparse.SUPPRESS)


def leaf(sub, name, func, action, progress_ok=False, help=None):
    p = sub.add_parser(name, help=help)
    p.set_defaults(func=func, action=action, progress_ok=progress_ok)
    return p


# ─── command tree ────────────────────────────────────────────────────────────
def add_commands(sub):
    # create -------------------------------------------------------------
    create = sub.add_parser("create", help="create a server / profile")
    csub = create.add_subparsers(dest="noun", metavar="<server|profile|profile-from-server>", required=True)
    p = leaf(csub, "server", servers.create_server, "create-server", progress_ok=True,
             help="create + download a new server")
    f_server_create(p)
    p = leaf(csub, "profile", profiles.create_profile, "create-profile", help="create a server preset")
    f_name(p)
    p.add_argument("-desc", "--description", dest="desc", default=None, metavar="<text>")
    p.add_argument("-sw", "--software", dest="software", default=None, metavar="<a,b>", help="restrict software (comma list)")
    p.add_argument("-versions", "--versions", dest="versions", default=None, metavar="<a,b>", help="restrict versions (comma list)")
    p = leaf(csub, "profile-from-server", profiles.create_profile_from_server, "create-profile-from-server",
             help="snapshot files from a server into a new profile")
    f_id(p)
    f_name(p)
    p.add_argument("-paths", "--paths", dest="paths", required=True, metavar="<a,b>", help="relative paths to capture (comma list)")
    p.add_argument("-desc", "--description", dest="desc", default=None)
    p.add_argument("-sw", "--software", dest="software", default=None)
    p.add_argument("-versions", "--versions", dest="versions", default=None)

    # list ---------------------------------------------------------------
    lst = sub.add_parser("list", help="list servers / profiles", aliases=["ls"])
    lsub = lst.add_subparsers(dest="noun", metavar="<servers|profiles>", required=True)
    leaf(lsub, "servers", servers.list_servers, "list-servers")
    leaf(lsub, "profiles", profiles.list_profiles, "list-profiles")

    # info ---------------------------------------------------------------
    info = sub.add_parser("info", help="show details of a server / profile")
    isub = info.add_subparsers(dest="noun", metavar="<server|profile>", required=True)
    p = leaf(isub, "server", servers.fetch_server, "fetch-server"); f_id(p)
    p = leaf(isub, "profile", profiles.fetch_profile, "fetch-profile"); f_id(p)

    # fetch (getters, JSON-friendly) ------------------------------------
    fetch = sub.add_parser("fetch", help="fetch raw data (server/profile/config/log/...)")
    fsub = fetch.add_subparsers(dest="noun", metavar="<noun>", required=True)
    p = leaf(fsub, "server", servers.fetch_server, "fetch-server"); f_id(p)
    p = leaf(fsub, "profile", profiles.fetch_profile, "fetch-profile"); f_id(p)
    leaf(fsub, "config", servers.get_config, "config")
    p = leaf(fsub, "log", servers.get_server_log, "get-server-log"); f_id(p)
    p = leaf(fsub, "files", servers.get_server_file_tree, "file-tree"); f_id(p)
    p = leaf(fsub, "stats", servers.get_server_dir_stats, "stats"); f_id(p)
    p = leaf(fsub, "status", servers.is_server_running, "status"); f_id(p)
    leaf(fsub, "system", _system_info, "system")
    leaf(fsub, "update", _check_update, "check-update")
    leaf(fsub, "jdk", _detect_jdk, "jdk")
    p = leaf(fsub, "versions", _versions, "versions")
    p.add_argument("-sw", "--software", dest="software", required=True)
    p.add_argument("--unstable", dest="unstable", action="store_true")
    p.add_argument("--prerelease", dest="prerelease", action="store_true")
    # delete -------------------------------------------------------------
    delete = sub.add_parser("delete", help="delete a server / profile", aliases=["rm"])
    dsub = delete.add_subparsers(dest="noun", metavar="<server|profile>", required=True)
    p = leaf(dsub, "server", servers.delete_server, "delete-server"); f_id(p)
    p = leaf(dsub, "profile", profiles.delete_profile, "delete-profile"); f_id(p)

    # update -------------------------------------------------------------
    p = sub.add_parser("update", help="update server settings")
    noun(p); f_id(p)
    p.add_argument("-t", "--name", dest="name", default=None)
    p.add_argument("-p", "-port", "--port", dest="port", type=int, default=None)
    p.add_argument("-ram", "--ram", dest="ram", default=None)
    p.add_argument("-sw", "--software", dest="software", default=None)
    p.add_argument("-v", "--version", dest="version", default=None)
    p.add_argument("-java", "--java", dest="java", default=None)
    p.add_argument("-jargs", "--jargs", dest="jargs", default=None)
    p.add_argument("-storage", "--storage", dest="storage", default=None)
    p.set_defaults(func=servers.update_server, action="update-server")

    # lifecycle (single-noun verbs) -------------------------------------
    for name, fn, action, extra in [
        ("start", servers.start_server, "start-server", "eula"),
        ("stop", servers.stop_server, "stop-server", None),
        ("restart", servers.restart_server, "restart-server", "eula"),
        ("kill", servers.kill_server, "kill-server", None),
    ]:
        p = sub.add_parser(name, help=f"{name} a server")
        noun(p); f_id(p)
        if extra == "eula":
            p.add_argument("--accept-eula", dest="accept_eula", action="store_true")
        p.set_defaults(func=fn, action=action)

    p = sub.add_parser("cmd", help="send a console command to a running server")
    noun(p); f_id(p)
    p.add_argument("-c", "--command", dest="command", required=True, metavar="<command>")
    p.set_defaults(func=servers.send_command, action="send-command")

    p = sub.add_parser("logs", help="show a server's console log")
    noun(p); f_id(p)
    p.add_argument("-f", "--follow", dest="follow", action="store_true", help="stream new output (human mode)")
    p.add_argument("-n", "--session", dest="session", type=int, default=None,
                   metavar="<N>", help="show archived session N (1=most recent, see: mcpanel sessions)")
    p.set_defaults(func=servers.get_server_log, action="logs", follow=False, session=None)

    p = sub.add_parser("sessions", help="list archived log sessions for a server")
    noun(p); f_id(p)
    p.set_defaults(func=servers.list_session_logs, action="list-sessions")

    p = sub.add_parser("console", help="attach to a server's live console (follow)")
    noun(p); f_id(p)
    p.set_defaults(func=servers.get_server_log, action="logs", follow=True)

    p = sub.add_parser("ping", help="server-list-ping a server")
    noun(p)
    p.add_argument("-id", "--id", dest="id", default=None)
    p.add_argument("-host", "--host", dest="host", default=None)
    p.add_argument("-port", "-p", "--port", dest="port", type=int, default=None)
    p.set_defaults(func=servers.ping, action="ping")

    p = sub.add_parser("duplicate", help="duplicate a server", aliases=["clone"])
    noun(p); f_id(p); f_name(p)
    p.set_defaults(func=servers.duplicate_server, action="duplicate-server", progress_ok=True)

    p = sub.add_parser("files", help="show a server's file tree")
    noun(p); f_id(p)
    p.set_defaults(func=servers.get_server_file_tree, action="file-tree")

    p = sub.add_parser("stats", help="show a server's disk usage")
    noun(p); f_id(p)
    p.set_defaults(func=servers.get_server_dir_stats, action="stats")

    p = sub.add_parser("accept-eula", help="accept the Minecraft EULA for a server")
    noun(p); f_id(p)
    p.set_defaults(func=servers.accept_eula, action="accept-eula")

    # import -------------------------------------------------------------
    imp = sub.add_parser("import", help="import an existing server / profile folder")
    imsub = imp.add_subparsers(dest="noun", metavar="<server|profile>", required=True)
    p = leaf(imsub, "server", servers.import_server, "import-server", progress_ok=True)
    p.add_argument("-path", "--path", dest="path", required=True, metavar="<folder>")
    f_name(p)
    p.add_argument("-p", "-port", "--port", dest="port", type=int, default=None)
    p.add_argument("-ram", "--ram", dest="ram", default=None)
    p.add_argument("-sw", "--software", dest="software", default=None)
    p.add_argument("-v", "--version", dest="version", default=None)
    p.add_argument("-java", "--java", dest="java", default=None)
    p.add_argument("-jargs", "--jargs", dest="jargs", default=None)
    p = leaf(imsub, "profile", profiles.import_profile, "import-profile")
    p.add_argument("-path", "--path", dest="path", required=True, metavar="<folder>")
    f_name(p)
    p.add_argument("-desc", "--description", dest="desc", default=None)
    p.add_argument("-sw", "--software", dest="software", default=None)
    p.add_argument("-versions", "--versions", dest="versions", default=None)

    # proxy --------------------------------------------------------------
    proxy = sub.add_parser("proxy", help="Velocity proxy link utilities")
    proxysub = proxy.add_subparsers(dest="noun", metavar="<info|link>", required=True)
    p = leaf(proxysub, "info", servers.proxy_info, "proxy-info",
             help="list servers registered in a Velocity proxy config")
    p.add_argument("--velocity-id", dest="velocity_id", required=True, metavar="<id>")
    p = leaf(proxysub, "link", servers.link_to_proxy, "proxy-link",
             help="link a Paper-based server to a Velocity proxy")
    f_id(p)
    p.add_argument("--velocity-id", dest="velocity_id", required=True, metavar="<id>")
    p.add_argument("--server-name", dest="server_name", default=None, metavar="<name>")
    p.add_argument("--priority", dest="priority", type=int, default=None, metavar="<pos>")
    p.add_argument("--custom-ip", dest="custom_ip", default=None, metavar="<ip:port>")

    # scan ---------------------------------------------------------------
    scan = sub.add_parser("scan", help="inspect a folder before importing")
    ssub = scan.add_subparsers(dest="noun", metavar="<server|profile>", required=True)
    p = leaf(ssub, "server", servers.scan_server_folder, "scan-server")
    p.add_argument("-path", "--path", dest="path", required=True)
    p = leaf(ssub, "profile", profiles.scan_profile_folder, "scan-profile")
    p.add_argument("-path", "--path", dest="path", required=True)

    # open ---------------------------------------------------------------
    op = sub.add_parser("open", help="open a server / profile folder in the file manager")
    osub = op.add_subparsers(dest="noun", metavar="<server|profile>", required=True)
    p = leaf(osub, "server", servers.open_server_folder, "open-server"); f_id(p)
    p = leaf(osub, "profile", profiles.open_profile_folder, "open-profile"); f_id(p)

    # versions (top-level convenience) -----------------------------------
    p = sub.add_parser("versions", help="list available versions for a software")
    p.add_argument("-sw", "--software", dest="software", required=True)
    p.add_argument("--unstable", dest="unstable", action="store_true")
    p.add_argument("--prerelease", dest="prerelease", action="store_true")
    p.set_defaults(func=_versions, action="versions")

    # system / misc ------------------------------------------------------
    p = sub.add_parser("detect-jdk", help="find installed Java runtimes", aliases=["jdk"])
    p.set_defaults(func=_detect_jdk, action="jdk")
    p = sub.add_parser("system", help="show system RAM / storage")
    p.set_defaults(func=_system_info, action="system")
    p = sub.add_parser("version", help="show MCPanel CLI version")
    p.set_defaults(func=_app_version, action="app-version")
    p = sub.add_parser("check-update", help="check for a newer MCPanel release")
    p.set_defaults(func=_check_update, action="check-update")

    cfgp = sub.add_parser("config", help="show config / data paths")
    cfgsub = cfgp.add_subparsers(dest="noun", metavar="<show|path>", required=True)
    leaf(cfgsub, "show", servers.get_config, "config")
    leaf(cfgsub, "path", _config_path, "config")

    # shutdown -----------------------------------------------------------
    p = sub.add_parser("shutdown", help="kill all running servers and stop MCPanel")
    p.set_defaults(func=_shutdown, action="shutdown")

    # debug --------------------------------------------------------------
    dbg = sub.add_parser("debug", help="debugging utilities for MCPanel development")
    dbgsub = dbg.add_subparsers(dest="noun", metavar="<command>", required=True)
    leaf(dbgsub, "first_start", _debug_first_start, "debug-first-start",
         help="force the first-start UI to show on the next MCPanel launch")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mcpanel",
        description="MCPanel — Minecraft Server Panel, from the terminal.",
        epilog="Use 'mcpanel api <command>' for raw JSON output, or 'mcpanel cli' for the interactive GUI.",
    )
    parser.add_argument("--version", action="version", version=f"MCPanel CLI v{__version__}")
    parser.set_defaults(json=False)
    sub = parser.add_subparsers(dest="top", metavar="<command>")

    add_commands(sub)

    # api: same tree, JSON output
    api = sub.add_parser("api", help="run any command with raw JSON output")
    api.set_defaults(json=True)
    apisub = api.add_subparsers(dest="verb", metavar="<command>", required=True)
    add_commands(apisub)

    # cli: interactive TUI
    clip = sub.add_parser("cli", help="interactive terminal GUI (WIP)")
    clip.set_defaults(func=_cli_tui, action="cli")

    return parser


# ─── log following (human `console` / `logs -f`) ─────────────────────────────
def _follow_logs(server_id):
    path = runstate.log_path(server_id)
    print(render.dim(f"— attaching to {server_id} (Ctrl-C to detach) —"))
    pos = 0
    try:
        while True:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    f.seek(pos)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            text = rec.get("text", "")
                            print(render.red(text) if rec.get("type") == "err" else text)
                        except Exception:
                            print(line)
                    pos = f.tell()
            except FileNotFoundError:
                pass
            if not runstate.is_running(server_id):
                # drain any final bytes then stop
                time.sleep(0.3)
                with open(path, "r", encoding="utf-8") as f:
                    f.seek(pos)
                    rest = f.read()
                if rest.strip():
                    for line in rest.splitlines():
                        try:
                            print(json.loads(line).get("text", ""))
                        except Exception:
                            print(line)
                print(render.dim("— server stopped —"))
                return
            time.sleep(0.4)
    except KeyboardInterrupt:
        print(render.dim("\n— detached —"))


# ─── main ────────────────────────────────────────────────────────────────────
def _progress_printer():
    def p(pct, status):
        sys.stderr.write(f"\r  {status:<40}")
        sys.stderr.flush()
        if pct >= 100:
            sys.stderr.write("\n")
            sys.stderr.flush()
    return p


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    paths.ensure_dirs()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "func"):
        # bare `mcpanel` or `mcpanel <verb>` with no subcommand
        parser.print_help()
        return 0 if not argv else 2

    is_json = getattr(args, "json", False)

    # interactive GUI
    if getattr(args, "action", None) == "cli":
        args.func(args)
        return 0

    # human log following (skip if a specific archived session was requested)
    if (getattr(args, "action", None) == "logs" and not is_json
            and getattr(args, "follow", False) and getattr(args, "session", None) is None):
        if not getattr(args, "id", None):
            print(render.red("✗ -id is required"))
            return 1
        _follow_logs(args.id)
        return 0

    progress = _progress_printer() if (not is_json and getattr(args, "progress_ok", False)) else None

    try:
        result = args.func(args, progress)
    except BrokenPipeError:
        return 0
    except Exception as e:
        if is_json:
            print(json.dumps({"error": str(e)}))
        else:
            print(render.red("✗ " + str(e)))
        return 1

    if is_json:
        print(json.dumps(result, default=str))
    else:
        render.render(getattr(args, "action", ""), result, args)

    if isinstance(result, dict) and result.get("error"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
