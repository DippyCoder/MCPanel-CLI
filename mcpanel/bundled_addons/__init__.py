"""First-party addons shipped inside the CLI itself.

Anything placed here is discovered before pip-installed and user addons and is
enabled by default. It is currently empty: the `accounts` addon that
MCPanel-WebUI signs in through used to live here and now ships separately
(https://github.com/DippyCoder/MCPanel-Accounts), so the CLI carries no
built-in assumptions about who uses it. The mechanism stays for future
first-party addons.
"""
