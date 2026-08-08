"""
userbot_own/modules/whois_handler.py
════════════════════════════════════════════════════════════════
Whois Handler — نمایش اطلاعات کامل کاربر/کانال/گروه

دستورات (قابل استفاده در هر چتی):
- `whois`                  — اطلاعات کامل چت فعلی
- `whois @username`        — اطلاعات کاربر/کانال/گروه با username
- `whois 123456789`        — اطلاعات با ID عددی
- `whois` (reply)          — اطلاعات فرستنده پیام reply شده

Features:
- User info: name, username, ID, status, bio, profile photo, last seen
- Channel info: title, username, ID, members count, description, link, creation date
- Group info: title, ID, members count, admins count
- Bot detection with bot-specific details
- Premium / Verified / Scam / Fake flags
- Online status with last seen time
- Restriction reasons (e.g. platform-specific content restrictions), if any
- Single most recent profile photo attached as the message media, with the
  whois text as its caption — when publicly available under the entity's
  own privacy settings
- Public link (t.me/username) when available
- Data Center (DC) ID, when available

Photo attachment design (v3.0.10):
Telegram does not allow editing a text-only message into one carrying
media, so the "🔍 در حال دریافت..." placeholder (the outgoing `whois`
command message itself) is deleted and replaced with a single fresh
`send_file(..., caption=info_text)` call whenever a photo is available —
one final message containing both the photo and the full text, rather than
two separate messages. If no photo is available (hidden by privacy
settings, or simply not set), the placeholder is edited to the text-only
result exactly as before — no extra message is created either way.

The `Photo` object returned by `get_profile_photos_safe()` is passed
directly as `send_file`'s `file` argument. This is a server-side file
reference, not raw bytes — Telethon/Telegram handle it like a forward, so
no image data is downloaded to or re-uploaded from this device.

Privacy note: this module only ever displays what Telethon's normal
`get_entity` / `get_profile_photos` calls return under the target's own
privacy settings. If a user has hidden their profile photo or last-seen
status, the API returns null/empty and the corresponding detail is simply
omitted — no attempt is made to bypass or circumvent those settings.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from telethon import TelegramClient, errors, events
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    Channel,
    Chat,
    User,
    UserProfilePhoto,
)

from userbot_own.core.context import ModuleContext
from userbot_own.helpers.utils import (
    format_user_flags,
    format_user_status,
    get_profile_photos_safe,
    truncate,
)
from userbot_own.modules.base import Module

# Logging is provided by Module._log_* helpers; no module-level logger needed.

# Telegram's caption limit for media messages is 1024 characters. If the
# built whois text would exceed this, attaching it as a photo caption would
# either fail or silently truncate server-side — safer to fall back to the
# existing text-only message in that case than to lose information.
_CAPTION_LIMIT = 1024


# ── Module ──────────────────────────────────────────────────────────────────

class WhoisHandler(Module):
    """Display detailed information about users, channels, and groups."""

    name = "whois_handler"
    category = "info"
    desc = "اطلاعات کاربر و چت"

    def setup(self, client: TelegramClient) -> None:
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_command)
        self._log_info("WhoisHandler ready.")

    # ── Command dispatcher ─────────────────────────────────────────────────

    async def _on_command(self, event) -> None:
        text = (event.raw_text or "").strip()
        parts = text.split(maxsplit=1)

        if not parts or parts[0].lower() != "whois":
            return

        client = event.client
        await self._safe_edit(event, "🔍 در حال دریافت اطلاعات...")

        try:
            # `whois` with argument
            if len(parts) == 2:
                identifier = parts[1].strip()
                info_text, entity = await self._whois_by_identifier(client, identifier)
                await self._finalize(event, client, info_text, entity)
                return

            # `whois` with reply
            reply = await event.get_reply_message()
            if reply is not None:
                info_text, entity = await self._whois_by_sender(client, reply)
                await self._finalize(event, client, info_text, entity)
                return

            # `whois` without args or reply → current chat
            info_text, entity = await self._whois_current_chat(client, event.chat_id)
            await self._finalize(event, client, info_text, entity)

        except errors.FloodWaitError as exc:
            wait_msg = f"⏳ درخواست بیش از حد — لطفاً {exc.seconds} ثانیه صبر کنید."
            self._log_warning("Whois FloodWait %ds.", exc.seconds)
            await self._safe_edit(event, wait_msg)
        except Exception as exc:
            self._log_error("Whois error: %s", exc)
            await self._safe_edit(event, f"❌ خطا در دریافت اطلاعات: `{exc}`")

    # ── By identifier (@username or numeric ID) ──────────────────────────

    async def _whois_by_identifier(self, client: TelegramClient, identifier: str):
        """Resolve identifier (username or numeric ID) and fetch info."""
        # Normalize username
        if identifier.startswith("@"):
            target = identifier[1:]
        else:
            # Try to parse as numeric ID
            try:
                target = int(identifier)
            except ValueError:
                target = identifier

        try:
            entity = await client.get_entity(target)
        except Exception as exc:
            return f"❌ **یافت نشد:** `{identifier}`\n\nخطا: `{exc}`", None

        info_text = await self._build_entity_info(client, entity)
        return info_text, entity

    # ── By reply sender ──────────────────────────────────────────────────

    async def _whois_by_sender(self, client: TelegramClient, reply_msg):
        """Fetch info about the sender of the replied message."""
        try:
            sender = await reply_msg.get_sender()
        except Exception as exc:
            return f"❌ خطا در دریافت فرستنده: `{exc}`", None

        if sender is None:
            # Try using sender_id
            sender_id = getattr(reply_msg, "sender_id", None)
            if sender_id:
                try:
                    sender = await client.get_entity(sender_id)
                except Exception as exc:
                    return f"❌ خطا در دریافت فرستنده: `{exc}`", None

        if sender is None:
            return "❌ فرستنده پیام یافت نشد.", None

        info_text = await self._build_entity_info(client, sender)
        return info_text, sender

    # ── Current chat ─────────────────────────────────────────────────────

    async def _whois_current_chat(self, client: TelegramClient, chat_id: int):
        """Fetch info about the current chat."""
        try:
            entity = await client.get_entity(chat_id)
        except Exception as exc:
            return f"❌ خطا در دریافت اطلاعات چت: `{exc}`", None

        info_text = await self._build_entity_info(client, entity)
        return info_text, entity

    # ── Entity info builder (dispatcher) ─────────────────────────────────

    async def _build_entity_info(self, client: TelegramClient, entity) -> str:
        """Dispatch to the correct builder based on entity type."""
        if isinstance(entity, User):
            return await self._build_user_info(client, entity)
        elif isinstance(entity, Channel):
            return await self._build_channel_info(client, entity)
        elif isinstance(entity, Chat):
            return await self._build_chat_info(client, entity)
        else:
            return f"❌ نوع موجودیت ناشناخته: `{type(entity).__name__}`"

    # ── Restriction reasons (shared across User/Channel) ──────────────────

    @staticmethod
    def _format_restriction_reasons(entity) -> str | None:
        """
        Build a single formatted line summarizing `entity.restriction_reason`
        (a list of `RestrictionReason` objects Telegram attaches when content
        is restricted on specific platforms/regions), or None if absent.
        This is standard metadata already returned by `get_entity` — not
        anything requiring extra privileged access.
        """
        reasons = getattr(entity, "restriction_reason", None)
        if not reasons:
            return None
        parts = []
        for r in reasons:
            platform = getattr(r, "platform", None) or "?"
            reason_text = getattr(r, "reason", None) or ""
            parts.append(f"{platform}: {reason_text}" if reason_text else platform)
        return " | ".join(parts)

    # ── Finalize: single-message photo+caption, or text-only fallback ─────

    async def _finalize(self, event, client: TelegramClient, info_text: str, entity) -> None:
        """
        Attach the single most recent profile photo (if publicly available)
        to the whois result as one combined photo+caption message, replacing
        the placeholder. Falls back to editing the placeholder to a
        text-only message if no photo is available, the caption would
        exceed Telegram's length limit, or the photo send itself fails.
        """
        photo = None
        if entity is not None:
            try:
                photos = await get_profile_photos_safe(client, entity, limit=1)
                photo = photos[0] if photos else None
            except errors.FloodWaitError:
                raise
            except Exception as exc:
                self._log_debug("get_profile_photos_safe failed for %s: %s", entity, exc)
                photo = None

        if photo is not None and len(info_text) <= _CAPTION_LIMIT:
            try:
                chat = await event.get_chat()
                await event.delete()
                await client.send_file(chat, file=photo, caption=info_text)
                return
            except errors.FloodWaitError:
                raise
            except Exception as exc:
                # The placeholder may already be deleted at this point; if
                # so _safe_edit below will simply fail silently (it swallows
                # edit errors), which is an acceptable degrade — the info
                # was already lost from view, but the command doesn't crash.
                self._log_debug("Photo attach failed for %s: %s", entity, exc)

        await self._safe_edit(event, info_text)

    # ── User info ────────────────────────────────────────────────────────

    async def _build_user_info(self, client: TelegramClient, user: User) -> str:
        """Build detailed info for a User entity."""
        lines: list[str] = ["👤 **اطلاعات کاربر**\n"]

        # Name
        name_parts = []
        if user.first_name:
            name_parts.append(user.first_name)
        if user.last_name:
            name_parts.append(user.last_name)
        name = " ".join(name_parts) or "Unknown"
        lines.append(f"• **نام:** `{name}`")

        # Username
        if user.username:
            lines.append(f"• **یوزرنیم:** @{user.username}")
            lines.append(f"• **لینک:** t.me/{user.username}")

        # ID
        lines.append(f"• **ID:** `{user.id}`")

        # Phone (only visible if user shares it)
        phone = getattr(user, "phone", None)
        if phone:
            lines.append(f"• **شماره:** `{phone}`")

        # Flags (shared helper — includes "خودتان" for self)
        flags = format_user_flags(user, include_self=True)
        if flags:
            lines.append(f"• **وضعیت:** {', '.join(flags)}")
        else:
            lines.append("• **وضعیت:** کاربر عادی")

        # Language code (if available)
        lang = getattr(user, "lang_code", None)
        if lang:
            lines.append(f"• **زبان:** `{lang}`")

        # Restriction reasons, if any
        restriction = self._format_restriction_reasons(user)
        if restriction:
            lines.append(f"• **محدودیت:** `{restriction}`")

        # Profile photo count + DC ID
        photo = getattr(user, "photo", None)
        if isinstance(photo, UserProfilePhoto):
            photo_count = getattr(photo, "photo_count", None)
            if photo_count:
                lines.append(f"• **عکس‌های پروفایل:** `{photo_count}`")
            dc_id = getattr(photo, "dc_id", None)
            if dc_id:
                lines.append(f"• **DC ID:** `{dc_id}`")

        # Online status (shared helper)
        status_lines = format_user_status(getattr(user, "status", None))
        lines.extend(status_lines)

        # Try to fetch full user info (bio, etc.).
        # Re-raise FloodWaitError so the top-level handler surfaces it to the
        # user; swallowing it would cause every subsequent whois to hit the
        # same rate-limit and compound the wait time.
        try:
            full_user = await client(GetFullUserRequest(user))
            full = getattr(full_user, "full_user", full_user)

            # Bio / About
            about = getattr(full, "about", None)
            if about:
                lines.append(f"• **بیو:** `{truncate(about, 200)}`")

            # Common chats count
            common_count = getattr(full, "common_chats_count", None)
            if common_count:
                lines.append(f"• **چت‌های مشترک:** `{common_count}`")

            # Contact / mutual-contact status — standard fields already
            # exposed on the full-user object, no extra privileged lookup.
            if getattr(full, "contact", False):
                lines.append("• **مخاطب:** ✅ در لیست مخاطبین شما")

        except errors.FloodWaitError:
            raise
        except Exception as exc:
            self._log_debug("GetFullUserRequest failed for %d: %s", user.id, exc)

        return "\n".join(lines)

    # ── Channel info ─────────────────────────────────────────────────────

    async def _build_channel_info(self, client: TelegramClient, channel: Channel) -> str:
        """Build detailed info for a Channel entity."""
        lines: list[str] = []

        # Determine type (channel vs supergroup)
        if getattr(channel, "broadcast", False):
            lines.append("📢 **اطلاعات کانال**\n")
        elif getattr(channel, "megagroup", False):
            lines.append("👥 **اطلاعات سوپرگروه**\n")
        else:
            lines.append("📢 **اطلاعات چت**\n")

        # Title
        title = getattr(channel, "title", None) or "Unknown"
        lines.append(f"• **عنوان:** `{title}`")

        # Username
        username = getattr(channel, "username", None)
        if username:
            lines.append(f"• **یوزرنیم:** @{username}")
            lines.append(f"• **لینک:** t.me/{username}")

        # ID (convert to public format with -100 prefix)
        public_id = int(f"-100{channel.id}")
        lines.append(f"• **ID:** `{public_id}`")

        # Flags
        flags = []
        if getattr(channel, "verified", False):
            flags.append("✅ Verified")
        if getattr(channel, "scam", False):
            flags.append("⚠️ Scam")
        if getattr(channel, "fake", False):
            flags.append("⚠️ Fake")
        if getattr(channel, "gigagroup", False):
            flags.append("📢 Broadcast Group")
        if getattr(channel, "noforwards", False):
            flags.append("🚫 بدون فوروارد")
        if getattr(channel, "creator", False):
            flags.append("👑 Creator")

        if flags:
            lines.append(f"• **وضعیت:** {', '.join(flags)}")

        # Restriction reasons, if any
        restriction = self._format_restriction_reasons(channel)
        if restriction:
            lines.append(f"• **محدودیت:** `{restriction}`")

        # Creation date
        creation_date = getattr(channel, "date", None)
        if creation_date:
            try:
                date_str = creation_date.strftime("%Y-%m-%d %H:%M:%S UTC")
                lines.append(f"• **تاریخ ساخت:** `{date_str}`")
            except Exception:
                pass

        # Members count (basic)
        participants_count = getattr(channel, "participants_count", None)
        if participants_count:
            lines.append(f"• **تعداد اعضا:** `{participants_count:,}`")

        # DC ID from the channel's own photo stub, if present
        channel_photo = getattr(channel, "photo", None)
        dc_id = getattr(channel_photo, "dc_id", None) if channel_photo else None
        if dc_id:
            lines.append(f"• **DC ID:** `{dc_id}`")

        # Try to fetch full channel info for extra details.
        # Re-raise FloodWaitError so the caller can surface it to the user.
        try:
            full_channel = await client(GetFullChannelRequest(channel))
            full = getattr(full_channel, "full_chat", full_channel)

            # About / Description
            about = getattr(full, "about", None)
            if about:
                lines.append(f"• **توضیحات:** `{truncate(about, 200)}`")

            # Accurate participants count from full info
            full_participants = getattr(full, "participants_count", None)
            if full_participants and not participants_count:
                lines.append(f"• **تعداد اعضا:** `{full_participants:,}`")

            # Admins count
            admins_count = getattr(full, "admins_count", None)
            if admins_count:
                lines.append(f"• **تعداد ادمین‌ها:** `{admins_count}`")

            # Online count
            online_count = getattr(full, "online_count", None)
            if online_count:
                lines.append(f"• **آنلاین:** `{online_count:,}`")

            # Linked chat (discussion group)
            linked_chat_id = getattr(full, "linked_chat_id", None)
            if linked_chat_id:
                try:
                    linked = await client.get_entity(linked_chat_id)
                    linked_title = getattr(linked, "title", None) or str(linked_chat_id)
                    linked_username = getattr(linked, "username", None)
                    if linked_username:
                        lines.append(f"• **چت مرتبط:** @{linked_username} ({linked_title})")
                    else:
                        lines.append(f"• **چت مرتبط:** `{linked_title}` (ID: `{linked_chat_id}`)")
                except errors.FloodWaitError:
                    # v3.0.9 fix: this used to be swallowed by the bare
                    # except below, unlike every other FloodWait in this
                    # module, which is deliberately re-raised so the
                    # top-level handler can show a "wait N seconds"
                    # message instead of silently hiding it.
                    raise
                except Exception:
                    lines.append(f"• **چت مرتبط ID:** `{linked_chat_id}`")

            # Invite link (from exported_invite if available)
            exported_invite = getattr(full, "exported_invite", None)
            if exported_invite:
                invite_link = getattr(exported_invite, "link", None)
                if invite_link:
                    lines.append(f"• **لینک دعوت:** `{invite_link}`")

        except errors.FloodWaitError:
            raise
        except Exception as exc:
            self._log_debug("GetFullChannelRequest failed for %d: %s", channel.id, exc)

        return "\n".join(lines)

    # ── Chat info (basic group) ──────────────────────────────────────────

    async def _build_chat_info(self, client: TelegramClient, chat: Chat) -> str:
        """Build detailed info for a basic Chat (group) entity."""
        lines: list[str] = ["👥 **اطلاعات گروه (Basic)**\n"]

        # Title
        title = getattr(chat, "title", None) or "Unknown"
        lines.append(f"• **عنوان:** `{title}`")

        # ID (negative for basic groups — Telethon returns positive component)
        chat_id = -chat.id
        lines.append(f"• **ID:** `{chat_id}`")

        # Participants count
        participants_count = getattr(chat, "participants_count", None)
        if participants_count:
            lines.append(f"• **تعداد اعضا:** `{participants_count}`")

        # Creation date
        creation_date = getattr(chat, "date", None)
        if creation_date:
            try:
                date_str = creation_date.strftime("%Y-%m-%d %H:%M:%S UTC")
                lines.append(f"• **تاریخ ساخت:** `{date_str}`")
            except Exception:
                pass

        # Flags
        flags = []
        if getattr(chat, "creator", False):
            flags.append("👑 Creator")
        if getattr(chat, "deactivated", False):
            flags.append("🚫 غیرفعال")
        if getattr(chat, "noforwards", False):
            flags.append("🚫 بدون فوروارد")

        if flags:
            lines.append(f"• **وضعیت:** {', '.join(flags)}")

        # DC ID from the chat's own photo stub, if present
        chat_photo = getattr(chat, "photo", None)
        dc_id = getattr(chat_photo, "dc_id", None) if chat_photo else None
        if dc_id:
            lines.append(f"• **DC ID:** `{dc_id}`")

        # Try to fetch full chat info for extra details.
        # Re-raise FloodWaitError so the caller can surface it to the user.
        try:
            full_chat = await client(GetFullChatRequest(chat.id))
            full = getattr(full_chat, "full_chat", full_chat)

            # About / Description
            about = getattr(full, "about", None)
            if about:
                lines.append(f"• **توضیحات:** `{truncate(about, 200)}`")

            # Admins count
            admins_count = getattr(full, "admins_count", None)
            if admins_count:
                lines.append(f"• **تعداد ادمین‌ها:** `{admins_count}`")

            # Online count
            online_count = getattr(full, "online_count", None)
            if online_count:
                lines.append(f"• **آنلاین:** `{online_count}`")

            # Exported invite
            exported_invite = getattr(full, "exported_invite", None)
            if exported_invite:
                invite_link = getattr(exported_invite, "link", None)
                if invite_link:
                    lines.append(f"• **لینک دعوت:** `{invite_link}`")

        except errors.FloodWaitError:
            raise
        except Exception as exc:
            self._log_debug("GetFullChatRequest failed for %d: %s", chat.id, exc)

        return "\n".join(lines)


# ── Help Texts (در انتهای ماژول طبق قوانین) ─────────────────────────────────

help_text = (
    "• `whois` | اطلاعات چت فعلی\n"
    "• `whois @username` | اطلاعات با یوزرنیم\n"
    "• `whois 123456789` | اطلاعات با ID عددی\n"
    "• `whois` (reply) | اطلاعات فرستنده پیام\n"
)

help_extra = (
    "Whois - نمایش اطلاعات کاربر، کانال و گروه\n\n"
    "دستورات اصلی:\n"
    "• `whois` | اطلاعات کامل چت فعلی\n"
    "• `whois @username` | اطلاعات کاربر/کانال/گروه با username\n"
    "• `whois 123456789` | اطلاعات با ID عددی\n"
    "• `whois` (reply) | اطلاعات فرستنده پیام reply شده\n\n"
    "اطلاعات کاربران:\n"
    "• نام کامل، یوزرنیم، ID\n"
    "• وضعیت | Bot / Verified / Premium / Scam / Fake / Deleted\n"
    "• بیو (Bio) تا ۲۰۰ کاراکتر\n"
    "• وضعیت مخاطب (در لیست مخاطبین شما یا خیر)\n"
    "• محدودیت‌های محتوایی (در صورت وجود)\n"
    "• DC ID (در صورت وجود عکس پروفایل)\n"
    "• تعداد عکس‌های پروفایل\n"
    "• وضعیت آنلاین / آخرین بازدید\n"
    "• لینک عمومی (t.me/username)\n"
    "• چت‌های مشترک با شما\n\n"
    "اطلاعات کانال‌ها:\n"
    "• عنوان، یوزرنیم، ID\n"
    "• تعداد اعضا و ادمین‌ها\n"
    "• توضیحات (Description)\n"
    "• لینک دعوت\n"
    "• تاریخ ساخت\n"
    "• لینک چت Discussion (در صورت وجود)\n"
    "• وضعیت Verified / Scam / Fake\n"
    "• محدودیت‌های محتوایی (در صورت وجود)\n\n"
    "اطلاعات گروه‌ها:\n"
    "• عنوان، ID\n"
    "• تعداد اعضا\n"
    "• نوع | Basic Group یا Supergroup\n"
    "• لینک (در صورت وجود)\n\n"
    "عکس پروفایل:\n"
    "• در صورتی که به‌صورت عمومی در دسترس باشد، آخرین عکس پروفایل به‌همراه "
    "کل متن اطلاعات در قالب یک پیام واحد (عکس + کپشن) ارسال می‌شود\n"
    "• ارسال عکس کاملاً سمت سرور تلگرام انجام می‌شود؛ دانلود یا آپلود "
    "مجدد فایل روی دستگاه شما انجام نمی‌شود\n"
    "• اگر کاربر عکس پروفایل خود را مخفی کرده یا عکسی نداشته باشد، یا "
    "متن اطلاعات بیش از حد مجاز کپشن تلگرام باشد، فقط متن اطلاعات "
    "نمایش داده می‌شود (بدون هیچ تلاشی برای دور زدن تنظیمات حریم خصوصی)\n\n"
    "مثال‌ها:\n"
    "• `whois` در یک کانال | نمایش اطلاعات کانال\n"
    "• `whois @durov` | اطلاعات Pavel Durov\n"
    "• `whois 792643829` | اطلاعات با ID عددی\n"
    "• reply روی یک پیام + `whois` | اطلاعات فرستنده آن پیام\n\n"
    "نکات مهم:\n"
    "• این دستور در هر چتی قابل استفاده است\n"
    "• بیو و توضیحات تا ۲۰۰ کاراکتر نمایش داده می‌شوند\n"
    "• برای کانال‌های خصوصی، برخی اطلاعات ممکن است در دسترس نباشد\n"
    "• وضعیت آنلاین دقیق فقط برای مخاطبین قابل مشاهده است\n"
    "• عکس پروفایل فقط در صورتی نمایش داده می‌شود که تنظیمات حریم "
    "خصوصی هدف اجازه دهد\n"
)

WhoisHandler.help_text = help_text
WhoisHandler.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return WhoisHandler(context)