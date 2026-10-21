"""
userbot_own/modules/packages/auto_forwarder
════════════════════════════════════════════════════════════════
Auto-forward bot messages back to the same bot.

Layout
------
``content.py``  the content classification and the default settings
``handlers.py`` the AutoForwarder class — the forwarding flow and the
                commands
``info.py``     the help texts and metadata (data only, no imports)

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
from userbot_own.modules.packages.auto_forwarder.content import (
    _ALBUM_MAX,
    _ALL_CONTENT_TYPES,
    _DEFAULT,
    _GROUP_DELAY,
    _content_type,
)
from userbot_own.modules.packages.auto_forwarder.handlers import AutoForwarder
from userbot_own.modules.packages.auto_forwarder.info import (
    category,
    desc,
    help_extra,
    help_text,
    name,
)

# Copy the metadata onto the class — `help_handler.py` and
# `core/loader.py` both read these off the instance.
AutoForwarder.name = name
AutoForwarder.category = category
AutoForwarder.desc = desc
AutoForwarder.help_text = help_text
AutoForwarder.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    """Factory the loader calls to build this module for one account."""
    return AutoForwarder(context)


__all__ = [
    "AutoForwarder",
    "create_module",
    # Metadata
    "name",
    "category",
    "desc",
    "help_text",
    "help_extra",
    "_ALBUM_MAX",
    "_ALL_CONTENT_TYPES",
    "_DEFAULT",
    "_GROUP_DELAY",
    "_content_type",
]
