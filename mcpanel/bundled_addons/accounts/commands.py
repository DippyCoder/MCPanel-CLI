"""Command handlers and human renderers for the accounts addon.

Split from ``__init__.py`` so that module stays what an addon author reads
first: metadata and wiring, nothing else.

Every handler returns a plain JSON-serialisable dict. Failures either raise
``db.AccountsError`` or return ``{"error": ...}`` — the CLI turns both into a
non-zero exit and prints them the same way built-in commands do.

Two rules run through the whole file:

  * a password never appears in a return value, a log line or a rendered row
  * a session token is only ever printed in full at the moment it is issued;
    everywhere else it is truncated, because the stored form is a hash and the
    raw token is a bearer credential
"""

import datetime
import json
import sys

from . import db, permissions

# Deliberately identical for "no such user", "wrong password" and "account
# disabled" so the login command cannot be used to enumerate account names.
# db.verify_password() enforces the same indistinguishability underneath,
# including spending the same CPU on an unknown username as on a real check,
# so there is no separate timing defence to apply here.
INVALID_CREDENTIALS = "Invalid username or password"


# ─── secrets off the command line ────────────────────────────────────────────

def _resolve_secrets(args):
    """Fills in ``args.password`` (and ``args.current``) from stdin when
    ``--password-stdin`` was given, and enforces that a password arrived by one
    route or the other.

    Command-line arguments are visible in ``ps`` to every other user on the
    host for as long as the process lives. MCPanel-WebUI shells out to
    ``accounts login`` on every sign-in, so without a pipe each login on a
    shared machine would briefly expose its password. Accepts either one bare
    line or a JSON object, so ``passwd`` — which needs two secrets at once —
    still works over a single pipe.
    """
    if getattr(args, "password_stdin", False):
        raw = sys.stdin.read()
        # Strip only the trailing newline a shell or here-doc adds; a password
        # may legitimately contain leading or trailing spaces.
        text = raw[:-1] if raw.endswith("\n") else raw
        payload = None
        if text.strip().startswith("{"):
            try:
                candidate = json.loads(text.strip())
                if isinstance(candidate, dict):
                    payload = candidate
            except ValueError:
                payload = None  # a password that merely starts with "{"
        if payload is None:
            args.password = text
        else:
            if payload.get("password") is not None:
                args.password = payload["password"]
            if payload.get("current") is not None:
                args.current = payload["current"]

    if getattr(args, "_password_required", False) and getattr(args, "password", None) is None:
        raise ValueError("a password is required: pass -p <password> or --password-stdin")
    return args


# ─── small helpers ───────────────────────────────────────────────────────────

def _get(row, *names, **kw):
    """First present key out of `names`. db.py is a sibling module written to a
    shared spec; accepting both camelCase and snake_case spellings keeps this
    file from breaking on a naming difference in a row it only displays."""
    default = kw.get("default")
    if not isinstance(row, dict):
        return default
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return default


def _ts(value):
    """Formats an epoch timestamp. Accepts seconds or milliseconds — anything
    past ~1973 in ms is far beyond a plausible seconds value, so the magnitude
    is a safe discriminator."""
    if not value:
        return ""
    try:
        n = float(value)
        if n > 1e11:
            n = n / 1000.0
        return datetime.datetime.fromtimestamp(n).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def _csv(value):
    """`--perms a,b,c` → ["a","b","c"]. An explicit empty string means "clear",
    which is why this is only called when the flag was actually supplied."""
    if value is None:
        return None
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _check_perms(items):
    """Rejects unknown permission strings up front rather than silently storing
    a typo that would then never match anything."""
    if items is None:
        return None
    valid, unknown = permissions.validate(items)
    if unknown:
        raise db.AccountsError(
            "Unknown permission(s): " + ", ".join(unknown)
            + "  — run 'mcpanel accounts perms' for the full list"
        )
    return valid


def _short(token, width=8):
    if not token:
        return ""
    text = str(token)
    return text[:width] + "…" if len(text) > width else text


