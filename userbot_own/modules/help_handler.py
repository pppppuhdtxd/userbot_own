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

#: Set of valid category keys — derived from CATEGORIES so the two never
#: drift apart. Used to detect a module whose `category` attribute doesn't
#: match any known key (see _show_compact_help's grouping loop below).
_VALID_CATEGORY_KEYS: frozenset[str] = frozenset(key for key, _ in CATEGORIES)

# v3.0.13: MODULE_MAP (a hardcoded stem → (category, desc) dict) has been
# removed. It required every new module's author to also manually add a
# matching entry here — a step nothing enforced and nothing documented
# clearly, so a module could be fully loaded and functional (handlers
# registered, commands working) while being completely invisible to both
# `help` and `help <module>`, simply because this dict was never updated.
# `category` and `desc` are now read directly off each loaded Module
# instance (see base.py), the same way `help_text`/`help_extra` already
# were — any module that loads is now automatically visible in help, with
# zero extra step required. See CHANGELOG v3.0.13 for the full root-cause
# writeup.


# ── Module ──────────────────────────────────────────────────────────────────

class HelpHandler(Module):
    """Category-based help system with dynamic reading from modules."""

    name = "help_handler"
    category = "general"
    desc = "راهنما"

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
        # v3.0.9 fix: previously `{cat: [] for cat, _ in CATEGORIES}` and
        # indexed directly with `grouped[cat_key]` — a future MODULE_MAP
        # entry whose category string didn't exactly match one in
        # CATEGORIES would raise an unhandled KeyError and crash the whole
        # `help` command. setdefault() makes this defensive instead.
        grouped: dict[str, list[tuple[str, str, str]]] = {}

        for stem in loaded_stems:
            # Retrieve the Module instance via the loader's public API.
            instance = loader.get_module(stem)
            if instance is None:
                continue

            help_text = getattr(instance, "help_text", "") or ""

            if not help_text.strip():
                continue

            cat_key = getattr(instance, "category", "general") or "general"
            desc = getattr(instance, "desc", "") or ""

            # v3.0.13: category/desc now come from the module instance
            # itself instead of an external MODULE_MAP, so there's no
            # longer a way for a loaded module to be silently skipped here.
            # Two remaining failure modes are still worth surfacing:
            if cat_key not in _VALID_CATEGORY_KEYS:
                # An unrecognized category value (typo, stale category from
                # a renamed group) — fall back to "general" so the module
                # still shows up somewhere, but log it so it gets fixed.
                self._log_warning(
                    "Module '%s' has unrecognized category '%s' — showing "
                    "under 'general' instead. Valid categories: %s",
                    stem, cat_key, ", ".join(k for k, _ in CATEGORIES),
                )
                cat_key = "general"
            elif cat_key == "general" and "category" not in type(instance).__dict__:
                # The module's class never set `category` at all, so this
                # is Module's own class-level default rather than a
                # deliberate choice — very likely a module author simply
                # forgot to set it. Not fatal (defaults to "general" is a
                # safe fallback, same spirit as MODULE_MAP's old design),
                # but worth a warning so the gap doesn't go unnoticed the
                # way a missing MODULE_MAP entry used to (see CHANGELOG
                # v3.0.13 for the bug this replaces).
                self._log_warning(
                    "Module '%s' has no `category` set — defaulting to "
                    "'general' ('عمومی'). Set `category` on its Module "
                    "subclass to place it in a more specific group.",
                    stem,
                )

            grouped.setdefault(cat_key, []).append((stem, desc, help_text.strip()))
            visible_modules += 1
            total_commands += help_text.count("•")

        # Build output
        header_lines: list[str] = [
            "راهنمای Userbot",
            "━" * 20,
            f"ماژول‌ها: {visible_modules} | دستورات: {total_commands}",
            "",
        ]
        footer_lines: list[str] = [
            "━" * 20,
            "برای جزئیات: `help <نام ماژول>`",
            "مثال: `help clearer` یا `help join_left`",
        ]

        # Categories, each rendered as its own block so a length-based
        # split (below) can never cut one in half.
        category_blocks: list[str] = []
        for cat_key, cat_label in CATEGORIES:
            items = grouped.get(cat_key, [])
            if not items:
                continue

            block_lines = [cat_label, ""]
            for stem, desc, help_text in items:
                block_lines.append(f"{stem} | {desc}")
                block_lines.append(help_text)
                block_lines.append("")
            category_blocks.append("\n".join(block_lines))

        # v3.0.9 fix: Telegram's plain-text message limit is ~4096
        # characters. The old code always sent one `event.edit(output)`
        # with every module's full help_text concatenated together — with
        # enough modules (well within range of what this project already
        # has), that silently overflowed the limit and the whole `help`
        # command did nothing, with only a log line and zero user-facing
        # feedback. Now split into multiple messages by category block
        # if needed, so a chat with many modules degrades to "more than
        # one message" instead of "no output at all".
        _MAX_CHUNK = 3500  # margin below Telegram's ~4096 char limit
        chunks: list[str] = []
        current = "\n".join(header_lines)
        for block in category_blocks:
            candidate = current + "\n" + block if current else block
            if len(candidate) > _MAX_CHUNK and current:
                chunks.append(current)
                current = block
            else:
                current = candidate
        current = current + "\n" + "\n".join(footer_lines) if current else "\n".join(footer_lines)
        if len(current) > _MAX_CHUNK and chunks:
            chunks.append(current[:_MAX_CHUNK])
            chunks.append(current[_MAX_CHUNK:])
        else:
            chunks.append(current)

        try:
            await event.edit(chunks[0])
            for extra_chunk in chunks[1:]:
                await event.respond(extra_chunk)
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

        # v3.0.13: previously checked `stem not in MODULE_MAP` here — a
        # hardcoded external dict that had no relationship to what was
        # actually loaded. A fully-functional module missing from that
        # dict would always land in this "not found" branch. Now the
        # loader's own live module list is the single source of truth:
        # any stem the loader actually has loaded resolves correctly here.
        instance = loader.get_module(stem)
        if instance is None:
            # Not currently loaded — try fuzzy search against everything
            # that *is* currently loaded, instead of a static map.
            matches = self._fuzzy_search(query, loader.list_modules())
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
