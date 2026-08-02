"""SQLite storage for the accounts addon — users, roles, sessions, settings.

The panel is reachable from any device on the network, so "who is asking" stops
being rhetorical: this module is what stands between a browser tab and someone
else's Minecraft servers. Three consequences shape everything below.

  * **Passwords are never recoverable.** PBKDF2-HMAC-SHA256, per-user salt,
    stored as `pbkdf2_sha256$<iters>$<salt>$<hash>`. Cost is raised over time
    and old records are re-hashed silently on the next successful login.
  * **Session tokens are never stored.** Only `sha256(token)` lands in the
    database, so a stolen `accounts.db` yields no usable sessions — the same
    reasoning that applies to passwords applies to bearer tokens.
  * **The install cannot lock itself out.** Every mutation is checked, inside
    its own transaction, against "does at least one enabled account still hold
    `accounts.manage`?" — and rolled back if the answer became no.

The WebUI shells out to `mcpanel api accounts ...` once per request, so several
OS processes hit this file concurrently. Hence WAL, a real busy timeout, and
short transactions.

Timestamps are **milliseconds** since the epoch, matching the convention the
rest of MCPanel already uses (`created` in config.json, `next_run` in
schedules.json).

Database lives at `<userData>/addon-data/accounts/accounts.db`.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager

try:
    from . import permissions as _perms
except ImportError:  # loaded as a loose module rather than as a package
    import permissions as _perms  # type: ignore

# Several public functions take a `permissions` argument, which would shadow
# the module inside their bodies — hence the underscored alias for internal
# use, and this plain alias for anyone importing it from here.
permissions = _perms

try:
    from mcpanel import paths
except ImportError:  # standalone use (tests); resolved by _user_data() instead
    paths = None


# ─── Constants ───────────────────────────────────────────────────────────────

SCHEMA_VERSION = 1

# Raised over time; verify_password transparently upgrades records hashed at a
# lower cost the next time their owner successfully authenticates.
PBKDF2_ITERATIONS = 260000
PBKDF2_SALT_BYTES = 32
PBKDF2_PREFIX = "pbkdf2_sha256"

SESSION_TOKEN_BYTES = 32

# Several `mcpanel` processes write here at once; wait rather than fail.
SQLITE_TIMEOUT_SECONDS = 15

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
ROLENAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class AccountsError(Exception):
    """Anything the operator caused and can fix. The message is surfaced to
    them verbatim, so it must read as an explanation rather than a stack
    trace fragment."""


class _Unset(object):
    """Distinguishes "argument not supplied" from an explicit None, so
    update_user(role=None) can genuinely clear a role."""

    def __repr__(self):
        return "<unset>"

    def __bool__(self):
        return False


_UNSET = _Unset()
UNSET = _UNSET  # public alias


def _now():
    return int(time.time() * 1000)


# ─── Location ────────────────────────────────────────────────────────────────

def _fallback_home():
    override = os.environ.get("MCPANEL_HOME")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    import sys
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(appdata, "mcpanel")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "mcpanel")
    appdata = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(appdata, "mcpanel")


def _user_data():
    # paths.USER_DATA is frozen at import time. Re-running the resolver keeps a
    # MCPANEL_HOME that was set later in the same process honest — which is how
    # the tests, and anything embedding the CLI, expect it to behave.
    if paths is not None:
        resolver = getattr(paths, "_default_home", None)
        if callable(resolver):
            try:
                return resolver()
            except Exception:
                pass
        return paths.USER_DATA
    return _fallback_home()


def data_dir():
    """`<userData>/addon-data/accounts/` — created on demand."""
    d = os.path.join(_user_data(), "addon-data", "accounts")
    os.makedirs(d, exist_ok=True)
    return d


def db_path():
    return os.path.join(data_dir(), "accounts.db")


# ─── Connections ─────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash        TEXT    NOT NULL,
    role                 TEXT,
    permissions          TEXT    NOT NULL DEFAULT '[]',
    enabled              INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL,
    last_login           INTEGER
);

CREATE TABLE IF NOT EXISTS roles (
    name        TEXT    PRIMARY KEY COLLATE NOCASE,
    description TEXT    NOT NULL DEFAULT '',
    permissions TEXT    NOT NULL DEFAULT '[]',
    builtin     INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL,
    user_agent TEXT,
    ip         TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
"""

# Schema/seed work is idempotent but not free; skip it once a given database
# file has been prepared in this process.
_prepared = set()

