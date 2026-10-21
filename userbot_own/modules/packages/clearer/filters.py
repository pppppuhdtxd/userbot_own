"""
userbot_own/modules/packages/clearer/filters.py
════════════════════════════════════════════════════════════════
The type filters `clear` understands, and the arguments that are valid.

Pure data plus two small helpers — no state, no client, no I/O. The
module class reads these; nothing here knows about the module.

v3.2.5: `clear all` was broken by the 3.2.3 refactor. `VALID_ARGS` had
been derived from `FILTER_TYPES` — the list of message TYPES — but
`all` is a KEY of `TYPE_FILTERS` (the composite filter covering every
type), not a type. The strict validation therefore rejected `clear all`
before it ever reached the scan, and the command produced no output at
all. `VALID_ARGS` is now derived from the keys of `TYPE_FILTERS`, so it
cannot drift from the filter table again.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from userbot_own.helpers.utils import MEDIA_TYPES, MESSAGE_TYPES

# ── Type filters ─────────────────────────────────────────────────────────────

#: Maps command argument → set of classified types to delete.
#: `clear` (no argument) is equivalent to `clear txt link`.
#: `clear media` = pic + vid + file (does NOT include `link`).
#:
#: v3.2.2 (B-14): the "media" and "all" composites are now derived from the
#: canonical type lists in helpers.utils instead of being hand-written
#: copies. The old literals were a fifth and sixth place a message type had
#: to be added to whenever classify_message() learned a new one — a new type
#: would have silently been missing from `clear all`.
TYPE_FILTERS: dict[str, set[str]] = {
    # Default: text + link messages (most common cleanup use-case)
    "default": {"txt", "link"},

    # Single-type filters
    "txt":   {"txt"},
    "link":  {"link"},
    "pic":   {"pic"},
    "vid":   {"vid"},
    "file":  {"file"},

    # Composite filters
    "media": set(MEDIA_TYPES),   # Real media only — no `link`
    "all":   set(MESSAGE_TYPES),
}


# ── Valid command arguments ──────────────────────────────────────────────────

#: Any argument not in this set causes the command to be silently ignored.
#:
#: v3.2.3: this was derived from ``FILTER_TYPES`` (the list of message
#: TYPES), which is the wrong source. ``all`` is a KEY of
#: ``TYPE_FILTERS`` — a composite filter — not a message type, so it was
#: missing from the set and `clear all` was rejected by the strict
#: validation and silently did nothing. The set is now derived from the
#: keys of ``TYPE_FILTERS`` (the things a user can actually type, minus
#: the internal ``default`` alias) plus the two scope filters, so it can
#: never drift from the filter table again.
VALID_ARGS: frozenset[str] = frozenset(
    (set(TYPE_FILTERS) - {"default"}) | {
        # Scope filters
        "self", "bot",
    }
)
