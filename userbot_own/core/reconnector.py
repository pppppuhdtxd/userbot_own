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

────────────────────────────────────────────────────────────────
v3.1.1 — Fast Reconnect (instant detection, interruptible backoff)
────────────────────────────────────────────────────────────────
Prior to this version, both halves of reconnection were pure polling:

- Detecting a drop could take up to _HEALTHY_INTERVAL (30s) — the loop
  only noticed a dead connection on its next scheduled check.
- Detecting that the network had come *back* was worse: once inside a
  NO_INTERNET/TELEGRAM_DOWN backoff, the wait was a plain
  `asyncio.sleep(backoff)` with no early exit — even if connectivity
  returned 1 second into a 256-second sleep, nothing woke the loop up
  until the full sleep elapsed. This was the "dead zone" reported in
  the v3.1.1 investigation.

Two additive mechanisms fix this without touching the backoff *ceilings*,
the 2-consecutive-failures escalation threshold, FloodWait handling, or
`loader.reattach()` — all of which were already correct:

1. Instant disconnect detection — `_watch_disconnected()` awaits
   Telethon's own `client.disconnected` Future (resolves the instant the
   sender detects a dead socket — no polling involved) and sets
   `self._disconnect_event`. The healthy-interval sleep in
   `_reconnect_cycle()` now races a plain timer against that event via
   `_interruptible_healthy_wait()`, so a drop interrupts the wait
   immediately instead of waiting up to 30s. The watcher is (re)started
   every time a client is (re)built, so it always tracks the *current*
   client.

2. Interruptible backoff — `_interruptible_backoff()` replaces the three
   plain `asyncio.sleep(backoff)` calls (in `_handle_no_internet()`,
   `_handle_telegram_down()`, and `_handle_online()`'s `RetryError`
   fallback) with a wait that periodically probes connectivity via a
   cheap raw TCP connect (identical to the TCP-connect step of
   `detect_network_state()`, deliberately NOT a Telegram API call, so it
   carries zero FloodWait risk) and returns the instant the probe
   succeeds. The backoff ceiling itself is untouched — this only ever
   *shortens* the wait, never lengthens it, so behavior during a
   genuinely prolonged outage is unchanged.

Both mechanisms are gated by `FAST_RECONNECT_ENABLED` (default: on) and
their intervals are configurable via environment variables, so v3.1.0's
pure-polling behavior can be restored with a single setting if the fast
path misbehaves on a given device:

    FAST_RECONNECT_ENABLED             default: true
    FAST_RECONNECT_HEALTHY_INTERVAL    default: 30   (seconds)
    FAST_RECONNECT_PROBE_INTERVAL      default: 3    (seconds)

These are read directly from the environment by this module (see
`_env_bool()` / `_env_float()` below) rather than routed through
`config/models.py`'s `Settings` object — this keeps the feature fully
functional via plain environment variables / `.env` with no changes
required to `config/loader.py` or `app/composition_root.py`. Matching
fields were added to `Settings` for discoverability/documentation
alongside the other env-configurable values there; see that file's
docstring for the note on why they are not (yet) wired through it.

A `_recovering` guard was also added around `_recover_connection()`. In
the current code this is defense-in-depth rather than a fix for an
observed bug — `_reconnect_cycle()` only ever calls `_recover_connection()`
from within its own single sequential loop iteration, so there was no
actual concurrent-invocation path even before this version. The guard
protects against that changing in the future (e.g. if the disconnect
watcher is ever wired to trigger recovery directly instead of merely
signalling the loop) without changing any currently-observable behavior.