# What the auto-seed in _prepare() actually did, keyed by db path. init_db()
# consumes it so that `accounts init` on a brand-new database still reports
# "created, here are the default credentials" — the auto-seed would otherwise
# have already happened by the time init_db ran, making it report a no-op.
_pending_seed = {}


def connect():
    """A configured connection with the schema guaranteed present."""
    path = db_path()
    conn = sqlite3.connect(path, timeout=SQLITE_TIMEOUT_SECONDS, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=%d" % (SQLITE_TIMEOUT_SECONDS * 1000))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        # A filesystem without shared-memory support (some network mounts)
        # refuses WAL. Rollback journalling still works, just with less
        # concurrency, so this is a downgrade rather than a failure.
        pass
    if path not in _prepared:
        _prepare(conn)
        _prepared.add(path)
    return conn


@contextmanager
def _db():
    """One short transaction: commits on success, rolls back on any exception.

    The rollback is load-bearing — the lockout guard deliberately mutates
    first and raises second, relying on this to undo the damage.
    """
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _prepare(conn):
    conn.executescript(_SCHEMA)
    _migrate(conn)
    _pending_seed[db_path()] = _seed(conn, force=False)
    conn.commit()


def _migrate(conn):
    current = _get_setting_row(conn, "schema_version")
    if current is None:
        _set_setting_row(conn, "schema_version", SCHEMA_VERSION)
        return
    version = int(current)
    if version == SCHEMA_VERSION:
        return
    if version > SCHEMA_VERSION:
        raise AccountsError(
            "accounts.db was written by a newer version of the accounts addon "
            "(schema v%d, this build understands v%d). Update MCPanel-CLI."
            % (version, SCHEMA_VERSION)
        )
    # Future migrations chain here: `if version < 2: ...; version = 2`.
    _set_setting_row(conn, "schema_version", SCHEMA_VERSION)


# ─── Seeding ─────────────────────────────────────────────────────────────────

def _seed(conn, force=False):
    """Ensure the builtin roles exist, and that *somebody* can log in.

    Auto-seeding on first touch (rather than only in an explicit `init`) means
    a fresh install is usable immediately: the WebUI's very first `login` call
    finds admin/admin waiting. It can never resurrect a deleted admin, because
    the lockout guard makes an empty users table unreachable once seeded.
    """
    now = _now()
    for name, spec in _perms.BUILTIN_ROLES.items():
        row = conn.execute("SELECT name, permissions FROM roles WHERE name = ?", (name,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO roles (name, description, permissions, builtin, created_at)"
                " VALUES (?, ?, ?, 1, ?)",
                (name, spec["description"], json.dumps(spec["permissions"]), now),
            )
        elif force or name == "admin":
            # `admin` is repaired unconditionally: a damaged admin role is the
            # one failure mode that has no in-band recovery path.
            conn.execute(
                "UPDATE roles SET description = ?, permissions = ?, builtin = 1 WHERE name = ?",
                (spec["description"], json.dumps(spec["permissions"]), name),
            )

    count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if count:
        return {"created": False, "defaultCredentials": None, "users": count}

    # The seeded password deliberately bypasses min_password_length: it is
    # meant to be trivially typeable once and then changed.
    conn.execute(
        "INSERT INTO users (username, password_hash, role, permissions, enabled,"
        " must_change_password, created_at, updated_at, last_login)"
        " VALUES (?, ?, 'admin', '[]', 1, 1, ?, ?, NULL)",
        (DEFAULT_ADMIN_USERNAME, hash_password(DEFAULT_ADMIN_PASSWORD), now, now),
    )
    return {
        "created": True,
        "defaultCredentials": {
            "username": DEFAULT_ADMIN_USERNAME,
            "password": DEFAULT_ADMIN_PASSWORD,
        },
        "users": 1,
    }


def init_db(force=False):
    """Create/repair the database. Idempotent; never resets a real password."""
    path = db_path()
    conn = connect()  # runs _prepare (and therefore the auto-seed) on first touch
    try:
        # If this very call is what created the database, _prepare already did
        # the seeding; report that rather than the no-op a second _seed sees.
        stashed = _pending_seed.pop(path, None)
        if force:
            with conn:
                result = _seed(conn, force=True)
            if stashed and stashed.get("created"):
                result["created"] = True
                result["defaultCredentials"] = stashed["defaultCredentials"]
        elif stashed is not None:
            result = dict(stashed)
        else:
            with conn:
                result = _seed(conn, force=False)

        result["users"] = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        result["path"] = path
        return result
    finally:
        conn.close()


# ─── Password hashing ────────────────────────────────────────────────────────

def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _unb64(text):
    return base64.b64decode(text.encode("ascii"))


def hash_password(password, iterations=None):
    iterations = int(iterations or PBKDF2_ITERATIONS)
    salt = secrets.token_bytes(PBKDF2_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "%s$%d$%s$%s" % (PBKDF2_PREFIX, iterations, _b64(salt), _b64(digest))


def _check_hash(stored, password):
    """Returns (ok, needs_rehash). A malformed record fails closed."""
    try:
        prefix, iters, salt_b64, hash_b64 = stored.split("$", 3)
        if prefix != PBKDF2_PREFIX:
            return False, False
        iterations = int(iters)
        salt = _unb64(salt_b64)
        expected = _unb64(hash_b64)
    except (ValueError, TypeError, AttributeError):
        return False, False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    ok = hmac.compare_digest(actual, expected)
    return ok, (ok and iterations < PBKDF2_ITERATIONS)


# ─── Settings ────────────────────────────────────────────────────────────────

def _get_setting_row(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except ValueError:
        return None


def _set_setting_row(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def _all_settings(conn):
    merged = dict(_perms.SETTINGS_DEFAULTS)
    for row in conn.execute("SELECT key, value FROM settings"):
        if row["key"] == "schema_version":
            continue
        try:
            merged[row["key"]] = json.loads(row["value"])
        except ValueError:
            pass
    return merged


def all_settings():
    with _db() as conn:
        return _all_settings(conn)


def get_setting(key):
    with _db() as conn:
        return _all_settings(conn).get(key)


def _coerce_setting(key, value):
    """CLI arguments arrive as strings; settings have real types."""
    if key not in _perms.SETTINGS_DEFAULTS:
        raise AccountsError(
            "Unknown setting '%s'. Known settings: %s"
            % (key, ", ".join(sorted(_perms.SETTINGS_DEFAULTS)))
        )
    default = _perms.SETTINGS_DEFAULTS[key]

    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        raise AccountsError("Setting '%s' expects true or false, got '%s'" % (key, value))

    if isinstance(default, int):
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            raise AccountsError("Setting '%s' expects a whole number, got '%s'" % (key, value))
        if key == "session_ttl_hours" and number <= 0:
            raise AccountsError("session_ttl_hours must be at least 1 — sessions are stored, not in-memory")
        if key == "min_password_length" and number < 1:
            raise AccountsError("min_password_length must be at least 1")
        return number

    return str(value)


def set_setting(key, value):
    coerced = _coerce_setting(key, value)
    with _db() as conn:
        _set_setting_row(conn, key, coerced)
        return {"key": key, "value": coerced}


# ─── Permission helpers ──────────────────────────────────────────────────────

def _role_permissions(conn, role):
    if not role:
        return []
    row = conn.execute("SELECT permissions FROM roles WHERE name = ?", (role,)).fetchone()
    if row is None:
        return []
    try:
        return list(json.loads(row["permissions"]))
    except ValueError:
        return []


def _grants(conn, row):
    """A user's raw grant list — role grants unioned with their own extras."""
    try:
        extras = list(json.loads(row["permissions"] or "[]"))
    except (ValueError, TypeError):
        extras = []
    return _role_permissions(conn, row["role"]) + extras


def _effective(conn, row):
    return _perms.expand(_grants(conn, row))


def effective_permissions(user):
    """Concrete, wildcard-expanded permissions for a user dict or username."""
    with _db() as conn:
        row = _row(conn, user["username"] if isinstance(user, dict) else user)
        if row is None:
            return []
        return _effective(conn, row)


def _has(conn, row, permission):
    return _perms.granted(_grants(conn, row), permission)


def _validated(perms):
    if perms is None:
        return None
    if isinstance(perms, str):
        perms = [p.strip() for p in perms.split(",")]
    valid, unknown = _perms.validate(perms)
    if unknown:
        raise AccountsError(
            "Unknown permission(s): %s. Run 'mcpanel accounts perms' for the full list."
            % ", ".join(unknown)
        )
    # Preserve order, drop duplicates.
    seen, out = set(), []
    for p in valid:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


# ─── Lockout guard ───────────────────────────────────────────────────────────

def _manager_count(conn):
    n = 0
    for row in conn.execute("SELECT * FROM users WHERE enabled = 1"):
        if _has(conn, row, "accounts.manage"):
            n += 1
    return n


def _assert_not_locked_out(conn, what):
    """Called after a mutation, inside its transaction. Raising here rolls the
    whole thing back — which is why every guarded path mutates first and checks
    second rather than trying to predict the outcome."""
    if _manager_count(conn) == 0:
        raise AccountsError(
            "Refusing to %s: it would leave no enabled account holding "
            "'accounts.manage', locking everyone out of account management. "
            "Grant that permission to another account first." % what
        )


# ─── Users ───────────────────────────────────────────────────────────────────

def _row(conn, username):
    return conn.execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()


def _require_row(conn, username):
    row = _row(conn, username)
    if row is None:
        raise AccountsError("No such user: %s" % username)
    return row


def _user_public(conn, row):
    try:
        extras = list(json.loads(row["permissions"] or "[]"))
    except (ValueError, TypeError):
        extras = []
    effective = _effective(conn, row)
    return {
        "username": row["username"],
        "role": row["role"],
        "permissions": extras,
        "effectivePermissions": effective,
        "isAdmin": _perms.granted(_grants(conn, row), "accounts.manage"),
        "enabled": bool(row["enabled"]),
        "mustChangePassword": bool(row["must_change_password"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "lastLogin": row["last_login"],
    }


def user_public(row):
    """Safe, JSON-ready shape. Never contains a password hash — this is the
    only shape any caller should be handing to the WebUI."""
    with _db() as conn:
        if isinstance(row, str):
            row = _require_row(conn, row)
        return _user_public(conn, row)


def _assert_username(username):
    if not username or not USERNAME_RE.match(username):
        raise AccountsError(
            "Invalid username '%s'. Use 1-64 characters from A-Z, a-z, 0-9, dot, dash or underscore."
            % username
        )


def _assert_password(conn, password):
    minimum = int(_all_settings(conn).get("min_password_length", 4))
    if password is None or len(password) < minimum:
        raise AccountsError("Password must be at least %d characters." % minimum)


def _assert_role_exists(conn, role):
    if role in (None, ""):
        return
    if conn.execute("SELECT 1 FROM roles WHERE name = ?", (role,)).fetchone() is None:
        known = [r["name"] for r in conn.execute("SELECT name FROM roles ORDER BY name")]
        raise AccountsError("No such role: %s. Known roles: %s" % (role, ", ".join(known)))


def create_user(username, password, role=None, permissions=None, enabled=True,
                must_change_password=False):
    _assert_username(username)
    extras = _validated(permissions) or []
    with _db() as conn:
        if _row(conn, username) is not None:
            raise AccountsError("A user named '%s' already exists." % username)
        _assert_password(conn, password)
        _assert_role_exists(conn, role)
        now = _now()
        conn.execute(
            "INSERT INTO users (username, password_hash, role, permissions, enabled,"
            " must_change_password, created_at, updated_at, last_login)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (username, hash_password(password), role or None, json.dumps(extras),
             1 if enabled else 0, 1 if must_change_password else 0, now, now),
        )
        return _user_public(conn, _require_row(conn, username))


def get_user(username):
    """Public shape, or None. Deliberately never exposes `password_hash` —
    callers that need it use the private row helpers."""
    with _db() as conn:
        row = _row(conn, username)
        return _user_public(conn, row) if row is not None else None


def list_users():
    with _db() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE").fetchall()
        return [_user_public(conn, r) for r in rows]


def update_user(username, role=_UNSET, permissions=_UNSET, enabled=_UNSET):
    with _db() as conn:
        row = _require_row(conn, username)
        sets, values = [], []

        if role is not _UNSET:
            _assert_role_exists(conn, role)
            sets.append("role = ?")
            values.append(role or None)

        if permissions is not _UNSET:
            sets.append("permissions = ?")
            values.append(json.dumps(_validated(permissions) or []))

        if enabled is not _UNSET:
            sets.append("enabled = ?")
            values.append(1 if enabled else 0)

        if not sets:
            return _user_public(conn, row)

        sets.append("updated_at = ?")
        values.append(_now())
        values.append(row["id"])
        conn.execute("UPDATE users SET %s WHERE id = ?" % ", ".join(sets), values)

        _assert_not_locked_out(conn, "update '%s'" % row["username"])

        if enabled is not _UNSET and not enabled and _all_settings(conn).get("revoke_sessions_on_disable", True):
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))

        return _user_public(conn, _require_row(conn, username))


def delete_user(username):
    with _db() as conn:
        row = _require_row(conn, username)
        conn.execute("DELETE FROM users WHERE id = ?", (row["id"],))
        _assert_not_locked_out(conn, "delete '%s'" % row["username"])


def set_password(username, new_password, current_password=None, keep_token=None):
    """Set a password.

    `current_password` makes this a self-service change: it must verify before
    anything happens. `keep_token` (an addition beyond the documented
    signature) spares one session from the mass revoke, so a user changing
    their own password in the WebUI is not immediately logged out.
    """
    with _db() as conn:
        row = _require_row(conn, username)

        if current_password is not None:
            ok, _ = _check_hash(row["password_hash"], current_password)
            if not ok:
                raise AccountsError("Current password is incorrect.")

        _assert_password(conn, new_password)
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE id = ?",
            (hash_password(new_password), _now(), row["id"]),
        )

        # A password change invalidates everything that was authorised by the
        # old one — that is the entire point of changing it after a leak.
        if keep_token:
            revoked = conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                (row["id"], _token_hash(keep_token)),
            ).rowcount
        else:
            revoked = conn.execute(
                "DELETE FROM sessions WHERE user_id = ?", (row["id"],)
            ).rowcount

        return {
            "username": row["username"],
            "sessionsRevoked": max(0, revoked),
            "user": _user_public(conn, _require_row(conn, username)),
        }


def verify_password(username, password):
    """Public user dict on success, None on any failure — wrong password,
    unknown user and disabled account are deliberately indistinguishable to
    the caller so the login endpoint cannot be used to enumerate accounts."""
    result = None
    absent = False

    with _db() as conn:
        row = _row(conn, username)
        if row is None:
            absent = True
        else:
            ok, needs_rehash = _check_hash(row["password_hash"], password)
            if ok and row["enabled"]:
                updates = ["last_login = ?"]
                values = [_now()]
                if needs_rehash:
                    updates.append("password_hash = ?")
                    values.append(hash_password(password))
                values.append(row["id"])
                conn.execute("UPDATE users SET %s WHERE id = ?" % ", ".join(updates), values)
                result = _user_public(conn, _require_row(conn, username))

    if absent:
        # Spend roughly what a real verification costs, so response time does
        # not reveal which usernames exist. Deliberately outside the
        # transaction above — this is ~200ms and must not hold a write lock.
        hashlib.pbkdf2_hmac("sha256", b"absent", b"absent-salt", PBKDF2_ITERATIONS)

    return result


# ─── Sessions ────────────────────────────────────────────────────────────────

def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _looks_like_hash(value):
    # token_urlsafe(32) is 43 base64url chars, never 64 hex — so a 64-hex
    # string is unambiguously a stored hash from `list_sessions`.
    return isinstance(value, str) and len(value) == 64 and re.match(r"^[0-9a-f]{64}$", value) is not None


def create_session(username, ttl_hours=None, user_agent=None, ip=None):
    with _db() as conn:
        row = _require_row(conn, username)
        if not row["enabled"]:
            raise AccountsError("Account '%s' is disabled." % row["username"])

        settings = _all_settings(conn)
        hours = int(ttl_hours or settings.get("session_ttl_hours", 720))
        if hours <= 0:
            raise AccountsError("Session TTL must be at least 1 hour.")

        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now(),))

        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        now = _now()
        expires = now + hours * 3600 * 1000
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen, user_agent, ip)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_token_hash(token), row["id"], now, expires, now, user_agent, ip),
        )
        return {
            "token": token,
            "createdAt": now,
            "expiresAt": expires,
            "user": _user_public(conn, row),
        }


