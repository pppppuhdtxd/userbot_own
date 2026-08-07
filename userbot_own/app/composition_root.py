"""
userbot_own/app/composition_root.py
════════════════════════════════════════════════════════════════
The Composition Root.

This is the ONE place in the whole application allowed to construct
the application-scoped singletons (AccountLoaderRegistry,
AccountRegistry) and wire them into everything that needs them. Every
other file in the project receives these through constructor injection
— nothing outside this module ever does `SomeRegistry()` for the
shared instances.

v3.0.11: this used to also construct an application-scoped `EventBus`
and `PluginMetadataStore` here. Both were removed after a full-repo
audit found neither had any real consumer — see core/registry.py and
core/reconnector.py's docstrings for the detailed reasoning.

Startup order (unchanged from the original main.py's _main(), just
now made explicit rather than split between "module import time" and
"_main() body"):
1. Build Paths from the userbot_own/ package directory.
2. Load Settings (.env + environment variables).
3. Discover accounts (exits with a descriptive error if none are valid
   — same behavior, same messages, as the original config.py).
4. Ensure runtime directories exist (including every account's
   settings_dir).
5. Configure root logging.
6. Build the application-scoped registries + event bus.

`start_account()` reproduces the original `_start_account()` exactly:
build client → load modules → connect with retry → verify auth →
run reconnector + hot-reload watcher concurrently.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from userbot_own import __version__
from userbot_own.config.loader import discover_accounts, load_settings
from userbot_own.config.models import AccountConfig, Paths, Settings
from userbot_own.core import logging_setup
from userbot_own.core.context import ModuleContext
from userbot_own.core.loader import AccountLoader
from userbot_own.core.reconnector import AccountReconnector
from userbot_own.core.registry import AccountLoaderRegistry, AccountRegistry
from userbot_own.core.telegram_client import AccountClient

log: logging.Logger


class CompositionRoot:
    """
    Builds and owns the application's full object graph.

    Usage::

        root = CompositionRoot(userbot_package_dir)
        root.bootstrap()
        await root.start_account(some_account_cfg)   # one account
    """

    def __init__(self, userbot_dir: Path) -> None:
        self.paths: Paths = Paths.from_base(userbot_dir)

        # Populated by bootstrap():
        self.settings: Settings | None = None
        self.account_registry: AccountRegistry | None = None

        # Application-scoped singletons — constructed once, right here.
        self.loader_registry: AccountLoaderRegistry = AccountLoaderRegistry()

    def bootstrap(self) -> logging.Logger:
        """
        Load configuration, discover accounts, prepare directories, and
        configure logging. Must be called exactly once, before anything
        else. Returns the root logger for convenience.

        Exits the process (via config.loader.discover_accounts()) with a
        descriptive message if no valid accounts are configured — same
        behavior as the original config.py's module-level _load_accounts().
        """
        global log

        self.settings = load_settings(env_file=self.paths.base / ".env")
        self.account_registry = AccountRegistry(discover_accounts(self.paths))

        # Same call shape as the original config.ensure_dirs([acc.settings_dir
        # for acc in config.ACCOUNTS]) — create every base directory plus each
        # account's own settings directory.
        self.paths.ensure(
            extra_dirs=[acc.settings_dir for acc in self.account_registry.all()]
        )

        logging_setup.setup(
            log_level=self.settings.log_level,
            log_file=str(self.paths.logs / "main.log"),
        )
        log = logging_setup.get_logger(__name__)

        self._log_startup_banner()
        return log

    def _log_startup_banner(self) -> None:
        log.info("=" * 65)
        log.info("Multi-Account Userbot_own v%s starting…", __version__)
        log.info("Accounts : %d", len(self.account_registry))
        for acc in self.account_registry.all():
            log.info(
                "  [%d] phone=%-16s  api_id=%s",
                acc.index, acc.phone or "N/A", acc.api_id,
            )
        log.info("Log dir  : %s", self.paths.logs)

        if sys.platform == "win32":
            log.info("Shortcuts: Ctrl+C=exit | Ctrl+R=restart")
        else:
            log.info("Shortcuts: Ctrl+C=exit | SIGUSR1=restart (pkill -SIGUSR1 -f main.py)")

        log.info("=" * 65)

    def build_module_context(self, acc_cfg: AccountConfig) -> ModuleContext:
        """Build the ModuleContext every plugin for this account will receive."""
        return ModuleContext(
            cfg=acc_cfg,
            settings=self.settings,
            loader_registry=self.loader_registry,
            account_registry=self.account_registry,
        )

    # ── Per-account runner (unified — all accounts equal) ────────────────

    async def start_account(self, acc_cfg: AccountConfig) -> None:
        """
        Build, load modules, connect, and run a single account until cancelled.

        All accounts are equal — no special-casing for account #1.
        File watchers are set up independently by Application before this runs.

        Order is critical:
        1. Build TelegramClient with direct connection
        2. Load modules (register handlers on client object, not connection)
        3. Connect to Telegram with retry logic
        4. Start reconnector (which monitors and recovers if connection drops)
        """
        label = f"Account{acc_cfg.index}"

        logging_setup.add_account_handler(
            account_index=acc_cfg.index,
            log_file=acc_cfg.log_file,
            log_level=self.settings.log_level,
        )

        # Step 1: Build client
        ac = AccountClient(acc_cfg)
        client = ac.build()

        # Step 2: Load modules on the (not-yet-connected) client.
        # Telethon registers handlers on the client object, not the connection,
        # so this is safe before connect(). It also closes the race window where
        # a message arrives between connect() and load_all().
        context = self.build_module_context(acc_cfg)
        loader = AccountLoader(context, self.paths.modules)
        loader.load_all(client)
        self.loader_registry.register(acc_cfg.index, loader)

        # Step 3: Connect with retry (exponential backoff for mobile networks)
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            try:
                await asyncio.wait_for(client.connect(), timeout=30.0)
                log.info("[%s] Connected to Telegram.", label)
                break
            except (TimeoutError, Exception) as exc:
                log.warning(
                    "[%s] Connect attempt %d/%d failed: %s",
                    label, attempt, max_attempts, exc,
                )
                if attempt == max_attempts:
                    log.error(
                        "[%s] Initial connect failed — reconnector will keep trying.",
                        label,
                    )
                else:
                    await asyncio.sleep(min(2 ** attempt, 10))

        # Step 4: Verify authorization (only if connected)
        if client.is_connected():
            try:
                if not await asyncio.wait_for(client.is_user_authorized(), timeout=10.0):
                    log.warning("[%s] Not authorized — session may be invalid.", label)
            except Exception as exc:
                log.warning("[%s] Authorization check failed: %s", label, exc)

        # Step 5: Run reconnector + file watcher concurrently
        reconnector = AccountReconnector(ac, loader)
        try:
            await asyncio.gather(
                reconnector.run(),
                loader.watch(),
            )
        except asyncio.CancelledError:
            log.info("[%s] Shutdown requested.", label)
        finally:
            if client and client.is_connected():
                try:
                    await client.disconnect()
                except Exception:
                    pass
            log.info("[%s] Disconnected.", label)


__all__ = ["CompositionRoot"]
