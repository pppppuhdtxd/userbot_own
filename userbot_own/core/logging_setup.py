"""
userbot_own/core/logging_setup.py  (was core/logger.py)
════════════════════════════════════════════════════════════════
Centralized structured logging with loguru + standard logging bridge.

Provides:
- setup(log_level, log_file) — Initialize root logger with file + console
- add_account_handler(account_index, log_file, log_level) — Per-account logging
- get_logger(name) — Returns an AccountLogger that supports %-style formatting

Key features:
- Colored console output
- InterceptHandler: redirects ALL standard logging (telethon, aiohttp) to loguru
- AccountLogger: supports both `log.info("text %s", arg)` AND `log.info("text {var}")`
- Automatic file rotation (10 MB max)
- Automatic retention (7 days)
- Per-account log filtering

A note on why this module is NOT wired through the composition root's
dependency injection like core/registry.py or core/events.py: loguru's
`logger` object is itself a process-wide singleton by the library's own
design (exactly like the stdlib `logging` module it wraps) — there is
no second instance to inject, and no test double that would make sense
to substitute for "the log sink configuration". The `_configured` /
`_account_handlers` module-level state here is sink bookkeeping, not
application state, so it stays as module-level state rather than being
forced through DI for the sake of consistency. This is a deliberate
exception, not an oversight.

────────────────────────────────────────────────────────────────
v3.1.9 — Terminal/file level split, exact per-account filtering,
non-blocking file sinks
────────────────────────────────────────────────────────────────
Three independent fixes, none requiring a new dependency:

1. `setup()` previously used a single `log_level` for both the console
   and the file sinks, and `Settings.log_level` defaults to "DEBUG" —
   so out of the box, every DEBUG-level call anywhere in the codebase
   was terminal-visible. `setup()` now takes an independent
   `terminal_level` (default "INFO") for the console sink; `log_level`
   continues to control the file sinks only, so full DEBUG detail is
   still captured on disk for post-incident review.

2. `add_account_handler()`'s per-account filter used to accept a
   record if `str(account_index) in record["extra"]["name"]` — a
   substring test. For accounts whose indices share a digit (e.g. `1`
   and `21`), this let one account's log lines leak into another
   account's file, undermining this project's per-account isolation
   invariant. The filter now requires BOTH `"name" in record["extra"]`
   (i.e. the record came from `get_logger()` — this project's own
   code) AND `record["extra"].get("account") == account_index`
   exactly. `account` is set via `logger.contextualize(account=...)`,
   entered once per account in `composition_root.start_account()`
   (and, for extra safety, again around `AccountReconnector.run()`)
   — see those files for where the context is actually bound; nothing
   in this module sets it.

   Hotfix note: the first cut of this filter checked `account` alone,
   dropping the `"name"` precondition entirely. That broke under real
   multi-account load: `InterceptHandler.emit()` below forwards
   third-party stdlib logging — including Telethon's own very verbose
   internal protocol chatter, one line per RPC call — to the bare
   loguru `logger` without ever binding `name`. Because that forwarding
   happens *inside* `logger.contextualize(account=idx)`, those records
   still picked up `account=idx` from the ambient context despite never
   going through `get_logger()`, matched the account-only filter, and
   then crashed loguru's own formatter with `KeyError: 'name'` (the
   format string below requires `{extra[name]}`) — visible as a wall of
   "Logging error in Loguru Handler #N" tracebacks on stderr rather
   than a clean crash. Requiring `"name"` again restores the
   third-party-exclusion guarantee this project always relied on, while
   keeping the exact-equality fix for the original leak.

3. `enqueue=True` is now set on both file sinks. Without it, each
   `logger.add(...)` file sink writes synchronously on the calling
   coroutine's thread; `enqueue=True` moves that write onto a
   background thread via an internal queue, so file I/O on
   storage-constrained/slower Android storage can never stall the
   event loop that the reconnector's 3-second probes also run on.
   Console sinks are unaffected — they were never the bottleneck and
   ordering relative to the terminal is more useful preserved exactly
   as emitted.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from loguru import logger

# ── Telethon Noise Filter ────────────────────────────────────────────────────

class _TelethonNoiseFilter(logging.Filter):
    """
    Suppresses recurring Telethon-internal messages that fire during every
    network disconnect but carry no actionable information.

    These messages originate inside Telethon's own connection layer as it
    races against the reconnector — they describe Telethon's *internal*
    attempt to recover (which we've disabled via ``auto_reconnect=False``
    and ``connection_retries=0``), not anything going wrong in our code.

    Filtered substrings (all from ``telethon.network``):
    • "Automatic reconnection failed"   — Telethon's own retry counter
    • "exception in shielded future"    — asyncio shield tear-down noise
    • "Future exception was never retrieved" — dropped asyncio.Task warning

    Intentionally NOT filtered: anything else from ``telethon.network`` at
    WARNING+, so genuinely new failure modes still surface.

    Applied narrowly to the ``telethon.network`` logger only — not to
    ``InterceptHandler`` globally, which would risk swallowing unrelated
    ERROR-level messages that happen to share a substring.
    """

    _NOISE_SUBSTRINGS: tuple[str, ...] = (
        "Automatic reconnection failed",
        "exception in shielded future",
        "Future exception was never retrieved",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(noise in msg for noise in self._NOISE_SUBSTRINGS)


# ── Intercept Handler ────────────────────────────────────────────────────────

class InterceptHandler(logging.Handler):
    """
    Redirect ALL standard logging (telethon, aiohttp, urllib3, etc.) to loguru.

    This solves the problem of telethon logs appearing without timestamp/level
    by routing them through loguru's formatter.
    """

    def emit(self, record: logging.LogRecord) -> None:
        # Get corresponding Loguru level
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find the frame that actually issued the log call, skipping over
        # logging internals. Guard against f_back being None (top of stack)
        # and normalise the filename so that .pyc and .py both match.
        frame = logging.currentframe()
        depth = 0
        logging_file = logging.__file__.rstrip("co")  # strip .pyc → .py
        while frame is not None:
            filename = frame.f_code.co_filename.rstrip("co")
            if filename == logging_file:
                frame = frame.f_back
                depth += 1
                continue
            break

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


# ── AccountLogger: %-style compatible wrapper ────────────────────────────────

class AccountLogger:
    """
    Logger wrapper that supports %-style formatting like standard logging.

    This solves the problem of `log.info("text %s", arg)` not being formatted
    by loguru (which expects `{}` style).

    Usage (both styles work):
        log.info("User %d logged in", user_id)      # %-style OK
        log.info("User {id} logged in", id=user_id) # {}-style OK
        log.success("Reconnected.")                  # loguru extra levels OK
        log.log("INFO", "msg %s", arg)               # generic level OK
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._logger = logger.bind(name=name)

    @staticmethod
    def _format(msg: str, args: tuple) -> str:
        """Format message with %-style args (like standard logging)."""
        if not args:
            return msg
        try:
            return msg % args
        except (TypeError, ValueError):
            # Fallback: concatenate
            return msg + " " + " ".join(str(a) for a in args)

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.opt(depth=1).debug(self._format(msg, args), **kwargs)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.opt(depth=1).info(self._format(msg, args), **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.opt(depth=1).warning(self._format(msg, args), **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.opt(depth=1).error(self._format(msg, args), **kwargs)

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        # opt(depth=1) keeps the caller's frame; exception=True captures
        # sys.exc_info() automatically — same as loguru's own .exception().
        self._logger.opt(depth=1, exception=True).error(
            self._format(msg, args), **kwargs
        )

    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._logger.opt(depth=1).critical(self._format(msg, args), **kwargs)

    def success(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Loguru's SUCCESS level (between INFO and WARNING)."""
        self._logger.opt(depth=1).success(self._format(msg, args), **kwargs)

    def log(self, level: str | int, msg: str, *args: Any, **kwargs: Any) -> None:
        """Generic level log, mirroring loguru's logger.log()."""
        self._logger.opt(depth=1).log(level, self._format(msg, args), **kwargs)


# ── Global state ─────────────────────────────────────────────────────────────
# See module docstring for why this stays module-level instead of DI'd.

_configured = False
_account_handlers: dict[int, int] = {}


# ── Setup ─────────────────────────────────────────────────────────────────────

def setup(
    log_level: str = "DEBUG",
    log_file: str | None = None,
    terminal_level: str = "INFO",
) -> None:
    """
    Initialize the root logger with console + optional file output.

    This function:
    1. Removes default loguru handler
    2. Installs InterceptHandler to capture ALL standard logging
    3. Silences noisy loggers (telethon.network, telethon.crypto)
    4. Adds colored console output
    5. Optionally adds rotating file output

    Args:
        log_level: Level for the FILE sinks (main.log and every
            per-account file). Defaults to "DEBUG" so full detail is
            always captured on disk regardless of what the terminal
            shows. Normally sourced from ``Settings.log_level``.
        log_file: Path to the main rotating log file, or ``None`` to
            skip file logging entirely (console only).
        terminal_level: Level for the app-log console sink only
            (v3.1.9). Defaults to "INFO" — this is what actually
            determines what scrolls past in a live terminal; kept
            independent of ``log_level`` so raising file verbosity for
            diagnostics never floods the terminal, and vice versa.
            The third-party/intercepted-log console sink below is
            unaffected by this parameter — it stays hardcoded to
            WARNING+ as before.
    """
    global _configured

    if _configured:
        return

    # Remove default loguru handler
    logger.remove()

    # ── Intercept ALL standard logging (telethon, etc.) ──
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # Silence noisy loggers (still captured, just at higher level)
    for noisy_logger in ("telethon.network", "telethon.crypto", "asyncio"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    # Apply the noise filter narrowly to telethon.network — the only logger
    # that emits the three recurring disconnect-noise substrings. Scoped here
    # rather than inside InterceptHandler so the filter is visible as explicit
    # policy and only affects the one noisy source.
    logging.getLogger("telethon.network").addFilter(_TelethonNoiseFilter())

    # ── Console handler for our app logs (with 'name' binding) ──
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[name]}</cyan> | "
            "<level>{message}</level>"
        ),
        level=terminal_level.upper(),
        colorize=True,
        backtrace=False,
        diagnose=False,
        filter=lambda record: "name" in record["extra"],
    )

    # ── Console handler for intercepted logs (telethon, etc.) ──
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<yellow>{name}</yellow> | "
            "<level>{message}</level>"
        ),
        level="WARNING",  # Only WARNING+ for external libraries
        colorize=True,
        backtrace=False,
        diagnose=False,
        filter=lambda record: "name" not in record["extra"],
    )

    # ── File handler (main log) ──
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        logger.add(
            str(log_path),
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[name]} | {message}",
            level=log_level.upper(),
            rotation="10 MB",
            retention="7 days",
            compression="zip",
            encoding="utf-8",
            backtrace=False,
            diagnose=False,
            filter=lambda record: "name" in record["extra"],
            # v3.1.9: offload file I/O to a background thread so it
            # can never block the event loop (see module docstring).
            enqueue=True,
        )

    _configured = True


