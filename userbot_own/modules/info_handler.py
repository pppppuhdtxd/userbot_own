"""
userbot_own/modules/info_handler.py
════════════════════════════════════════════════════════════════
Message Information — نمایش اطلاعات جامع پیام

دستور (قابل استفاده در هر چتی):
- `info` (reply) — نمایش اطلاعات کامل پیام reply شده

Features:
- Basic info: ID, date, sender, chat type
- Message classification: file/vid/pic/link/txt/other (priority system)
- Media details: photo, video, file, sticker, voice, video note
- Link details: WebPage preview info, URL entities, inline keyboard URL buttons
- Message flags: edited, forwarded, pinned, silent, mentioned
- Text formatting entities: bold, italic, code, spoiler, mention, etc.
- Reply chain: shows replied-to message info
- Views and forwards statistics (for channels)

Message Classification System
──────────────────────────────
Each message is classified into exactly ONE type based on priority:
    file > vid > pic > link > txt > other

This module uses the shared `classify_message()` helper for consistency
with `clearer.py` and `auto_clearer.py`.

v3.1.7 Changes:
  F2 — _photo_details now handles document-photos (images sent as document
       attachments, e.g. photo.jpg). Previously it returned empty details for
       any pic-classified message whose media was MessageMediaDocument instead
       of MessageMediaPhoto. Now shows File ID, size, MIME type, filename,
       extension, and image dimensions (DocumentAttributeImageSize if present).

  F3 — Removed dead audio-handling branches from _file_details.
       DocumentAttributeAudio checks (lines 307-318 in v3.1.6) were
       permanently unreachable because is_file() explicitly returns False when
       is_audio() is True. Audio documents are classified as 'other', never
       'file'. The matching audio detail display has been moved to _other_details
       where it is actually reachable.

  F4 — _link_details now shows inline keyboard URL buttons (glass buttons /
       دکمه‌های شیشه‌ای). Previously, a message classified as 'link' solely
       because of KeyboardButtonUrl in its reply_markup showed only the section
       header with no content. Now lists button labels and URLs (capped at 5,
       URLs truncated at 100 chars).
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from telethon import TelegramClient, errors, events
from telethon.tl.types import (
    Channel,
    Chat,
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeImageSize,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    KeyboardButtonUrl,
    Message,
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
    MessageMediaContact,
    MessageMediaDice,
    MessageMediaDocument,
    MessageMediaGame,
    MessageMediaGeo,
    MessageMediaGeoLive,
    MessageMediaInvoice,
    MessageMediaPhoto,
    MessageMediaPoll,
    MessageMediaVenue,
    MessageMediaWebPage,
    ReplyInlineMarkup,
    User,
    UserStatusOffline,
    UserStatusOnline,
    WebPage,
    WebPageEmpty,
    WebPagePending,
)

from userbot_own.core.context import ModuleContext
from userbot_own.helpers.utils import (
    classify_message,
    format_user_flags,
    get_file_extension,
    get_file_size,
    truncate,
)
from userbot_own.modules.base import Module

# Logging is provided by Module._log_* helpers; no module-level logger needed.


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


# ── Module ──────────────────────────────────────────────────────────────────

class InfoHandler(Module):
    name = "info_handler"
    category = "info"
    desc = "اطلاعات پیام"

    def setup(self, client: TelegramClient) -> None:
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_command)
        self._log_info("InfoHandler ready.")

    # ── Command dispatcher ─────────────────────────────────────────────────

    async def _on_command(self, event) -> None:
        text = (event.raw_text or "").strip().lower()
        if text != "info":
            return

        # Must be a reply
        reply = await event.get_reply_message()
        if reply is None:
            await self._safe_edit(
                event,
                "❌ **این دستور باید به عنوان reply استفاده شود.**\n\n"
                "یک پیام را reply کنید و `info` را بفرستید."
            )
            return

        await self._safe_edit(event, "🔍 در حال جمع‌آوری اطلاعات...")

        try:
            info_text = await self._build_info(event.client, reply)
            await self._safe_edit(event, info_text)
        except Exception as exc:
            self._log_error("Info handler error: %s", exc)
            await self._safe_edit(event, f"❌ خطا در دریافت اطلاعات: `{exc}`")

    # ── Info builder ───────────────────────────────────────────────────────

    async def _build_info(self, client: TelegramClient, msg: Message) -> str:
        """Build the full info text for a message."""
        lines: list[str] = ["📊 **تحلیل جامع پیام**\n"]

        # ── 1. Identifiers ──────────────────────────────────────────────
        lines.append("**🔗 شناسه‌ها:**")
        lines.append(f"• Message ID: `{msg.id}`")
        lines.append(f"• Chat ID: `{msg.chat_id}`")
        lines.append(f"• Sender ID: `{msg.sender_id}`")
        lines.append("")

        # ── 2. Time ─────────────────────────────────────────────────────
        lines.append("**⏰ زمان:**")
        if msg.date:
            date_str = msg.date.strftime("%Y-%m-%d %H:%M:%S UTC")
            lines.append(f"• ارسال: `{date_str}`")
        if msg.edit_date:
            edit_str = msg.edit_date.strftime("%Y-%m-%d %H:%M:%S UTC")
            lines.append(f"• ویرایش: `{edit_str}`")
        else:
            lines.append("• ویرایش: ویرایش نشده")
        lines.append("")

        # ── 3. Statistics (channels only) ───────────────────────────────
        if msg.views is not None or msg.forwards is not None:
            lines.append("**📈 آمار:**")
            if msg.views is not None:
                lines.append(f"• بازدید: `{msg.views}`")
            if msg.forwards is not None:
                lines.append(f"• فوروارد: `{msg.forwards}`")
            lines.append("")
        else:
            lines.append("**📈 آمار:**")
            lines.append("• آمار خاصی موجود نیست")
            lines.append("")

        # ── 4. Content classification ───────────────────────────────────
        msg_type = classify_message(msg)
        type_label = _TYPE_LABELS.get(msg_type, f"`{msg_type}`")
        text_len = len(msg.text or msg.message or "")
        word_count = len((msg.text or msg.message or "").split()) if (msg.text or msg.message) else 0

        lines.append("**📝 محتوا:**")
        lines.append(f"• نوع: **{type_label}**")
        lines.append(f"• متن: `{text_len}` کاراکتر | `{word_count}` کلمه")

        # Message flags (safe access via getattr to avoid AttributeError)
        flags = []
        if msg.edit_date:
            flags.append("ویرایش‌شده")
        if getattr(msg, 'forward', None):
            flags.append("فوروارد شده")
        if getattr(msg, 'pinned', False):
            flags.append("سنجاق شده")
        if getattr(msg, 'silent', False):
            flags.append("بی‌صدا")
        if getattr(msg, 'mentioned', False):
            flags.append("منشن شده")
        if getattr(msg, 'out', False):
            flags.append("خروجی")
        if getattr(msg, 'noforwards', False):
            flags.append("🚫 بدون فوروارد")
        if getattr(msg, 'from_scheduled', False):
            flags.append("زمان‌بندی شده")
        if flags:
            lines.append(f"• ویژگی‌ها: {', '.join(flags)}")
        lines.append("")

        # ── 5. Type-specific details ────────────────────────────────────
        type_details = self._get_type_details(msg, msg_type)
        if type_details:
            lines.append(type_details)
            lines.append("")

        # ── 6. Entities ─────────────────────────────────────────────────
        entity_section = self._get_entities_section(msg)
        if entity_section:
            lines.append(entity_section)
            lines.append("")

        # ── 7. Sender info ──────────────────────────────────────────────
        sender_section = await self._get_sender_section(client, msg)
        if sender_section:
            lines.append(sender_section)
            lines.append("")

        # ── 8. Chat info ────────────────────────────────────────────────
        chat_section = await self._get_chat_section(client, msg)
        if chat_section:
            lines.append(chat_section)
            lines.append("")

        # ── 9. Reply info ───────────────────────────────────────────────
        if msg.is_reply:
            reply_section = await self._get_reply_section(client, msg)
            if reply_section:
                lines.append(reply_section)
                lines.append("")

        return "\n".join(lines)

    # ── Type-specific details ─────────────────────────────────────────────

    def _get_type_details(self, msg: Message, msg_type: str) -> str:
        """Return detailed info specific to the message type."""
        if msg_type == "file":
            return self._file_details(msg)
        elif msg_type == "vid":
            return self._video_details(msg)
        elif msg_type == "pic":
            return self._photo_details(msg)
        elif msg_type == "link":
            return self._link_details(msg)
        elif msg_type == "txt":
            return ""
        elif msg_type == "other":
            return self._other_details(msg)
        return ""

    def _file_details(self, msg: Message) -> str:
        """Details for file-type messages.

        NOTE (F3, v3.1.7): The DocumentAttributeAudio branches that existed
        here in v3.1.6 were permanently unreachable dead code. is_file() in
        utils.py explicitly returns False when is_audio() is True, so any
        document with DocumentAttributeAudio is classified as 'other', never
        'file'. The audio detail display has been moved to _other_details()
        where is_audio documents actually arrive.
        """
        media = msg.media
        if not isinstance(media, MessageMediaDocument) or not media.document:
            return ""

        lines = ["**📎 جزئیات فایل:**"]
        lines.append(f"• File ID: `{media.document.id}`")
        lines.append(f"• حجم: `{get_file_size(media.document.size)}`")

        ext = get_file_extension(media)
        if ext:
            lines.append(f"• فرمت: `{ext}`")

        mime = media.document.mime_type
        if mime:
            lines.append(f"• MIME: `{mime}`")

        for attr in media.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                lines.append(f"• نام فایل: `{attr.file_name}`")
                break

        # DocumentAttributeAnimated: GIF file sent as document without
        # DocumentAttributeVideo (e.g. uploaded as a .gif file attachment).
        # This IS reachable: is_video() returns False (no AttributeVideo),
        # is_file() returns True (has AttributeFilename, no audio/sticker).
        for attr in media.document.attributes:
            if isinstance(attr, DocumentAttributeAnimated):
                lines.append("• نوع: **GIF (Animated)**")
                break

        return "\n".join(lines)

    def _video_details(self, msg: Message) -> str:
        """Details for video-type messages."""
        media = msg.media
        if not isinstance(media, MessageMediaDocument) or not media.document:
            return ""

        lines = ["**🎬 جزئیات ویدیو:**"]
        lines.append(f"• Video ID: `{media.document.id}`")
        lines.append(f"• حجم: `{get_file_size(media.document.size)}`")

        mime = media.document.mime_type
        if mime:
            lines.append(f"• MIME: `{mime}`")

        for attr in media.document.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                lines.append(f"• مدت: `{attr.duration}s`")
                lines.append(f"• ابعاد: `{attr.w}×{attr.h}`")
                if attr.supports_streaming:
                    lines.append("• Streaming: ✅")
                if attr.round_message:
                    lines.append("• نوع: **Video Note (دایره‌ای)**")
            elif isinstance(attr, DocumentAttributeFilename):
                lines.append(f"• نام: `{attr.file_name}`")

        return "\n".join(lines)

    def _photo_details(self, msg: Message) -> str:
        """Details for photo-type messages.

        F2 (v3.1.7): classify_message() can return 'pic' for two kinds of
        media:
        1. MessageMediaPhoto   — native photo sent via the photo UI
        2. MessageMediaDocument — image file sent as a document attachment
           (e.g. photo.jpg, image.png) that has an image extension but no
           DocumentAttributeVideo or DocumentAttributeSticker.

        Previously this method only handled case 1 and returned "" for case 2,
        leaving the 'جزئیات عکس' section silently empty for document-photos.
        Now both cases produce a details section.
        """
        media = msg.media

        if isinstance(media, MessageMediaPhoto) and media.photo:
            # ── Native photo (MessageMediaPhoto) ──────────────────────────
            lines = ["**🖼 جزئیات عکس:**"]
            lines.append(f"• Photo ID: `{media.photo.id}`")

            if hasattr(media.photo, "sizes") and media.photo.sizes:
                max_size = max(
                    (s for s in media.photo.sizes if hasattr(s, "w") and hasattr(s, "h")),
                    key=lambda s: getattr(s, "w", 0) * getattr(s, "h", 0),
                    default=None,
                )
                if max_size:
                    w = getattr(max_size, "w", "?")
                    h = getattr(max_size, "h", "?")
                    lines.append(f"• ابعاد: `{w}×{h}`")
                    if hasattr(max_size, "size"):
                        lines.append(f"• حجم: `{get_file_size(max_size.size)}`")

            return "\n".join(lines)

        elif isinstance(media, MessageMediaDocument) and media.document:
            # ── Document-photo (image file sent as document attachment) ───
            # is_photo() returns True for MessageMediaDocument whose filename
            # ends in .jpg/.jpeg/.png/.bmp/.webp AND has no DocumentAttribute-
            # Video or DocumentAttributeSticker. Show document-level details.
            lines = ["**🖼 جزئیات عکس (فایل سند):**"]
            lines.append(f"• File ID: `{media.document.id}`")

            doc_size = getattr(media.document, "size", None)
            if doc_size is not None:
                lines.append(f"• حجم: `{get_file_size(doc_size)}`")

            mime = getattr(media.document, "mime_type", None)
            if mime:
                lines.append(f"• MIME: `{mime}`")

            for attr in (media.document.attributes or []):
                if isinstance(attr, DocumentAttributeFilename):
                    lines.append(f"• نام فایل: `{attr.file_name}`")
                    ext = get_file_extension(media)
                    if ext:
                        lines.append(f"• فرمت: `{ext}`")
                elif isinstance(attr, DocumentAttributeImageSize):
                    # DocumentAttributeImageSize carries the pixel dimensions
                    # of image documents (fields: w, h).
                    lines.append(f"• ابعاد: `{attr.w}×{attr.h}`")

            return "\n".join(lines)

        return ""

    @staticmethod
    def _utf16_slice(text: str, offset: int, length: int) -> str:
        """
        Slice *text* using Telegram's entity offset/length, which are
        counted in UTF-16 code units — not Python code points. Plain
        `text[offset:offset+length]` slicing (v3.0.8 and earlier) gives
        the wrong substring for any text containing characters outside
        the Basic Multilingual Plane (most emoji included) before the
        entity, since those become UTF-16 surrogate pairs (2 code units)
        but remain a single Python code point. Round-tripping through
        UTF-16 bytes makes the offsets line up correctly either way.
        """
        try:
            encoded = text.encode("utf-16-le")
            piece = encoded[offset * 2: (offset + length) * 2]
            return piece.decode("utf-16-le", errors="ignore")
        except Exception:
            return text[offset:offset + length]

    def _link_details(self, msg: Message) -> str:
        """Details for link-type messages (WebPage or URL entities).

        F4 (v3.1.7): Added inline keyboard URL button display. is_link() in
        utils.py has three detection paths: (1) MessageMediaWebPage,
        (2) URL entities, (3) KeyboardButtonUrl in ReplyInlineMarkup. Previously
        only paths 1 and 2 produced output here. A message classified as 'link'
        solely via path 3 would show only the section header with no content.
        Now, if reply_markup contains KeyboardButtonUrl entries, they are listed
        (capped at 5, URLs truncated at 100 chars for readability).
        """
        lines = ["**🔗 جزئیات لینک:**"]

        media = msg.media

        if isinstance(media, MessageMediaWebPage) and media.webpage:
            wp = media.webpage
            if isinstance(wp, WebPage):
                lines.append("• نوع: **WebPage Preview**")
                if wp.url:
                    lines.append(f"• URL: `{wp.url}`")
                if wp.site_name:
                    lines.append(f"• Site: `{wp.site_name}`")
                if wp.title:
                    lines.append(f"• Title: `{truncate(wp.title, 100)}`")
                if wp.description:
                    lines.append(f"• Description: `{truncate(wp.description, 150)}`")
                if wp.duration:
                    lines.append(f"• Duration: `{wp.duration}s`")
                if wp.author:
                    lines.append(f"• Author: `{wp.author}`")
            elif isinstance(wp, WebPagePending):
                lines.append("• نوع: **WebPage Pending** (در حال تولید preview)")
            elif isinstance(wp, WebPageEmpty):
                lines.append("• نوع: **WebPage Empty**")

        entities = getattr(msg, "entities", None) or []
        url_entities = [
            e for e in entities
            if isinstance(e, (MessageEntityUrl, MessageEntityTextUrl))
        ]
        if url_entities:
            lines.append(f"• تعداد URL entities: `{len(url_entities)}`")

            text = msg.text or msg.message or ""
            shown = 0
            for e in url_entities:
                if shown >= 5:
                    remaining = len(url_entities) - 5
                    if remaining > 0:
                        lines.append(f"  … و `{remaining}` لینک دیگر")
                    break
                url_text = self._utf16_slice(text, e.offset, e.length) if text else ""
                if isinstance(e, MessageEntityTextUrl) and e.url:
                    lines.append(f"  - `{url_text}` → `{e.url}`")
                else:
                    lines.append(f"  - `{url_text}`")
                shown += 1

        # F4: Inline keyboard URL buttons (glass/web buttons — دکمه‌های شیشه‌ای)
        # is_link() classifies a message as 'link' when ReplyInlineMarkup
        # contains at least one KeyboardButtonUrl. Display those buttons here.
        reply_markup = getattr(msg, "reply_markup", None)
        if isinstance(reply_markup, ReplyInlineMarkup):
            url_buttons: list = []
            for row in (getattr(reply_markup, "rows", None) or []):
                for button in (getattr(row, "buttons", None) or []):
                    if isinstance(button, KeyboardButtonUrl):
                        url_buttons.append(button)

            if url_buttons:
                lines.append(f"• 🔘 دکمه‌های لینک (inline keyboard): `{len(url_buttons)}`")
                shown = 0
                for btn in url_buttons:
                    if shown >= 5:
                        remaining = len(url_buttons) - 5
                        if remaining > 0:
                            lines.append(f"  … و `{remaining}` دکمه دیگر")
                        break
                    label = (getattr(btn, "text", "") or "?")
                    url   = (getattr(btn, "url",  "") or "")
                    if len(url) > 100:
                        url = url[:100] + "…"
                    lines.append(f"  - `{label}` → `{url}`")
                    shown += 1

        return "\n".join(lines)

    def _other_details(self, msg: Message) -> str:
        """Details for 'other' type messages (sticker, voice, audio, contact, etc.).

        v3.1.7: Added regular audio file handling (DocumentAttributeAudio with
        voice=False — mp3, flac, ogg, etc.). Audio files are classified as
        'other' (not 'file') because is_file() returns False when is_audio()
        is True. Previously there was no detail display for non-voice audio
        files in this section. Now shows type label, artist, title, duration.
        """
        media = msg.media
        lines = ["**📦 جزئیات سایر:**"]

        if isinstance(media, MessageMediaContact):
            lines.append("• نوع: **Contact**")
            lines.append(f"• Phone: `{media.phone_number}`")
            name = f"{media.first_name or ''} {media.last_name or ''}".strip()
            if name:
                lines.append(f"• Name: `{name}`")

        elif isinstance(media, MessageMediaGeo):
            lines.append("• نوع: **Location**")
            if media.geo:
                lines.append(f"• Lat: `{media.geo.lat}`")
                lines.append(f"• Long: `{media.geo.long}`")

        elif isinstance(media, MessageMediaGeoLive):
            lines.append("• نوع: **Live Location**")
            if media.geo:
                lines.append(f"• Lat: `{media.geo.lat}`")
                lines.append(f"• Long: `{media.geo.long}`")
            if media.period:
                lines.append(f"• Period: `{media.period}s`")

        elif isinstance(media, MessageMediaVenue):
            lines.append("• نوع: **Venue**")
            if media.title:
                lines.append(f"• Title: `{media.title}`")
            if media.address:
                lines.append(f"• Address: `{media.address}`")

        elif isinstance(media, MessageMediaPoll):
            lines.append("• نوع: **Poll**")
            if media.poll:
                lines.append(f"• Question: `{truncate(media.poll.question, 100)}`")
                if media.results and media.results.total_voters is not None:
                    lines.append(f"• Votes: `{media.results.total_voters}`")

        elif isinstance(media, MessageMediaDice):
            lines.append("• نوع: **Dice**")
            lines.append(f"• Emoji: `{media.emoticon}`")
            lines.append(f"• Value: `{media.value}`")

        elif isinstance(media, MessageMediaGame):
            lines.append("• نوع: **Game**")
            if media.game:
                lines.append(f"• Title: `{media.game.title}`")

        elif isinstance(media, MessageMediaInvoice):
            lines.append("• نوع: **Invoice**")
            lines.append(f"• Title: `{media.title}`")
            if media.currency:
                lines.append(f"• Currency: `{media.currency}`")
            if media.amount:
                lines.append(f"• Amount: `{media.amount}`")

        elif isinstance(media, MessageMediaDocument) and media.document:
            for attr in media.document.attributes:
                if isinstance(attr, DocumentAttributeSticker):
                    lines.append("• نوع: **Sticker**")
                    lines.append(f"• Emoji: `{attr.alt}`")
                    if hasattr(attr, "stickerset") and attr.stickerset:
                        lines.append(f"• Set: `{getattr(attr.stickerset, 'short_name', '?')}`")
                    break
                elif isinstance(attr, DocumentAttributeAudio) and attr.voice:
                    lines.append("• نوع: **Voice Message**")
                    if attr.duration:
                        lines.append(f"• Duration: `{attr.duration}s`")
                    break
                elif isinstance(attr, DocumentAttributeAudio):
                    # F3 companion (v3.1.7): Regular audio file (.mp3, .flac,
                    # .ogg, etc.) — DocumentAttributeAudio with voice=False.
                    # These are classified as 'other' (not 'file') because
                    # is_file() explicitly excludes is_audio() documents.
                    # The matching detail display was removed from _file_details
                    # (where it was dead code) and placed here where audio
                    # documents actually arrive.
                    lines.append("• نوع: **Audio File**")
                    if getattr(attr, "performer", None):
                        lines.append(f"• Artist: `{attr.performer}`")
                    if getattr(attr, "title", None):
                        lines.append(f"• Title: `{attr.title}`")
                    if getattr(attr, "duration", None):
                        lines.append(f"• Duration: `{attr.duration}s`")
                    break

        return "\n".join(lines)

    # ── Entities section ──────────────────────────────────────────────────

    def _get_entities_section(self, msg: Message) -> str:
        """Build the entities section of the info text."""
        entities = getattr(msg, "entities", None)
        if not entities:
            return ""

        counts: dict[str, int] = {}
        for e in entities:
            label = _ENTITY_LABELS.get(type(e))
            if label:
                counts[label] = counts.get(label, 0) + 1

        if not counts:
            return ""

        lines = [f"**🧩 Entities یافت شده (`{len(entities)}`):**"]
        for label, count in sorted(counts.items(), key=lambda x: -x[1]):
            emoji = "🔗" if "URL" in label else "🎨"
            lines.append(f"• {emoji} {count}× {label}")

        return "\n".join(lines)

    # ── Sender section ────────────────────────────────────────────────────

    async def _get_sender_section(self, client: TelegramClient, msg: Message) -> str:
        """Build the sender info section."""
        try:
            sender = await msg.get_sender()
        except errors.FloodWaitError:
            # v3.0.9 fix: previously swallowed here (and in
            # _get_chat_section / _get_reply_section below), unlike
            # whois_handler.py's deliberate re-raise pattern — a flood
            # wait would just silently show "not available" with no
            # indication to the user that a rate limit was hit.
            raise
        except Exception:
            sender = None

        lines = ["**👤 فرستنده:**"]

        if sender is None:
            lines.append(f"• ID: `{msg.sender_id}`")
            lines.append("• وضعیت: ❓ در دسترس نیست")
            return "\n".join(lines)

        if isinstance(sender, User):
            name_parts = []
            if sender.first_name:
                name_parts.append(sender.first_name)
            if sender.last_name:
                name_parts.append(sender.last_name)
            name = " ".join(name_parts) or "Unknown"
            lines.append(f"• نام: `{name}`")

            if sender.username:
                lines.append(f"• یوزرنیم: @{sender.username}")

            lines.append(f"• ID: `{sender.id}`")

            flags = format_user_flags(sender, include_self=False)
            if flags:
                lines.append(f"• وضعیت: {', '.join(flags)}")
            else:
                lines.append("• وضعیت: 👤 کاربر عادی")

            if sender.status:
                if isinstance(sender.status, UserStatusOnline):
                    lines.append("• آنلاین: 🟢 Online")
                elif isinstance(sender.status, UserStatusOffline):
                    if sender.status.was_online:
                        was_str = sender.status.was_online.strftime("%Y-%m-%d %H:%M")
                        lines.append(f"• آخرین بازدید: `{was_str}`")

        elif isinstance(sender, Channel):
            # v3.0.10 fix: previously always printed the generic
            # "Group/Channel" label for anonymous channel-admin posts /
            # linked-channel posts, unlike _get_chat_section below, which
            # already distinguishes Channel vs. Supergroup for the same
            # underlying entity type. Now uses the same labeling.
            if sender.broadcast:
                lines.append("• نوع: 📢 Channel")
            elif sender.megagroup:
                lines.append("• نوع: 👥 Supergroup")
            else:
                lines.append("• نوع: 📢 Channel/Group")
            if hasattr(sender, "title"):
                lines.append(f"• Title: `{sender.title}`")
            lines.append(f"• ID: `{sender.id}`")

        elif isinstance(sender, Chat):
            lines.append("• نوع: 👥 Group (Basic)")
            if hasattr(sender, "title"):
                lines.append(f"• Title: `{sender.title}`")
            lines.append(f"• ID: `{sender.id}`")
        else:
            lines.append(f"• ID: `{msg.sender_id}`")

        return "\n".join(lines)

    # ── Chat section ──────────────────────────────────────────────────────

    async def _get_chat_section(self, client: TelegramClient, msg: Message) -> str:
        """Build the chat info section."""
        try:
            chat = await msg.get_chat()
        except errors.FloodWaitError:
            raise
        except Exception:
            chat = None

        lines = ["**🌐 چت:**"]

        if chat is None:
            lines.append(f"• Chat ID: `{msg.chat_id}`")
            return "\n".join(lines)

        if isinstance(chat, User):
            if chat.id == msg.sender_id:
                lines.append("• نوع: 👤 User (Private)")
            else:
                lines.append("• نوع: 👤 User")
            if chat.username:
                lines.append(f"• لینک: t.me/{chat.username}")
            elif chat.bot:
                lines.append("• نوع: 🤖 Bot Chat")

        elif isinstance(chat, Channel):
            if chat.broadcast:
                lines.append("• نوع: 📢 Channel")
            elif chat.megagroup:
                lines.append("• نوع: 👥 Supergroup")
            else:
                lines.append("• نوع: 📢 Channel/Group")
            if chat.title:
                lines.append(f"• Title: `{chat.title}`")
            if chat.username:
                lines.append(f"• لینک: t.me/{chat.username}")
            if hasattr(chat, "participants_count") and chat.participants_count:
                lines.append(f"• اعضا: `{chat.participants_count}`")

        elif isinstance(chat, Chat):
            lines.append("• نوع: 👥 Group (Basic)")
            if chat.title:
                lines.append(f"• Title: `{chat.title}`")
            if hasattr(chat, "participants_count") and chat.participants_count:
                lines.append(f"• اعضا: `{chat.participants_count}`")
        else:
            lines.append(f"• Chat ID: `{msg.chat_id}`")

        return "\n".join(lines)

    # ── Reply section ─────────────────────────────────────────────────────

    async def _get_reply_section(self, client: TelegramClient, msg: Message) -> str:
        """Build the reply chain info."""
        try:
            reply = await msg.get_reply_message()
        except errors.FloodWaitError:
            raise
        except Exception:
            reply = None

        if reply is None:
            return ""

        lines = ["**↩️ Reply به:**"]
        lines.append(f"• Message ID: `{reply.id}`")

        reply_type = classify_message(reply)
        type_label = _TYPE_LABELS.get(reply_type, f"`{reply_type}`")
        lines.append(f"• نوع: {type_label}")

        if reply.sender_id:
            lines.append(f"• Sender ID: `{reply.sender_id}`")

        preview = (reply.text or reply.message or "")
        if preview:
            preview = truncate(preview, 100).replace("\n", " ")
            lines.append(f"• Preview: `{preview}`")

        return "\n".join(lines)


# ── Help Texts (در انتهای ماژول طبق قوانین) ─────────────────────────────────

help_text = (
    "• `info` (reply) | نمایش اطلاعات کامل پیام reply شده\n"
)

help_extra = (
    "اطلاعات پیام - نمایش اطلاعات جامع پیام reply شده\n\n"
    "دستور اصلی:\n"
    "• `info` (reply) | نمایش اطلاعات کامل پیام reply شده\n\n"
    "اطلاعات نمایش داده‌شده:\n"
    "• شناسه‌ها | Message ID, Chat ID, Sender ID\n"
    "• زمان | زمان ارسال و ویرایش\n"
    "• آمار | بازدید و فوروارد (برای کانال‌ها)\n"
    "• محتوا | نوع پیام بر اساس سیستم طبقه‌بندی\n"
    "• Entities | Bold, Italic, Code, URL, Mention و غیره\n"
    "• فرستنده | نام، یوزرنیم، ID، وضعیت (Bot/Verified/Premium)، "
    "نوع Channel/Supergroup/Group برای فرستنده‌های ناشناس\n"
    "• چت | نوع چت، عنوان، لینک عمومی، تعداد اعضا\n"
    "• Reply | اطلاعات پیام reply شده\n\n"
    "جزئیات اختصاصی هر نوع:\n"
    "• فایل | File ID, حجم، فرمت، MIME type\n"
    "• ویدیو | Video ID, مدت، ابعاد، Streaming support, نوع (دایره‌ای/معمولی)\n"
    "• عکس | Photo ID, ابعاد بزرگ‌ترین سایز، حجم تقریبی\n"
    "  ↳ عکس ارسال‌شده به صورت فایل سند هم پشتیبانی می‌شود (File ID, حجم، ابعاد)\n"
    "• لینک | WebPage Preview (URL, Site, Title, Description)\n"
    "  ↳ URL entities در متن\n"
    "  ↳ دکمه‌های شیشه‌ای (inline keyboard URL buttons)\n"
    "• سایر | Contact, Location, Poll, Sticker, Voice, Audio File\n\n"
    "ویژگی‌های پیام:\n"
    "• ویرایش‌شده، فوروارد شده، سنجاق شده\n"
    "• بی‌صدا، منشن شده، خروجی\n"
    "• بدون فوروارد، زمان‌بندی شده\n\n"
    "مثال‌ها:\n"
    "• reply روی یک عکس + `info` | Photo ID، ابعاد، حجم\n"
    "• reply روی یک ویدیو + `info` | مدت، ابعاد، MIME\n"
    "• reply روی پیام متنی با لینک + `info` | WebPage preview\n"
    "• reply روی پیام با دکمه شیشه‌ای + `info` | دکمه‌ها و URLهای آن‌ها\n"
    "• reply روی فایل صوتی + `info` | Artist, Title, Duration\n\n"
    "نکات مهم:\n"
    "• این دستور در هر چتی قابل استفاده است\n"
    "• حتماً باید به یک پیام reply شود\n"
    "• فایل‌های صوتی (.mp3, .flac) به عنوان 'سایر' طبقه‌بندی می‌شوند\n"
    "• اطلاعات کامل WebPage برای لینک‌های deep link ربات‌ها هم نمایش داده می‌شود\n"
)

InfoHandler.help_text = help_text
InfoHandler.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return InfoHandler(context)
