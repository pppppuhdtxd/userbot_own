"""
userbot_own/modules/packages/info_handler/labels.py
════════════════════════════════════════════════════════════════
Display labels for the message types and the entity types.

Pure data — the Persian text the `info` command shows, kept apart from
the logic so it can be edited (or translated) without touching any
behaviour.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from telethon.tl.types import (
    MessageEntityBlockquote,
    MessageEntityBold,
    MessageEntityBotCommand,
    MessageEntityCode,
    MessageEntityEmail,
    MessageEntityHashtag,
    MessageEntityItalic,
    MessageEntityMention,
    MessageEntityMentionName,
    MessageEntityPhone,
    MessageEntityPre,
    MessageEntitySpoiler,
    MessageEntityStrike,
    MessageEntityTextUrl,
    MessageEntityUnderline,
    MessageEntityUrl,
)

# ── Entity type labels ──────────────────────────────────────────────────────

_ENTITY_LABELS: dict[type, str] = {
    MessageEntityBold:           "Bold",
    MessageEntityItalic:         "Italic",
    MessageEntityUnderline:      "Underline",
    MessageEntityStrike:         "Strikethrough",
    MessageEntitySpoiler:        "Spoiler",
    MessageEntityCode:           "Code",
    MessageEntityPre:            "Pre (code block)",
    MessageEntityBlockquote:     "Quote",
    MessageEntityTextUrl:        "Text URL",
    MessageEntityUrl:            "URL",
    MessageEntityEmail:          "Email",
    MessageEntityPhone:          "Phone",
    MessageEntityHashtag:        "Hashtag",
    MessageEntityMention:        "Mention",
    MessageEntityMentionName:    "Text Mention",
    MessageEntityBotCommand:     "Bot Command",
}


# ── Type display labels (Persian for user-facing output) ────────────────────

_TYPE_LABELS: dict[str, str] = {
    "file":   "📎 فایل (Document)",
    "vid":    "🎬 ویدیو (Video)",
    "pic":    "🖼 عکس (Photo)",
    "link":   "🔗 لینک (WebPage/URL)",
    "txt":    "📝 متن خالص (Text)",
    "other":  "📦 سایر (Sticker/Voice/Contact/...)",
}