def verify_session(token):
    if not token:
        return None
    with _db() as conn:
        row = conn.execute(
            "SELECT s.*, u.username AS username FROM sessions s"
            " JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
            (_token_hash(token),),
        ).fetchone()
        if row is None:
            return None

        now = _now()
        if row["expires_at"] <= now:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
            return None

        user_row = _row(conn, row["username"])
        if user_row is None or not user_row["enabled"]:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
            return None

        conn.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, row["token_hash"]))
        return {
            "user": _user_public(conn, user_row),
            "createdAt": row["created_at"],
            "expiresAt": row["expires_at"],
            "lastSeen": now,
            "userAgent": row["user_agent"],
            "ip": row["ip"],
        }


def delete_session(token):
    """Accepts a raw token or the `tokenHash` reported by list_sessions."""
    if not token:
        return False
    key = token if _looks_like_hash(token) else _token_hash(token)
    with _db() as conn:
        return conn.execute("DELETE FROM sessions WHERE token_hash = ?", (key,)).rowcount > 0


def delete_user_sessions(username):
    with _db() as conn:
        row = _require_row(conn, username)
        return conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],)).rowcount


def list_sessions(username=None):
    with _db() as conn:
        if username is not None:
            row = _require_row(conn, username)
            rows = conn.execute(
                "SELECT s.*, u.username AS username FROM sessions s JOIN users u ON u.id = s.user_id"
                " WHERE s.user_id = ? ORDER BY s.last_seen DESC",
                (row["id"],),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT s.*, u.username AS username FROM sessions s JOIN users u ON u.id = s.user_id"
                " ORDER BY s.last_seen DESC"
            ).fetchall()
        return [
            {
                "tokenHash": r["token_hash"],
                "username": r["username"],
                "createdAt": r["created_at"],
                "expiresAt": r["expires_at"],
                "lastSeen": r["last_seen"],
                "userAgent": r["user_agent"],
                "ip": r["ip"],
            }
            for r in rows
        ]


