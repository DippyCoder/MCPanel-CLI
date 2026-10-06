"""The CLI's error contract — one place that defines what an API error looks like.

Every failure the `api` surface reports has the same shape:

    {"error": "<human-readable message>", "code": "<stable_snake_case_code>", ...extra}

* ``error`` is always the full, ready-to-display sentence. Clients (the MCPanel
  desktop app, MCPanel-WebUI) show it verbatim and never map codes to their own
  text — so an error introduced by a newer CLI still reads correctly in an older
  app that has never heard of its code.
* ``code`` is for *logic* only (retry, highlight a field, offer a fix button).
  Codes are stable: once shipped, a code keeps its meaning. Unknown codes must
  be treated like the generic ``error``.
* Extra keys (``failedStep``, ``rolledBack``, …) are optional detail.

`mcpanel api errors` lists the catalogue below plus every code an addon
registered, so a client *can* discover codes — but never needs to.
"""

# code -> default message, used when a call site doesn't supply its own text.
# Keep messages self-contained sentences; call sites usually pass a more
# specific message that includes names/paths.
CATALOG = {
    # ── generic ──────────────────────────────────────────────────────────────
    "error": "Something went wrong.",
    "internal_error": "MCPanel-CLI hit an unexpected internal error.",
    "operation_failed": "The operation failed.",
    "invalid_arguments": "The command was called with invalid arguments.",
    "unknown_command": "Unknown command. It may come from an addon that isn't installed or enabled.",
    "interrupted": "The operation was interrupted.",
    "data_dir_unwritable": "MCPanel's data directory can't be created or written.",
    "invalid_id": "That id is not valid.",
    "invalid_path": "That path is not valid here.",

    # ── servers ──────────────────────────────────────────────────────────────
    "server_not_found": "Server not found.",
    "server_already_registered": "That folder is already registered as a server.",
    "server_dir_missing": "The server's folder is missing.",
    "server_running": "The server is running — stop it first.",
    "server_not_running": "The server is not running.",
    "server_already_running": "The server is already running.",
    "still_stopping": "The previous instance is still shutting down — try again in a moment.",
    "start_failed": "The server failed to start.",
    "start_timeout": "Timed out waiting for the server to start.",
    "stop_failed": "The server could not be stopped.",
    "command_failed": "The command could not be sent to the server.",
    "storage_limit_exceeded": "The server is over its storage limit.",
    "jar_not_found": "No usable server jar was found.",
    "invalid_name": "A name is required.",
    "invalid_port": "The port must be between 1 and 65535.",
    "invalid_ram": "The RAM value is not valid — use megabytes (2048) or e.g. 4G.",
    "invalid_software": "Unknown server software.",
    "version_required": "A version is required.",
    "unknown_version": "That version is not available.",
    "versions_unavailable": "The version list could not be fetched.",
    "download_failed": "The download failed.",

    # ── profiles ─────────────────────────────────────────────────────────────
    "profile_not_found": "Profile not found.",

    # ── proxy (Velocity) ─────────────────────────────────────────────────────
    "not_paper": "Only Paper-based servers (Paper, Purpur, Folia, Leaf) can be linked to Velocity.",
    "velocity_not_found": "Velocity server not found.",
    "not_velocity": "The target is not a Velocity server.",
    "invalid_target": "A server cannot be linked to itself.",
    "velocity_toml_missing": "velocity.toml not found — start Velocity once to generate it.",
    "secret_missing": "The Velocity forwarding secret could not be read.",
    "invalid_server_name": "The proxy server name may only contain letters, digits, '-' and '_'.",
    "invalid_address": "The address must look like host:port.",
    "proxy_config_invalid": "A proxy config file could not be understood; nothing was changed.",
    "proxy_link_failed": "Linking to the proxy failed; changes were rolled back.",

    # ── backups ──────────────────────────────────────────────────────────────
    "backup_not_found": "Backup not found.",
    "invalid_backup_name": "That backup name is not valid.",
    "backup_corrupt": "The backup is corrupt; nothing was restored.",
    "backup_failed": "The backup could not be created.",
    "restore_failed": "The backup could not be restored.",

    # ── server log files ─────────────────────────────────────────────────────
    "log_not_found": "That log file does not exist.",
    "log_unreadable": "The log file could not be read.",
    "log_empty": "The log file is empty — nothing to upload.",
    "upload_failed": "The log could not be uploaded to mclo.gs.",

    # ── plugins / mods ───────────────────────────────────────────────────────
    "unknown_platform": "Unknown platform — use modrinth, hangar or spigotmc.",
    "install_target_required": "Choose a server (--id) or a profile (--profile-id) to install into.",
    "plugin_unavailable": "That plugin or mod can't be downloaded automatically.",

    # ── BuildTools / Java ────────────────────────────────────────────────────
    "buildtools_unavailable": "BuildTools could not be downloaded.",
    "build_failed": "BuildTools failed to build the server.",
    "java_not_found": "Java was not found.",
    "jdk_no_compiler": "The selected Java is a JRE without a compiler (javac).",

    # ── addon libraries (mcpanel mclib) ──────────────────────────────────────
    "disclaimer_required": "Installing third-party addons requires accepting the disclaimer (--yes).",
    "disclaimer_declined": "Cancelled — the third-party disclaimer was not accepted.",
    "mclib_unknown_library": "Unknown addon library.",
    "mclib_index_invalid": "The addon library's index is not valid.",
    "mclib_fetch_failed": "Could not reach the addon library or repository.",
    "mclib_addon_not_found": "That addon is not listed in the library.",
    "mclib_platform_not_allowed": "Addons may only be installed from GitHub or Codeberg.",
    "mclib_invalid_project": "That is not a repository URL.",
    "mclib_no_releases": "The addon's repository has no releases.",
    "mclib_no_archive": "The release has no downloadable archive.",
    "mclib_version_not_found": "That version does not exist.",
    "mclib_not_installed": "That addon is not installed from a library.",
    "mclib_update_failed": "Some addons could not be updated.",

    # ── addons ───────────────────────────────────────────────────────────────
    "addon_not_found": "No addon with that name.",
    "addon_install_failed": "The addon could not be installed.",
    "addon_not_removable": "That addon is not user-installed and can't be removed — disable it instead.",
}

