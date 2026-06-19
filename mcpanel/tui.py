"""Interactive REPL for MCPanel.

Launch with:  mcpanel cli

  /create server            guided wizard
  /create server -t "name"  direct with some flags (wizard fills the rest)
  /list servers             no wizard needed
  Tab                       complete commands, IDs, software names, flags
  ↑ ↓                       command history / picker navigation
  /help                     full command list
  /exit  or  Ctrl-D         leave the TUI (servers keep running)
  /shutdown                 kill all running servers + exit
"""

import shlex
import sys
import types

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.application import Application
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style
    _PT = True
except ImportError:
    _PT = False

from . import paths, render, runstate
from . import __version__
from .config import load_config
from .versions import SOFTWARE


# ── static registry ──────────────────────────────────────────────────────────

_VERBS = [
    "create", "list", "ls", "info", "start", "stop", "restart", "kill",
    "cmd", "logs", "console", "sessions", "delete", "rm", "update",
    "duplicate", "clone", "import", "scan", "open", "ping", "files",
    "stats", "accept-eula", "versions", "detect-jdk", "jdk", "system",
    "version", "check-update", "config", "help", "exit", "quit", "shutdown",
]

_NOUNS = {
    "create":      ["server", "profile", "profile-from-server"],
    "list":        ["servers", "profiles"],
    "ls":          ["servers", "profiles"],
    "info":        ["server", "profile"],
    "start":       ["server"],
    "stop":        ["server"],
    "restart":     ["server"],
    "kill":        ["server"],
    "cmd":         ["server"],
    "logs":        ["server"],
    "console":     ["server"],
    "sessions":    ["server"],
    "delete":      ["server", "profile"],
    "rm":          ["server", "profile"],
    "update":      ["server"],
    "duplicate":   ["server"],
    "clone":       ["server"],
    "import":      ["server", "profile"],
    "scan":        ["server", "profile"],
    "open":        ["server", "profile"],
    "ping":        ["server"],
    "files":       ["server"],
    "stats":       ["server"],
    "accept-eula": ["server"],
    "config":      ["show", "path"],
}

_FLAGS = {
    ("create",  "server"):   ["-t", "-ram", "-port", "-sw", "-v", "-profile", "-java", "-jargs", "-storage", "--unstable", "--accept-eula"],
    ("create",  "profile"):  ["-t", "-desc", "-sw", "-versions"],
    ("create",  "profile-from-server"): ["-id", "-t", "-paths", "-desc", "-sw", "-versions"],
    ("update",  "server"):   ["-id", "-t", "-ram", "-port", "-sw", "-v", "-java", "-jargs", "-storage"],
    ("start",   "server"):   ["-id", "--accept-eula"],
    ("stop",    "server"):   ["-id"],
    ("restart", "server"):   ["-id", "--accept-eula"],
    ("kill",    "server"):   ["-id"],
    ("cmd",     "server"):   ["-id", "-c"],
    ("logs",    "server"):   ["-id", "-f", "-n"],
    ("console", "server"):   ["-id"],
    ("sessions","server"):   ["-id"],
    ("delete",  "server"):   ["-id"],
    ("delete",  "profile"):  ["-id"],
    ("info",    "server"):   ["-id"],
    ("info",    "profile"):  ["-id"],
    ("ping",    "server"):   ["-id", "-host", "-port"],
    ("files",   "server"):   ["-id"],
    ("stats",   "server"):   ["-id"],
    ("duplicate","server"):  ["-id", "-t"],
    ("import",  "server"):   ["-path", "-t", "-port", "-ram", "-sw", "-v", "-java", "-jargs"],
    ("import",  "profile"):  ["-path", "-t", "-desc", "-sw", "-versions"],
    ("scan",    "server"):   ["-path"],
    ("scan",    "profile"):  ["-path"],
    ("open",    "server"):   ["-id"],
    ("open",    "profile"):  ["-id"],
    ("accept-eula","server"):["-id"],
    ("versions",None):       ["-sw", "--unstable", "--prerelease"],
}