def _perm_summary(obj):
    """Permission count for a user or role row.

    Reads `effectivePermissions` rather than the raw grant list: an admin's
    power comes from their role, so their own extra-permissions list is empty
    and reporting that would read as "none" for the most privileged account on
    the install.
    """
    if not isinstance(obj, dict):
        return "none"
    effective = obj.get("effectivePermissions")
    if effective is None:
        effective = permissions.expand(obj.get("permissions") or [])
    if not effective:
        return "none"
    total = len(permissions.ALL)
    if len(effective) >= total:
        return "all ({})".format(total)
    return str(len(effective))


def _pad(text, width):
    """Pads to `width` counting only visible characters, for columns whose
    value is colourised before printing."""
    return " " * max(0, width - len(text))


def _err(result, render):
    if isinstance(result, dict) and result.get("error"):
        print(render.red("✗ ") + str(result["error"]))
        return True
    return False


# ─── setup ───────────────────────────────────────────────────────────────────

def cmd_init(args, progress=None):
    info = db.init_db(force=bool(getattr(args, "force", False)))
    users = db.list_users()

    created = bool(info.get("created"))
    credentials = info.get("defaultCredentials")

    # The addon seeds on load, so by the time an explicit `init` runs the
    # database usually already exists and db reports created=False. Without the
    # check below, the one warning that matters — "you are still on admin/admin"
    # — would never be printed on a fresh install.
    #
    # `mustChangePassword` is a reliable proxy for "never changed": seeding is
    # the only thing that sets it on the default admin, and set_password is the
    # only thing that clears it.
    if not credentials:
        for user in users:
            if (user.get("username") == db.DEFAULT_ADMIN_USERNAME
                    and user.get("mustChangePassword")):
                credentials = {
                    "username": db.DEFAULT_ADMIN_USERNAME,
                    "password": db.DEFAULT_ADMIN_PASSWORD,
                }
                break

    return {
        "success": True,
        "created": created,
        "defaultCredentials": credentials,
        "defaultPasswordInUse": credentials is not None,
        "database": info.get("path") or db.db_path(),
        "users": users,
    }


# ─── sessions / authentication ───────────────────────────────────────────────

def cmd_login(args, progress=None):
    _resolve_secrets(args)
    username = getattr(args, "username", None)

    # One gate for everything: verify_password() returns the public user dict
    # on success and None for a wrong password, an unknown username OR a
    # disabled account, all indistinguishably.
    if db.verify_password(username, getattr(args, "password", None)) is None:
        return {"error": INVALID_CREDENTIALS}

    session = db.create_session(
        username,
        ttl_hours=getattr(args, "ttl_hours", None),
        user_agent=getattr(args, "user_agent", None),
        ip=getattr(args, "ip", None),
    )

    return {
        "success": True,
        "token": session["token"],
        "expiresAt": session["expiresAt"],
        "user": session["user"],
    }


def cmd_verify(args, progress=None):
    session = db.verify_session(getattr(args, "token", None))
    if not session:
        return {"valid": False, "user": None}
    return {"valid": True, "user": session["user"], "expiresAt": session.get("expiresAt")}


def cmd_whoami(args, progress=None):
    session = db.verify_session(getattr(args, "token", None))
    if not session:
        return {"error": "Not signed in, or the session has expired"}
    return {"user": session["user"], "expiresAt": session.get("expiresAt")}


def cmd_logout(args, progress=None):
    db.delete_session(getattr(args, "token", None))
    return {"success": True}


def cmd_sessions_list(args, progress=None):
    db.purge_expired_sessions()
    username = getattr(args, "username", None)
    rows = db.list_sessions(username) if username else db.list_sessions()
    return {"sessions": list(rows or [])}


def cmd_sessions_revoke(args, progress=None):
    token = getattr(args, "token", None)
    username = getattr(args, "username", None)
    revoke_all = bool(getattr(args, "all", False))

    if not (token or username or revoke_all):
        return {"error": "Nothing to revoke — pass -t <token>, -u <user>, or --all"}

    if revoke_all:
        # No bulk delete in the db API, and looping the user list keeps this
        # honest about what it touches rather than reaching into the schema.
        for row in db.list_users():
            name = _get(row, "username")
            if name:
                db.delete_user_sessions(name)
        return {"success": True, "scope": "all"}

    if token:
        db.delete_session(token)
        return {"success": True, "scope": "token"}

    db.delete_user_sessions(username)
    return {"success": True, "scope": "user", "username": username}


