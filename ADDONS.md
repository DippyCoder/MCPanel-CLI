# MCPanel-CLI Addon API

Addons extend the CLI with new command groups. Because the CLI *is* MCPanel's
backend, an addon that adds `mcpanel api foo bar` automatically becomes callable
from the desktop app and the WebUI too — there is no second integration to write.

The accounts/permissions system that MCPanel-WebUI depends on is itself an addon,
shipped separately as [MCPanel-Accounts](https://github.com/DippyCoder/MCPanel-Accounts)
(`mcpanel-addon-accounts` on pip), so this API is exercised by a real consumer.

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

### `api.errors(codes)` *(CLI 1.4.0+)*

Declares the addon's error codes as `{code: default message}`; they are listed
by `mcpanel api errors` next to the CLI's own. Guard it with
`hasattr(api, "errors")` if you also support older CLIs.

Every API error uses one shape:

```json
{"error": "Ready-to-show message", "code": "stable_snake_case_code"}
```

A handler fails either by **returning** that dict, or by **raising** any
exception that has a string `code` attribute — the CLI turns it into the same
document (an exception without one becomes `internal_error`). Always put a
complete sentence in `error`: MCPanel and MCPanel-WebUI display it verbatim
and only branch on `code`, which is what lets a new code you ship today show
up correctly in an app built before it existed. Once a code is published,
keep its meaning.

---

## Extending the MCPanel and MCPanel-WebUI interfaces *(CLI 1.4.0+)*

An addon can ship JavaScript and CSS that the desktop app and the WebUI load
into their own page: add sidebar pages, add server tabs, or change existing
pages. Declare the files in `ADDON`:

```python
ADDON = {
    "name": "hello", "version": "1.0.0", "api_version": 1,
    "ui": {
        "scripts": ["ui/main.js"],
        "styles":  ["ui/style.css"],
        "products": ["mcpanel", "webui"],      # default: both
        # optional per-product override: "webui": {"scripts": ["ui/web.js"]}
    },
}
```

Paths are relative to the addon folder (max 2 MB each). The apps fetch them
with `mcpanel api addons ui --product mcpanel|webui` at startup — after
installing or updating an addon, reload the app to pick up UI changes.

Each script runs in its own function scope with `addon` (`{name, version,
file}`) and the global `MCPanelAddons`:

```js
const A = window.MCPanelAddons;

// A new sidebar page. render() runs once, onShow() every time it's opened.
A.registerPage({
  id: 'hello', label: 'Hello', title: 'Hello', subtitle: `Running in ${A.product}`,
  render(el) { el.innerHTML = '<div class="card">…</div>'; },
});

// A new tab on the server detail page; render() gets the open server.
A.registerServerTab({
  id: 'hello', label: 'Hello',
  render(el, server) { el.textContent = `Server ${server.name}`; },
});

// Change existing pages: react when they're shown and edit their DOM.
A.on('page', (name) => { if (name === 'settings') { /* … */ } });
A.on('server-tab', (tab, serverId) => {});   // 'console', 'files', … or 'addon-<id>'
A.on('server-open', (serverId) => {});

// Call your own CLI commands (or any `mcpanel api …`); failures resolve as {error, code}.
const r = await A.cli(['hello', 'status']);
if (r.error) A.toast(r.error, 'error');
```

Also available: `A.product` (`'mcpanel'` | `'webui'`), `A.servers()`,
`A.currentServer()`, `A.showPage(name)`, `A.addStyle(css)`, `A.escapeHtml(s)`,
`A.openExternal(url)`, `A.off(event, fn)`, `A.apiVersion`.

**Theming.** Everything renders inside the panel's own DOM, so the user's
selected theme applies automatically. Use the theme's CSS variables rather than
fixed colours — `--bg-base`, `--bg-elevated`, `--bg-hover`, `--border`,
`--text-primary`, `--text-secondary`, `--text-muted`, `--accent`,
`--accent-dim`, `--accent-rgb`, `--green`, `--red`, `--orange`, `--radius`,
`--font-display`, `--font-mono` — and the panel's classes (`.card`,
`.btn-primary`, `.btn-ghost`, `.btn-sm`, `.input`, `.page-header`).

**Trust.** UI scripts run with the app's full privileges — in the WebUI, with
the signed-in user's session (the WebUI's permission checks still apply to
what they call). This is part of what the third-party disclaimer covers.

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
