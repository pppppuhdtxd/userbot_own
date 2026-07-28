"""
userbot_own/config/env.py
════════════════════════════════════════════════════════════════
Typed environment-variable helpers.

Pulled out of the old monolithic config.py so that "how we read the
environment" is a separate, independently testable concern from
"what the configuration values mean" (see config/models.py) and
"how we assemble the final config objects" (see config/loader.py).

Behaviour is unchanged from the original implementation:
- `python-dotenv` is used when available.
- Otherwise a tiny hand-rolled parser loads KEY=VALUE lines from a
  `.env` file, skipping blanks/comments and never overriding a
  variable that is already set in the real environment.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import os
from pathlib import Path


def load_env_file(env_path: Path) -> None:
    """Minimal `.env` parser used when `python-dotenv` is not installed."""
    if not env_path.exists():
        return
    try:
        with open(env_path, encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass


def load_dotenv(env_path: Path) -> None:
    """
    Load *env_path* into the process environment.

    Prefers `python-dotenv` (handles more edge cases correctly); falls
    back to `load_env_file()` above when the package isn't installed.
    Existing environment variables are never overridden.
    """
    try:
        from dotenv import load_dotenv as _load_dotenv  # type: ignore[import-untyped]
        _load_dotenv(env_path, override=False)
    except ImportError:
        load_env_file(env_path)


def env_str(key: str, default: str) -> str:
    """Read *key* from the environment as a stripped string, or *default*."""
    return (os.environ.get(key, default) or default).strip()


def env_int(key: str, default: int) -> int:
    """Read *key* from the environment as an int, or *default* on any error."""
    try:
        return int(os.environ.get(key, "").strip() or default)
    except ValueError:
        return default


__all__ = ["load_dotenv", "load_env_file", "env_str", "env_int"]
