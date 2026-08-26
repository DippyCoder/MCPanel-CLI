"""Full-screen log-file browser — used by the interactive TUI's `/logs server`
and `/logs mcpanel`.

Same shape as console_ui.py (frame lines, a prompt_toolkit Application, a
plain-text fallback) but browses static files instead of live-tailing one: a
tree pane on the left lists log files, and selecting one loads its content
into a pane on the right, colored with the same _classify() the live console
uses (real server .log lines match the same "[HH:MM:SS INFO]: ..." format
console_ui parses, since it's the same text the server prints to stdout).
"""

import gzip
import json
import os
import threading

from . import paths, render, util
from .console_ui import _classify

try:
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, HSplit, VSplit, Window
    from prompt_toolkit.layout.containers import WindowAlign
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.mouse_events import MouseEventType
    from prompt_toolkit.styles import Style
    _PT = True
except ImportError:
    _PT = False


_MAX_CONTENT_LINES = 5000


# ── tree building ────────────────────────────────────────────────────────────

def _build_dir_tree(dir_path, root_path, depth=0):
    """Same shape as util.build_file_tree, but every dir starts expanded and
    every node gets a `key` (its path relative to root) stable across
    rebuilds — used to carry expand-state and the open file over a refresh."""
    if depth > 12:
        return []
    try:
        entries = list(os.scandir(dir_path))
    except OSError:
        return []
    items = []
    for entry in entries:
        full = os.path.join(dir_path, entry.name)
        rel = os.path.relpath(full, root_path)
        if entry.is_dir():
            items.append({
                "name": entry.name, "type": "dir", "key": rel, "expanded": True,
                "children": _build_dir_tree(full, root_path, depth + 1),
            })
        else:
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            items.append({"name": entry.name, "type": "file", "key": rel,
                           "path": full, "size": size})
    items.sort(key=lambda it: (0 if it["type"] == "dir" else 1, it["name"].lower()))
    return items


def _flatten(nodes, depth=0, out=None):
    if out is None:
        out = []
    for node in nodes:
        out.append((node, depth))
        if node["type"] == "dir" and node.get("expanded"):
            _flatten(node.get("children", []), depth + 1, out)
    return out


def _merge_expanded(new_nodes, old_nodes):
    """Copy expand-state from an old tree into a freshly rebuilt one, keyed
    by each node's stable relative-path `key`."""
    old_by_key = {n["key"]: n for n in old_nodes}
    for node in new_nodes:
        old = old_by_key.get(node["key"])
        if old and node["type"] == "dir":
            node["expanded"] = old.get("expanded", True)
            _merge_expanded(node.get("children", []), old.get("children", []))


# ── file content loading ─────────────────────────────────────────────────────

def _load_file_lines(path):
    """Read a log file into classified (style, text) fragment-lines. Handles
    gzip'd rotated logs and mcpanel's .jsonl records transparently; plain
    .log text gets the same [HH:MM:SS INFO] coloring as the live console,
    since it's the exact format a server prints to stdout."""
    try:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
            raw_lines = f.read().splitlines()
    except OSError as e:
        return [[("class:log-error", f"Could not read file: {e}")]], False

    truncated = len(raw_lines) > _MAX_CONTENT_LINES
    if truncated:
        raw_lines = raw_lines[-_MAX_CONTENT_LINES:]

    is_jsonl = path.endswith(".jsonl")
    out = []
    for line in raw_lines:
        if not line.strip():
            out.append([("", "")])
            continue
        rec = None
        if is_jsonl:
            try:
                rec = json.loads(line)
            except Exception:
                rec = None
        if rec is None:
            rec = {"text": line, "type": "out"}
        # Older captured sessions can carry embedded \r left over from
        # before supervisor.py started stripping it from the raw process
        # output — drop it here too so archived logs don't render ^M.
        rec["text"] = rec.get("text", "").replace("\r", "")
        out.append(_classify(rec))
    return out, truncated


# ── state actions ────────────────────────────────────────────────────────────

def _open_file(state, node):
    lines, truncated = _load_file_lines(node["path"])
    with state["lock"]:
        state["content_lines"] = lines
        state["content_offset"] = 0
        state["content_title"] = node["key"]
        state["content_truncated"] = truncated
        state["open_key"] = node["key"]


def _activate_cursor(state):
    with state["lock"]:
        if not state["flat"]:
            return
        node, _depth = state["flat"][state["cursor"]]
    if node["type"] == "dir":
        node["expanded"] = not node.get("expanded")
        with state["lock"]:
            state["flat"] = _flatten(state["tree"])
            state["cursor"] = min(state["cursor"], len(state["flat"]) - 1)
        return
    _open_file(state, node)


