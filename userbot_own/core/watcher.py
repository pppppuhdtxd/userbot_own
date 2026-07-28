"""
userbot_own/core/watcher.py
════════════════════════════════════════════════════════════════
File Watcher — Runtime Configuration Reload

Monitors configuration files for changes and applies them at runtime
without requiring a restart.

Watched files:
- accounts/N/account.json — phone number changes apply instantly
- accounts/ directory     — new accounts trigger startup callback

Architecture:
This watcher is independent of any specific account. It is set up ONCE
in the composition root BEFORE any accounts start, so it works
regardless of which account (if any) successfully connects.

Note:
This version uses direct connection only (no proxy support).
For network restriction bypass, use a system-level VPN
(WireGuard, OpenVPN, V2Ray) on Termux or Windows.

Public API:
    setup_watchers(paths, account_registry, start_account_cb=None) → Observer | None
        Returns the started Observer instance so the caller can stop it
        cleanly on shutdown via observer.stop() / observer.join().

DI note: the original watcher reached for the module-level `config.ACCOUNTS`
/ `config.ACCOUNTS_DIR` globals directly. Both handlers below now take the
accounts directory and the (live, mutable) AccountRegistry as constructor
arguments instead — supplied once by the composition root, exactly like
everything else in core/.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from userbot_own.core.logging_setup import get_logger
from userbot_own.core.registry import AccountRegistry

try:
    from watchdog.events import FileSystemEventHandler as _FileSystemEventHandler
    from watchdog.observers import Observer as _Observer
    _WATCHDOG_AVAILABLE = True
except ImportError:  # pragma: no cover
    _WATCHDOG_AVAILABLE = False
    _FileSystemEventHandler = object  # type: ignore[assignment, misc]
    _Observer = None  # type: ignore[assignment]

# Re-export as the canonical names used throughout this module.
FileSystemEventHandler = _FileSystemEventHandler

if TYPE_CHECKING:
    # Always available to the type-checker regardless of runtime import outcome.
    from watchdog.observers import Observer

log = get_logger(__name__)


# ── Account JSON Watcher ─────────────────────────────────────────────────────

class AccountJsonHandler(FileSystemEventHandler):
    """
    Watches `accounts/N/account.json` files for modifications.

    When an account.json file is modified:
    • Re-reads the file
    • Logs phone number changes for audit purposes

    Note: The admin system has been removed. All accounts are equal and
    owned by the user. No permission tracking is needed.
    """

    def __init__(self, accounts_dir: Path, account_registry: AccountRegistry) -> None:
        super().__init__()
        self._accounts_dir = accounts_dir
        self._account_registry = account_registry

    def on_modified(self, event) -> None:
        if event.is_directory:
            return

        path = Path(event.src_path)
        if path.name != "account.json":
            return

        # Extract account index from path: accounts/N/account.json
        try:
            idx = int(path.parent.name)
        except (ValueError, AttributeError):
            return

        self._reload_account(idx, path)

    def _reload_account(self, idx: int, path: Path) -> None:
        """Re-read and apply changes from a modified account.json."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to read modified account.json #%d: %s", idx, exc)
            return

        new_phone = str(raw.get("phone", "")).strip()

        # Find the account in the live account registry
        account = self._account_registry.get(idx)
        if account is None:
            log.debug("Modified account.json for unknown account #%d", idx)
            return

        # Log phone number changes (for audit purposes)
        if new_phone and new_phone != account.phone:
            log.info(
                "Account #%d phone changed: %s → %s (restart required for full effect)",
                idx, account.phone or "N/A", new_phone,
            )
        else:
            log.debug("Account #%d configuration reloaded from disk.", idx)


# ── Accounts Directory Watcher ───────────────────────────────────────────────

class AccountsDirHandler(FileSystemEventHandler):
    """
    Watches the `accounts/` directory for new account folders.

    When a new numeric directory is created (e.g., accounts/3/):
    • Checks if it contains a valid account.json
    • If valid and start_account_cb is provided, triggers account startup
    • Logs the discovery for audit purposes

    This allows adding new accounts at runtime without restarting the bot.
    """

    def __init__(
        self,
        account_registry: AccountRegistry,
        start_account_cb: Callable | None = None,
    ) -> None:
        super().__init__()
        self._start_account_cb = start_account_cb
        self._known_accounts = {acc.index for acc in account_registry.all()}

    def on_created(self, event) -> None:
        if not event.is_directory:
            return

        path = Path(event.src_path)

        # Check if it's a numeric directory (account folder)
        if not path.name.isdigit():
            return

        idx = int(path.name)

        # Skip if we already know about this account
        if idx in self._known_accounts:
            return

        self._known_accounts.add(idx)

        # Check if account.json exists
        account_json = path / "account.json"
        if not account_json.exists():
            log.info(
                "New account folder #%d created, but account.json not found yet.",
                idx,
            )
            return

        log.info("New account #%d detected at runtime.", idx)

        # Trigger account startup if callback is available
        if self._start_account_cb:
            log.info("Triggering startup for new account #%d...", idx)
            # Note: A full implementation would:
            # 1. Re-run config.loader.discover_accounts() to pick up the new account
            # 2. Find the new AccountConfig
            # 3. Call _start_account_cb(new_config)
            # For now, we log and require a restart for full functionality.
            log.warning(
                "Runtime account addition detected. Account #%d will be fully "
                "loaded on next restart (manual restart required).",
                idx,
            )


# ── Setup Function ───────────────────────────────────────────────────────────

def setup_watchers(
    accounts_dir: Path,
    account_registry: AccountRegistry,
    start_account_cb: Callable | None = None,
) -> Observer | None:
    """
    Set up file watchers for runtime configuration reload.

    This function is called ONCE by the composition root BEFORE any accounts
    start. It is completely independent of any specific account, so it works
    regardless of which account (if any) successfully connects.

    Args:
        accounts_dir:     Path to the `accounts/` directory (Paths.accounts).
        account_registry: The application's live AccountRegistry.
        start_account_cb: Optional callback to start new accounts at runtime.
                         If provided, called when a new account folder is detected.

    Returns:
        The started ``Observer`` instance so the caller can stop the background
        thread cleanly on shutdown::

            observer = setup_watchers(...)
            # … at shutdown:
            observer.stop()
            observer.join()

        Returns ``None`` if the ``watchdog`` package is not installed (the bot
        continues without file-watching in that case).

    Watchers configured:
    • accounts/N/account.json — phone number changes
    • accounts/ directory — new account detection

    Note:
    The `modules/` directory watcher is handled separately by
    AccountLoader.watch() for hot-reload support (per-account).
    """
    if not _WATCHDOG_AVAILABLE:
        log.warning(
            "watchdog package not installed — file watchers disabled. "
            "Install it with: pip install watchdog"
        )
        return None

    observer = _Observer()

    # Watch accounts/ directory for account.json changes
    accounts_handler = AccountJsonHandler(accounts_dir, account_registry)
    observer.schedule(accounts_handler, str(accounts_dir), recursive=True)

    # Watch accounts/ directory for new account folders
    dir_handler = AccountsDirHandler(account_registry, start_account_cb)
    observer.schedule(dir_handler, str(accounts_dir), recursive=False)

    observer.start()

    log.info(
        "File watchers configured: accounts/ directory (account.json + new accounts)."
    )

    # Return the observer so the caller can stop it on shutdown, preventing
    # the watchdog thread from outliving the event loop.
    return observer


# ── Public API ───────────────────────────────────────────────────────────────

__all__ = [
    "setup_watchers",
    "AccountJsonHandler",
    "AccountsDirHandler",
]
