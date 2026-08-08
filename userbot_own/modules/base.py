"""
userbot_own/modules/base.py
════════════════════════════════════════════════════════════════
Abstract base class for all userbot modules (plugins).

Every plugin file (e.g. `clearer.py`, `info_handler.py`) must define a
class that inherits from `Module` and exposes a factory function
`create_module(context)` at module level so the loader can instantiate it.

The base class provides:
- Constructor injection of a `ModuleContext` (cfg + application-scoped
  services) — see core/context.py. `self.cfg` remains available directly
  as a convenience since almost every module's body reads it, but the
  full context (`settings`, `loader_registry`, `account_registry`) is
  also available via `self.context` for the few modules that need it.
  (v3.0.13: corrected — this used to say "event bus, loader registry,
  plugin store", neither of which exist. There is no event bus anywhere
  in this codebase, and `PluginMetadataStore` was removed in v3.0.11 —
  see core/registry.py's docstring. `ModuleContext` only ever carries
  `cfg`, `settings`, `loader_registry`, `account_registry`.)
- Automatic handler registration with duplicate-prevention
- Per-module structured logging helpers
- Safe message editing (swallows MessageNotModified / NotFound)
- Cached self-ID retrieval
- Saved-Messages-only detection (`_is_saved_messages`)
- Tracked auto-delete-after-delay for status/progress messages
  (`_safe_edit_with_auto_delete`, `_track_delete_task`) — hot-reload-safe,
  cancelled automatically in `teardown()`
- Help text attributes (`help_text` for compact view, `help_extra` for
  detailed view via `help <module>` command)

Lifecycle
─────────
create_module(context)      → Module instance
instance.setup(client)      # register handlers, warm caches
[running…]
instance.teardown(client)   # remove handlers (hot-reload safe)

NOTE on `is_admin_only`: earlier versions of this project had a
per-module `is_admin_only` flag used to hide admin-restricted modules
from non-owner accounts. It was deliberately and completely removed
(see CHANGELOG) once the project moved to a single-owner,
all-accounts-equal model — it is intentionally absent here too. If
you're comparing against very old copies of the README's "Writing a
New Module" section, ignore the `is_admin_only` line; that section
was stale documentation and has been corrected as part of this
refactor.

NOTE on `_is_saved_messages` / `_safe_edit_with_auto_delete` /
`_track_delete_task`: these were extracted in version 3.0.1 from
duplicated per-module implementations. `_is_saved_messages` unifies
what `help_handler.py`, `join_left.py` (×3), and `reaction_commands.py`
each did inline, and what `system.py`'s `_is_owner_saved` and
`auto_clearer.py` / `auto_forwarder.py`'s separate `self._me_id`
caching each did with slightly different mechanics — all five now
share the one cached lookup via `_get_me_id`. The auto-delete trio
unifies what `system.py` (`_schedule_delete` / `_track_task` /
`_edit_and_auto_delete`, backed by a `list`) and `join_left.py`
(`_auto_delete_after_delay` / `_track_delete_task` /
`_safe_edit_with_auto_delete`, backed by a `set`) each implemented
separately — both call sites now use `join_left`'s naming and the
`set`-based tracking (marginally simpler: `set.discard` is safe to
call unconditionally, unlike the list version's membership-checked
`remove`). Each module's own auto-delete *delay value* (5s for
`join_left`, 8s for `system`) is unchanged — only the mechanics moved,
via a per-subclass `_auto_delete_default_delay` class attribute that
`_track_delete_task` / `_safe_edit_with_auto_delete` fall back to when
called without an explicit `delay` (both modules' many call sites
already omitted it, relying on what was previously a method-default
argument — that stays true now, just resolved from a class attribute
instead so the two modules can keep their own different timing without
either one needing to touch its call sites).
not the timing.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any
from weakref import WeakKeyDictionary

from telethon import TelegramClient, errors
from telethon.events.common import EventBuilder

from userbot_own.core.context import ModuleContext
from userbot_own.core.logging_setup import get_logger


class Module:
    """
    Abstract base for all userbot plugins.

    Subclasses must set at minimum:
        name      — short identifier (used in logs, and as the key the
                    loader tracks this module under)
        help_text — short Persian help string shown in the compact ``help``
                    output. Keep it to ~3-5 lines max.

    Optional:
        help_extra — extended Persian help shown via ``help <module>``.
                     Include examples, detailed explanations, edge cases.
        category   — which group this module's help_text is shown under
                     in the compact ``help`` output (see
                     modules/help_handler.py's ``CATEGORIES`` list for the
                     valid keys: cleaning, forward, info, social, reaction,
                     system, general). Defaults to ``"general"``.
                     (v3.0.13: previously the help system tracked
                     category/description in a hardcoded external dict,
                     `MODULE_MAP`, in help_handler.py — a new module was
                     invisible in `help` and `help <module>` unless a
                     human remembered to add a matching entry there too,
                     even though the module itself loaded and ran
                     perfectly fine. `category`/`desc` living directly on
                     the module, read the same way `help_text`/
                     `help_extra` always have been, removes that manual
                     step entirely — see CHANGELOG v3.0.13.)
        desc       — short Persian one-line description shown next to
                     this module's name in the compact ``help`` listing
                     (distinct from `help_text`, which is the module's own
                     command summary). Defaults to ``""``.
    """

    # ── Public attributes (override in subclasses) ───────────────────────────
    name: str = ""
    help_text: str = ""
    help_extra: str = ""
    category: str = "general"
    desc: str = ""

    #: Default delay (seconds) for _track_delete_task / _safe_edit_with_auto_delete
    #: when called without an explicit delay. Override per-subclass — e.g.
    #: system.py uses 8.0, join_left.py uses 5.0.
    _auto_delete_default_delay: float = 5.0

    # ── Private state ────────────────────────────────────────────────────────
    def __init__(self, context: ModuleContext) -> None:
        self.context: ModuleContext = context
        self.cfg = context.cfg  # convenience alias — used throughout every module
        self._log: logging.Logger = get_logger(
            f"_modules_a{context.cfg.index}.{self.name or 'unknown'}"
        )

        # Track registered handlers per client so teardown() can remove them.
        # WeakKeyDictionary lets entries disappear when the client is GC'd,
        # preventing memory leaks across hot-reloads.
        self._handlers: WeakKeyDictionary[
            TelegramClient, list[tuple[EventBuilder, Callable]]
        ] = WeakKeyDictionary()

        # Cached me_id per client (populated on first _get_me_id call).
        self._me_cache: WeakKeyDictionary[TelegramClient, int] = WeakKeyDictionary()

        # Auto-delete tasks scheduled via _track_delete_task(), tracked so
        # teardown() can cancel any still-pending ones on hot-reload —
        # prevents a stale task from calling .delete() against a
        # disconnected/stale client after this instance has been torn down.
        self._pending_delete_tasks: set[asyncio.Task] = set()

    # ── Lifecycle (override in subclasses) ───────────────────────────────────

    def setup(self, client: TelegramClient) -> None:
        """
        Called once after the module is instantiated and a client is bound.

        Subclasses should register their event handlers here using
        ``self._add_handler(client, builder, callback)``.
        """
        pass

    def teardown(self, client: TelegramClient) -> None:
        """
        Called before hot-reload or shutdown.

        Removes all handlers registered via ``_add_handler`` for *client*,
        and cancels any auto-delete tasks scheduled via
        ``_track_delete_task`` that are still pending. Subclasses with
        additional cleanup (extra background tasks, caches, etc.) should
        override this and call ``super().teardown(client)``.
        """
        handlers = self._handlers.pop(client, [])
        for builder, cb in handlers:
            try:
                client.remove_event_handler(cb, builder)
            except Exception as exc:
                self._log.debug("teardown: remove_event_handler error: %s", exc)

        for task in self._pending_delete_tasks:
            if not task.done():
                task.cancel()
        self._pending_delete_tasks.clear()

    # ── Handler management ───────────────────────────────────────────────────

    def _add_handler(
        self,
        client: TelegramClient,
        event_builder: EventBuilder,
        callback: Callable,
    ) -> None:
        """
        Register *callback* for *event_builder* on *client* and remember it
        so ``teardown()`` can remove it later.

        Safe to call multiple times for the same (client, builder, callback);
        duplicates are skipped automatically.
        """
        bucket = self._handlers.setdefault(client, [])

        # Deduplicate: same (builder-type, callback) → skip.
        for existing_builder, existing_cb in bucket:
            if existing_cb is callback and type(existing_builder) is type(event_builder):
                return

        client.add_event_handler(callback, event_builder)
        bucket.append((event_builder, callback))

    # ── Cached self-ID ───────────────────────────────────────────────────────

    async def _get_me_id(self, client: TelegramClient) -> int | None:
        """
        Return the current user's Telegram ID (cached after first call).

        Returns ``None`` if the client is not authorized or any error occurs.
        """
        cached = self._me_cache.get(client)
        if cached is not None:
            return cached
        try:
            me = await client.get_me()
            if me is not None:
                self._me_cache[client] = me.id
                return me.id
        except Exception as exc:
            self._log.debug("_get_me_id error: %s", exc)
        return None

    async def _is_saved_messages(self, event) -> bool:
        """
        Return True if *event* was sent in this account's own Saved Messages
        chat (i.e. ``event.chat_id`` equals the account's own user ID).

        Uses the cached ``_get_me_id`` lookup, so calling this repeatedly
        (e.g. once per incoming command) costs at most one API call per
        client, not one per call.
        """
        me_id = await self._get_me_id(event.client)
        return me_id is not None and event.chat_id == me_id

    # ── Safe message editing ─────────────────────────────────────────────────

    async def _safe_edit(self, message, text: str, **kwargs: Any) -> None:
        """
        Edit *message* to *text*, silently absorbing the most common errors
        (``MessageNotModifiedError``, ``MessageIdInvalidError``).

        Extra *kwargs* are forwarded to ``Message.edit`` (e.g. ``parse_mode``,
        ``link_preview``).
        """
        try:
            await message.edit(text, **kwargs)
        except errors.MessageNotModifiedError:
            pass
        except errors.MessageIdInvalidError:
            self._log.debug("_safe_edit: invalid message id (already deleted?)")
        except Exception as exc:
            self._log.debug("_safe_edit error: %s", exc)

    # ── Auto-delete after delay ───────────────────────────────────────────────

    async def _auto_delete_after_delay(self, message, delay: float) -> None:
        """Sleep for *delay* seconds, then delete *message*. Cancel-safe."""
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        try:
            await message.delete()
        except Exception:
            pass

    def _track_delete_task(self, message, delay: float | None = None) -> asyncio.Task:
        """
        Schedule ``_auto_delete_after_delay(message, delay)`` and remember the
        task so ``teardown()`` can cancel it if it's still pending at
        hot-reload/shutdown time. The task removes itself from tracking on
        completion (success, failure, or cancellation) via ``add_done_callback``.

        If *delay* is omitted, uses ``self._auto_delete_default_delay``.
        """
        actual_delay = self._auto_delete_default_delay if delay is None else delay
        task = asyncio.create_task(
            self._auto_delete_after_delay(message, actual_delay),
            name=f"{self.name or 'module'}_autodel_a{self.cfg.index}",
        )
        self._pending_delete_tasks.add(task)
        task.add_done_callback(self._pending_delete_tasks.discard)
        return task

    async def _safe_edit_with_auto_delete(
        self,
        event,
        text: str,
        delay: float | None = None,
        **kwargs: Any,
    ) -> None:
        """
        ``_safe_edit(event, text, **kwargs)`` then auto-delete after *delay*
        (or ``self._auto_delete_default_delay`` if omitted).
        """
        await self._safe_edit(event, text, **kwargs)
        self._track_delete_task(event, delay)

    # ── Structured logging helpers ───────────────────────────────────────────

    def _log_info(self, msg: str, *args: Any) -> None:
        self._log.info("[%s] %s", self.cfg.index, msg % args if args else msg)

    def _log_warning(self, msg: str, *args: Any) -> None:
        self._log.warning("[%s] %s", self.cfg.index, msg % args if args else msg)

    def _log_error(self, msg: str, *args: Any) -> None:
        self._log.error("[%s] %s", self.cfg.index, msg % args if args else msg)

    def _log_debug(self, msg: str, *args: Any) -> None:
        self._log.debug("[%s] %s", self.cfg.index, msg % args if args else msg)

    # ── Repr ─────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} account={self.cfg.index}>"


__all__ = ["Module", "ModuleContext"]
