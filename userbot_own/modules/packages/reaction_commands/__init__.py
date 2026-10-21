"""
userbot_own/modules/packages/reaction_commands
════════════════════════════════════════════════════════════════
Reaction commands — run a command by reacting to a message.

Layout
------
``const.py``    the tunables (state caps, cooldown, heartbeat interval)
``scope.py``    reading and writing the persisted chat-scope set
``handlers.py`` the ReactionCommands class — the funnel and the commands
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
from userbot_own.modules.packages.reaction_commands.const import (
    _HEARTBEAT_INTERVAL_SECONDS,
    _MAX_TRACKED_COOLDOWNS,
    _MAX_TRACKED_MESSAGES,
    _REACTION_COOLDOWN_SECONDS,
)
from userbot_own.modules.packages.reaction_commands.handlers import ReactionCommands
from userbot_own.modules.packages.reaction_commands.info import (
    category,
    desc,
    help_extra,
    help_text,
    name,
)

# Copy the metadata onto the class — `help_handler.py` and
# `core/loader.py` both read these off the instance.
ReactionCommands.name = name
ReactionCommands.category = category
ReactionCommands.desc = desc
ReactionCommands.help_text = help_text
ReactionCommands.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    """Factory the loader calls to build this module for one account."""
    return ReactionCommands(context)


__all__ = [
    "ReactionCommands",
    "create_module",
    # Metadata
    "name",
    "category",
    "desc",
    "help_text",
    "help_extra",
    "_HEARTBEAT_INTERVAL_SECONDS",
    "_MAX_TRACKED_COOLDOWNS",
    "_MAX_TRACKED_MESSAGES",
    "_REACTION_COOLDOWN_SECONDS",
]
