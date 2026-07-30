"""
userbot_own/core/events.py
════════════════════════════════════════════════════════════════
Application-scoped event bus.

This formalizes a pattern that already existed informally in the
original codebase: core/reconnector.py kept its own private
module-level list of "connection changed" callbacks
(register_connection_callback / notify_connection_change), with no
other event types and no shared infrastructure, and — as of this
refactor — zero actual subscribers anywhere in the project. That
one-off mechanism is now a general-purpose, typed publish/subscribe
bus that any component can use. It is built ONCE by the composition
root and handed to whoever needs it via constructor injection —
never imported as a module-level global.

Design notes:
- Deliberately small: a synchronous callback dispatcher, because
  that is all the original code ever needed. This is not a message
  queue or an async framework.
- Events are plain frozen dataclasses, dispatched by exact type.
  Handlers registered for a supertype are NOT invoked for subtypes —
  keep event hierarchies flat.
- publish() never lets one handler's exception stop the others or
  crash the publisher, matching the original
  notify_connection_change()'s per-callback try/except.
- The original notify_connection_change(connected, account_index)
  called subscribers with a *different* number of positional
  arguments depending on whether account_index was None. Since there
  were no real subscribers to preserve compatibility with, the
  replacement event below always carries both fields — one
  consistent shape instead of two.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

log = logging.getLogger(__name__)

TEvent = TypeVar("TEvent")
Handler = Callable[[TEvent], None]


class EventBus:
    """
    Minimal synchronous publish/subscribe bus, keyed by exact event type.

    Usage::

        bus = EventBus()
        bus.subscribe(ConnectionStateChanged, on_state_changed)
        ...
        bus.publish(ConnectionStateChanged(account_index=1, connected=True))
    """

    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: type[TEvent], handler: Handler) -> None:
        """Register *handler* to be called whenever *event_type* is published."""
        bucket = self._handlers[event_type]
        if handler not in bucket:
            bucket.append(handler)

    def unsubscribe(self, event_type: type[TEvent], handler: Handler) -> None:
        """Remove a previously registered handler, if present. Safe to call twice."""
        try:
            self._handlers[event_type].remove(handler)
        except ValueError:
            pass

    def publish(self, event: object) -> None:
        """
        Call every handler subscribed to `type(event)`.

        A handler that raises is logged and skipped so one broken
        subscriber can never prevent the others from running, or crash
        the publisher — the same guarantee the original
        notify_connection_change() gave each callback.
        """
        for handler in list(self._handlers.get(type(event), ())):
            try:
                handler(event)
            except Exception:
                log.warning(
                    "Event handler failed for %s", type(event).__name__, exc_info=True
                )


# ── Application events ────────────────────────────────────────────────────────
#
# Currently one event type, carrying exactly what the original
# reconnector.py notified about. New event types can be added here as
# the refactor progresses without touching EventBus itself.

@dataclass(frozen=True, slots=True)
class ConnectionStateChanged:
    """Published by AccountReconnector whenever an account's connection state changes."""
    account_index: int | None
    connected: bool


__all__ = ["EventBus", "ConnectionStateChanged"]
