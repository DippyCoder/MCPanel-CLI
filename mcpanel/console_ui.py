"""Full-screen live server console — used by `mcpanel console`, `mcpanel logs
-f`, and the TUI's `/console` (and offered right after `/start` / `/restart`).

Layout is a fixed frame of "=" rule lines around three regions: a title/status
header, a scrolling color-coded log, and a command input line. Ctrl-C/S/R/K
are wired to real lifecycle actions rather than being decorative hints.
"""

import json
import os
import re
import threading
import time
import types

from . import render, runstate
from .config import load_config, find_server
from .ping import ping_server

try:
    from prompt_toolkit import Application
    from prompt_toolkit.formatted_text import ANSI, to_formatted_text
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout, HSplit, Window
    from prompt_toolkit.layout.containers import WindowAlign
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.mouse_events import MouseEventType
    from prompt_toolkit.styles import Style
    _PT = True
except ImportError:
    _PT = False


_LOG_LINE_RE = re.compile(r"^(\[[^\]]*\]:)\s?(.*)$")
_MAX_LINES = 3000


# ── log line coloring ────────────────────────────────────────────────────────

def _classify(rec):
    """A log record -> list of (style, text) fragments, no trailing newline."""
    text = rec.get("text", "")
    rtype = rec.get("type")
    if rtype == "cmd":
        return [("class:log-cmd", text)]

    m = _LOG_LINE_RE.match(text)
    header, rest = m.groups() if m else (None, text)

    # Modern server jars (Paper/Purpur/Leaf's Brigadier error highlighting,
    # some Log4j configs) emit real ANSI/truecolor escapes in their console
    # output even though it's piped, not a tty. Render those as actual
    # colors instead of the raw \x1b[...m bytes.
    if "\x1b[" in rest:
        try:
            frags = list(to_formatted_text(ANSI(rest)))
        except Exception:
            frags = [("class:log-info", rest)]
        return ([("class:log-dim", header + " ")] + frags) if header else frags

    if rtype == "err":
        return [("class:log-error", text)]
    if not header:
        return [("class:log-info", text)]

    upper = header.upper()
    if "ERROR" in upper or "SEVERE" in upper or "FATAL" in upper:
        level_style = "class:log-error"
    elif "WARN" in upper:
        level_style = "class:log-warn"
    elif "DEBUG" in upper:
        level_style = "class:log-debug"
    else:
        level_style = "class:log-info"
    return [
        ("class:log-dim", header + " "),
        (level_style, rest),
    ]


# ── background workers ───────────────────────────────────────────────────────

def _tail_log(state, server_id):
    pos = 0
    while not state["stop"]:
        path = runstate.log_path(server_id)
        try:
            size = os.path.getsize(path)
            if pos > size:
                pos = 0  # server restarted / log rotated out from under us
            with open(path, "r", encoding="utf-8") as f:
                f.seek(pos)
                new_lines = f.readlines()
                pos = f.tell()
        except FileNotFoundError:
            new_lines = []
        if new_lines:
            with state["lock"]:
                for line in new_lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        state["lines"].append(json.loads(line))
                    except Exception:
                        state["lines"].append({"text": line, "type": "out"})
                if len(state["lines"]) > _MAX_LINES:
                    state["lines"] = state["lines"][-_MAX_LINES:]
        time.sleep(0.3)


def _poll_status(state, server_id, port):
    while not state["stop"]:
        running = runstate.is_running(server_id)
        ping = ping_server("127.0.0.1", int(port), timeout=1.5) if running else {"online": False}
        with state["lock"]:
            state["running"] = running
            state["ping"] = ping
        time.sleep(2.0)


# ── fragment builders ────────────────────────────────────────────────────────

def _title_frags(state):
    return [("class:title", "Server Console")]


def _status_frags(state, name):
    with state["lock"]:
        running = state["running"]
        ping = state["ping"]
    if not running:
        return [("", "Server: "), ("class:name", name), ("", " | "),
                ("class:status-off", "Offline")]
    if ping.get("online"):
        players = f"{ping.get('players', 0)}/{ping.get('maxPlayers', 0)} Players"
        return [("", "Server: "), ("class:name", name), ("", f" | {players} | "),
                ("class:status-on", "Online")]
    return [("", "Server: "), ("class:name", name), ("", " | "),
            ("class:status-pending", "Starting…")]


