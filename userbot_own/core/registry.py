"""
userbot_own/core/registry.py
════════════════════════════════════════════════════════════════
Application-scoped registries: AccountRegistry, AccountLoaderRegistry,
and PluginMetadataStore.

Both are still intentionally *application*-scoped (shared across
every running account) — that isn't changing. What changes is how
components get hold of them: the original module-level singletons
(`loader_registry = AccountLoaderRegistry()`, `plugin_store =
PluginMetadataStore()`) were instantiated at import time and reached
for via `from core.plugin_registry import loader_registry` wherever
needed. That is the textbook Service Locator anti-pattern the
architecture goals call out.

Here, the composition root builds exactly one instance of each at
startup and passes references down through constructors (see
core/context.py's ModuleContext, and app/composition_root.py). No
other module should construct or import a global instance of either
class.

Two scoping notes carried over unchanged from the original design:
- AccountLoaderRegistry is genuinely cross-account: `system.py`'s
  `.stats` command reports connected-account counts and total
  loaded-module counts *across every running account*, not just the
  one it was invoked from. That's why every Module receives the same
  shared registry instance rather than "just its own loader".
- PluginMetadataStore is written to on every load/reload/unload but,
  as of this refactor, has no reader anywhere in the codebase. It is
  kept (not deleted) because the README lists it as a supported
  feature ("Plugin registry — Rich metadata, introspection, and
  runtime management API") — removing it would be removing a
  documented feature, which is out of scope for an internal
  refactor. Worth knowing about if you ever want to prune it
  yourself.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
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


# ── Plugin metadata ──────────────────────────────────────────────────────────

@dataclass(slots=True)
class PluginMetadata:
    """
    Rich metadata snapshot for one loaded plugin instance.
    Captured at load/reload time and stored in the PluginMetadataStore.

    Attributes:
        account_index: Which account this plugin belongs to.
        stem:          File stem, e.g. "clearer" for clearer.py.
        name:          Human-readable module name from Module.name.
        help_text:     Short help string for compact help view.
        file_path:     Absolute path to the module .py file.
        loaded_at:     Timestamp of the most recent load/reload.
        load_count:    Cumulative number of times this plugin has been loaded
                       (increments on each reload, preserved across reloads).
    """
    account_index: int
    stem:          str
    name:          str
    help_text:     str
    file_path:     str
    loaded_at:     datetime = field(default_factory=datetime.now)
    load_count:    int      = 1


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


# ── Plugin metadata store ─────────────────────────────────────────────────────

class PluginMetadataStore:
    """
    Thread-safe store mapping (account_index, stem) → PluginMetadata.
    The loader calls ``upsert()`` on every load/reload so the store always
    reflects the current runtime state.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[int, str], PluginMetadata] = {}
        self._lock = threading.Lock()

    def upsert(self, account_index: int, stem: str, metadata: PluginMetadata) -> None:
        """Insert or update the metadata for *(account_index, stem)*."""
        with self._lock:
            key = (account_index, stem)
            existing = self._store.get(key)
            if existing is not None:
                # Preserve the cumulative load count
                metadata.load_count = existing.load_count + 1
            self._store[key] = metadata

    def get(self, account_index: int, stem: str) -> PluginMetadata | None:
        """Return metadata for *(account_index, stem)*, or ``None``."""
        return self._store.get((account_index, stem))

    def remove(self, account_index: int, stem: str) -> None:
        """Delete metadata for *(account_index, stem)* if present."""
        with self._lock:
            self._store.pop((account_index, stem), None)

    def remove_account(self, account_index: int) -> None:
        """Remove all metadata entries for *account_index*."""
        with self._lock:
            keys = [k for k in self._store if k[0] == account_index]
            for k in keys:
                del self._store[k]

    def for_account(self, account_index: int) -> list[PluginMetadata]:
        """Return all metadata entries for *account_index*, sorted by stem."""
        return sorted(
            (v for k, v in self._store.items() if k[0] == account_index),
            key=lambda m: m.stem,
        )

    def all(self) -> list[PluginMetadata]:
        """Return all stored metadata entries."""
        return list(self._store.values())


__all__ = ["AccountRegistry", "PluginMetadata", "AccountLoaderRegistry", "PluginMetadataStore"]