# ─── accounts ────────────────────────────────────────────────────────────────

def cmd_list(args, progress=None):
    # list_users() already returns the safe public shape, hash-free.
    return {"users": db.list_users()}


def cmd_create(args, progress=None):
    _resolve_secrets(args)
    user = db.create_user(
        getattr(args, "username", None),
        getattr(args, "password", None),
        role=getattr(args, "role", None),
        permissions=_check_perms(_csv(getattr(args, "perms", None))),
        enabled=not bool(getattr(args, "disabled", False)),
        must_change_password=bool(getattr(args, "must_change_password", False)),
    )
    return {"success": True, "user": user}


def cmd_delete(args, progress=None):
    username = getattr(args, "username", None)

    target = db.get_user(username)
    if target is None:
        return {"error": "No such account: {}".format(username)}

    # Deleting the last administrator would lock everyone out of account
    # management with no way back short of editing the database by hand. db has
    # its own lockout guard; this one exists to fail with a message that says
    # what to do about it.
    if target.get("isAdmin"):
        others = [
            u for u in db.list_users()
            if str(u.get("username", "")).lower() != str(username).lower()
        ]
        if not any(o.get("isAdmin") and o.get("enabled", True) for o in others):
            return {"error": "This is the only enabled administrator — create another one before deleting it."}

    db.delete_user(username)
    return {"success": True, "username": username}


def cmd_update(args, progress=None):
    username = getattr(args, "username", None)
    if db.get_user(username) is None:
        return {"error": "No such account: {}".format(username)}

    enable = bool(getattr(args, "enable", False))
    disable = bool(getattr(args, "disable", False))
    if enable and disable:
        return {"error": "--enable and --disable are mutually exclusive"}

    role = getattr(args, "role", None)
    clear_role = bool(getattr(args, "clear_role", False))
    if role and clear_role:
        return {"error": "--clear-role cannot be combined with -r/--role"}

    # Only forward what was actually asked for; anything omitted keeps its
    # current value rather than being reset to a default.
    changes = {}
    if clear_role:
        changes["role"] = None
    elif role:
        changes["role"] = role

    raw_perms = getattr(args, "perms", None)
    if raw_perms is not None:
        changes["permissions"] = _check_perms(_csv(raw_perms))

    if enable:
        changes["enabled"] = True
    elif disable:
        changes["enabled"] = False

    if not changes:
        return {"error": "Nothing to change — pass -r, --clear-role, --perms, --enable or --disable"}

    db.update_user(username, **changes)
    return {"success": True, "user": db.get_user(username), "changed": sorted(changes)}


def cmd_passwd(args, progress=None):
    _resolve_secrets(args)
    username = getattr(args, "username", None)
    new_password = getattr(args, "password", None)
    current = getattr(args, "current", None)

    # Supplying --current is what marks this as the self-service path: the
    # caller is proving they already know the password rather than acting as
    # an administrator. That path is what the global switch gates.
    if current is not None and not db.get_setting("allow_self_password_change"):
        return {"error": "Self-service password changes are disabled on this install — "
                         "ask an administrator to change it for you."}

    result = db.set_password(
        username, new_password,
        current_password=current,
        # Changing your own password revokes every other session but should not
        # sign you out of the one you are using to do it.
        keep_token=getattr(args, "keep_token", None),
    )
    return {
        "success": True,
        "username": username,
        "sessionsRevoked": (result or {}).get("sessionsRevoked", 0),
        "user": (result or {}).get("user"),
    }


# ─── permissions, roles, settings ────────────────────────────────────────────

def cmd_perms(args, progress=None):
    return {
        "permissions": [{"name": name, "description": desc} for name, desc in permissions.CATALOG],
        "areas": list(permissions.AREAS),
    }


def cmd_roles_list(args, progress=None):
    return {"roles": list(db.list_roles() or [])}


def cmd_roles_create(args, progress=None):
    perms = _check_perms(_csv(getattr(args, "perms", None))) or []
    db.create_role(
        getattr(args, "name", None),
        description=getattr(args, "description", None) or "",
        permissions=perms,
    )
    return {"success": True, "roles": list(db.list_roles() or [])}


