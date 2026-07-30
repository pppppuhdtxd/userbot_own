"""
userbot_own/config/models.py
════════════════════════════════════════════════════════════════
Configuration *models* — plain, immutable data. No file I/O, no
environment reads, no `sys.exit()`, nothing that runs at import
time. That is deliberate: models.py can be imported anywhere
(including in tests) without side effects. Everything that actually
touches disk or the environment lives in config/loader.py instead.

This mirrors the original config.py exactly in terms of *content*
(same fields, same defaults, same directory layout) — only the
loading side effects have been moved out.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# ── Directory layout ──────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Paths:
    """
    Directory layout for the running bot, all derived from a single base
    directory (the `userbot/` package directory, same as the original
    `BASE_DIR = Path(__file__).parent`).

    Attributes:
        base:      `userbot/` — the package root.
        accounts:  `userbot/accounts/` — one numbered sub-folder per account.
        data:      `userbot/data/` — runtime data root.
        settings:  `userbot/data/settings/` — per-account module settings.
        logs:      `userbot/data/logs/` — rotating log files.
        modules:   `userbot/modules/` — hot-reloadable plugin files.
    """
    base:      Path
    accounts:  Path
    data:      Path
    settings:  Path
    logs:      Path
    modules:   Path

    @classmethod
    def from_base(cls, base_dir: Path) -> Paths:
        """Build the standard directory layout from a base directory."""
        data_dir = base_dir / "data"
        return cls(
            base=base_dir,
            accounts=base_dir / "accounts",
            data=data_dir,
            settings=data_dir / "settings",
            logs=data_dir / "logs",
            modules=base_dir / "modules",
        )

    def ensure(self, extra_dirs: list[Path] | None = None) -> None:
        """Create all required runtime directories, including any extra_dirs."""
        for d in (self.data, self.settings, self.logs, self.accounts):
            d.mkdir(parents=True, exist_ok=True)
        if extra_dirs:
            for d in extra_dirs:
                d.mkdir(parents=True, exist_ok=True)


# ── Global runtime settings ───────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Settings:
    """
    Global runtime settings, overridable via environment variables or a
    `.env` file. Same variables/defaults as the original config.py:

    | Variable         | Default | Description                              |
    |------------------|---------|-------------------------------------------|
    | BACKOFF_START     | 1       | Initial reconnect back-off (seconds)      |
    | BACKOFF_MAX       | 300     | Maximum reconnect back-off (seconds)      |
    | HISTORY_LIMIT     | 2000    | Max messages scanned by clearer modules   |
    | LOG_LEVEL         | DEBUG   | Root logging level                        |
    """
    backoff_start: int = 1
    backoff_max:   int = 300
    history_limit: int = 2000
    log_level:     str = "DEBUG"


# ── AccountConfig ─────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class AccountConfig:
    """
    Immutable configuration snapshot for a single Telegram account.
    Built once at startup by config.loader.discover_accounts() and never
    mutated afterward — identical shape to the original config.py version.

    Attributes:
        index:        Numeric folder name (1, 2, 3, …).
        account_dir:  Absolute path to accounts/N/.
        session_path: Path to the Telethon session file *without* the
                      .session extension.
        api_id:       Telegram API application ID.
        api_hash:     Telegram API application hash.
        phone:        E.164 phone number, e.g. "+989123456789".
        log_file:     Absolute path to the per-account log file.
        settings_dir: Absolute path to the per-account settings directory.
    """
    index:        int
    account_dir:  Path
    session_path: str
    api_id:       int
    api_hash:     str
    phone:        str
    log_file:     str
    settings_dir: Path


__all__ = ["Paths", "Settings", "AccountConfig"]
