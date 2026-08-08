"""
userbot_own/modules/system.py
════════════════════════════════════════════════════════════════
System — Owner management commands (read-only subset).

All commands work only inside Saved Messages and require the message
to be outgoing (sent by the account owner).

Commands:
• `.modules`  — list all loaded plugins
• `.account`  — show current account info
• `.stats`    — show system statistics (uptime, modules, etc.)
• `.ping`     — test latency to Telegram servers

Features:
• Auto-delete command output after 8 seconds (keeps Saved Messages clean)
• Silent logging (DEBUG level for routine operations)
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import time
from pathlib import Path

from telethon import TelegramClient, events

from userbot_own.core.context import ModuleContext
from userbot_own.core.exceptions import LoaderNotFoundError
from userbot_own.modules.base import Module
from userbot_own.modules.router import CommandRouter

# ── Constants ─────────────────────────────────────────────────────────────────

_AUTO_DELETE_DELAY = 8.0  # seconds before auto-deleting command output


# ── Module ────────────────────────────────────────────────────────────────────

class SystemModule(Module):
    """Owner management commands (Saved Messages only, read-only subset)."""

    name = "system"
    category = "system"
    desc = "مدیریت سیستم"
    _auto_delete_default_delay = _AUTO_DELETE_DELAY

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self._start_time: float = time.time()
        self._router = CommandRouter()
        self._router.register(".modules", handler=self._cmd_modules)
        self._router.register(".account", handler=self._cmd_account)
        self._router.register(".stats", handler=self._cmd_stats)
        self._router.register(".ping", handler=self._cmd_ping)

    def setup(self, client: TelegramClient) -> None:
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_outgoing)
        self._log_info("SystemModule ready.")

    # teardown() needs no override: Module.teardown() already removes handlers
    # and cancels any pending _track_delete_task tasks (this module's only
    # background work), so the base implementation is sufficient as-is.

    # ── Helper: Get loader from registry ──────────────────────────────────────

    def _get_loader(self):
        """
        Get the AccountLoader for this account from the application-scoped
        loader registry (injected via ModuleContext).

        Returns None if the loader is not registered (e.g. during startup),
        rather than raising LoaderNotFoundError.
        """
        try:
            return self.context.loader_registry.get(self.cfg.index)
        except LoaderNotFoundError:
            return None

    # ── Owner check ───────────────────────────────────────────────────────────

    async def _is_owner_saved(self, event) -> bool:
        """
        Return True when the message is outgoing and sent in Saved Messages.

        Since this is a personal tool, all accounts belong to the owner —
        there is no separate admin/permission system to check. The
        `event.out` check is synchronous (no API call) and reliable.
        """
        return event.out and await self._is_saved_messages(event)

    # ── Outgoing handler (commands) ───────────────────────────────────────────

    async def _on_outgoing(self, event) -> None:
        text = (event.raw_text or "").strip()
        if not text:
            return

        # Cheap, synchronous command lookup first — only pay for the
        # (cached, but still async) owner/Saved-Messages check if the text
        # is actually one of our commands. Same short-circuit order as the
        # original hand-rolled `cmd not in _OWNER_COMMANDS` check.
        handler, _ = self._router.resolve(text)
        if handler is None:
            return

        if not await self._is_owner_saved(event):
            return

        await handler(event)

    # ── .modules ──────────────────────────────────────────────────────────────

    async def _cmd_modules(self, event) -> None:
        loader = self._get_loader()
        mods = loader.list_modules() if loader else []
        body = (
            "\n".join(f"• `{m}`" for m in mods)
            if mods else "_No modules loaded._"
        )
        await self._safe_edit_with_auto_delete(
            event,
            f"📦 **Loaded modules — Account #{self.cfg.index} ({len(mods)}):**\n\n{body}"
        )
        self._log_debug("[Account%d] .modules executed", self.cfg.index)

    # ── .account ──────────────────────────────────────────────────────────────

    async def _cmd_account(self, event) -> None:
        """Show current account information."""
        cfg = self.cfg

        try:
            me = await event.client.get_me()
        except Exception as exc:
            self._log_error("Failed to get me: %s", exc)
            await self._safe_edit_with_auto_delete(event, f"❌ خطا در دریافت اطلاعات: `{exc}`")
            return

        client = event.client
        if client.is_connected():
            connection_status = "✅ متصل"
        else:
            connection_status = "❌ قطع"

        session_ok = Path(cfg.session_path + ".session").exists()

        await self._safe_edit_with_auto_delete(event,
            f"👤 **Account #{cfg.index}**\n\n"
            f"• **User ID:** `{me.id}`\n"
            f"• **Username:** @{me.username or 'N/A'}\n"
            f"• **Phone:** `{cfg.phone or 'N/A'}`\n"
            f"• **API ID:** `{cfg.api_id}`\n"
            f"• **Session:** {'✅' if session_ok else '❌ missing'}\n"
            f"• **Connection:** `{connection_status}`\n"
        )
        self._log_debug("[Account%d] .account executed", self.cfg.index)

    # ── .stats ────────────────────────────────────────────────────────────────

    async def _cmd_stats(self, event) -> None:
        """Show system statistics."""
        uptime_seconds = int(time.time() - self._start_time)
        days, rem = divmod(uptime_seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        uptime_str = ""
        if days > 0:
            uptime_str += f"{days}d "
        uptime_str += f"{hours}h {minutes}m {seconds}s"

        total_modules = 0
        connected_accounts = 0
        for loader in self.context.loader_registry.all().values():
            total_modules += len(loader.list_modules())
            client = loader.client
            if client is not None and client.is_connected():
                connected_accounts += 1

        await self._safe_edit_with_auto_delete(event,
            f"📊 **آمار یوزربات**\n\n"
            f"• اکانت‌های فعال: `{len(self.context.account_registry)}`\n"
            f"• اکانت‌های متصل: `{connected_accounts}`\n"
            f"• ماژول‌های لود شده: `{total_modules}`\n"
            f"• Uptime: `{uptime_str}`\n"
        )
        self._log_debug("[Account%d] .stats executed", self.cfg.index)

    # ── .ping ─────────────────────────────────────────────────────────────────

    async def _cmd_ping(self, event) -> None:
        """Measure latency to Telegram servers."""
        from telethon.tl.functions.updates import GetStateRequest

        # Measure API call latency
        start = time.monotonic()
        try:
            await event.client(GetStateRequest())
            end = time.monotonic()
            api_latency = (end - start) * 1000
            api_status = f"`{api_latency:.2f} ms`"
            is_success = True
        except Exception:
            api_status = "❌ خطا"
            is_success = False
            api_latency = 0.0

        # Measure message edit latency
        start_edit = time.monotonic()
        try:
            await event.edit("🏓 **Pong!**")
        except Exception as exc:
            # v3.0.9 fix: this was a bare, unhandled call — unlike every
            # other command in this module, which uses _safe_edit /
            # _safe_edit_with_auto_delete. A failure here (e.g. the
            # message was deleted between receipt and this call) used to
            # propagate uncaught and abort the command with no final
            # report at all.
            self._log_debug("[Account%d] .ping intermediate edit failed: %s", self.cfg.index, exc)
        end_edit = time.monotonic()
        edit_latency = (end_edit - start_edit) * 1000

        # Evaluate connection quality
        if is_success:
            if api_latency < 150:
                quality = "🟢 عالی"
            elif api_latency < 300:
                quality = "🟡 خوب"
            elif api_latency < 800:
                quality = "🟠 متوسط"
            else:
                quality = "🔴 ضعیف"
        else:
            quality = "❌ قطع"

        await self._safe_edit_with_auto_delete(event,
            f"🏓 **Pong!**\n\n"
            f"• **API Latency:** {api_status}\n"
            f"• **Edit Latency:** `{edit_latency:.2f} ms`\n"
            f"• **کیفیت اتصال:** {quality}"
        )
        self._log_debug("[Account%d] .ping executed", self.cfg.index)


# ── Help Texts (در انتهای ماژول طبق قوانین) ──────────────────────────────────

help_text = (
    "• `.modules` | لیست ماژول‌های فعال\n"
    "• `.account` | اطلاعات اکانت فعلی\n"
    "• `.stats`   | آمار کلی سیستم\n"
    "• `.ping`    | تست تأخیر اتصال\n"
)

help_extra = (
    "دستورات سیستم (فقط خواندنی)\n\n"
    "همه این دستورات فقط در Saved Messages کار می‌کنند.\n\n"
    "وضعیت سیستم:\n"
    "• `.modules` | نمایش لیست همه ماژول‌های فعال این اکانت\n"
    "• `.stats`   | آمار کلی شامل اکانت‌ها، ماژول‌ها و uptime\n"
    "• `.ping`    | تست سرعت پاسخگویی و تأخیر اتصال به سرورهای تلگرام\n\n"
    "مدیریت اکانت‌ها:\n"
    "• `.account` | اطلاعات کامل اکانت فعلی\n\n"
    "مثال‌ها:\n"
    "• `.modules` | نمایش ۹ ماژول فعال\n"
    "• `.ping`    | بررسی تأخیر اتصال (API و Edit Latency)\n"
    "• `.account` | نمایش User ID, API ID, Connection\n"
    "• `.stats`   | نمایش uptime و تعداد اکانت‌های متصل\n\n"
    "نکات مهم:\n"
    "• اتصال به‌صورت مستقیم انجام می‌شود\n"
    "• برای دور زدن محدودیت‌های شبکه از VPN سیستمی استفاده کنید\n"
    "• خروجی همه دستورات پس از ۸ ثانیه به‌صورت خودکار حذف می‌شود\n"
)

SystemModule.help_text = help_text
SystemModule.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return SystemModule(context)
