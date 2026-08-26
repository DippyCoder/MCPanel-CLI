"""mcpanel's own application log.

This is the *same* logs/latest.log the MCPanel desktop (Electron) app already
writes to, under the userData directory this CLI shares with it (see
paths.py). The CLI/TUI appends its own entries in the same
"[YYYY-MM-DD HH:MM:SS] [LEVEL] message" format so `/logs mcpanel` shows one
unified timeline — what the app did, what the CLI did, in the order it
happened — instead of a second, CLI-only log file sitting next to the real
one.

Rotation is left entirely to the desktop app (it renames latest.log to a
timestamped archive on its own startup, as seen in an existing userData's
logs/ folder) — the CLI only ever appends to whatever latest.log currently
exists.
"""

import datetime
import os
import threading
import traceback

from . import paths

LOG_FILE = os.path.join(paths.LOGS_DIR, "latest.log")

_lock = threading.Lock()


def _write(level, msg):
    line = f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {msg}\n"
    try:
        os.makedirs(paths.LOGS_DIR, exist_ok=True)
        with _lock, open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def info(msg):
    _write("INFO", msg)


def warning(msg):
    _write("WARN", msg)


def error(msg):
    _write("ERROR", msg)


def exception(msg):
    """Log `msg` plus the current exception's traceback. Call only from an
    `except` block."""
    _write("ERROR", f"{msg}\n{traceback.format_exc()}")