def cmd_roles_update(args, progress=None):
    name = getattr(args, "name", None)

    # permissions.py documents `admin` as un-strippable precisely so an install
    # cannot lock itself out; db enforces it too, but failing here gives a
    # clearer message than a constraint error would.
    if str(name or "").lower() == "admin":
        return {"error": "The built-in admin role always holds every permission and cannot be edited."}

    changes = {}
    description = getattr(args, "description", None)
    if description is not None:
        changes["description"] = description
    raw_perms = getattr(args, "perms", None)
    if raw_perms is not None:
        changes["permissions"] = _check_perms(_csv(raw_perms))

    if not changes:
        return {"error": "Nothing to change — pass --desc and/or --perms"}

    db.update_role(name, **changes)
    return {"success": True, "roles": list(db.list_roles() or [])}


def cmd_roles_delete(args, progress=None):
    name = getattr(args, "name", None)
    if str(name or "").lower() == "admin":
        return {"error": "The built-in admin role cannot be deleted."}
    db.delete_role(name)
    return {"success": True, "roles": list(db.list_roles() or [])}


def cmd_settings(args, progress=None):
    assignments = getattr(args, "assignments", None)

    if assignments:
        for item in assignments:
            if "=" not in item:
                return {"error": "Expected key=value, got: {}".format(item)}
            key, raw = item.split("=", 1)
            key = key.strip()
            if key not in permissions.SETTINGS_DEFAULTS:
                return {"error": "Unknown setting: {}  — valid keys: {}".format(
                    key, ", ".join(sorted(permissions.SETTINGS_DEFAULTS)))}
            # JSON first so `false`, `0` and `720` land as the right type;
            # anything else is stored verbatim as a string.
            try:
                value = json.loads(raw)
            except ValueError:
                value = raw
            db.set_setting(key, value)

    return {"settings": db.all_settings()}


# ─── renderers ───────────────────────────────────────────────────────────────

