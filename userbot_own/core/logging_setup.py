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

def setup(log_level: str = "INFO", log_file: str | None = None) -> None:
    """
    Initialize the root logger with console + optional file output.

    This function:
    1. Removes default loguru handler
    2. Installs InterceptHandler to capture ALL standard logging
    3. Silences noisy loggers (telethon.network, telethon.crypto)
    4. Adds colored console output
    5. Optionally adds rotating file output
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
        level=log_level.upper(),
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

    # Use a default-argument capture to avoid the late-binding closure pitfall:
    # if this function were ever called in a loop, all lambdas would share the
    # same `account_index` cell and end up with the last iteration's value.
    # The default-argument form binds the value at definition time.
    def _account_filter(record: dict, _idx: int = account_index) -> bool:
        return (
            "name" in record["extra"]
            and (
                record["extra"].get("account") == _idx
                or str(_idx) in record["extra"].get("name", "")
            )
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
