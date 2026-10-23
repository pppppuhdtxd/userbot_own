"""
userbot_own/modules_extra/packages/bot_link_starter/handlers.py
════════════════════════════════════════════════════════════════
The BotLinkStarter module class — the command and the start loop.

Reads the links out of the quoted part of the message, then starts each
bot in order, reporting the result back on the command message.

Getting at the links is the hard part, and it is deliberately kept in
one place (:meth:`BotLinkStarter._collect_links`) because Telegram
delivers a quote in more than one shape:

  * ``quote_text`` AND ``quote_entities`` — the complete quote, straight
    from the reply header.
  * ``quote_text`` only, entities missing — the text is there but a link
    that lives *behind* that text (Telegram's "create link") has lost
    its target, because the target is stored in the entity and nowhere
    else.
  * ``quote_text`` missing entirely — the whole message was quoted, so
    the quote carries no text and the replied message is the source.

Only the first shape can be read from the header alone. The other two
need the replied message, and the module says which source it used, so
the choice is never invisible to the user.
"""
from __future__ import annotations

import asyncio

from telethon import TelegramClient, events

from userbot_own.core.context import ModuleContext
from userbot_own.helpers.utils import FLOOD_ERRORS, flood_seconds
from userbot_own.modules._infra.base import Module
from userbot_own.modules_extra.packages.bot_link_starter.links import (
    BotLink,
    find_bot_links,
    find_bot_links_by_labels,
    find_bot_links_in_range,
    locate_quote,
)

#: The command that triggers the module.
_COMMAND = "start"

#: Seconds to wait between two `start` messages. Telegram rate-limits
#: consecutive messages to bots, so a short pause keeps the whole batch
#: from being flagged.
DEFAULT_DELAY = 3.0

#: How long to wait for a bot to answer before moving on. A bot that
#: sends a file may take a moment; a bot that never answers should not
#: hold up the rest of the batch. This only costs a timer — the answer
#: arrives as an event, not by polling — so it can afford to be
#: generous.
DEFAULT_TIMEOUT = 30.0

#: Outcome of one start.
_ANSWERED = "answered"   # the bot sent something back
_SILENT = "silent"       # the start was delivered, the bot said nothing
_FAILED = "failed"       # the start could not be delivered at all

#: Where the links came from.
_SRC_QUOTE = "quote"            # the quoted part, read from its own entities
_SRC_QUOTE_ORIGIN = "origin"    # the quoted RANGE of the original message
_SRC_FULL = "full"              # the whole message being replied to