def _refresh_tree(state):
    new_tree = state["builder"]()
    with state["lock"]:
        _merge_expanded(new_tree, state["tree"])
        state["tree"] = new_tree
        state["flat"] = _flatten(new_tree)
        state["cursor"] = min(state["cursor"], max(0, len(state["flat"]) - 1))


# ── fragment builders ────────────────────────────────────────────────────────

def _header_frags(state):
    return [("class:title", state["title"])]


def _tree_frags(state):
    with state["lock"]:
        flat = list(state["flat"])
        cursor = state["cursor"]
        open_key = state["open_key"]
    if not flat:
        frags = [("[SetCursorPosition]", ""), ("class:log-dim", "  (no log files found)")]
        return frags
    frags = []
    for i, (node, depth) in enumerate(flat):
        if frags:
            frags.append(("", "\n"))
        indent = "  " * depth
        cursor_here = i == cursor
        if cursor_here:
            frags.append(("[SetCursorPosition]", ""))
        if node["type"] == "dir":
            glyph = "▾ " if node.get("expanded") else "▸ "
            style = "class:tree-cursor" if cursor_here else "class:tree-dir"
            frags.append((style, f"{indent}{glyph}{node['name']}/"))
        else:
            base_style = "class:tree-cursor" if cursor_here else (
                "class:tree-open" if node["key"] == open_key else "class:tree-file")
            frags.append((base_style, f"{indent}  {node['name']}"))
            frags.append((base_style if cursor_here else "class:log-dim",
                           f"  ({util.human_size(node.get('size', 0))})"))
    return frags


def _content_header_frags(state):
    with state["lock"]:
        title = state["content_title"]
        truncated = state["content_truncated"]
    if not title:
        return [("class:log-dim", "(no file open)")]
    frags = [("class:tree-open", title)]
    if truncated:
        frags.append(("class:log-warn", f"  (showing last {_MAX_CONTENT_LINES} lines)"))
    return frags


def _content_frags(state):
    with state["lock"]:
        lines = list(state["content_lines"])
        offset = state["content_offset"]
    if not lines:
        return [("class:log-dim", "  Select a file from the tree (Enter) to view it.")]
    offset = max(0, min(offset, len(lines) - 1))
    frags = []
    for i, line_frags in enumerate(lines):
        if frags:
            frags.append(("", "\n"))
        if i == offset:
            frags.append(("[SetCursorPosition]", ""))
        frags.extend(line_frags)
    return frags


def _footer_frags(state):
    hints = (("↑↓", "move"), ("Enter", "open/expand"), ("PgUp/PgDn", "scroll"),
             ("r", "refresh"), ("^C", "close"))
    out = []
    for key, label in hints:
        if out:
            out.append(("", "        "))
        out.append(("class:footer-key", key))
        out.append(("class:footer-dim", f"={label}"))
    return out


# ── key bindings / mouse ─────────────────────────────────────────────────────

_TREE_SCROLL_STEP = 1
_CONTENT_SCROLL_STEP = 3
_CONTENT_PAGE_STEP = 10


def _make_keybindings(state):
    def do_close(event):
        state["stop"] = True
        event.app.exit()

    kb = KeyBindings()
    kb.add("c-c")(do_close)

    @kb.add("up")
    def _up(event):
        with state["lock"]:
            state["cursor"] = max(0, state["cursor"] - 1)

    @kb.add("down")
    def _down(event):
        with state["lock"]:
            state["cursor"] = min(len(state["flat"]) - 1, state["cursor"] + 1)

    @kb.add("enter")
    def _enter(event):
        _activate_cursor(state)

    @kb.add("r")
    def _refresh(event):
        _refresh_tree(state)

    @kb.add("pageup")
    def _pgup(event):
        with state["lock"]:
            state["content_offset"] = max(0, state["content_offset"] - _CONTENT_PAGE_STEP)

    @kb.add("pagedown")
    def _pgdn(event):
        with state["lock"]:
            top = max(0, len(state["content_lines"]) - 1)
            state["content_offset"] = min(top, state["content_offset"] + _CONTENT_PAGE_STEP)

    return kb


