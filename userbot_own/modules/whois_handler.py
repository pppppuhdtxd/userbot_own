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
- Profile photo count
- Public link (t.me/username) when available
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
    UserStatusOffline,
    UserStatusOnline,
    UserStatusRecently,
)

from userbot_own.core.context import ModuleContext
from userbot_own.modules.base import Module

# Logging is provided by Module._log_* helpers; no module-level logger needed.


# ── Module ──────────────────────────────────────────────────────────────────

class WhoisHandler(Module):
    """Display detailed information about users, channels, and groups."""

    name = "whois_handler"

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
                info_text = await self._whois_by_identifier(client, identifier)
                await self._safe_edit(event, info_text)
                return

            # `whois` with reply
            reply = await event.get_reply_message()
            if reply is not None:
                info_text = await self._whois_by_sender(client, reply)
                await self._safe_edit(event, info_text)
                return

            # `whois` without args or reply → current chat
            info_text = await self._whois_current_chat(client, event.chat_id)
            await self._safe_edit(event, info_text)

        except errors.FloodWaitError as exc:
            wait_msg = f"⏳ درخواست بیش از حد — لطفاً {exc.seconds} ثانیه صبر کنید."
            self._log_warning("Whois FloodWait %ds.", exc.seconds)
            await self._safe_edit(event, wait_msg)
        except Exception as exc:
            self._log_error("Whois error: %s", exc)
            await self._safe_edit(event, f"❌ خطا در دریافت اطلاعات: `{exc}`")

    # ── By identifier (@username or numeric ID) ──────────────────────────

    async def _whois_by_identifier(self, client: TelegramClient, identifier: str) -> str:
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
            return f"❌ **یافت نشد:** `{identifier}`\n\nخطا: `{exc}`"

        return await self._build_entity_info(client, entity)

    # ── By reply sender ──────────────────────────────────────────────────

    async def _whois_by_sender(self, client: TelegramClient, reply_msg) -> str:
        """Fetch info about the sender of the replied message."""
        try:
            sender = await reply_msg.get_sender()
        except Exception as exc:
            return f"❌ خطا در دریافت فرستنده: `{exc}`"

        if sender is None:
            # Try using sender_id
            sender_id = getattr(reply_msg, "sender_id", None)
            if sender_id:
                try:
                    sender = await client.get_entity(sender_id)
                except Exception as exc:
                    return f"❌ خطا در دریافت فرستنده: `{exc}`"

        if sender is None:
            return "❌ فرستنده پیام یافت نشد."

        return await self._build_entity_info(client, sender)

    # ── Current chat ─────────────────────────────────────────────────────

    async def _whois_current_chat(self, client: TelegramClient, chat_id: int) -> str:
        """Fetch info about the current chat."""
        try:
            entity = await client.get_entity(chat_id)
        except Exception as exc:
            return f"❌ خطا در دریافت اطلاعات چت: `{exc}`"

        return await self._build_entity_info(client, entity)

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

        # Flags
        flags = []
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
        if getattr(user, "self", False):
            flags.append("👤 خودتان")

        if flags:
            lines.append(f"• **وضعیت:** {', '.join(flags)}")
        else:
            lines.append("• **وضعیت:** کاربر عادی")

        # Language code (if available)
        lang = getattr(user, "lang_code", None)
        if lang:
            lines.append(f"• **زبان:** `{lang}`")

        # Profile photo count
        photo = getattr(user, "photo", None)
        if isinstance(photo, UserProfilePhoto):
            photo_count = getattr(photo, "photo_count", None)
            if photo_count:
                lines.append(f"• **عکس‌های پروفایل:** `{photo_count}`")

        # Online status
        status = getattr(user, "status", None)
        if status:
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
                about_preview = about[:200] + ("…" if len(about) > 200 else "")
                lines.append(f"• **بیو:** `{about_preview}`")

            # Common chats count
            common_count = getattr(full, "common_chats_count", None)
            if common_count:
                lines.append(f"• **چت‌های مشترک:** `{common_count}`")

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

        # Try to fetch full channel info for extra details.
        # Re-raise FloodWaitError so the caller can surface it to the user.
        try:
            full_channel = await client(GetFullChannelRequest(channel))
            full = getattr(full_channel, "full_chat", full_channel)

            # About / Description
            about = getattr(full, "about", None)
            if about:
                about_preview = about[:200] + ("…" if len(about) > 200 else "")
                lines.append(f"• **توضیحات:** `{about_preview}`")

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

        # Try to fetch full chat info for extra details.
        # Re-raise FloodWaitError so the caller can surface it to the user.
        try:
            full_chat = await client(GetFullChatRequest(chat.id))
            full = getattr(full_chat, "full_chat", full_chat)

            # About / Description
            about = getattr(full, "about", None)
            if about:
                about_preview = about[:200] + ("…" if len(about) > 200 else "")
                lines.append(f"• **توضیحات:** `{about_preview}`")

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
    "• وضعیت Verified / Scam / Fake\n\n"
    "اطلاعات گروه‌ها:\n"
    "• عنوان، ID\n"
    "• تعداد اعضا\n"
    "• نوع | Basic Group یا Supergroup\n"
    "• لینک (در صورت وجود)\n\n"
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
)

WhoisHandler.help_text = help_text
WhoisHandler.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return WhoisHandler(context)
