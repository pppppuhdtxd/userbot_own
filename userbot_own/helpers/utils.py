"""
userbot_own/helpers/utils.py
================
Shared utility functions used across all userbot modules.

Contents
--------
- Message-delete helpers (``safe_delete``, ``batch_delete``)
- Media-type predicate functions (``is_photo``, ``is_video``, …)
- Link detection (``is_link`` — includes WebPage, URL entities, inline
  keyboard buttons with URLs, and raw URL patterns in text)
- Message classification (``classify_message``)
- File-info helpers (``get_file_extension``, ``get_file_size``, ``get_media_info``)
- URL detection (``contains_any_link``)

Message Classification System (v1.6.1+)
----------------------------------------
Messages are classified into ONE of these types based on priority:
    file > vid > pic > link > txt > other

The `link` type detection is comprehensive and covers:
    • MessageMediaWebPage (auto-generated preview for download links, etc.)
    • MessageEntityUrl / MessageEntityTextUrl (inline URL entities)
    • KeyboardButtonUrl in ReplyInlineMarkup (inline keyboard with URL buttons)
    • Raw URL patterns in text (fallback when entities aren't parsed)

This ensures each message has exactly one type, avoiding ambiguity
in filtering operations (clear, auto_clearer, etc.).

This module is deliberately plain functions, not a class — there is no
per-account or cross-cutting state here to justify constructor injection;
it's a pure(-ish) utility library, same as the original.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from pathlib import Path
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    KeyboardButtonUrl,
    MessageEntityTextUrl,
    MessageEntityUrl,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
    ReplyInlineMarkup,
    UserStatusOffline,
    UserStatusOnline,
    UserStatusRecently,
)
from telethon.tl.types import messages as tl_msg_types

from userbot_own.core.logging_setup import get_logger

if TYPE_CHECKING:
    pass

log = get_logger(__name__)


__all__ = [
    # Delete helpers
    "safe_delete",
    "batch_delete",
    # Media predicates
    "is_photo",
    "is_video",
    "is_sticker",
    "is_audio",
    "is_file",
    "is_non_file_media",
    # Link detection
    "is_link",
    # Message classification
    "classify_message",
    # File info
    "get_file_extension",
    "get_file_size",
    "get_media_info",
    # URL detection
    "contains_any_link",
    # JSON settings persistence
    "read_json_file",
    "write_json_file_atomic",
    # Entity/user formatting (v3.0.10)
    "format_user_flags",
    "format_user_status",
    "truncate",
    "get_profile_photos_safe",
]


# ── Shared regex for URL detection ───────────────────────────────────────────
_URL_RE = re.compile(r"https?://[^\s<>\"{}|\\^`\[\]]+", re.IGNORECASE)


# ── Delete helpers ────────────────────────────────────────────────────────────

async def safe_delete(
    client,
    entity,
    message_ids: int | list[int],
) -> bool:
    """
    Delete one or more messages, silently absorbing permission errors.

    Args:
        client:      Active ``TelegramClient``.
        entity:      Chat or peer to delete from.
        message_ids: Single message ID or list of IDs.

    Returns:
        ``True`` if the delete API call succeeded, ``False`` otherwise.

    Example:
        >>> await safe_delete(client, chat_id, 12345)
        True
    """
    ids = message_ids if isinstance(message_ids, list) else [message_ids]
    try:
        await client.delete_messages(entity, ids, revoke=True)
        return True
    except errors.FloodWaitError as exc:
        log.warning("safe_delete: FloodWait %ds.", exc.seconds)
        return False
    except (
        errors.MessageDeleteForbiddenError,
        errors.ChatAdminRequiredError,
        errors.UserAdminInvalidError,
        errors.ChatWriteForbiddenError,
        errors.RPCError,
    ) as exc:
        log.debug("safe_delete: permission denied — %s", exc)
        return False
    except Exception as exc:
        log.error("safe_delete: unexpected error — %s", exc)
        return False


def _pts_count(results) -> int:
    """
    Sum the ``pts_count`` fields from a list of ``messages.AffectedMessages``
    returned by ``client.delete_messages()``.

    ``pts_count`` is the number of messages Telegram actually deleted
    server-side. This may be less than the number of IDs submitted when the
    caller does not have permission to delete some (e.g. other members'
    messages in a group without admin rights). Summing it gives an exact
    deletion count rather than relying on ``len(batch)`` as a proxy.

    Args:
        results: Return value of ``client.delete_messages()`` — a list of
                 ``messages.AffectedMessages`` objects (one per internal
                 100-message chunk).

    Returns:
        Total number of messages deleted according to Telegram.
    """
    if not results:
        return 0
    total = 0
    for r in results:
        if isinstance(r, tl_msg_types.AffectedMessages):
            total += r.pts_count
        else:
            log.debug("batch_delete: unexpected result type %s", type(r).__name__)
    return total


async def batch_delete(
    client,
    entity,
    ids: list[int],
    batch_size: int = 100,
) -> int:
    """
    Delete a list of message IDs in batches of *batch_size*.

    Uses ``messages.AffectedMessages.pts_count`` returned by
    ``client.delete_messages()`` for an accurate deletion count.
    Telegram's ``pts_count`` reflects the number of messages actually
    removed server-side, which may be less than ``len(batch)`` when some
    IDs belong to other users in a group where the caller lacks admin
    delete rights.

    Falls back to one-by-one deletion via ``safe_delete()`` if a batch
    raises a non-FloodWait exception.

    Args:
        client:     Active ``TelegramClient``.
        entity:     Chat or peer to delete from.
        ids:        List of message IDs to delete.
        batch_size: Max IDs per API call (Telegram limit is 100).

    Returns:
        Number of messages actually deleted according to Telegram's
        ``pts_count`` response field.

    Example:
        >>> deleted = await batch_delete(client, chat_id, [1, 2, 3, 4, 5])
        >>> print(f"Deleted {deleted} messages")
        Deleted 5 messages
    """
    deleted = 0
    _MAX_BATCH_FW_RETRIES = 5
    for i in range(0, len(ids), batch_size):
        batch = ids[i : i + batch_size]

        # v3.0.9: small proactive pacing between batches (mirrors the
        # wait_time mitigation already used on iter_messages scans) so a
        # large clear doesn't fire many delete_messages calls back-to-back
        # — the burst pattern most likely to trigger FloodWait in the
        # first place, rather than just reacting to it after the fact.
        if i > 0:
            await asyncio.sleep(0.5)

        attempt = 0
        while True:
            try:
                results = await client.delete_messages(entity, batch, revoke=True)
                deleted += _pts_count(results)
                break
            except errors.FloodWaitError as exc:
                attempt += 1
                if attempt > _MAX_BATCH_FW_RETRIES:
                    # v3.0.9: previously gave up silently after exactly one
                    # retry regardless of the new wait time — a second
                    # consecutive FloodWait on the same batch would drop
                    # its deletions with no further attempt. Now retries
                    # up to a cap, honoring each new exc.seconds.
                    log.error(
                        "batch_delete: FloodWait retry cap (%d) exceeded — "
                        "skipping remaining batch.", _MAX_BATCH_FW_RETRIES,
                    )
                    break
                log.warning(
                    "batch_delete: FloodWait %ds (attempt %d/%d) — waiting…",
                    exc.seconds, attempt, _MAX_BATCH_FW_RETRIES,
                )
                await asyncio.sleep(exc.seconds)
            except Exception as exc:
                log.error("batch_delete: batch failed (%s) — one-by-one fallback.", exc)
                for mid in batch:
                    if await safe_delete(client, entity, mid):
                        deleted += 1
                break
    return deleted


# ── Media type predicates ─────────────────────────────────────────────────────
#
# Performance-critical: these are called thousands of times per clear/forward
# operation. Using isinstance() is ~100x faster than try/except or getattr().

def is_photo(media) -> bool:
    """
    Return ``True`` if *media* is a photo or a photo-like document.

    Checks:
    - Native ``MessageMediaPhoto`` (standard Telegram photos)
    - Documents with image file extensions (.jpg, .jpeg, .png, .bmp, .webp)
      that are NOT videos or stickers

    Performance:
        O(1) for native photos, O(n) for documents where n = attribute count.

    Example:
        >>> is_photo(msg.media)
        True
    """
    if isinstance(media, MessageMediaPhoto):
        return True
    if isinstance(media, MessageMediaDocument) and media.document:
        # Quick reject: videos and stickers are never photos
        for attr in media.document.attributes:
            if isinstance(attr, (DocumentAttributeVideo, DocumentAttributeSticker)):
                return False
        # Check filename extension for image-like documents
        for attr in media.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                return attr.file_name.lower().endswith(
                    (".jpg", ".jpeg", ".png", ".bmp", ".webp")
                )
    return False


def is_video(media) -> bool:
    """
    Return ``True`` if *media* contains a video document.

    Performance:
        O(1) for non-documents, O(n) for documents.

    Example:
        >>> is_video(msg.media)
        True
    """
    if not isinstance(media, MessageMediaDocument) or not media.document:
        return False
    return any(isinstance(a, DocumentAttributeVideo) for a in media.document.attributes)


def is_sticker(media) -> bool:
    """
    Return ``True`` if *media* is a sticker.

    Performance:
        O(1) for non-documents, O(n) for documents.
    """
    if not isinstance(media, MessageMediaDocument) or not media.document:
        return False
    return any(isinstance(a, DocumentAttributeSticker) for a in media.document.attributes)


def is_audio(media) -> bool:
    """
    Return ``True`` if *media* is an audio file or voice message.

    Performance:
        O(1) for non-documents, O(n) for documents.
    """
    if not isinstance(media, MessageMediaDocument) or not media.document:
        return False
    return any(isinstance(a, DocumentAttributeAudio) for a in media.document.attributes)


def is_file(media) -> bool:
    """
    Return ``True`` if *media* is a generic file (not photo/video/sticker/audio).

    This represents the highest-priority type in the classification system.
    Documents with ``DocumentAttributeFilename`` but no video/audio/sticker
    attributes are classified as files.

    Performance:
        O(n) where n = attribute count.

    Example:
        >>> is_file(msg.media)  # PDF, ZIP, etc.
        True
    """
    if not media:
        return False
    if is_photo(media) or is_video(media) or is_sticker(media) or is_audio(media):
        return False
    if isinstance(media, MessageMediaDocument) and media.document:
        return any(
            isinstance(a, DocumentAttributeFilename)
            for a in media.document.attributes
        )
    return False


def is_non_file_media(media) -> bool:
    """
    Return ``True`` for photo, video, sticker, or audio (not a file attachment).

    Useful for distinguishing inline media from file attachments.
    """
    if not media:
        return False
    return is_photo(media) or is_video(media) or is_sticker(media) or is_audio(media)


# ── Link detection ────────────────────────────────────────────────────────────

def is_link(msg) -> bool:
    """
    Return ``True`` if *msg* contains a link in any form.

    A message is considered a "link" if ANY of the following is true:
    1. It has ``MessageMediaWebPage`` (auto-generated preview for downloadable
       links, t.me links, bot deep links, etc.)
    2. It contains ``MessageEntityUrl`` or ``MessageEntityTextUrl`` in its
       entities (clickable URL hyperlinks in text)
    3. Its inline keyboard (``reply_markup``) contains at least one
       ``KeyboardButtonUrl`` (URL button in a "glass button" / دکمه شیشه‌ای)
    4. Its text matches a raw URL pattern (fallback for cases where Telegram
       didn't parse the URL as an entity)

    This type sits between media types (file/vid/pic) and plain text in the
    priority hierarchy: ``file > vid > pic > link > txt``

    Args:
        msg: A Telethon Message object (not just media).

    Returns:
        ``True`` if the message contains link-related content.

    Example:
        >>> is_link(msg_with_webpage)          # Download URL → True
        True
        >>> is_link(msg_with_inline_url_btn)   # دکمه شیشه‌ای → True
        True
        >>> is_link(plain_text_msg)
        False
    """
    if not msg:
        return False

    # 1. Check for WebPage preview (auto-generated for download links, etc.)
    media = getattr(msg, "media", None)
    if isinstance(media, MessageMediaWebPage):
        return True

    # 2. Check for URL entities in the message text
    entities = getattr(msg, "entities", None) or []
    for entity in entities:
        if isinstance(entity, (MessageEntityUrl, MessageEntityTextUrl)):
            return True

    # 3. Check for URL buttons in inline keyboard (دکمه شیشه‌ای)
    #    This covers bot messages with buttons like "🔗 Visit Website"
    reply_markup = getattr(msg, "reply_markup", None)
    if isinstance(reply_markup, ReplyInlineMarkup):
        rows = getattr(reply_markup, "rows", None) or []
        for row in rows:
            buttons = getattr(row, "buttons", None) or []
            for button in buttons:
                if isinstance(button, KeyboardButtonUrl):
                    return True

    # 4. Fallback: check for raw URL pattern in text
    #    Handles cases where Telegram didn't parse the URL as an entity
    #    (e.g., some bot messages, or URLs inside code blocks)
    text = getattr(msg, "text", None) or getattr(msg, "message", None)
    if text and _URL_RE.search(text):
        return True

    return False


# ── Message classification ────────────────────────────────────────────────────

def classify_message(msg) -> str:
    """
    Classify a message into exactly ONE type based on priority.

    Priority order (highest to lowest):
        1. ``file``   — Document with filename, no video/audio/sticker attributes
        2. ``vid``    — Document with video attribute
        3. ``pic``    — Photo or photo-like document
        4. ``link``   — WebPage preview, URL entity, inline keyboard URL button,
                        or raw URL in text
        5. ``txt``    — Plain text message (no media, no links)
        6. ``other``  — Stickers, voice messages, contacts, locations, etc.

    This ensures each message has exactly one classification, avoiding
    ambiguity in filtering operations.

    Args:
        msg: A Telethon Message object.

    Returns:
        One of: ``"file"``, ``"vid"``, ``"pic"``, ``"link"``, ``"txt"``, ``"other"``

    Example:
        >>> classify_message(msg_with_video)
        'vid'
        >>> classify_message(msg_with_link)
        'link'
        >>> classify_message(msg_with_inline_url_button)
        'link'
        >>> classify_message(plain_text_msg)
        'txt'
    """
    if not msg:
        return "other"

    media = getattr(msg, "media", None)

    # Priority 1: File (document without video/audio/sticker)
    if is_file(media):
        return "file"

    # Priority 2: Video
    if is_video(media):
        return "vid"

    # Priority 3: Photo
    if is_photo(media):
        return "pic"

    # Priority 4: Link (WebPage, URL entity, inline keyboard URL, or raw URL)
    if is_link(msg):
        return "link"

    # Priority 5: Plain text
    text = getattr(msg, "text", None) or getattr(msg, "message", None)
    if text:
        return "txt"

    # Priority 6: Other (stickers, voice, contacts, locations, etc.)
    return "other"


# ── File info ─────────────────────────────────────────────────────────────────

def get_file_extension(media) -> str | None:
    """
    Extract the lowercase file extension from a document attribute.

    Returns a string like ``".pdf"`` or ``None`` if not available.

    Example:
        >>> get_file_extension(msg.media)
        '.pdf'
    """
    if not isinstance(media, MessageMediaDocument) or not media.document:
        return None
    for attr in media.document.attributes:
        if isinstance(attr, DocumentAttributeFilename):
            parts = attr.file_name.rsplit(".", 1)
            if len(parts) == 2:
                return f".{parts[1].lower()}"
    return None


def get_file_size(size_bytes: int | None) -> str:
    """
    Format *size_bytes* as a human-readable string (e.g. ``"1.23 MB"``).

    Returns ``"Unknown"`` for ``None`` input.

    Example:
        >>> get_file_size(1048576)
        '1.0 MB'
        >>> get_file_size(None)
        'Unknown'
    """
    if size_bytes is None:
        return "Unknown"
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx   = min(int(math.floor(math.log(size_bytes, 1024))), len(units) - 1)
    val   = round(size_bytes / math.pow(1024, idx), 2)
    return f"{val} {units[idx]}"


async def get_media_info(media) -> str:
    """
    Build a multi-line string describing the metadata of *media*.

    Returns an empty string if *media* carries no useful metadata.

    Example:
        >>> info = await get_media_info(msg.media)
        >>> print(info)
        ID: 1234567890
        Size: 1.5 MB
        Filename: document.pdf
    """
    lines: list[str] = []
    if isinstance(media, MessageMediaDocument) and media.document:
        doc = media.document
        lines.append(f"ID: {doc.id}")
        lines.append(f"Size: {get_file_size(doc.size)}")
        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                lines.append(f"Filename: {attr.file_name}")
            elif isinstance(attr, DocumentAttributeVideo):
                lines.append(f"Duration: {attr.duration}s")
                lines.append(f"Dimensions: {attr.w}×{attr.h}")
                if attr.supports_streaming:
                    lines.append("Streaming: Yes")
            elif isinstance(attr, DocumentAttributeSticker):
                lines.append(f"Sticker emoji: {attr.alt}")
            elif isinstance(attr, DocumentAttributeAudio):
                kind = "Voice" if attr.voice else "Audio"
                lines.append(f"Type: {kind}")
                if attr.duration:
                    lines.append(f"Duration: {attr.duration}s")
    elif isinstance(media, MessageMediaPhoto):
        lines.append("Type: Photo")
    return "\n".join(lines)


# ── URL detection ─────────────────────────────────────────────────────────────

def contains_any_link(text: str | None) -> bool:
    """
    Return ``True`` if *text* contains at least one HTTP/HTTPS URL.

    Example:
        >>> contains_any_link("Check out https://example.com")
        True
        >>> contains_any_link("No links here")
        False
    """
    return bool(text and _URL_RE.search(text))


# ── JSON settings persistence ────────────────────────────────────────────────
#
# Extracted in version 3.0.1 from four independent, near-identical
# implementations (auto_clearer.py, auto_forwarder.py, join_left.py — both
# its main settings and its separate invite cache — and reaction_commands.py).
# One of the four (auto_clearer.py) turned out NOT to write atomically, unlike
# the other three — a genuine inconsistency, now fixed as a side effect of
# sharing this implementation rather than each module's own copy.
#
# Deliberately NOT extracted: each module's own validation, type-coercion,
# and migration logic on the loaded dict (e.g. auto_clearer.py's per-key
# bool()/int() coercion and its "add `link` key if missing" migration). That
# logic differs enough between modules that unifying it would risk silently
# changing one module's validation rules to match another's — only the
# shared "read this file defensively" / "write this file atomically"
# mechanics are extracted here.

def read_json_file(path: Path) -> tuple[dict | None, Exception | None]:
    """
    Defensively read and parse a JSON object file.

    Returns a ``(data, error)`` pair with exactly one of three shapes:
        (dict, None)   — file existed and parsed successfully.
        (None, None)   — file does not exist. This is the expected,
                         silent first-run case — callers should proceed
                         with their own defaults, not log anything.
        (None, exc)    — file exists but could not be read or parsed.
                         Callers should log *exc* and fall back to
                         defaults, since this indicates real corruption
                         rather than an expected missing-file state.

    Never raises.

    Example:
        >>> data, err = read_json_file(settings_path)
        >>> if err is not None:
        ...     log.error("settings load error: %s", err)
        >>> if data is not None:
        ...     apply(data)
    """
    if not path.exists():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (json.JSONDecodeError, OSError) as exc:
        return None, exc


def write_json_file_atomic(
    path: Path,
    data: dict,
    *,
    indent: int = 4,
    tmp_suffix: str = ".tmp",
) -> Exception | None:
    """
    Atomically write *data* as JSON to *path*.

    Writes to a sibling temp file first (``path.with_suffix(tmp_suffix)``),
    then renames it over *path*. This prevents a crash mid-write from ever
    leaving a truncated/corrupted settings file — a partial write only ever
    touches the temp file; the real file is replaced in one atomic
    filesystem operation.

    Creates *path*'s parent directory if it doesn't exist yet.

    Args:
        path:       Destination file.
        data:       JSON-serializable value to write.
        indent:     `json.dumps` indent (default matches most callers; pass
                    `indent=2` for the more compact style a couple of
                    modules use for smaller cache-style files).
        tmp_suffix: Suffix for the temp file, applied via `Path.with_suffix`.
                    Override this if *path* already has meaningful dots in
                    its name that plain `.with_suffix(".tmp")` would clobber,
                    or to keep an existing distinct temp-file naming
                    convention (e.g. join_left.py's invite cache uses
                    ``.cache.tmp`` to keep it visually distinct from its
                    main settings file's own temp file).

    Returns:
        ``None`` on success, or the exception on failure. Never raises.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=indent)
        tmp_path = path.with_suffix(tmp_suffix)
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(path)
        return None
    except OSError as exc:
        return exc


# ── Entity/user formatting (v3.0.10) ─────────────────────────────────────────
#
# Extracted from whois_handler.py's `_build_user_info` and info_handler.py's
# `_get_sender_section`, which each independently built near-identical flag
# badge lists and status text, with small inconsistencies between the two
# (e.g. only whois_handler showed the "خودتان" self-flag or the online-status
# expiry time). Both call sites now use these shared versions.

def truncate(text: str, n: int) -> str:
    """
    Truncate *text* to at most *n* characters, appending an ellipsis (…) if
    it was actually cut. Safe for ``None``/empty input.

    Example:
        >>> truncate("hello world", 5)
        'hello…'
        >>> truncate("hi", 5)
        'hi'
    """
    if not text:
        return text or ""
    return text[:n] + ("…" if len(text) > n else "")


def format_user_flags(user, *, include_self: bool = False) -> list[str]:
    """
    Build the standard list of badge strings (Bot / Verified / Premium /
    Scam / Fake / Deleted / …) for a Telethon ``User`` entity, based only on
    the boolean flags the API already exposes on that entity.

    Args:
        user:         A Telethon ``User`` object (or any object exposing the
                      same boolean attributes via ``getattr``).
        include_self: If ``True``, appends "👤 خودتان" when ``user.self`` is
                      set (whois_handler's behavior). info_handler's sender
                      section does not show this, so it defaults to ``False``.

    Returns:
        List of human-readable flag strings, in a fixed, consistent order.
        Empty list if no flags apply.

    Example:
        >>> format_user_flags(bot_user)
        ['🤖 Bot']
    """
    flags: list[str] = []
    if getattr(user, "bot", False):
        flags.append("🤖 Bot")
    if getattr(user, "verified", False):
        flags.append("✅ Verified")
    if getattr(user, "premium", False):
        flags.append("⭐ Premium")
    if getattr(user, "scam", False):
        flags.append("⚠️ Scam")
    if getattr(user, "fake", False):
        flags.append("⚠️ Fake")
    if getattr(user, "deleted", False):
        flags.append("🗑 Deleted")
    if include_self and getattr(user, "self", False):
        flags.append("👤 خودتان")
    return flags


def format_user_status(status) -> list[str]:
    """
    Format a Telethon ``UserStatus*`` object into display-ready lines
    (already prefixed with "• **...**" bullet markup, matching this
    codebase's existing whois/info output style).

    Handles ``UserStatusOnline`` (with expiry, if present),
    ``UserStatusOffline`` (with last-seen time, if the target's privacy
    settings expose it), ``UserStatusRecently``, and falls back to a generic
    label for any other/unknown status type. Returns an empty list for
    ``None`` — this is the normal "status hidden by privacy settings" case
    and is not treated as an error anywhere that calls this helper.

    Example:
        >>> format_user_status(None)
        []
        >>> format_user_status(some_online_status)
        ['• **وضعیت آنلاین:** 🟢 آنلاین']
    """
    if status is None:
        return []

    lines: list[str] = []

    if isinstance(status, UserStatusOnline):
        lines.append("• **وضعیت آنلاین:** 🟢 آنلاین")
        expires = getattr(status, "expires", None)
        if expires:
            try:
                exp_str = expires.strftime("%Y-%m-%d %H:%M:%S UTC")
                lines.append(f"  - تا: `{exp_str}`")
            except Exception:
                pass
    elif isinstance(status, UserStatusOffline):
        was_online = getattr(status, "was_online", None)
        if was_online:
            try:
                was_str = was_online.strftime("%Y-%m-%d %H:%M:%S UTC")
                lines.append(f"• **آخرین بازدید:** `{was_str}`")
            except Exception:
                pass
        else:
            lines.append("• **آخرین بازدید:** نامشخص")
    elif isinstance(status, UserStatusRecently):
        lines.append("• **آخرین بازدید:** اخیراً")
    else:
        status_name = type(status).__name__.replace("UserStatus", "")
        lines.append(f"• **وضعیت:** {status_name}")

    return lines


async def get_profile_photos_safe(client, entity, limit: int = 1) -> list:
    """
    Fetch up to *limit* of *entity*'s most recent profile photos via
    ``client.get_profile_photos()``. Defaults to ``limit=1`` — callers that
    only need the single most recent photo (the common case) get back a
    list of at most one ``Photo`` object.

    This only ever returns what Telegram's API is willing to expose under
    the target's own privacy settings — if photos are hidden or none are
    set, ``get_profile_photos`` itself returns an empty list (not an error),
    and that is passed through unchanged here. No attempt is made to work
    around or bypass that.

    Zero-download note: the ``Photo`` objects returned here carry a server
    file reference only — no image bytes are downloaded by this call. A
    caller that passes one of these ``Photo`` objects straight into
    ``client.send_file(chat, file=photo, ...)`` gets a server-side
    copy/forward (Telegram MTProto handles it entirely on their end), not a
    download-then-reupload round trip. Only calling something like
    ``client.download_media(photo)`` would pull bytes locally — this helper
    never does that.

    ``FloodWaitError`` is deliberately re-raised (not swallowed) so callers
    can surface a "please wait N seconds" message, matching the FloodWait
    convention used everywhere else in this codebase. Any other exception is
    logged and treated as "no photos available" — a fetch failure should
    degrade to text-only output, not crash the calling command.

    Args:
        client: Active ``TelegramClient``.
        entity: The user/channel/chat entity to fetch photos for.
        limit:  Max number of photos to return (default 1).

    Returns:
        List of ``Photo`` objects (possibly empty), most-recent-first.

    Example:
        >>> photos = await get_profile_photos_safe(client, user)
        >>> if photos:
        ...     await client.send_file(chat, file=photos[0], caption=info_text)
    """
    try:
        photos = await client.get_profile_photos(entity, limit=limit)
        return list(photos) if photos else []
    except errors.FloodWaitError:
        raise
    except Exception as exc:
        log.debug("get_profile_photos_safe failed for %s: %s", entity, exc)
        return []