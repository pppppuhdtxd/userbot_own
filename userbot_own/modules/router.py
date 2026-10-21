"""
userbot_own/modules/router.py
════════════════════════════════════════════════════════════════
Compatibility re-export — the real module now lives in
``userbot_own/modules/_infra/router.py``.

See ``userbot_own/modules/base.py`` for why these shims exist.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from userbot_own.modules._infra.router import CommandHandler, CommandRouter

__all__ = ["CommandRouter", "CommandHandler"]
