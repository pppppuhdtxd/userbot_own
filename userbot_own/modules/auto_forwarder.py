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
    MessageMediaWebPage,
    User,
)

from userbot_own.core.context import ModuleContext
from userbot_own.helpers.utils import (
    is_audio,
    is_file,
    is_photo,
    read_json_file,
    safe_delete,
    write_json_file_atomic,
)
from userbot_own.modules.base import Module

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


# ── Module ────────────────────────────────────────────────────────────────────

class AutoForwarder(Module):
    name = "auto_forwarder"
    category = "forward"
    desc = "فوروارد خودکار"

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
            #
            # v3.2.2 (B-02): per-bot entries are stored as OVERRIDES ONLY —
            # just the types the user explicitly set for that bot — instead
            # of a full snapshot of every type. Files written by 3.2.1 and
            # earlier stored a full snapshot, which is indistinguishable
            # from "the user set all of these" and therefore permanently
            # shadowed the global settings. The migration keeps a type as
            # an override only if it actually DIFFERS from the global value;
            # a type the user never touched falls back to the global, which
            # is exactly what the user meant.
            for bid_str, bset in data.get("bots", {}).items():
                try:
                    bid = int(bid_str)
                except (ValueError, TypeError):
                    continue
                if not isinstance(bset, dict):
                    continue
                entry: dict[str, bool] = {}
                for k in _DEFAULT:
                    if k not in bset:
                        continue
                    value = bool(bset[k])
                    if value != self._global[k]:
                        entry[k] = value
                if entry:
                    self._bots[bid] = entry

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
        """
        Return the effective settings for *bot_id*: the global settings with
        this bot's explicit overrides layered on top.

        v3.2.2 (B-02): this is the merge the old design was missing.
        Previously it returned ``self._bots.get(bot_id, self._global)`` — a
        bot entry REPLACED the global wholesale. Because a bot entry used to
        be a full snapshot of every type, one per-bot command (say
        ``autofor pic on``) silently disabled every globally enabled type
        for that bot, permanently, and nothing in the status output showed
        it.

        The merge is now: ``{**self._global, **self._bots.get(bot_id, {})}``
        — the global applies unless the bot explicitly overrides that type.
        """
        overrides = self._bots.get(bot_id)
        if not overrides:
            return self._global
        return {**self._global, **overrides}

    def _set_bot_override(self, bot_id: int, ftype: str, value: bool) -> None:
        """
        Set (or clear) one type's override for a bot.

        v3.2.2 (B-02): when the new value equals the global value for that
        type, the override is REMOVED rather than stored — so a bot's
        override set can never grow a redundant entry that shadows a later
        global change. If the bot ends up with no overrides at all, its
        entry is removed entirely.
        """
        overrides = dict(self._bots.get(bot_id, {}))
        if value == self._global[ftype]:
            overrides.pop(ftype, None)
        else:
            overrides[ftype] = value
        if overrides:
            self._bots[bot_id] = overrides
        else:
            self._bots.pop(bot_id, None)

    def _differs_from_global(self, bot_id: int) -> bool:
        """
        True if this bot has at least one override that differs from the
        global value for that type.

        v3.2.2 (B-02): under override-only storage, "differs from the global"
        no longer means "the whole bot dict != the global dict" — the bot
        dict holds only the overridden types, so the comparison has to be
        made per type. An override that equals the global is not an
        override at all.
        """
        overrides = self._bots.get(bot_id)
        if not overrides:
            return False
        return any(overrides.get(k) != self._global.get(k) for k in overrides)

    def _cleanup_if_same(self, bot_id: int) -> None:
        """
        Drop a bot's overrides that no longer differ from the global, and
        remove the bot's entry entirely if nothing is left.

        v3.2.2 (B-02): called after a global change. An override that now
        matches the global is redundant — the global applies anyway — so
        keeping it would only risk shadowing a LATER global change.
        """
        overrides = self._bots.get(bot_id)
        if not overrides:
            self._bots.pop(bot_id, None)
            return
        remaining = {
            k: v for k, v in overrides.items() if v != self._global.get(k)
        }
        if remaining:
            self._bots[bot_id] = remaining
        else:
            self._bots.pop(bot_id, None)

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

        # ── txt / link: فوروارد فوری به همان بات ── ──────────────────
        #
        # v3.2.2 (B-12): "link" is handled on the same immediate path as
        # "txt" — a WebPage-preview message is sent back to the bot as text
        # (its caption plus the URL), exactly like a plain text message.
        if ctype in ("txt", "link"):
            if not settings.get(ctype, False):
                return
            try:
                await event.client.send_message(event.chat_id, msg.message)
                await safe_delete(event.client, event.chat_id, msg.id)
                self._log_debug(
                    "[Account%d] %s forwarded to bot %d.",
                    self.cfg.index, ctype, sender.id,
                )
            except Exception as exc:
                self._log_error(
                    "[Account%d] %s forward error: %s", self.cfg.index, ctype, exc
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
                "type: `txt` | `link` | `pic` | `vid` | `file` | `caption` | `all`"
            )
            return

        ftype  = parts[1].lower()
        action = parts[2].lower()

        if ftype not in _DEFAULT and ftype != "all":
            await event.edit(
                f"❌ نوع نامعتبر: `{ftype}`\n"
                "مقادیر مجاز: `txt`, `link`, `pic`, `vid`, `file`, `caption`, `all`"
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
                for k in _ALL_CONTENT_TYPES:
                    self._global[k] = state
                label = "همه انواع (به جز caption)"
            else:
                self._global[ftype] = state
                label = f"`{ftype}`"

            # v3.2.2 (B-02): a global change must not leave a bot's override
            # set pointing at a stale value. Any bot override that now
            # equals the global is no longer an override at all, so it is
            # dropped — otherwise the bot would keep shadowing the global
            # for a type the user never meant to differentiate.
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

            # v3.2.2 (B-02): store an OVERRIDE for the affected type(s)
            # only — never a full snapshot of the global. _set_bot_override()
            # drops an override that matches the global and removes the
            # bot's entry once it holds no overrides at all.
            if ftype == "all":
                for k in _ALL_CONTENT_TYPES:
                    self._set_bot_override(bid, k, state)
                label = "همه انواع (به جز caption)"
            else:
                self._set_bot_override(bid, ftype, state)
                label = f"`{ftype}`"

            self._save()

            # The effective value for this bot is the merged one — it is
            # what actually governs the live forward filter.
            effective = self._effective(bid)
            overrides = self._bots.get(bid, {})
            status_lines = [
                f"✅ Auto-forward {label} برای {bot_name} **{'فعال' if state else 'غیرفعال'}** شد.\n",
                f"**وضعیت {bot_name} (ادغام‌شده با کلی):**",
            ]
            for k, v in effective.items():
                mark = "✏️ " if k in overrides else ""
                status_lines.append(f"  • {mark}`{k}`: {'✅ ON' if v else '❌ OFF'}")
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

        # v3.2.2 (B-02): the per-bot block now shows the MERGED effective
        # settings — what actually governs the live filter for that bot —
        # and marks which types are the bot's own OVERRIDE of the global.
        #
        # Before this fix, a bot entry was a full snapshot, so this block
        # listed only the types that DIFFERED from the global. Every type
        # the bot inherited from the global was invisible — and every type
        # the bot's snapshot had silently turned OFF (thereby shadowing a
        # globally-enabled rule) was invisible too. The user saw "everything
        # follows the global" while a global rule was in fact being
        # overridden. The merged view is shown now, with an explicit marker
        # on genuine overrides.
        diff_bots = {
            bid: s for bid, s in self._bots.items()
            if any(self._effective(bid).get(k) for k in s)
        }
        if diff_bots:
            lines.append("\n**تنظیمات بات‌ها (ادغام‌شده با کلی):**")
            for bid, overrides in diff_bots.items():
                try:
                    ent  = await client.get_entity(bid)
                    name = f"@{ent.username}" if ent.username else str(bid)
                except Exception:
                    name = str(bid)
                effective = self._effective(bid)
                lines.append(f"\n**{name}:**")
                for k, v in effective.items():
                    mark = "✏️ " if k in overrides else ""
                    lines.append(
                        f"  • {mark}`{k}`: {'✅ ON' if v else '❌ OFF'}   "
                        f"(کلی: {'✅' if self._global.get(k) else '❌'})"
                    )
            lines.append("\n_✏️ = تنظیم مخصوص این بات (override)_")
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
    "• `link` | پیام‌های لینک (WebPage preview) — فوروارد فوری\n"
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
    "• `link` | همان رفتار `txt` — لینک به‌همراه کپشن به همان ربات ارسال می‌شود\n"
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
