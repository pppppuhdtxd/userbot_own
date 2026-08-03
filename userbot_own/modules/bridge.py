"""
userbot_own/modules/bridge.py
════════════════════════════════════════════════════════════════
MockEvent — the "Simple Bridge layer" between reaction-triggered
commands and the normal Telethon NewMessage event handlers.

This was previously a private class (`_MockEvent`) defined inline in
reaction_commands.py. It is lifted here unchanged in behavior because
it is a genuine bridge/adapter: it makes a reaction trigger look
enough like a real Telethon `NewMessage.Event` that the *exact same*
handler methods in clearer.py, join_left.py, info_handler.py, and
whois_handler.py can be invoked directly — no send_message() round
trip through the event loop, no duplicate command-parsing logic.

Compatibility surface (unchanged from the original):
- clearer.py     → event.raw_text, event.message.id, event.edit()/delete()
- join_left.py   → event.is_reply, get_reply_message(), event.edit() for
                   live progress updates, event.message.message for
                   entity extraction
- info_handler   → get_reply_message()
- whois_handler  → get_reply_message(), get_chat()

Design decisions (unchanged from the original _MockEvent):
- When `target_msg` is provided, `is_reply` is True and
  `get_reply_message()` returns it (needed by join_left, info, whois).
- `edit()` sends a new progress message on its first call, then edits
  that same message on subsequent calls — mirrors how join_left uses
  repeated `event.edit()` calls for live progress updates.
- `message.message` / `message.text` are both set to `raw_text` so
  `join_left._collect_entities()` can extract entities from the
  command text exactly as it would from a real message.
- `delete()` removes the progress message, if one was ever created.

v3.0.8 fix: a real Telethon `NewMessage.Event` exposes `is_private` /
`is_group` / `is_channel` (proxied through to the underlying `Message`
via `ChatGetter`, itself resolved from `Message.peer_id`). `MockEvent`
never had these at all — no attributes, and no `__getattr__` fallback
the way the real event class has. `clearer.py`'s `_run_clear` reads
`event.is_private` unconditionally, so every reaction-triggered `clear`
command raised `AttributeError` inside the direct-invocation try block
in `reaction_commands.py`, which silently fell back to `send_message`
every single time — never actually exercising the "Direct Module
Invocation" path the architecture was built around for this, the most
commonly reaction-mapped command.

Fix: the three flags are now constructor parameters, supplied by the
caller (`reaction_commands.py`), which already classifies the chat
type once per reaction event as part of its own scope-filtering gate
(Gate 2) — so populating these costs no additional API calls beyond
what the caller was already doing for its own purposes; MockEvent
itself performs no classification and makes no network calls.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.tl.types import Message

_log = logging.getLogger(__name__)


@dataclass(slots=True)
class _MockMessage:
    """Stand-in for the parts of a real Message that handlers read directly."""
    id: int
    text: str
    message: str


class MockEvent:
    """
    Synthesizes a Telethon-NewMessage-event-shaped object around a command
    string, so existing command handlers can be invoked directly (e.g. from
    a reaction trigger) without going through a real incoming/outgoing
    message and without duplicating each handler's parsing logic.
    """

    def __init__(
        self,
        client: TelegramClient,
        chat_id: int,
        target_msg_id: int,
        raw_text: str,
        target_msg: Message | None = None,
        is_private: bool = True,
        is_group: bool = False,
        is_channel: bool = False,
    ) -> None:
        self.client = client
        self.chat_id = chat_id
        self.raw_text = raw_text
        self._target_msg_id = target_msg_id
        self._target_msg = target_msg
        self.is_reply = target_msg is not None

        # v3.0.8: chat-type flags, mirroring the real NewMessage.Event /
        # ChatGetter surface. Supplied by the caller (already classified
        # for its own scope-filtering purposes — see module docstring),
        # never computed here. Defaults lean toward "private", the
        # least-consequential guess if a future call site ever omits
        # these (clearer.py's `clear` — without scope filters — behaves
        # the same for private chats regardless of self/bot).
        self.is_private = is_private
        self.is_group = is_group
        self.is_channel = is_channel

        # Needed by join_left._collect_entities(), which reads
        # event.message.message for command-text entities.
        #
        # Note (v3.0.9): id is intentionally a placeholder, not the real
        # target_msg_id. clearer.py's _run_clear reads event.message.id as
        # `command_id` and excludes it via `skip_ids` — using the real
        # target_msg_id here would make the reacted-to message immune to
        # its own reaction-triggered `clear`. That's not desired: if you
        # react with an emoji mapped to e.g. `clear txt` on a text message,
        # that message should be deletable like any other match. A prior
        # revision of this file set id=target_msg_id specifically to
        # protect the reacted message from deletion; per product decision
        # this has been reverted, since it wasn't a bug — matching the
        # active filter is the intended, expected outcome.
        self.message = _MockMessage(id=0, text=raw_text, message=raw_text)

        # Progress message — created on first edit(), reused afterward.
        self._progress_msg = None

    async def edit(self, text: str, **kwargs):
        """
        First call: send a new message as a reply to the target message.
        Subsequent calls: edit that same progress message.
        Returns the message object (which itself has a working .edit()).
        """
        try:
            if self._progress_msg is None:
                self._progress_msg = await self.client.send_message(
                    self.chat_id, text, reply_to=self._target_msg_id, **kwargs
                )
                return self._progress_msg
            await self._progress_msg.edit(text, **kwargs)
            return self._progress_msg
        except Exception as exc:
            _log.warning("MockEvent.edit failed: %s", exc)
            return self._progress_msg

    async def delete(self) -> None:
        """Delete the progress message, if one exists."""
        if self._progress_msg:
            try:
                await self._progress_msg.delete()
            except Exception:
                pass

    async def respond(self, text: str, **kwargs):
        """Send a response message."""
        try:
            return await self.client.send_message(self.chat_id, text, **kwargs)
        except Exception as exc:
            _log.warning("MockEvent.respond failed: %s", exc)
            return None

    async def get_reply_message(self):
        """Return the target message (the one that was reacted to)."""
        return self._target_msg

    async def get_chat(self):
        """Return the chat entity."""
        try:
            return await self.client.get_entity(self.chat_id)
        except Exception:
            return None


__all__ = ["MockEvent"]