────────────────────────────────────────────────────────────────
v3.1.9 — Logging fix (per-account attribution + file persistence)
────────────────────────────────────────────────────────────────
Prior to this version, `self._log` here was a raw `loguru.logger.bind(...)`
call with no `name` key in its bound `extra`. `core/logging_setup.py`'s
file sinks (both `main.log` and every per-account file) require
`"name" in record["extra"]` to accept a record at all — so almost every
log call in this file (all of `run()`, `_recover_connection()`, the
NO_INTERNET / TELEGRAM_DOWN / ONLINE handlers, `_attempt_connect()`'s
success path, etc.) was silently dropped from every log file, and only
ever reached the console at WARNING+, indistinguishable there from
genuine third-party library noise. Reconnection is the single most
important thing to have a durable, on-disk record of for a Termux
deployment nobody is watching live — this was a significant,
unintentional loss of observability.

Fixed by switching `self._log` to `logging_setup.get_logger()` (this
project's own factory, used everywhere else) and wrapping `run()` in
`logger.contextualize(account=self._cfg.index)` for its entire lifetime.
The manual `"[Account{}] "` text this file used to prepend to every
message is removed — the bound logger name and the `contextualize()`
context now carry that information structurally instead of as a string
the reader has to parse out, and `add_account_handler()`'s per-account
file filter checks `record["extra"]["account"] == idx` exactly rather
than a fragile substring test. See CHANGELOG v3.1.9 for the fuller
before/after picture, including the multi-account log-file-leak bug
this also fixes.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import os
import socket
import sqlite3
from enum import Enum, auto
from typing import TYPE_CHECKING

from loguru import logger as _loguru_core
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

from userbot_own.core.logging_setup import get_logger

if TYPE_CHECKING:
    from telethon import TelegramClient

    from userbot_own.core.loader import AccountLoader
    from userbot_own.core.telegram_client import AccountClient


# ── Fast-reconnect environment configuration (v3.1.1) ────────────────────────
#
# Read once at import time, same pattern as the pre-existing module-level
# constants below.

def _env_float(key: str, default: float) -> float:
    """Read *key* from the environment as a float, or *default* on any error."""
    try:
        raw = os.environ.get(key, "").strip()
        return float(raw) if raw else default
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    """Read *key* from the environment as a bool, or *default* if unset/unrecognized."""
    raw = os.environ.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


#: Master switch for the v3.1.1 fast-reconnect behavior (instant disconnect
#: detection + interruptible backoff probing). Set to false to fall back to
#: v3.1.0's pure-polling behavior unchanged.
_FAST_RECONNECT_ENABLED: bool = _env_bool("FAST_RECONNECT_ENABLED", True)

#: Interval when connection is healthy (seconds).
_HEALTHY_INTERVAL: float = _env_float("FAST_RECONNECT_HEALTHY_INTERVAL", 30.0)

#: Minimum interval when degraded (seconds). Unrelated to the fast-reconnect
#: feature — unchanged from prior versions.
_MIN_DEGRADED_INTERVAL: float = 5.0

#: How often the interruptible backoff wait probes connectivity during a
#: NO_INTERNET / TELEGRAM_DOWN / post-RetryError backoff (seconds). A raw
#: TCP connect, never a Telegram API call — see _cheap_probe_online().
_PROBE_INTERVAL: float = _env_float("FAST_RECONNECT_PROBE_INTERVAL", 3.0)


# ── Network State Detection ──────────────────────────────────────────────────

class NetworkState(Enum):
    """Current state of network connectivity."""
    ONLINE = auto()        # Everything works
    NO_INTERNET = auto()   # No internet at all
    TELEGRAM_DOWN = auto() # Internet works but Telegram unreachable
    UNKNOWN = auto()       # Couldn't determine


#: Known Telegram DC IP used for both detect_network_state()'s TCP-connect
#: step and the v3.1.1 fast-reconnect probe (_cheap_probe_online()) — kept
#: as one constant so the two only ever test the same endpoint.
_TELEGRAM_PROBE_HOST: str = "149.154.167.50"
_TELEGRAM_PROBE_PORT: int = 443


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
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(_TELEGRAM_PROBE_HOST, _TELEGRAM_PROBE_PORT),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return NetworkState.ONLINE
    except (TimeoutError, OSError, ConnectionRefusedError):
        return NetworkState.TELEGRAM_DOWN
    except Exception:
        return NetworkState.UNKNOWN


async def _cheap_probe_online(timeout: float = 3.0) -> bool:
    """
    v3.1.1 — Cheap connectivity probe used only inside interruptible backoff
    waits, to detect restoration early.

    Deliberately a raw TCP connect to the same Telegram DC endpoint
    `detect_network_state()` uses for its second step — NOT a Telegram API
    call (no MTProto handshake, no GetStateRequest), so repeating it every
    `_PROBE_INTERVAL` seconds during an outage carries zero FloodWait risk.
    Skips the DNS-lookup step `detect_network_state()` does, since here we
    only need a binary online/offline signal, not which failure category —
    `_recover_connection()`'s next full cycle will re-classify NO_INTERNET
    vs TELEGRAM_DOWN properly once backoff exits.

    Returns:
        True if the TCP connect succeeded, False otherwise.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(_TELEGRAM_PROBE_HOST, _TELEGRAM_PROBE_PORT),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


# v3.1.9: module-level logger used only by the @retry decorator's
# `before_sleep_log` below. That decorator is evaluated at class-
# definition time, before any AccountReconnector instance (and
# therefore any self._log) exists, so it needs its own logger created
# through this project's factory rather than the raw loguru `logger`
# singleton this file used to import for that purpose.
_tenacity_retry_log = get_logger(__name__)


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

    v3.1.1 additionally runs a lightweight background watcher
    (`_watch_disconnected`) that awaits Telethon's own `client.disconnected`
    Future so drops are noticed the instant they happen rather than on the
    next scheduled poll, and races that signal (plus a cheap TCP probe
    during backoff waits) against the existing timers so restoration is
    noticed within `_PROBE_INTERVAL` seconds instead of up to the full
    backoff ceiling. See the module docstring's "v3.1.1" section for the
    full design rationale.

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

        # v3.1.1: guards against a concurrent second call to
        # _recover_connection() while one is already in flight. Not fixing
        # an observed bug today (see module docstring) — defense-in-depth
        # now that a second signal source (the disconnect watcher) exists
        # alongside the main loop.
        self._recovering: bool = False

        # v3.1.1: set the instant client.disconnected resolves; consumed by
        # _interruptible_healthy_wait() to interrupt the healthy-interval
        # sleep immediately instead of waiting out the full interval.
        self._disconnect_event: asyncio.Event = asyncio.Event()
        self._disconnect_watch_task: asyncio.Task | None = None

        # v3.1.9: use the project's logger factory instead of a raw
        # loguru `logger.bind()` call — see the module docstring's
        # "v3.1.9" section for why the old form silently never reached
        # any log file. Account attribution for the per-account file
        # filter comes from `logger.contextualize(account=...)`,
        # entered around run() below (and, more broadly, around
        # composition_root.start_account()) — not from this name — so
        # it stays correct regardless of what this string contains.
        self._log = get_logger(f"reconnector.account{self._cfg.index}")

    def _notify(self, connected: bool) -> None:
        """Log a connection-state transition for this account."""
        self._log.info("Connection state changed: connected=%s", connected)

    async def run(self) -> None:
        """
        Main reconnect loop. Runs until cancelled.

        This is typically started as an async task by the composition root:
            asyncio.create_task(reconnector.run())

        v3.1.9: the entire body runs inside
        `logger.contextualize(account=self._cfg.index)` so every log
        record emitted anywhere in this call tree — including from
        `_attempt_connect()`'s tenacity retry callback and anything a
        reattached module logs during recovery — carries the exact
        account index, and `add_account_handler()`'s per-account file
        filter can match on it precisely instead of guessing from a
        name string.
        """
        self._running = True
        with _loguru_core.contextualize(account=self._cfg.index):
            self._log.info("Reconnector started.")

            # v3.1.1: if a client already exists (composition_root connects
            # the initial client before starting the reconnector), start
            # watching it for an instant disconnect signal right away
            # rather than only after the first rebuild.
            if self._ac.client is not None:
                self._start_disconnect_watcher(self._ac.client)

            while self._running:
                try:
                    await self._reconnect_cycle()
                except asyncio.CancelledError:
                    self._log.info("Reconnector cancelled.")
                    break
                except Exception as exc:
                    self._log.exception("Reconnector cycle error: %s", exc)
                    await asyncio.sleep(5)

            self._cancel_disconnect_watcher()
            self._log.info("Reconnector stopped.")

    def stop(self) -> None:
        """Signal the reconnect loop to stop."""
        self._running = False

    def is_connected(self) -> bool:
        """Check if the client is currently connected."""
        client = self._ac.client
        return client is not None and client.is_connected()

    # ── v3.1.1: instant disconnect watcher ──────────────────────────────────

    def _start_disconnect_watcher(self, client: TelegramClient) -> None:
        """
        (Re)start the background task that watches `client.disconnected`.

        Called once at reconnector startup (for the initial client) and
        again every time `_attempt_connect()` builds a fresh client, so the
        watcher always tracks the *current* client instance. Clears
        `_disconnect_event` before starting so a stale signal from a
        previous (now-replaced) client can never leak into the new watch
        period — the event is otherwise never cleared elsewhere, which
        avoids a race where a genuine pending disconnect signal could be
        wiped out by a wait loop that clears-then-waits (see module
        docstring for why this ordering matters).
        """
        self._cancel_disconnect_watcher()
        self._disconnect_event.clear()
        self._disconnect_watch_task = asyncio.create_task(
            self._watch_disconnected(client),
            name=f"reconnect_watch_a{self._cfg.index}",
        )

    def _cancel_disconnect_watcher(self) -> None:
        """Cancel and clear the current disconnect-watch task, if any."""
        task = self._disconnect_watch_task
        if task is not None and not task.done():
            task.cancel()
        self._disconnect_watch_task = None

    async def _watch_disconnected(self, client: TelegramClient) -> None:
        """
        Await Telethon's own `client.disconnected` Future.

        This resolves the instant the sender detects the socket is gone —
        no polling, no timeout, genuinely event-driven. Telethon's docs
        show it can resolve with an exception (e.g. OSError) as well as
        cleanly; either way, a resolution means "this client is no longer
        connected", which is all `_disconnect_event` needs to communicate.
        """
        if not _FAST_RECONNECT_ENABLED:
            return
        try:
            await client.disconnected
        except asyncio.CancelledError:
            return
        except Exception:
            # Any resolution (clean or exceptional) of client.disconnected
            # means the same thing here: the client is no longer connected.
            pass
        self._disconnect_event.set()

    async def _interruptible_healthy_wait(self) -> None:
        """
        v3.1.1 — Wait `_HEALTHY_INTERVAL` seconds, same as before, but wake
        immediately if `_disconnect_event` fires in the meantime.

        Falls back to a plain `asyncio.sleep(_HEALTHY_INTERVAL)` — byte-for-
        byte the v3.1.0 behavior — when `FAST_RECONNECT_ENABLED` is false.

        Waking early here does not itself trigger recovery: it simply
        returns control to `_reconnect_cycle()`, whose very next iteration
        re-checks `client.is_connected()` (Step 1) and will correctly route
        into `_recover_connection()` on its own. This deliberately reuses
        the existing, already-correct escalation path instead of adding a
        second one.
        """
        if not _FAST_RECONNECT_ENABLED:
            await asyncio.sleep(_HEALTHY_INTERVAL)
            return

        sleep_task = asyncio.ensure_future(asyncio.sleep(_HEALTHY_INTERVAL))
        event_task = asyncio.ensure_future(self._disconnect_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {sleep_task, event_task}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for t in (sleep_task, event_task):
                if not t.done():
                    t.cancel()
            # Swallow cancellation of whichever task didn't win the race.
            await asyncio.gather(sleep_task, event_task, return_exceptions=True)

        if event_task in done:
            self._log.info(
                "Instant disconnect signal received — "
                "skipping remainder of healthy-interval wait."
            )

    async def _interruptible_backoff(self, backoff_seconds: float) -> None:
        """
        v3.1.1 — Wait up to `backoff_seconds`, same ceiling as before, but
        wake early the moment a cheap background probe reports connectivity
        is back.

        This never waits *longer* than the original fixed sleep would have
        — it only ever shortens it — so behavior during a genuinely
        prolonged outage (probe keeps failing) is unchanged from v3.1.0.
        Falls back to a plain `asyncio.sleep(backoff_seconds)` when
        `FAST_RECONNECT_ENABLED` is false, or when the backoff itself is
        already shorter than one probe interval (nothing to gain by
        probing).

        Args:
            backoff_seconds: The full backoff duration computed by the
                caller (_handle_no_internet / _handle_telegram_down / the
                RetryError fallback in _handle_online) — unchanged formulas.
        """
        if not _FAST_RECONNECT_ENABLED or backoff_seconds <= _PROBE_INTERVAL:
            await asyncio.sleep(backoff_seconds)
            return

        elapsed = 0.0
        while elapsed < backoff_seconds:
            step = min(_PROBE_INTERVAL, backoff_seconds - elapsed)
            await asyncio.sleep(step)
            elapsed += step
            if elapsed >= backoff_seconds:
                return
            if await _cheap_probe_online():
                self._log.info(
                    "Connectivity probe succeeded during backoff — waking "
                    "early (%.1fs of %.1fs skipped).",
                    backoff_seconds - elapsed, backoff_seconds,
                )
                return

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
            self._log.warning("Client disconnected — triggering recovery.")
            await self._recover_connection()
            return

        # Step 2: Verify connection with lightweight API call
        try:
            await asyncio.wait_for(client(GetStateRequest()), timeout=10.0)

            # Connection is healthy — reset failure counter, use long
            # interval (v3.1.1: now interruptible — see docstring above).
            self._consecutive_failures = 0
            await self._interruptible_healthy_wait()
            return

        except TimeoutError:
            self._log.warning("Connection verification timeout.")
            self._consecutive_failures += 1

        except errors.AuthKeyError:
            self._log.error("Auth key error — session may be invalid.")
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
                "Incomplete MTProto frame read (%d of %d bytes) — "
                "connection dropped mid-frame.",
                partial_bytes, exc.expected,
            )
            self._consecutive_failures += 1

        except (errors.FloodWaitError, OSError, ConnectionError) as exc:
            self._log.warning("Connection verification failed: %s", exc)
            self._consecutive_failures += 1

            # Special handling for FloodWait
            if isinstance(exc, errors.FloodWaitError):
                self._log.warning("FloodWait %ss — waiting.", exc.seconds)
                await asyncio.sleep(exc.seconds + 5)
                return

        except Exception as exc:
            self._log.error("Unexpected verification error: %s", exc)
            self._consecutive_failures += 1

        # Step 3: Decide recovery action based on failure count
        if self._consecutive_failures >= 2:
            self._log.warning(
                "%d consecutive failures — triggering recovery.",
                self._consecutive_failures,
            )
            await self._recover_connection()
        else:
            # Minor issue — use adaptive shorter interval for faster detection
            # Formula: 30s / (failures+1), but at least 5s
            # failures=1 → 15s, failures=2 → recovery (handled above)
            # v3.1.1: intentionally NOT routed through the interruptible
            # wait — this interval is already short (≤15s) and this path
            # is a one-cycle grace period before escalation, not a
            # multi-minute backoff, so there is little to gain and it
            # keeps this branch's behavior identical to v3.1.0.
            interval = max(
                _MIN_DEGRADED_INTERVAL,
                _HEALTHY_INTERVAL / (self._consecutive_failures + 1),
            )
            self._log.debug("Minor issue — re-checking in %.1fs.", interval)
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

        v3.1.1: guarded by `_recovering` so a second, concurrent call (e.g.
        from a future direct-trigger use of the disconnect watcher) can't
        race this one — see module docstring. Under the current call
        pattern (always sequential, from within `_reconnect_cycle()`'s own
        loop iteration) this guard should never actually trigger; it logs
        at debug level if it ever does.
        """
        if self._recovering:
            self._log.debug(
                "Recovery already in progress — skipping duplicate trigger."
            )
            return

        self._recovering = True
        try:
            self._log.info("Starting connection recovery...")

            # Detect network state
            state = await detect_network_state()
            self._last_state = state

            self._log.info("Network state detected: %s", state.name)

            if state == NetworkState.NO_INTERNET:
                await self._handle_no_internet()
            elif state == NetworkState.TELEGRAM_DOWN:
                await self._handle_telegram_down()
            elif state == NetworkState.ONLINE:
                await self._handle_online()
            else:  # UNKNOWN
                self._log.warning("Unknown network state — attempting reconnect.")
                await self._handle_online()
        finally:
            self._recovering = False

    async def _handle_no_internet(self) -> None:
        """Handle NO_INTERNET state — wait with exponential backoff."""
        # Calculate backoff: 1s → 2s → 4s → 8s → ... → 300s max.
        # Cap _consecutive_failures at 8 before the exponent to avoid
        # integer overflow (2**300 is a valid Python int but meaningless here).
        backoff = min(2 ** min(self._consecutive_failures, 8), 300)

        self._log.warning(
            "No internet connection detected. Waiting %ss before retry...",
            backoff,
        )
        self._notify(connected=False)
        self._consecutive_failures += 1
        # v3.1.1: interruptible — wakes early the instant a probe succeeds,
        # never waits longer than `backoff` would have on its own.
        await self._interruptible_backoff(backoff)

    async def _handle_telegram_down(self) -> None:
        """Handle TELEGRAM_DOWN state — wait longer, Telegram is having issues."""
        backoff = min(60 + self._consecutive_failures * 30, 300)

        self._log.warning(
            "Internet works but Telegram is unreachable. "
            "Waiting %ss before retry...",
            backoff,
        )
        self._notify(connected=False)
        self._consecutive_failures += 1
        # v3.1.1: interruptible — see _handle_no_internet() above.
        await self._interruptible_backoff(backoff)

    async def _handle_online(self) -> None:
        """Handle ONLINE state — rebuild client with tenacity retry."""
        self._log.info("Internet available. Rebuilding client...")

        try:
            await self._rebuild_client_with_retry()
            # _attempt_connect() resets _consecutive_failures to 0 on success.
        except RetryError as exc:
            self._log.error("All reconnect attempts failed: %s", exc)
            self._consecutive_failures += 1
            # Wait before next cycle
            # v3.1.1: interruptible — see _handle_no_internet() above.
            await self._interruptible_backoff(min(30 * self._consecutive_failures, 300))

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
                self._log.debug("Old client disconnected.")
            except Exception as exc:
                self._log.warning(
                    "Error disconnecting old client: %s — forcing closure.", exc
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
        # v3.1.9: `_tenacity_retry_log` (module-level, created via this
        # project's own get_logger() factory) replaces the raw loguru
        # `logger` singleton previously passed here — same reasoning as
        # the rest of this file's v3.1.9 change (see module docstring).
        # This decorator runs at class-definition time, before any
        # AccountReconnector instance (and therefore any self._log)
        # exists, so it needs its own module-level logger rather than
        # an instance attribute.
        before_sleep=before_sleep_log(_tenacity_retry_log, "WARNING"),
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

            self._log.success("Reconnected and authorized.")

            # Re-register all module handlers on the new client
            self._loader.reattach(new_client)

            # v3.1.1: (re)start the instant-disconnect watcher on the new
            # client — it must always track whichever client is current.
            self._start_disconnect_watcher(new_client)

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
