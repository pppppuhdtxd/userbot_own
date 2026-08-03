"""
userbot_own/modules/auto_forwarder.py
════════════════════════════════════════════════════════════════
Auto-Forwarder — فوروارد خودکار پیام‌های ربات به همان ربات

منطق اصلی:
ربات پیامی می‌فرسته → فیلتر می‌شه → همون پیام دوباره
به همون ربات فوروارد می‌شه → پیام اصلی حذف می‌شه

دستورات:
- `autofor <type> <on/off>`
    در Saved Messages → تنظیم کلی (همه بات‌ها)
    در چت بات        → تنظیم مخصوص همان بات

- `forward status` (فقط در Saved Messages)

type: txt | pic | vid | file | caption | all

نکات:
- txt     : پیام متنی محض — بلافاصله به همان بات فوروارد می‌شه
- pic/vid/file : رسانه‌ها در قالب album گروه‌بندی می‌شن (حداکثر ۱۰ تایی)
- caption : اگه on باشه caption پیام اصلی هم ارسال می‌شه
- all     : همه انواع به جز caption را toggle می‌کند
- sticker : هیچ‌وقت فوروارد نمی‌شه
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio

from telethon import TelegramClient, events
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    User,
)

from userbot_own.core.context import ModuleContext
from userbot_own.helpers.utils import is_photo, read_json_file, safe_delete, write_json_file_atomic
from userbot_own.modules.base import Module

# ── تنظیمات پیش‌فرض ───────────────────────────────────────────────────────────

_DEFAULT: dict[str, bool] = {
    "txt":     False,  # پیام‌های متنی محض
    "pic":     False,  # عکس
    "vid":     False,  # ویدیو / گیف
    "file":    False,  # فایل
    "caption": False,  # caption پیام اصلی
}

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
    """
    if msg.media is None:
        return "txt" if msg.message else None

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
        return "file"

    return None


def _is_audio(msg) -> bool:
    """بررسی می‌کند که آیا media پیام audio است (نه video/sticker)."""
    if not (hasattr(msg.media, "document") and msg.media.document):
        return False
    for attr in msg.media.document.attributes:
        if isinstance(attr, DocumentAttributeAudio):
            return True
    return False


# ── Module ────────────────────────────────────────────────────────────────────

