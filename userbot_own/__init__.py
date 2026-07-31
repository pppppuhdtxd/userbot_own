"""
userbot
════════════════════════════════════════════════════════════════
Multi-Account Telegram Userbot

A professional, async, hot-reload-capable Telegram account management
system built with Python 3.11+ and Telethon.

Version is read from the VERSION file at the repository root.

NOTE: this is the single place `__version__` is computed. The original
project also re-implemented a near-identical version reader inside
core/client.py, with a *different* (and by the time of this refactor,
stale) fallback constant. That duplication has been removed — every
other module that needs the version now imports `__version__` from
here instead of re-reading the file itself.
════════════════════════════════════════════════════════════════
"""
from pathlib import Path

_version_file = Path(__file__).parent.parent / "VERSION"

# Fallback used only if VERSION cannot be read at all (missing, unreadable,
# or empty). Kept in sync with the actual VERSION file content so a
# missing/corrupted VERSION file doesn't silently report a stale,
# multiple-versions-old number.
_FALLBACK_VERSION = "3.0.8"

try:
    __version__ = _version_file.read_text(encoding="utf-8").strip() or _FALLBACK_VERSION
except OSError:
    # Catches FileNotFoundError, PermissionError, and any other I/O failure
    # reading VERSION — not just the missing-file case. A narrower except
    # clause here would let an unreadable-but-present VERSION file crash
    # the import of the entire userbot package.
    __version__ = _FALLBACK_VERSION

__all__ = ["__version__"]
