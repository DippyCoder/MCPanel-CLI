"""The permission catalogue — single source of truth for the whole stack.

MCPanel-WebUI reads this list over `mcpanel api accounts perms` rather than
keeping its own copy, so there is exactly one place a permission is defined.
The WebUI maps *its own RPC command names* onto these strings; that mapping is
WebUI-side, but every string it names must exist here.

Permission strings are `<area>.<action>`. Two wildcards are understood when
matching:

    "*"          → every permission
    "servers.*"  → every permission in the `servers` area

Wildcards are only meaningful in a grant (a role's or user's permission list);
a permission *check* always names a concrete string.
"""

# (name, description) — order is the display order in the WebUI.
CATALOG = [
    # ── Account administration ───────────────────────────────────────────────
    ("accounts.view",     "See the list of user accounts"),
    ("accounts.manage",   "Create, edit, disable and delete accounts and roles"),
    ("self.password",     "Change your own password"),

    # ── Servers ──────────────────────────────────────────────────────────────
    ("servers.view",      "See servers and their status"),
    ("servers.create",    "Create and import servers"),
    ("servers.duplicate", "Duplicate an existing server"),
    ("servers.edit",      "Change a server's settings (RAM, port, Java, flags)"),
    ("servers.delete",    "Delete servers"),
    ("servers.start",     "Start servers"),
    ("servers.stop",      "Stop, restart and kill servers"),
    ("servers.console",   "Read live console output"),
    ("servers.command",   "Send commands to a server console"),

    # ── Files ────────────────────────────────────────────────────────────────
    ("files.read",        "Browse and read server files"),
    ("files.write",       "Edit and create server files"),
    ("files.delete",      "Delete and rename server files"),
    ("files.upload",      "Upload files into a server"),
    ("files.download",    "Export files out of a server"),

    # ── Profiles ─────────────────────────────────────────────────────────────
    ("profiles.view",     "See server profiles"),
    ("profiles.manage",   "Create, edit and delete profiles"),

    # ── Plugins / mods ───────────────────────────────────────────────────────
    ("plugins.view",      "Search the plugin and mod catalogues"),
    ("plugins.install",   "Install plugins and mods"),

    # ── Backups ──────────────────────────────────────────────────────────────
    ("backups.view",      "See a server's backups"),
    ("backups.create",    "Create backups"),
    ("backups.restore",   "Restore a server from a backup"),
    ("backups.delete",    "Delete backups"),

    # ── Schedules ────────────────────────────────────────────────────────────
    ("schedules.view",    "See scheduled tasks"),
    ("schedules.manage",  "Create, edit, delete and run scheduled tasks"),

    # ── Proxy ────────────────────────────────────────────────────────────────
    ("proxy.manage",      "Link servers to a Velocity proxy and read its secret"),

    # ── Themes ───────────────────────────────────────────────────────────────
    ("themes.view",       "Use installed themes"),
    ("themes.manage",     "Install and delete themes"),

    # ── App ──────────────────────────────────────────────────────────────────
    ("system.view",       "See system stats and app/CLI versions"),
    ("settings.view",     "See application settings"),
    ("settings.manage",   "Change application settings"),

    # ── Powerful ─────────────────────────────────────────────────────────────
    # Both of these are effectively root on the host: a shell, or an arbitrary
    # CLI invocation. They are deliberately excluded from every non-admin
    # builtin role.
    ("terminal.access",   "Open the embedded shell on the host machine"),
    ("cli.raw",           "Run arbitrary MCPanel-CLI commands"),
]

ALL = [name for name, _ in CATALOG]
DESCRIPTIONS = dict(CATALOG)

# Areas, derived — used to validate `area.*` wildcards.
AREAS = sorted({name.split(".", 1)[0] for name in ALL})


# ─── Builtin roles ───────────────────────────────────────────────────────────
# `admin` is special-cased everywhere: it always holds "*" and cannot be
# deleted, renamed or stripped of permissions, so an install can never lock
# itself out of its own account management.

BUILTIN_ROLES = {
    "admin": {
        "description": "Full control over everything, including accounts.",
        "permissions": ["*"],
    },
    "operator": {
        "description": "Run and maintain servers, but not create or delete them.",
        "permissions": [
            "self.password",
            "servers.view", "servers.start", "servers.stop",
            "servers.console", "servers.command",
            "files.read", "files.write", "files.upload", "files.download",
            "profiles.view",
            "plugins.view", "plugins.install",
            "backups.view", "backups.create",
            "schedules.view",
            "themes.view",
            "system.view", "settings.view",
        ],
    },
    "viewer": {
        "description": "Read-only: watch consoles and browse files, change nothing.",
        "permissions": [
            "self.password",
            "servers.view", "servers.console",
            "files.read", "files.download",
            "profiles.view",
            "plugins.view",
            "backups.view",
            "schedules.view",
            "themes.view",
            "system.view", "settings.view",
        ],
    },
}


# ─── Global settings ─────────────────────────────────────────────────────────
# "Global permissions" in the UI: install-wide switches that apply on top of a
# user's own grants. A user needs BOTH the permission and (where one exists)
# the global switch — so an admin can revoke a capability fleet-wide without
# editing every account.

SETTINGS_DEFAULTS = {
    # When false, only someone with accounts.manage can change any password,
    # including a user's own. Gates the `self.password` permission.
    "allow_self_password_change": True,
    # How long a login stays valid, in hours. 0 means "until the CLI restarts"
    # is NOT supported — sessions live in SQLite, so 0 is rejected.
    "session_ttl_hours": 720,
    # Minimum length enforced on every password change. The seeded admin
    # password deliberately predates this and is not retroactively rejected.
    "min_password_length": 4,
    # When true, a disabled account's existing sessions are revoked
    # immediately rather than being left to expire.
    "revoke_sessions_on_disable": True,
}


# ─── Matching ────────────────────────────────────────────────────────────────

def granted(held, permission):
    """True when `held` (a list of grants, possibly with wildcards) covers
    `permission` (one concrete string)."""
    if not held:
        return False
    if "*" in held:
        return True
    if permission in held:
        return True
    area = permission.split(".", 1)[0]
    return f"{area}.*" in held


def expand(held):
    """Resolves a grant list containing wildcards into concrete permissions."""
    if not held:
        return []
    if "*" in held:
        return list(ALL)
    out = []
    for name in ALL:
        if granted(held, name):
            out.append(name)
    return out


def validate(held):
    """Splits a grant list into (valid, unknown). Wildcards for real areas and
    the global "*" count as valid."""
    valid, unknown = [], []
    for item in held or []:
        s = str(item).strip()
        if not s:
            continue
        if s == "*" or s in ALL:
            valid.append(s)
        elif s.endswith(".*") and s[:-2] in AREAS:
            valid.append(s)
        else:
            unknown.append(s)
    return valid, unknown
