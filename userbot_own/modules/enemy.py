"""
userbot_own/modules/enemy.py
════════════════════════════════════════════════════════════════
ماژول دشمن — Enemy Module

وقتی یکی از «دشمنان» پیام می‌دهد، یک پیام تصادفی از «چت منبع» به‌عنوان
پاسخ ارسال می‌شود. همه انواع رسانه (متن، عکس، ویدیو، GIF، استیکر، فایل)
بدون دانلود/آپلود مجدد کپی می‌شوند.

دستورات:
- `منبع <chat_id یا @username>`   | تنظیم چت منبع
- `دشمن`                           | (ریپلای به پیام) — اضافه کردن دشمن
- `حذف دشمن`                       | (ریپلای به پیام) — حذف دشمن
- `لیست دشمنان`                    | نمایش دشمنان چت فعلی
- `پاک کردن همه`                   | حذف همه دشمنان چت فعلی
- `تنظیمات`                        | نمایش پیکربندی فعلی
- `راهنما دشمن`                    | راهنمای کامل

معماری:
- تنظیمات per-account در data/settings/account{N}/enemy.json
- کش پیام‌های چت منبع (اخیراً بازیابی‌شده، حداکثر 200 عدد)
- کپی رسانه بدون دانلود (forward + suppress_signature=True، سپس delete
  فوروارد اصلی نیست — از send_file با file=msg.media استفاده نمی‌شود؛
  به‌جای آن از copy_message pattern استفاده می‌شود)
- FloodWait مدیریت می‌شود (لاگ + ادامه بدون crash)
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import random
from pathlib import Path

from telethon import TelegramClient, errors, events
from telethon.tl.functions.messages import ForwardMessagesRequest
from telethon.tl.types import InputPeerEmpty

from userbot_own.core.context import ModuleContext
from userbot_own.helpers.utils import read_json_file, write_json_file_atomic
from userbot_own.modules.base import Module

# ── Constants ────────────────────────────────────────────────────────────────

#: حداکثر پیام‌هایی که از چت منبع در کش نگه می‌داریم.
#: بزرگ‌تر = تنوع بیشتر، مصرف حافظه بیشتر.
_CACHE_SIZE = 200

#: حداکثر تعداد پیامی که برای پر کردن کش fetch می‌کنیم.
_FETCH_LIMIT = 200

#: وقتی کش به این درصد رسید، یک refresh در پس‌زمینه زده می‌شود.
_REFRESH_THRESHOLD = 0.3


# ── Module ───────────────────────────────────────────────────────────────────

class EnemyModule(Module):
    name = "enemy"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)

        self._settings_file: Path = self.cfg.settings_dir / "enemy.json"

        # source_chat: int | None
        self._source_chat: int | None = None

        # enemies: {str(chat_id): [user_id, ...]}
        # کلیدها رشته هستند چون JSON اجازه کلید int نمی‌دهد.
        self._enemies: dict[str, list[int]] = {}

        # کش پیام‌های چت منبع — list of message IDs
        self._msg_cache: list[int] = []

        # در حال refresh هستیم یا نه (برای جلوگیری از refresh موازی)
        self._refreshing: bool = False

        # تسک refresh در پس‌زمینه
        self._refresh_task: asyncio.Task | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def setup(self, client: TelegramClient) -> None:
        self._load_settings()

        # دستورات outgoing (ما می‌نویسیم)
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_command)

        # پیام‌های incoming — چک می‌کنیم فرستنده دشمن هست یا نه
        self._add_handler(client, events.NewMessage(incoming=True), self._on_incoming)

        self._log_info(
            "EnemyModule ready. source_chat=%s, %d chat(s) with enemies.",
            self._source_chat,
            len(self._enemies),
        )

    def teardown(self, client: TelegramClient) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = None
        super().teardown(client)

    # ── Settings ──────────────────────────────────────────────────────────

    def _load_settings(self) -> None:
        data, err = read_json_file(self._settings_file)
        if err is not None:
            self._log_error("خطا در بارگذاری تنظیمات enemy.json: %s", err)
        if data is not None:
            self._source_chat = data.get("source_chat")
            raw_enemies = data.get("enemies", {})
            # اطمینان از صحت نوع داده‌ها
            self._enemies = {
                str(k): [int(uid) for uid in v]
                for k, v in raw_enemies.items()
                if isinstance(v, list)
            }

    def _save_settings(self) -> None:
        data = {
            "source_chat": self._source_chat,
            "enemies": self._enemies,
        }
        err = write_json_file_atomic(self._settings_file, data, indent=2)
        if err is not None:
            self._log_error("خطا در ذخیره تنظیمات enemy.json: %s", err)

    # ── Command dispatcher ────────────────────────────────────────────────

    async def _on_command(self, event) -> None:
        text = (event.raw_text or "").strip()

        if text.startswith("منبع"):
            await self._cmd_set_source(event, text)
        elif text == "دشمن":
            await self._cmd_add_enemy(event)
        elif text == "حذف دشمن":
            await self._cmd_remove_enemy(event)
        elif text == "لیست دشمنان":
            await self._cmd_list_enemies(event)
        elif text == "پاک کردن همه":
            await self._cmd_clear_all(event)
        elif text == "تنظیمات":
            await self._cmd_settings(event)
        elif text == "راهنما دشمن":
            await self._cmd_help(event)

    # ── Commands ──────────────────────────────────────────────────────────

    async def _cmd_set_source(self, event, text: str) -> None:
        """منبع <chat_id یا @username> — تنظیم چت منبع"""
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await self._safe_edit_with_auto_delete(
                event,
                "❌ **استفاده:** `منبع <chat_id یا @username>`\n"
                "مثال: `منبع @my_channel` یا `منبع -1001234567890`",
            )
            return

        target = parts[1].strip()
        client = event.client

        try:
            entity = await client.get_entity(target)
            chat_id = entity.id
            # کانال‌ها/سوپرگروه‌ها ID منفی دارند در Telegram
            # اما get_entity چت‌های عادی را با ID مثبت برمی‌گرداند
            self._source_chat = chat_id
            self._msg_cache.clear()
            self._save_settings()

            chat_title = getattr(entity, "title", None) or getattr(entity, "username", str(chat_id))
            await self._safe_edit_with_auto_delete(
                event,
                f"✅ **چت منبع تنظیم شد:**\n"
                f"• نام: `{chat_title}`\n"
                f"• آیدی: `{chat_id}`",
                delay=8.0,
            )
            self._log_info("Source chat set to %s (%s)", chat_id, chat_title)

        except errors.FloodWaitError as exc:
            await self._safe_edit_with_auto_delete(
                event,
                f"⏳ درخواست بیش از حد — لطفاً {exc.seconds} ثانیه صبر کنید.",
            )
        except Exception as exc:
            await self._safe_edit_with_auto_delete(
                event,
                f"❌ **خطا در یافتن چت:**\n`{exc}`",
            )

    async def _cmd_add_enemy(self, event) -> None:
        """دشمن — (ریپلای) اضافه کردن دشمن"""
        reply = await event.get_reply_message()
        if reply is None:
            await self._safe_edit_with_auto_delete(
                event,
                "❌ لطفاً روی پیام دشمن ریپلای بزنید و بنویسید `دشمن`.",
            )
            return

        user_id = reply.sender_id
        if user_id is None:
            await self._safe_edit_with_auto_delete(event, "❌ نمی‌توان آیدی فرستنده را تشخیص داد.")
            return

        me_id = await self._get_me_id(event.client)
        if user_id == me_id:
            await self._safe_edit_with_auto_delete(event, "❌ نمی‌توانید خودتان را دشمن کنید.")
            return

        chat_key = str(event.chat_id)
        if chat_key not in self._enemies:
            self._enemies[chat_key] = []

        if user_id in self._enemies[chat_key]:
            await self._safe_edit_with_auto_delete(
                event,
                f"⚠️ این کاربر (`{user_id}`) قبلاً در لیست دشمنان این چت بود.",
            )
            return

        self._enemies[chat_key].append(user_id)
        self._save_settings()

        # تلاش برای گرفتن نام کاربر
        display = await self._get_user_display(event.client, user_id)
        await self._safe_edit_with_auto_delete(
            event,
            f"🔴 **دشمن اضافه شد:**\n• {display}\n• آیدی: `{user_id}`",
        )
        self._log_info("Enemy added: user_id=%s in chat %s", user_id, event.chat_id)

    async def _cmd_remove_enemy(self, event) -> None:
        """حذف دشمن — (ریپلای) حذف دشمن"""
        reply = await event.get_reply_message()
        if reply is None:
            await self._safe_edit_with_auto_delete(
                event,
                "❌ لطفاً روی پیام دشمن ریپلای بزنید و بنویسید `حذف دشمن`.",
            )
            return

        user_id = reply.sender_id
        if user_id is None:
            await self._safe_edit_with_auto_delete(event, "❌ نمی‌توان آیدی فرستنده را تشخیص داد.")
            return

        chat_key = str(event.chat_id)
        enemies_here = self._enemies.get(chat_key, [])

        if user_id not in enemies_here:
            await self._safe_edit_with_auto_delete(
                event,
                f"⚠️ کاربر `{user_id}` در لیست دشمنان این چت نیست.",
            )
            return

        enemies_here.remove(user_id)
        if not enemies_here:
            del self._enemies[chat_key]
        else:
            self._enemies[chat_key] = enemies_here

        self._save_settings()
        display = await self._get_user_display(event.client, user_id)
        await self._safe_edit_with_auto_delete(
            event,
            f"✅ **دشمن حذف شد:**\n• {display}\n• آیدی: `{user_id}`",
        )
        self._log_info("Enemy removed: user_id=%s from chat %s", user_id, event.chat_id)

    async def _cmd_list_enemies(self, event) -> None:
        """لیست دشمنان — نمایش دشمنان چت فعلی"""
        chat_key = str(event.chat_id)
        enemies_here = self._enemies.get(chat_key, [])

        if not enemies_here:
            await self._safe_edit_with_auto_delete(
                event,
                "ℹ️ هیچ دشمنی در این چت ثبت نشده.",
                delay=8.0,
            )
            return

        lines = [f"🔴 **دشمنان این چت ({len(enemies_here)} نفر):**\n"]
        for i, uid in enumerate(enemies_here, 1):
            display = await self._get_user_display(event.client, uid)
            lines.append(f"{i}. {display} — `{uid}`")

        await self._safe_edit_with_auto_delete(
            event,
            "\n".join(lines),
            delay=15.0,
        )

    async def _cmd_clear_all(self, event) -> None:
        """پاک کردن همه — حذف همه دشمنان چت فعلی"""
        chat_key = str(event.chat_id)
        enemies_here = self._enemies.get(chat_key, [])

        if not enemies_here:
            await self._safe_edit_with_auto_delete(
                event,
                "ℹ️ لیست دشمنان این چت از قبل خالی است.",
            )
            return

        count = len(enemies_here)
        del self._enemies[chat_key]
        self._save_settings()

        await self._safe_edit_with_auto_delete(
            event,
            f"✅ **{count} دشمن از این چت حذف شد.**",
        )
        self._log_info("Cleared %d enemies from chat %s", count, event.chat_id)

    async def _cmd_settings(self, event) -> None:
        """تنظیمات — نمایش پیکربندی فعلی"""
        source_display = f"`{self._source_chat}`" if self._source_chat else "❌ تنظیم نشده"
        total_enemies = sum(len(v) for v in self._enemies.values())
        chat_count = len(self._enemies)
        cache_size = len(self._msg_cache)

        await self._safe_edit_with_auto_delete(
            event,
            f"⚙️ **تنظیمات ماژول دشمن:**\n\n"
            f"• **چت منبع:** {source_display}\n"
            f"• **کش پیام‌ها:** `{cache_size}` از `{_CACHE_SIZE}`\n"
            f"• **تعداد دشمنان:** `{total_enemies}` نفر در `{chat_count}` چت\n",
            delay=12.0,
        )

    async def _cmd_help(self, event) -> None:
        """راهنما دشمن — راهنمای کامل"""
        await self._safe_edit_with_auto_delete(
            event,
            EnemyModule.help_extra,
            delay=30.0,
        )

    # ── Incoming message handler ──────────────────────────────────────────

    async def _on_incoming(self, event) -> None:
        """بررسی پیام‌های ورودی و ریپلای به دشمنان"""
        try:
            await self._on_incoming_impl(event)
        except Exception as exc:
            self._log_error("خطای پیش‌بینی‌نشده در _on_incoming: %s", exc)

    async def _on_incoming_impl(self, event) -> None:
        if self._source_chat is None:
            return

        sender_id = event.sender_id
        if sender_id is None:
            return

        chat_key = str(event.chat_id)
        enemies_here = self._enemies.get(chat_key)
        if not enemies_here or sender_id not in enemies_here:
            return

        # این کاربر دشمن است — یک پیام تصادفی از منبع بگیریم
        client = event.client
        msg_id = await self._get_random_message_id(client)
        if msg_id is None:
            self._log_warning(
                "هیچ پیامی در کش یا چت منبع موجود نیست. دشمن %s نادیده گرفته شد.",
                sender_id,
            )
            return

        await self._reply_with_source_message(client, event, msg_id)

    # ── Core: reply with a source-chat message ────────────────────────────

    async def _reply_with_source_message(
        self,
        client: TelegramClient,
        event,
        source_msg_id: int,
    ) -> None:
        """
        پاسخ به پیام دشمن با یک پیام از چت منبع، بدون تگ «فوروارد».

        روش کار:
        1. از ForwardMessagesRequest با drop_author=True استفاده می‌کنیم تا
           پیام (با هر نوع رسانه‌ای) کپی شود، بدون نوشته «Forwarded from».
        2. اما ForwardMessagesRequest در چت مقصد ارسال می‌کند، نه reply.
           برای ریپلای، پیام forward شده را حذف کرده و مستقیم send می‌کنیم.

        روش بهتر (بدون دانلود):
        - forward + drop_author=True → پیام تمیز بدون forward tag
        - اما reply_to را ForwardMessagesRequest پشتیبانی نمی‌کند.
        - پس: از copy_to الگو استفاده می‌کنیم:
            a) پیام منبع را fetch می‌کنیم (فقط metadata، بدون دانلود media)
            b) اگر text-only: send_message با متن
            c) اگر media: send_file با file=msg.media (Telethon از file ID استفاده
               می‌کند و media دانلود/آپلود نمی‌شود — فقط ID رسانه منتقل می‌شود)
        """
        source_chat = self._source_chat
        if source_chat is None:
            return

        try:
            # یک بار پیام منبع را fetch می‌کنیم — فقط metadata، بدون دانلود
            source_msgs = await client.get_messages(source_chat, ids=source_msg_id)
        except errors.FloodWaitError as exc:
            self._log_warning("FloodWait %ds هنگام fetch پیام منبع.", exc.seconds)
            return
        except Exception as exc:
            self._log_error("خطا در fetch پیام منبع %s: %s", source_msg_id, exc)
            # پیام احتمالاً حذف شده — از کش پاک کنیم
            self._evict_from_cache(source_msg_id)
            return

        if source_msgs is None:
            self._evict_from_cache(source_msg_id)
            return

        # get_messages با ids= یک پیام یا None برمی‌گرداند
        msg = source_msgs

        if msg is None or not hasattr(msg, "id"):
            self._evict_from_cache(source_msg_id)
            return

        try:
            media = getattr(msg, "media", None)

            if media is not None:
                # رسانه: Telethon وقتی file=media.photo یا file=msg.media دریافت کند،
                # از file_id استفاده می‌کند و هیچ دانلود/آپلودی انجام نمی‌دهد.
                await client.send_file(
                    event.chat_id,
                    file=msg.media,
                    caption=msg.text or "",
                    reply_to=event.message.id,
                    force_document=False,
                )
            else:
                # متن خالص
                text = msg.text or msg.message or ""
                if not text:
                    # پیام خالی است (ممکن است حذف شده باشد)
                    self._evict_from_cache(source_msg_id)
                    return
                await client.send_message(
                    event.chat_id,
                    text,
                    reply_to=event.message.id,
                )

        except errors.FloodWaitError as exc:
            self._log_warning("FloodWait %ds هنگام ارسال پاسخ به دشمن.", exc.seconds)
        except errors.MessageIdInvalidError:
            self._log_debug("پیام دشمن حذف شده بود — پاسخ لغو شد.")
        except Exception as exc:
            self._log_error("خطا در ارسال پاسخ به دشمن: %s", exc)

    # ── Cache management ──────────────────────────────────────────────────

    async def _get_random_message_id(self, client: TelegramClient) -> int | None:
        """یک آیدی پیام تصادفی از کش برمی‌گرداند؛ در صورت نیاز کش را refresh می‌کند."""
        if not self._msg_cache:
            await self._fill_cache(client)

        if not self._msg_cache:
            return None

        msg_id = random.choice(self._msg_cache)

        # اگر کش رو به اتمام است، در پس‌زمینه refresh کن
        if (
            len(self._msg_cache) < _CACHE_SIZE * _REFRESH_THRESHOLD
            and not self._refreshing
        ):
            if self._refresh_task is None or self._refresh_task.done():
                self._refresh_task = asyncio.create_task(
                    self._background_refresh(client),
                    name=f"enemy_refresh_a{self.cfg.index}",
                )

        return msg_id

    async def _fill_cache(self, client: TelegramClient) -> None:
        """کش را از ابتدا با پیام‌های چت منبع پر می‌کند."""
        if self._source_chat is None:
            return

        self._refreshing = True
        try:
            ids: list[int] = []
            async for msg in client.iter_messages(
                self._source_chat,
                limit=_FETCH_LIMIT,
            ):
                # فقط پیام‌هایی که محتوا دارند (متن یا رسانه)
                has_content = bool(getattr(msg, "text", "") or getattr(msg, "media", None))
                if has_content:
                    ids.append(msg.id)

            self._msg_cache = ids
            self._log_info("کش پیام منبع پر شد: %d پیام.", len(ids))

        except errors.FloodWaitError as exc:
            self._log_warning("FloodWait %ds هنگام پر کردن کش.", exc.seconds)
        except Exception as exc:
            self._log_error("خطا در پر کردن کش: %s", exc)
        finally:
            self._refreshing = False

    async def _background_refresh(self, client: TelegramClient) -> None:
        """Refresh کش در پس‌زمینه (بدون block کردن handler)."""
        await self._fill_cache(client)

    def _evict_from_cache(self, msg_id: int) -> None:
        """یک پیام حذف‌شده را از کش بیرون می‌کند."""
        try:
            self._msg_cache.remove(msg_id)
        except ValueError:
            pass

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _get_user_display(self, client: TelegramClient, user_id: int) -> str:
        """نام نمایشی کاربر را (در صورت امکان) برمی‌گرداند."""
        try:
            entity = await client.get_entity(user_id)
            first = getattr(entity, "first_name", "") or ""
            last = getattr(entity, "last_name", "") or ""
            username = getattr(entity, "username", None)
            name = f"{first} {last}".strip() or str(user_id)
            if username:
                return f"**{name}** (@{username})"
            return f"**{name}**"
        except Exception:
            return f"`{user_id}`"


# ── Help Texts (در انتهای ماژول طبق قوانین) ─────────────────────────────────

help_text = (
    "• `منبع @username` | تنظیم چت منبع پیام‌های تصادفی\n"
    "• `دشمن` (ریپلای) | اضافه کردن کاربر به لیست دشمنان\n"
    "• `حذف دشمن` (ریپلای) | حذف از لیست دشمنان\n"
    "• `لیست دشمنان` | نمایش دشمنان چت فعلی\n"
    "• `پاک کردن همه` | پاک کردن لیست دشمنان این چت\n"
    "• `تنظیمات` | نمایش پیکربندی فعلی\n"
    "• `راهنما دشمن` | راهنمای کامل\n"
)

help_extra = (
    "🔴 **ماژول دشمن** — پاسخ تصادفی به دشمنان\n\n"
    "**نحوه کار:**\n"
    "وقتی یکی از دشمنان پیام می‌دهد، یک پیام تصادفی از چت منبع\n"
    "به‌عنوان پاسخ ارسال می‌شود (بدون تگ فوروارد).\n\n"
    "**مرحله ۱ — تنظیم منبع:**\n"
    "• `منبع @my_channel` | تنظیم کانال/گروه منبع\n"
    "• `منبع -1001234567890` | تنظیم با آیدی عددی\n\n"
    "**مرحله ۲ — تعریف دشمن:**\n"
    "• روی پیام کاربر ریپلای بزنید و بنویسید: `دشمن`\n"
    "• دشمن در همان چتی که ریپلای زدید ثبت می‌شود\n\n"
    "**مدیریت دشمنان:**\n"
    "• `حذف دشمن` (ریپلای) | حذف یک دشمن\n"
    "• `لیست دشمنان` | نمایش لیست دشمنان چت فعلی\n"
    "• `پاک کردن همه` | حذف همه دشمنان چت فعلی\n\n"
    "**اطلاعات و تنظیمات:**\n"
    "• `تنظیمات` | نمایش چت منبع، تعداد دشمنان و وضعیت کش\n\n"
    "**نکات مهم:**\n"
    "• همه انواع رسانه (عکس، ویدیو، GIF، استیکر، فایل، متن) پشتیبانی می‌شوند\n"
    "• بدون دانلود/آپلود مجدد — از file ID تلگرام استفاده می‌شود\n"
    "• تعداد دشمنان نامحدود است\n"
    "• تنظیمات در enemy.json ذخیره می‌شود\n"
    "• پس از تنظیم منبع، کش پیام‌ها خودکار پر می‌شود\n"
)

EnemyModule.help_text = help_text
EnemyModule.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return EnemyModule(context)