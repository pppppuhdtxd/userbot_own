#!/usr/bin/env python3
"""
settings.py  (repository root)
════════════════════════════════════════════════════════════════
Thin launcher — delegates to userbot_own.settings_menu.cli.main().

Run:
    python settings.py                          # interactive menu
    python settings.py show                     # print current settings
    python settings.py set-direct               # mode = direct
    python settings.py set-socks5 [host port [user] [pass]]
    python settings.py set-cf-no-worker         # cf, official endpoints
    python settings.py set-cf-worker [domain]    # cf, personal relay Worker
    python settings.py test                      # live connection test

New in v3.2.7 (Phase 5): interactive manager for the system-wide
connection mode — direct / SOCKS5 / Cloudflare (cf proxy), plus the
subcommands above so run.bat / the Termux launcher can drive it
without any JSON handling in shell code. Same shim pattern as
add_account.py at the repo root.
"""
from userbot_own.settings_menu.cli import main

if __name__ == "__main__":
    main()