_USAGE = {
    ("create",  "server"):   "/create server -t <name> -sw <software> -v <version> [-ram <MB|nG>] [-port <n>] [--accept-eula]",
    ("create",  "profile"):  "/create profile -t <name> [-desc <text>] [-sw paper,purpur] [-versions 1.21,1.20]",
    ("create",  "profile-from-server"): "/create profile-from-server -id <id> -t <name> -paths <rel,paths>",
    ("update",  "server"):   "/update server -id <id> [-t <name>] [-ram <MB>] [-port <n>] [-sw <sw>] [-v <ver>]",
    ("list",    "servers"):  "/list servers",
    ("list",    "profiles"): "/list profiles",
    ("info",    "server"):   "/info server -id <id>",
    ("info",    "profile"):  "/info profile -id <id>",
    ("start",   "server"):   "/start server -id <id> [--accept-eula]",
    ("stop",    "server"):   "/stop server -id <id>",
    ("restart", "server"):   "/restart server -id <id> [--accept-eula]",
    ("kill",    "server"):   "/kill server -id <id>",
    ("cmd",     "server"):   "/cmd server -id <id> -c <command>",
    ("logs",    "server"):   "/logs server -id <id> [-f] [-n <session N>]",
    ("console", "server"):   "/console server -id <id>",
    ("sessions","server"):   "/sessions server -id <id>",
    ("delete",  "server"):   "/delete server -id <id>",
    ("delete",  "profile"):  "/delete profile -id <id>",
    ("ping",    "server"):   "/ping server [-id <id>] [-host <h>] [-port <p>]",
    ("files",   "server"):   "/files server -id <id>",
    ("stats",   "server"):   "/stats server -id <id>",
    ("duplicate","server"):  "/duplicate server -id <id> -t <new name>",
    ("import",  "server"):   "/import server -path <folder> -t <name> [-sw <sw>] [-v <ver>]",
    ("import",  "profile"):  "/import profile -path <folder> -t <name>",
    ("scan",    "server"):   "/scan server -path <folder>",
    ("scan",    "profile"):  "/scan profile -path <folder>",
    ("open",    "server"):   "/open server -id <id>",
    ("open",    "profile"):  "/open profile -id <id>",
    ("accept-eula","server"):"/accept-eula server -id <id>",
    ("versions",None):       "/versions -sw <paper|purpur|velocity|fabric|vanilla|leaf|folia|spigot> [--unstable] [--prerelease]",
    ("detect-jdk",None):     "/detect-jdk",
    ("system",  None):       "/system",
    ("version", None):       "/version",
    ("check-update",None):   "/check-update",
    ("config",  "show"):     "/config show",
    ("config",  "path"):     "/config path",
}


# ── data helpers ─────────────────────────────────────────────────────────────

def _server_entries():
    try:
        return load_config().get("servers", [])
    except Exception:
        return []

def _server_ids():
    return [s["id"] for s in _server_entries()]

def _server_labels():
    return [f"{s['id']}  {s.get('name','')}" for s in _server_entries()]

def _profile_ids():
    import os
    try:
        return [d for d in os.listdir(paths.PROFILES_DIR)
                if os.path.isdir(os.path.join(paths.PROFILES_DIR, d))]
    except Exception:
        return []


# ── inline arrow-key picker ──────────────────────────────────────────────────

