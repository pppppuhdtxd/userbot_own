"""
userbot_own/core/reconnector.py
════════════════════════════════════════════════════════════════
Account Reconnector — Per-Account Connection Recovery (Direct Only)

Uses tenacity for smart retry with exponential backoff.
Uses loguru for structured logging with context.
Includes DNS + Telegram endpoint test to distinguish outages.

Each account runs its own reconnect loop that:
- Monitors connection health via lightweight API calls
- Detects internet availability via DNS lookup
- Detects Telegram availability via endpoint test
- Rebuilds client with direct connection after failures
- Uses tenacity's exponential backoff for recovery attempts
- RE-REGISTERS all module handlers on new client after rebuild

Network State Detection:
- ONLINE        — Internet works, can reach Telegram
- NO_INTERNET   — No connectivity at all (DNS fails)
- TELEGRAM_DOWN — Internet works but Telegram unreachable
- UNKNOWN       — Couldn't determine state

Adaptive Backoff Strategy (via tenacity):
- NO_INTERNET:  Exponential (1s → 2s → 4s → ... → 300s max)
- TELEGRAM_DOWN: Longer waits (60s → 90s → ... → 300s max)
- ONLINE:       Tenacity retry with exponential (1s → 60s, 5 attempts)
- FloodWait:    Wait the exact requested time + buffer

Adaptive Health Check:
- 30s interval when healthy (low overhead)
- 5-15s interval when degraded (faster recovery detection)

Public API:
    AccountReconnector(account_client, loader) → reconnector instance
        .run()          → start the reconnect loop (async)
        .stop()         → stop the loop
        .is_connected() → check current connection status

v3.0.11 note: connection-state transitions were previously published as
`ConnectionStateChanged` events through an application-scoped `EventBus`
(core/events.py, now removed). A full-repo audit found zero subscribers
anywhere in the codebase — the *original* pre-refactor mechanism this
replaced (`notify_connection_change()` / `register_connection_callback()`)
had zero real subscribers too. Two independent implementations of the same
notification in a row with no consumer is a strong signal it isn't needed
yet, so the bus was removed rather than carried forward unused. Every
trigger point below is unchanged — `_notify()` now logs the transition
directly via this reconnector's own contextual logger instead of
publishing it. If a real consumer shows up later (e.g. a live per-account
status view), it's easy to reintroduce a bus purpose-built for whatever
that feature actually needs.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import socket
import sqlite3
from enum import Enum, auto
from typing import TYPE_CHECKING

from loguru import logger
from telethon import errors
from telethon.tl.functions.updates import GetStateRequest
from tenacity import (
    RetryError,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

if TYPE_CHECKING:
    from userbot_own.core.loader import AccountLoader
    from userbot_own.core.telegram_client import AccountClient


# ── Health Check Intervals ────────────────────────────────────────────────────

#: Interval when connection is healthy (seconds)
_HEALTHY_INTERVAL: float = 30.0

#: Minimum interval when degraded (seconds)
_MIN_DEGRADED_INTERVAL: float = 5.0


# ── Network State Detection ──────────────────────────────────────────────────

class NetworkState(Enum):
    """Current state of network connectivity."""
    ONLINE = auto()        # Everything works
    NO_INTERNET = auto()   # No internet at all
    TELEGRAM_DOWN = auto() # Internet works but Telegram unreachable
    UNKNOWN = auto()       # Couldn't determine


async def detect_network_state(timeout: float = 3.0) -> NetworkState:
    """
    Detect current network state by testing DNS resolution + Telegram endpoint.

    Strategy:
    1. DNS lookup for google.com (fast, 1-2s)
    2. If DNS fails → NO_INTERNET
    3. If DNS succeeds, try Telegram DC endpoint
    4. If Telegram fails → TELEGRAM_DOWN
    5. If both succeed → ONLINE

    Returns:
        NetworkState enum value
    """
    # Step 1: DNS test — use get_running_loop() (Python 3.10+ safe)
    try:
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(
            loop.getaddrinfo("google.com", 443, family=socket.AF_INET),
            timeout=timeout,
        )
    except (TimeoutError, socket.gaierror, OSError):
        return NetworkState.NO_INTERNET
    except Exception:
        return NetworkState.UNKNOWN

    # Step 2: Telegram endpoint test (lightweight TCP connect)
    try:
        # Try connecting to Telegram DC (149.154.167.50 is a known DC)
        telegram_dc = "149.154.167.50"
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(telegram_dc, 443),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return NetworkState.ONLINE
    except (TimeoutError, OSError, ConnectionRefusedError):
        return NetworkState.TELEGRAM_DOWN
    except Exception:
        return NetworkState.UNKNOWN


# ── AccountReconnector ───────────────────────────────────────────────────────

class AccountReconnector:
    """
    Per-account reconnection manager with tenacity-powered retry.

    Uses a continuous loop that:
    1. Checks if client is connected
    2. If not, detects network state
    3. Applies exponential backoff based on failure type
    4. Rebuilds client when internet is available
    5. RE-REGISTERS handlers on new client (CRITICAL)
    6. Logs connection-state transitions for this account

    The actual reconnect logic uses @retry decorator from tenacity
    for automatic exponential backoff with smart exception handling.

    Usage:
        reconnector = AccountReconnector(account_client, loader)
        await reconnector.run()  # blocks until cancelled
    """

    def __init__(
        self,
        account_client: AccountClient,
        loader: AccountLoader,
    ) -> None:
        self._ac = account_client
        self._loader = loader
        self._cfg = account_client.cfg
        self._running = False
        self._last_state = NetworkState.UNKNOWN
        self._consecutive_failures = 0

        # Create a context-bound logger for this account
        self._log = logger.bind(
            event_type="reconnect",
            account=self._cfg.index,
        )

    def _notify(self, connected: bool) -> None:
        """Log a connection-state transition for this account."""
        self._log.info(
            "[Account{}] Connection state changed: connected={}",
            self._cfg.index, connected,
        )

    async def run(self) -> None:
        """
        Main reconnect loop. Runs until cancelled.

        This is typically started as an async task by the composition root:
            asyncio.create_task(reconnector.run())
        """
        self._running = True
        self._log.info("[Account{}] Reconnector started.", self._cfg.index)

        while self._running:
            try:
                await self._reconnect_cycle()
            except asyncio.CancelledError:
                self._log.info("[Account{}] Reconnector cancelled.", self._cfg.index)
                break
            except Exception as exc:
                self._log.exception(
                    "[Account{}] Reconnector cycle error: {}",
                    self._cfg.index, exc,
                )
                await asyncio.sleep(5)

        self._log.info("[Account{}] Reconnector stopped.", self._cfg.index)

    def stop(self) -> None:
        """Signal the reconnect loop to stop."""
        self._running = False

    def is_connected(self) -> bool:
        """Check if the client is currently connected."""
        client = self._ac.client
        return client is not None and client.is_connected()

    async def _reconnect_cycle(self) -> None:
        """
        Single iteration of the reconnect loop.

        Steps:
        1. Check if client exists and is connected
        2. If connected, verify with a lightweight API call
        3. If disconnected or verification fails, trigger recovery
        4. Apply adaptive backoff delay before next cycle
        """
        client = self._ac.client

        # Step 1: Check basic connection
        if client is None or not client.is_connected():
            self._log.warning(
                "[Account{}] Client disconnected — triggering recovery.",
                self._cfg.index,
            )
            await self._recover_connection()
            return

        # Step 2: Verify connection with lightweight API call
        try:
            await asyncio.wait_for(client(GetStateRequest()), timeout=10.0)

            # Connection is healthy — reset failure counter, use long interval
            self._consecutive_failures = 0
            await asyncio.sleep(_HEALTHY_INTERVAL)
            return

        except TimeoutError:
            self._log.warning(
                "[Account{}] Connection verification timeout.",
                self._cfg.index,
            )
            self._consecutive_failures += 1

        except errors.AuthKeyError:
            self._log.error(
                "[Account{}] Auth key error — session may be invalid.",
                self._cfg.index,
            )
            # Don't try to reconnect, let user fix session
            self._notify(connected=False)
            await asyncio.sleep(300)
            return

        except asyncio.IncompleteReadError as exc:
            # Network dropped mid-read of a Telegram MTProto frame.
            # asyncio.IncompleteReadError subclasses EOFError, not OSError or
            # ConnectionError, so it falls through to the generic except
            # without this explicit clause.  It is a predictable symptom of a
            # dropped connection — treat it identically to OSError/ConnectionError.
            partial_bytes = len(exc.partial) if exc.partial else 0
            self._log.warning(
                "[Account{}] Incomplete MTProto frame read ({} of {} bytes) — "
                "connection dropped mid-frame.",
                self._cfg.index, partial_bytes, exc.expected,
            )
            self._consecutive_failures += 1

        except (errors.FloodWaitError, OSError, ConnectionError) as exc:
            self._log.warning(
                "[Account{}] Connection verification failed: {}",
                self._cfg.index, exc,
            )
            self._consecutive_failures += 1

            # Special handling for FloodWait
            if isinstance(exc, errors.FloodWaitError):
                self._log.warning(
                    "[Account{}] FloodWait {}s — waiting.",
                    self._cfg.index, exc.seconds,
                )
                await asyncio.sleep(exc.seconds + 5)
                return

        except Exception as exc:
            self._log.error(
                "[Account{}] Unexpected verification error: {}",
                self._cfg.index, exc,
            )
            self._consecutive_failures += 1

        # Step 3: Decide recovery action based on failure count
        if self._consecutive_failures >= 2:
            self._log.warning(
                "[Account{}] {} consecutive failures — triggering recovery.",
                self._cfg.index, self._consecutive_failures,
            )
            await self._recover_connection()
        else:
            # Minor issue — use adaptive shorter interval for faster detection
            # Formula: 30s / (failures+1), but at least 5s
            # failures=1 → 15s, failures=2 → recovery (handled above)
            interval = max(
                _MIN_DEGRADED_INTERVAL,
                _HEALTHY_INTERVAL / (self._consecutive_failures + 1),
            )
            self._log.debug(
                "[Account{}] Minor issue — re-checking in {:.1f}s.",
                self._cfg.index, interval,
            )
            await asyncio.sleep(interval)

    async def _recover_connection(self) -> None:
        """
        Recover from a connection failure.

        Strategy:
        1. Detect network state (DNS + Telegram endpoint test)
        2. If no internet, wait with exponential backoff
        3. If internet available but Telegram down, wait longer
        4. If all good, rebuild client with tenacity retry
        5. RE-REGISTER handlers on new client (CRITICAL)
        6. Log the connection-state transition

        On successful recovery _consecutive_failures is reset to zero inside
        _attempt_connect().  On any non-success path the counter continues
        to grow so that backoff naturally lengthens — but it is capped at
        the formula maxima to avoid runaway values.
        """
        self._log.info(
            "[Account{}] Starting connection recovery...",
            self._cfg.index,
        )

        # Detect network state
        state = await detect_network_state()
        self._last_state = state

        self._log.info(
            "[Account{}] Network state detected: {}",
            self._cfg.index, state.name,
        )

        if state == NetworkState.NO_INTERNET:
            await self._handle_no_internet()
        elif state == NetworkState.TELEGRAM_DOWN:
            await self._handle_telegram_down()
        elif state == NetworkState.ONLINE:
            await self._handle_online()
        else:  # UNKNOWN
            self._log.warning(
                "[Account{}] Unknown network state — attempting reconnect.",
                self._cfg.index,
            )
            await self._handle_online()

    async def _handle_no_internet(self) -> None:
        """Handle NO_INTERNET state — wait with exponential backoff."""
        # Calculate backoff: 1s → 2s → 4s → 8s → ... → 300s max.
        # Cap _consecutive_failures at 8 before the exponent to avoid
        # integer overflow (2**300 is a valid Python int but meaningless here).
        backoff = min(2 ** min(self._consecutive_failures, 8), 300)

        self._log.warning(
            "[Account{}] No internet connection detected. "
            "Waiting {}s before retry...",
            self._cfg.index, backoff,
        )
        self._notify(connected=False)
        self._consecutive_failures += 1
        await asyncio.sleep(backoff)

    async def _handle_telegram_down(self) -> None:
        """Handle TELEGRAM_DOWN state — wait longer, Telegram is having issues."""
        backoff = min(60 + self._consecutive_failures * 30, 300)

        self._log.warning(
            "[Account{}] Internet works but Telegram is unreachable. "
            "Waiting {}s before retry...",
            self._cfg.index, backoff,
        )
        self._notify(connected=False)
        self._consecutive_failures += 1
        await asyncio.sleep(backoff)

    async def _handle_online(self) -> None:
        """Handle ONLINE state — rebuild client with tenacity retry."""
        self._log.info(
            "[Account{}] Internet available. Rebuilding client...",
            self._cfg.index,
        )

        try:
            await self._rebuild_client_with_retry()
            # _attempt_connect() resets _consecutive_failures to 0 on success.
        except RetryError as exc:
            self._log.error(
                "[Account{}] All reconnect attempts failed: {}",
                self._cfg.index, exc,
            )
            self._consecutive_failures += 1
            # Wait before next cycle
            await asyncio.sleep(min(30 * self._consecutive_failures, 300))

    async def _rebuild_client_with_retry(self) -> None:
        """
        Orchestrate disconnect (once) then connect (with tenacity retry).

        The disconnect step is intentionally NOT inside the retry loop.
        The root cause of "database is locked" errors is that the old retry
        design called disconnect() on every attempt — each failed attempt
        left the SQLite session file in a locked state, and the next attempt
        immediately tried to disconnect again before the lock was released.

        Fixed design:
          1. Disconnect the old client ONCE, with a hard timeout.
          2. Wait long enough for SQLite to release the session file lock.
             On Windows a dropped TCP connection (WinError 64) can hold the
             SQLite WAL lock for 1-3 seconds; 2 s is a safe minimum.
          3. Hand off to _attempt_connect(), which builds a fresh client on
             each retry so a failed connect() never taints the next attempt.
        """
        # ── Step 1: Disconnect old client — runs ONCE, never retried ─────────
        old_client = self._ac.client
        if old_client is not None:
            try:
                await asyncio.wait_for(old_client.disconnect(), timeout=5.0)
                self._log.debug(
                    "[Account{}] Old client disconnected.",
                    self._cfg.index,
                )
            except Exception as exc:
                self._log.warning(
                    "[Account{}] Error disconnecting old client: {} — forcing closure.",
                    self._cfg.index, exc,
                )
                # Nullify so the GC can close the underlying DB connection.
                self._ac.client = None

        # ── Step 2: Wait for SQLite session file lock to be released ─────────
        # A hard TCP drop (WinError 64) leaves SQLite holding an exclusive lock
        # for up to ~2 s on Windows. The new client's connect() opens the same
        # file; without this sleep it immediately raises "database is locked".
        await asyncio.sleep(2.0)

        # ── Step 3: Connect with retry ────────────────────────────────────────
        await self._attempt_connect()

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        retry=retry_if_exception_type((
            ConnectionError,
            OSError,
            asyncio.TimeoutError,
            sqlite3.OperationalError,
            asyncio.IncompleteReadError,  # EOFError subclass; not caught by OSError
        )),
        before_sleep=before_sleep_log(logger, "WARNING"),
        reraise=True,
    )
    async def _attempt_connect(self) -> None:
        """
        Build a fresh TelegramClient and connect. Retried by tenacity.

        A new client instance is created on every attempt so that a
        failed connect() (which may leave the client in a bad internal
        state) never pollutes the next retry.

        On any exception we disconnect the failed client and wait an
        extra second before tenacity fires the next attempt, giving
        SQLite additional time to release the session file lock.

        CRITICAL: On success, calls loader.reattach() to re-register all
        module handlers on the new client. Without this the bot reconnects
        but stays "deaf" — unable to respond to any commands.
        """
        new_client = self._ac.rebuild()
        try:
            # Connect (raises OSError / sqlite3.OperationalError on lock)
            await asyncio.wait_for(new_client.connect(), timeout=30.0)

            # Verify session is still valid
            authorized = await asyncio.wait_for(
                new_client.is_user_authorized(), timeout=10.0
            )
            if not authorized:
                # Auth failure is permanent — don't retry, surface immediately
                self._notify(connected=False)
                raise RuntimeError(
                    f"[Account{self._cfg.index}] Not authorized after reconnect "
                    "— session file may be invalid. Run add_account.py to re-login."
                )

            self._log.success(
                "[Account{}] Reconnected and authorized.",
                self._cfg.index,
            )

            # Re-register all module handlers on the new client
            self._loader.reattach(new_client)

            # Reset failure counter and notify subscribers
            self._consecutive_failures = 0
            self._notify(connected=True)

        except Exception:
            # Clean up the failed client so the NEXT attempt starts fresh.
            # The 1 s extra sleep here stacks with tenacity's own wait,
            # giving SQLite more room to release the file lock.
            try:
                await asyncio.wait_for(new_client.disconnect(), timeout=3.0)
            except Exception:
                pass
            await asyncio.sleep(1.0)
            raise


# ── Public API ───────────────────────────────────────────────────────────────

__all__ = [
    "AccountReconnector",
    "NetworkState",
    "detect_network_state",
]
