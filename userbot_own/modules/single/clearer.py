"""
userbot_own/modules/single/clearer.py
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

v3.1.8 Changes (G6, G7 — observability only, no filtering logic changed):
- G6: When a scan stops exactly at `history_limit` messages, the report
  (both the "no matches" and the final success/partial report) now adds
  an explicit note that older messages may remain beyond the scanned
  window. `history_limit` itself is completely unchanged — still 2000 by
  default, still applied identically to every chat type — this is purely
  a transparency addition so "scanned == history_limit" is never silently
  read as "reached the true end of chat history".
- G7: The scan-start status message and a debug log line now show
  `is_private`, `use_from_user`, and `scope` for every `clear` run. This
  makes the exact filtering decision visible for both directly-typed
  commands and reaction-triggered commands (the latter route through
  `reaction_commands.py`'s `MockEvent` bridge — see that module's own
  v3.1.8 changes for the corresponding instrumentation on that side).
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import time

from telethon import TelegramClient, events
from telethon.tl.types import User

from userbot_own.core.context import ModuleContext
from userbot_own.helpers.utils import (
    FLOOD_ERRORS,
    MEDIA_TYPES,
    MESSAGE_TYPES,
    batch_delete,
    classify_message,
    flood_seconds,
)
from userbot_own.modules._infra.base import Module
from userbot_own.modules.single.clearer_info import (
    category,
    desc,
    help_extra,
    help_text,
    name,
)

# ── Type filters ─────────────────────────────────────────────────────────────

#: Maps command argument → set of classified types to delete.
#: `clear` (no argument) is equivalent to `clear txt link`.
#: `clear media` = pic + vid + file (does NOT include `link`).
#:
#: v3.2.2 (B-14): the "media" and "all" composites are now derived from the
#: canonical type lists in helpers.utils instead of being hand-written
#: copies. The old literals were a fifth and sixth place a message type had
#: to be added to whenever classify_message() learned a new one — a new type
#: would have silently been missing from `clear all`.
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
    "media": set(MEDIA_TYPES),   # Real media only — no `link`
    "all":   set(MESSAGE_TYPES),
}


# ── Valid command arguments ──────────────────────────────────────────────────

#: Any argument not in this set causes the command to be silently ignored.
#:
#: v3.2.3: this was derived from ``FILTER_TYPES`` (the list of message
#: TYPES), which is the wrong source. ``all`` is a KEY of
#: ``TYPE_FILTERS`` — a composite filter — not a message type, so it was
#: missing from the set and `clear all` was rejected by the strict
#: validation and silently did nothing. The set is now derived from the
#: keys of ``TYPE_FILTERS`` (the things a user can actually type, minus
#: the internal ``default`` alias) plus the two scope filters, so it can
#: never drift from the filter table again.
VALID_ARGS: frozenset[str] = frozenset(
    (set(TYPE_FILTERS) - {"default"}) | {
        # Scope filters
        "self", "bot",
    }
)


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
        except FLOOD_ERRORS as exc:
            # v3.0.11: previously _run_clear swallowed this internally and
            # reported partial scan results as if they were complete. Now
            # it re-raises past here, matching whois_handler.py's pattern,
            # so the user is told the scan stopped instead of getting a
            # silently-wrong "done" report.
            wait_msg = (
                f"⏳ درخواست بیش از حد — لطفاً {exc.seconds} ثانیه صبر کنید.\n"
                f"اسکن متوقف شد؛ ممکن است پیام‌هایی بررسی نشده باشند."
            )
            self._log_warning("Clear FloodWait %ds.", flood_seconds(exc))
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

        # v3.1.8 (G7): Observability instrumentation. Logs the exact
        # classification values that decide filtering behavior at the
        # start of every scan. This is purely diagnostic — it does not change
        # any filtering logic. If deletion is ever unexpectedly limited
        # again (e.g. a reaction-triggered `clear` in a bot chat), these
        # values make the cause immediately apparent instead of requiring
        # guesswork: is_private=False when it should be True would point
        # straight at a chat-type misclassification; use_from_user=True in a
        # chat that should allow full deletion would point straight at the
        # scope logic; both being correct would point elsewhere entirely
        # (e.g. an emoji mapped to a more restrictive scope than expected).
        #
        # v3.2.2 (B-07): these values used to be rendered into the
        # scan-start status message as well ("debug: private=…,
        # from_user_filter=…"), which put internal implementation detail in
        # front of every user in every group where `clear` ran. They are
        # LOG-ONLY now — the diagnostic value is identical (the log line was
        # always there), and the chat message is clean again.
        self._log_debug(
            "[Account%d] _run_clear start: chat_id=%s, is_private=%s, "
            "use_from_user=%s, scope=%s, history_limit=%d",
            self.cfg.index, chat_id, is_private, use_from_user, scope, history_limit,
        )

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

        except FLOOD_ERRORS:
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
            # v3.1.8 (G6): Report transparency when the scan stopped exactly
            # at history_limit. iter_messages(limit=history_limit) silently
            # stops yielding once `limit` messages have been returned —
            # this is indistinguishable from "reached the true end of chat
            # history" unless we explicitly check scanned == history_limit.
            # Without this note, a user could believe a scan that hit the
            # cap was complete when older messages may still remain beyond
            # the scanned window. history_limit itself is unchanged — this
            # is observability only, applying equally to all chat types.
            limit_note = (
                "\n• ⚠️ اسکن به دلیل محدودیت تعداد متوقف شد — "
                "ممکن است پیام‌های قدیمی‌تری باقی مانده باشند"
                if scanned >= history_limit else ""
            )
            await self._safe_edit(
                status_msg,
                f"ℹ️ **پیامی یافت نشد**\n"
                f"• اسکن شده: `{scanned}` پیام\n"
                f"• نوع: {type_label}\n"
                f"• زمان: `{elapsed:.2f}s`{note}{limit_note}"
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
        # v3.2.2 (B-16): iterate the canonical MESSAGE_TYPES list instead of
        # a hand-written copy of it (a sixth duplicate of the same list).
        breakdown_lines = []
        for t in MESSAGE_TYPES:
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
        # v3.1.8 (G6): same history_limit-hit transparency note as the
        # no-matches path above. history_limit stays unchanged for every
        # chat type — this only reports whether the scan happened to hit
        # that existing cap, so old messages beyond the scanned window
        # aren't silently assumed to be fully cleared.
        if scanned >= history_limit:
            report_lines.append(
                "• ⚠️ اسکن به دلیل محدودیت تعداد متوقف شد — "
                "ممکن است پیام‌های قدیمی‌تری باقی مانده باشند"
            )
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

        Scope "all": returns True unconditionally — no sender check is needed
        here. In non-private chats `from_user='me'` was already applied at the
        iter_messages level (server-side); in private chats the whole chat is
        in scope.

        Scope "self": the sender is ALWAYS re-checked client-side against
        base._get_me_id() (a per-client cache — a dict lookup, not an API
        call), whatever the chat type. In non-private chats that is redundant
        with the server-side `from_user='me'` filter, and it is deliberately
        left that way: a wrong `is_private` flag (for example on a MockEvent
        built for a reaction command) therefore cannot widen "clear self" to
        other people's messages. (v3.1.10: this docstring used to say the
        check only happened for private chats; the code never worked that
        way.)

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





# ── Metadata ───────────────────────────────────────────────────────────────
#
# help_handler.py and core/loader.py both read these off the *instance*
# (getattr(instance, "help_text", "")), so they must be class attributes.
# The values live in the sibling info file so they can be edited without
# touching any logic.
Clearer.name = name
Clearer.category = category
Clearer.desc = desc
Clearer.help_text = help_text
Clearer.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return Clearer(context)
