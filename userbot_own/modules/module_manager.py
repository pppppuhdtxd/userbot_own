"""
userbot_own/modules/module_manager.py
════════════════════════════════════════════════════════════════
Module Manager (v3.1.0) — per-account control over modules_extra/.

Commands (Saved Messages only, outgoing — same gating as system.py):
- `.extra`                — list every available extra module + status
- `.extra enable <name>`  — enable a module for this account
- `.extra disable <name>` — disable a module for this account
- `.extra info <name>`    — status, category, and description for one

This module lives in the core `modules/` folder (not modules_extra/)
because it must always be loaded — it's the thing that controls the
extra-module system, not a part of it.

It never touches AccountLoader internals directly — only the public
methods added for this purpose: list_available_extra(),
is_extra_enabled(), enable_extra_module(), disable_extra_module()
(see core/loader.py). Core modules in modules/ are completely
unaffected by anything here; this only ever touches modules_extra/
and this account's own enabled_modules.json.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from telethon import TelegramClient, events

from userbot_own.core.context import ModuleContext
from userbot_own.core.exceptions import LoaderNotFoundError
from userbot_own.modules.base import Module
from userbot_own.modules.router import CommandRouter

# ── Constants ─────────────────────────────────────────────────────────────────

_AUTO_DELETE_DELAY = 8.0  # matches system.py — this is the same class of
                          # owner/system utility, not a user-facing feature.


# ── Module ────────────────────────────────────────────────────────────────────

class ModuleManager(Module):
    """Per-account enable/disable control for modules_extra/ (Saved Messages only)."""

    name = "module_manager"
    category = "system"
    desc = "مدیریت ماژول‌های اضافی"
    _auto_delete_default_delay = _AUTO_DELETE_DELAY

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self._router = CommandRouter()
        # Single top-level route — `.extra` alone lists, everything else
        # (enable/disable/info + module name) is parsed out of the
        # remaining text inside _cmd_extra, same shape help_handler.py
        # uses for `help` vs `help <module>`.
        self._router.register(".extra", handler=self._cmd_extra)

    def setup(self, client: TelegramClient) -> None:
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_outgoing)
        self._log_info("ModuleManager ready.")

    # teardown() needs no override: Module.teardown() already removes
    # handlers and cancels any pending _track_delete_task tasks, which is
    # this module's only cleanup need — same as system.py.

    # ── Helper: get loader from registry ────────────────────────────────────

    def _get_loader(self):
        """
        Get the AccountLoader for this account from the application-scoped
        loader registry (injected via ModuleContext). Returns None if the
        loader is not yet registered (e.g. during startup) rather than
        raising LoaderNotFoundError — same pattern as system.py /
        help_handler.py.
        """
        try:
            return self.context.loader_registry.get(self.cfg.index)
        except LoaderNotFoundError:
            return None

    # ── Owner check ───────────────────────────────────────────────────────────

    async def _is_owner_saved(self, event) -> bool:
        """
        True when the message is outgoing and sent in Saved Messages — same
        single-owner model as every other management command in this
        project (see system.py).
        """
        return event.out and await self._is_saved_messages(event)

    # ── Outgoing handler (commands) ─────────────────────────────────────────

    async def _on_outgoing(self, event) -> None:
        text = (event.raw_text or "").strip()
        if not text:
            return

        # Cheap, synchronous lookup first — only pay for the (cached, but
        # still async) owner/Saved-Messages check once we know the message
        # is actually `.extra ...`. Same short-circuit order as system.py.
        handler, _ = self._router.resolve(text)
        if handler is None:
            return

        if not await self._is_owner_saved(event):
            return

        await handler(event)

    # ── `.extra` dispatcher ──────────────────────────────────────────────────

    async def _cmd_extra(self, event) -> None:
        parts = (event.raw_text or "").strip().split()
        # parts[0] == ".extra" is guaranteed by the router match that got
        # us here; anything after it is a subcommand + optional module name.

        if len(parts) == 1:
            await self._show_list(event)
            return

        sub = parts[1].lower()
        arg = parts[2] if len(parts) > 2 else None

        if sub == "enable" and arg:
            await self._do_enable(event, arg)
        elif sub == "disable" and arg:
            await self._do_disable(event, arg)
        elif sub == "info" and arg:
            await self._show_info(event, arg)
        else:
            await self._safe_edit_with_auto_delete(
                event,
                "❌ **استفاده:**\n"
                "`.extra` | لیست ماژول‌های اضافی\n"
                "`.extra enable <name>` | فعال‌سازی\n"
                "`.extra disable <name>` | غیرفعال‌سازی\n"
                "`.extra info <name>` | جزئیات\n",
            )

    # ── Subcommands ───────────────────────────────────────────────────────────

    async def _show_list(self, event) -> None:
        """`.extra` — list every available extra module with ✅/❌ status."""
        loader = self._get_loader()
        if loader is None:
            await self._safe_edit(event, "خطا: loader در دسترس نیست.")
            return

        available = loader.list_available_extra()
        if not available:
            await self._safe_edit_with_auto_delete(
                event,
                "ℹ️ هیچ ماژول اضافه‌ای در `modules_extra/` یافت نشد.",
            )
            return

        lines = [f"📦 **ماژول‌های اضافی ({len(available)}):**\n"]
        for stem in available:
            mark = "✅" if loader.is_extra_enabled(stem) else "❌"
            lines.append(f"{mark} `{stem}`")
        lines.append("")
        lines.append("جزئیات: `.extra info <name>`")

        await self._safe_edit_with_auto_delete(event, "\n".join(lines), delay=15.0)
        self._log_debug("[Account%d] .extra (list) executed", self.cfg.index)

    async def _do_enable(self, event, name: str) -> None:
        """`.extra enable <name>` — enable + load immediately."""
        loader = self._get_loader()
        if loader is None:
            await self._safe_edit(event, "خطا: loader در دسترس نیست.")
            return

        ok, message = loader.enable_extra_module(name)
        icon = "✅" if ok else "❌"
        await self._safe_edit_with_auto_delete(event, f"{icon} {message}")
        self._log_info("[%d] .extra enable %s -> %s", self.cfg.index, name, ok)

    async def _do_disable(self, event, name: str) -> None:
        """`.extra disable <name>` — teardown + disable immediately."""
        loader = self._get_loader()
        if loader is None:
            await self._safe_edit(event, "خطا: loader در دسترس نیست.")
            return

        ok, message = loader.disable_extra_module(name)
        icon = "✅" if ok else "❌"
        await self._safe_edit_with_auto_delete(event, f"{icon} {message}")
        self._log_info("[%d] .extra disable %s -> %s", self.cfg.index, name, ok)

    async def _show_info(self, event, name: str) -> None:
        """`.extra info <name>` — status + category/desc if currently loaded."""
        loader = self._get_loader()
        if loader is None:
            await self._safe_edit(event, "خطا: loader در دسترس نیست.")
            return

        available = loader.list_available_extra()
        if name not in available:
            await self._safe_edit_with_auto_delete(
                event, f"❌ ماژول `{name}` در `modules_extra/` یافت نشد."
            )
            return

        enabled = loader.is_extra_enabled(name)
        instance = loader.get_module(name)

        lines = [
            f"ℹ️ **اطلاعات ماژول `{name}`:**\n",
            f"• وضعیت: {'✅ فعال' if enabled else '❌ غیرفعال'}",
        ]
        if instance is not None:
            desc = getattr(instance, "desc", "") or "—"
            category = getattr(instance, "category", "general")
            lines.append(f"• دسته: `{category}`")
            lines.append(f"• توضیح: {desc}")
        elif enabled:
            # Enabled but not in _loaded — shouldn't normally happen (enable
            # only marks it enabled after a successful load), but surface it
            # plainly rather than silently show a half-true status.
            lines.append("• ⚠️ فعال است اما در حال حاضر لود نشده — لاگ‌ها را بررسی کنید.")

        await self._safe_edit_with_auto_delete(event, "\n".join(lines), delay=12.0)


# ── Help Texts (در انتهای ماژول طبق قوانین) ─────────────────────────────────

help_text = (
    "• `.extra` | لیست ماژول‌های اضافی\n"
    "• `.extra enable <name>` | فعال‌سازی یک ماژول\n"
    "• `.extra disable <name>` | غیرفعال‌سازی یک ماژول\n"
    "• `.extra info <name>` | جزئیات یک ماژول\n"
)

help_extra = (
    "📦 **مدیریت ماژول‌های اضافی (`modules_extra/`)**\n\n"
    "ماژول‌های داخل `modules_extra/`، برخلاف ماژول‌های اصلی `modules/`، "
    "به‌صورت پیش‌فرض غیرفعال هستند و باید برای هر اکانت جداگانه فعال شوند.\n\n"
    "**دستورات:**\n"
    "• `.extra` | نمایش همه ماژول‌های اضافه با وضعیت ✅ فعال / ❌ غیرفعال\n"
    "• `.extra enable <name>` | فعال‌سازی — بلافاصله لود و اجرا می‌شود\n"
    "• `.extra disable <name>` | غیرفعال‌سازی — بلافاصله متوقف می‌شود\n"
    "• `.extra info <name>` | نمایش وضعیت، دسته‌بندی و توضیح ماژول\n\n"
    "**مثال‌ها:**\n"
    "• `.extra enable example_module` | فعال‌سازی ماژول نمونه\n"
    "• `help example_module` | پس از فعال‌سازی، در راهنما هم نمایش داده می‌شود\n\n"
    "**نکات مهم:**\n"
    "• ماژول‌های غیرفعال هرگز import نمی‌شوند — صفر overhead\n"
    "• فعال/غیرفعال بودن هر ماژول per-account است، نه global\n"
    "• ویرایش مستقیم `enabled_modules.json` هم پشتیبانی می‌شود و به‌صورت "
    "خودکار شناسایی می‌شود — نتیجه با استفاده از دستورات بالا یکسان است\n"
    "• این دستور فقط در Saved Messages کار می‌کند\n"
)

ModuleManager.help_text = help_text
ModuleManager.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return ModuleManager(context)
