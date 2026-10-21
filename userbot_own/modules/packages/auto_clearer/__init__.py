"""
userbot_own/modules/packages/auto_clearer
════════════════════════════════════════════════════════════════
Auto-clear — delete bot messages by type and scope.

Layout
------
``filters.py``  the type filters, the scope rules and the defaults
``handlers.py`` the AutoClearer class — the filter and the commands
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
from userbot_own.modules.packages.auto_clearer.filters import (
    _DEFAULT,
    _SCOPES,
    _TYPES,
    _default_entry,
    _message_matches_filter,
    _scope_matches,
)
from userbot_own.modules.packages.auto_clearer.handlers import AutoClearer
from userbot_own.modules.packages.auto_clearer.info import (
    category,
    desc,
    help_extra,
    help_text,
    name,
)

# Copy the metadata onto the class — `help_handler.py` and
# `core/loader.py` both read these off the instance.
AutoClearer.name = name
AutoClearer.category = category
AutoClearer.desc = desc
AutoClearer.help_text = help_text
AutoClearer.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    """Factory the loader calls to build this module for one account."""
    return AutoClearer(context)


__all__ = [
    "AutoClearer",
    "create_module",
    # Metadata
    "name",
    "category",
    "desc",
    "help_text",
    "help_extra",
    "_DEFAULT",
    "_SCOPES",
    "_TYPES",
    "_default_entry",
    "_message_matches_filter",
    "_scope_matches",
]
