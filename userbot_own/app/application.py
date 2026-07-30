"""
userbot_own/app/application.py
════════════════════════════════════════════════════════════════
Application lifecycle — was main.py's `_main()` / `main()`.

Startup order:
1. Composition root bootstrap (directories, logging, config, registries)
2. Register file watchers (independent of accounts)
3. Register SIGTERM handler for graceful shutdown (Unix/Docker/Termux)
4. Start all accounts concurrently (all are equal)

Signal handling:
- Ctrl+C  / SIGINT  → Graceful shutdown (normal exit)
- SIGTERM           → Graceful shutdown (Unix supervisors, Docker, Termux)

Connection:
- Direct connection only (no proxy support)
- For bypassing restrictions, use system-level VPN
  (WireGuard, OpenVPN, V2Ray) on Termux or Windows

Run (from the repository root):
    python main.py
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from userbot_own.app.composition_root import CompositionRoot
from userbot_own.core.watcher import setup_watchers


class Application:
    """
    Owns one full run of the bot: bootstrap, start every account,
    handle shutdown signals, and clean up on exit.
    """

    def __init__(self, userbot_dir: Path) -> None:
        self.root = CompositionRoot(userbot_dir)
        self._watcher_observer = None

    async def run(self) -> None:
        """Equivalent of the original main.py's `_main()`."""
        log = self.root.bootstrap()

        # ── File watchers (independent of account startup order) ───
        self._watcher_observer = setup_watchers(
            accounts_dir=self.root.paths.accounts,
            account_registry=self.root.account_registry,
            start_account_cb=self.root.start_account,
        )
        log.info("File watchers active.")

        # ── SIGTERM handler (Unix supervisors, Docker, Termux) ──────
        # asyncio handles SIGINT (Ctrl+C) natively by raising
        # KeyboardInterrupt inside asyncio.run(). SIGTERM needs an
        # explicit handler so supervisors / `kill` / Docker stop
        # also trigger a clean shutdown rather than an abrupt exit.
        if sys.platform != "win32":
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(
                signal.SIGTERM,
                lambda: loop.call_soon_threadsafe(
                    asyncio.get_event_loop().stop
                    if False  # placeholder — overridden below
                    else _cancel_all_tasks,
                    loop,
                ),
            )
            # Replace placeholder with the real cancellation helper
            loop.add_signal_handler(
                signal.SIGTERM,
                lambda: _request_shutdown(loop),
            )

        # ── Start all accounts concurrently — all are equal ─────────
        tasks = [
            asyncio.create_task(
                self.root.start_account(acc),
                name=f"account{acc.index}",
            )
            for acc in self.root.account_registry.all()
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            log.info("Shutdown — stopping all accounts…")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            log.info("All accounts stopped.")
        finally:
            if self._watcher_observer is not None:
                try:
                    self._watcher_observer.stop()
                    self._watcher_observer.join(timeout=5.0)
                except Exception:
                    pass
                self._watcher_observer = None


def _request_shutdown(loop: asyncio.AbstractEventLoop) -> None:
    """
    Schedule cancellation of all running tasks on the given loop.
    Called by the SIGTERM signal handler.
    """
    import logging
    logging.getLogger(__name__).info(
        "SIGTERM received — initiating graceful shutdown."
    )
    for task in asyncio.all_tasks(loop):
        task.cancel()


def main() -> None:
    """Entry point — run the async main loop, exit cleanly on Ctrl+C or SIGTERM."""
    userbot_dir = Path(__file__).resolve().parent.parent

    try:
        asyncio.run(Application(userbot_dir).run())
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Ctrl+C received — exiting gracefully.")


__all__ = ["Application", "main"]
