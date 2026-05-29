<div align="center">
  <img src="public/banner.svg" alt="MCPanel Banner" width="860"/>
</div>

<div align="center">

[![Download](https://img.shields.io/badge/releases-blue?label=download&style=for-the-badge&colorA=19201a&colorB=7B2FBE)](https://github.com/DippyCoder/MCPanel-CLI/releases)⠀
[![Source](https://img.shields.io/badge/source-code?label=source&style=for-the-badge&colorA=19201a&colorB=7B2FBE)](https://github.com/DippyCoder/MCPanel-CLI)
[![Discord](https://img.shields.io/badge/discord-join-blue?style=for-the-badge&colorA=19201a&colorB=7B2FBE)](https://discord.gg/xe5BPEd6JA)
[![License](https://img.shields.io/badge/license-GPL--3.0-blue?style=for-the-badge&colorA=19201a&colorB=7B2FBE)](LICENSE)
</div>

A 1:1 replica of [MCPanel](https://github.com/DippyCoder/MCPanel), but with a CLI instead of running Electron.
This Project uses Python instead of HTML, CSS, JS and Electron.

There are three faces to the same engine:

| Surface | Example | Output |
|---------|---------|--------|
| **Human commands** | `mcpanel create server -t "test server" -ram 20480 -sw paper -v 1.21.1` | pretty, colourised |
| **API commands** | `mcpanel api fetch server -id srv_123` | **raw JSON** on stdout |
| **Interactive GUI** | `mcpanel cli` | a TUI (work in progress) |

The `api` family exists so the terminal itself can be the backend for a future
MCPanel UI: anything the GUI needs, it gets as JSON from `mcpanel api …`.

---

## 🚀 Install

**Dependencies:** Python 3.8+ and Java (to actually run Minecraft servers).

```bash
./install.sh           # symlink bin/mcpanel into ~/.local/bin (no build needed)
./install.sh --pip     # or: pip install --user .  (proper console-script)
./install.sh --uninstall
```

No install required either — run it straight from the checkout:

```bash
./bin/mcpanel --help
python3 -m mcpanel --help
```

Data lives in `~/.config/MCPanel/` (same place the desktop app uses). Override
with the `MCPANEL_HOME` environment variable.

---

## ⚡ Quick start

```bash
# Create a Paper 1.21.1 server with 20 GB RAM and accept the EULA
mcpanel create server -t "test server" -ram 20480 -sw paper -v 1.21.1 --accept-eula

# List servers, then start one
mcpanel list servers
mcpanel start server -id srv_1700000000000

# Watch the live console (Ctrl-C to detach)
mcpanel console server -id srv_1700000000000

# Send a console command
mcpanel cmd server -id srv_1700000000000 -c "say hello"

# Check who's online via server-list-ping
mcpanel ping server -id srv_1700000000000

# Stop it gracefully
mcpanel stop server -id srv_1700000000000
```

`-ram` takes plain **megabytes** (e.g. `20480`) or a suffixed value (`20G`).

---

## 📟 Command reference

### Servers
| Command | What it does |
|---------|--------------|
| `create server -t <name> -sw <software> -v <version> [-ram <MB>] [-port <n>] [-profile <id>] [-java <path>] [-jargs "<args>"] [-storage <limit>] [--unstable] [--accept-eula]` | Create + download a server |
| `list servers` | List all servers + online status |
| `info server -id <id>` | Full server details |
| `update server -id <id> [-port -ram -t -sw -v -java -jargs -storage]` | Change settings (port rewrites `server.properties`) |
| `delete server -id <id>` | Delete server + files |
| `duplicate server -id <id> -t <newName>` | Copy a server |
| `import server -path <folder> -t <name> [-port -ram -sw -v -java -jargs]` | Adopt an existing server folder |
| `start \| stop \| restart \| kill server -id <id>` | Lifecycle (`start`/`restart` accept `--accept-eula`) |
| `cmd server -id <id> -c "<command>"` | Send a console command |
| `logs server -id <id> [-f]` / `console server -id <id>` | View / follow console output |
| `ping server -id <id>` (or `-host <h> -port <p>`) | Server-list-ping |
| `files server -id <id>` | File tree |
| `stats server -id <id>` | Disk usage |
| `accept-eula server -id <id>` | Write `eula=true` |
| `scan server -path <folder>` | Detect port/software before importing |
| `open server -id <id>` | Open folder in file manager |

### Profiles (server presets)
`create profile`, `list profiles`, `info profile`, `delete profile`,
`import profile`, `create profile-from-server`, `scan profile`, `open profile`.

```bash
mcpanel create profile -t "Survival" -desc "essentials" -sw paper,purpur
# then drop plugins/, config/, server.properties into the printed folder
```

### Versions
```bash
mcpanel versions -sw paper            # paper|purpur|velocity|fabric|vanilla|leaf|folia|spigot
mcpanel versions -sw vanilla --prerelease
mcpanel versions -sw paper --unstable
```
Sources: PaperMC API (paper/folia/velocity), Purpur API, Mojang manifest
(vanilla), FabricMC meta, GitHub releases (leaf). Spigot is a static list
(needs BuildTools to actually build).

### Themes
`list themes`, `install theme -url <zip>` / `install theme -file <zip>`,
`apply theme -id <id|none>`, `delete theme -id <id>`, `browse themes`.
Ships with **Dark Slate** and **Bright Slate**.

### System
`detect-jdk`, `system`, `version`, `check-update`, `config show`, `config path`.

---

## 🤖 API mode (terminal as backend)

Prefix **any** command with `api` to get raw JSON instead of formatted text —
ideal for scripting or wiring a UI on top:

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
```

`api` getters available under `fetch`: `server`, `profile`, `config`, `log`,
`files`, `stats`, `status`, `system`, `update`, `jdk`, `themes`, `versions`,
`css`. Errors come back as `{"error": "..."}` with a non-zero exit code.

---

## 🏗️ How running servers work

A CLI invocation is short-lived, so each running server is owned by a detached
**supervisor** process (`python -m mcpanel.supervisor <id>`). It launches Java,
streams stdout/stderr to a structured log, and exposes a Unix control socket so
`cmd` / `stop` / `kill` from any later invocation reach the live process.
Runtime state lives under `~/.config/MCPanel/run/`.

---

## 📂 Layout

```
mcpanel/
├── cli.py          ← argparse tree (human + api + cli) and dispatch
├── servers.py      ← server controllers (create/start/stop/…)
├── profiles.py     ← profile controllers
├── themes.py       ← theme controllers (+ bundled defaults)
├── versions.py     ← version listing + download-URL resolution
├── supervisor.py   ← detached daemon that runs one server
├── runstate.py     ← client helpers for talking to supervisors
├── ping.py         ← Minecraft server-list-ping
├── system.py       ← JDK detection, system info, update check
├── render.py       ← human-readable output
├── http.py · util.py · config.py · paths.py
└── bundled_themes/ ← Dark Slate / Bright Slate
```

---

## 🔧 Notes

- **Spigot** needs [BuildTools](https://www.spigotmc.org/wiki/buildtools/) — the
  server folder is created but no JAR is downloaded.
- **Fabric** downloads the server-side loader JAR from FabricMC.
- Match your Java version to the Minecraft version (1.20.5+ needs Java 21).
- The JSON shapes from `api` mirror the original Electron IPC return values 1:1.

THE PROJECT IS CURRENTLY IN EARLY-DEVELOPMENT! BUGS MAY OCCUR