def make_renderers(render):
    """Builds the human-mode printers. Takes the CLI's `render` module so the
    output uses exactly the colour helpers the built-in commands use."""

    def r_init(result, args):
        if _err(result, render):
            return
        print(render.green("✓ ") + "Accounts database ready "
              + render.dim("({})".format(result.get("database", ""))))
        print(render.dim("{} account(s) present.".format(len(result.get("users", [])))))
        creds = result.get("defaultCredentials")
        if creds:
            print(render.red("⚠ Default login is {} / {} — change it now:".format(
                creds.get("username"), creds.get("password"))))
            print(render.dim("    mcpanel accounts passwd -u {} -p <new password>".format(creds.get("username"))))

    def r_login(result, args):
        if _err(result, render):
            return
        user = result.get("user") or {}
        print(render.green("✓ ") + "Signed in as " + render.bold(user.get("username", "?"))
              + render.dim("  ({})".format(user.get("role") or "no role")))
        print("  token   : " + str(result.get("token", "")))
        expires = _ts(result.get("expiresAt"))
        if expires:
            print("  expires : " + render.dim(expires))
        if user.get("mustChangePassword"):
            print(render.yellow("⚠ This account still needs a new password."))

    def r_verify(result, args):
        if _err(result, render):
            return
        if not result.get("valid"):
            print(render.dim("○ Session is not valid."))
            return
        user = result.get("user") or {}
        print(render.green("● valid") + "  " + render.bold(user.get("username", "?"))
              + render.dim("  ({})".format(user.get("role") or "no role")))

    def r_whoami(result, args):
        if _err(result, render):
            return
        user = result.get("user") or {}
        print(render.bold(user.get("username", "?"))
              + render.dim("  [{}]".format(user.get("role") or "no role")))
        print("  status      : " + (render.green("enabled") if user.get("enabled", True) else render.red("disabled")))
        print("  admin       : " + ("yes" if user.get("isAdmin") else "no"))
        print("  permissions : " + _perm_summary(user))
        print("  last login  : " + render.dim(_ts(user.get("lastLogin")) or "never"))

    def r_list(result, args):
        if _err(result, render):
            return
        users = result.get("users", [])
        if not users:
            print(render.dim("No accounts. Create one with: mcpanel accounts create -u <name> -p <password>"))
            return
        print(render.bold("{:<20} {:<12} {:<9} {:<12} LAST LOGIN".format(
            "USERNAME", "ROLE", "STATUS", "PERMISSIONS")))
        for u in users:
            enabled = u.get("enabled", True)
            raw_status = "enabled" if enabled else "disabled"
            status = render.green(raw_status) if enabled else render.red(raw_status)
            name = str(u.get("username", ""))[:20]
            role = str(u.get("role") or "-")[:12]
            perms = _perm_summary(u)[:12]
            print("{:<20} {:<12} {}{} {:<12} {}".format(
                name, role, status, _pad(raw_status, 9), perms,
                render.dim(_ts(u.get("lastLogin")) or "never")))

    def r_user(result, args):
        if _err(result, render):
            return
        user = result.get("user") or {}
        print(render.green("✓ ") + render.bold(user.get("username", "?"))
              + render.dim("  role={} perms={}".format(
                  user.get("role") or "-", _perm_summary(user))))

    def r_deleted(result, args):
        if _err(result, render):
            return
        print(render.green("✓ ") + "Deleted account " + render.bold(result.get("username", "")))

    def r_passwd(result, args):
        if _err(result, render):
            return
        print(render.green("✓ ") + "Password updated for " + render.bold(result.get("username", "")))

    def r_success(result, args):
        if _err(result, render):
            return
        print(render.green("✓ done"))

    def r_perms(result, args):
        if _err(result, render):
            return
        by_area = {}
        for entry in result.get("permissions", []):
            area = entry["name"].split(".", 1)[0]
            by_area.setdefault(area, []).append(entry)
        for area in sorted(by_area):
            print(render.bold(area))
            for entry in by_area[area]:
                print("  {:<20} {}".format(entry["name"], render.dim(entry["description"])))

    def r_roles(result, args):
        if _err(result, render):
            return
        roles = result.get("roles", [])
        if not roles:
            print(render.dim("No roles defined."))
            return
        print(render.bold("{:<14} {:<8} {:<12} DESCRIPTION".format("NAME", "BUILTIN", "PERMISSIONS")))
        for role in roles:
            builtin = "yes" if _get(role, "builtin", default=False) else "no"
            print("{:<14} {:<8} {:<12} {}".format(
                str(_get(role, "name", default=""))[:14],
                builtin,
                _perm_summary(role)[:12],
                render.dim(str(_get(role, "description", default="") or ""))))

    def r_sessions(result, args):
        if _err(result, render):
            return
        sessions = result.get("sessions", [])
        if not sessions:
            print(render.dim("No active sessions."))
            return
        print(render.bold("{:<11} {:<18} {:<17} {:<17} IP".format(
            "TOKEN", "USER", "CREATED", "EXPIRES")))
        for s in sessions:
            # Only a hash of the token is stored, and even that is truncated —
            # a session identifier is a bearer credential.
            ident = _get(s, "tokenPrefix", "token", "tokenHash", "token_hash", "id", default="")
            print("{:<11} {:<18} {:<17} {:<17} {}".format(
                _short(ident),
                str(_get(s, "username", default=""))[:18],
                _ts(_get(s, "createdAt", "created_at")),
                _ts(_get(s, "expiresAt", "expires_at")),
                render.dim(str(_get(s, "ip", default="") or ""))))

    def r_settings(result, args):
        if _err(result, render):
            return
        settings = result.get("settings", {}) or {}
        if not settings:
            print(render.dim("No settings stored."))
            return
        print(render.bold("{:<32} VALUE".format("SETTING")))
        for key in sorted(settings):
            print("{:<32} {}".format(key, json.dumps(settings[key])))

    return {
        "accounts-init": r_init,
        "accounts-login": r_login,
        "accounts-verify": r_verify,
        "accounts-whoami": r_whoami,
        "accounts-logout": r_success,
        "accounts-list": r_list,
        "accounts-create": r_user,
        "accounts-update": r_user,
        "accounts-delete": r_deleted,
        "accounts-passwd": r_passwd,
        "accounts-perms": r_perms,
        "accounts-roles-list": r_roles,
        "accounts-roles-create": r_roles,
        "accounts-roles-update": r_roles,
        "accounts-roles-delete": r_roles,
        "accounts-settings": r_settings,
        "accounts-sessions-list": r_sessions,
        "accounts-sessions-revoke": r_success,
    }
