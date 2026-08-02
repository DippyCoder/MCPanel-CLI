"""First-party addons shipped inside the CLI itself.

These are discovered before pip-installed and user addons and are enabled by
default, so an install that ships one (`accounts`, which MCPanel-WebUI's login
system depends on) works with no extra setup. They are still ordinary addons:
`mcpanel addons disable <name>` turns one off exactly like any other.
"""
