#!/usr/bin/env python3
"""
settings.py  (repository root)
════════════════════════════════════════════════════════════════
Thin launcher — delegates to userbot_own.settings_menu.cli.main().
...
"""
import sys

sys.dont_write_bytecode = True  # Prevent __pycache__ folders

from userbot_own.settings_menu.cli import main

if __name__ == "__main__":
    main()
