"""
userbot_own/modules_extra/example_module.py
════════════════════════════════════════════════════════════════
ExampleModule — template / smoke-test module for `modules_extra/`.

Ships DISABLED. A file in `modules_extra/` only runs for an account that
has explicitly enabled it, so this one is never imported — and cannot
affect anything — until you run `.extra enable example_module` in Saved
Messages.

What it does, deliberately almost nothing:
• `پینگ اضافی` (typed in Saved Messages) → the message becomes `🏓 pong`,
  which is then auto-deleted after a few seconds.

That is enough to verify the whole extras system end to end on a real
account: `.extra` lists it as disabled → `.extra enable example_module`
loads it live → the command answers → `help` shows it →
`.extra disable example_module` tears it down and the command goes quiet
again. The README's "Extra Modules" section walks through exactly this.

Use it as a template: copy the file, rename the class and `name`, replace
the handler. Everything a module needs is here — the `Module` subclass
attributes (`name`, `category`, `desc`, `help_text`, `help_extra`),
`setup()` registering handlers through `self._add_handler()` (which
`Module.teardown()` removes for you), and the module-level
`create_module(context)` factory the loader looks for. Delete this file
once you no longer need it.

v3.1.10: restored. v3.1.0 introduced this file and documented it (README,
the in-app `.extra` help) as shipping with the project, but it was missing
from the v3.1.9 package; nothing in the CHANGELOG records a removal.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from telethon import TelegramClient, events

from userbot_own.core.context import ModuleContext
from userbot_own.modules.base import Module

# ── Constants ────────────────────────────────────────────────────────────────

_COMMAND = "پینگ اضافی"
_AUTO_DELETE_DELAY = 5.0  # seconds before the pong is removed from Saved Messages


# ── Module ──────────────────────────────────────────────────────────────────

class ExampleModule(Module):
    """Answers `پینگ اضافی` with a pong — the smallest useful extra module."""

    name = "example_module"
    category = "general"
    desc = "ماژول نمونه"
    _auto_delete_default_delay = _AUTO_DELETE_DELAY

    def setup(self, client: TelegramClient) -> None:
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_outgoing)
        self._log_info("ExampleModule ready.")

    # teardown() needs no override: Module.teardown() already removes the
    # handlers registered through _add_handler() and cancels any pending
    # auto-delete task, so the base implementation is sufficient as-is.

    async def _on_outgoing(self, event) -> None:
        # Match the WHOLE message (whitespace-normalised) instead of using
        # CommandRouter: the command is two words and the router only looks at
        # the first token, so the bare word "پینگ" (plain "ping") would trigger
        # this module too.
        if " ".join((event.raw_text or "").split()) != _COMMAND:
            return

        # Saved Messages only, like the other modules' owner commands.
        if not await self._is_saved_messages(event):
            return

        await self._safe_edit_with_auto_delete(event, "🏓 pong")
        self._log_debug("[Account%d] example_module: pong", self.cfg.index)


# ── Help Texts (در انتهای ماژول طبق قوانین) ──────────────────────────────────

help_text = (
    "• `پینگ اضافی` | آزمایش سیستم ماژول‌های اضافی (پاسخ: 🏓 pong)\n"
)

help_extra = (
    "ماژول نمونه (modules_extra)\n\n"
    "این ماژول به‌صورت پیش‌فرض غیرفعال است. فقط برای آزمایش سیستم ماژول‌های\n"
    "اضافی و به‌عنوان الگو برای نوشتن ماژول جدید وجود دارد.\n\n"
    "فعال/غیرفعال‌سازی:\n"
    "• `.extra enable example_module`  | فعال‌سازی (بدون ری‌استارت)\n"
    "• `.extra disable example_module` | غیرفعال‌سازی (بدون ری‌استارت)\n\n"
    "دستور:\n"
    "• `پینگ اضافی` | پاسخ 🏓 pong — فقط در Saved Messages کار می‌کند\n\n"
    "نکات مهم:\n"
    "• پیام پاسخ پس از ۵ ثانیه به‌صورت خودکار حذف می‌شود\n"
    "• پس از اطمینان از کارکرد، این فایل را حذف کنید یا از آن به‌عنوان الگو استفاده کنید\n"
)

ExampleModule.help_text = help_text
ExampleModule.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return ExampleModule(context)