def _log_frags(state):
    with state["lock"]:
        lines = list(state["lines"])
        offset = state["scroll_offset"]
    frags = []
    if not lines:
        frags.append(("[SetCursorPosition]", ""))
        frags.append(("class:log-dim", "  (no output yet)"))
        return frags

    # scroll_offset counts lines back from the newest one; 0 == following the
    # live tail. The cursor is placed on that anchor line — prompt_toolkit's
    # Window keeps whatever fragment holds `[SetCursorPosition]` in view, so
    # moving the marker is what makes the pane track a scrolled-up position
    # instead of always snapping back to the bottom.
    offset = max(0, min(offset, len(lines) - 1))
    anchor = len(lines) - 1 - offset
    for i, rec in enumerate(lines):
        if frags:
            frags.append(("", "\n"))
        if i == anchor:
            frags.append(("[SetCursorPosition]", ""))
        frags.extend(_classify(rec))
    return frags


def _input_frags(state):
    return [("class:prompt", "> "), ("", state["input"]), ("reverse", " ")]


def _footer_frags(state):
    out = []
    for key, label in (("^C", "close"), ("^S", "stop/start"), ("^R", "restart"), ("^K", "kill")):
        if out:
            out.append(("", "        "))
        out.append(("class:footer-key", key))
        out.append(("class:footer-dim", f"={label}"))
    return out


# ── key bindings ──────────────────────────────────────────────────────────────

def _run_bg(fn):
    threading.Thread(target=fn, daemon=True).start()


def _run_action(state, label, fn):
    """Run a lifecycle action in the background and surface any failure in the
    log pane. Fire-and-forget was silently swallowing errors (and exceptions,
    whose tracebacks go to a stderr the full-screen alt-buffer hides) — making
    a real failure look like the keybinding did nothing at all."""
    def worker():
        try:
            result = fn()
        except Exception as e:
            result = {"error": str(e)}
        msg = None
        if isinstance(result, dict):
            if result.get("error"):
                msg = f"[mcpanel] {label} failed: {result['error']}"
            elif result.get("needsEula"):
                msg = f"[mcpanel] {label} failed: EULA not accepted — run /accept-eula server first"
        if msg:
            with state["lock"]:
                state["lines"].append({"text": msg, "type": "err"})
    _run_bg(worker)


def _make_keybindings(state, server_id):
    from . import servers

    def do_stop(event):
        _run_action(state, "stop", lambda: servers.stop_server(types.SimpleNamespace(id=server_id)))

    def do_start(event):
        _run_action(state, "start", lambda: servers.start_server(
            types.SimpleNamespace(id=server_id, accept_eula=False)))

    def do_toggle(event):
        (do_stop if state["running"] else do_start)(event)

    def do_restart(event):
        _run_action(state, "restart", lambda: servers.restart_server(
            types.SimpleNamespace(id=server_id, accept_eula=False)))

    def do_kill(event):
        _run_action(state, "kill", lambda: servers.kill_server(types.SimpleNamespace(id=server_id)))

    def do_close(event):
        state["stop"] = True
        event.app.exit()

    kb = KeyBindings()
    kb.add("c-c")(do_close)
    kb.add("c-s")(do_toggle)
    kb.add("c-r")(do_restart)
    kb.add("c-k")(do_kill)

    @kb.add("enter")
    def _submit(event):
        text = state["input"].strip()
        state["input"] = ""
        if not text:
            return
        state["history"].append(text)
        state["hist_idx"] = len(state["history"])
        if not state["running"]:
            with state["lock"]:
                state["lines"].append({"text": "Server is not running.", "type": "err"})
            return
        with state["lock"]:
            state["lines"].append({"text": f"> {text}", "type": "cmd"})
        _run_action(state, "command", lambda: servers.send_command(
            types.SimpleNamespace(id=server_id, command=text)))

    @kb.add("backspace")
    def _bksp(event):
        state["input"] = state["input"][:-1]

    @kb.add("up")
    def _hist_up(event):
        if state["history"]:
            state["hist_idx"] = max(0, state["hist_idx"] - 1)
            state["input"] = state["history"][state["hist_idx"]]

    @kb.add("down")
    def _hist_down(event):
        if not state["history"]:
            return
        state["hist_idx"] = min(len(state["history"]), state["hist_idx"] + 1)
        state["input"] = (state["history"][state["hist_idx"]]
                           if state["hist_idx"] < len(state["history"]) else "")

    @kb.add(Keys.Any)
    def _typed(event):
        data = event.data
        if data and data.isprintable():
            state["input"] += data

    return kb


