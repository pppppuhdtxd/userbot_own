"""
userbot_own/modules/packages/join_left/entities.py
════════════════════════════════════════════════════════════════
Extracting Telegram chat identifiers from free-form text.

Pure text-in / list-out — no network, no client, no state. The single
left-to-right scan over one combined pattern is what guarantees the
entities come back in the order they appear in the message (v3.2.2,
B-03).
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import re

#: One combined pattern for every Telegram identifier form, so a single
#: left-to-right scan produces the entities in the order they appear in the
#: text.
#:
#: v3.2.2 (B-03): this used to be FIVE independent regex passes, each
#: appending to one list — so the result was grouped BY PATTERN, not by
#: position. The v3.1.8 "I4" fix claimed "links now appear in exactly the
#: order they were found in the message", but the invite-link pass ran
#: after the username pass and the numeric pass ran last of all, so:
#:
#:   "https://t.me/+AbCdEfGhIjKl then https://t.me/someuser"
#:     -> [('username','someuser'), ('invite_link','https://t.me/+AbCdEfGhIjKl')]
#:
#: — the two were swapped relative to the text. A single alternation
#: pattern fixes the ordering by construction: the scanner walks the text
#: once, left to right, and each match is emitted at its own position.
#:
#: The scheme is now OPTIONAL in every form, so `t.me/+AbCdEfGhIjKl` and
#: `t.me/joinchat/AbCdEfGhIjKl` (the form Telegram's own "Copy Link"
#: button produces in some clients) are recognised, and `t.me/c/<id>/<msg>`
#: is recognised as a private-channel link instead of falling through to
#: the generic numeric-id pass.
_TG_HOST = r'(?:www\.)?(?:t\.me|telegram\.me|telegram\.org)'
_SCHEME = r'(?:https?://)?'

#: The combined alternation. Group names:
#:   ``priv``   — t.me/c/<channel id>/<message id>   (private channel)
#:   ``invite`` — t.me/+<hash> or t.me/joinchat/<hash>
#:   ``user``   — @<name> or t.me/<name>
#:   ``num``    — a bare 9-14 digit id
_ENTITY_RE = re.compile(
    r'(?P<priv>' + _SCHEME + _TG_HOST + r'/c/(?P<priv_id>\d{10,15})/\d+)'
    r'|(?P<invite>' + _SCHEME + _TG_HOST + r'/(?:joinchat/|\+)(?P<invite_hash>[a-zA-Z0-9_-]{10,64}))'
    r'|(?P<user>@(?P<user_at>[a-zA-Z0-9_]{5,32})(?![a-zA-Z0-9_/])'
    r'|' + _SCHEME + _TG_HOST + r'/(?P<user_path>[a-zA-Z0-9_]{5,32})(?![a-zA-Z0-9_/]))'
    r'|(?P<num>\b\d{9,14}\b)',
    re.IGNORECASE,
)

#: Usernames that are really t.me *paths*, not usernames.
_RESERVED_PATH_NAMES: frozenset[str] = frozenset({
    'joinchat', 'c', 'proxy', 's', 'addstickers',
})


def extract_telegram_entities(text: str | None) -> list[tuple[str, str | int]]:
    """
    Extract Telegram chat identifiers from free-form text.

    Returns list of (type, value) tuples where type is one of:
    'channel_id', 'username', 'invite_link', 'numeric_id'

    v3.2.2 (B-03): the entities are returned in the order they appear in the
    text. The previous implementation ran five independent regex passes,
    each appending to the same list, which grouped the result by PATTERN
    instead of by POSITION — a link that appeared before another could end
    up after it, silently changing the order the user asked to join in.
    A single left-to-right scan over one combined pattern fixes that by
    construction. The scheme is also optional in every form now, so
    `t.me/+Hash` and `t.me/c/<id>/<msg>` are recognised.

    Bot usernames (ending in "bot") are still skipped: bots cannot be
    joined, only started with /start.
    """
    if not text:
        return []

    entities: list[tuple[str, str | int]] = []
    seen: set[tuple[str, str | int]] = set()

    def _add(kind: str, value: str | int) -> None:
        key = (kind, value)
        if key in seen:
            return
        seen.add(key)
        entities.append(key)

    for m in _ENTITY_RE.finditer(text):
        if m.group("priv"):
            # Private channel link: t.me/c/1234567890/123
            _add('channel_id', int(m.group('priv_id')))

        elif m.group("invite"):
            # Invite link: t.me/+Hash or t.me/joinchat/Hash
            _add('invite_link', m.group('invite'))

        elif m.group("user"):
            # Username: @name or t.me/name
            username = m.group('user_at') or m.group('user_path')
            if not username:
                continue
            lowered = username.lower()
            if lowered in _RESERVED_PATH_NAMES:
                continue
            # N1: Intentionally skip bot usernames (ending in 'bot').
            # Bots cannot be "joined" — they must be started with /start.
            # Including them would cause a ChannelPrivateError or similar failure.
            if lowered.endswith('bot'):
                continue
            _add('username', username)

        elif m.group("num"):
            # Bare numeric id. A t.me/c/<id>/<msg> link is matched by the
            # "priv" branch above, so the same id is never emitted twice
            # (v3.0.9 fix).
            _add('numeric_id', int(m.group('num')))

    return entities


def _extract_invite_hash(identifier: str) -> str | None:
    m = re.search(r'(?:\+|joinchat/)([a-zA-Z0-9_-]{10,64})$', str(identifier))
    return m.group(1) if m else None


