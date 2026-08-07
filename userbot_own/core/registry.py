"""
userbot_own/core/registry.py
════════════════════════════════════════════════════════════════
Application-scoped registries: AccountRegistry and AccountLoaderRegistry.

Both are intentionally *application*-scoped (shared across every
running account). The composition root builds exactly one instance of
each at startup and passes references down through constructors (see
core/context.py's ModuleContext, and app/composition_root.py) instead
of the original module-level Service Locator pattern
(`from core.plugin_registry import loader_registry`). No other module
should construct or import a global instance of either class.

Scoping note carried over unchanged from the original design:
- AccountLoaderRegistry is genuinely cross-account: `system.py`'s
  `.stats` command reports connected-account counts and total
  loaded-module counts *across every running account*, not just the
  one it was invoked from. That's why every Module receives the same
  shared registry instance rather than "just its own loader".

v3.0.11: `PluginMetadataStore` (and its `PluginMetadata` record type)
was removed. It was written to on every load/reload/unload but, per a
full-repo audit, had no reader anywhere in the codebase. It had
originally been kept despite being unused because the README
advertised it as a feature ("Plugin registry — Rich metadata,
introspection, and runtime management API") — that README entry has
been removed in the same release so the docs and the code agree
again. If plugin introspection is wanted later, it's straightforward
to reintroduce, ideally alongside the command/UI that will actually
read it.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from userbot_own.config.models import AccountConfig
from userbot_own.core.exceptions import LoaderNotFoundError

if TYPE_CHECKING:
    from userbot_own.core.loader import AccountLoader


# ── Account registry ──────────────────────────────────────────────────────────
#
# Replaces the original `config.ACCOUNTS` module-level list. That list was
# NOT read-only in the original code, even though config.py made it look
# like a one-time startup snapshot: core/account_manager.py appended to it
# when `.addaccount` provisioned a new account, and filtered it in place
# when `.removeaccount` removed one — with core/watcher.py and system.py's
# `.stats` reading it live. This class formalizes that as a proper
# thread-safe, application-scoped registry instead of a mutable global list
# threaded through `import config` everywhere.

class AccountRegistry:
    """
    Thread-safe collection of every configured `AccountConfig`, keyed by
    account index. Populated at startup from `config.loader.discover_accounts()`
    and mutated at runtime as accounts are added/removed via the in-chat
    account-management flows.
    """

    def __init__(self, initial: list[AccountConfig] | None = None) -> None:
        self._by_index: dict[int, AccountConfig] = {a.index: a for a in (initial or [])}
        self._lock = threading.Lock()

    def add(self, account: AccountConfig) -> None:
        """Register a newly provisioned account."""
        with self._lock:
            self._by_index[account.index] = account

    def remove(self, index: int) -> AccountConfig | None:
        """Remove an account by index, returning it if it existed."""
        with self._lock:
            return self._by_index.pop(index, None)

    def get(self, index: int) -> AccountConfig | None:
        """Return the AccountConfig for *index*, or ``None`` if not registered."""
        return self._by_index.get(index)

    def all(self) -> list[AccountConfig]:
        """Return all registered accounts, sorted by index."""
        return sorted(self._by_index.values(), key=lambda a: a.index)

    def __len__(self) -> int:
        return len(self._by_index)

    def __iter__(self):
        return iter(self.all())

    def __contains__(self, index: int) -> bool:
        return index in self._by_index


# ── AccountLoader registry ────────────────────────────────────────────────────

class AccountLoaderRegistry:
    """
    Thread-safe map from account index to its `AccountLoader`.

    Writes are protected by a lock; reads are not (dict reads are GIL-safe in
    CPython and the registry is written only during startup/shutdown).
    """

    def __init__(self) -> None:
        self._registry: dict[int, AccountLoader] = {}
        self._lock = threading.Lock()

    def register(self, account_index: int, loader: AccountLoader) -> None:
        """Register (or replace) the loader for *account_index*."""
        with self._lock:
            self._registry[account_index] = loader

    def get(self, account_index: int) -> AccountLoader:
        """
        Return the loader for *account_index*.

        Raises:
            LoaderNotFoundError: If no loader is registered for the index.
        """
        try:
            return self._registry[account_index]
        except KeyError:
            raise LoaderNotFoundError(account_index) from None

    def remove(self, account_index: int) -> AccountLoader | None:
        """Remove and return the loader for *account_index*, or ``None``."""
        with self._lock:
            return self._registry.pop(account_index, None)

    def all(self) -> dict[int, AccountLoader]:
        """Return a shallow copy of the full registry mapping."""
        return dict(self._registry)

    def __len__(self) -> int:
        return len(self._registry)

    def __contains__(self, account_index: int) -> bool:
        return account_index in self._registry


__all__ = ["AccountRegistry", "AccountLoaderRegistry"]
