#!/usr/bin/env python3
"""
userbot_own/settings_menu/cli.py  (new in v3.2.7, Phase 5)
════════════════════════════════════════════════════════════════
Interactive connection-settings manager — the Phase 5 user
interface for the v3.2.7 "cf proxy" capability.

Everything this menu writes goes to ONE file, `data/connection.json`,
and applies to the WHOLE system: the running bot (every account) and
the login/verify clients of add_account.py alike — per the owner's
explicit design decision that the connection setting is system-wide,
never per-account. That file is read once at startup by
config/loader.load_connection_settings(); changes take effect the
next time the bot (or add_account.py) is started.

Modes (config/models.ConnectionSettings):
  • direct — pre-3.2.7 behavior exactly; the DEFAULT and the
    fallback whenever anything is missing or inconsistent.
  • socks5 — Telethon's native proxy tuple through a SOCKS5 server.
  • cf    — MTProto over WebSocket via Cloudflare, in two clearly
    separate sub-routes (owner's end-of-3.2.7 request):
      - WITHOUT a Worker  → Telegram's own official `*.web.telegram.org`
        endpoints (zero-configuration).
      - WITH a Worker     → the owner's personal relay Worker
        (cloudflare/tg-relay-worker.js), for networks that block the
        official endpoints too.

End-of-3.2.7 REWRITE (owner-approved): the menu is now run.bat-style —
every option is a single keypress that executes immediately, no Enter
needed (msvcrt on Windows, termios on Termux/Linux; falls back to line
input when there is no TTY, e.g. piped stdin). The old free-text loop
had two flaws this fixes:
  1. It could not CLEAR a Worker domain: `_ask` returned the current
     domain on empty input, so Enter always re-saved it and the
     no-Worker route was unreachable from the menu. Now "without
     Worker" and "with Worker" are two separate options that set the
     domain explicitly.
  2. Number selection needed Enter and accepted any text.

The same actions are also exposed as subcommands so run.bat's numeric
sub-menu can drive this file without any JSON handling in batch code:

    python settings.py                          # interactive menu
    python settings.py show                     # print current settings
    python settings.py set-direct               # mode = direct
    python settings.py set-socks5 [host port [user] [pass]]
    python settings.py set-cf-no-worker         # cf, empty worker domain
    python settings.py set-cf-worker [domain]   # cf, with worker domain
    python settings.py test                     # live connection test

All user-facing text is English (owner's explicit request 2026-10-26:
the console must stay pure LTR — no Persian text anywhere in the UI).

Self-contained by design: no imports from account_management/ (that
module is an account tool; this one must stay launchable alone on
Termux, where a thin root shim calls main() here — same pattern as
add_account.py).

Run:  python settings.py   (repo-root shim → main() here)
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

from userbot_own import __version__
from userbot_own.config.loader import (
    load_connection_settings,
    save_connection_settings,
)
from userbot_own.config.models import ConnectionSettings, Paths

# ── Enable ANSI colors on Windows 10+ (same trick as cli.py) ─────────────────
if sys.platform == "win32":
    os.system("")

# ── Paths ─────────────────────────────────────────────────────────────────────
# settings_menu/cli.py → settings_menu/ → userbot_own/ (the package root,
# which doubles as the data-dir parent exactly as in account_management/cli.py).
BASE_DIR = Path(__file__).resolve().parent.parent

_MAX_RETRIES = 5
_PROBE_TIMEOUT = 5.0

# A worker domain as the user might paste it: tolerate a scheme prefix,
# a trailing slash or path, and stray whitespace. Everything the
# transport itself needs is the bare hostname (ConnectionWsViaWorker
# builds wss://<domain>/<dc-host>/apiws itself).
_DOMAIN_CLEAN_RE = re.compile(
    r"^\s*(?:https?://)?([a-z0-9.-]+?\.[a-z]{2,})\s*/?\s*$", re.IGNORECASE
)


# ── Terminal Colors (same palette as account_management/cli.py) ──────────────
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"


def _ok(msg: str) -> None:
    print(f"{C.GREEN}  ✔  {msg}{C.RESET}")


def _err(msg: str) -> None:
    print(f"{C.RED}  ✘  {msg}{C.RESET}")


def _info(msg: str) -> None:
    print(f"{C.CYAN}  ›  {msg}{C.RESET}")


def _warn(msg: str) -> None:
    print(f"{C.YELLOW}  ⚠  {msg}{C.RESET}")


def _sep(char: str = "─", width: int = 70) -> None:
    print(f"{C.DIM}{char * width}{C.RESET}")


def _ask(prompt: str, default: str = "", secret: bool = False) -> str:
    """Prompt with optional secret mode; empty input → default."""
    suffix = f" [{default}]" if default else ""
    display = f"{C.BOLD}  {prompt}{suffix}: {C.RESET}"
    try:
        if secret:
            import getpass
            val = getpass.getpass(display)
        else:
            val = input(display).strip()
        return val or default
    except (KeyboardInterrupt, EOFError):
        print()
        raise


# ── Mode labels ──────────────────────────────────────────────────────────────

#: ConnectionSettings.mode → human label shown in every menu.
_MODE_LABELS: dict[str, str] = {
    "direct": "Direct connection",
    "socks5": "SOCKS5 proxy",
    "cf": "Cloudflare (CF Proxy)",
}


def _describe(conn: ConnectionSettings) -> str:
    """One English line describing the ACTIVE route (mode + target)."""
    if conn.is_cf():
        target = conn.cf_worker_domain or "Telegram official endpoints (web.telegram.org)"
        return f"CF Proxy via {target}"
    if conn.is_socks5():
        return f"SOCKS5 proxy {conn.socks_host}:{conn.socks_port}"
    return "Direct connection (no proxy)"


# ── Actions ──────────────────────────────────────────────────────────────────

def _print_header(title: str) -> None:
    _sep("═")
    print(f"{C.BOLD}{C.CYAN}  {title}{C.RESET}")
    _sep("═")


def _show_current(conn: ConnectionSettings) -> None:
    _print_header("Current Connection Settings")
    print()
    print(f"  Mode    : {C.BOLD}{_MODE_LABELS.get(conn.mode, conn.mode)}{C.RESET}")
    print(f"  Active  : {_describe(conn)}")
    if conn.is_socks5():
        print(f"  Username: {conn.socks_user or '—'}")
    if conn.is_cf():
        print(f"  Worker  : {conn.cf_worker_domain or '—'}")
    print(f"  {C.DIM}Stored in data/connection.json — applies system-wide"
          f" (bot AND account manager).{C.RESET}")
    print()


def _switch_mode(
    conn: ConnectionSettings,
    mode: str,
    cf_worker_domain: str | None = None,
) -> ConnectionSettings:
    """
    New settings with only the mode (and, for cf, the worker domain)
    changed — everything else (SOCKS5 details etc.) is carried over so
    switching back and forth never loses what the user typed before.
    """
    return ConnectionSettings(
        mode=mode,
        socks_host=conn.socks_host,
        socks_port=conn.socks_port,
        socks_user=conn.socks_user,
        socks_pass=conn.socks_pass,
        # None = keep the current domain; "" = explicitly no worker.
        cf_worker_domain=(
            conn.cf_worker_domain if cf_worker_domain is None else cf_worker_domain
        ),
    )


def _edit_socks5(conn: ConnectionSettings) -> ConnectionSettings:
    """Edit the SOCKS5 server details (host/port/username/password)."""
    _print_header("SOCKS5 Proxy Settings")
    print()

    host = None
    for attempt in range(1, _MAX_RETRIES + 1):
        raw = _ask("Server address (IP or domain)", default=conn.socks_host)
        if raw:
            host = raw
            break
        _err(f"Address cannot be empty. (Attempt {attempt}/{_MAX_RETRIES})")
    else:
        _err("Too many invalid attempts — nothing changed.")
        return conn

    port = conn.socks_port
    for attempt in range(1, _MAX_RETRIES + 1):
        raw = _ask("Port", default=str(conn.socks_port))
        try:
            candidate = int(raw)
        except ValueError:
            candidate = 0
        if 1 <= candidate <= 65535:
            port = candidate
            break
        _err(f"Port must be an integer between 1 and 65535. (Attempt {attempt}/{_MAX_RETRIES})")
    else:
        _err("Too many invalid attempts — nothing changed.")
        return conn

    print(f"  {C.DIM}If your server has no username/password, leave both empty.{C.RESET}")
    user = _ask("Username (optional)", default=conn.socks_user)
    password = _ask("Password (optional)", default=conn.socks_pass, secret=True)

    return ConnectionSettings(
        # A host was just given, so socks5 is clearly the intent — set the
        # mode too, exactly as if the user had picked it from the mode menu.
        mode="socks5",
        socks_host=host,
        socks_port=port,
        socks_user=user,
        socks_pass=password,
        cf_worker_domain=conn.cf_worker_domain,
    )


def _clean_worker_domain(raw: str) -> str | None:
    """Normalize a pasted worker URL to the bare hostname, or None."""
    m = _DOMAIN_CLEAN_RE.match(raw)
    return m.group(1).lower() if m else None


def _edit_cf_worker_domain(conn: ConnectionSettings) -> ConnectionSettings | None:
    """
    Ask for the personal relay Worker's hostname — the ONLY text input
    in the CF flow. Empty input CANCELS (nothing changes); clearing a
    domain is what the separate "without Worker" option is for. This
    is the fix for the old menu's trap where Enter always re-used the
    current domain and the no-Worker route was unreachable.
    """
    _print_header("Cloudflare — With Worker")
    print()
    print(f"  {C.DIM}The Worker relays the bot's WebSocket traffic to Telegram.{C.RESET}")
    print(f"  {C.DIM}Deploy cloudflare/tg-relay-worker.js first (see README),{C.RESET}")
    print(f"  {C.DIM}then enter the Worker's hostname below.{C.RESET}")
    print()
    print(f"  {C.DIM}Empty input cancels — nothing changes. To use Telegram's{C.RESET}")
    print(f"  {C.DIM}official endpoints instead, pick 'without Worker' in the menu.{C.RESET}")
    print()

    for attempt in range(1, _MAX_RETRIES + 1):
        raw = _ask("Worker domain (e.g. userbot-relay.your-name.workers.dev)")
        if not raw:
            _info("Cancelled — nothing changed.")
            return None
        domain = _clean_worker_domain(raw)
        if domain:
            return _switch_mode(conn, "cf", cf_worker_domain=domain)
        _err(f"Not a bare hostname (no http://, no path, no www). (Attempt {attempt}/{_MAX_RETRIES})")

    _err("Too many invalid attempts — nothing changed.")
    return None


async def _test_connection_ws(conn: ConnectionSettings, host: str) -> None:
    """
    CF-mode test: a real TLS + WebSocket Upgrade handshake against the
    exact (host, path) the live transport would dial, then a clean
    close — no MTProto bytes, no Telegram auth, so it is safe to
    repeat.

    The path comes from the transport's own ws_endpoint() logic via a
    throwaway instance of the connection class make_ws_connection_class()
    builds ('/apiws' directly, or '/<dc-host>/apiws' behind a Worker),
    pinned to DC 2 — the same probe DC core/connection_ws.py's
    ws_probe_target() uses — so this test can never drift from the
    real route. A bare TCP connect can accept and still have the
    upgrade killed (filtered network, broken Worker); a 101 response
    proves the route the bot needs is actually negotiable.
    """
    from userbot_own.core.connection_ws import WsStream, make_ws_connection_class

    kls = make_ws_connection_class(conn)
    probe = kls.__new__(kls)  # no Telethon machinery — only ws_endpoint()
    probe._dc_id = 2
    _host, path = probe.ws_endpoint()
    print(f"  Test path    : {path}")
    print()

    ws = WsStream(host, path)
    try:
        await asyncio.wait_for(ws.connect(timeout=_PROBE_TIMEOUT), timeout=_PROBE_TIMEOUT)
    except (TimeoutError, OSError, ConnectionError, asyncio.LimitOverrunError) as exc:
        # Never leak the transport when the handshake died after the
        # TCP+TLS leg succeeded (close() is None/is_closing-safe, and
        # writes nothing that matters on a half-dead connection).
        try:
            ws.close()
        except Exception:
            pass
        _err(f"WebSocket handshake failed ({type(exc).__name__}).")
        # WsStream embeds the server's status line / partial HTTP head
        # in its errors, so the operator sees WHY the upgrade failed.
        _warn(str(exc)[:200])
        _warn("If your network is filtered, set a Worker domain in CF mode, or use SOCKS5.")
        print()
        return

    try:
        ws.close()
        await asyncio.wait_for(ws.wait_closed(), timeout=_PROBE_TIMEOUT)
    except Exception:
        pass
    _ok("TLS + WebSocket handshake succeeded (HTTP 101) on the exact route the bot uses.")
    _info("To be fully sure, run the bot and check the 'Connection mode' log line.")
    print()


async def _test_connection(conn: ConnectionSettings) -> None:
    """
    Cheap live test against the exact endpoint this mode would use
    (never a Telegram API call, so it is safe to repeat — same
    rationale as core/reconnector.py's _cheap_probe_online()).

    direct / SOCKS5: raw TCP connect, mirroring what the reconnector's
    probes check for these modes. CF: see _test_connection_ws().
    """
    from userbot_own.core.connection_selector import probe_host_port

    _print_header("Connection Test")
    print()
    host, port = probe_host_port(conn)
    print(f"  Active route : {_describe(conn)}")
    print(f"  Test target  : {host}:{port}")
    print()

    if conn.is_cf():
        await _test_connection_ws(conn, host)
        return

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=_PROBE_TIMEOUT
        )
    except (TimeoutError, OSError) as exc:
        _err(f"Could not reach the target ({type(exc).__name__}).")
        _warn("If your network is filtered, choose SOCKS5 or CF mode from the menu.")
        print()
        return

    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass
    _ok("Target is reachable.")
    _info("To be fully sure, run the bot and check the 'Connection mode' log line.")
    print()


def _save(paths: Paths, conn: ConnectionSettings) -> bool:
    """Atomically persist *conn*; True on success."""
    exc = save_connection_settings(paths, conn)
    if exc is not None:
        _err(f"Saving settings failed: {exc}")
        return False
    _ok(f"Saved — new mode: {_describe(conn)}")
    _info("Takes effect the next time the bot (or add_account.py) starts.")
    return True


# ── Single-keypress input (run.bat-style) ────────────────────────────────────

def _read_key() -> str:
    """
    ONE keypress, no Enter needed — the run.bat-style input the owner
    asked for. Returns '' when a raw keypress is impossible (no TTY:
    piped/redirected stdin, or the platform library is missing) so the
    caller can fall back to classic line input.

    Ctrl+C / Ctrl+D are translated to KeyboardInterrupt / EOFError:
    in raw mode the terminal no longer turns them into signals, and
    the existing cancellation handling relies on those exceptions.
    """
    if not sys.stdin.isatty():
        return ""
    if sys.platform == "win32":
        try:
            import msvcrt
        except ImportError:
            return ""
        try:
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                msvcrt.getwch()  # arrow/function key: swallow the 2nd half
                return ""
        except (OSError, ValueError):
            return ""
    else:
        try:
            import termios
            import tty
        except ImportError:
            return ""
        try:
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except (termios.error, ValueError, OSError):
            return ""
    if ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    if ch == "\x04":  # Ctrl+D
        raise EOFError
    return ch


def _ask_choice() -> str:
    """
    The menu key: one keypress when a TTY allows it, line input
    otherwise (piped stdin keeps the menu scriptable/testable).
    """
    print(f"{C.BOLD}  Your choice [1-7]: {C.RESET}", end="", flush=True)
    key = _read_key()
    if key:
        print(key)  # raw mode doesn't echo, so show what was pressed
        return key
    # No TTY (or library missing): classic line input still works —
    # the prompt is already printed, so read the line bare.
    return input().strip()


# ── Main menu (interactive) ──────────────────────────────────────────────────

#: The one and only menu: every mode is a first-class option, and the
#: two CF sub-routes (without / with Worker) are explicit choices —
#: the owner's end-of-3.2.7 request. 7 = Exit; 'q' works too.
_MENU_OPTIONS: tuple[tuple[str, str], ...] = (
    ("1", "Show current settings"),
    ("2", "Direct connection"),
    ("3", "SOCKS5 proxy"),
    ("4", "Cloudflare — without Worker (official endpoints)"),
    ("5", "Cloudflare — with Worker"),
    ("6", "Test connection"),
    ("7", "Exit"),
)


def _print_menu(conn: ConnectionSettings) -> None:
    _print_header(f"Connection Settings — userbot_own (v{__version__})")
    print()
    print(f"  Active: {C.BOLD}{_describe(conn)}{C.RESET}")
    print()
    print(f"  {C.BOLD}Options:{C.RESET}")
    for key, label in _MENU_OPTIONS:
        print(f"    {C.WHITE}{key}{C.RESET}  {label}")
    print()


async def _run_menu() -> None:
    paths = Paths.from_base(BASE_DIR)

    while True:
        conn = load_connection_settings(paths)
        _print_menu(conn)
        try:
            choice = _ask_choice()
        except (KeyboardInterrupt, EOFError):
            print()
            _info("Goodbye.")
            return
        print()

        try:
            if choice == "1":
                _show_current(conn)
            elif choice == "2":
                _save(paths, _switch_mode(conn, "direct"))
            elif choice == "3":
                _save(paths, _edit_socks5(conn))
            elif choice == "4":
                # Explicit empty domain — the route the old menu could
                # never reach: clearing the worker domain.
                _save(paths, _switch_mode(conn, "cf", cf_worker_domain=""))
            elif choice == "5":
                new_conn = _edit_cf_worker_domain(conn)
                if new_conn is not None:
                    _save(paths, new_conn)
            elif choice == "6":
                await _test_connection(conn)
            elif choice in ("7", "q", "Q"):
                _info("Goodbye.")
                return
            else:
                _err(f"Unknown option: '{choice}' — enter 1 to 7.")
        except (KeyboardInterrupt, EOFError):
            print()
            _warn("Operation cancelled.")


# ── Subcommands (run.bat's numeric sub-menu drives these) ────────────────────

#: Printed for `--help` and unknown commands.
_USAGE = """Usage: python settings.py [command]

No command   interactive menu (single-keypress)

Commands:
  show                          print the current settings and exit
  set-direct                    switch to direct connection
  set-socks5 [host port [user] [pass]]
                                switch to SOCKS5 (prompts when args omitted)
  set-cf-no-worker              CF via Telegram's official endpoints
  set-cf-worker [domain]        CF via your relay Worker (prompts when
                                the domain is omitted)
  test                          live test of the active route
"""


async def _run_command(argv: list[str]) -> int:
    """Execute one subcommand; returns the process exit code."""
    paths = Paths.from_base(BASE_DIR)
    conn = load_connection_settings(paths)
    cmd = argv[0]

    if cmd in ("--help", "-h", "help"):
        print(_USAGE)
        return 0
    if cmd == "show":
        _show_current(conn)
        return 0
    if cmd == "set-direct":
        _save(paths, _switch_mode(conn, "direct"))
        return 0
    if cmd == "set-cf-no-worker":
        _save(paths, _switch_mode(conn, "cf", cf_worker_domain=""))
        return 0
    if cmd == "set-cf-worker":
        if len(argv) > 1:
            domain = _clean_worker_domain(argv[1])
            if not domain:
                _err(f"Invalid domain: '{argv[1]}' — pass the bare hostname, no http://, no path.")
                return 2
            _save(paths, _switch_mode(conn, "cf", cf_worker_domain=domain))
            return 0
        new_conn = _edit_cf_worker_domain(conn)
        if new_conn is None:
            return 1
        _save(paths, new_conn)
        return 0
    if cmd == "set-socks5":
        if len(argv) >= 3:
            host = argv[1]
            try:
                port = int(argv[2])
            except ValueError:
                port = 0
            if not (1 <= port <= 65535):
                _err(f"Invalid port: '{argv[2]}' — must be an integer between 1 and 65535.")
                return 2
            user = argv[3] if len(argv) > 3 else ""
            password = argv[4] if len(argv) > 4 else ""
            _save(paths, ConnectionSettings(
                mode="socks5",
                socks_host=host,
                socks_port=port,
                socks_user=user,
                socks_pass=password,
                cf_worker_domain=conn.cf_worker_domain,
            ))
            return 0
        _save(paths, _edit_socks5(conn))
        return 0
    if cmd == "test":
        await _test_connection(conn)
        return 0

    _err(f"Unknown command: '{cmd}'")
    print()
    print(_USAGE)
    return 2


def main() -> None:
    # Windows consoles and redirected pipes can default to legacy
    # single-byte code pages (cp1252/437), where the ✔/—/box-drawing
    # characters in the menu output raise UnicodeEncodeError. Force
    # UTF-8 on the three text streams so the output never depends on
    # the host console's code page.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):
            pass

    argv = sys.argv[1:]
    try:
        if not argv:
            asyncio.run(_run_menu())
        else:
            sys.exit(asyncio.run(_run_command(argv)))
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}  Interrupted.{C.RESET}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
