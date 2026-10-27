"""
userbot_own/config/loader.py
════════════════════════════════════════════════════════════════
Configuration *loading* — all the file/environment I/O that used to
run implicitly at import time in the old config.py (including a
bare `sys.exit()` if no accounts were found — which made the module
nearly impossible to import for testing or tooling purposes).

Nothing in this file runs on import. The composition root
(app/composition_root.py) calls these functions explicitly, in a
known order, during startup. Behaviour — including every validation
rule, every error message, and every exit path — is unchanged from
the original config.py.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from userbot_own.config.env import env_int, env_str, load_dotenv
from userbot_own.config.models import (
    AccountConfig,
    ConnectionSettings,
    Paths,
    Settings,
)
from userbot_own.helpers.utils import write_json_file_atomic


def load_settings(env_file: Path | None = None) -> Settings:
    """
    Load a `.env` file (if given) into the process environment, then build
    a `Settings` instance from environment variables (same variables and
    defaults as the original config.py).
    """
    if env_file is not None:
        load_dotenv(env_file)

    return Settings(
        backoff_start=env_int("BACKOFF_START", 1),
        backoff_max=env_int("BACKOFF_MAX", 300),
        history_limit=env_int("HISTORY_LIMIT", 2000),
        log_level=env_str("LOG_LEVEL", "DEBUG").upper(),
    )


def discover_accounts(paths: Paths) -> list[AccountConfig]:
    """
    Scan `accounts/` and return one `AccountConfig` per valid sub-folder.

    Exits the process with a descriptive error if no account folders exist,
    or if none of them contain a valid account.json — identical behaviour
    (including exact message text) to the original config.py.
    """
    paths.accounts.mkdir(exist_ok=True)

    folders: list[Path] = sorted(
        (p for p in paths.accounts.iterdir() if p.is_dir() and p.name.isdigit()),
        key=lambda p: int(p.name),
    )

    if not folders:
        sys.exit(
            "\n[CONFIG] No account folders found in accounts/.\n"
            "         Run:  python add_account.py\n"
        )

    accounts: list[AccountConfig] = []

    for folder in folders:
        idx = int(folder.name)
        cfg_file = folder / "account.json"

        if not cfg_file.exists():
            print(f"[CONFIG] #{idx}: missing account.json — skipped.", file=sys.stderr)
            continue

        try:
            raw: dict = json.loads(cfg_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[CONFIG] #{idx}: cannot read account.json — {exc}", file=sys.stderr)
            continue

        try:
            api_id = int(raw.get("api_id", 0))
        except (ValueError, TypeError):
            api_id = 0

        api_hash: str = str(raw.get("api_hash", "")).strip()

        if not api_id or not api_hash:
            print(f"[CONFIG] #{idx}: missing api_id or api_hash — skipped.", file=sys.stderr)
            continue

        # Optional (v3.1.3+). Empty string means "no string session" —
        # AccountClient falls back to the file-based
        # session at session_path exactly as they always have.
        session_string: str = str(raw.get("session_string", "")).strip()

        accounts.append(
            AccountConfig(
                index=idx,
                account_dir=folder,
                session_path=str(folder / "session"),
                api_id=api_id,
                api_hash=api_hash,
                phone=str(raw.get("phone", "")).strip(),
                log_file=str(paths.logs / f"account{idx}.log"),
                settings_dir=paths.settings / f"account{idx}",
                session_string=session_string,
            )
        )

    if not accounts:
        sys.exit(
            "\n[CONFIG] No valid accounts found.\n"
            "         Check your account.json files.\n"
        )

    return accounts


# ── Connection settings (v3.2.7) ───────────────────────────────────────────────

#: File (under paths.data) holding the single system-wide connection config.
#: Written by save_connection_settings(); read by load_connection_settings().
CONNECTION_FILE: str = "connection.json"


def load_connection_settings(paths: Paths) -> ConnectionSettings:
    """
    Load the system-wide connection settings from `data/connection.json`.

    Every failure mode — missing file, unreadable file, invalid JSON,
    missing/invalid fields — degrades to `ConnectionSettings.direct_default()`
    (the exact pre-3.2.7 behavior). A hand-mangled settings file can never
    leave the bot unable to connect; worst case it falls back to what 3.2.6
    did. Nothing here prints, raises, or exits.

    v3.2.7 file schema (all keys optional; unknown keys ignored):

        {
          "mode": "direct" | "socks5" | "cf",
          "socks5": {"host": str, "port": int, "username": str, "password": str},
          "cf":    {"worker_domain": str}
        }
    """
    file = paths.data / CONNECTION_FILE

    try:
        raw: dict = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ConnectionSettings.direct_default()

    if not isinstance(raw, dict):
        return ConnectionSettings.direct_default()

    mode = str(raw.get("mode", "direct")).strip().lower()
    if mode not in ("direct", "socks5", "cf"):
        return ConnectionSettings.direct_default()

    socks = raw.get("socks5") or {}
    if not isinstance(socks, dict):
        socks = {}

    cf = raw.get("cf") or {}
    if not isinstance(cf, dict):
        cf = {}

    try:
        socks_port = int(socks.get("port", 1080))
    except (ValueError, TypeError):
        socks_port = 1080
    if not (1 <= socks_port <= 65535):
        socks_port = 1080

    return ConnectionSettings(
        mode=mode,
        socks_host=str(socks.get("host", "")).strip(),
        socks_port=socks_port,
        socks_user=str(socks.get("username", "")).strip(),
        socks_pass=str(socks.get("password", "")),
        cf_worker_domain=str(cf.get("worker_domain", "")).strip().lower(),
    )


def save_connection_settings(paths: Paths, settings: ConnectionSettings) -> Exception | None:
    """
    Atomically write *settings* to `data/connection.json`.

    Uses the same write_json_file_atomic() pattern as every other settings
    file in this project: a crash mid-write can never corrupt the real file,
    only the sibling temp file. Returns the exception on failure (or None on
    success) — the caller decides how loudly to report it; the settings
    menu (settings.py, Phase 5) will surface it in Persian.
    """
    data = {
        "mode": settings.mode,
        "socks5": {
            "host": settings.socks_host,
            "port": settings.socks_port,
            "username": settings.socks_user,
            "password": settings.socks_pass,
        },
        "cf": {
            "worker_domain": settings.cf_worker_domain,
        },
    }

    paths.data.mkdir(parents=True, exist_ok=True)
    return write_json_file_atomic(
        paths.data / CONNECTION_FILE, data, indent=2, tmp_suffix=".json.tmp"
    )


__all__ = [
    "load_settings",
    "discover_accounts",
    "load_connection_settings",
    "save_connection_settings",
    "CONNECTION_FILE",
]