class _TreeWindow(Window):
    def __init__(self, *args, browser_state=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = browser_state

    def _mouse_handler(self, mouse_event):
        state = self._state
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            with state["lock"]:
                state["cursor"] = max(0, state["cursor"] - _TREE_SCROLL_STEP)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            with state["lock"]:
                state["cursor"] = min(len(state["flat"]) - 1, state["cursor"] + _TREE_SCROLL_STEP)
            return None
        return super()._mouse_handler(mouse_event)


class _ContentWindow(Window):
    def __init__(self, *args, browser_state=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = browser_state

    def _mouse_handler(self, mouse_event):
        state = self._state
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            with state["lock"]:
                state["content_offset"] = max(0, state["content_offset"] - _CONTENT_SCROLL_STEP)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            with state["lock"]:
                top = max(0, len(state["content_lines"]) - 1)
                state["content_offset"] = min(top, state["content_offset"] + _CONTENT_SCROLL_STEP)
            return None
        return super()._mouse_handler(mouse_event)


_STYLE = Style.from_dict({
    "frame":       "ansibrightblack",
    "title":       "bold",
    "log-info":    "",
    "log-warn":    "ansiyellow",
    "log-error":   "bold ansired",
    "log-debug":   "ansibrightblack",
    "log-dim":     "ansibrightblack",
    "log-cmd":     "bold ansicyan",
    "tree-dir":    "bold ansiblue",
    "tree-file":   "",
    "tree-open":   "bold ansicyan",
    "tree-cursor": "reverse",
    "footer-key":  "bold",
    "footer-dim":  "ansibrightblack",
}) if _PT else None


def _build_app(state):
    tree_window = _TreeWindow(
        FormattedTextControl(lambda: _tree_frags(state), focusable=True),
        wrap_lines=False,
        width=Dimension(min=24, max=52, preferred=34),
        browser_state=state,
    )
    content_window = _ContentWindow(
        FormattedTextControl(lambda: _content_frags(state), focusable=True),
        wrap_lines=True,
        width=Dimension(weight=1),
        browser_state=state,
    )
    root = HSplit([
        Window(height=1, char="═", style="class:frame"),
        Window(FormattedTextControl(lambda: _header_frags(state)), height=1, align=WindowAlign.CENTER),
        Window(height=1, char="═", style="class:frame"),
        VSplit([
            tree_window,
            Window(width=1, char="│", style="class:frame"),
            HSplit([
                Window(FormattedTextControl(lambda: _content_header_frags(state)), height=1),
                Window(height=1, char="─", style="class:frame"),
                content_window,
            ]),
        ]),
        Window(height=1, char="═", style="class:frame"),
        Window(FormattedTextControl(lambda: _footer_frags(state)), height=1),
    ])
    return Application(
        layout=Layout(root, focused_element=tree_window),
        key_bindings=_make_keybindings(state),
        style=_STYLE,
        full_screen=True,
        mouse_support=True,
        refresh_interval=0.5,
    )


def _new_state(title, builder):
    tree = builder()
    return {
        "lock": threading.Lock(),
        "title": title,
        "builder": builder,
        "tree": tree,
        "flat": _flatten(tree),
        "cursor": 0,
        "open_key": None,
        "content_lines": [],
        "content_offset": 0,
        "content_title": "",
        "content_truncated": False,
        "stop": False,
    }


# ── plain fallback (no prompt_toolkit) ────────────────────────────────────────

def _print_tree_plain(nodes, indent=0):
    for node in nodes:
        pad = "  " * indent
        if node["type"] == "dir":
            print(render.cyan(pad + node["name"] + "/"))
            _print_tree_plain(node.get("children", []), indent + 1)
        else:
            size = util.human_size(node.get("size", 0))
            print(pad + node["name"] + render.dim(f"  ({size})"))


def _run_plain(title, builder):
    print(render.dim(f"— {title} —"))
    print(render.dim("  (install prompt_toolkit for the interactive log browser)\n"))
    tree = builder()
    if not tree:
        print(render.dim("  (no log files found)"))
    else:
        _print_tree_plain(tree)


# ── entry points ──────────────────────────────────────────────────────────────

def _run(title, builder):
    if not _PT:
        _run_plain(title, builder)
        return
    state = _new_state(title, builder)
    app = _build_app(state)
    try:
        app.run()
    finally:
        state["stop"] = True
    print(render.dim("— log browser closed —"))


def run_server_log_browser(server_id, server_dir, server_name=None):
    logs_dir = os.path.join(server_dir, "logs")
    title = f"Logs — {server_name or server_id}"
    _run(title, lambda: _build_dir_tree(logs_dir, logs_dir))


def run_mcpanel_log_browser():
    logs_dir = paths.LOGS_DIR
    _run("mcpanel system logs", lambda: _build_dir_tree(logs_dir, logs_dir))