# ── Per-account logging ───────────────────────────────────────────────────────

def add_account_handler(
    account_index: int,
    log_file: str,
    log_level: str = "INFO",
) -> None:
    """
    Add a per-account log file handler.

    Each account gets its own rotating log file.
    """
    if account_index in _account_handlers:
        return

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # v3.1.9, revised: exact-equality on `account`, replacing the original
    # substring test (`str(_idx) in record["extra"]["name"]`) which could
    # match an unrelated account whose index happened to contain this
    # one's digit(s) as a substring (e.g. account 1 matching account 21's
    # records) — a real cross-account log leak.
    #
    # IMPORTANT (v3.1.9 hotfix): the first cut of this filter dropped the
    # `"name" in record["extra"]` precondition entirely, checking only
    # `account`. That broke in practice: `InterceptHandler.emit()` (this
    # module, above) forwards third-party stdlib logging — including
    # Telethon's own very verbose internal protocol chatter ("Assigned
    # msg_id=…", "Getting difference for account updates", one line per
    # RPC call) — straight to the bare loguru `logger`, without ever
    # binding a `name` key. Because that forwarding happens *inside*
    # `logger.contextualize(account=idx)` (entered in
    # composition_root.start_account() / AccountReconnector.run()),
    # loguru's context machinery still merges `account=idx` into those
    # records' `extra` even though nothing explicitly bound it. Those
    # records then matched this filter on `account` alone, reached this
    # sink, and crashed loguru's own formatter with `KeyError: 'name'`
    # (the format string below requires `{extra[name]}`) — loguru catches
    # that internally and prints a wall of "Logging error in Loguru
    # Handler #N" tracebacks to stderr instead of crashing the process,
    # which is what a "Logging error in Loguru Handler" spam under real
    # multi-account load turned out to look like.
    #
    # Fixed by requiring BOTH: `name` present (so only records that went
    # through `get_logger()` — i.e. this project's own code — are ever
    # candidates) AND an exact `account` match (closing the original
    # substring-leak bug without reopening this one). Third-party/
    # intercepted records are excluded from every per-account file exactly
    # as they always were before v3.1.9 — this restores that guarantee
    # rather than changing it.
    #
    # Use a default-argument capture to avoid the late-binding closure
    # pitfall: if this function is ever called in a loop, all lambdas
    # would share the same `account_index` cell and end up with the
    # last iteration's value. The default-argument form binds the
    # value at definition time.
    def _account_filter(record: dict, _idx: int = account_index) -> bool:
        return (
            "name" in record["extra"]
            and record["extra"].get("account") == _idx
        )

    handler_id = logger.add(
        str(log_path),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[name]} | {message}",
        level=log_level.upper(),
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        encoding="utf-8",
        filter=_account_filter,
        # v3.1.9: see the main file sink above — same non-blocking
        # rationale applies per-account.
        enqueue=True,
    )

    _account_handlers[account_index] = handler_id


# ── Logger factory ────────────────────────────────────────────────────────────

def get_logger(name: str) -> AccountLogger:
    """
    Return an AccountLogger bound to the given module name.

    The returned logger supports %-style formatting:
        log.info("User %d logged in", user_id)

    And also {}-style:
        log.info("User {id} logged in", id=user_id)
    """
    return AccountLogger(name)


# ── Utilities ─────────────────────────────────────────────────────────────────

def remove_account_handler(account_index: int) -> None:
    """Remove the log handler for a specific account."""
    if account_index in _account_handlers:
        handler_id = _account_handlers.pop(account_index)
        logger.remove(handler_id)


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "setup",
    "add_account_handler",
    "get_logger",
    "remove_account_handler",
    "AccountLogger",
    "InterceptHandler",
    "_TelethonNoiseFilter",
]
