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

Chat-type-aware sender filtering (v3.0.6):
- In groups, supergroups and channels `iter_messages` is called with
  `from_user='me'` for scope=="all" and scope=="self", so only the user's
  own messages are fetched server-side. This is dramatically more efficient
  (no wasted bandwidth) and also correct — other members' messages are never
  collected, so the deletion count in the report is exact.
- In private chats (PeerUser) and Saved Messages `from_user` is omitted;
  Telegram's server ignores it there anyway (returns all messages). Telethon
  would locally filter if we passed it, but for private conversations we want
  to process messages from both sides.
- scope=="bot" never uses `from_user='me'` since bot messages belong to other
  senders, not the userbot account.

Bot-sender cache (v3.0.6):
- For scope=="bot", sender.bot is checked via a per-session
  `_bot_peer_cache: dict[int, bool]` keyed on `msg.sender_id`. This avoids
  the hidden `get_entity()` call that `msg.sender` previously triggered on
  every uncached message, which was a FloodWait risk at scale.

Permission handling:
- The module does NOT pre-check permissions. It attempts to delete every
  matching message via `batch_delete()`, which uses `AffectedMessages.pts_count`
  (v3.0.6) for exact deletion counting. Messages the server silently skips
  (e.g. other members' messages without admin rights) are now correctly
  reported as "not deleted" rather than being falsely counted as successes.

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

import time

from telethon import TelegramClient, errors, events
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
    category = "cleaning"
    desc = "پاک‌سازی دستی پیام‌ها"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        # Per-session cache: sender_id → is_bot (bool).
        # Populated lazily in _is_bot_sender() to avoid per-message
        # get_entity() calls (which were a hidden FloodWait risk).
        self._bot_peer_cache: dict[int, bool] = {}
        # v3.0.9: guards against two overlapping `clear` runs in the same
        # chat (e.g. a double-tap or a queued duplicate command) racing
        # each other — both scanning/deleting concurrently, overlapping
        # IDs, and producing a misleading second report.
        self._active_clears: set[int] = set()

    def setup(self, client: TelegramClient) -> None:
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_command)
        self._log_info("Clearer ready.")

    def teardown(self, client: TelegramClient) -> None:
        self._bot_peer_cache.clear()
        self._active_clears.clear()
        super().teardown(client)

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

        # v3.0.9 fix: reject a second `clear` in the same chat while one is
        # already running, instead of letting both scan/delete concurrently.
        if event.chat_id in self._active_clears:
            await self._safe_edit(
                event,
                "⏳ یک عملیات `clear` دیگر در همین چت در حال اجراست — لطفاً صبر کنید."
            )
            return

        self._active_clears.add(event.chat_id)
        try:
            await self._run_clear(client, event, target_types, scope)
        except errors.FloodWaitError as exc:
            # v3.0.11: previously _run_clear swallowed this internally and
            # reported partial scan results as if they were complete. Now
            # it re-raises past here, matching whois_handler.py's pattern,
            # so the user is told the scan stopped instead of getting a
            # silently-wrong "done" report.
            wait_msg = (
                f"⏳ درخواست بیش از حد — لطفاً {exc.seconds} ثانیه صبر کنید.\n"
                f"اسکن متوقف شد؛ ممکن است پیام‌هایی بررسی نشده باشند."
            )
            self._log_warning("Clear FloodWait %ds.", exc.seconds)
            await self._safe_edit(event, wait_msg)
        finally:
            self._active_clears.discard(event.chat_id)

    # ── Main clear logic ───────────────────────────────────────────────────

    async def _run_clear(
        self,
        client: TelegramClient,
        event,
        target_types: set[str],
        scope: str,
    ) -> None:
        """Scan chat history and delete messages matching the filter.

        Chat-type-aware sender filtering (v3.0.6)
        ──────────────────────────────────────────
        ``event.is_private`` is True when the chat is a PeerUser entity
        (private chat with a person or bot, or Saved Messages).

        - Private / Saved Messages → fetch all messages (no ``from_user``).
          In private chats Telegram ignores ``from_user`` server-side anyway;
          we also intentionally want both sides of the conversation for these.
        - Groups / supergroups / channels (``is_private`` is False) with
          scope "all" or "self" → add ``from_user='me'`` so Telegram filters
          server-side. Only the user's own messages are returned, making the
          scan efficient and the deletion count accurate.
        - scope "bot" → no ``from_user`` ever (we want bot messages, not ours).
        """
        chat_id = event.chat_id
        command_id = event.message.id
        history_limit = self.context.settings.history_limit

        # Determine if this is a private/saved-messages chat.
        # event.is_private is True for PeerUser (person, bot, Saved Messages).
        is_private: bool = bool(event.is_private)

        # Should we let Telegram filter by sender server-side?
        # Yes for non-private chats when we only want our own messages.
        use_from_user = (not is_private) and (scope in ("all", "self"))

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

        scan_interrupted = False
        try:
            # wait_time=1: adds a 1-second pause every 100 messages to avoid
            # hitting GetHistoryRequest FloodWait. Telethon only auto-applies
            # this when limit > 3000; at the default 2000 we add it explicitly.
            iter_kwargs: dict = dict(limit=history_limit, wait_time=1)
            if use_from_user:
                iter_kwargs["from_user"] = "me"

            async for msg in client.iter_messages(chat_id, **iter_kwargs):
                # Skip command and status message BEFORE counting
                if msg.id in skip_ids:
                    continue

                # Count as scanned
                scanned += 1

                # Scope filter (client-side for "bot"; server-side already
                # handled for "all"/"self" in non-private chats via from_user)
                if not await self._matches_scope(client, msg, scope):
                    continue

                # Type classification (uses shared classify_message helper)
                msg_type = classify_message(msg)
                if msg_type not in target_types:
                    continue

                to_delete.append(msg.id)
                matched_by_type[msg_type] = matched_by_type.get(msg_type, 0) + 1

        except errors.FloodWaitError:
            # v3.0.11 fix: this used to be caught by the broad `except
            # Exception` below and silently swallowed, so a FloodWait
            # partway through a scan produced a result reported as
            # complete when it was actually partial. Re-raising here
            # matches the pattern already used consistently in
            # whois_handler.py / info_handler.py — _on_command now has
            # a dedicated handler that tells the user the scan was
            # interrupted instead of presenting partial results as final.
            raise
        except Exception as exc:
            self._log_error("Scan error: %s", exc)
            scan_interrupted = True

        # No matches
        if not to_delete:
            elapsed = time.monotonic() - start_time
            note = (
                "\n• ⚠️ اسکن به‌دلیل خطا ناقص ماند — نتیجه ممکن است کامل نباشد"
                if scan_interrupted else ""
            )
            await self._safe_edit(
                status_msg,
                f"ℹ️ **پیامی یافت نشد**\n"
                f"• اسکن شده: `{scanned}` پیام\n"
                f"• نوع: {type_label}\n"
                f"• زمان: `{elapsed:.2f}s`{note}"
            )
            self._track_delete_task(status_msg, 6.0)
            return

        # Update status before deletion
        await self._safe_edit(
            status_msg,
            f"🗑 **در حال حذف `{len(to_delete)}` پیام...**\n"
            f"• اسکن شده: `{scanned}` پیام"
        )

        # Batch delete — uses AffectedMessages.pts_count (v3.0.6) for exact
        # deletion counting. Messages the server silently skipped (no permission)
        # are now correctly reflected in failed_count.
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
        if scan_interrupted:
            report_lines.append("• ⚠️ اسکن به‌دلیل خطا زودتر متوقف شد — ممکن است پیام‌های بیشتری باقی مانده باشند")
        report_lines.append(f"• زمان: `{elapsed:.2f}s`")
        report_lines.append("")
        report_lines.append("🏷 **بر اساس نوع:**")
        report_lines.append(breakdown)

        result_text = "\n".join(report_lines)

        # Try to edit status message; fall back to a new message if it was
        # deleted. Capture the fallback message so _track_delete_task targets
        # the correct object (v3.0.6 bug fix — previously the fallback message
        # was never auto-deleted because status_msg was passed instead).
        report_msg = status_msg
        try:
            await status_msg.edit(result_text)
        except Exception:
            try:
                report_msg = await client.send_message(chat_id, result_text)
            except Exception as exc:
                self._log_error("Failed to send result: %s", exc)

        # Auto-delete the result message after a short delay
        self._track_delete_task(report_msg, 6.0)

    # ── Helpers ─────────────────────────────────────────────────────────────

    async def _matches_scope(self, client: TelegramClient, msg, scope: str) -> bool:
        """Check if a message matches the requested scope (all/self/bot).

        For scope "all" and "self" in non-private chats, `from_user='me'`
        is already applied at the iter_messages level (server-side), so this
        method returns True unconditionally for those scopes — the filtering
        was already done. For private chats with scope "self", we fall back to
        a client-side sender_id check using base._get_me_id().

        For scope "bot", sender.bot is checked via _is_bot_sender() which uses
        a local cache (self._bot_peer_cache) to avoid a get_entity() call per
        message.
        """
        if scope == "all":
            return True

        if scope == "self":
            me_id = await self._get_me_id(client)
            if me_id is None:
                return False
            return getattr(msg, "sender_id", None) == me_id

        if scope == "bot":
            return await self._is_bot_sender(client, msg)

        return False

    async def _is_bot_sender(self, client: TelegramClient, msg) -> bool:
        """Return True if the message was sent by a bot.

        Uses self._bot_peer_cache (keyed on sender_id) to avoid a
        get_entity() call for every message. Only the first occurrence of a
        given sender_id triggers an API call; subsequent messages from the
        same sender use the cached result.

        Falls back to False on any error to avoid crashing the scan loop.
        """
        sender_id = getattr(msg, "sender_id", None)
        if sender_id is None:
            return False

        # Return cached result if available
        if sender_id in self._bot_peer_cache:
            return self._bot_peer_cache[sender_id]

        # Try msg.sender first — it may already be populated (no API call)
        try:
            sender = getattr(msg, "sender", None)
            if isinstance(sender, User):
                result = bool(getattr(sender, "bot", False))
                self._bot_peer_cache[sender_id] = result
                return result
        except Exception:
            pass

        # Fall back to an explicit get_entity() call (one per unique sender)
        try:
            entity = await client.get_entity(sender_id)
            result = isinstance(entity, User) and bool(getattr(entity, "bot", False))
            self._bot_peer_cache[sender_id] = result
            return result
        except Exception:
            self._bot_peer_cache[sender_id] = False
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
    "• `clear` | پاک‌سازی پیش‌فرض (متن و لینک) — فقط پیام‌های خودم در گروه‌ها\n"
    "• `clear all` | پاک‌سازی همه پیام‌های خودم\n"
    "• `clear media` | پاک‌سازی عکس، ویدیو و فایل (فقط پیام‌های خودم در گروه)\n"
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
    "• `clear all` | پاک‌سازی همه پیام‌های خودم (گروه) یا همه پیام‌ها (چت خصوصی)\n"
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
    "رفتار در گروه‌ها و کانال‌ها (v3.0.6):\n"
    "• در گروه، سوپرگروه و کانال: دستور `clear` (و همه حالت‌های آن به‌جز `clear bot`)\n"
    "  فقط پیام‌های خودتان را پیدا و حذف می‌کند. پیام‌های سایر اعضا نادیده گرفته می‌شوند.\n"
    "• در چت خصوصی و مکالمه با ربات: پیام‌های هر دو طرف پردازش می‌شوند.\n\n"
    "ترکیب دستور و scope:\n"
    "• `clear txt self` | فقط متن‌های خودم\n"
    "• `clear media bot` | فقط رسانه‌های ربات‌ها\n"
    "• `clear all self` | همه پیام‌های خودم\n\n"
    "ترکیب چند نوع با هم:\n"
    "• `clear txt link` | متن‌ها و لینک‌ها با هم\n"
    "• `clear pic vid` | عکس‌ها و ویدیوها با هم\n"
    "• `clear media link self` | رسانه + لینک، فقط پیام‌های خودم\n\n"
    "مثال‌ها:\n"
    "• `clear` | حذف متن و لینک‌های خودم\n"
    "• `clear media` | حذف عکس‌ها، ویدیوها و فایل‌های خودم در گروه\n"
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
