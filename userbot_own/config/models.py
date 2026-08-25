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
        modules:   `userbot/modules/` — hot-reloadable core plugin files
                   (always loaded, every account, no opt-out).
        modules_extra: `userbot/modules_extra/` — hot-reloadable *optional*
                   plugin files (v3.1.0). Ships with the repo and is
                   git-tracked exactly like `modules/` — unlike
                   `data/settings/`, nothing here is runtime-generated,
                   so it is intentionally not part of `ensure()` below.
                   Which files are actually active is a *per-account*
                   decision recorded in each account's own
                   `data/settings/account{N}/enabled_modules.json` (see
                   `core/loader.py`), not a property of this path itself.
    """
    base:          Path
    accounts:      Path
    data:          Path
    settings:      Path
    logs:          Path
    modules:       Path
    modules_extra: Path

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
            modules_extra=base_dir / "modules_extra",
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

    v3.1.1 note on `backoff_start` / `backoff_max` above: a repo-wide grep
    confirms these two fields are loaded from the environment by
    `config/loader.py` but were never actually read by
    `core/reconnector.py` — the reconnector's real backoff formulas
    (`_handle_no_internet()`'s `2 ** min(failures, 8)`,
    `_handle_telegram_down()`'s `60 + failures * 30`, both capped at 300)
    are hardcoded there and always have been. This dataclass simply
    documents the values as loaded; it doesn't claim they're consumed.

    v3.1.1 also adds three fast-reconnect settings below. Unlike the
    fields above, these are **not** read from this `Settings` object at
    runtime — `core/reconnector.py` reads the same three environment
    variables directly (see its module docstring's "v3.1.1" section for
    the full rationale). They're listed here purely so this table stays
    the single place documenting every environment variable this project
    reads, alongside their actual defaults:

    | Variable                          | Default | Description                                    |
    |------------------------------------|---------|-------------------------------------------------|
    | FAST_RECONNECT_ENABLED             | true    | Master switch for v3.1.1 fast reconnect          |
    | FAST_RECONNECT_HEALTHY_INTERVAL    | 30      | Healthy-connection check interval (seconds)      |
    | FAST_RECONNECT_PROBE_INTERVAL      | 3       | Backoff-probe interval during outages (seconds)  |
    """
    backoff_start: int = 1
    backoff_max:   int = 300
    history_limit: int = 2000
    log_level:     str = "DEBUG"

    # v3.1.1 — documented here for discoverability; NOT wired through this
    # object at runtime (core/reconnector.py reads the corresponding
    # FAST_RECONNECT_* environment variables directly). These fields exist
    # so `Settings` remains a complete inventory of configurable values
    # even though the reconnector doesn't currently receive a `Settings`
    # instance at all (composition_root.py constructs `AccountReconnector`
    # with just `(ac, loader)`). Wiring these through end-to-end — passing
    # `Settings` into `AccountReconnector` via `config/loader.py` and
    # `app/composition_root.py` — is a natural follow-up if a single
    # source of truth for all config becomes worth the extra plumbing;
    # out of scope for this release, which only touched
    # `core/reconnector.py`, this file, `README.md`, `VERSION`, and
    # `CHANGELOG.md`.
    fast_reconnect_enabled:          bool  = True
    fast_reconnect_healthy_interval: float = 30.0
    fast_reconnect_probe_interval:   float = 3.0


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
        session_string: Optional Telethon StringSession value, loaded
                      from account.json's "session_string" field (added
                      in v3.1.3). When set (non-empty), core/telegram_client.py
                      connects via StringSession instead of the file-based
                      session at session_path — session_path is still
                      always populated for backward compatibility (e.g.
                      account_management/cli.py's file-session tooling)
                      but is simply not used to build the client in that
                      case. When unset (the default — every account.json
                      created before v3.1.3 has no such field), behavior
                      is 100% identical to before this field existed.
    """
    index:          int
    account_dir:    Path
    session_path:   str
    api_id:         int
    api_hash:       str
    phone:          str
    log_file:       str
    settings_dir:   Path
    session_string: str = ""


__all__ = ["Paths", "Settings", "AccountConfig"]
