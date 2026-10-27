"""
userbot_own/core/connection_ws.py  (new in v3.2.7, Phase 3)
════════════════════════════════════════════════════════════════
MTProto over WebSocket ("cf" connection mode).

Telegram's official web client reaches MTProto over a WebSocket
transport: the same "obfuscated2" framing as ConnectionTcpObfuscated
— a 64-byte obfuscation header followed by AES-CTR-encrypted
abridged packets — carried in binary WebSocket frames against

    wss://<dc-name>(-1).web.telegram.org/apiws

with the subprotocol header ``Sec-WebSocket-Protocol: binary``.

Why that helps on filtered networks: ``*.web.telegram.org`` sits on
Cloudflare's edge, so the TLS+WS handshake looks like ordinary HTTPS
to a Cloudflare front. When even that hostname is blocked (DNS
poisoned to raw Telegram IPs), the same transport works through the
owner's personal relay Worker (cloudflare/tg-relay-worker.js), which
forwards frames byte-for-byte:

    wss://<worker>.workers.dev/<dc-host>/apiws

Design — deliberately two small pieces on top of Telethon:

  • WsStream — a minimal RFC 6455 client (handshake + framing) that
    quacks like the asyncio reader/writer pair Telethon's Connection
    expects: readexactly() / write() / drain() / close() /
    wait_closed(). No third-party dependency, so cf mode works on a
    bare Python install (Termux included).

  • ConnectionWs* — subclasses of Telethon's ObfuscatedConnection
    that reuse its ObfuscatedIO (the obfuscated2 AES-CTR layer) and
    AbridgedPacketCodec verbatim, only swapping the raw TCP socket
    for WsStream. The crypto/frame layer is therefore Telethon's own
    battle-tested code; what is new here is transport only.

AES-CTR is a stream cipher, so WebSocket frame boundaries have no
meaning to MTProto — every write() becomes one binary frame, and
readexactly() just reassembles whatever the frames carry.

Public API (used by core/connection_selector.py):
    make_ws_connection_class(conn) → the Telethon connection class
        for the current settings: via the personal Worker when one
        is configured, directly to web.telegram.org otherwise.
    ws_probe_target(conn)      → (host, port) for the reconnector's
        cheap probes.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import ssl
import struct

from telethon.network.connection.connection import ObfuscatedConnection
from telethon.network.connection.tcpabridged import AbridgedPacketCodec
from telethon.network.connection.tcpobfuscated import ObfuscatedIO

from userbot_own.config.models import ConnectionSettings

# Telegram's official web endpoints: DC id → hostname prefix
# ("-1" raises the per-hostname simultaneous-connection limit and
# is what the official web client itself dials).
_DC_NAMES = {
    1: "pluto",
    2: "venus",
    3: "aurora",
    4: "vesta",
    5: "flora",
}
_WS_PORT = 443
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def dc_ws_host(dc_id: int) -> str:
    """Map a Telegram DC id to its official WebSocket endpoint host."""
    return f"{_DC_NAMES.get(dc_id, 'venus')}-1.web.telegram.org"


def ws_probe_target(conn: ConnectionSettings) -> tuple[str, int]:
    """
    What the reconnector's cheap probes should dial in cf mode: the
    host the client will actually connect to — the personal Worker
    when one is configured (its edge is what must be reachable),
    otherwise Telegram's own web endpoint.
    """
    if conn.cf_worker_domain:
        return (conn.cf_worker_domain, _WS_PORT)
    return (dc_ws_host(2), _WS_PORT)


def _xor_mask(data: bytes, key: bytes) -> bytes:
    """
    XOR `data` with the repeating 4-byte `key` (RFC 6455 masking),
    via a single wide-int XOR — fast even for MTProto-sized payloads
    (file uploads reach hundreds of KB per frame).
    """
    if not data:
        return data
    repeated = (key * ((len(data) + 3) // 4))[: len(data)]
    return (
        int.from_bytes(data, "little") ^ int.from_bytes(repeated, "little")
    ).to_bytes(len(data), "little")


class WsStream:
    """
    Minimal RFC 6455 WebSocket client over asyncio TLS, presenting
    the reader/writer surface Telethon's Connection machinery uses:

        readexactly(n) → bytes      (async, buffered across frames)
        write(data)                (sync, buffers one binary frame)
        drain()                    (async — flushes the transport)
        close() / wait_closed()    (graceful WS close, best-effort)

    Control frames are handled transparently inside readexactly():
    pings are answered with pongs, pongs ignored, close frames are
    echoed and surface as asyncio.IncompleteReadError — the exact
    signal Telethon's recv loop maps to "server closed the
    connection", so reconnects behave like any TCP drop.
    """

    def __init__(self, host: str, path: str, log=None):
        self._host = host
        self._path = path
        self._log = log
        self._tr_reader: asyncio.StreamReader | None = None
        self._tr_writer: asyncio.StreamWriter | None = None
        self._buf = bytearray()
        self._eof = False

    # ── connect / handshake ─────────────────────────────────────

    async def connect(self, timeout=None, local_addr=None) -> None:
        ctx = ssl.create_default_context()
        open_kw = {"ssl": ctx}
        if local_addr is not None:
            open_kw["local_addr"] = local_addr

        self._tr_reader, self._tr_writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, _WS_PORT, **open_kw),
            timeout=timeout,
        )

        # The handshake the official web client sends; the Origin
        # header matches what telegram.org's edge expects from
        # browsers (verified against the live endpoint 2026-10-25).
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {self._path} HTTP/1.1\r\n"
            f"Host: {self._host}\r\n"
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            " AppleWebKit/537.36 (KHTML, like Gecko)"
            " Chrome/130.0.0.0 Safari/537.36\r\n"
            "Origin: https://web.telegram.org\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Protocol: binary\r\n"
            "\r\n"
        )
        self._tr_writer.write(request.encode())
        await self._tr_writer.drain()

        try:
            head = await asyncio.wait_for(
                self._tr_reader.readuntil(b"\r\n\r\n"), timeout=timeout
            )
        except asyncio.IncompleteReadError as e:
            # Server answered something and hung up — surface the
            # partial HTTP head so the operator sees WHY (403/404/…).
            partial = e.partial.decode("utf-8", "replace")[:512]
            raise ConnectionError(
                f"{self._host} closed during WebSocket handshake: {partial}"
            ) from e
        head_text = head.decode("utf-8", "replace")

        if " 101 " not in head_text.split("\r\n", 1)[0]:
            raise ConnectionError(
                f"{self._host} refused WebSocket upgrade: "
                + head_text.split("\r\n", 1)[0]
            )

        # Defense-in-depth: verify the accept token, which proves the
        # peer speaks real WebSocket (and is the endpoint we asked
        # for, not a transparent middlebox).
        want = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode()).digest()
        ).decode()
        got = ""
        for line in head_text.split("\r\n"):
            if line.lower().startswith("sec-websocket-accept:"):
                got = line.split(":", 1)[1].strip()
                break
        if got != want:
            raise ConnectionError(
                f"{self._host} bad Sec-WebSocket-Accept (middlebox?)"
            )
        # NOTE: Telegram's edge does NOT echo Sec-WebSocket-Protocol —
        # per RFC 6455 that means "subprotocol declined", and the
        # binary channel works regardless (verified live), so a
        # missing echo is not an error.

    # ── writer surface (sync write + async drain, like StreamWriter) ──

    def write(self, data: bytes) -> None:
        """Queue `data` as one masked binary frame. Sync by design —
        Telethon's send loop calls write() then awaits drain()."""
        if not data:
            return
        mask = os.urandom(4)
        header = bytearray(b"\x82")  # FIN + binary opcode
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        self._tr_writer.write(bytes(header) + _xor_mask(data, mask))

    async def drain(self) -> None:
        await self._tr_writer.drain()

    # ── reader surface ──────────────────────────────────────────

    async def readexactly(self, n: int) -> bytes:
        while len(self._buf) < n:
            if self._eof:
                partial = bytes(self._buf)
                self._buf.clear()
                raise asyncio.IncompleteReadError(partial, n)
            await self._read_frame()
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    async def _read_frame(self) -> None:
        """Read one WebSocket frame into the buffer. Data frames
        append their payload; control frames (ping/pong/close) are
        handled inline."""
        hdr = await self._tr_reader.readexactly(2)
        opcode = hdr[0] & 0x0F
        masked = bool(hdr[1] & 0x80)
        length = hdr[1] & 0x7F
        if length == 126:
            length = struct.unpack(
                ">H", await self._tr_reader.readexactly(2)
            )[0]
        elif length == 127:
            length = struct.unpack(
                ">Q", await self._tr_reader.readexactly(8)
            )[0]
            if length >= 1 << 63:
                raise ConnectionError("absurd WebSocket frame length")
        mask = await self._tr_reader.readexactly(4) if masked else None
        payload = await self._tr_reader.readexactly(length)
        if mask:
            payload = _xor_mask(payload, mask)

        if opcode in (0x1, 0x2, 0x0):  # text/binary/continuation
            self._buf += payload
            return
        if opcode == 0x9:  # ping → pong, echoing the payload
            try:
                m = os.urandom(4)
                self._tr_writer.write(
                    b"\x8a" + bytes([0x80 | len(payload)]) + m
                    + _xor_mask(payload, m)
                )
            except Exception:
                pass
            return
        if opcode == 0xA:  # unsolicited pong — ignore
            return
        if opcode == 0x8:  # close → echo, then EOF for our reader
            self._eof = True
            try:
                m = os.urandom(4)
                self._tr_writer.write(
                    b"\x88" + bytes([0x80 | len(payload)]) + m
                    + _xor_mask(payload, m)
                )
            except Exception:
                pass
            self._tr_writer.close()
            return
        # Unknown opcode: RFC 6455 says fail the connection.
        raise ConnectionError(f"unexpected WebSocket opcode {opcode}")

    # ── close surface ────────────────────────────────────────────

    def close(self) -> None:
        if self._tr_writer is None or self._tr_writer.is_closing():
            return
        try:  # best-effort clean close frame (masked, empty payload)
            self._tr_writer.write(b"\x88\x80" + os.urandom(4))
        except Exception:
            pass
        self._tr_writer.close()

    async def wait_closed(self) -> None:
        await self._tr_writer.wait_closed()


