"""
userbot_own/modules/packages/auto_clearer/filters.py
════════════════════════════════════════════════════════════════
The message-type filters auto-clear understands, and the scope rules.

Pure data plus two small helpers — no state, no client, no I/O. The
scope numbers are the same everywhere in the project:

- 1 = bot messages only
- 2 = the user's own messages only
- 3 = both
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from userbot_own.helpers.utils import (
    FILTER_TYPES,
    MEDIA_TYPES,
    classify_message,
)

# ── Type / scope constants ────────────────────────────────────────────────────
#
# v3.2.2 (B-14/B-15): the type lists are no longer hand-maintained copies.
# They come from helpers.utils, which owns the single canonical list that
# matches what classify_message() can actually return:
#
#   MESSAGE_TYPES  — the six real types, in classification-priority order
#                    (file > vid > pic > link > txt > other)
#   MEDIA_TYPES    — the "real media" subset the `media` composite covers
#   FILTER_TYPES   — every selectable filter target (the six types + "media")
#
# The old module-local `_SINGLE_TYPES` (dead — defined with a v3.1.7 comment
# but referenced nowhere), `_MEDIA_TYPES` and `_TYPES` were three more copies
# of the same knowledge.

_MEDIA_TYPES = MEDIA_TYPES
_TYPES = FILTER_TYPES

_SCOPES: frozenset[int] = frozenset({1, 2, 3})


# ── Default per-type settings ─────────────────────────────────────────────────

# F6: Added "other" with state=False (off by default to prevent accidental
# deletion of stickers, voice messages, and other casual message types).
_DEFAULT: dict[str, dict] = {
    "file":  {"state": False, "scope": 3},
    "vid":   {"state": False, "scope": 3},
    "pic":   {"state": False, "scope": 3},
    "link":  {"state": False, "scope": 3},
    "txt":   {"state": False, "scope": 3},
    "media": {"state": False, "scope": 3},
    "other": {"state": False, "scope": 3},
}


def _default_entry() -> dict[str, dict]:
    """Return a fresh copy of the default settings dict."""
    return {k: v.copy() for k, v in _DEFAULT.items()}


# ── Scope check helper ───────────────────────────────────────────────────────

def _scope_matches(scope: int, is_outgoing: bool) -> bool:
    """Return True if the scope allows deletion for this message direction."""
    if scope == 1:
        return not is_outgoing   # bot messages only
    if scope == 2:
        return is_outgoing       # user messages only
    if scope == 3:
        return True              # both
    return False


# ── Filter matching ──────────────────────────────────────────────────────────

def _message_matches_filter(msg, filter_type: str) -> bool:
    """
    Check if a message's classified type matches filter_type.

    For `media`, the message must be classified as pic, vid, or file
    (not `link`, not `txt`, not `other`).
    """
    msg_type = classify_message(msg)

    if filter_type == "media":
        return msg_type in _MEDIA_TYPES

    return msg_type == filter_type