class BotLinkStarter(Module):
    """Start the bot links inside a Quote & Reply, one after another."""

    name = "bot_link_starter"
    category = "general"
    desc = "استارت خودکار لینک‌های بات"
    _auto_delete_default_delay = 30.0

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        #: Seconds between two start messages.
        self.delay: float = DEFAULT_DELAY
        #: How long to wait for a bot to answer.
        self.timeout: float = DEFAULT_TIMEOUT
        #: Chats with a start run in progress, so a second `start` in the
        #: same chat cannot interleave with the first.
        self._active: set[int] = set()
        #: bot user id -> future resolved when that bot answers. Keyed by
        #: the bot's own id (not its username), because one bot can
        #: legitimately be started several times in a single run.
        self._pending: dict[int, asyncio.Future] = {}

    def setup(self, client: TelegramClient) -> None:
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_command)
        self._add_handler(client, events.NewMessage(incoming=True), self._on_bot_message)
        self._log_info("BotLinkStarter ready.")

    def teardown(self, client: TelegramClient) -> None:
        self._active.clear()
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        super().teardown(client)

    # ── Command ───────────────────────────────────────────────────────────

    async def _on_command(self, event) -> None:
        text = (event.raw_text or "").strip().lower()
        if text not in (_COMMAND, f"/{_COMMAND}"):
            return

        # Only in the owner's own Saved Messages, like the other owner
        # commands in this project.
        if not await self._is_saved_messages(event):
            return

        if event.chat_id in self._active:
            await self._safe_edit(
                event,
                "⏳ یک اجرای `start` دیگر در همین چت در حال انجام است — لطفاً صبر کنید.",
            )
            return

        reply_to = getattr(event.message, "reply_to", None)
        if reply_to is None:
            await self._safe_edit(
                event,
                "❌ این دستور فقط با **Quote & Reply** کار می‌کند.\n\n"
                "بخشی از پیام حاوی لینک‌ها را Quote & Reply کنید و سپس `start` را بفرستید.",
            )
            return

        bot_links, source = await self._collect_links(event, reply_to)
        if not bot_links:
            await self._safe_edit(event, self._no_links_message(event, reply_to, source))
            return

        self._active.add(event.chat_id)
        try:
            await self._run_starts(event, bot_links, source)
        finally:
            self._active.discard(event.chat_id)

    def _no_links_message(self, event, reply_to, source: str) -> str:
        """Explain WHY nothing was found — the reasons differ."""
        if not getattr(reply_to, "quote_text", None):
            return (
                "❌ در پیامی که به آن Reply کرده‌اید هیچ لینک باتِ تلگرامی پیدا نشد.\n\n"
                "لینک‌هایی مثل `https://t.me/MyBot?start=ref123` پشتیبانی می‌شوند."
            )

        return (
            "❌ در بخش Quote‌شده هیچ لینک باتِ تلگرامی پیدا نشد.\n\n"
            "لینک‌هایی مثل `https://t.me/MyBot?start=ref123` پشتیبانی می‌شوند."
        )

    async def _collect_links(self, event, reply_to) -> tuple[list[BotLink], str]:
        """
        Find the bot links the command is about.

        Returns ``(links, source)``, where *source* is one of
        :data:`_SRC_QUOTE`, :data:`_SRC_QUOTE_ORIGIN` or
        :data:`_SRC_FULL`.

        The quoted part is preferred — that is the point of Quote &
        Reply, and it lets the user pick three lines out of a message
        holding thirty. But a quote is only *readable* when it arrives
        with its entities, and Telegram does not always send them. When
        the quote's text contains no URLs of its own and its entities
        are missing, every link in it is a "create link" whose target
        was left behind — there is genuinely nothing there to start, and
        widening to the whole message is the only way to honour the
        request. That case is reported to the user rather than silently
        guessed at, and it is the one case where the selection is
        widened.
        """
        quote_text = getattr(reply_to, "quote_text", None)
        quote_entities = getattr(reply_to, "quote_entities", None)

        if quote_text:
            # 1. A quote that carried its own entities is fully readable.
            links = find_bot_links(quote_text, quote_entities)
            if links:
                return links, _SRC_QUOTE

            # 2. The quote's text alone is not enough. A link created with
            #    Telegram's "create link" stores its target ONLY in the
            #    entity — the quoted words look like ordinary text — and
            #    the server does not store quote_entities at all (verified
            #    against the live API). The original message always has
            #    them, so its entities are matched to the quote.
            links = await self._links_from_replied(
                event, quote_text, getattr(reply_to, "quote_offset", None),
            )
            if links:
                return links, _SRC_QUOTE_ORIGIN

            return [], _SRC_QUOTE

        # 3. No quoted text at all — the whole message was quoted.
        return await self._links_from_replied(event), _SRC_FULL

    async def _fetch_replied(self, event):
        """
        Fetch the message the command replied to, from whichever chat it
        actually lives in.

        ``event.get_reply_message()`` only ever looks in the **current**
        chat. A quote can point somewhere else entirely: reply in Saved
        Messages to a message that was forwarded from a channel and the
        header carries ``reply_to_peer_id = PeerChannel(...)``, so the
        lookup runs against Saved Messages, finds nothing, and returns
        ``None`` — even though the message is perfectly readable at the
        peer the header names.

        That is the normal case for this module: the user copies a post
        into Saved Messages, quotes it, and the reply header points at
        the original channel message. Reading only the current chat made
        the module find nothing on exactly those messages.

        So: try the normal path first; if it yields nothing, follow
        ``reply_to_peer_id`` and fetch the message there.
        """
        try:
            replied = await event.get_reply_message()
        except Exception as exc:
            self._log_debug("get_reply_message failed: %s", exc)
            replied = None

        if replied is not None:
            return replied

        message = getattr(event, "message", None)
        reply_to = getattr(message, "reply_to", None)
        peer = getattr(reply_to, "reply_to_peer_id", None)
        msg_id = getattr(reply_to, "reply_to_msg_id", None)
        if peer is None or msg_id is None:
            return None

        # get_messages accepts the raw peer, but resolving it first turns a
        # bare channel id into a real entity and warms the cache — and the
        # two exceptions are worth telling apart in the log.
        try:
            entity = await event.client.get_entity(peer)
        except Exception as exc:
            self._log_debug("Could not resolve reply peer %r: %s", peer, exc)
            entity = peer

        try:
            fetched = await event.client.get_messages(entity, ids=msg_id)
        except Exception as exc:
            self._log_debug("Could not fetch the replied message from %r: %s", peer, exc)
            return None

        if isinstance(fetched, list):
            fetched = fetched[0] if fetched else None
        if fetched is None:
            self._log_debug("Replied message %s not found at %r.", msg_id, peer)
        return fetched

    async def _links_from_replied(
        self,
        event,
        quote_text: str | None = None,
        quote_offset: int | None = None,
    ) -> list[BotLink]:
        """
        Read the bot links out of the message this command replied to.

        With no *quote_text*, the whole message is read (the whole-message
        quote case). With one, only the part of the message the quote
        covers is read — located by matching the quoted text inside the
        original, and falling back to ``quote_offset``.
        """
        replied = await self._fetch_replied(event)
        if replied is None:
            return []

        if quote_text is None:
            return find_bot_links_in_range(replied.message, replied.entities, None)

        span = locate_quote(quote_text, quote_offset, replied.message)
        if span is not None:
            links = find_bot_links_in_range(replied.message, replied.entities, span)
            if links:
                return links

        # The quote could not be located as a contiguous stretch — which
        # is the normal case on real messages, because clients rewrite
        # whitespace and the server drops quote_entities. Fall back to
        # matching by what the quote SAYS: an entity whose visible text
        # (e.g. "Chapter 557") is named by a quoted line belongs to the
        # selection.
        #
        # This still never widens to the whole message: an entity that
        # the quote does not name is not started.
        self._log_debug(
            "Quote not locatable as a range; matching entities by their text.",
        )
        return find_bot_links_by_labels(replied.message, replied.entities, quote_text)

    # ── The start loop ─────────────────────────────────────────────────────

    async def _run_starts(self, event, bot_links: list[BotLink], source: str) -> None:
        """Start every bot in order, reporting as we go."""
        total = len(bot_links)
        source_label = {
            _SRC_QUOTE: "بخش Quote‌شده",
            _SRC_QUOTE_ORIGIN: "بخش Quote‌شده (از روی پیام اصلی)",
            _SRC_FULL: "کل پیام Reply‌شده",
        }[source]
        await self._safe_edit(
            event,
            f"🚀 شروع {total} لینک از {source_label}…\n"
            f"فاصلهٔ بین هر استارت: `{self.delay:g}` ثانیه",
        )

        answered: list[str] = []
        silent: list[str] = []
        failed: list[str] = []

        for index, bot_link in enumerate(bot_links, 1):
            label = f"`@{bot_link.username}`"
            await self._safe_edit(
                event,
                f"🚀 شروع {total} لینک…\n"
                f"**({index}/{total})** در حال استارت {label}…",
            )

            outcome, error = await self._start_one(event.client, bot_link)
            if outcome == _ANSWERED:
                answered.append(bot_link.username)
            elif outcome == _SILENT:
                silent.append(bot_link.username)
            else:
                failed.append(f"{bot_link.username} ({error})")

            # Pause before the next bot, but not after the last one.
            if index < total and self.delay > 0:
                await asyncio.sleep(self.delay)

        await self._report(event, answered, silent, failed, total)

    async def _start_one(self, client: TelegramClient, bot_link: BotLink) -> tuple[str, str]:
        """
        Send the start command to one bot.

        Returns ``(outcome, error)`` — one of :data:`_ANSWERED`,
        :data:`_SILENT`, or :data:`_FAILED`.
        """
        try:
            entity = await client.get_entity(bot_link.username)
        except Exception as exc:
            return _FAILED, f"resolve failed: {exc}"

        # A bare "t.me/name" is ambiguous until resolved: it may be a bot,
        # a user, or a CHANNEL. Only a bot can be started. Sending
        # "/start" into a channel would post that text into a channel the
        # account owns or administers — a real, visible side effect, and
        # not something a link in a message should ever cause.
        from telethon.tl.types import Channel as ChannelEntity

        if isinstance(entity, ChannelEntity) and entity.broadcast:
            return _FAILED, "not a bot (channel)"
        if not getattr(entity, "bot", False) and not getattr(entity, "username", None):
            # A user account with no username at all — nothing to start.
            return _FAILED, "not a bot"

        # Register the answer slot BEFORE sending, so a fast bot cannot
        # reply in the gap between "sent" and "listening" and be missed.
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[entity.id] = future

        try:
            # The payload must arrive EXACTLY as it was in the link, so
            # the default markdown parsing is turned off.
            try:
                await client.send_message(entity, bot_link.command, parse_mode=None)
            except FLOOD_ERRORS as exc:
                wait = flood_seconds(exc)
                self._log_warning(
                    "[Account%d] FloodWait %ds while starting @%s.",
                    self.cfg.index, wait, bot_link.username,
                )
                return _FAILED, f"flood wait: {wait}s"
            except Exception as exc:
                return _FAILED, str(exc)

            try:
                await asyncio.wait_for(future, timeout=self.timeout)
            except TimeoutError:
                return _SILENT, "no answer"
            return _ANSWERED, ""
        finally:
            # Drop the slot whether we answered, timed out, or failed —
            # otherwise a later message from the same bot could resolve a
            # future nobody is waiting on any more.
            if self._pending.get(entity.id) is future:
                del self._pending[entity.id]

    # ── Answer detection ──────────────────────────────────────────────────

    async def _on_bot_message(self, event) -> None:
        """
        Wake whoever is waiting for this bot.

        A bot answering our ``/start`` sends a message; this handler
        turns that into the future's result. Doing it this way — rather
        than polling ``iter_messages`` in a loop — means a start costs
        one API call instead of one every half second, and the answer is
        noticed the instant it arrives.
        """
        # The cheap check first: with no run in progress this fires on
        # every incoming message on the account, and must cost nothing.
        if not self._pending:
            return

        sender_id = getattr(event, "sender_id", None)
        if sender_id is None:
            return

        future = self._pending.get(sender_id)
        if future is not None and not future.done():
            future.set_result(event)

    # ── Reporting ─────────────────────────────────────────────────────────

    async def _report(
        self,
        event,
        answered: list[str],
        silent: list[str],
        failed: list[str],
        total: int,
    ) -> None:
        """Write the final report on the command message."""
        lines = ["**📋 نتیجهٔ استارت لینک‌ها**\n"]

        if answered:
            lines.append(f"✅ پاسخ گرفت ({len(answered)}):")
            lines.extend(f"  • `@{name}`" for name in answered)
        else:
            lines.append("✅ پاسخ گرفت: هیچ")

        if silent:
            lines.append(f"\n⚠️ ارسال شد، پاسخی نیامد ({len(silent)}):")
            lines.extend(f"  • `@{name}`" for name in silent)

        if failed:
            lines.append(f"\n❌ ناموفق ({len(failed)}):")
            lines.extend(f"  • {item}" for item in failed)

        if not silent and not failed:
            lines.append("\n⚠️ ارسال شد، پاسخی نیامد: هیچ")
            lines.append("❌ ناموفق: هیچ")

        sent = len(answered) + len(silent)
        lines.append(f"\nمجموع: `{sent}` از `{total}` لینک ارسال شد")

        await self._safe_edit(event, "\n".join(lines))
        self._log_info(
            "[Account%d] BotLinkStarter finished: %d/%d answered, %d silent, %d failed.",
            self.cfg.index, len(answered), total, len(silent), len(failed),
        )
