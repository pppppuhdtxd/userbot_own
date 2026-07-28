"""
userbot_own/modules/auto_clearer.py
════════════════════════════════════════════════════════════════
Auto-Clearer — پاک‌سازی خودکار پیام‌های ربات‌ها

Commands (Saved Messages or bot chat):
• `autoclear <type> <on/off> <1/2/3>`
    In Saved Messages → global setting (all bots)
    In bot chat       → bot-specific setting

• `autoclear status` (Saved Messages only)

Scope:
    1 = bot messages only
    2 = your messages only
    3 = both

Types:
    file | vid | pic | link | txt | media

Message Classification System (v1.6.1+)
────────────────────────────────────────
Each message is classified into exactly ONE type based on priority:
    file > vid > pic > link > txt > other

The `media` filter covers `pic + vid + file` only (real media types)
and does NOT include `link`. This keeps the semantics of "media"
accurate — WebPage previews are not true media attachments.

The `link` type detection covers:
• MessageMediaWebPage (auto-generated preview for download links, etc.)
• MessageEntityUrl / MessageEntityTextUrl (clickable URLs in text)
• KeyboardButtonUrl in ReplyInlineMarkup (inline keyboard URL buttons)
• Raw URL patterns in text (fallback)
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import logging

from telethon import TelegramClient, errors, events
from telethon.tl.types import User
from telethon.utils import get_display_name

from userbot_own.core.context import ModuleContext
from userbot_own.helpers.utils import (
    classify_message,
    read_json_file,
    safe_delete,
    write_json_file_atomic,
)
from userbot_own.modules.base import Module

log = logging.getLogger(__name__)


# ── Type / scope constants ────────────────────────────────────────────────────

_SINGLE_TYPES: tuple[str, ...] = ("file", "vid", "pic", "link", "txt")

_MEDIA_TYPES: frozenset[str] = frozenset({"pic", "vid", "file"})

_TYPES: frozenset[str] = frozenset({"pic", "txt", "vid", "file", "media", "link"})

_SCOPES: frozenset[int] = frozenset({1, 2, 3})


# ── Default per-type settings ─────────────────────────────────────────────────

_DEFAULT: dict[str, dict] = {
    "file":  {"state": False, "scope": 3},
    "vid":   {"state": False, "scope": 3},
    "pic":   {"state": False, "scope": 3},
    "link":  {"state": False, "scope": 3},
    "txt":   {"state": False, "scope": 3},
    "media": {"state": False, "scope": 3},
}


def _default_entry() -> dict[str, dict]:
    """Return a fresh copy of the default settings dict."""
    return {k: v.copy() for k, v in _DEFAULT.items()}


# ── Scope check helper ───────────────────────────────────────────────────────

def _scope_matches(scope: int, is_outgoing: bool) -> bool:
    """Return True if the scope allows deletion for this message direction."""
    if scope == 1:
        return not is_outgoing   # bot messages only
    if scope == 2:
        return is_outgoing       # user messages only
    if scope == 3:
        return True              # both
    return False


# ── Filter matching ──────────────────────────────────────────────────────────

def _message_matches_filter(msg, filter_type: str) -> bool:
    """
    Check if a message's classified type matches filter_type.

    For `media`, the message must be classified as pic, vid, or file
    (not `link`, not `txt`, not `other`).
    """
    msg_type = classify_message(msg)

    if filter_type == "media":
        return msg_type in _MEDIA_TYPES

    return msg_type == filter_type


# ── Module ───────────────────────────────────────────────────────────────────

class AutoClearer(Module):
    name = "auto_clearer"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self._global:   dict[str, dict]             = _default_entry()
        self._bots:     dict[int, dict[str, dict]]   = {}
        self._cache:    dict[int, object]            = {}
        self._settings_file = self.cfg.settings_dir / "autoclear.json"

    def setup(self, client: TelegramClient) -> None:
        self._load()
        self._add_handler(client, events.NewMessage(incoming=True),  self._on_incoming)
        self._add_handler(client, events.NewMessage(outgoing=True),  self._on_outgoing)
        self._add_handler(client, events.NewMessage(outgoing=True),  self._on_command)
        self._log_info("AutoClearer ready.")

    # ── Settings persistence ──────────────────────────────────────────────

    def _load(self) -> None:
        """Load settings from disk, auto-migrating old files missing `link`."""
        data, err = read_json_file(self._settings_file)
        if err is not None:
            self._log_error("AutoClearer load error: %s", err)
            return
        if data is None:
            return  # no file yet — keep defaults

        try:
            g = data.get("global", {})
            migrated = False
            for k in _DEFAULT:
                src = g.get(k, {})
                self._global[k] = {
                    "state": bool(src.get("state", False)),
                    "scope": int(src.get("scope", 3)),
                }
                if k not in g:
                    migrated = True

            for bid_str, bsettings in data.get("bots", {}).items():
                try:
                    bid = int(bid_str)
                except (ValueError, TypeError):
                    continue
                entry: dict[str, dict] = {}
                for k in _DEFAULT:
                    src = bsettings.get(k, {})
                    entry[k] = {
                        "state": bool(src.get("state", False)),
                        "scope": int(src.get("scope", 3)),
                    }
                self._bots[bid] = entry

            old_keys = set(g.keys()) if "global" in data else set()
            if old_keys and "link" not in old_keys:
                migrated = True

            if migrated:
                self._log_info("Settings migrated: added `link` type.")
                self._save()

        except Exception as exc:
            self._log_error("AutoClearer load error: %s", exc)

    def _save(self) -> None:
        data = {
            "global": self._global,
            "bots":   {str(k): v for k, v in self._bots.items()},
        }
        # BUG FIX (v3.0.1): this previously wrote settings directly
        # (write_text) instead of atomically like every sibling module
        # (auto_forwarder / join_left / reaction_commands) — a crash
        # mid-write could have left a truncated, unparseable
        # autoclear.json. Now uses the same atomic tmp-file-then-rename
        # helper as the others.
        err = write_json_file_atomic(self._settings_file, data, indent=4)
        if err is not None:
            self._log_error("AutoClearer save error: %s", err)

    def _effective(self, bot_id: int) -> dict[str, dict]:
        """Return bot-specific settings if present, else global."""
        return self._bots.get(bot_id, self._global)

    # ── Bot chat detection ────────────────────────────────────────────────

    async def _is_bot_chat(self, client: TelegramClient, chat_id: int) -> User | None:
        """Return the User entity if chat_id is a bot, else None."""
        if chat_id in self._cache:
            ent = self._cache[chat_id]
            return ent if isinstance(ent, User) and ent.bot else None
        try:
            ent = await client.get_entity(chat_id)
            self._cache[chat_id] = ent
            return ent if isinstance(ent, User) and ent.bot else None
        except Exception as exc:
            log.debug("[Account%d] _is_bot_chat(%s): %s", self.cfg.index, chat_id, exc)
            return None

    # ── Auto-delete on message ────────────────────────────────────────────

    async def _try_auto_delete(self, event, is_outgoing: bool) -> None:
        """Check all active filters and delete the message if any match."""
        msg = event.message
        if msg is None:
            return

        bot = await self._is_bot_chat(event.client, event.chat_id)
        if bot is None:
            return

        settings = self._effective(bot.id)
        for ftype, cfg in settings.items():
            if not cfg["state"]:
                continue
            if not _scope_matches(cfg["scope"], is_outgoing):
                continue
            if _message_matches_filter(msg, ftype):
                await safe_delete(event.client, event.chat_id, msg.id)
                return

    async def _on_incoming(self, event) -> None:
        await self._try_auto_delete(event, is_outgoing=False)

    async def _on_outgoing(self, event) -> None:
        await self._try_auto_delete(event, is_outgoing=True)

    # ── Command handling ──────────────────────────────────────────────────

    async def _on_command(self, event) -> None:
        text = (event.raw_text or "").strip()
        if not text.lower().startswith("autoclear"):
            return

        client = event.client
        me_id = await self._get_me_id(client)
        if me_id is None:
            self._log_error("Failed to resolve self ID.")
            return

        is_saved = (event.chat_id == me_id)
        parts    = text.split()

        if len(parts) >= 2 and parts[1].lower() == "status":
            if not is_saved:
                return
            await self._cmd_status(event, client)
            return

        if len(parts) != 4:
            await self._safe_edit(
                event,
                "❌ **فرمت نادرست.** استفاده:\n"
                "• `autoclear <type> <on/off> <1/2/3>`\n"
                "• `autoclear status`\n\n"
                "**انواع:** `file`, `vid`, `pic`, `link`, `txt`, `media`"
            )
            return

        ftype  = parts[1].lower()
        action = parts[2].lower()
        scope_s = parts[3]

        if ftype not in _TYPES:
            await self._safe_edit(
                event,
                f"❌ نوع نامعتبر: `{ftype}`.\n"
                f"**انواع مجاز:** `file`, `vid`, `pic`, `link`, `txt`, `media`"
            )
            return
        if action not in ("on", "off"):
            await self._safe_edit(event, "❌ عمل نامعتبر. از `on` یا `off` استفاده کنید.")
            return
        try:
            scope = int(scope_s)
            if scope not in _SCOPES:
                raise ValueError
        except ValueError:
            await self._safe_edit(
                event,
                "❌ scope نامعتبر. از `1` (بات)، `2` (شما)، یا `3` (هر دو) استفاده کنید."
            )
            return

        state = action == "on"

        target_chat = await event.get_chat()
        if isinstance(target_chat, User) and target_chat.bot:
            bid = target_chat.id
            if bid not in self._bots:
                self._bots[bid] = _default_entry()

            self._bots[bid][ftype]["state"] = state
            self._bots[bid][ftype]["scope"] = scope
            self._save()

            if state:
                proc = await event.respond(
                    f"🗑️ در حال پاک کردن پیام‌های قدیمی `{ftype}` (scope {scope})..."
                )
                deleted = await self._clear_past(
                    event.client, target_chat, {ftype: self._bots[bid][ftype]}
                )
                await self._safe_edit(
                    proc,
                    f"✅ `{deleted}` پیام `{ftype}` قدیمی پاک شد.\n"
                    f"Auto-clear برای این بات فعال شد (scope {scope})."
                )
            else:
                await self._safe_edit(
                    event,
                    f"✅ Auto-clear `{ftype}` برای این بات غیرفعال شد (scope {scope})."
                )

        elif is_saved:
            self._global[ftype]["state"] = state
            self._global[ftype]["scope"] = scope
            self._save()

            if state:
                proc = await event.respond(
                    f"🗑️ در حال پاک کردن پیام‌های قدیمی `{ftype}` در همه بات‌ها (scope {scope})..."
                )
                total = 0
                async for dialog in event.client.iter_dialogs():
                    ent = dialog.entity
                    if isinstance(ent, User) and ent.bot:
                        bot_setting = self._bots.get(ent.id, {}).get(
                            ftype, self._global[ftype]
                        )
                        total += await self._clear_past(
                            event.client, ent, {ftype: bot_setting}
                        )
                await self._safe_edit(
                    proc,
                    f"✅ `{total}` پیام `{ftype}` قدیمی پاک شد.\n"
                    f"Auto-clear کلی فعال شد (scope {scope})."
                )
            else:
                await self._safe_edit(
                    event,
                    f"✅ Auto-clear کلی `{ftype}` غیرفعال شد (scope {scope})."
                )
        else:
            await self._safe_edit(
                event,
                "ℹ️ این دستور فقط در **Saved Messages** یا **چت یک بات** قابل استفاده است."
            )

    # ── Clear past messages matching filter ───────────────────────────────

    async def _clear_past(
        self, client: TelegramClient, entity, settings: dict[str, dict]
    ) -> int:
        """Delete historical messages matching the given filter settings."""
        deleted = 0
        ids: list[int] = []
        history_limit = self.context.settings.history_limit
        try:
            async for msg in client.iter_messages(entity, limit=history_limit):
                if msg is None:
                    continue
                for ftype, cfg in settings.items():
                    if cfg["state"] and _message_matches_filter(msg, ftype):
                        ids.append(msg.id)
                        break
        except Exception as exc:
            self._log_error("_clear_past scan error: %s", exc)

        if ids:
            for i in range(0, len(ids), 100):
                batch = ids[i:i + 100]
                try:
                    await client.delete_messages(entity, batch)
                    deleted += len(batch)
                except errors.FloodWaitError as exc:
                    await asyncio.sleep(exc.seconds)
                    try:
                        await client.delete_messages(entity, batch)
                        deleted += len(batch)
                    except Exception as retry_exc:
                        self._log_error("_clear_past retry error: %s", retry_exc)
                except Exception as exc:
                    self._log_error("_clear_past batch error: %s", exc)
        return deleted

    # ── Status display ────────────────────────────────────────────────────

    async def _cmd_status(self, event, client: TelegramClient) -> None:
        lines = ["📊 **وضعیت Auto-Clear:**\n", "**تنظیمات کلی:**"]

        type_order = ["file", "vid", "pic", "link", "txt", "media"]
        type_labels = {
            "file":   "فایل",
            "vid":    "ویدیو",
            "pic":    "عکس",
            "link":   "لینک",
            "txt":    "متن",
            "media":  "رسانه (عکس+ویدیو+فایل)",
        }
        scope_labels = {1: "بات", 2: "شما", 3: "هر دو"}

        for k in type_order:
            cfg = self._global.get(k, {"state": False, "scope": 3})
            state = "✅ ON" if cfg["state"] else "❌ OFF"
            scope_desc = scope_labels.get(cfg["scope"], "?")
            label = type_labels.get(k, k)
            lines.append(f"  • `{k}` ({label}): {state} (Scope: {scope_desc})")

        active_bots = {
            bid: s for bid, s in self._bots.items()
            if any(c["state"] for c in s.values())
        }
        if active_bots:
            lines.append("\n**تنظیمات مخصوص بات‌ها (فقط فعال‌ها):**")
            for bid, settings in active_bots.items():
                try:
                    if bid in self._cache:
                        ent = self._cache[bid]
                    else:
                        ent = await client.get_entity(bid)
                        self._cache[bid] = ent
                    bot_name = get_display_name(ent)
                except Exception:
                    bot_name = f"ID {bid}"
                lines.append(f"  • **{bot_name}** ([ID: {bid}](tg://user?id={bid})):")
                for k in type_order:
                    cfg = settings.get(k, {"state": False, "scope": 3})
                    if cfg["state"]:
                        scope_desc = scope_labels.get(cfg["scope"], "?")
                        label = type_labels.get(k, k)
                        lines.append(f"    - `{k}` ({label}): ✅ ON (Scope: {scope_desc})")

        await self._safe_edit(event, "\n".join(lines), parse_mode="Markdown")


# ── Help Texts (در انتهای ماژول طبق قوانین) ─────────────────────────────────

help_text = (
    "• `autoclear <type> on <scope>` | فعال‌سازی پاک‌سازی خودکار\n"
    "• `autoclear <type> off <scope>` | غیرفعال‌سازی پاک‌سازی خودکار\n"
    "• `autoclear status` | نمایش وضعیت فعلی\n"
)

help_extra = (
    "پاک‌سازی خودکار پیام‌های ربات\n\n"
    "دستور اصلی:\n"
    "• `autoclear <type> on <scope>` | فعال‌سازی پاک‌سازی خودکار\n"
    "• `autoclear <type> off <scope>` | غیرفعال‌سازی پاک‌سازی خودکار\n"
    "• `autoclear status` | نمایش وضعیت فعلی\n\n"
    "انواع پیام (type):\n"
    "• `file` | فایل‌های ضمیمه\n"
    "• `vid` | ویدیوها و GIF\n"
    "• `pic` | عکس‌ها\n"
    "• `link` | پیام‌های حاوی لینک یا WebPage\n"
    "• `txt` | متن خالص بدون لینک\n"
    "• `media` | عکس، ویدیو و فایل بدون لینک\n\n"
    "محدوده (scope):\n"
    "• `1` | فقط پیام‌های ربات\n"
    "• `2` | فقط پیام‌های شما\n"
    "• `3` | هر دو\n\n"
    "مکان استفاده:\n"
    "• در Saved Messages | تنظیم کلی برای همه ربات‌ها\n"
    "• در چت یک ربات | تنظیم مخصوص همان ربات\n\n"
    "مثال‌ها:\n"
    "• `autoclear txt on 3` | پاک‌سازی خودکار همه متن‌ها\n"
    "• `autoclear pic on 1` | پاک‌سازی فقط عکس‌های ربات\n"
    "• `autoclear link on 3` | پاک‌سازی خودکار لینک‌ها\n"
    "• `autoclear media off 3` | غیرفعال کردن رسانه‌ها\n"
    "• `autoclear status` | نمایش وضعیت فعلی\n\n"
    "سیستم طبقه‌بندی:\n"
    "هر پیام فقط یک نوع دارد بر اساس اولویت زیر:\n"
    "`file > vid > pic > link > txt > other`\n\n"
    "نکات مهم:\n"
    "• `media` شامل `link` نمی‌شود\n"
    "• `link` شامل دکمه‌های شیشه‌ای با لینک هم می‌شود\n"
    "• تنظیمات در `autoclear.json` ذخیره می‌شوند\n"
    "• پیام‌های پین شده هرگز پاک نمی‌شوند\n"
)

AutoClearer.help_text = help_text
AutoClearer.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return AutoClearer(context)
