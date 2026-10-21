"""
userbot_own/modules/packages/info_handler
════════════════════════════════════════════════════════════════
Message info — the `info` command.

Layout
------
``labels.py``   the display labels for the message and entity types
``handlers.py``  the InfoHandler class — dispatch and the report builder
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
from userbot_own.modules.packages.info_handler.handlers import InfoHandler
from userbot_own.modules.packages.info_handler.info import (
    category,
    desc,
    help_extra,
    help_text,
    name,
)
from userbot_own.modules.packages.info_handler.labels import (
    _ENTITY_LABELS,
    _TYPE_LABELS,
)

# Copy the metadata onto the class — `help_handler.py` and
# `core/loader.py` both read these off the instance.
InfoHandler.name = name
InfoHandler.category = category
InfoHandler.desc = desc
InfoHandler.help_text = help_text
InfoHandler.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    """Factory the loader calls to build this module for one account."""
    return InfoHandler(context)


__all__ = [
    "InfoHandler",
    "create_module",
    # Metadata
    "name",
    "category",
    "desc",
    "help_text",
    "help_extra",
    "_ENTITY_LABELS",
    "_TYPE_LABELS",
]