def _pick(title, options, default=0):
    """Inline arrow-key selector. Returns chosen string or raises KeyboardInterrupt."""
    if not options:
        raise ValueError("No options to pick from")

    if not _PT:
        print(f"\n  {title}")
        for i, o in enumerate(options):
            print(f"  {'>' if i == default else ' '} [{i+1}] {o}")
        while True:
            try:
                raw = input(f"\n  Choice [1-{len(options)}]: ").strip()
            except (EOFError, KeyboardInterrupt):
                raise KeyboardInterrupt
            if not raw:
                return options[default]
            try:
                n = int(raw) - 1
                if 0 <= n < len(options):
                    return options[n]
            except ValueError:
                pass
            print(f"  Enter a number 1-{len(options)}")

    state = {"idx": min(default, len(options) - 1)}
    chosen = [None]

    kb = KeyBindings()

    @kb.add("up")
    def _(ev):
        state["idx"] = (state["idx"] - 1) % len(options)

    @kb.add("down")
    def _(ev):
        state["idx"] = (state["idx"] + 1) % len(options)

    @kb.add("enter")
    def _(ev):
        chosen[0] = options[state["idx"]]
        ev.app.exit()

    @kb.add("escape")
    @kb.add("c-c")
    def _(ev):
        ev.app.exit()

    def content():
        lines = [("class:pk-title", f"  {title}\n")]
        start = max(0, state["idx"] - 5)
        end   = min(len(options), start + 12)
        if end - start < 12:
            start = max(0, end - 12)
        if start > 0:
            lines.append(("class:pk-hint", f"  ↑ {start} more\n"))
        for i in range(start, end):
            if i == state["idx"]:
                lines.append(("class:pk-sel", f"  ❯ {options[i]}\n"))
            else:
                lines.append(("class:pk-item", f"    {options[i]}\n"))
        if end < len(options):
            lines.append(("class:pk-hint", f"  ↓ {len(options)-end} more\n"))
        lines.append(("class:pk-hint", "  ↑↓ navigate   Enter select   Esc cancel\n"))
        return lines

    style = Style.from_dict({
        "pk-title": "bold",
        "pk-sel":   "bold ansicyan",
        "pk-item":  "",
        "pk-hint":  "italic ansibrightblack",
    })
    app = Application(
        layout=Layout(Window(FormattedTextControl(content, focusable=True))),
        key_bindings=kb, style=style, full_screen=False, mouse_support=False,
    )
    app.run()
    if chosen[0] is None:
        raise KeyboardInterrupt
    return chosen[0]


# ── text / confirm prompts ────────────────────────────────────────────────────

def _ask(label, default=None, secret=False):
    """Single-line text prompt. Returns stripped input or default."""
    disp = f"  {label}"
    if default is not None:
        disp += f" [{default}]"
    disp += ":  "
    try:
        if _PT:
            from prompt_toolkit import prompt as _p
            val = _p(disp, is_password=secret).strip()
        else:
            val = input(disp).strip()
        return val if val else default
    except (EOFError, KeyboardInterrupt):
        raise KeyboardInterrupt


def _confirm(label, default=False):
    hint = "[Y/n]" if default else "[y/N]"
    try:
        raw = _ask(f"{label} {hint}", default="y" if default else "n")
        return bool(raw and raw.lower().startswith("y"))
    except KeyboardInterrupt:
        return False


# ── completer ────────────────────────────────────────────────────────────────

