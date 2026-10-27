"""
userbot_own/core/connection_selector.py  (new in v3.2.7)
════════════════════════════════════════════════════════════════
Connection-mode selection — the single place that turns a
ConnectionSettings instance into Telethon constructor arguments.

Phase wiring (v3.2.7):
  • "direct" — ConnectionTcpFull / ConnectionTcpAbridged, no proxy
      (byte-for-byte the pre-3.2.7 behavior; this is the default and
      the fallback whenever anything is missing or inconsistent).
  • "socks5" — same TCP transports, Telethon's native `proxy=` tuple
      routed through a SOCKS5 server (python-socks preferred, PySocks
      fallback — Telethon picks automatically).  Phase 2.
  • "cf"     — MTProto over WebSocket via Telegram's own
      *.web.telegram.org endpoints (behind Cloudflare), or via a
      personal Cloudflare Worker when one is configured.  Phase 3/4.

Every client this process ever builds — the long-lived per-account
clients (core/telegram_client.py) AND the short-lived login/verify
clients (account_management/cli.py) — asks THIS module which transport
to use. There is deliberately no per-account connection config: the
owner's design decision is one system-wide mode for everything, per
the ConnectionSettings docstring in config/models.py.

The cf-specific connection classes live in core/connection_ws.py:
a dependency-free RFC 6455 WebSocket client (WsStream) plus Telethon
ObfuscatedConnection subclasses that reuse Telethon's own
ObfuscatedIO + AbridgedPacketCodec verbatim — only the raw socket is
swapped for the WebSocket channel.

Public API:
    connection_mode_kwargs(conn, default_connection) → dict fragment
        ("connection" and optionally "proxy" kwargs)
    probe_host_port(conn) → (host, port)
        what the reconnector's cheap probes should test in this mode
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from telethon.network.connection import ConnectionTcpFull

from userbot_own.config.models import ConnectionSettings

# Phase 3 import (v3.2.7): core/connection_ws.py is the WebSocket
# transport — minimal RFC 6455 client + Telethon ObfuscatedConnection
# subclasses. It is dependency-free, so importing it eagerly is safe
# for "direct" mode too.
from userbot_own.core.connection_ws import (
    make_ws_connection_class,
    ws_probe_target,
)


def connection_mode_kwargs(
    conn: ConnectionSettings,
    default_connection: type | None = None,
) -> dict:
    """
    Build the Telethon constructor kwargs for the given connection mode.

    Args:
        conn: The system-wide ConnectionSettings (never per-account).
        default_connection: The transport class the caller would have
            used in direct mode (core/telegram_client.py passes
            ConnectionTcpFull; account_management/cli.py passes
            ConnectionTcpAbridged). Only actually used in "direct"
            and "socks5" modes — "cf" has its own WebSocket transport.

    Returns:
        A dict with at least a "connection" key; "socks5" mode adds a
        "proxy" key. Unpack with TelegramClient(**{**base, **frag}).
    """
    if conn.is_socks5():
        # Telethon's positional proxy-tuple format, as unpacked by
        # Connection._proxy_connect() → _parse_proxy():
        # (proxy_type, addr, port, rdns, username, password).
        # proxy_type accepts "socks5" (or 2); rdns=True resolves the
        # target hostname through the proxy, which is what we want for
        # Telegram's DC hostnames. Telethon prefers python-socks when
        # installed and falls back to PySocks automatically.
        proxy = (
            "socks5",
            conn.socks_host,
            conn.socks_port,
            True,
            conn.socks_user or None,
            conn.socks_pass or None,
        )
        return {
            "connection": default_connection or ConnectionTcpFull,
            "proxy": proxy,
        }

    if conn.is_cf():
        # Phase 3 (v3.2.7): MTProto over WebSocket — via the personal
        # Cloudflare Worker when one is configured, directly to
        # Telegram's own *.web.telegram.org endpoints otherwise.
        # `default_connection` is ignored on purpose: cf has its own
        # transport (obfuscated2-over-WS reuses Telethon's stack, but
        # the socket layer is WsStream, not a TCP transport class).
        return {"connection": make_ws_connection_class(conn)}

    # "direct" — pre-3.2.7 behavior exactly, whether by choice or by
    # fallback (socks5 selected but host empty → treated as direct).
    return {"connection": default_connection or ConnectionTcpFull}


def probe_host_port(conn: ConnectionSettings) -> tuple[str, int]:
    """
    The (host, port) the reconnector's cheap connectivity probes should
    test for THIS connection mode.

    "direct"/"socks5": Telegram's own DC2 IP (unchanged since pre-3.2.7,
    probe semantics identical — for socks5 the raw connect is still the
    right signal for "can we reach the internet at all", and Telethon's
    own connect retries handle the proxy hop).

    "cf": the WebSocket endpoint the client will actually dial — the
    personal Worker's edge when one is configured, Telegram's own
    web endpoint otherwise (ws_probe_target in connection_ws.py).

    Telethon's Connection passes its probe destination as a (host, port)
    pair through to asyncio.open_connection (see Connection._connect),
    so both modes return exactly what asyncio can dial.
    """
    # None → direct: pre-3.2.7 callers (and tests) that pass no mode at
    # all must keep the old probe behavior, not an AttributeError.
    if conn is None:
        return ("149.154.167.50", 443)

    if conn.is_cf():
        # Phase 3: probe exactly what the client will dial — the
        # Worker's edge (what must be reachable) or Telegram's own WS
        # endpoint when no Worker is configured.
        return ws_probe_target(conn)

    # Direct/proxy TCP modes probe Telegram's DC2 IP on 443 (pre-3.2.7
    # behavior — same constant reconnector.py has always used).
    return ("149.154.167.50", 443)