_SCROLL_STEP = 3  # log lines per wheel notch


class _LogWindow(Window):
    """A Window whose mouse wheel scrolls the log buffer (via scroll_offset)
    instead of prompt_toolkit's default vertical_scroll +/- 1, which would
    otherwise fight the tail-follow cursor anchor in _log_frags and snap
    straight back down. Arrow keys are deliberately left alone — those drive
    command history (see _hist_up/_hist_down), not scrolling."""

    def __init__(self, *args, console_state=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._console_state = console_state

    def _mouse_handler(self, mouse_event):
        state = self._console_state
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            with state["lock"]:
                max_offset = max(0, len(state["lines"]) - 1)
                state["scroll_offset"] = min(max_offset, state["scroll_offset"] + _SCROLL_STEP)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            with state["lock"]:
                state["scroll_offset"] = max(0, state["scroll_offset"] - _SCROLL_STEP)
            return None
        return super()._mouse_handler(mouse_event)


_STYLE = Style.from_dict({
    "frame":          "ansibrightblack",
    "title":          "bold",
    "name":           "bold",
    "status-on":      "bold ansigreen",
    "status-off":     "bold ansired",
    "status-pending": "bold ansiyellow",
    "log-info":       "",
    "log-warn":       "ansiyellow",
    "log-error":      "bold ansired",
    "log-debug":      "ansibrightblack",
    "log-dim":        "ansibrightblack",
    "log-cmd":        "bold ansicyan",
    "prompt":         "bold ansicyan",
    "footer-key":     "bold",
    "footer-dim":     "ansibrightblack",
}) if _PT else None


def _build_app(state, server_id, name):
    log_window = _LogWindow(
        FormattedTextControl(lambda: _log_frags(state), focusable=True),
        wrap_lines=True,
        height=Dimension(weight=1),
        console_state=state,
    )
    root = HSplit([
        Window(height=1, char="═", style="class:frame"),
        Window(FormattedTextControl(lambda: _title_frags(state)), height=1, align=WindowAlign.CENTER),
        Window(FormattedTextControl(lambda: _status_frags(state, name)), height=1, align=WindowAlign.CENTER),
        Window(height=1),
        Window(height=1, char="═", style="class:frame"),
        Window(height=1),
        log_window,
        Window(height=1, char="═", style="class:frame"),
        Window(FormattedTextControl(lambda: _input_frags(state)), height=1),
        Window(height=1, char="═", style="class:frame"),
        Window(FormattedTextControl(lambda: _footer_frags(state)), height=1),
    ])
    return Application(
        layout=Layout(root, focused_element=log_window),
        key_bindings=_make_keybindings(state, server_id),
        style=_STYLE,
        full_screen=True,
        mouse_support=True,
        refresh_interval=0.3,
    )


# ── plain fallback (no prompt_toolkit) ────────────────────────────────────────

def _run_plain(server_id):
    path = runstate.log_path(server_id)
    print(render.dim(f"— attaching to {server_id} (Ctrl-C to detach) —"))
    print(render.dim("  (install prompt_toolkit for the interactive console)"))
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
                time.sleep(0.3)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        f.seek(pos)
                        rest = f.read()
                except FileNotFoundError:
                    rest = ""
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


# ── entry point ───────────────────────────────────────────────────────────────

def run_console(server_id):
    cfg = load_config()
    srv = find_server(cfg, server_id)
    if not srv:
        print(render.red(f"✗ Server not found: {server_id}"))
        return

    if not _PT:
        _run_plain(server_id)
        return

    state = {
        "lock": threading.Lock(),
        "lines": [],
        "input": "",
        "history": [],
        "hist_idx": 0,
        "running": runstate.is_running(server_id),
        "ping": {"online": False},
        "scroll_offset": 0,
        "stop": False,
    }
    # Seed with whatever is already in the current log so the pane isn't
    # empty on attach.
    state["lines"] = runstate.read_log(server_id)[-500:]

    t_log = threading.Thread(target=_tail_log, args=(state, server_id), daemon=True)
    t_status = threading.Thread(target=_poll_status, args=(state, server_id, srv.get("port", 25565)), daemon=True)
    t_log.start()
    t_status.start()

    app = _build_app(state, server_id, srv.get("name", server_id))
    try:
        app.run()
    finally:
        state["stop"] = True
    print(render.dim("— console closed —"))
