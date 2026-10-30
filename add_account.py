#!/usr/bin/env python3
"""
add_account.py  (repository root)
════════════════════════════════════════════════════════════════
Thin launcher — delegates to userbot_own.account_management.cli.main().

Run:
    python add_account.py
════════════════════════════════════════════════════════════════
"""
import sys

sys.dont_write_bytecode = True  # Prevent __pycache__ folders

from userbot_own.account_management.cli import main

if __name__ == "__main__":
    main()
