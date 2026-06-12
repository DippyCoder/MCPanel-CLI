"""MCPanel CLI — a 1:1 terminal backend port of the MCPanel Electron app.

The Electron app drives a Node.js backend (main.js) over IPC. This package
re-implements every one of those backend handlers as real Linux commands so
the terminal itself can act as the MCPanel backend.

  - Human commands:  mcpanel create server -t "test" -ram 4096 ...
  - API commands:    mcpanel api fetch server -id srv_123   (raw JSON output)
  - Interactive GUI: mcpanel cli                             (TUI, WIP)
"""

__version__ = "1.0.4"
__app_name__ = "MCPanel"
