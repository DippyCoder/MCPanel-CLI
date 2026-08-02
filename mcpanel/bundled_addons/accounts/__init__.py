"""User accounts, roles and permissions for MCPanel.

MCPanel-WebUI is reachable from any device on the network, so something has to
decide who may do what. That decision belongs in the CLI rather than in the web
server: the CLI *is* the backend, so putting authentication here means the
desktop app, the WebUI and a plain terminal all share one account database and
one permission model instead of three.

Everything this addon owns lives in SQLite under the addon's own data
directory — see ``db.py``. The permission catalogue, the built-in roles and the
install-wide switches live in ``permissions.py``, which the WebUI reads over
``mcpanel api accounts perms`` so there is only ever one definition of them.

Command surface::

    mcpanel accounts init                       create the database + default admin
    mcpanel accounts login    -u <u> -p <pw>    open a session, print a token
    mcpanel accounts verify   -t <token>        is this token still good?
    mcpanel accounts whoami   -t <token>
    mcpanel accounts logout   -t <token>
    mcpanel accounts list
    mcpanel accounts create   -u <u> -p <pw> [-r <role>] [--perms a,b]
    mcpanel accounts update   -u <u> [-r <role>] [--perms a,b] [--enable|--disable]
    mcpanel accounts delete   -u <u>
    mcpanel accounts passwd   -u <u> -p <new> [--current <old>]
    mcpanel accounts perms
    mcpanel accounts roles    list|create|update|delete
    mcpanel accounts settings [--set key=value]
    mcpanel accounts sessions list|revoke

Prefix any of them with ``api`` for raw JSON, which is what the WebUI calls.
"""

from . import commands, db

ADDON = {
    "name": "accounts",
    "version": "1.0.0",
    "description": "User accounts, roles and permissions (SQLite-backed)",
    "author": "DippyCoder",
    "url": "https://github.com/DippyCoder/MCPanel-CLI",
    "api_version": 1,
}


def on_load():
    """Create the database and seed the default admin on first use.

    Doing this at load time rather than lazily means ``mcpanel accounts login``
    works immediately after installing, with no separate setup step — and the
    WebUI can assume an account database exists the moment the CLI reports the
    addon as enabled.

    A failure here is re-raised with context so the loader records a legible
    reason in ``mcpanel addons list`` instead of a bare sqlite3 error.
    """
    try:
        db.init_db()
    except Exception as exc:
        raise RuntimeError(
            "accounts: could not open the account database at {} — {}".format(
                _safe_db_path(), exc
            )
        )


def _safe_db_path():
    try:
        return db.db_path()
    except Exception:
        return "<unknown>"


# ─── command tree ────────────────────────────────────────────────────────────

