#!/usr/bin/env python3
"""
main.py  (repository root)
════════════════════════════════════════════════════════════════
Thin launcher — delegates to userbot_own.app.application.main().

Run:
    python main.py

(No more `cd userbot_own/` first — that was only ever needed because the
original main.py relied on Python's script-directory sys.path
auto-insertion to make bare imports like `import config` resolve.
This version uses proper package-relative imports throughout, so it
runs the same from the repository root regardless of your working
directory.)
════════════════════════════════════════════════════════════════
"""
import sys

sys.dont_write_bytecode = True  # Prevent __pycache__ folders

from userbot_own.app.application import main  # noqa: E402 — must follow dont_write_bytecode above

if __name__ == "__main__":
    main()