class AutoForwarder(Module):
    name = "auto_forwarder"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self._global: dict[str, bool]            = _DEFAULT.copy()
        self._bots:   dict[int, dict[str, bool]]  = {}

        # bot_id → لیست آیتم‌های در صف: (msg_id, media, original_msg, chat_id)
        self._queues: dict[int, list]             = {}

        # bot_id → task مربوط به flush timer
        self._timers: dict[int, asyncio.Task]     = {}

        # جلوگیری از queue کردن دوباره یک پیام
        self._queued_ids: dict[int, set[int]]     = {}

        self._settings_file = self.cfg.settings_dir / "autoforward.json"

    def setup(self, client: TelegramClient) -> None:
        self._load()
        self._add_handler(client, events.NewMessage(incoming=True), self._on_incoming)
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_command)
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_status)
        self._log_info("[Account%d] AutoForwarder ready.", self.cfg.index)

    def teardown(self, client: TelegramClient) -> None:
        for t in self._timers.values():
            t.cancel()
        self._timers.clear()
        self._queues.clear()
        self._queued_ids.clear()
        super().teardown(client)

    # ── ذخیره‌سازی تنظیمات ───────────────────────────────────────

    def _load(self) -> None:
        """Load settings from file. If file doesn't exist or is invalid, use defaults."""
        data, err = read_json_file(self._settings_file)
        if err is not None:
            self._log_error("[Account%d] AutoForwarder load error: %s", self.cfg.index, err)
            return
        if data is None:
            self._log_info(
                "[Account%d] AutoForwarder settings file not found, using defaults (all OFF).",
                self.cfg.index,
            )
            return

        try:
            # Load global settings
            g = data.get("global", {})
            for k in _DEFAULT:
                if k in g:
                    self._global[k] = bool(g[k])

            # Load bot-specific settings
            for bid_str, bset in data.get("bots", {}).items():
                try:
                    bid = int(bid_str)
                    entry = _DEFAULT.copy()
                    for k in _DEFAULT:
                        if k in bset:
                            entry[k] = bool(bset[k])
                    self._bots[bid] = entry
                except (ValueError, TypeError):
                    pass

            self._log_info("[Account%d] AutoForwarder settings loaded.", self.cfg.index)
        except Exception as exc:
            self._log_error("[Account%d] AutoForwarder load error: %s", self.cfg.index, exc)

    def _save(self) -> None:
        """Atomically persist settings to disk (see helpers.utils.write_json_file_atomic)."""
        data = {
            "global": self._global,
            "bots":   {str(k): v for k, v in self._bots.items()},
        }
        err = write_json_file_atomic(self._settings_file, data, indent=4)
        if err is not None:
            self._log_error("[Account%d] AutoForwarder save error: %s", self.cfg.index, err)

    # ── ابزارهای کمکی ────────────────────────────────────────────

    def _effective(self, bot_id: int) -> dict[str, bool]:
        return self._bots.get(bot_id, self._global)

    def _differs_from_global(self, bot_id: int) -> bool:
        if bot_id not in self._bots:
            return False
        return self._bots[bot_id] != self._global

    def _cleanup_if_same(self, bot_id: int) -> None:
        if bot_id in self._bots and not self._differs_from_global(bot_id):
            del self._bots[bot_id]

    # ── منطق فوروارد ──────────────────────────────────────────────

    async def _on_incoming(self, event) -> None:
        msg = event.message
        if msg is None:
            return

        # فقط چت‌های خصوصی
        chat = await event.get_chat()
        if not isinstance(chat, User):
            return

        try:
            sender = await event.get_sender()
        except Exception:
            return
        if sender is None or not getattr(sender, "bot", False):
            return

        ctype = _content_type(msg)
        if ctype is None:
            return

        settings = self._effective(sender.id)

        # ── txt: فوروارد فوری به همان بات ── ────────────────────────
        if ctype == "txt":
            if not settings.get("txt", False):
                return
            try:
                await event.client.send_message(event.chat_id, msg.message)
                await safe_delete(event.client, event.chat_id, msg.id)
                self._log_debug(
                    "[Account%d] txt forwarded to bot %d.",
                    self.cfg.index, sender.id,
                )
            except Exception as exc:
                self._log_error(
                    "[Account%d] txt forward error: %s", self.cfg.index, exc
                )
            return

        # ── media: بررسی تنظیم و افزودن به queue ─────────────────
        if not settings.get(ctype, False):
            return

        bid = sender.id

        # جلوگیری از تکراری شدن
        if msg.id in self._queued_ids.get(bid, set()):
            self._log_debug(
                "[Account%d] Duplicate msg %d skipped.", self.cfg.index, msg.id
            )
            return

        # chat_id هم ذخیره می‌کنیم تا موقع flush بدونیم کجا بفرستیم
        self._queues.setdefault(bid, []).append((msg.id, msg.media, msg, event.chat_id))
        self._queued_ids.setdefault(bid, set()).add(msg.id)

        self._log_debug(
            "[Account%d] Queued %s msg %d from bot %d (queue: %d).",
            self.cfg.index, ctype, msg.id, bid, len(self._queues[bid]),
        )

        # اگه به سقف album رسیدیم، بلافاصله flush کن
        if len(self._queues[bid]) >= _ALBUM_MAX:
            if bid in self._timers:
                self._timers[bid].cancel()
                self._timers.pop(bid, None)
            self._timers[bid] = asyncio.create_task(
                self._flush(event.client, bid, immediate=True),
                name=f"autofor_flush_a{self.cfg.index}_b{bid}",
            )
            return

        # ریست timer
        if bid in self._timers:
            self._timers[bid].cancel()
        self._timers[bid] = asyncio.create_task(
            self._flush(event.client, bid, immediate=False),
            name=f"autofor_flush_a{self.cfg.index}_b{bid}",
        )

    async def _flush(
        self,
        client: TelegramClient,
        bot_id: int,
        immediate: bool = False,
    ) -> None:
        """
        پیام‌های queue شده را گروه‌بندی کرده و به همان چت بات ارسال می‌کند.
        سپس پیام‌های اصلی را حذف می‌کند.
        """
        if not immediate:
            await asyncio.sleep(_GROUP_DELAY)

        queue = self._queues.pop(bot_id, [])
        self._timers.pop(bot_id, None)
        self._queued_ids.pop(bot_id, None)

        if not queue:
            return

        settings    = self._effective(bot_id)
        use_caption = settings.get("caption", False)

        # chat_id یکسانه برای همه پیام‌های این queue (همه از یه بات میان)
        bot_chat_id = queue[0][3]

        # جداسازی audio (audio نمی‌تونه در album با عکس/ویدیو باشه)
        audio_items = [(mid, med, msg) for mid, med, msg, _ in queue if _is_audio(msg)]
        other_items = [(mid, med, msg) for mid, med, msg, _ in queue if not _is_audio(msg)]

        # ارسال media معمولی در batches به همان بات
        if other_items:
            await self._send_batched(client, other_items, bot_chat_id, use_caption, bot_id)

        # ارسال audio‌ها تک‌تک به همان بات
        for mid, med, msg in audio_items:
            try:
                cap = msg.message if use_caption else None
                await client.send_file(bot_chat_id, med, caption=cap)
                await safe_delete(client, bot_chat_id, mid)
                self._log_debug(
                    "[Account%d] Audio %d forwarded to bot %d.",
                    self.cfg.index, mid, bot_id,
                )
            except Exception as exc:
                self._log_error(
                    "[Account%d] Audio forward error (msg %d, bot %d): %s",
                    self.cfg.index, mid, bot_id, exc,
                )

    async def _send_batched(
        self,
        client: TelegramClient,
        items: list,
        bot_chat_id: int,
        use_caption: bool,
        bot_id: int,
    ) -> None:
        """
        رسانه‌ها را در batch‌های حداکثر ۱۰ تایی به همان چت بات ارسال می‌کند.
        در صورت شکست album، یک‌به‌یک امتحان می‌کند.
        """
        for i in range(0, len(items), _ALBUM_MAX):
            batch = items[i : i + _ALBUM_MAX]

            files   = [med for _, med, _ in batch]
            all_ids = [mid for mid, _, _ in batch]

            final_cap: str | None = None
            if use_caption:
                parts = [msg.message for _, _, msg in batch if msg.message]
                if parts:
                    final_cap = "\n---\n".join(parts)

            # v3.0.9 fix: track which original message IDs were actually
            # sent successfully, per item — not one batch-wide flag. The
            # old `sent_ok` boolean was set True/False for the *whole*
            # batch based on the one-by-one fallback's last failure, so a
            # single failed item among several successes caused ALL
            # originals in the batch (including the ones that genuinely
            # forwarded) to be left undeleted — permanent duplicate
            # content in the bot chat. `sent_ids` now records exactly
            # which originals may be safely deleted.
            sent_ids: list[int] = []
            try:
                if len(files) == 1:
                    await client.send_file(bot_chat_id, files[0], caption=final_cap)
                else:
                    await client.send_file(bot_chat_id, files, caption=final_cap)
                sent_ids = list(all_ids)
                self._log_debug(
                    "[Account%d] Batch of %d media forwarded to bot %d.",
                    self.cfg.index, len(files), bot_id,
                )
            except Exception as exc:
                self._log_error(
                    "[Account%d] Album send failed (bot %d): %s — retrying one-by-one.",
                    self.cfg.index, bot_id, exc,
                )
                for mid, med, msg in batch:
                    try:
                        cap = msg.message if use_caption else None
                        await client.send_file(bot_chat_id, med, caption=cap)
                        sent_ids.append(mid)
                    except Exception as exc2:
                        self._log_error(
                            "[Account%d] Fallback send FAILED for msg %d: %s",
                            self.cfg.index, mid, exc2,
                        )

            # حذف پیام‌های اصلی فقط برای آیتم‌هایی که واقعاً ارسال شدند
            if sent_ids:
                try:
                    await safe_delete(client, bot_chat_id, sent_ids)
                except Exception as exc:
                    self._log_error(
                        "[Account%d] Delete originals failed (bot %d): %s",
                        self.cfg.index, bot_id, exc,
                    )
            if len(sent_ids) < len(all_ids):
                self._log_warning(
                    "[Account%d] Skipping delete for %d/%d item(s) — send failed for bot %d.",
                    self.cfg.index, len(all_ids) - len(sent_ids), len(all_ids), bot_id,
                )

    # ── دستور: autofor <type> <on/off> ───────────────────────────

    async def _on_command(self, event) -> None:
        text = (event.raw_text or "").strip()
        if not text.lower().startswith("autofor"):
            return

        parts = text.split()
        if len(parts) < 3:
            await event.edit(
                "❌ فرمت نادرست.\n"
                "استفاده: `autofor <type> <on/off>`\n"
                "type: `txt` | `pic` | `vid` | `file` | `caption` | `all`"
            )
            return

        ftype  = parts[1].lower()
        action = parts[2].lower()

        if ftype not in _DEFAULT and ftype != "all":
            await event.edit(
                f"❌ نوع نامعتبر: `{ftype}`\n"
                "مقادیر مجاز: `txt`, `pic`, `vid`, `file`, `caption`, `all`"
            )
            return
        if action not in ("on", "off"):
            await event.edit("❌ عمل نامعتبر. از `on` یا `off` استفاده کنید.")
            return

        client   = event.client
        me_id    = await self._get_me_id(client)
        is_saved = (event.chat_id == me_id)
        state    = (action == "on")

        # ── Saved Messages → تنظیم کلی ───────────────────────────
        if is_saved:
            if ftype == "all":
                for k in ("txt", "pic", "vid", "file"):
                    self._global[k] = state
                label = "همه انواع (به جز caption)"
            else:
                self._global[ftype] = state
                label = f"`{ftype}`"

            for bid in list(self._bots):
                self._cleanup_if_same(bid)
            self._save()

            status_lines = [
                f"✅ Auto-forward کلی {label} **{'فعال' if state else 'غیرفعال'}** شد.\n",
                "**وضعیت فعلی:**",
            ]
            for k, v in self._global.items():
                status_lines.append(f"  • `{k}`: {'✅ ON' if v else '❌ OFF'}")
            await event.edit("\n".join(status_lines), parse_mode="Markdown")
            self._log_info(
                "[Account%d] Global autofor %s → %s.", self.cfg.index, ftype, action
            )

        # ── چت بات → تنظیم اختصاصی ──────────────────────────────
        else:
            chat = await event.get_chat()
            if not isinstance(chat, User) or not getattr(chat, "bot", False):
                return

            bid      = chat.id
            bot_name = f"@{chat.username}" if chat.username else str(bid)

            if bid not in self._bots:
                self._bots[bid] = self._effective(bid).copy()

            if ftype == "all":
                for k in ("txt", "pic", "vid", "file"):
                    self._bots[bid][k] = state
                label = "همه انواع (به جز caption)"
            else:
                self._bots[bid][ftype] = state
                label = f"`{ftype}`"

            self._cleanup_if_same(bid)
            self._save()

            effective = self._effective(bid)
            status_lines = [
                f"✅ Auto-forward {label} برای {bot_name} **{'فعال' if state else 'غیرفعال'}** شد.\n",
                f"**وضعیت {bot_name}:**",
            ]
            for k, v in effective.items():
                status_lines.append(f"  • `{k}`: {'✅ ON' if v else '❌ OFF'}")
            await event.edit("\n".join(status_lines), parse_mode="Markdown")
            self._log_info(
                "[Account%d] Bot-specific autofor for %d: %s → %s.",
                self.cfg.index, bid, ftype, action,
            )

    # ── دستور: forward status ─────────────────────────────────────

    async def _on_status(self, event) -> None:
        text = (event.raw_text or "").strip().lower()
        if text != "forward status":
            return

        client = event.client
        if not await self._is_saved_messages(event):
            return

        lines = ["📊 **Auto-Forward Status:**", "\n**تنظیمات کلی:**"]
        for k, v in self._global.items():
            lines.append(f"  • `{k}`: {'✅ ON' if v else '❌ OFF'}")

        diff_bots = {bid: s for bid, s in self._bots.items() if s != self._global}
        if diff_bots:
            lines.append("\n**تنظیمات متفاوت برای بات‌ها:**")
            for bid, bset in diff_bots.items():
                try:
                    ent  = await client.get_entity(bid)
                    name = f"@{ent.username}" if ent.username else str(bid)
                except Exception:
                    name = str(bid)
                lines.append(f"\n**{name}:**")
                for k, v in bset.items():
                    if v != self._global.get(k):
                        lines.append(
                            f"  • `{k}`: {'✅ ON' if v else '❌ OFF'}   "
                            f"(کلی: {'✅' if self._global.get(k) else '❌'})"
                        )
        else:
            lines.append("\n_همه بات‌ها از تنظیمات کلی پیروی می‌کنند._")

        if self._queues:
            lines.append("\n**Queue های فعال:**")
            for bid, q in self._queues.items():
                lines.append(f"  • Bot `{bid}`: {len(q)} پیام در صف")

        await event.edit("\n".join(lines), parse_mode="Markdown")
        self._log_debug("[Account%d] Forward status displayed.", self.cfg.index)


