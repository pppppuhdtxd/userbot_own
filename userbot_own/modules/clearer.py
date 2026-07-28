"""
userbot_own/modules/clearer.py
════════════════════════════════════════════════════════════════
Manual Message Clearing — پاک‌سازی دستی پیام‌ها در چت فعلی

دستورات (قابل استفاده در هر چتی):
- `clear`                — حذف پیام‌های متنی و لینک‌ها (txt + link)
- `clear all`            — حذف همه پیام‌ها
- `clear media`          — حذف رسانه‌های واقعی (pic + vid + file)
- `clear pic`            — فقط عکس‌ها
- `clear vid`            — فقط ویدیوها و GIF ها
- `clear file`           — فقط فایل‌های ضمیمه
- `clear txt`            — فقط پیام‌های متنی خالص (بدون لینک)
- `clear link`           — فقط پیام‌های حاوی لینک (WebPage, URL entity,
                           inline keyboard URL button, raw URL in text)
- `clear self`           — فقط پیام‌های خودتان
- `clear bot`            — فقط پیام‌های ربات‌ها

Strict argument validation:
- Any argument outside VALID_ARGS causes the command to be silently ignored.
- This prevents false positives like `clear fvjnfvo` from triggering cleanup.

Permission handling:
- The module does NOT pre-check permissions. It attempts to delete every
  matching message via `batch_delete()`, which internally handles all
  permission errors (MessageDeleteForbiddenError, ChatAdminRequiredError,
  etc.) and counts them as "failed" in the final report.
- This approach is simpler and more accurate than guessing permissions,
  especially in bot chats where users can delete any message.

Message Classification System (v1.6.1+)
────────────────────────────────────────
Each message is classified into exactly ONE type based on priority:
    file > vid > pic > link > txt > other

The `link` type covers:
- MessageMediaWebPage (download URLs, t.me links, bot deep links)
- MessageEntityUrl / MessageEntityTextUrl (clickable URLs in text)
- KeyboardButtonUrl in ReplyInlineMarkup (دکمه شیشه‌ای با لینک)
- Raw URL patterns in text (fallback)

This ensures predictable and non-overlapping filter behavior.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import time

from telethon import TelegramClient, events
from telethon.tl.types import User

from userbot_own.core.context import ModuleContext
from userbot_own.helpers.utils import batch_delete, classify_message
from userbot_own.modules.base import Module

# ── Valid command arguments ──────────────────────────────────────────────────

#: Any argument not in this set causes the command to be silently ignored.
VALID_ARGS: frozenset[str] = frozenset({
    # Type filters
    "all", "media", "pic", "vid", "file", "txt", "link",
    # Scope filters
    "self", "bot",
})


# ── Type filters ─────────────────────────────────────────────────────────────

#: Maps command argument → set of classified types to delete.
#: `clear` (no argument) is equivalent to `clear txt link`.
#: `clear media` = pic + vid + file (does NOT include `link`).
TYPE_FILTERS: dict[str, set[str]] = {
    # Default: text + link messages (most common cleanup use-case)
    "default": {"txt", "link"},

    # Single-type filters
    "txt":   {"txt"},
    "link":  {"link"},
    "pic":   {"pic"},
    "vid":   {"vid"},
    "file":  {"file"},

    # Composite filters
    "media": {"pic", "vid", "file"},  # Real media only — no `link`
    "all":   {"file", "vid", "pic", "link", "txt", "other"},
}


# ── Module ───────────────────────────────────────────────────────────────────

class Clearer(Module):
    name = "clearer"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self._me_id: int | None = None
        self._me_id_task: asyncio.Task | None = None

    def setup(self, client: TelegramClient) -> None:
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_command)

        # Store the task reference so teardown() can cancel it on hot-reload,
        # preventing it from calling client.get_me() on a stale/disconnected
        # client and potentially overwriting _me_id on the new instance.
        self._me_id_task = asyncio.create_task(
            self._cache_me_id(client),
            name=f"clearer_me_a{self.cfg.index}",
        )

        self._log_info("Clearer ready.")

    def teardown(self, client: TelegramClient) -> None:
        if self._me_id_task is not None and not self._me_id_task.done():
            self._me_id_task.cancel()
        self._me_id_task = None
        super().teardown(client)

    async def _cache_me_id(self, client: TelegramClient) -> None:
        await asyncio.sleep(2)
        try:
            if client.is_connected():
                me = await client.get_me()
                if me:
                    self._me_id = me.id
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._log_error("Failed to cache me_id: %s", exc)

    # ── Command dispatcher ─────────────────────────────────────────────────

    async def _on_command(self, event) -> None:
        text = (event.raw_text or "").strip()
        parts = text.split()

        if not parts or parts[0].lower() != "clear":
            return

        client = event.client
        args = [p.lower() for p in parts[1:]]

        # ── STRICT VALIDATION ─────────────────────────────────────────────
        # If any argument is invalid, silently ignore the command.
        # This prevents false positives like `clear fvjnfvo`.
        for arg in args:
            if arg not in VALID_ARGS:
                return

        # Determine filter scope
        scope: str = "all"  # all | self | bot
        type_args: list[str] = []

        for arg in args:
            if arg in TYPE_FILTERS:
                type_args.append(arg)
            elif arg == "self":
                scope = "self"
            elif arg == "bot":
                scope = "bot"

        # BUG FIX (v3.0.1): a combined command like `clear txt link pic` must
        # process all three types. The previous version tracked "the active
        # type" as a single `mode` string that each recognized type keyword
        # overwrote in turn, so only the *last* one in the command ever took
        # effect — `clear txt link pic` silently became `clear pic`, and
        # `txt`/`link` were never even checked. Every type keyword found is
        # now unioned into target_types instead, so multiple types combine
        # correctly. A single type keyword still produces exactly the same
        # target_types as before (union of one set is that set); the
        # scope-only and bare-`clear` defaults below are unchanged.
        if type_args:
            target_types: set[str] = set()
            for t in type_args:
                target_types |= TYPE_FILTERS[t]
        elif scope != "all":
            # Handle scope-only commands (clear self / clear bot without type)
            target_types = TYPE_FILTERS["all"]
        else:
            target_types = TYPE_FILTERS["default"]

        await self._run_clear(client, event, target_types, scope)

    # ── Main clear logic ───────────────────────────────────────────────────

    async def _run_clear(
        self,
        client: TelegramClient,
        event,
        target_types: set[str],
        scope: str,
    ) -> None:
        """Scan chat history and delete messages matching the filter."""
        chat_id = event.chat_id
        command_id = event.message.id
        history_limit = self.context.settings.history_limit

        # Show progress
        type_label = self._format_type_label(target_types)
        scope_label = {"all": "همه", "self": "خودم", "bot": "ربات‌ها"}[scope]

        try:
            status_msg = await event.edit(
                f"🔍 **در حال اسکن...**\n"
                f"• نوع: {type_label}\n"
                f"• محدوده: {scope_label}\n"
                f"• حداکثر: `{history_limit}` پیام"
            )
        except Exception as exc:
            self._log_error("Failed to create status message: %s", exc)
            return

        # IDs to skip: command itself + status message (they share the same ID
        # after edit, but we keep both for safety)
        skip_ids: set[int] = {command_id}
        try:
            if status_msg and status_msg.id:
                skip_ids.add(status_msg.id)
        except Exception:
            pass

        # Collect matching message IDs
        to_delete: list[int] = []
        scanned = 0
        matched_by_type: dict[str, int] = {}
        start_time = time.monotonic()

        try:
            async for msg in client.iter_messages(chat_id, limit=history_limit):
                # Skip command and status message BEFORE counting
                if msg.id in skip_ids:
                    continue

                # Now count this message as scanned
                scanned += 1

                # Scope filter
                if not self._matches_scope(msg, scope):
                    continue

                # Type classification (uses shared classify_message helper)
                msg_type = classify_message(msg)
                if msg_type not in target_types:
                    continue

                to_delete.append(msg.id)
                matched_by_type[msg_type] = matched_by_type.get(msg_type, 0) + 1

        except Exception as exc:
            self._log_error("Scan error: %s", exc)

        # No matches
        if not to_delete:
            elapsed = time.monotonic() - start_time
            await self._safe_edit(
                status_msg,
                f"ℹ️ **پیامی یافت نشد**\n"
                f"• اسکن شده: `{scanned}` پیام\n"
                f"• نوع: {type_label}\n"
                f"• زمان: `{elapsed:.2f}s`"
            )
            # Self-delete the result message after a short delay
            self._track_delete_task(status_msg, 6.0)
            return

        # Update status before deletion
        await self._safe_edit(
            status_msg,
            f"🗑 **در حال حذف `{len(to_delete)}` پیام...**\n"
            f"• اسکن شده: `{scanned}` پیام"
        )

        # Batch delete — internally handles all permission errors and counts
        # successful deletions accurately. Messages that fail due to permission
        # are counted separately in the final report.
        deleted_count = await batch_delete(client, chat_id, to_delete, batch_size=100)

        elapsed = time.monotonic() - start_time
        failed_count = len(to_delete) - deleted_count

        # Build type breakdown
        breakdown_lines = []
        for t in ("file", "vid", "pic", "link", "txt", "other"):
            count = matched_by_type.get(t, 0)
            if count:
                breakdown_lines.append(f"  • `{t}`: {count}")
        breakdown = "\n".join(breakdown_lines) if breakdown_lines else "  —"

        # Choose icon/title based on outcome
        if failed_count > 0:
            title = "⚠️ **پاک‌سازی با محدودیت انجام شد**"
        else:
            title = "✅ **پاک‌سازی کامل شد**"

        # Build report
        report_lines = [
            title,
            "",
            "📊 **آمار:**",
            f"• اسکن شده: `{scanned}`",
            f"• حذف شده: `{deleted_count}`",
        ]
        if failed_count > 0:
            report_lines.append(f"• ناموفق: `{failed_count}`")
        report_lines.append(f"• زمان: `{elapsed:.2f}s`")
        report_lines.append("")
        report_lines.append("🏷 **بر اساس نوع:**")
        report_lines.append(breakdown)

        result_text = "\n".join(report_lines)

        # Try to edit status message; fall back to a new message if it was deleted
        try:
            await status_msg.edit(result_text)
        except Exception:
            try:
                await client.send_message(chat_id, result_text)
            except Exception as exc:
                self._log_error("Failed to send result: %s", exc)

        # Self-delete the result message after a short delay
        self._track_delete_task(status_msg, 6.0)

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _matches_scope(self, msg, scope: str) -> bool:
        """Check if a message matches the requested scope (all/self/bot)."""
        if scope == "all":
            return True

        if scope == "self":
            if self._me_id is None:
                return False
            return getattr(msg, "sender_id", None) == self._me_id

        if scope == "bot":
            sender = None
            try:
                sender = getattr(msg, "sender", None)
                if sender is None:
                    return False
            except Exception:
                return False

            if isinstance(sender, User):
                return bool(getattr(sender, "bot", False))
            return False

        return False

    @staticmethod
    def _format_type_label(target_types: set[str]) -> str:
        """Format target types as a readable Persian label."""
        if target_types == TYPE_FILTERS["all"]:
            return "**همه انواع**"
        if target_types == TYPE_FILTERS["default"]:
            return "**متن + لینک**"
        if target_types == TYPE_FILTERS["media"]:
            return "**رسانه‌ها** (عکس/ویدیو/فایل)"

        names = {
            "txt":  "متن",
            "link": "لینک",
            "pic":  "عکس",
            "vid":  "ویدیو",
            "file": "فایل",
            "other": "سایر",
        }
        labels = [f"`{t}` ({names.get(t, t)})" for t in sorted(target_types)]
        return "، ".join(labels)


# ── Help Texts (در انتهای ماژول طبق قوانین) ─────────────────────────────────

help_text = (
    "• `clear` | پاک‌سازی پیش‌فرض (متن و لینک)\n"
    "• `clear all` | پاک‌سازی همه پیام‌ها\n"
    "• `clear media` | پاک‌سازی عکس، ویدیو و فایل\n"
    "• `clear pic` | فقط عکس‌ها\n"
    "• `clear vid` | فقط ویدیوها و GIF\n"
    "• `clear file` | فقط فایل‌های ضمیمه\n"
    "• `clear txt` | فقط متن‌های خالص\n"
    "• `clear link` | فقط پیام‌های حاوی لینک\n"
    "• `clear self` | فقط پیام‌های خودم\n"
    "• `clear bot` | فقط پیام‌های ربات‌ها\n"
)

help_extra = (
    "پاک‌سازی دستی پیام‌ها\n\n"
    "دستورات اصلی:\n"
    "• `clear` | پاک‌سازی پیش‌فرض شامل متن و لینک\n"
    "• `clear all` | پاک‌سازی همه پیام‌ها شامل استیکر، ویس و سایر\n"
    "• `clear media` | پاک‌سازی عکس، ویدیو و فایل بدون لینک\n\n"
    "فیلتر بر اساس نوع:\n"
    "• `clear pic` | فقط عکس‌ها\n"
    "• `clear vid` | فقط ویدیوها و GIF\n"
    "• `clear file` | فقط فایل‌های ضمیمه\n"
    "• `clear txt` | فقط متن خالص بدون لینک\n"
    "• `clear link` | فقط پیام‌های حاوی لینک یا WebPage\n\n"
    "فیلتر بر اساس فرستنده:\n"
    "• `clear self` | فقط پیام‌های خودتان\n"
    "• `clear bot` | فقط پیام‌های ربات‌ها\n\n"
    "ترکیب دستور و scope:\n"
    "• `clear txt self` | فقط متن‌های خودم\n"
    "• `clear media bot` | فقط رسانه‌های ربات‌ها\n"
    "• `clear all self` | همه پیام‌های خودم\n\n"
    "ترکیب چند نوع با هم:\n"
    "• `clear txt link` | متن‌ها و لینک‌ها با هم\n"
    "• `clear pic vid` | عکس‌ها و ویدیوها با هم\n"
    "• `clear media link self` | رسانه + لینک، فقط پیام‌های خودم\n\n"
    "مثال‌ها:\n"
    "• `clear` | حذف همه پیام‌های متنی و لینک‌ها\n"
    "• `clear media` | حذف عکس‌ها، ویدیوها و فایل‌ها بدون لینک\n"
    "• `clear self pic` | فقط عکس‌هایی که خودم فرستادم\n"
    "• `clear bot txt` | فقط متن‌های ربات‌ها\n\n"
    "سیستم طبقه‌بندی:\n"
    "هر پیام فقط یک نوع دارد بر اساس اولویت زیر:\n"
    "`file > vid > pic > link > txt > other`\n\n"
    "نکات مهم:\n"
    "• `clear media` شامل `link` نمی‌شود\n"
    "• تایپ `link` شامل دکمه‌های شیشه‌ای با لینک هم می‌شود\n"
    "• پیام‌های استیکر، ویس و سایر فقط با `clear all` حذف می‌شوند\n"
    "• آرگومان‌های نامعتبر باعث نادیده گرفته شدن کامل دستور می‌شوند\n"
    "• حداکثر ۲۰۰۰ پیام اسکن می‌شود\n"
)

Clearer.help_text = help_text
Clearer.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return Clearer(context)
