"""
userbot_own/modules/router.py
════════════════════════════════════════════════════════════════
CommandRouter — small, declarative first-token command dispatch.

Every module in the original codebase re-implemented the same shape
by hand: a set/frozenset of recognized command strings, a
`text.split()` + lowercase-first-token check, and a local dict
mapping command -> handler method (system.py's `_OWNER_COMMANDS` +
`dispatch` dict is the clearest example; help_handler.py does the
same thing with a single literal instead of a dict). This class
lifts that repeated shape into one place without changing what it
does: it is still exactly "lowercase the first whitespace-separated
token, look it up, call the handler" — no argument parsing, no
middleware pipeline, nothing the original modules didn't already do
by hand.

Deliberately NOT included, to avoid re-introducing complexity the
original code never had:
- No automatic "outgoing message" / "Saved Messages only" / "owner"
  gating. Different modules apply different gates (help_handler and
  system.py both restrict to Saved Messages; whois_handler works in
  any chat per the README) — that decision stays in each module,
  exactly as before, applied before or inside the handler it calls.
- No argument parsing beyond the first token. Modules that need to
  inspect the remaining tokens (help_handler's `help <module>`,
  clearer's `clear <type> <scope>`, etc.) still do that themselves,
  exactly as before.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

CommandHandler = Callable[..., Awaitable[None]]


class CommandRouter:
    """
    Maps lowercase first-token command strings to async handlers.

    Usage (mirrors the original system.py dispatch dict exactly, just
    built once in setup() instead of rebuilt on every message)::

        router = CommandRouter()
        router.register(".modules", self._cmd_modules)
        router.register(".account", self._cmd_account)
        ...

        async def _on_outgoing(self, event) -> None:
            if not await self._is_owner_saved(event):
                return
            await router.dispatch(event)
    """

    def __init__(self) -> None:
        self._routes: dict[str, CommandHandler] = {}

    def register(self, *commands: str, handler: CommandHandler | None = None) -> None:
        """
        Register *handler* for one or more command strings (case-insensitive).

        Supports both call styles used across the modules being migrated::

            router.register(".ping", handler=self._cmd_ping)
            router.register("clear", "clean", handler=self._cmd_clear)  # aliases
        """
        if handler is None:
            raise TypeError("register() requires a handler")
        for cmd in commands:
            self._routes[cmd.lower()] = handler

    @property
    def commands(self) -> frozenset[str]:
        """All registered command strings — handy for help-text generation."""
        return frozenset(self._routes)

    def resolve(self, text: str) -> tuple[CommandHandler | None, list[str]]:
        """
        Split *text* and look up a handler for its first token.

        Returns (handler_or_None, remaining_tokens). Never raises — callers
        that expect a command already check the return value.
        """
        parts = (text or "").strip().split()
        if not parts:
            return None, []
        return self._routes.get(parts[0].lower()), parts[1:]

    async def dispatch(self, event, text: str | None = None) -> bool:
        """
        Resolve and invoke the handler for *text* (default: `event.raw_text`).

        Returns True if a handler was found and called, False otherwise —
        callers that need to fall through to other logic when nothing
        matched (as several modules do) can check this.
        """
        raw = text if text is not None else (getattr(event, "raw_text", "") or "")
        handler, _ = self.resolve(raw)
        if handler is None:
            return False
        await handler(event)
        return True


__all__ = ["CommandRouter", "CommandHandler"]
