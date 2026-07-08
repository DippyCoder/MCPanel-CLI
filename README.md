<div align="center">
  <img src="public/banner.svg" alt="MCPanel Banner" width="860"/>
</div>

<div align="center">

[![Download](https://img.shields.io/badge/releases-blue?label=download&style=for-the-badge&colorA=19201a&colorB=7B2FBE)](https://github.com/DippyCoder/MCPanel-CLI/releases)⠀
[![Source](https://img.shields.io/badge/source-code?label=source&style=for-the-badge&colorA=19201a&colorB=7B2FBE)](https://github.com/DippyCoder/MCPanel-CLI)
[![Discord](https://img.shields.io/badge/discord-join-blue?style=for-the-badge&colorA=19201a&colorB=7B2FBE)](https://discord.gg/xe5BPEd6JA)
[![License](https://img.shields.io/badge/license-GPL--3.0-blue?style=for-the-badge&colorA=19201a&colorB=7B2FBE)](LICENSE)

</div>

A CLI featuring all [MCPanel](https://github.com/DippyCoder/MCPanel) features, but as a CLI.
Runs on **Windows, macOS, and Linux** with no native dependencies beyond Python and Java.

Three faces to the same engine:

| Surface | How to use | Output |
|---------|-----------|--------|
| **Human commands** | `mcpanel create server -t "test" -sw paper -v 1.21.1` | pretty, colourised |
| **API commands** | `mcpanel api fetch server -id srv_123` | raw JSON on stdout |
| **Interactive TUI** | `mcpanel cli` | full REPL with tab completion + guided wizards |

---

## Install

**Dependencies:** Python 3.8+ · [`prompt_toolkit`](https://pypi.org/project/prompt-toolkit/) (auto-installed) · Java (to run Minecraft servers)

**Linux / macOS**
```bash
./install.sh           # symlink bin/mcpanel into ~/.local/bin (no build needed)
./install.sh --pip     # pip install --user .  (proper console-script entry)
./install.sh --uninstall
```

**Windows** (PowerShell)
```powershell
.\install.ps1          # pip install --user (recommended)
.\install.ps1 -Uninstall
```

No install required — run straight from the checkout:

```bash
# Linux / macOS
./bin/mcpanel --help
python3 -m mcpanel --help

# Windows
python bin\mcpanel --help
python -m mcpanel --help
```

Data directory (same location the desktop app uses):

| Platform | Path |
|----------|------|
| Windows  | `%APPDATA%\mcpanel\` |
| macOS    | `~/Library/Application Support/mcpanel/` |
| Linux    | `~/.config/mcpanel/` |

Override with the `MCPANEL_HOME` environment variable.

---

## Interactive TUI (`mcpanel cli`)

```
mcpanel › /create server
```

- **Commands start with `/`** — `/create server`, `/list servers`, `/start server`, …
- **Tab completion** — completes command names, nouns, flag names, server IDs, and software names, with a usage hint shown inline for each match
- **Live usage tooltip** — the bottom toolbar updates as you type, showing the full syntax for the current command
- **Guided wizards** — enter a command without arguments and you get prompted for each one interactively (name, software picker, version picker, RAM, port, EULA)
- **Arrow-key pickers** — selecting a server, software, or version shows a scrollable inline list; ↑↓ to navigate, Enter to confirm, Esc to cancel
- **Command history** — ↑↓ recalls previous commands
- **`/exit`** — leave the TUI and return to the terminal (servers keep running in the background)
- **`/shutdown`** — kill all running servers then exit
- **`/backup create|list|delete|restore`** and **`/buildtools version|update`** — same features as the command-line, with guided pickers for server/backup selection
- **`/discover`** — re-scan `servers/` for folders dropped in since the TUI started (the one-shot CLI already does this on every startup)

```
mcpanel › /create server
  Select software:
  ❯ paper
    purpur
    velocity
    ...
  ↑↓ navigate   Enter select   Esc cancel
```

Type `/help` inside the TUI for the full command list.

---

## Quick start (command-line)

```bash
# Create a Paper 1.21.1 server with 20 GB RAM and accept the EULA
mcpanel create server -t "my server" -ram 20480 -sw paper -v 1.21.1 --accept-eula

# List servers, then start one
mcpanel list servers
mcpanel start server -id srv_1700000000000

# Watch the live console (Ctrl-C to detach)
mcpanel console server -id srv_1700000000000

# Send a console command
mcpanel cmd server -id srv_1700000000000 -c "say hello"

# Check who's online via server-list-ping
mcpanel ping server -id srv_1700000000000

# Stop gracefully
mcpanel stop server -id srv_1700000000000
```

`-ram` takes plain **megabytes** (`20480`) or a suffixed value (`20G`).

---

## Command reference

### Servers
| Command | What it does |
|---------|--------------|
| `create server -t <name> -sw <software> -v <version> [-ram <MB\|nG>] [-port <n>] [-profile <id>] [-java <path>] [-jargs "<args>"] [-storage <limit>] [--unstable] [--accept-eula]` | Create + download a server |
| `list servers` | List all servers + online status |
| `info server -id <id>` | Full server details |
| `update server -id <id> [-t -ram -port -sw -v -java -jargs -storage]` | Change settings (port rewrites `server.properties`) |
| `delete server -id <id>` | Delete server + files |
| `duplicate server -id <id> -t <newName>` | Copy a server |
| `import server -path <folder> -t <name> [-port -ram -sw -v -java -jargs]` | Adopt an existing server folder |
| `start \| stop \| restart \| kill server -id <id>` | Lifecycle (`start`/`restart` accept `--accept-eula`) |
| `cmd server -id <id> -c "<command>"` | Send a console command |
| `logs server -id <id> [-f] [-n <N>]` / `console server -id <id>` | View / follow console output |
| `sessions server -id <id>` | List archived log sessions |
| `ping server -id <id>` (or `-host <h> -port <p>`) | Server-list-ping |
| `files server -id <id>` | File tree |
| `stats server -id <id>` | Disk usage |
| `accept-eula server -id <id>` | Write `eula=true` |
| `scan server -path <folder>` | Detect port/software before importing |
| `open server -id <id>` | Open folder in file manager |

### Proxy (Velocity)
| Command | What it does |
|---------|--------------|
| `proxy info --velocity-id <id>` | List servers registered in a Velocity proxy config |
| `proxy link -id <id> --velocity-id <id> [--server-name <name>] [--priority <pos>] [--custom-ip <ip:port>]` | Link a Paper-based server into a Velocity proxy (writes `paper-global.yml` + `velocity.toml`) |

### Profiles (server presets)
| Command | What it does |
|---------|--------------|
| `create profile -t <name> [-desc <text>] [-sw <a,b>] [-versions <a,b>]` | Create a blank profile |
| `create profile-from-server -id <id> -t <name> -paths <rel,paths> [-desc <text>] [-sw <sw>] [-versions <ver>]` | Snapshot files from a running server into a new profile |
| `list profiles` | List all profiles |
| `info profile -id <id>` | Show profile details |
| `delete profile -id <id>` | Delete a profile |
| `import profile -path <folder> -t <name> [-desc <text>] [-sw <sw>] [-versions <ver>]` | Import an existing profile folder |
| `scan profile -path <folder>` | Read metadata from a profile folder |
| `open profile -id <id>` | Open profile folder in file manager |

```bash
mcpanel create profile -t "Survival" -desc "essentials" -sw paper,purpur
# then drop plugins/, config/, server.properties into the printed folder

mcpanel create profile-from-server -id srv_1700000000000 -t "my preset" -paths "plugins,config"
```

### Versions
```bash
mcpanel versions -sw paper            # paper|purpur|velocity|fabric|vanilla|leaf|folia|spigot
mcpanel versions -sw vanilla --prerelease
mcpanel versions -sw paper --unstable
```

Sources: PaperMC API (paper/folia/velocity), Purpur API, Mojang manifest (vanilla), FabricMC meta, GitHub releases (leaf). Spigot's version list is fetched live from `hub.spigotmc.org/versions/` (the same metadata directory BuildTools itself reads).

### Plugins & mods
| Command | What it does |
|---------|--------------|
| `search plugins \| mods <modrinth\|hangar\|spigotmc> [query] [-id <serverid>] [-v <MC version>] [-sw <software>] [-n <limit>] [-o <offset>]` | Search a platform for plugins/mods |
| `install plugin \| mod <modrinth\|hangar\|spigotmc> <slug> -id <serverid> [-v <MC version>] [--owner <owner>] [--version <version>]` | Download + drop a plugin/mod into a server's `plugins/`/`mods/` folder (or `--profile-id <id>` to install into a profile instead) |
| `info plugin <modrinth\|hangar\|spigotmc> <slug> [--owner <owner>]` | Version history + website link for a plugin/mod |

```bash
mcpanel search plugins modrinth luckperms -id srv_1700000000000
mcpanel install plugin modrinth luckperms -id srv_1700000000000
mcpanel install mod hangar someproject --owner someowner -id srv_1700000000000
```

`-id`/`-v` auto-detect the target Minecraft version from the server so results are pre-filtered to what's actually compatible. Hangar projects need `--owner` whenever the slug alone is ambiguous. Fabric servers install into `mods/`; everything else installs into `plugins/`.

### Backups
| Command | What it does |
|---------|--------------|
| `backup create -id <id>` | Zip the server folder (logs excluded) into `<data dir>/backups/<id>/backup_<timestamp>.zip` |
| `backup list -id <id>` | List backups for a server |
| `backup restore -id <id> -name <filename>` | Restore a server from a backup zip (overwrites current files) |
| `backup delete -id <id> -name <filename>` | Delete a specific backup |

### BuildTools (Spigot)
Spigot ships no prebuilt jars — creating a Spigot server compiles one locally with SpigotMC's BuildTools. The CLI keeps its own copy of `BuildTools.jar` (downloaded once, in the CLI's own install directory, never overwritten after that) and runs it automatically the first time it's needed.

| Command | What it does |
|---------|--------------|
| `buildtools version` | Show whether BuildTools is installed (downloads it if missing) |
| `buildtools update` | Force `BuildTools.jar` to re-download now |
| `fetch jdk-compat -sw <software> -v <version>` | Which detected JDKs can actually build/run a given software + version — BuildTools enforces an exact compile-time Java range, so this tells you upfront instead of failing after minutes of building |

### Server discovery
Every server folder carries a `mcpanel.json` manifest (a copy of its config entry, minus the machine-specific path). Drop a server folder — or a folder restored from a backup — straight into the `servers/` data directory and it self-registers automatically:

```bash
mcpanel discover          # re-scan servers/ for folders not yet registered; also runs on every CLI startup
```

### Shell completion
```bash
eval "$(mcpanel completion bash)"     # add to ~/.bashrc
eval "$(mcpanel completion zsh)"      # add to ~/.zshrc
```
Completes command names, nouns, `-sw`/platform values, and live server IDs (via `mcpanel api list servers`).

### System
`detect-jdk` (now flags JRE-only installs via `hasCompiler`), `fetch jdk-compat`, `system`, `version`, `check-update`, `config show`, `config path`.

### Shutdown
```bash
mcpanel shutdown          # kill all running servers and stop MCPanel
```
Also available as `/shutdown` inside `mcpanel cli` (servers keep running if you use `/exit` instead).

---

## 🤖 API mode (terminal as backend)

Prefix **any** command with `api` to get raw JSON — ideal for scripting or wiring a UI on top:

```bash
mcpanel api fetch server  -id srv_123      # one server (+ running flag)
mcpanel api list servers                    # { "servers": [...] }
mcpanel api fetch config                    # the whole config.json
mcpanel api fetch versions -sw paper        # { "versions": [...] }
mcpanel api fetch log     -id srv_123       # [{time,text,type}, ...]
mcpanel api fetch status  -id srv_123       # true / false
mcpanel api ping server   -id srv_123       # { online, players, ... }
mcpanel api create server -t x -sw paper -v 1.21.1   # { success, server }
mcpanel api system                          # { totalRam, availableStorage }
mcpanel api search plugins modrinth luckperms -id srv_123   # { results, hasMore }
mcpanel api backup list -id srv_123         # { backups: [...] }
mcpanel api fetch jdk-compat -sw spigot -v 1.21.1   # { range, jdks, recommended }
```

Errors come back as `{"error": "..."}` with a non-zero exit code.

---

## How running servers work

A CLI invocation is short-lived, so each running server is owned by a detached
**supervisor** process (`python -m mcpanel.supervisor <id>`). It launches Java,
streams stdout/stderr to a structured log, and exposes a control socket (Unix
domain socket on Linux/macOS, TCP loopback on Windows) so `cmd` / `stop` /
`kill` from any later invocation reach the live process.
Runtime state lives in the `run/` subfolder of the data directory.

---

## Layout

```
mcpanel/
├── cli.py          ← argparse tree (human + api) and dispatch
├── tui.py          ← interactive REPL (mcpanel cli)
├── servers.py      ← server controllers (create/start/stop/…)
├── profiles.py     ← profile controllers
├── plugins.py      ← plugin/mod search + install (Modrinth, Hangar, Spiget)
├── backup.py       ← server backup create/list/restore/delete (zip)
├── buildtools.py   ← SpigotMC BuildTools download + compile-on-create for Spigot
├── versions.py     ← version listing + download-URL resolution
├── supervisor.py   ← detached daemon that owns one running server
├── runstate.py     ← client helpers for talking to supervisors
├── ping.py         ← Minecraft server-list-ping
├── system.py       ← JDK detection (+ compatibility ranges), system info, update check
├── render.py       ← human-readable output + ANSI helpers
└── http.py · util.py · config.py · paths.py
```

`config.py` also owns each server's `mcpanel.json` manifest (see [Server discovery](#server-discovery)) — written on every create/update/duplicate/import so a server folder can be moved between installs or restored from backup and re-register itself.

---

## Notes

- **Spigot** is compiled locally via [BuildTools](https://www.spigotmc.org/wiki/buildtools/), which the CLI downloads and runs automatically on first use — this requires a full JDK (not a JRE-only install) in the version BuildTools expects for that Minecraft version; run `mcpanel fetch jdk-compat -sw spigot -v <version>` to check first.
- **Fabric** downloads the server-side loader JAR from FabricMC.
- Match your Java version to the Minecraft version (1.20.5+ needs Java 21) — `detect-jdk` and `fetch jdk-compat` both report this per-JDK now.
- The JSON shapes from `api` mirror the original Electron IPC return values 1:1.
- The interactive TUI requires `prompt_toolkit` (installed automatically via pip). On terminals without ANSI support the TUI degrades gracefully to plain text.