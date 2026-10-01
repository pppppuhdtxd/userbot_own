"""
userbot_own/core/watcher.py
════════════════════════════════════════════════════════════════
File Watcher — Runtime Configuration Reload

Monitors configuration files for changes and reports them in the logs.

Watched files:
- accounts/N/account.json — a changed phone number is logged (audit trail)
- accounts/ directory     — a new account folder is detected and logged

v3.1.10: this module OBSERVES and LOGS — it does not apply either change to
the running bot; both need a restart to take effect. Earlier text here (and
in README.md) said phone changes "apply instantly" and that new accounts
"trigger startup"; neither was ever true of the code below:
AccountJsonHandler._reload_account() only compares and logs, and
AccountsDirHandler.on_created() never invokes start_account_cb (its
docstring explains why that is not a one-line fix).

Architecture:
This watcher is independent of any specific account. It is set up ONCE
in the composition root BEFORE any accounts start, so it works
regardless of which account (if any) successfully connects.

Note:
This version uses direct connection only (no proxy support).
For network restriction bypass, use a system-level VPN
(WireGuard, OpenVPN, V2Ray) on Termux or Windows.

Public API:
    setup_watchers(accounts_dir, account_registry, start_account_cb=None) → Observer | None
        Returns the started Observer instance so the caller can stop it
        cleanly on shutdown via observer.stop() / observer.join().
        (start_account_cb is accepted but not invoked — see AccountsDirHandler.)

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

    When a new numeric directory is created (e.g., accounts/3/) this handler
    LOGS that it appeared and that the bot must be restarted to load it. It
    does NOT start the account — adding an account still requires a restart.

    Why `start_account_cb` is accepted but never called (v3.1.10 note; this
    docstring used to claim accounts could be added "without restarting the
    bot", and a log line said "Triggering startup..." while nothing was
    triggered):
    • `on_created` runs on watchdog's observer THREAD as a plain function,
      but what the composition root passes in — CompositionRoot.start_account
      — is a coroutine. Calling it here would just create a coroutine that
      never runs; it has to be scheduled onto the running event loop, and
      setup_watchers() is not given the loop.
    • The event fires when account_management/cli.py CREATES the folder,
      before account.json is written and before the login has finished with
      the session file, so an immediate start would race the CLI that is
      still setting the account up.
    A real implementation would pass the loop in, wait for a complete
    account.json plus an authorized session, and re-run
    config.loader.discover_accounts(). Until then the parameter is kept so
    the wiring in app/application.py (and this signature) does not change.
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

        # v3.1.10: BOTH branches below now say what will actually happen.
        # Previously only the second one (account.json already present when
        # the folder appeared — rare) mentioned a restart; in the normal
        # add-account flow the CLI creates the folder first and writes
        # account.json afterwards, so the operator only ever saw a quiet
        # "not found yet" line and was never told the account would not load.
        # `_known_accounts` is already updated above, so no later event will
        # re-check this folder — hence the restart hint has to be in both.
        account_json = path / "account.json"
        if not account_json.exists():
            log.info(
                "New account folder #%d created (no account.json yet). The bot "
                "does not start accounts at runtime — restart it once the "
                "account is fully set up to load it.",
                idx,
            )
            return

        log.warning(
            "New account #%d detected at runtime. The bot does not start "
            "accounts at runtime — restart it to load this account.",
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
        start_account_cb: Reserved for a future runtime-start feature. It is
                         accepted (and stored) so the composition root's wiring
                         does not have to change, but it is NOT invoked — see
                         AccountsDirHandler for why. New accounts need a restart.

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
    • accounts/N/account.json — phone number changes (logged only)
    • accounts/ directory — new account detection (logged; restart required)

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
