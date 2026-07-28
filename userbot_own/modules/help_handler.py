"""
userbot_own/modules/help_handler.py
════════════════════════════════════════════════════════════════
Help Command — راهنمای دسته‌بندی‌شده دستورات

Commands (Saved Messages only):
- help          — نمایش راهنمای فشرده
- help <module> — نمایش جزئیات یک ماژول مشخص

Design principles:
- Dynamic help reading: خواندن help_text از خود ماژول‌ها
- Smart copy format: هر دستور کامل در یک backtick
- Clean formatting: خط‌بندی تمیز و خوانا
- No admin filtering: همه ماژول‌ها برای همه اکانت‌ها قابل مشاهده هستند
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from telethon import TelegramClient, events

from userbot_own.core.context import ModuleContext
from userbot_own.core.exceptions import LoaderNotFoundError
from userbot_own.modules.base import Module

# Logging is provided by Module._log_* helpers; no module-level logger needed.


# ── Category definitions ────────────────────────────────────────────────────

#: (category_key, display_label)
CATEGORIES: list[tuple[str, str]] = [
    ("cleaning",  "پاک‌سازی"),
    ("forward",   "فوروارد"),
    ("info",      "اطلاعات"),
    ("social",    "عضویت و ترک"),
    ("reaction",  "Reaction"),
    ("system",    "سیستم"),
    ("general",   "عمومی"),
]

#: Maps module stem → (category_key, short_description)
MODULE_MAP: dict[str, tuple[str, str]] = {
    "clearer":            ("cleaning",  "پاک‌سازی دستی پیام‌ها"),
    "auto_clearer":       ("cleaning",  "پاک‌سازی خودکار"),
    "auto_forwarder":     ("forward",   "فوروارد خودکار"),
    "info_handler":       ("info",      "اطلاعات پیام"),
    "whois_handler":      ("info",      "اطلاعات کاربر و چت"),
    "join_left":          ("social",    "عضویت و ترک چت‌ها"),
    "reaction_commands":  ("reaction",  "دستورات با ری‌اکشن"),
    "system":             ("system",    "مدیریت سیستم"),
    "help_handler":       ("general",   "راهنما"),
}


# ── Module ──────────────────────────────────────────────────────────────────

class HelpHandler(Module):
    """Category-based help system with dynamic reading from modules."""

    name = "help_handler"

    def setup(self, client: TelegramClient) -> None:
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_command)
        self._log_info("HelpHandler ready.")

    # ── Command dispatcher ─────────────────────────────────────────────────

    async def _on_command(self, event) -> None:
        text = (event.raw_text or "").strip()
        if not text:
            return

        parts = text.lower().split()

        # Only handle `help` commands
        if parts[0] != "help":
            return

        # Only in Saved Messages
        client = event.client
        if not await self._is_saved_messages(event):
            return

        if len(parts) == 1:
            # `help` → compact list
            await self._show_compact_help(event, client)
        else:
            # `help <module>` → detailed help
            query = parts[1]
            await self._show_module_help(event, client, query)

    # ── Compact help (main `help` command) ─────────────────────────────────

    async def _show_compact_help(
        self,
        event,
        client: TelegramClient,
    ) -> None:
        """Build and display the category-based compact help output."""
        # Fetch the loader once — not inside the loop.
        loader = self._get_loader(client)
        if loader is None:
            await self._safe_edit(event, "خطا: loader در دسترس نیست.")
            return

        loaded_stems = loader.list_modules()

        total_commands = 0
        visible_modules = 0

        # Group modules by category
        grouped: dict[str, list[tuple[str, str, str]]] = {
            cat: [] for cat, _ in CATEGORIES
        }

        for stem in loaded_stems:
            if stem not in MODULE_MAP:
                continue

            cat_key, desc = MODULE_MAP[stem]

            # Retrieve the Module instance via the loader's public API.
            instance = loader.get_module(stem)
            if instance is None:
                continue

            help_text = getattr(instance, "help_text", "") or ""

            if not help_text.strip():
                continue

            grouped[cat_key].append((stem, desc, help_text.strip()))
            visible_modules += 1
            total_commands += help_text.count("•")

        # Build output
        lines: list[str] = []

        # Header
        lines.append("راهنمای Userbot")
        lines.append("━" * 20)
        lines.append(f"ماژول‌ها: {visible_modules} | دستورات: {total_commands}")
        lines.append("")

        # Categories
        for cat_key, cat_label in CATEGORIES:
            items = grouped.get(cat_key, [])
            if not items:
                continue

            # Category header
            lines.append(cat_label)
            lines.append("")

            # Modules in this category
            for stem, desc, help_text in items:
                lines.append(f"{stem} | {desc}")
                lines.append(help_text)
                lines.append("")

        # Footer
        lines.append("━" * 20)
        lines.append("برای جزئیات: `help <نام ماژول>`")
        lines.append("مثال: `help clearer` یا `help join_left`")

        output = "\n".join(lines)

        try:
            await event.edit(output)
        except Exception as exc:
            self._log_error("Failed to show help: %s", exc)

    # ── Module help (`help <module>` command) ──────────────────────────────

    async def _show_module_help(
        self,
        event,
        client: TelegramClient,
        query: str,
    ) -> None:
        """Display detailed help for a specific module."""
        loader = self._get_loader(client)
        if loader is None:
            await self._safe_edit(event, "خطا: loader در دسترس نیست.")
            return

        # Try exact match first
        stem = query.lower().strip()

        # Check if module exists in the known map
        if stem not in MODULE_MAP:
            # Try fuzzy search
            matches = self._fuzzy_search(query, list(MODULE_MAP.keys()))
            if matches:
                suggestions = "، ".join(f"`{m}`" for m in matches[:5])
                await self._safe_edit(
                    event,
                    f"ماژول `{query}` یافت نشد.\n\n"
                    f"شاید منظورتان این بود:\n{suggestions}\n\n"
                    f"برای لیست کامل: `help`"
                )
            else:
                await self._safe_edit(
                    event,
                    f"ماژول `{query}` یافت نشد.\n\n"
                    f"برای لیست کامل: `help`"
                )
            return

        # Retrieve the Module instance via the loader's public API.
        instance = loader.get_module(stem)
        if instance is None:
            await self._safe_edit(event, f"ماژول `{stem}` لود نشده است.")
            return

        extra = getattr(instance, "help_extra", "") or ""

        if not extra.strip():
            # Fall back to help_text
            help_text = getattr(instance, "help_text", "") or ""
            if help_text.strip():
                await self._safe_edit(
                    event,
                    f"{stem}\n\n{help_text}"
                )
            else:
                await self._safe_edit(
                    event,
                    f"ماژول `{stem}` اطلاعات تکمیلی ندارد."
                )
            return

        # Show help_extra
        await self._safe_edit(event, extra)

    # ── Fuzzy search helper ────────────────────────────────────────────────

    @staticmethod
    def _fuzzy_search(query: str, candidates: list[str]) -> list[str]:
        """Simple fuzzy search for module names."""
        query = query.lower()
        results = []

        for candidate in candidates:
            candidate_lower = candidate.lower()

            # Exact prefix match
            if candidate_lower.startswith(query):
                results.append((0, candidate))
                continue

            # Substring match
            if query in candidate_lower:
                results.append((1, candidate))
                continue

            # Character overlap score
            score = 0
            qi = 0
            for char in candidate_lower:
                if qi < len(query) and char == query[qi]:
                    score += 1
                    qi += 1

            if score >= len(query) // 2:
                results.append((2 - score / len(query), candidate))

        # Sort by score (lower is better)
        results.sort(key=lambda x: x[0])
        return [r[1] for r in results[:5]]

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _get_loaded_stems(self, client: TelegramClient) -> list[str]:
        """Return the list of currently loaded module stems for this account."""
        loader = self._get_loader(client)
        if loader is None:
            return []
        return loader.list_modules()

    def _get_loader(self, client: TelegramClient):
        """
        Retrieve the AccountLoader for the given client's account, via the
        application-scoped loader registry injected in ModuleContext.

        Returns None instead of raising if the loader is not registered.
        """
        account_index = getattr(self.cfg, "index", None)
        if account_index is None:
            return None
        try:
            return self.context.loader_registry.get(account_index)
        except LoaderNotFoundError:
            return None


# ── Help Texts (در انتهای ماژول طبق قوانین) ─────────────────────────────────

help_text = (
    "• `help` | نمایش راهنما\n"
    "• `help <module>` | جزئیات یک ماژول\n"
)

help_extra = (
    "راهنما - اطلاعات تکمیلی\n\n"
    "دستورات موجود:\n"
    "• `help` | نمایش راهنمای فشرده و دسته‌بندی‌شده\n"
    "• `help <نام ماژول>` | نمایش اطلاعات تکمیلی یک ماژول\n\n"
    "مثال‌ها:\n"
    "• `help` | لیست همه ماژول‌ها\n"
    "• `help clearer` | جزئیات ماژول clearer\n"
    "• `help join_left` | جزئیات ماژول join_left\n"
    "• `help system` | جزئیات دستورات سیستم\n\n"
    "نحوه استفاده از ماژول‌ها:\n"
    "• برای دیدن جزئیات هر ماژول، نام آن را بعد از help بنویسید\n"
    "• هر ماژول شامل دستورات، مثال‌ها و نکات مهم است\n\n"
    "جستجو:\n"
    "• اگر نام دقیق را نمی‌دانید، بخشی از نام را بنویسید\n"
    "• سیستم پیشنهاد‌های مشابه را نمایش می‌دهد\n\n"
    "دسته‌بندی‌ها:\n"
    "• پاک‌سازی: clearer و auto_clearer\n"
    "• فوروارد: auto_forwarder\n"
    "• اطلاعات: info_handler و whois_handler\n"
    "• عضویت و ترک: join_left\n"
    "• Reaction: reaction_commands\n"
    "• سیستم: system\n"
    "• عمومی: help_handler\n\n"
    "نکات مهم:\n"
    "• این دستور فقط در Saved Messages کار می‌کند\n"
    "• همه ماژول‌ها برای همه اکانت‌ها قابل دسترسی هستند\n"
)

HelpHandler.help_text = help_text
HelpHandler.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return HelpHandler(context)
