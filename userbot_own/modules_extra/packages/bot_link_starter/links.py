"""
userbot_own/modules_extra/packages/bot_link_starter/links.py
════════════════════════════════════════════════════════════════
Finding the links inside a Quote & Reply, and turning them into
something that can be started.

Two separate jobs, kept apart on purpose:

1. ``extract_quoted_links()`` — pull the links out of the QUOTED part of a
   message, and only that part. A Quote & Reply carries its own text and
   its own entities in ``MessageReplyHeader`` (``quote_text`` /
   ``quote_entities``), so the quoted part is read directly and the
   rest of the message is never looked at.

2. ``parse_bot_link()`` — decide whether a URL is a Telegram bot link
   and, if so, extract the bot's username and the ``?start=`` payload.

Both are pure functions over plain data — no client, no state, no I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

#: Telegram's own link domains. ``t.me`` is the modern one; the other two
#: still work and are what older "copy link" buttons produce.
TELEGRAM_HOSTS: frozenset[str] = frozenset({
    "t.me",
    "telegram.me",
    "telegram.org",
})

#: Paths that are NOT a bot username, even though they sit under a
#: Telegram host. Sending "/start" for any of these would be wrong:
#:
#: - ``joinchat/<hash>`` and ``+<hash>``  — chat invite links
#: - ``c/<id>``                            — a private channel link
#: - ``s/<channel>``                       — a saved-messages style link
#: - ``proxy``                              — a proxy settings link
#: - ``addstickers``                        — a sticker pack link
_NON_BOT_PATHS: frozenset[str] = frozenset({
    "joinchat", "c", "s", "proxy", "addstickers",
})

#: A Telegram username. Telegram's documented minimum is 5 characters,
#: but the length is not what decides whether a link is a bot link —
#: the server does. A slightly permissive pattern (4+) means a short
#: username is still attempted rather than silently skipped, and the
#: server's "bot not found" is the honest answer.
#:
#: Note that a username is NOT required to end in "bot": plenty of bots
#: have other names, and the server rejects non-bots anyway.
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")

#: A bare URL in plain text (the fallback path, for text Telegram did
#: not turn into an entity). Deliberately permissive about the scheme
#: so that ``t.me/Bot?start=x`` is found too.
_PLAIN_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:t\.me|telegram\.me|telegram\.org)"
    r"/[^\s<>\"'()\[\]]+",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class BotLink:
    """One Telegram bot link found in the quoted text."""

    #: The bot's username, without the leading "@".
    username: str
    #: The ``?start=`` payload, or None when the link had none.
    payload: str | None
    #: The URL exactly as it appeared.
    url: str

    @property
    def command(self) -> str:
        """The text to send to the bot — ``/start`` plus the payload."""
        return f"/start {self.payload}" if self.payload else "/start"


def _utf16_slice(text: str, offset: int, length: int) -> str:
    """
    Slice *text* using Telegram's entity offsets.

    Telegram counts entity offsets and lengths in UTF-16 code units, not
    in Python characters. Any character outside the Basic Multilingual
    Plane (most emoji) counts as TWO units, so a naive character slice
    drifts — and the link comes out mangled. Encoding to UTF-16 first
    makes the offsets line up.
    """
    try:
        encoded = text.encode("utf-16-le")
        return encoded[offset * 2: (offset + length) * 2].decode("utf-16-le", "ignore")
    except (UnicodeError, IndexError):
        return text[offset: offset + length]


def _char_to_utf16(text: str, char_index: int) -> int:
    """
    Convert a Python character index into a UTF-16 code-unit index.

    Telegram counts entity offsets in UTF-16 code units, while a Python
    string index counts characters. Any character outside the Basic
    Multilingual Plane (most emoji) occupies TWO UTF-16 units, so the
    two numbering schemes drift apart as soon as an emoji appears. The
    raw-text scan works in character indices, so its positions are
    converted here before they are compared with entity positions.
    """
    try:
        return len(text[:char_index].encode("utf-16-le")) // 2
    except (UnicodeError, IndexError):
        return char_index


def extract_quoted_links(quote_text: str | None, quote_entities) -> list[str]:
    """
    Return every link inside a quoted message part, in order of
    appearance.

    Two sources are used, in this order:

    1. **The entities.** A link the user created with "create link" is a
       ``MessageEntityTextUrl``: the visible text is arbitrary and the
       real target is in ``.url``. A link Telegram detected on its own is
       a ``MessageEntityUrl``, where the visible text IS the URL — so it
       has to be sliced out of the text using the entity's offsets.

    2. **The raw text**, as a fallback. Telegram does not always turn a
       pasted URL into an entity (it depends on the client's auto-link
       setting), so the text is scanned too.

    The two sources are merged by **position**, not by comparing the URL
    strings. A link that appears in both is reported once, and the
    entity's version wins — because the entity carries the exact
    characters Telegram saw, while the raw-text scan can only guess
    where a URL ends. Comparing the strings instead would report a
    truncated entity-URL and the full raw-URL as two different links.

    The two sources count positions differently — entity offsets are
    UTF-16 code units, Python string offsets are characters — so both
    are converted to the same unit (UTF-16) before they are compared.
    Without that, one link would be reported twice whenever an emoji
    appeared before it.

    Args:
        quote_text:    The text of the quoted part (``quote_text``).
        quote_entities: The entities of the quoted part
                        (``quote_entities``).

    Returns:
        The links, in the order they appear in the text.
    """
    text = quote_text or ""

    # position (in UTF-16 units) -> url, keeping the FIRST source that
    # found each position (entities win, they are the more precise
    # source).
    found: dict[int, str] = {}

    def add(position: int, url: str) -> None:
        url = url.strip()
        if not url:
            return
        if position not in found:
            found[position] = url

    # 1. the entities
    for entity in quote_entities or []:
        if isinstance(entity, MessageEntityTextUrl):
            # A "create link" — the URL is right there.
            add(entity.offset, entity.url)
        elif isinstance(entity, MessageEntityUrl):
            # A plain detected link — the text at those offsets is the URL.
            add(entity.offset, _utf16_slice(text, entity.offset, entity.length))

    # 2. the raw text, for links Telegram did not turn into entities.
    #    The match offset is a character index, so it is converted to
    #    UTF-16 units to match the entity positions. A position already
    #    claimed by an entity is skipped, so the same link is never
    #    reported twice.
    for match in _PLAIN_URL_RE.finditer(text):
        position = _char_to_utf16(text, match.start())
        if position in found:
            continue
        add(position, match.group(0))

    return [found[position] for position in sorted(found)]


def _normalize(text: str) -> str:
    """
    Collapse the differences that make two copies of the same words stop
    matching.

    Telegram clients rewrite a quote as they send it: non-breaking spaces
    become ordinary spaces, whitespace runs collapse, and the ends are
    trimmed. The original message keeps whatever the channel author
    typed. Comparing the two verbatim therefore fails on real messages
    that look identical on screen.
    """
    return " ".join(text.replace("\xa0", " ").split())


def locate_quote(
    quote_text: str | None,
    quote_offset: int | None,
    full_text: str | None,
) -> tuple[int, int] | None:
    """
    Work out which stretch of the full message a quote covers.

    Returns ``(start, end)`` in **character** indices into *full_text*,
    or None when it cannot be determined.

    Three attempts, in order of confidence:

    1. the reported ``quote_offset``, accepted only if the text there
       really is the quoted text;
    2. the quoted text found verbatim in the message;
    3. the quoted text found after normalising whitespace on both sides.

    The offset goes first because the same words can appear twice in a
    message and a plain search always finds the FIRST one — and two
    identical-looking lines can hide different links. Every attempt is
    checked against the actual text: a wrong range is worse than no
    range, because it would hand the caller a slice of an unrelated part
    of the message.
    """
    if not quote_text or not full_text:
        return None

    # 1. the reported offset, if the text there is really the quote.
    if quote_offset is not None and 0 <= quote_offset <= len(full_text):
        end = quote_offset + len(quote_text)
        if full_text[quote_offset:end] == quote_text:
            return quote_offset, end

    # 2. verbatim search.
    start = full_text.find(quote_text)
    if start >= 0:
        return start, start + len(quote_text)

    # 3. normalised search: clients rewrite whitespace on the way out, so
    #    the quote no longer matches the original character for character.
    #    Both sides are normalised and the match is mapped back by
    #    counting the deltas between the two forms.
    normalized_full = _normalize(full_text)
    normalized_quote = _normalize(quote_text)
    if not normalized_quote:
        return None

    idx = normalized_full.find(normalized_quote)
    if idx < 0:
        return None

    # Map the normalised index back to a character index in full_text by
    # walking both strings in step.
    def to_original(norm_index: int) -> int:
        seen = 0
        in_space = False
        for i, ch in enumerate(full_text):
            if ch.isspace() or ch == "\xa0":
                if not in_space:
                    in_space = True
                    if seen == norm_index:
                        return i
                    seen += 1
                continue
            in_space = False
            if seen == norm_index:
                return i
            seen += 1
        return len(full_text)

    start = to_original(idx)
    end = to_original(idx + len(normalized_quote))
    return (start, end) if end > start else None


def _visible_for(entry: str) -> str:
    """
    Strip the decoration off a quoted line so it can be compared with the
    original.

    A quote copies what the user sees, including whatever the client adds
    when it copies — an arrow, a bullet, a leading emoji. The line in the
    original may carry different decoration, or none. What identifies the
    line is the words, so everything but letters, digits and a few inner
    separators is dropped.
    """
    return "".join(
        ch for ch in entry
        if ch.isalnum() or ch in "_-.:"
    ).lower()


def find_bot_links_by_labels(
    full_text: str | None,
    entities,
    quote_text: str,
) -> list[BotLink]:
    """
    Match the quoted lines to the message by their visible text.

    This is the last resort, and on the messages this module exists for it
    is the one that actually works.

    A quote carries the *words* the user selected — "⬇️ Chapter 557" —
    while the link itself is an entity whose target is stored nowhere
    else. When the quoted text cannot be located in the message as a
    contiguous stretch (clients rewrite whitespace, and a quote may join
    lines that were not adjacent in the original), the words are matched
    against the text each entity covers instead. An entity whose visible
    text is named by a quoted line is part of the selection.

    The comparison ignores decoration and case, because the same line is
    commonly rendered differently in the quote than in the original.
    """
    text = full_text or ""
    wanted = {_visible_for(line) for line in quote_text.splitlines()}
    wanted.discard("")
    if not wanted:
        return []

    result: list[BotLink] = []
    seen: set[tuple[str, str | None]] = set()

    for entity in entities or []:
        if not isinstance(entity, (MessageEntityTextUrl, MessageEntityUrl)):
            continue

        visible = _utf16_slice(text, entity.offset, entity.length)
        if _visible_for(visible) not in wanted:
            continue

        url = entity.url if isinstance(entity, MessageEntityTextUrl) else visible
        bot_link = parse_bot_link((url or "").strip())
        if bot_link is None:
            continue

        key = (bot_link.username.lower(), bot_link.payload)
        if key in seen:
            continue
        seen.add(key)
        result.append(bot_link)

    return result


def find_bot_links_in_range(
    full_text: str | None,
    entities,
    span: tuple[int, int] | None,
) -> list[BotLink]:
    """
    Read the bot links out of *entities*, restricted to *span*.

    This is the path that makes "create link" work at all. The original
    message always carries its entities — ``MessageEntityTextUrl`` with
    the real target in ``.url`` — but a QUOTE does not: it carries the
    quoted words and, depending on the client and the layer version, may
    carry none of their entities. The target of a create-link exists
    nowhere except in that entity, so a quote alone is unreadable for
    exactly the kind of message this module is for.

    *span* limits the result to the quoted stretch of the message, so
    quoting three lines out of thirty still starts only those three.
    ``None`` means the whole message was quoted.

    Entities are matched to the range by their position in the full
    message. ``MessageEntityTextUrl`` carries its URL directly;
    ``MessageEntityUrl`` does not, so the URL is sliced out of the text
    using the entity's UTF-16 offsets.
    """
    text = full_text or ""
    result: list[BotLink] = []
    seen: set[tuple[str, str | None]] = set()

    for entity in entities or []:
        # Unknown entity types are skipped, not guessed at.
        if not isinstance(entity, (MessageEntityTextUrl, MessageEntityUrl)):
            continue

        # Entity offsets are UTF-16 code units; the span is in
        # characters. Convert the entity's start to a character index
        # for the comparison, so an emoji earlier in the message cannot
        # shift a link out of the range.
        try:
            char_start = len(text.encode("utf-16-le")[: entity.offset * 2]
                             .decode("utf-16-le", "ignore"))
            char_end = len(
                text.encode("utf-16-le")[: (entity.offset + entity.length) * 2]
                .decode("utf-16-le", "ignore")
            )
        except (UnicodeError, IndexError):
            continue

        # Overlap, not containment. A selection can end in the middle of
        # a link (the quote text is whatever the user dragged over), and
        # clients may trim whitespace at either end — so a link whose
        # text is only partly inside the selection is still part of it.
        if span is not None and not (char_start < span[1] and char_end > span[0]):
            continue

        if isinstance(entity, MessageEntityTextUrl):
            url = entity.url or ""
        else:
            url = _utf16_slice(text, entity.offset, entity.length)

        bot_link = parse_bot_link(url.strip())
        if bot_link is None:
            continue

        key = (bot_link.username.lower(), bot_link.payload)
        if key in seen:
            continue
        seen.add(key)
        result.append(bot_link)

    return result


def parse_bot_link(url: str) -> BotLink | None:
    """
    Turn a URL into a :class:`BotLink`, or return None if it is not a
    Telegram bot link that can be started.

    A link qualifies when it points at a Telegram host and its first
    path segment is a plausible username. The ``?start=`` payload is
    taken from the query string, URL-decoded exactly as Telegram would
    decode it.
    """
    if not url:
        return None

    # Telegram's own in-app link scheme: tg://resolve?domain=Bot&start=x
    # has no host and no path, so it is unwrapped onto the https form the
    # rest of this function expects. Without this, a link copied from an
    # app that uses the tg:// form was silently ignored.
    if url.lower().startswith("tg://"):
        try:
            tg = urlparse(url)
        except ValueError:
            return None
        domain = parse_qs(tg.query).get("domain")
        if not domain or not domain[0]:
            return None
        # Keep the query so the start payload survives.
        url = f"https://t.me/{domain[0]}" + (f"?{tg.query}" if tg.query else "")
        candidate = url
    else:
        # A link pasted without a scheme ("t.me/Bot?start=x") has no host
        # for urlparse to find — give it one.
        candidate = url if "://" in url else "https://" + url

    try:
        parts = urlparse(candidate)
    except ValueError:
        return None

    host = (parts.hostname or "").lower()
    if host not in TELEGRAM_HOSTS and not host.endswith(".t.me"):
        return None

    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        return None

    # "joinchat/...", "+hash", "c/123", "s/..." are not bots.
    if segments[0].lower() in _NON_BOT_PATHS or segments[0].startswith("+"):
        return None

    username = segments[0]
    if not _USERNAME_RE.match(username):
        return None

    # A second path segment means a link to a POST in a channel
    # (``t.me/somechannel/123``), not to the account itself. ``/start``
    # to a channel is meaningless — the account may not even be
    # resolvable as a user — so these are skipped rather than attempted.
    if len(segments) > 1:
        return None

    # The start payload. Query keys are case-sensitive in parse_qs, but a
    # link written by hand may spell it "Start" or "START" — Telegram's
    # own clients accept any case, so this does too.
    payload: str | None = None
    if parts.query:
        for key, values in parse_qs(parts.query).items():
            if key.lower() == "start":
                payload = (values[0] if values else "") or None
                break

    return BotLink(username=username, payload=payload, url=url)


def find_bot_links(quote_text: str | None, quote_entities) -> list[BotLink]:
    """
    Return the startable bot links inside a quoted message part.

    Only Telegram bot links are returned. A link that appears twice
    verbatim is reported once, but **different payloads to the same bot
    are kept**. The payload is what identifies the item being started —
    a file-hosting bot hands out a different file for every ``?start=``
    value, so ``Bot?start=111`` and ``Bot?start=222`` are two separate
    things the user asked for. Deduplicating by username would silently
    throw one of them away.
    """
    result: list[BotLink] = []
    seen: set[tuple[str, str | None]] = set()

    for url in extract_quoted_links(quote_text, quote_entities):
        bot_link = parse_bot_link(url)
        if bot_link is None:
            continue
        # Same bot + same payload is a genuine duplicate; same bot with
        # a different payload is a different item.
        key = (bot_link.username.lower(), bot_link.payload)
        if key in seen:
            continue
        seen.add(key)
        result.append(bot_link)

    return result


__all__ = [
    "TELEGRAM_HOSTS",
    "BotLink",
    "extract_quoted_links",
    "find_bot_links",
    "find_bot_links_by_labels",
    "find_bot_links_in_range",
    "locate_quote",
    "parse_bot_link",
]
