"""Data-directory layout.

Shares the *same* directory the MCPanel desktop app uses, so servers, profiles
and themes are interchangeable between the CLI and the Electron app. Electron's
``app.getPath('userData')`` on Linux resolves to ``$XDG_CONFIG_HOME/<name>``
(falling back to ``~/.config/<name>``), where ``<name>`` is the package name
``mcpanel``:

    ~/.config/mcpanel/
    ├── config.json          ← servers list + jdkPaths + activeTheme (shared)
    ├── servers/<id>/         ← each server's working directory (shared)
    ├── profiles/<id>/        ← server presets (shared)
    ├── themes/<id>/          ← installed themes (shared)
    └── run/                  ← runtime state for running servers (CLI-only;
                                ignored by the Electron app)

Override the root with the MCPANEL_HOME environment variable.
"""

import os

# Must match the MCPanel Electron app's name (package.json "name") so both
# read/write the same userData directory.
APP_NAME = "mcpanel"


def _default_home():
    override = os.environ.get("MCPANEL_HOME")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    # Electron on Linux: appData = $XDG_CONFIG_HOME or ~/.config, then productName
    appdata = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(appdata, APP_NAME)


USER_DATA = _default_home()
SERVERS_DIR = os.path.join(USER_DATA, "servers")
PROFILES_DIR = os.path.join(USER_DATA, "profiles")
THEMES_DIR = os.path.join(USER_DATA, "themes")
RUN_DIR = os.path.join(USER_DATA, "run")
CONFIG_FILE = os.path.join(USER_DATA, "config.json")


def ensure_dirs():
    for d in (SERVERS_DIR, PROFILES_DIR, THEMES_DIR, RUN_DIR):
        os.makedirs(d, exist_ok=True)