class _ConnectionWsBase(ObfuscatedConnection):
    """
    Common WebSocket transport: same obfuscated2 + abridged stack as
    ConnectionTcpObfuscated, over WsStream. Subclasses provide the
    endpoint via ws_endpoint().
    """

    packet_codec = AbridgedPacketCodec
    obfuscated_io = ObfuscatedIO  # Telethon's own obfuscated2 layer

    def ws_endpoint(self) -> tuple[str, str]:
        """→ (tls_host, path) the WsStream should dial."""
        raise NotImplementedError

    async def _connect(self, timeout=None, ssl=None):
        # "cf" is its own TLS route; the `ssl` argument Telethon
        # passes is None for TCP transports (MTProto does its own
        # crypto) and is deliberately ignored — the wss channel
        # always encrypts.
        host, path = self.ws_endpoint()
        ws = WsStream(host, path, log=self._log)
        await ws.connect(timeout=timeout, local_addr=self._local_addr)
        self._reader = ws  # one object serves both roles: it has
        self._writer = ws  # readexactly/write/drain/close/wait_closed
        self._codec = self.packet_codec(self)
        self._init_conn()  # writes the 64-byte obfuscation header
        await self._writer.drain()

    def __str__(self):
        host, path = self.ws_endpoint()
        return f"wss://{host}{path}"


