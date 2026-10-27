"""
userbot_own/core/telegram_client.py  (was core/client.py)
════════════════════════════════════════════════════════════════
TelegramClient Factory

Builds TelegramClient instances for each account. v3.2.7 adds the
system-wide connection-mode selection: "direct" (default, the exact
pre-3.2.7 behavior), "socks5" (native Telethon proxy), and "cf"
(MTProto over WebSocket via Telegram's Cloudflare-fronted web
endpoints / a personal CF Worker). The mode is one system-wide
ConnectionSettings shared by every account and by the login/verify
clients in account_management/cli.py — never per-account. Selection
lives in core/connection_selector.py; this file only passes the
chosen transport through to Telethon.

Client tuning (applied to all accounts):
• flood_sleep_threshold=0.5 — proactive pre-FloodWait delays
• request_retries=3         — resilience against transient network errors
• retry_delay=1.0           — delay between retries
• catch_up=True             — fetch missed updates on reconnect
• auto_reconnect=False      — let our reconnector own reconnection
• device_model="Userbot"    — custom device identification

Optimized for Termux (Android) and Windows environments.

Public API:
    AccountClient(cfg, conn) → client factory wrapper
        .build()        → TelegramClient (first build)
        .rebuild()      → TelegramClient (rebuild after disconnect)
        .client         → current TelegramClient instance
        .cfg            → AccountConfig
        .conn           → ConnectionSettings

NOTE: the original core/client.py re-implemented its own VERSION file
reader (`_read_version()`) with a fallback ("1.9.1") that had already
drifted from userbot/__init__.py's fallback ("2.0.0") by the time of
this refactor. That duplicate has been removed — SYSTEM_VERSION/
APP_VERSION now import the single canonical `userbot.__version__`.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from telethon import TelegramClient
from telethon.network.connection import ConnectionTcpFull
from telethon.sessions import StringSession

from userbot_own import __version__ as _BOT_VERSION
from userbot_own.config.models import AccountConfig, ConnectionSettings
from userbot_own.core.connection_selector import connection_mode_kwargs
from userbot_own.core.logging_setup import get_logger

log = get_logger(__name__)


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

def _build_connection_kwargs(cfg: AccountConfig, conn: ConnectionSettings) -> dict:
    """
    Build the kwargs dict for TelegramClient constructor.

    Transport (v3.2.7): the system-wide ConnectionSettings decides
    direct / socks5 / cf via core/connection_selector.py's
    connection_mode_kwargs(). In "direct" mode (the default — and the
    fallback for every missing/inconsistent setting) the transport is
    exactly what 3.2.6 used, ConnectionTcpFull with no proxy kwarg.

    Session source (v3.1.3+): if cfg.session_string is set (populated by
    account_management/cli.py, either from a fresh login or from an
    imported string session), it takes priority and the client connects
    via telethon.sessions.StringSession — no local .session file is
    touched. Otherwise, falls back to the file-based session at
    cfg.session_path exactly as before. Accounts created before v3.1.3
    always have an empty session_string, so their behavior is unchanged.

    Returns:
        Dict ready to be unpacked into TelegramClient(**kwargs).
    """
    session = StringSession(cfg.session_string) if cfg.session_string else cfg.session_path
    kwargs = {
        "session":               session,
        "api_id":                cfg.api_id,
        "api_hash":              cfg.api_hash,
        # ConnectionTcpFull adds a sequence number and a CRC32 checksum to every
        # packet (ConnectionTcpAbridged sends only a length prefix), so a
        # corrupted packet on an unreliable link is detected rather than
        # silently mis-parsed. account_management/cli.py's short-lived
        # login/verify clients use Abridged — see the comment there.
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
    # v3.2.7: mode selection overrides the default ConnectionTcpFull
    # and may add a proxy kwarg (socks5). Direct mode reproduces the
    # pre-3.2.7 dict byte-for-byte.
    kwargs.update(connection_mode_kwargs(conn, default_connection=ConnectionTcpFull))
    return kwargs


# ── AccountClient wrapper ────────────────────────────────────────────────────

class AccountClient:
    """
    Per-account TelegramClient factory and lifecycle wrapper.

    Usage::

        >>> ac = AccountClient(cfg, conn)
        >>> client = ac.build()           # first build
        >>> # ... run the bot ...
        >>> client = ac.rebuild()         # after disconnect

    The wrapper keeps a reference to the current client so that other
    subsystems (reconnector, loader, watchers) can always access it
    via ``ac.client``.

    Attributes:
        cfg:    Immutable AccountConfig for this account.
        conn:   System-wide ConnectionSettings (v3.2.7) — never per-account.
        client: Current TelegramClient instance (None before first build).
    """

    def __init__(self, cfg: AccountConfig, conn: ConnectionSettings | None = None) -> None:
        self.cfg: AccountConfig = cfg
        # v3.2.7: None → direct (pre-3.2.7 behavior) so every existing
        # call site and test that constructs AccountClient(cfg) keeps
        # working unchanged; composition_root passes the real settings.
        self.conn: ConnectionSettings = conn or ConnectionSettings.direct_default()
        self.client: TelegramClient | None = None

    def build(self) -> TelegramClient:
        """
        Build a new TelegramClient for this account using the configured
        connection mode (direct / socks5 / cf — see connection_selector).

        Returns:
            The newly created TelegramClient (also stored in self.client).
        """
        kwargs = _build_connection_kwargs(self.cfg, self.conn)
        self.client = TelegramClient(**kwargs)
        session_source = "string session" if self.cfg.session_string else "file session"
        log.info(
            "[Account%d] Client built — %s (%s).",
            self.cfg.index,
            self.conn.describe(),
            session_source,
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


# v3.2.2 (B-22): `build_temp_client()` was removed. It had ZERO call sites
# anywhere in the project — its docstring's "e.g. admin ID resolution at
# startup" described a feature that no longer exists (the admin system was
# removed long before the DI refactor).


# ── Public API ───────────────────────────────────────────────────────────────

__all__ = [
    "AccountClient",
    # Tuning constants (exported for inspection/override)
    "FLOOD_SLEEP_THRESHOLD",
    "REQUEST_RETRIES",
    "RETRY_DELAY",
    "REQUEST_TIMEOUT",
    "DEVICE_MODEL",
    "SYSTEM_VERSION",
    "APP_VERSION",
]
