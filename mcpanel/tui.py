"""Interactive terminal GUI for MCPanel.

WIP — per the project plan this is a placeholder. For now it renders a simple
read-only dashboard of your servers so the entry point does something useful.
The full TUI (server controls, live console, profile/theme management) will be
built on top of the same controllers used by the CLI commands.
"""

from . import servers, render


def run():
    print(render.bold("MCPanel — interactive console") + render.dim("  (WIP)"))
    print(render.dim("This GUI is not finished yet. Use the `mcpanel <command>` interface for now."))
    print()
    result = servers.list_servers(None)
    render.render("list-servers", result, None)
    print()
    print(render.dim("Try:  mcpanel --help   |   mcpanel list servers   |   mcpanel api fetch config"))
