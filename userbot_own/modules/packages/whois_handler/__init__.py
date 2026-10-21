"""
userbot_own/modules/packages/whois_handler
════════════════════════════════════════════════════════════════
Whois — information about a user, channel or group.

Layout
------
``const.py``    the caption/message limits and the cache TTL
``handlers.py``  the WhoisHandler class — dispatch and the result cache
``info.py``      the help texts and metadata (data only, no imports)

The module contract is the same as every other module: a ``Module``
subclass plus a module-level ``create_module(context)`` factory. The
loader discovers the package through this ``__init__.py``.

The metadata lives in ``info.py`` (data only, no imports), and is
copied onto the class below — because ``help_handler.py`` and
``core/loader.py`` read it off the *instance*.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from userbot_own.core.context import ModuleContext
from userbot_own.modules._infra.base import Module
from userbot_own.modules.packages.whois_handler.const import (
    _CACHE_TTL_SECONDS,
    _CAPTION_LIMIT,
    _MESSAGE_LIMIT,
)
from userbot_own.modules.packages.whois_handler.handlers import WhoisHandler
from userbot_own.modules.packages.whois_handler.info import (
    category,
    desc,
    help_extra,
    help_text,
    name,
)

# Copy the metadata onto the class — `help_handler.py` and
# `core/loader.py` both read these off the instance.
WhoisHandler.name = name
WhoisHandler.category = category
WhoisHandler.desc = desc
WhoisHandler.help_text = help_text
WhoisHandler.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    """Factory the loader calls to build this module for one account."""
    return WhoisHandler(context)


__all__ = [
    "WhoisHandler",
    "create_module",
    # Metadata
    "name",
    "category",
    "desc",
    "help_text",
    "help_extra",
    "_CAPTION_LIMIT",
    "_CACHE_TTL_SECONDS",
    "_MESSAGE_LIMIT",
]
