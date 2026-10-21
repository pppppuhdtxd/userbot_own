"""
userbot_own/modules/packages/clearer
════════════════════════════════════════════════════════════════
Manual message clearing — the `clear` command.

Layout
------
``filters.py``   the type filters `clear` understands, and the valid
                 command arguments
``handlers.py``  the Clearer class — dispatch and the scan/delete flow
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
from userbot_own.modules.packages.clearer.filters import (
    TYPE_FILTERS,
    VALID_ARGS,
)
from userbot_own.modules.packages.clearer.handlers import Clearer
from userbot_own.modules.packages.clearer.info import (
    category,
    desc,
    help_extra,
    help_text,
    name,
)

# Copy the metadata onto the class — `help_handler.py` and
# `core/loader.py` both read these off the instance.
Clearer.name = name
Clearer.category = category
Clearer.desc = desc
Clearer.help_text = help_text
Clearer.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    """Factory the loader calls to build this module for one account."""
    return Clearer(context)


__all__ = [
    "Clearer",
    "create_module",
    # Metadata
    "name",
    "category",
    "desc",
    "help_text",
    "help_extra",
    "TYPE_FILTERS",
    "VALID_ARGS",
]
