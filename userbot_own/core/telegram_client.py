"""
userbot_own/core/telegram_client.py  (was core/client.py)
════════════════════════════════════════════════════════════════
TelegramClient Factory — Direct Connection Only

Builds TelegramClient instances for each account using direct connection
to Telegram servers. No proxy support — for bypassing restrictions,
use system-level VPN (WireGuard, OpenVPN, V2Ray) on Termux or Windows.

Client tuning (applied to all accounts):
• flood_sleep_threshold=0.5 — proactive pre-FloodWait delays
• request_retries=3         — resilience against transient network errors
• retry_delay=1.0           — delay between retries
• catch_up=True             — fetch missed updates on reconnect
• auto_reconnect=False      — let our reconnector own reconnection
• device_model="Userbot"    — custom device identification

Optimized for Termux (Android) and Windows environments.

Public API:
    AccountClient(cfg) → client factory wrapper
        .build()        → TelegramClient (first build)
        .rebuild()      → TelegramClient (rebuild after disconnect)
        .client         → current TelegramClient instance
        .cfg            → AccountConfig

NOTE: the original core/client.py re-implemented its own VERSION file
reader (`_read_version()`) with a fallback ("1.9.1") that had already
drifted from userbot/__init__.py's fallback ("2.0.0") by the time of
this refactor. That duplicate has been removed — SYSTEM_VERSION/
APP_VERSION now import the single canonical `userbot.__version__`.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import logging

from telethon import TelegramClient
from telethon.network.connection import ConnectionTcpFull

from userbot_own import __version__ as _BOT_VERSION
from userbot_own.config.models import AccountConfig

log = logging.getLogger(__name__)


# ── Client tuning constants ──────────────────────────────────────────────────

#: Seconds before triggering a FloodWait sleep. Setting this to a small
#: positive value makes Telethon proactively throttle requests before
#: Telegram sends a FloodWait error.
FLOOD_SLEEP_THRESHOLD: float = 0.5

#: Number of automatic retries on transient network failures.
#: Increased to 3 for better resilience on mobile networks (Termux).
REQUEST_RETRIES: int = 3

#: Seconds to wait between retries.
RETRY_DELAY: float = 1.0

#: Timeout for individual requests in seconds.
REQUEST_TIMEOUT: float = 15.0

#: Custom device model shown in Telegram's "Active Sessions" list.
DEVICE_MODEL: str = "Userbot"

#: Custom system version shown in Telegram's "Active Sessions" list.
#: Read from the single canonical userbot.__version__.
SYSTEM_VERSION: str = _BOT_VERSION

#: Custom app version shown in Telegram's "Active Sessions" list.
APP_VERSION: str = _BOT_VERSION

#: Fetch missed updates on reconnect.
CATCH_UP: bool = True


# ── Connection info builder ──────────────────────────────────────────────────

def _build_connection_kwargs(cfg: AccountConfig) -> dict:
    """
    Build the kwargs dict for TelegramClient constructor.

    Uses direct connection (ConnectionTcpFull) with optimized settings
    for mobile and desktop environments.

    Returns:
        Dict ready to be unpacked into TelegramClient(**kwargs).
    """
    return {
        "session":               cfg.session_path,
        "api_id":                cfg.api_id,
        "api_hash":              cfg.api_hash,
        "connection":            ConnectionTcpFull,
        "flood_sleep_threshold": FLOOD_SLEEP_THRESHOLD,
        "request_retries":       REQUEST_RETRIES,
        "retry_delay":           RETRY_DELAY,
        "timeout":               REQUEST_TIMEOUT,
        "device_model":          DEVICE_MODEL,
        "system_version":        SYSTEM_VERSION,
        "app_version":           APP_VERSION,
        "catch_up":              CATCH_UP,
        "auto_reconnect":        False,  # Let our reconnector own reconnection
    }


# ── AccountClient wrapper ────────────────────────────────────────────────────

class AccountClient:
    """
    Per-account TelegramClient factory and lifecycle wrapper.

    Usage::

        >>> ac = AccountClient(cfg)
        >>> client = ac.build()           # first build
        >>> # ... run the bot ...
        >>> client = ac.rebuild()         # after disconnect

    The wrapper keeps a reference to the current client so that other
    subsystems (reconnector, loader, watchers) can always access it
    via ``ac.client``.

    Attributes:
        cfg:    Immutable AccountConfig for this account.
        client: Current TelegramClient instance (None before first build).
    """

    def __init__(self, cfg: AccountConfig) -> None:
        self.cfg: AccountConfig = cfg
        self.client: TelegramClient | None = None

    def build(self) -> TelegramClient:
        """
        Build a new TelegramClient for this account using direct connection.

        Returns:
            The newly created TelegramClient (also stored in self.client).
        """
        kwargs = _build_connection_kwargs(self.cfg)
        self.client = TelegramClient(**kwargs)
        log.info(
            "[Account%d] Client built — direct connection.",
            self.cfg.index,
        )
        return self.client

    def rebuild(self) -> TelegramClient:
        """
        Rebuild the client after a disconnect.

        NOTE: The reconnector is responsible for calling disconnect() on the
        old client BEFORE calling rebuild(). This method simply clears the
        old reference and builds a fresh client.

        This avoids the sync-in-async bug where disconnect() was called
        synchronously from an async context.

        Returns:
            The newly created TelegramClient (also stored in self.client).
        """
        # Clear old reference (reconnector already disconnected it)
        self.client = None
        return self.build()


# ── Helper: build a one-shot client ──────────────────────────────────────────

def build_temp_client(cfg: AccountConfig) -> TelegramClient:
    """
    Build a throwaway TelegramClient for one-off operations
    (e.g. admin ID resolution at startup).

    The caller is responsible for connecting, using, and disconnecting
    this client. It does NOT affect the main AccountClient instance.

    Returns:
        A fresh TelegramClient with direct connection settings.
    """
    kwargs = _build_connection_kwargs(cfg)
    return TelegramClient(**kwargs)


# ── Public API ───────────────────────────────────────────────────────────────

__all__ = [
    "AccountClient",
    "build_temp_client",
    # Tuning constants (exported for inspection/override)
    "FLOOD_SLEEP_THRESHOLD",
    "REQUEST_RETRIES",
    "RETRY_DELAY",
    "REQUEST_TIMEOUT",
    "DEVICE_MODEL",
    "SYSTEM_VERSION",
    "APP_VERSION",
]