def register(api):
    grp = api.group("accounts", help="user accounts, roles and permissions")

    # ── setup ────────────────────────────────────────────────────────────────
    p = api.command(grp, "init", commands.cmd_init, "accounts-init",
                    help="create the account database and the default admin")
    p.add_argument("--force", dest="force", action="store_true",
                   help="re-seed built-in roles and settings over an existing database")

    # ── authentication ───────────────────────────────────────────────────────
    p = api.command(grp, "login", commands.cmd_login, "accounts-login",
                    help="authenticate and open a session")
    _f_user(p, required=True)
    _f_password(p, required=True, help="the account's password")
    p.add_argument("--ttl", dest="ttl_hours", type=float, default=None, metavar="<hours>",
                   help="session lifetime, overriding the session_ttl_hours setting")
    p.add_argument("--ua", dest="user_agent", default=None, metavar="<string>",
                   help="user agent to record against the session")
    p.add_argument("--ip", dest="ip", default=None, metavar="<address>",
                   help="client address to record against the session")

    p = api.command(grp, "verify", commands.cmd_verify, "accounts-verify",
                    help="check whether a session token is still valid")
    _f_token(p)

    p = api.command(grp, "whoami", commands.cmd_whoami, "accounts-whoami",
                    help="show the account a session token belongs to")
    _f_token(p)

    p = api.command(grp, "logout", commands.cmd_logout, "accounts-logout",
                    help="revoke a session token")
    _f_token(p)

    # ── accounts ─────────────────────────────────────────────────────────────
    api.command(grp, "list", commands.cmd_list, "accounts-list",
                help="list all accounts")

    p = api.command(grp, "create", commands.cmd_create, "accounts-create",
                    help="create an account")
    _f_user(p, required=True)
    _f_password(p, required=True, help="initial password")
    _f_role(p)
    _f_perms(p, help="extra permissions on top of the role, comma-separated")
    p.add_argument("--disabled", dest="disabled", action="store_true",
                   help="create the account disabled")
    p.add_argument("--must-change-password", dest="must_change_password", action="store_true",
                   help="flag the account to change its password on next sign-in")

    p = api.command(grp, "update", commands.cmd_update, "accounts-update",
                    help="change an account's role, permissions or status")
    _f_user(p, required=True)
    _f_role(p)
    _f_perms(p, help="replace the account's extra permissions (empty string clears them)")
    p.add_argument("--clear-role", dest="clear_role", action="store_true",
                   help="remove the account's role, leaving only its own permissions")
    p.add_argument("--enable", dest="enable", action="store_true", help="enable the account")
    p.add_argument("--disable", dest="disable", action="store_true", help="disable the account")

    p = api.command(grp, "delete", commands.cmd_delete, "accounts-delete",
                    help="delete an account")
    _f_user(p, required=True)

    p = api.command(grp, "passwd", commands.cmd_passwd, "accounts-passwd",
                    help="set an account's password")
    _f_user(p, required=True)
    _f_password(p, required=True, help="the new password")
    p.add_argument("--current", dest="current", default=None, metavar="<password>",
                   help="the account's current password — supply it for a self-service change")
    p.add_argument("--keep-token", dest="keep_token", default=None, metavar="<token>",
                   help="spare this session from the mass revoke, so the caller "
                        "changing their own password stays signed in")

    # ── permissions ──────────────────────────────────────────────────────────
    api.command(grp, "perms", commands.cmd_perms, "accounts-perms",
                help="list every permission MCPanel understands")

    # ── roles ────────────────────────────────────────────────────────────────
    roles = api.group("roles", parent=grp, help="manage roles")

    api.command(roles, "list", commands.cmd_roles_list, "accounts-roles-list",
                help="list roles and their permissions")

    p = api.command(roles, "create", commands.cmd_roles_create, "accounts-roles-create",
                    help="create a role")
    _f_name(p)
    _f_desc(p)
    _f_perms(p, help="permissions granted by the role, comma-separated")

    p = api.command(roles, "update", commands.cmd_roles_update, "accounts-roles-update",
                    help="change a role's description or permissions")
    _f_name(p)
    _f_desc(p)
    _f_perms(p, help="replace the role's permissions (empty string clears them)")

    p = api.command(roles, "delete", commands.cmd_roles_delete, "accounts-roles-delete",
                    help="delete a role")
    _f_name(p)

    # ── global settings ──────────────────────────────────────────────────────
    p = api.command(grp, "settings", commands.cmd_settings, "accounts-settings",
                    help="read or change install-wide account settings")
    p.add_argument("--set", dest="assignments", action="append", default=None,
                   metavar="<key=value>",
                   help="set a value, e.g. --set allow_self_password_change=false "
                        "(repeatable)")

    # ── sessions ─────────────────────────────────────────────────────────────
    sessions = api.group("sessions", parent=grp, help="inspect and revoke sessions")

    p = api.command(sessions, "list", commands.cmd_sessions_list, "accounts-sessions-list",
                    help="list active sessions")
    _f_user(p, required=False, help="only this account's sessions")

    p = api.command(sessions, "revoke", commands.cmd_sessions_revoke, "accounts-sessions-revoke",
                    help="revoke one session, one account's sessions, or all of them")
    _f_token(p, required=False)
    _f_user(p, required=False, help="revoke every session belonging to this account")
    p.add_argument("--all", dest="all", action="store_true",
                   help="revoke every session on the install")

    # ── human output ─────────────────────────────────────────────────────────
    for action, fn in commands.make_renderers(api.helpers.render).items():
        api.renderer(action, fn)


# ─── shared argument shapes ──────────────────────────────────────────────────
# Mirrors cli.py's f_id()/f_name() convention so addon flags read the same as
# the built-in ones.

def _f_user(p, required=True, help="account username"):
    p.add_argument("-u", "--user", dest="username", required=required,
                   default=None, metavar="<username>", help=help)


def _f_password(p, required=True, help="password"):
    # `required` is deliberately not passed through to argparse: a secret may
    # arrive on stdin instead, and argparse cannot express "one of these two".
    # commands._resolve_secrets() enforces it after parsing, which also lets us
    # give a better message than argparse's.
    p.add_argument("-p", "--password", dest="password",
                   default=None, metavar="<password>", help=help)
    p.set_defaults(_password_required=required)
    # Anything on a process command line is world-readable via `ps` for as long
    # as the process lives. That is fine for a human typing into their own
    # terminal, but MCPanel-WebUI calls these commands on every sign-in, so on a
    # shared host every login would briefly expose the password to other users.
    # With this flag the secret travels over a pipe instead and never appears in
    # the process table.
    p.add_argument("--password-stdin", dest="password_stdin", action="store_true",
                   help="read the secret from stdin instead of the command line: "
                        "either one bare line, or a JSON object with \"password\" "
                        "and optionally \"current\" keys")


def _f_token(p, required=True):
    p.add_argument("-t", "--token", dest="token", required=required,
                   default=None, metavar="<token>", help="session token")


def _f_role(p):
    p.add_argument("-r", "--role", dest="role", default=None, metavar="<role>",
                   help="role to assign")


def _f_perms(p, help="permissions, comma-separated"):
    p.add_argument("--perms", dest="perms", default=None, metavar="<a,b,c>", help=help)


def _f_name(p):
    p.add_argument("-n", "--name", dest="name", required=True, metavar="<role>",
                   help="role name")


def _f_desc(p):
    p.add_argument("--desc", dest="description", default=None, metavar="<text>",
                   help="human-readable description")
