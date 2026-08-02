# MCPanel-CLI Addon API

Addons extend the CLI with new command groups. Because the CLI *is* MCPanel's
backend, an addon that adds `mcpanel api foo bar` automatically becomes callable
from the desktop app and the WebUI too — there is no second integration to write.

The accounts/permissions system that MCPanel-WebUI depends on is itself an addon
(`mcpanel-addon-accounts`), so this API is exercised by a real consumer.

---

## Where addons live

| Source | Location | Notes |
| ------ | -------- | ----- |
| User-installed | `<userData>/addons/<name>.py` | single-file addon |
| User-installed | `<userData>/addons/<name>/__init__.py` | package addon |
| pip-installed | any distribution exposing the `mcpanel.addons` entry-point group | preferred for real addons |

`<userData>` is the same directory the panel uses — `~/.config/mcpanel` on
Linux, `%APPDATA%\mcpanel` on Windows, `~/Library/Application Support/mcpanel`
on macOS. Override with `MCPANEL_HOME`.

Enable/disable state lives in `<userData>/addons.json`:

```json
{ "disabled": ["some-addon"] }
```

An addon absent from `disabled` is enabled. Disabling never deletes anything.

---

## What an addon module must expose

```python
ADDON = {
    "name":        "accounts",          # required, unique, kebab-case
    "version":     "1.0.0",             # required
    "description": "User accounts and permissions",
    "author":      "DippyCoder",
    "url":         "https://github.com/...",
    "api_version": 1,                   # required — see "Versioning"
}


def register(api):
    """Called once per parser tree. Mount commands here."""
    ...
```

`register()` is called **twice** per process — once for the human command tree
and once for the `api` (JSON) tree — because the CLI mounts one tree twice. Do
not keep state between calls; treat `register()` as pure command wiring.

Optional module-level hooks, all called at most once per process:

```python
def on_load():     ...   # after import, before any command runs
def on_startup():  ...   # after the CLI's own startup scan, before dispatch
```

An exception in `on_load` / `on_startup` disables that addon for the rest of
the process and is reported by `mcpanel addons list`; it never takes the CLI
down with it.

---

## The `api` object

```python
def register(api):
    grp = api.group("accounts", help="user accounts and permissions")

    p = api.command(grp, "login", do_login, "accounts-login",
                    help="authenticate and open a session")
    p.add_argument("-u", "--user", dest="username", required=True)
    p.add_argument("-p", "--password", dest="password", required=True)

    api.renderer("accounts-login", render_login)
```

### `api.group(name, help=None) -> subparsers`

Adds a top-level command group and returns its subparsers object, ready to hand
to `api.command()`. Use one group per addon; nest with
`api.group(name, parent=grp)` when a second level is genuinely needed.

### `api.command(parent, name, func, action, help=None, progress_ok=False) -> ArgumentParser`

Registers a leaf command. Returns the `ArgumentParser` so you can add arguments.

- `func(args, progress) -> dict` — the handler. `progress` is `None` unless
  `progress_ok=True` and the command is running in human mode; when it is
  callable, call `progress(percent, status)`.
- `action` — a unique string identifying this command. It is what
  `api.renderer()` keys on, and it appears in `args.action`.
- Return a plain JSON-serialisable `dict`. Returning `{"error": "..."}` makes
  the CLI exit non-zero, exactly as for built-in commands.
- Raising an exception is also fine: the CLI prints `{"error": str(e)}` in JSON
  mode and a red `✗ message` in human mode.

### `api.renderer(action, fn)`

Registers the human-readable printer for an `action`. `fn(result, args)` prints
whatever it likes. Without one, the CLI falls back to a generic pretty-printer,
so a renderer is optional — JSON mode never uses it.

### `api.helpers`

The CLI's own modules, so addons don't re-import private paths:
`paths`, `config`, `runstate`, `render`, `util`.

`api.helpers.render` gives you the same colour helpers the built-ins use
(`render.green`, `render.red`, `render.dim`, `render.table`, …), so addon output
looks native.

### `api.data_dir(name) -> str`

Returns (and creates) `<userData>/addon-data/<name>/` — where an addon should
put its database, cache or config. Never write outside it.

---

## Versioning

`api_version` is the addon API contract number, currently **1**. The CLI
refuses to load an addon declaring an `api_version` it does not implement, and
says so in `mcpanel addons list` rather than failing silently.

---

## Managing addons

```
mcpanel addons list                    # name, version, source, enabled, status
mcpanel addons info <name>
mcpanel addons enable <name>
mcpanel addons disable <name>
mcpanel addons install <path|url>      # copy a .py / directory / .zip into <userData>/addons
mcpanel addons remove <name>           # user-installed addons only
```

`mcpanel api addons list` returns the same data as JSON.

---

## Failure policy

Addons are third-party code running inside the backend that manages people's
servers, so the loader is defensive by design:

- an addon that fails to import is skipped, recorded with its traceback, and
  reported by `addons list` — the CLI still starts
- an addon whose `register()` raises is skipped the same way, and any commands
  it managed to mount before raising are left in place only if they are
  complete
- a duplicate `ADDON["name"]` loses to whichever addon loaded first, and the
  conflict is reported
- `MCPANEL_NO_ADDONS=1` disables addon loading entirely — the escape hatch when
  a bad addon makes the CLI unusable

Addons are **not** sandboxed. An addon can do anything the user running
`mcpanel` can do. Install addons you trust.