class ConnectionWsDirect(_ConnectionWsBase):
    """cf mode, zero-configuration route: straight to Telegram's own
    *.web.telegram.org endpoints (works whenever the ISP blocks raw
    Telegram IPs but not Cloudflare-fronted hostnames)."""

    def ws_endpoint(self) -> tuple[str, str]:
        return (dc_ws_host(self._dc_id), "/apiws")


class ConnectionWsViaWorker(_ConnectionWsBase):
    """cf mode behind the owner's personal relay Worker
    (cloudflare/tg-relay-worker.js): survives ISPs that also block
    *.web.telegram.org DNS. WORKER_DOMAIN is set per-class by
    make_ws_connection_class() below."""

    WORKER_DOMAIN = ""

    def ws_endpoint(self) -> tuple[str, str]:
        return (self.WORKER_DOMAIN, f"/{dc_ws_host(self._dc_id)}/apiws")


def make_ws_connection_class(conn: ConnectionSettings) -> type:
    """
    The Telethon connection class for the current system-wide cf
    settings: via the personal Worker when a domain is configured,
    directly to Telegram's endpoints otherwise.

    A fresh subclass per call carries the worker domain as a class
    attribute (Telethon instantiates connection classes itself with
    (ip, port, dc_id, loggers, proxy, local_addr) — there is no kwarg
    channel for extra settings — and the classes stay cheap).
    """
    if conn.cf_worker_domain:
        return type(
            "ConnectionWsViaWorker",
            (ConnectionWsViaWorker,),
            {"WORKER_DOMAIN": conn.cf_worker_domain},
        )
    return ConnectionWsDirect
