"""
userbot_own/modules/bridge.py
════════════════════════════════════════════════════════════════
Compatibility re-export — the real module now lives in
``userbot_own/modules/_infra/bridge.py``.

See ``userbot_own/modules/base.py`` for why these shims exist.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from userbot_own.modules._infra.bridge import MockEvent

__all__ = ["MockEvent"]
