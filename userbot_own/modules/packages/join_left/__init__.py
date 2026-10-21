"""
userbot_own/modules/packages/join_left
════════════════════════════════════════════════════════════════
Join / Left / Folder / List / AutoLeave / Join Delay — as a package.

This package is one *logical module* named ``join_left``, exactly like
the single-file module it replaces. The loader discovers it through
this ``__init__.py`` and calls ``create_module(context)`` — the same
contract every single-file module in ``modules/single/`` uses.

Layout
------
``const.py``      tunable numbers (delays, caps, thresholds)
``types.py``      result records (PostJoinResult, EnsureResult, …)
``entities.py``   pulling chat identifiers out of free-form text
``folders.py``    the joined* folders and the exclude_archived protection
``post_join.py``  mute + archive right after a join
``leave.py``      leaving everything and resetting the folders
``handlers.py``   the JoinLeft class — dispatch and the join/left flows
``info.py``       help texts and metadata (data only, no imports)

Why the split
-------------
``handlers.py`` is the only stateful file; every other module in the
package is a set of free functions over plain arguments. That means
the interesting pieces — folder handling, post-join actions, entity
extraction — can be read (and reasoned about) without walking through
the whole command flow, and a change to one of them cannot
accidentally alter another.

The metadata lives in ``info.py`` because it is the one part of a
module that is edited most often (help texts) and is pure data. It
imports nothing, so an import cycle is structurally impossible.

Contract with the loader
------------------------
``create_module(context)`` is the module-level factory the loader
looks for. ``help_text`` / ``help_extra`` / ``category`` / ``desc``
are copied onto the class below, because ``help_handler.py`` reads
them off the *instance* (``getattr(instance, "help_text", "")``) and
``core/loader.py`` reads ``help_extra`` for its oversized-help
warning. Setting them on the class is what makes both work.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from userbot_own.core.context import ModuleContext
from userbot_own.modules._infra.base import Module
from userbot_own.modules.packages.join_left.entities import (
    _extract_invite_hash,
    extract_telegram_entities,
)
from userbot_own.modules.packages.join_left.folders import (
    _add_single_peer_to_joined_folder,
    _create_joined_folder,
    _find_all_joined_folders,
    _remove_left_from_folders,
    _write_folder_with_retry,
    ensure_exclude_archived,
)
from userbot_own.modules.packages.join_left.handlers import JoinLeft
from userbot_own.modules.packages.join_left.info import (
    category,
    desc,
    help_extra,
    help_text,
    name,
)
from userbot_own.modules.packages.join_left.leave import (
    _leave_and_reset_joined_folder,
)
from userbot_own.modules.packages.join_left.post_join import (
    _archive_chat,
    _call_with_flood_retry,
    _mute_chat,
)
from userbot_own.modules.packages.join_left.types import (
    EnsureResult,
    PostJoinResult,
)

# Copy the metadata onto the class. `help_handler.py` and `core/loader.py`
# both read these off the instance, so they must be class attributes —
# not module-level names.
JoinLeft.name = name
JoinLeft.category = category
JoinLeft.desc = desc
JoinLeft.help_text = help_text
JoinLeft.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    """Factory the loader calls to build this module for one account."""
    return JoinLeft(context)


__all__ = [
    "JoinLeft",
    "create_module",
    # Metadata
    "name",
    "category",
    "desc",
    "help_text",
    "help_extra",
    # Result records
    "PostJoinResult",
    "EnsureResult",
    # Entity extraction
    "extract_telegram_entities",
    "_extract_invite_hash",
    # Folder management
    "_add_single_peer_to_joined_folder",
    "_create_joined_folder",
    "_find_all_joined_folders",
    "_leave_and_reset_joined_folder",
    "_remove_left_from_folders",
    "_write_folder_with_retry",
    "ensure_exclude_archived",
    # Post-join actions
    "_archive_chat",
    "_call_with_flood_retry",
    "_mute_chat",
]
