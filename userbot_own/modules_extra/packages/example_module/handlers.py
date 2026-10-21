"""
userbot_own/modules_extra/packages/example_module/handlers.py
════════════════════════════════════════════════════════════════
The ExampleModule class — the behaviour of the example module.

The smallest useful module: one command, one handler, one auto-delete.
Everything else about a module (metadata, the `create_module` factory)
lives in the sibling files.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from telethon import TelegramClient, events

from userbot_own.modules._infra.base import Module

# ── Constants ────────────────────────────────────────────────────────────────

_COMMAND = "پینگ اضافی"
_AUTO_DELETE_DELAY = 5.0  # seconds before the pong is removed from Saved Messages


# ── Module ───────────────────────────────────────────────────────────────────

class ExampleModule(Module):
    """Answers `پینگ اضافی` with a pong — the smallest useful extra module."""

    name = "example_module"
    category = "general"
    desc = "ماژول نمونه"
    _auto_delete_default_delay = _AUTO_DELETE_DELAY

    def setup(self, client: TelegramClient) -> None:
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_outgoing)
        self._log_info("ExampleModule ready.")

    # teardown() needs no override: Module.teardown() already removes the
    # handlers registered through _add_handler() and cancels any pending
    # auto-delete task, so the base implementation is sufficient as-is.

    async def _on_outgoing(self, event) -> None:
        # Match the WHOLE message (whitespace-normalised) instead of using
        # CommandRouter: the command is two words and the router only looks at
        # the first token, so the bare word "پینگ" (plain "ping") would trigger
        # this module too.
        if " ".join((event.raw_text or "").split()) != _COMMAND:
            return

        # Saved Messages only, like the other modules' owner commands.
        if not await self._is_saved_messages(event):
            return

        await self._safe_edit_with_auto_delete(event, "🏓 pong")
        self._log_debug("[Account%d] example_module: pong", self.cfg.index)
