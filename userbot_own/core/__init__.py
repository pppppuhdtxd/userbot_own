"""
userbot_own/core
════════════════════════════════════════════════════════════════
Core infrastructure for the Multi-Account Userbot.

Sub-modules
───────────
telegram_client — TelegramClient factory per account (direct connection)
exceptions      — structured exception hierarchy
context         — ModuleContext (constructor-injection payload)
registry        — AccountRegistry, AccountLoaderRegistry
loader          — per-account plugin loader with hot-reload
logging_setup   — centralized structured logging
reconnector     — per-account reconnect loop with exponential backoff
watcher         — file-change callbacks for config + module hot-reload

Deliberately no eager `from userbot_own.core import (...)` re-export of every
submodule here (the original core/__init__.py did this). That pattern
forces a full import of every core submodule — including the heavier
ones — any time anything imports `userbot_own.core` at all, which only
increases circular-import risk for no actual benefit: nothing in this
project ever relied on `core.<submodule>` as an attribute access: every
call site does `from userbot_own.core.x import Y` directly. Import what you
need directly instead.

Note: this version uses direct connection only (no proxy support). For
bypassing network restrictions, use a system-level VPN (WireGuard,
OpenVPN, V2Ray) on Termux or Windows.
════════════════════════════════════════════════════════════════
"""