def purge_expired_sessions():
    with _db() as conn:
        return conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now(),)).rowcount


# ─── Roles ───────────────────────────────────────────────────────────────────

def _role_public(conn, row):
    try:
        grants = list(json.loads(row["permissions"] or "[]"))
    except (ValueError, TypeError):
        grants = []
    users = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = ? COLLATE NOCASE", (row["name"],)
    ).fetchone()["n"]
    return {
        "name": row["name"],
        "description": row["description"],
        "permissions": grants,
        "effectivePermissions": _perms.expand(grants),
        "builtin": bool(row["builtin"]),
        "users": users,
        "createdAt": row["created_at"],
    }


def list_roles():
    with _db() as conn:
        rows = conn.execute("SELECT * FROM roles ORDER BY builtin DESC, name COLLATE NOCASE").fetchall()
        return [_role_public(conn, r) for r in rows]


def create_role(name, description="", permissions=None):
    if not name or not ROLENAME_RE.match(name):
        raise AccountsError(
            "Invalid role name '%s'. Use 1-64 characters from A-Z, a-z, 0-9, dot, dash or underscore." % name
        )
    grants = _validated(permissions) or []
    with _db() as conn:
        if conn.execute("SELECT 1 FROM roles WHERE name = ?", (name,)).fetchone():
            raise AccountsError("A role named '%s' already exists." % name)
        conn.execute(
            "INSERT INTO roles (name, description, permissions, builtin, created_at) VALUES (?, ?, ?, 0, ?)",
            (name, description or "", json.dumps(grants), _now()),
        )
        row = conn.execute("SELECT * FROM roles WHERE name = ?", (name,)).fetchone()
        return _role_public(conn, row)


