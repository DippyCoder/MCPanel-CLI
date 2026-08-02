# accounts — user accounts, roles and permissions

A bundled MCPanel-CLI addon. It adds a `mcpanel accounts …` command group backed
by SQLite, giving MCPanel a real multi-user layer: named accounts, hashed
passwords, session tokens, roles, and a per-permission access model.

It ships with the CLI and is **enabled by default** — nothing to install. Because
the CLI is MCPanel's backend, every consumer of the CLI shares one user
database, so an account created here works in [MCPanel-WebUI](https://github.com/DippyCoder/MCPanel-WebUI) immediately.

> ### ⚠ The default login is `admin` / `admin`
>
> It is created the first time anything touches the addon, and it is flagged
> `mustChangePassword`. **Change it before the panel is reachable from anywhere
> but localhost:**
>
> ```bash
> mcpanel accounts passwd -u admin -p "a real password"
> ```

---

## Where the data lives

```
<userData>/addon-data/accounts/accounts.db
```

`<userData>` is MCPanel's shared data directory — `~/.config/mcpanel` on Linux,
`%APPDATA%\mcpanel` on Windows, `~/Library/Application Support/mcpanel` on
macOS. `MCPANEL_HOME` overrides it.

- **Passwords** are stored as PBKDF2-HMAC-SHA256 with a 32-byte random salt and
  a high iteration count — never plaintext, never reversible. A password is
  transparently re-hashed at the current cost the next time it authenticates
  against an older record.
- **Session tokens** are stored as a SHA-256 hash of the token, not the token
  itself. A stolen database yields no usable sessions.
- The database is opened in WAL mode with a busy timeout, because the CLI is
  invoked concurrently — the WebUI verifies a session on essentially every
  request.

---

## Command reference

### Accounts

| Command | What it does |
|---------|--------------|
| `accounts init [--force]` | Create the database, the builtin roles, and the default `admin` account. Runs automatically on first use. `--force` re-seeds the builtin roles (repairing a damaged `admin` role) but never resets a password |
| `accounts list` | List every account: role, status, last login, permission count |
| `accounts create -u <user> -p <pass> [-r <role>] [--perms <a,b,c>] [--disabled] [--must-change-password]` | Create an account |
| `accounts update -u <user> [-r <role>] [--perms <a,b,c>] [--clear-role] [--enable] [--disable]` | Change an account's role, extra permissions or status |
| `accounts passwd -u <user> -p <new> [--current <old>]` | Set a password. With `--current` this is the self-service path and the old password must match |
| `accounts delete -u <user>` | Delete an account |

### Sessions

| Command | What it does |
|---------|--------------|
| `accounts login -u <user> -p <pass> [--ttl <hours>] [--ua <string>] [--ip <address>]` | Authenticate and open a session; returns the token and its expiry |
| `accounts verify -t <token>` | `{valid, user}` — the call the WebUI makes on every request |
| `accounts whoami -t <token>` | Like `verify`, but errors on an invalid token |
| `accounts logout -t <token>` | Revoke one session |
| `accounts sessions list [-u <user>]` | List active sessions (tokens are shown truncated, never in full) |
| `accounts sessions revoke [-t <token>] [-u <user>] [--all]` | Revoke one session, one account's sessions, or every session |

### Roles, permissions and settings

| Command | What it does |
|---------|--------------|
| `accounts roles list` | List roles with their permission counts |
| `accounts roles create -n <role> [--desc <text>] [--perms <a,b,c>]` | Create a role |
| `accounts roles update -n <role> [--desc <text>] [--perms <a,b,c>]` | Change a role |
| `accounts roles delete -n <role>` | Delete a role (builtin roles are protected) |
| `accounts perms` | List every permission MCPanel understands, grouped by area |
| `accounts settings [--set <key=value>]` | Read or change install-wide settings (`--set` is repeatable) |

Every command works under `mcpanel api …` for raw JSON:

```bash
mcpanel api accounts login -u admin -p admin
# {"success": true, "token": "…", "expiresAt": 1788037153509, "user": {…}}
```

### Examples

```bash
# An operator who can run servers but not create or delete them
mcpanel accounts create -u steve -p "correct horse" -r operator

# A viewer who is additionally allowed to restart things
mcpanel accounts create -u alex -p "battery staple" -r viewer --perms servers.start,servers.stop

# Promote someone, then force a password change on next login
mcpanel accounts update -u steve -r admin
mcpanel accounts create -u temp -p onboarding -r viewer --must-change-password

# Lock an account without deleting it (its sessions are dropped immediately)
mcpanel accounts update -u steve --disable

# Self-service password change (verifies the old one first)
mcpanel accounts passwd -u steve -p "new password" --current "correct horse"

# Kick every session on the install
mcpanel accounts sessions revoke --all
```

---

## The permission model

Permissions are `<area>.<action>` strings — `servers.start`, `files.write`,
`backups.restore`. `mcpanel accounts perms` prints the full catalogue.

Two wildcards are understood **in a grant** (a role's or an account's permission
list). A permission *check* always names one concrete string:

| Grant | Means |
|-------|-------|
| `*` | every permission |
| `servers.*` | every permission in the `servers` area |
| `servers.start` | exactly that one |

An account's **effective permissions** are the union of:

1. the permissions of its **role**, if it has one, and
2. its own **extra permissions** (`--perms`)

So a role sets the baseline and per-account extras top it up. There is no
subtractive grant — to give someone less than their role, put them on a
narrower role.

```
role: viewer          → servers.view, servers.console, files.read, …
--perms servers.start → plus servers.start
                      ────────────────────────────────────────────
effective             → the viewer set, plus servers.start
```

Unknown permission strings are rejected when you set them, naming the offending
entry, so a typo can't silently grant nothing.

### Builtin roles

Three roles are created by `init` and cannot be deleted.

**`admin`** — *Full control over everything, including accounts.*
Holds `*`. Always holds `*`: the role cannot be deleted, renamed, or have its
permissions reduced, so an install can never lock itself out of its own account
management.

**`operator`** — *Run and maintain servers, but not create or delete them.*

```
self.password
servers.view      servers.start     servers.stop
servers.console   servers.command
files.read        files.write       files.upload      files.download
profiles.view
plugins.view      plugins.install
backups.view      backups.create
schedules.view
themes.view
system.view       settings.view
```

**`viewer`** — *Read-only: watch consoles and browse files, change nothing.*

```
self.password
servers.view      servers.console
files.read        files.download
profiles.view
plugins.view
backups.view
schedules.view
themes.view
system.view       settings.view
```

Note what neither non-admin role gets: `servers.create`, `servers.delete`,
`files.delete`, `backups.restore`, `backups.delete`, `schedules.manage`,
`proxy.manage`, `themes.manage`, `settings.manage`, `accounts.*` — and in
particular **`terminal.access`** and **`cli.raw`**, which are excluded
deliberately. Both amount to root on the host machine: one opens a shell, the
other runs arbitrary CLI commands. Grant them only to people you would give an
SSH key.

---

## Global settings

Install-wide switches that apply on top of an account's own grants. Where a
setting gates a permission, a user needs **both** — so a capability can be
revoked fleet-wide without editing every account.

| Setting | Default | What it does |
|---------|---------|--------------|
| `allow_self_password_change` | `true` | Gates `self.password`. When false, only someone with `accounts.manage` can change any password, including a user's own |
| `session_ttl_hours` | `720` | How long a login stays valid (30 days). `0` is rejected — sessions live in SQLite, so there is no "until restart" |
| `min_password_length` | `4` | Enforced on every password change. The seeded `admin` password predates it and is not retroactively rejected |
| `revoke_sessions_on_disable` | `true` | Disabling an account drops its sessions immediately instead of letting them expire |

```bash
mcpanel accounts settings
mcpanel accounts settings --set allow_self_password_change=false
mcpanel accounts settings --set session_ttl_hours=24 --set min_password_length=12
```

Values parse as JSON first and fall back to a string, so `false`, `24` and
`"text"` all do what you would expect.

Changing a password always revokes that account's *other* sessions.

---

## The lockout guard

The addon refuses any operation that would leave the install with no way to
manage its own accounts. The **last enabled account holding `accounts.manage`**
cannot be:

- deleted
- disabled
- moved to a role without `accounts.manage`

The refusal says exactly why. Create a second admin first if you genuinely want
to remove the current one.

---

## Notes

- `login` returns the same `Invalid username or password` for an unknown account
  and a wrong password, and does a dummy hash verification when the account does
  not exist, so the endpoint cannot be used to enumerate usernames by response
  or by timing.
- Passwords never appear in output, and password hashes are never included in
  any JSON payload.
- `accounts sessions list` shows truncated token prefixes only.
- Addons are not sandboxed — see [ADDONS.md](../../../ADDONS.md). This one runs
  with the privileges of whoever runs `mcpanel`.
