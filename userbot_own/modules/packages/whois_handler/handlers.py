"""
userbot_own/modules/packages/whois_handler/handlers.py
════════════════════════════════════════════════════════════════
The WhoisHandler module class — command dispatch and the result cache.

Resolves the target (by identifier, by reply sender, or the current
chat), asks the builders in ``builders.py`` for the text, and attaches
the profile photo as a caption when one is publicly available.

v3.2.5: the per-entity builders moved to ``builders.py``, the limits to
``const.py``, and the help texts to ``info.py``.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import time

from telethon import TelegramClient, events
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
    FLOOD_ERRORS,
    EntityResolutionError,
    flood_seconds,
    format_user_flags,
    format_user_status,
    get_profile_photos_safe,
    resolve_entity_by_id,
    truncate,
)
from userbot_own.modules._infra.base import Module
from userbot_own.modules._infra.router import CommandRouter
from userbot_own.modules.packages.whois_handler.const import (
    _CACHE_TTL_SECONDS,
    _CAPTION_LIMIT,
    _MESSAGE_LIMIT,
)

# ── Module ──────────────────────────────────────────────────────────────────

class WhoisHandler(Module):
    """Display detailed information about users, channels, and groups."""

    name = "whois_handler"
    category = "info"
    desc = "اطلاعات کاربر و چت"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)

        # cache_key -> (expires_at_monotonic, info_text, entity)
        # cache_key is the resolved entity's Telethon-internal id, prefixed
        # by its class name to avoid any (extremely unlikely) collision
        # between a User id and a Channel id that happen to share a number.
        self._result_cache: dict[str, tuple[float, str, object]] = {}

        self._router = CommandRouter()
        self._router.register("whois", handler=self._on_command)

    def setup(self, client: TelegramClient) -> None:
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_dispatch)
        self._log_info("WhoisHandler ready.")

    # ── Command dispatcher ─────────────────────────────────────────────────

    async def _on_dispatch(self, event) -> None:
        """
        Thin entry point registered with Telethon — hands off to
        `CommandRouter` (v3.1.5) for consistency with the rest of the
        codebase's module contract. `whois` currently has a single root
        command with free-form trailing arguments (unlike e.g. `enemy.py`'s
        multiple literal commands), so this migration doesn't change
        dispatch behavior — the router still just matches the lowercased
        first token and calls `_on_command`, which parses the remainder
        exactly as before.
        """
        await self._router.dispatch(event)

    async def _on_command(self, event) -> None:
        text = (event.raw_text or "").strip()
        parts = text.split(maxsplit=1)

        # CommandRouter already matched the first token as "whois" before
        # calling us, but this guard is kept intentionally — it costs
        # nothing and keeps _on_command safe to call directly (e.g. from
        # tests) without going through the router first.
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

        except FLOOD_ERRORS as exc:
            wait_msg = f"⏳ درخواست بیش از حد — لطفاً {flood_seconds(exc)} ثانیه صبر کنید."
            self._log_warning("Whois FloodWait %ds.", flood_seconds(exc))
            await self._safe_edit(event, wait_msg)
        except Exception as exc:
            self._log_error("Whois error: %s", exc)
            await self._safe_edit(event, f"❌ خطا در دریافت اطلاعات: `{exc}`")

    # ── By identifier (@username or numeric ID) ──────────────────────────

    async def _whois_by_identifier(self, client: TelegramClient, identifier: str):
        """
        Resolve identifier (username or numeric ID) and fetch info.

        v3.1.5: numeric identifiers no longer go through a bare
        `client.get_entity(int(...))` call (see module docstring / bug
        history for why that silently failed for any ID this account
        hadn't already cached). They're routed through
        `resolve_entity_by_id`, which tries every plausible peer shape —
        including the public `-100`-prefixed channel/supergroup form —
        before giving up with a clear, actionable message.
        """
        if identifier.startswith("@"):
            target: str | int = identifier[1:]
        else:
            try:
                target = int(identifier)
            except ValueError:
                target = identifier

        cache_key = None
        if isinstance(target, int):
            cached = self._get_cached_by_raw_id(target)
            if cached is not None:
                return cached

        try:
            if isinstance(target, int):
                entity = await resolve_entity_by_id(client, target)
            else:
                entity = await client.get_entity(target)
        except EntityResolutionError as exc:
            self._log_debug(
                "Numeric ID resolution failed for %s: %s", identifier, exc.detail
            )
            return exc.user_message, None
        except FLOOD_ERRORS:
            raise
        except Exception as exc:
            return f"❌ **یافت نشد:** `{identifier}`\n\nخطا: `{exc}`", None

        cache_key = self._cache_key_for(entity)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        info_text = await self._build_entity_info(client, entity)
        self._store_cache(cache_key, info_text, entity)
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

        cache_key = self._cache_key_for(sender)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        info_text = await self._build_entity_info(client, sender)
        self._store_cache(cache_key, info_text, sender)
        return info_text, sender

    # ── Current chat ─────────────────────────────────────────────────────

    async def _whois_current_chat(self, client: TelegramClient, chat_id: int):
        """Fetch info about the current chat."""
        try:
            entity = await client.get_entity(chat_id)
        except Exception as exc:
            return f"❌ خطا در دریافت اطلاعات چت: `{exc}`", None

        cache_key = self._cache_key_for(entity)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        info_text = await self._build_entity_info(client, entity)
        self._store_cache(cache_key, info_text, entity)
        return info_text, entity

    # ── Result cache (v3.1.5) ──────────────────────────────────────────────

    @staticmethod
    def _cache_key_for(entity) -> str:
        """Build a collision-safe cache key from an entity's type + id."""
        return f"{type(entity).__name__}:{getattr(entity, 'id', id(entity))}"

    def _get_cached(self, cache_key: str):
        """Return (info_text, entity) if a fresh cache entry exists, else None."""
        entry = self._result_cache.get(cache_key)
        if entry is None:
            return None
        expires_at, info_text, entity = entry
        if time.monotonic() >= expires_at:
            self._result_cache.pop(cache_key, None)
            return None
        self._log_debug("Whois cache hit for %s (resolved via cache).", cache_key)
        return info_text, entity

    def _get_cached_by_raw_id(self, raw_id: int):
        """
        Best-effort cache lookup keyed only by a raw numeric ID before an
        entity object even exists — covers the common case of the exact
        same numeric identifier being looked up twice in a row. Checks
        both User and Channel key shapes since we don't yet know the type.

        v3.2.2 (B-09): the "-100-stripped" form the docstring promised was
        never actually probed. The old code only checked ``raw_id`` and
        ``abs(raw_id)`` — and ``abs(-1001234567890)`` is ``1001234567890``,
        NOT the internal channel id ``1234567890``. So the public ``-100…``
        form — the exact form ``whois`` PRINTS for a channel
        (``public_id = int(f"-100{channel.id}")``) and the form the README
        tells users they can paste back — never hit the cache and re-issued
        the full GetFull*Request + profile-photo round trip every time.
        ``resolve_entity_by_id()`` already strips the prefix correctly; the
        cache probe now does the same.
        """
        # Candidate ids this raw value could correspond to:
        #   raw_id                 — the internal form (e.g. 1234567890)
        #   -100-stripped          — the public form   (e.g. -1001234567890)
        #   abs() of either        — the magnitude fallback
        candidates: list[int] = []
        for value in (raw_id, abs(raw_id)):
            if value not in candidates:
                candidates.append(value)
        # The public "-100…" form: strip the prefix to get the internal id.
        if raw_id <= -100:
            stripped = int(str(raw_id)[4:]) if str(raw_id)[4:] else 0
            if stripped and stripped not in candidates:
                candidates.append(stripped)

        for prefix in ("User", "Channel", "Chat"):
            for candidate in candidates:
                hit = self._get_cached(f"{prefix}:{candidate}")
                if hit is not None:
                    return hit
        return None

    def _store_cache(self, cache_key: str, info_text: str, entity) -> None:
        self._result_cache[cache_key] = (
            time.monotonic() + _CACHE_TTL_SECONDS,
            info_text,
            entity,
        )

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

        v3.1.5: the text-only fallback is now also truncated to
        `_MESSAGE_LIMIT` as a safety net, mirroring the existing
        `_CAPTION_LIMIT` check on the photo-caption path.
        """
        photo = None
        if entity is not None:
            try:
                photos = await get_profile_photos_safe(client, entity, limit=1)
                photo = photos[0] if photos else None
            except FLOOD_ERRORS:
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
            except FLOOD_ERRORS:
                raise
            except Exception as exc:
                # The placeholder may already be deleted at this point; if
                # so _safe_edit below will simply fail silently (it swallows
                # edit errors), which is an acceptable degrade — the info
                # was already lost from view, but the command doesn't crash.
                self._log_debug("Photo attach failed for %s: %s", entity, exc)

        safe_text = truncate(info_text, _MESSAGE_LIMIT)
        await self._safe_edit(event, safe_text)

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

        except FLOOD_ERRORS:
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

        # ID (public "-100..." form — unchanged display format; v3.1.5 only
        # changes what forms are *accepted* on input, see
        # resolve_entity_by_id in helpers/utils.py)
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

        # v3.1.5: a few extra channel/group flags surfaced directly from
        # the basic Channel object — these don't require GetFullChannelRequest
        # at all, so they're shown here alongside the other basic flags.
        if getattr(channel, "slowmode_enabled", False):
            lines.append("• **Slow mode:** ✅ فعال")
        if getattr(channel, "join_to_send", False):
            lines.append("• **ارسال پیام فقط برای اعضا:** ✅")
        if getattr(channel, "stories_hidden", False):
            lines.append("• **استوری‌ها:** 🙈 مخفی")

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

            # Slow-mode seconds, if enabled (extra detail beyond the basic
            # boolean already shown above)
            slowmode_seconds = getattr(full, "slowmode_seconds", None)
            if slowmode_seconds:
                lines.append(f"• **مدت Slow mode:** `{slowmode_seconds}` ثانیه")

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
                except FLOOD_ERRORS:
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

        except FLOOD_ERRORS:
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

        except FLOOD_ERRORS:
            raise
        except Exception as exc:
            self._log_debug("GetFullChatRequest failed for %d: %s", chat.id, exc)

        return "\n".join(lines)