def update_role(name, description=_UNSET, permissions=_UNSET):
    with _db() as conn:
        row = conn.execute("SELECT * FROM roles WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise AccountsError("No such role: %s" % name)

        if permissions is not _UNSET and row["name"].lower() == "admin":
            raise AccountsError(
                "The 'admin' role always holds every permission and cannot be changed. "
                "Create a separate role if you need a narrower set."
            )

        sets, values = [], []
        if description is not _UNSET:
            sets.append("description = ?")
            values.append(description or "")
        if permissions is not _UNSET:
            sets.append("permissions = ?")
            values.append(json.dumps(_validated(permissions) or []))
        if not sets:
            return _role_public(conn, row)

        values.append(row["name"])
        conn.execute("UPDATE roles SET %s WHERE name = ?" % ", ".join(sets), values)

        _assert_not_locked_out(conn, "change role '%s'" % row["name"])
        return _role_public(conn, conn.execute("SELECT * FROM roles WHERE name = ?", (name,)).fetchone())


def delete_role(name):
    with _db() as conn:
        row = conn.execute("SELECT * FROM roles WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise AccountsError("No such role: %s" % name)
        if row["name"].lower() == "admin":
            raise AccountsError("The 'admin' role cannot be deleted.")
        if row["builtin"]:
            raise AccountsError("'%s' is a builtin role and cannot be deleted." % row["name"])

        holders = [
            r["username"]
            for r in conn.execute(
                "SELECT username FROM users WHERE role = ? COLLATE NOCASE ORDER BY username", (row["name"],)
            )
        ]
        if holders:
            raise AccountsError(
                "Role '%s' is still assigned to: %s. Reassign those accounts first."
                % (row["name"], ", ".join(holders))
            )

        conn.execute("DELETE FROM roles WHERE name = ?", (row["name"],))
        _assert_not_locked_out(conn, "delete role '%s'" % row["name"])