class _Completer(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        if not text.startswith("/"):
            if not text.strip():
                yield Completion("/", start_position=0, display_meta="start a command")
            return

        raw = text[1:]
        parts = raw.split()
        trailing_space = text.endswith(" ")

        # ── completing the verb ──
        if not parts or (len(parts) == 1 and not trailing_space):
            prefix = parts[0] if parts else ""
            for verb in _VERBS:
                if verb.startswith(prefix):
                    sample = _USAGE.get((verb, None), "")
                    yield Completion(
                        "/" + verb,
                        start_position=-len(text),
                        display=verb,
                        display_meta=sample,
                    )
            return

        verb = parts[0]
        nouns = _NOUNS.get(verb)

        # ── completing the noun ──
        if nouns and (not trailing_space and len(parts) == 2) or \
                     (trailing_space and len(parts) == 1):
            prefix = parts[1] if len(parts) == 2 and not trailing_space else ""
            for noun in nouns:
                if noun.startswith(prefix):
                    yield Completion(
                        noun,
                        start_position=-len(prefix),
                        display=noun,
                        display_meta=_USAGE.get((verb, noun), ""),
                    )
            return

        noun = (parts[1] if nouns and len(parts) > 1
                         and not parts[1].startswith("-") else None)
        flag_parts = parts[2:] if noun else parts[1:]
        avail = _FLAGS.get((verb, noun), [])
        last  = parts[-1] if parts else ""
        prev  = parts[-2] if len(parts) >= 2 else ""

        # ── completing -id argument value ──
        if prev in ("-id", "--id"):
            if noun in ("server", None):
                for sid in _server_ids():
                    yield Completion(sid, start_position=0, display_meta="server")
            elif noun == "profile":
                for pid in _profile_ids():
                    yield Completion(pid, start_position=0, display_meta="profile")
            return

        # ── completing -sw argument value ──
        if prev in ("-sw", "--software"):
            for sw in SOFTWARE:
                yield Completion(sw, start_position=0)
            return

        # ── completing flag names ──
        if last.startswith("-") or trailing_space:
            prefix = last if last.startswith("-") else ""
            used = set(parts)
            for flag in avail:
                if flag.startswith(prefix) and flag not in used:
                    yield Completion(flag, start_position=-len(prefix))


# ── toolbar ───────────────────────────────────────────────────────────────────

def _toolbar(text):
    def _esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    if not text.startswith("/"):
        return HTML("<ansibrightblack>Type <b>/</b> to start a command — <b>/help</b> for the full list</ansibrightblack>")
    parts = text[1:].split()
    if not parts:
        return HTML("<ansibrightblack>/&lt;command&gt;  Tab to complete</ansibrightblack>")
    verb = parts[0]
    noun = parts[1] if len(parts) > 1 and not parts[1].startswith("-") else None
    usage = _USAGE.get((verb, noun)) or _USAGE.get((verb, None))
    if usage:
        return HTML(f"<ansibrightblack>{_esc(usage)}</ansibrightblack>")
    if verb in _NOUNS and _NOUNS[verb]:
        opts = "|".join(_NOUNS[verb])
        return HTML(f"<ansibrightblack>/{_esc(verb)} &lt;{_esc(opts)}&gt; ...</ansibrightblack>")
    return HTML(f"<ansibrightblack>/{_esc(verb)}</ansibrightblack>")


# ── flag parser ───────────────────────────────────────────────────────────────

def _parse_flags(parts):
    """Return a flat dict of flag→value from a token list."""
    flags = {}
    i = 0
    while i < len(parts):
        p = parts[i]
        if p.startswith("-"):
            key = p.lstrip("-").replace("-", "_")
            if i + 1 < len(parts) and not parts[i + 1].startswith("-"):
                flags[key] = parts[i + 1]
                i += 2
            else:
                flags[key] = True
                i += 1
        else:
            i += 1
    return flags


# ── result printer ────────────────────────────────────────────────────────────

def _pr(action, result):
    import argparse
    render.render(action, result, argparse.Namespace())


# ── wizards ───────────────────────────────────────────────────────────────────

def _pick_server(prompt="Select server:", running_only=False):
    """Picker that returns server id or raises KeyboardInterrupt."""
    entries = _server_entries()
    if running_only:
        entries = [s for s in entries if runstate.is_running(s["id"])]
    if not entries:
        label = "running " if running_only else ""
        print(render.yellow(f"\n  No {label}servers found."))
        raise KeyboardInterrupt
    labels = [f"{s['id']}  {s.get('name','')}" for s in entries]
    chosen = _pick(prompt, labels)
    idx = labels.index(chosen)
    return entries[idx]["id"]


def _wizard_create_server(flags):
    print()
    name = flags.get("name") or flags.get("t") or _ask("Server name")
    if not name:
        return

    sw_list = list(SOFTWARE)
    sw = flags.get("software") or flags.get("sw")
    if not sw:
        sw = _pick("Select software:", sw_list)

    version = flags.get("version") or flags.get("v")
    if not version:
        print(f"\n  Fetching {sw} versions...")
        from .versions import fetch_versions
        vdata = fetch_versions(sw)
        vers = vdata.get("versions", [])
        if vers:
            version = _pick(f"Select {sw} version:", vers)
        else:
            version = _ask("Version")
    if not version:
        return

    ram  = flags.get("ram")  or _ask("RAM", default="2G")
    port = flags.get("port") or _ask("Port", default="25565")
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 25565

    accept = flags.get("accept_eula") or _confirm("\n  Accept Minecraft EULA?")

    print(f"\n  Creating {name!r} ({sw} {version}, {ram} RAM, port {port})...")
    from . import servers
    ns = types.SimpleNamespace(
        name=name, software=sw, version=version, ram=ram, port=port,
        profile=None, java=None, jargs=None, storage=None,
        unstable=False, accept_eula=accept,
    )
    _pr("create-server", servers.create_server(ns))


def _wizard_create_profile(flags):
    print()
    name = flags.get("name") or flags.get("t") or _ask("Profile name")
    if not name:
        return
    desc = flags.get("desc") or _ask("Description", default="") or ""
    sw   = flags.get("software") or flags.get("sw") or ""
    vers = flags.get("versions") or ""
    from . import profiles
    ns = types.SimpleNamespace(name=name, desc=desc, software=sw, versions=vers)
    _pr("create-profile", profiles.create_profile(ns))


def _wizard_start(flags, restart=False):
    sid = flags.get("id")
    if not sid:
        print()
        sid = _pick_server(f"Select server to {'restart' if restart else 'start'}:")
    accept = bool(flags.get("accept_eula"))
    from . import servers
    ns = types.SimpleNamespace(id=sid, accept_eula=accept)
    fn = servers.restart_server if restart else servers.start_server
    action = "restart-server" if restart else "start-server"
    _pr(action, fn(ns))


def _wizard_stop_kill(flags, kill=False):
    sid = flags.get("id")
    if not sid:
        print()
        sid = _pick_server(f"Select server to {'kill' if kill else 'stop'}:", running_only=True)
    from . import servers
    ns = types.SimpleNamespace(id=sid)
    fn = servers.kill_server if kill else servers.stop_server
    action = "kill-server" if kill else "stop-server"
    _pr(action, fn(ns))


def _wizard_cmd(flags):
    sid = flags.get("id")
    if not sid:
        print()
        sid = _pick_server("Select server:", running_only=True)
    cmd = flags.get("command") or flags.get("c") or _ask("Console command")
    if not cmd:
        return
    from . import servers
    _pr("send-command", servers.send_command(types.SimpleNamespace(id=sid, command=cmd)))


def _wizard_logs(flags, follow_override=False):
    sid = flags.get("id")
    if not sid:
        print()
        sid = _pick_server("Select server:")
    follow  = follow_override or bool(flags.get("follow") or flags.get("f"))
    session = flags.get("session") or flags.get("n")
    if follow:
        from .cli import _follow_logs
        _follow_logs(sid)
    else:
        from . import servers
        ns = types.SimpleNamespace(id=sid, session=int(session) if session else None)
        _pr("logs", servers.get_server_log(ns))


def _wizard_update(flags):
    sid = flags.get("id")
    if not sid:
        print()
        sid = _pick_server("Select server to update:")
    from . import servers
    p = flags.get("port")
    ns = types.SimpleNamespace(
        id=sid,
        name=flags.get("name") or flags.get("t"),
        port=int(p) if p else None,
        ram=flags.get("ram"),
        software=flags.get("software") or flags.get("sw"),
        version=flags.get("version") or flags.get("v"),
        java=flags.get("java"),
        jargs=flags.get("jargs"),
        storage=flags.get("storage"),
    )
    _pr("update-server", servers.update_server(ns))


def _wizard_delete_server(flags):
    sid = flags.get("id")
    if not sid:
        print()
        sid = _pick_server("Select server to delete:")
    if not _confirm(f"\n  Permanently delete {sid}?"):
        print(render.dim("  Aborted."))
        return
    from . import servers
    _pr("delete-server", servers.delete_server(types.SimpleNamespace(id=sid)))


def _wizard_delete_profile(flags):
    pid = flags.get("id")
    if not pid:
        pids = _profile_ids()
        if not pids:
            print(render.yellow("\n  No profiles found."))
            return
        print()
        pid = _pick("Select profile to delete:", pids)
    if not _confirm(f"\n  Permanently delete {pid}?"):
        print(render.dim("  Aborted."))
        return
    from . import profiles
    _pr("delete-profile", profiles.delete_profile(types.SimpleNamespace(id=pid)))


# ── shutdown ──────────────────────────────────────────────────────────────────

def _shutdown_all():
    """Kill every running supervisor, clean up state, then exit the TUI."""
    import time
    cfg = load_config()
    running = [s for s in cfg.get("servers", []) if runstate.is_running(s["id"])]
    if not running:
        print(render.dim("\n  No servers running. Shutting down."))
        raise SystemExit(0)

    print(render.yellow(f"\n  Shutting down {len(running)} running server{'s' if len(running) != 1 else ''}..."))
    for srv in running:
        sid  = srv["id"]
        name = srv.get("name", sid)
        r = runstate.send_request(sid, {"op": "kill"})
        if r.get("ok"):
            print(f"  {render.dim('killed')}  {name}  {render.dim(sid)}")
        else:
            print(f"  {render.yellow('warn')}    {name}  {render.dim(r.get('error', ''))}")

    # Give supervisors a moment to write final log entries and remove their state files
    time.sleep(0.8)

    # Force-clean any leftover state files
    for srv in running:
        runstate.cleanup_state(srv["id"])

    print(render.dim("  All stopped. Goodbye."))
    raise SystemExit(0)


# ── command executor ──────────────────────────────────────────────────────────

def _execute(text):
    text = text.strip()
    if not text:
        return
    if text.lower() in ("exit", "quit"):
        raise SystemExit(0)
    if text.lower() == "shutdown":
        _shutdown_all()
        return
    if not text.startswith("/"):
        print(render.yellow("  Commands start with /  — try /help"))
        return

    try:
        parts = shlex.split(text[1:])
    except ValueError as e:
        print(render.red(f"  Parse error: {e}"))
        return
    if not parts:
        return

    verb = parts[0].lower()
    # noun: second token if it doesn't start with -
    noun = (parts[1].lower()
            if len(parts) > 1 and not parts[1].startswith("-") else None)
    flag_parts = parts[2:] if noun else parts[1:]
    flags = _parse_flags(flag_parts)

    try:
        _dispatch(verb, noun, flags)
    except KeyboardInterrupt:
        print(render.dim("\n  Cancelled."))
    except Exception as e:
        print(render.red(f"  Error: {e}"))


def _dispatch(verb, noun, flags):
    # ── meta ──────────────────────────────────────────────────────────────
    if verb in ("exit", "quit"):
        raise SystemExit(0)

    if verb == "shutdown":
        _shutdown_all()
        return

    if verb == "help":
        _help()
        return

    # ── no-arg commands ───────────────────────────────────────────────────
    if verb in ("detect-jdk", "jdk"):
        from . import system
        _pr("jdk", {"jdks": system.detect_jdk()})
        return
    if verb == "system":
        from . import system
        _pr("system", system.get_system_info())
        return
    if verb == "version":
        _pr("app-version", {"version": __version__})
        return
    if verb == "check-update":
        from . import system
        _pr("check-update", system.check_update())
        return

    # ── config ────────────────────────────────────────────────────────────
    if verb == "config":
        if noun == "path":
            _pr("config", {
                "userData": paths.USER_DATA,
                "config": paths.CONFIG_FILE,
                "servers": paths.SERVERS_DIR,
                "profiles": paths.PROFILES_DIR,
                "themes": paths.THEMES_DIR,
                "run": paths.RUN_DIR,
            })
        else:
            from . import servers
            _pr("config", servers.get_config(None))
        return

    # ── versions ──────────────────────────────────────────────────────────
    if verb == "versions":
        sw = flags.get("software") or flags.get("sw")
        if not sw:
            sw = _pick("Select software:", list(SOFTWARE))
        print(f"\n  Fetching {sw} versions...")
        from .versions import fetch_versions
        _pr("versions", fetch_versions(sw,
            pre_release=bool(flags.get("prerelease")),
            unstable=bool(flags.get("unstable")),
        ))
        return

    # ── list ──────────────────────────────────────────────────────────────
    if verb in ("list", "ls"):
        if noun in ("servers", None):
            from . import servers
            _pr("list-servers", servers.list_servers(None))
        elif noun == "profiles":
            from . import profiles
            _pr("list-profiles", profiles.list_profiles(None))
        return

    # ── info ──────────────────────────────────────────────────────────────
    if verb == "info":
        if noun in ("server", None):
            sid = flags.get("id") or _pick_server("Select server:")
            from . import servers
            _pr("fetch-server", servers.fetch_server(types.SimpleNamespace(id=sid)))
        elif noun == "profile":
            pid = flags.get("id") or _pick("Select profile:", _profile_ids())
            from . import profiles
            _pr("fetch-profile", profiles.fetch_profile(types.SimpleNamespace(id=pid)))
        return

    # ── create ────────────────────────────────────────────────────────────
    if verb == "create":
        if noun in ("server", None):
            _wizard_create_server(flags)
        elif noun == "profile":
            _wizard_create_profile(flags)
        else:
            print(render.yellow(f"  Unknown: /create {noun}"))
        return

    # ── lifecycle ─────────────────────────────────────────────────────────
    if verb == "start":
        _wizard_start(flags)
        return
    if verb == "restart":
        _wizard_start(flags, restart=True)
        return
    if verb == "stop":
        _wizard_stop_kill(flags)
        return
    if verb == "kill":
        _wizard_stop_kill(flags, kill=True)
        return

    # ── cmd ───────────────────────────────────────────────────────────────
    if verb == "cmd":
        _wizard_cmd(flags)
        return

    # ── logs / console ────────────────────────────────────────────────────
    if verb == "logs":
        _wizard_logs(flags)
        return
    if verb == "console":
        _wizard_logs(flags, follow_override=True)
        return

    # ── sessions ──────────────────────────────────────────────────────────
    if verb == "sessions":
        sid = flags.get("id") or _pick_server("Select server:")
        from . import servers
        _pr("list-sessions", servers.list_session_logs(types.SimpleNamespace(id=sid)))
        return

    # ── delete ────────────────────────────────────────────────────────────
    if verb in ("delete", "rm"):
        if noun in ("server", None):
            _wizard_delete_server(flags)
        elif noun == "profile":
            _wizard_delete_profile(flags)
        return

    # ── update ────────────────────────────────────────────────────────────
    if verb == "update":
        _wizard_update(flags)
        return

    # ── duplicate ─────────────────────────────────────────────────────────
    if verb in ("duplicate", "clone"):
        sid = flags.get("id") or _pick_server("Select server to duplicate:")
        name = flags.get("name") or flags.get("t") or _ask("\n  New server name")
        if name:
            from . import servers
            _pr("duplicate-server",
                servers.duplicate_server(types.SimpleNamespace(id=sid, name=name)))
        return

    # ── ping ──────────────────────────────────────────────────────────────
    if verb == "ping":
        sid  = flags.get("id")
        host = flags.get("host")
        port = flags.get("port")
        if not sid and not host:
            print()
            sid = _pick_server("Select server to ping:")
        from . import servers
        _pr("ping", servers.ping(types.SimpleNamespace(
            id=sid, host=host,
            port=int(port) if port else None,
        )))
        return

    # ── files / stats ─────────────────────────────────────────────────────
    if verb in ("files", "stats"):
        sid = flags.get("id") or _pick_server(f"Select server ({verb}):")
        from . import servers
        if verb == "files":
            _pr("file-tree", servers.get_server_file_tree(types.SimpleNamespace(id=sid)))
        else:
            _pr("stats", servers.get_server_dir_stats(types.SimpleNamespace(id=sid)))
        return

    # ── open ──────────────────────────────────────────────────────────────
    if verb == "open":
        if noun in ("server", None):
            sid = flags.get("id") or _pick_server("Select server to open:")
            from . import servers
            _pr("open-server", servers.open_server_folder(types.SimpleNamespace(id=sid)))
        elif noun == "profile":
            pid = flags.get("id") or _pick("Select profile to open:", _profile_ids())
            from . import profiles
            _pr("open-profile", profiles.open_profile_folder(types.SimpleNamespace(id=pid)))
        return

    # ── accept-eula ───────────────────────────────────────────────────────
    if verb == "accept-eula":
        sid = flags.get("id") or _pick_server("Select server:")
        from . import servers
        _pr("accept-eula", servers.accept_eula(types.SimpleNamespace(id=sid)))
        return

    # ── scan ──────────────────────────────────────────────────────────────
    if verb == "scan":
        path = flags.get("path")
        if not path:
            path = _ask("Folder path")
        if not path:
            return
        if noun in ("server", None):
            from . import servers
            _pr("scan-server", servers.scan_server_folder(types.SimpleNamespace(path=path)))
        elif noun == "profile":
            from . import profiles
            _pr("scan-profile", profiles.scan_profile_folder(types.SimpleNamespace(path=path)))
        return

    # ── import ────────────────────────────────────────────────────────────
    if verb == "import":
        path = flags.get("path")
        if not path:
            path = _ask("\n  Folder path")
        if not path:
            return
        name = flags.get("name") or flags.get("t") or _ask("Name")
        if not name:
            return
        if noun in ("server", None):
            from . import servers
            p = flags.get("port")
            ns = types.SimpleNamespace(
                path=path, name=name,
                port=int(p) if p else None,
                ram=flags.get("ram"),
                software=flags.get("software") or flags.get("sw"),
                version=flags.get("version") or flags.get("v"),
                java=flags.get("java"),
                jargs=flags.get("jargs"),
            )
            _pr("import-server", servers.import_server(ns))
        elif noun == "profile":
            from . import profiles
            ns = types.SimpleNamespace(
                path=path, name=name,
                desc=flags.get("desc") or "",
                software=flags.get("software") or flags.get("sw") or "",
                versions=flags.get("versions") or "",
            )
            _pr("import-profile", profiles.import_profile(ns))
        return

    print(render.yellow(f"  Unknown command: /{verb}  — try /help"))


# ── help ──────────────────────────────────────────────────────────────────────

def _help():
    B = render.bold
    C = render.cyan
    D = render.dim
    print(f"""
{B("  MCPanel CLI commands")}
  ─────────────────────────────────────────────────────────────────

{C("  Servers")}
  /create server           guided wizard (or pass flags to skip steps)
  /list servers            list all servers + running status
  /info server             show full server details
  /start  /stop  /restart  /kill  server
  /cmd server -c <command> send a console command
  /console server          attach to live output  (Ctrl-C to detach)
  /logs server [-f] [-n N] view or follow log, or open archived session
  /sessions server         list archived log sessions
  /update server           change settings (name/ram/port/sw/version/…)
  /duplicate server        copy a server
  /delete server           delete server + files
  /ping server             server-list-ping
  /files server            file tree
  /stats server            disk usage
  /open server             open folder in file manager
  /accept-eula server      write eula=true
  /scan server -path <dir> detect port/software in a folder
  /import server           import an existing server folder

{C("  Profiles")}
  /create profile          /list profiles
  /info profile            /delete profile
  /import profile          /scan profile
  /open profile

{C("  Versions & system")}
  /versions -sw <software>   list available versions
  /detect-jdk                find Java installations
  /system                    RAM + storage info
  /version                   MCPanel CLI version
  /check-update              check for a newer release

{C("  General")}
  /config show | path      raw config / data-directory paths
  Tab                      complete commands, IDs, software names, flags
  ↑ ↓                      command history
  /help                    this message
  /exit  or  Ctrl-D        leave the TUI
  /shutdown                kill all running servers + exit
""")


# ── main loop ─────────────────────────────────────────────────────────────────

def run():
    print(render.bold(f"\n  MCPanel CLI  v{__version__}"))
    print(render.dim("  /help for commands · Tab to complete · /exit to quit · /shutdown to stop all servers\n"))

    if not _PT:
        print(render.yellow(
            "  prompt_toolkit not installed — completions disabled.\n"
            "  Run:  pip install prompt_toolkit\n"
        ))

    if _PT:
        session = PromptSession(
            completer=_Completer(),
            complete_while_typing=False,
            bottom_toolbar=lambda: _toolbar(session.default_buffer.text),
            style=Style.from_dict({"prompt": "bold ansicyan", "bottom-toolbar": "bg:#1a1a1a #888888"}),
            history=InMemoryHistory(),
            mouse_support=False,
        )
        prompt_str = HTML("<ansicyan><b>mcpanel</b></ansicyan> <ansibrightblack>›</ansibrightblack> ")
    else:
        session = None

    while True:
        try:
            if session:
                line = session.prompt(prompt_str)
            else:
                line = input("mcpanel › ")
            _execute(line)
        except KeyboardInterrupt:
            print()
        except EOFError:
            print(render.dim("\n  Goodbye."))
            break
        except SystemExit:
            print(render.dim("  Goodbye."))
            break