# Codes registered by addons at runtime: code -> (message, addon name).
_ADDON_CODES = {}


class CLIError(Exception):
    """Raise anywhere below a command handler to fail with a specific code.
    main() turns it into the standard error document."""

    def __init__(self, code, message=None, **extra):
        self.code = code
        self.message = message or default_message(code)
        self.extra = extra
        super().__init__(self.message)

    def to_dict(self):
        return fail(self.code, self.message, **self.extra)


def default_message(code):
    if code in CATALOG:
        return CATALOG[code]
    if code in _ADDON_CODES:
        return _ADDON_CODES[code][0]
    return CATALOG["error"]


def fail(code, message=None, **extra):
    """The standard error document. `message` defaults to the catalogue text."""
    out = {"error": str(message) if message else default_message(code), "code": code}
    out.update(extra)
    return out


def register(codes, source):
    """Lets an addon declare its codes ({code: default message}) so they show
    up in `mcpanel api errors`. Built-in codes can't be redefined."""
    for code, message in (codes or {}).items():
        code = str(code)
        if code in CATALOG:
            continue
        _ADDON_CODES[code] = (str(message), source)


def from_exception(exc):
    """Error document for an exception that escaped a command handler. Any
    exception carrying a string `code` attribute (CLIError, an addon's own
    error class) keeps that code."""
    if isinstance(exc, CLIError):
        return exc.to_dict()
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return fail(code, str(exc) or None)
    return fail("internal_error", str(exc) or None)


def normalize(result):
    """Guarantee the contract on whatever a handler returned: an error
    document always has a string `error` and a `code`."""
    if isinstance(result, dict) and result.get("error"):
        if not isinstance(result["error"], str):
            result["error"] = str(result["error"])
        if not isinstance(result.get("code"), str) or not result.get("code"):
            result["code"] = "error"
    return result


def list_errors(args=None, progress=None):
    """Backs `mcpanel api errors`."""
    items = [{"code": c, "message": m, "source": "cli"} for c, m in CATALOG.items()]
    items += [{"code": c, "message": m, "source": src}
              for c, (m, src) in sorted(_ADDON_CODES.items())]
    return {"errors": items}
