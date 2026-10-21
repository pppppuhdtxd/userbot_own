"""
userbot_own/modules/packages/auto_forwarder/content.py
════════════════════════════════════════════════════════════════
What kind of content a message carries, and the module's defaults.

Two things live here:

- the content classification (txt / link / pic / vid / file) — the
  shared media predicates decide it, so it always agrees with
  ``classify_message()``
- the default settings dict and the tunables (album size, grouping
  delay)

Pure functions and data — no state, no client.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    MessageMediaWebPage,
)

from userbot_own.helpers.utils import is_audio, is_file, is_photo

# ── تنظیمات پیش‌فرض ───────────────────────────────────────────────────────────

_DEFAULT: dict[str, bool] = {
    "txt":     False,  # پیام‌های متنی محض
    "link":    False,  # پیام‌های لینک (WebPage preview)
    "pic":     False,  # عکس
    "vid":     False,  # ویدیو / گیف
    "file":    False,  # فایل
    "caption": False,  # caption پیام اصلی
}

#: The content types `autofor all` toggles. `caption` is deliberately not
#: one of them — it is a modifier (send the original caption too), not a
#: content type, and `all` never touches it.
#:
#: v3.2.2 (B-12): "link" was added. It was missing entirely, so a link
#: message (WebPage preview) was never forwarded even with `autofor all`
#: on — while `all` advertises itself as covering every type.
_ALL_CONTENT_TYPES: tuple[str, ...] = ("txt", "link", "pic", "vid", "file")

#: حداکثر آیتم در هر album (محدودیت تلگرام)
_ALBUM_MAX   = 10
#: تأخیر گروه‌بندی: اگه ظرف این مدت پیام جدید نرسه، flush می‌شه
_GROUP_DELAY = 1.0


# ── تشخیص نوع محتوا ──────────────────────────────────────────────────────────

def _content_type(msg) -> str | None:
    """
    نوع محتوای پیام را برمی‌گرداند.

    برگشتی: 'txt' | 'pic' | 'vid' | 'file' | None
    None یعنی نباید فوروارد بشه (sticker یا پیام خالی)

    v3.2.2 (B-13): the final `return "file"` used to fire for ANY document
    that carried no sticker/video/audio attribute — including a document
    with no filename at all, which the shared `is_file()` helper does NOT
    consider a file (it requires a DocumentAttributeFilename). The two
    definitions of "file" disagreed, so a document with no filename was
    classified `file` here but `other` by classify_message(). The shared
    helper is now the single authority, exactly as `is_photo` already was.

    v3.2.2 (B-12): a message carrying a WebPage preview (a link) used to
    return None here — it is neither a photo nor a document — so it was
    NEVER forwarded, even with `autofor all` on, which claims to cover
    "every type except caption". classify_message() classifies the very
    same message as `link`, so the two disagreed. It is now returned as
    "link" and forwarded like a text message (the text, including the URL,
    is sent back to the bot).
    """
    if msg.media is None:
        return "txt" if msg.message else None

    if isinstance(msg.media, MessageMediaWebPage):
        # A link message: forwarded as text (the caption + URL).
        return "link" if msg.message else None

    if is_photo(msg.media):
        return "pic"

    if hasattr(msg.media, "document") and msg.media.document:
        for attr in msg.media.document.attributes:
            if isinstance(attr, DocumentAttributeSticker):
                return None   # sticker → فوروارد نمی‌شود
            if isinstance(attr, DocumentAttributeVideo):
                return "vid"
            if isinstance(attr, DocumentAttributeAudio):
                return "file"
        # v3.2.2 (B-13): use the shared is_file() — a document without a
        # filename is not a "file" (it lands in `other`).
        return "file" if is_file(msg.media) else None

    return None


def _is_audio(msg) -> bool:
    """بررسی می‌کند که آیا media پیام audio است (نه video/sticker).

    v3.2.2 (B-11): this was a byte-for-byte re-implementation of the shared
    `helpers.utils.is_audio()` helper, living right next to an import of the
    shared `is_photo` in the same file — the exact "two copies of a media
    predicate drift apart" shape the project has been bitten by before. It
    is now a thin delegation to the shared helper.
    """
    return is_audio(msg.media)
