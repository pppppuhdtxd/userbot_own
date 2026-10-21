"""
userbot_own/modules/packages/reaction_commands/scope.py
════════════════════════════════════════════════════════════════
The chat kinds a reaction trigger is allowed to fire in, and how that
set is persisted.

The scope set is runtime-configurable (`.reaction_scope`) and stored per
account, in ``reaction_scope.json``. The file format is read and
written here; the module class decides when to call these.

The four kinds are:

- ``private`` — a chat with a person
- ``bot``     — a chat with a bot
- ``group``   — a basic group OR a supergroup/megagroup
- ``channel`` — a broadcast channel

``group`` deliberately covers both legacy basic groups and
supergroups; ``channel`` means broadcast channels only. The
classification itself lives with the module class, because it needs the
client to resolve a peer.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from userbot_own.helpers.utils import read_json_file, write_json_file_atomic

#: The valid scope values, in the order they are shown to the user.
VALID_SCOPES: tuple[str, ...] = ("private", "bot", "group", "channel")

#: The scope active on a fresh install.
DEFAULT_SCOPES: frozenset[str] = frozenset({"bot"})

#: Display labels for the scope values.
SCOPE_LABELS: dict[str, str] = {
    "private": "چت خصوصی (کاربران)",
    "bot":     "چت خصوصی (ربات‌ها)",
    "group":   "گروه و سوپرگروه",
    "channel": "کانال (broadcast)",
}


def read_scope_file(path) -> set[str] | None:
    """
    Read the persisted scope set from *path*.

    Returns the set of scope names, or ``None`` when the file is
    missing, unreadable, or not in the expected shape — the caller then
    falls back to its defaults. Never raises.
    """
    data, err = read_json_file(path)
    if err is not None or data is None:
        return None
    raw = data.get("active_scopes", [])
    if not isinstance(raw, list):
        return None
    # Silently drop any value that isn't currently valid (e.g. a stale
    # value from a future/older format) rather than failing to load.
    return {v for v in raw if isinstance(v, str)}


def write_scope_file(path, scopes: set[str]) -> bool:
    """
    Atomically persist the scope set to *path*.

    Returns True on success, False on failure (the caller logs it).
    """
    err = write_json_file_atomic(
        path, {"active_scopes": sorted(scopes)}, indent=2
    )
    return err is None


def format_scope_lines(scopes: set[str]) -> str:
    """Render the scope set as the user-facing checklist."""
    lines = []
    for value in VALID_SCOPES:
        mark = "✅" if value in scopes else "◻️"
        lines.append(f"{mark} `{value}` — {SCOPE_LABELS[value]}")
    return "\n".join(lines)


__all__ = [
    "VALID_SCOPES",
    "DEFAULT_SCOPES",
    "SCOPE_LABELS",
    "read_scope_file",
    "write_scope_file",
    "format_scope_lines",
]
