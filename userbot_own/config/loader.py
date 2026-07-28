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
from userbot_own.config.models import AccountConfig, Paths, Settings


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
            )
        )

    if not accounts:
        sys.exit(
            "\n[CONFIG] No valid accounts found.\n"
            "         Check your account.json files.\n"
        )

    return accounts


__all__ = ["load_settings", "discover_accounts"]