# ── Help Texts (در انتهای ماژول طبق قوانین) ─────────────────────────────────

help_text = (
    "• `autofor <type> on` | فعال‌سازی فوروارد\n"
    "• `autofor <type> off` | غیرفعال‌سازی فوروارد\n"
    "• `forward status` | نمایش وضعیت فعلی\n"
)

help_extra = (
    "فوروارد خودکار - فوروارد خودکار پیام‌های ربات به همان ربات\n\n"
    "دستور اصلی:\n"
    "• `autofor <type> on` | فعال‌سازی فوروارد\n"
    "• `autofor <type> off` | غیرفعال‌سازی فوروارد\n"
    "• `forward status` | نمایش وضعیت فعلی (فقط در Saved Messages)\n\n"
    "انواع پیام (type):\n"
    "• `txt` | پیام‌های متنی محض (فوروارد فوری)\n"
    "• `pic` | عکس‌ها\n"
    "• `vid` | ویدیوها و GIF\n"
    "• `file` | فایل‌های ضمیمه (شامل audio)\n"
    "• `caption` | ارسال caption پیام اصلی\n"
    "• `all` | همه انواع به جز caption\n\n"
    "مکان استفاده:\n"
    "• در Saved Messages | تنظیم کلی برای همه ربات‌ها\n"
    "• در چت یک ربات | تنظیم مخصوص همان ربات\n\n"
    "منطق فوروارد:\n"
    "• `txt` | بلافاصله به همان ربات فوروارد می‌شود و پیام اصلی حذف می‌شود\n"
    "• `pic` / `vid` / `file` | در قالب album گروه‌بندی می‌شوند (حداکثر ۱۰ تایی)\n"
    "• `caption` | اگر on باشد، caption پیام اصلی هم ارسال می‌شود\n"
    "• `sticker` | هیچ‌وقت فوروارد نمی‌شود (حتی اگر فعال باشد)\n"
    "• `audio` | تک‌تک فوروارد می‌شود (نمی‌تواند در album با عکس/ویدیو باشد)\n\n"
    "مثال‌ها:\n"
    "• `autofor txt on` | فعال‌سازی فوروارد پیام‌های متنی\n"
    "• `autofor pic on` | فعال‌سازی فوروارد عکس‌ها\n"
    "• `autofor caption on` | ارسال caption پیام‌های اصلی\n"
    "• `autofor all off` | غیرفعال‌سازی همه انواع\n"
    "• `forward status` | نمایش وضعیت فعلی\n\n"
    "نکات مهم:\n"
    "• پیام‌های اصلی پس از فوروارد موفق حذف می‌شوند\n"
    "• اگر فوروارد album شکست بخورد، پیام‌ها تک‌تک فوروارد می‌شوند\n"
    "• گروه‌بندی media با تأخیر ۱ ثانیه انجام می‌شود\n"
    "• تنظیمات در `autoforward.json` ذخیره می‌شود\n"
    "• فقط پیام‌های ربات‌ها فوروارد می‌شوند\n"
    "• فقط در چت‌های خصوصی (Private Chat) کار می‌کند\n"
    "• همه تنظیمات به‌صورت پیش‌فرض خاموش هستند\n"
)

AutoForwarder.help_text = help_text
AutoForwarder.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return AutoForwarder(context)
