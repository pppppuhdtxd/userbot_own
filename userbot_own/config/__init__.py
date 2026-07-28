"""
userbot_own/config
════════════════════════════════════════════════════════════════
Configuration package: models (pure data) + loader (I/O).

Unlike the original single config.py, importing this package has
NO side effects — nothing is read from disk, nothing exits the
process. Call `config.loader.load_settings()` and
`config.loader.discover_accounts()` explicitly (the composition
root does this once, at startup, in app/composition_root.py).
════════════════════════════════════════════════════════════════
"""
from userbot_own.config.loader import discover_accounts, load_settings
from userbot_own.config.models import AccountConfig, Paths, Settings

__all__ = [
    "AccountConfig",
    "Paths",
    "Settings",
    "load_settings",
    "discover_accounts",
]
