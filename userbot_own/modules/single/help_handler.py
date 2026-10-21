"""
userbot_own/modules/single/help_handler.py
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

from telethon import TelegramClient, errors, events

from userbot_own.core.context import ModuleContext
from userbot_own.core.exceptions import LoaderNotFoundError
from userbot_own.modules._infra.base import Module
from userbot_own.modules._infra.router import CommandRouter
from userbot_own.modules.single.help_handler_info import (
    category,
    desc,
    help_extra,
    help_text,
    name,
)

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

# Telegram rejects any message over ~4096 chars. Chunk at 3500 to leave
# margin for markup and for the chunker's own line-based splitting. Single
# source of truth for both the compact list and `help <module>` — v3.2.1
# moved the module-detail path onto the same limit the v3.0.9 fix gave the
# compact path (join_left's help_extra alone measured 4,849 chars in 3.2.0
# and was silently rejected as one message).
_MAX_HELP_CHUNK = 3500

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

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        # v3.1.10: first-token dispatch now goes through CommandRouter like
        # system.py, module_manager.py and whois_handler.py, instead of the
        # hand-written `parts[0] != "help"` check. Behaviour is unchanged:
        # exact case-insensitive match on the first token only.
        self._router = CommandRouter()
        self._router.register("help", handler=self._cmd_help)

    def setup(self, client: TelegramClient) -> None:
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_command)
        self._log_info("HelpHandler ready.")

    # ── Command dispatcher ─────────────────────────────────────────────────

    async def _on_command(self, event) -> None:
        text = (event.raw_text or "").strip()
        # Match the command FIRST, so ordinary chat messages never reach the
        # Saved-Messages check below (same order as before the migration).
        handler, _ = self._router.resolve(text)
        if handler is None:
            return

        # Only in Saved Messages
        if not await self._is_saved_messages(event):
            # v3.2.1: this silent return was the leading hypothesis for
            # "help does nothing" reports — behaviour is unchanged (still
            # no reply in chat), but the block is now observable in the
            # per-account log instead of invisible.
            self._log_info(
                "help command ignored — not in Saved Messages (chat_id=%s).",
                getattr(event, "chat_id", None),
            )
            return

        await handler(event)

    async def _cmd_help(self, event) -> None:
        # The router only matched the first token; the argument is this
        # command's own business. Lowercasing the whole text and using just
        # the first argument is exactly what the pre-router code did.
        parts = (event.raw_text or "").strip().lower().split()
        client = event.client

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
            await self._send_help(event, "خطا: loader در دسترس نیست.")
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
        _MAX_CHUNK = _MAX_HELP_CHUNK  # margin below Telegram's ~4096 char limit
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

        # v3.2.1: was a bare try/edit/except-log — an edit failure left no
        # user-facing output at all. The shared sender re-checks every chunk
        # against the limit and falls back to plain responses.
        await self._send_help_chunks(event, chunks)

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
            await self._send_help(event, "خطا: loader در دسترس نیست.")
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
            # v3.1.0: before falling to generic fuzzy search, check whether
            # this is a *known* modules_extra/ module that's simply
            # disabled — that's a far more useful answer than "not found"
            # or a fuzzy guess at some unrelated module, and it's the exact
            # scenario a user hits right after reading `.extra`'s listing.
            if stem in loader.list_available_extra() and not loader.is_extra_enabled(stem):
                await self._send_help(
                    event,
                    f"⚠️ ماژول `{stem}` غیرفعال است.\n"
                    f"برای فعال‌سازی: `.extra enable {stem}`"
                )
                return

            # Not currently loaded — try fuzzy search against everything
            # that *is* currently loaded, instead of a static map.
            matches = self._fuzzy_search(query, loader.list_modules())
            if matches:
                suggestions = "، ".join(f"`{m}`" for m in matches[:5])
                await self._send_help(
                    event,
                    f"ماژول `{query}` یافت نشد.\n\n"
                    f"شاید منظورتان این بود:\n{suggestions}\n\n"
                    f"برای لیست کامل: `help`"
                )
            else:
                await self._send_help(
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
                await self._send_help(
                    event,
                    f"{stem}\n\n{help_text}"
                )
            else:
                await self._send_help(
                    event,
                    f"ماژول `{stem}` اطلاعات تکمیلی ندارد."
                )
            return

        # Show help_extra (v3.2.1: chunked — join_left's help_extra measured
        # 4,849 chars in 3.2.0, over Telegram's ~4096 limit, so this edit was
        # silently rejected and `help join_left` produced nothing).
        await self._send_help(event, extra)

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

    # ── Sending (v3.2.1) ─────────────────────────────────────────────────────

    async def _send_help(self, event, text: str) -> None:
        """Send *text* through the chunked sender below (splitting first)."""
        await self._send_help_chunks(event, self._split_help_chunks(text))

    async def _send_help_chunks(self, event, chunks: list[str]) -> None:
        """
        Send *chunks* as Telegram messages: the first chunk edits the command
        message the user just typed, any further chunks are sent as new
        messages. Every chunk is re-checked against the ~4096-char limit so no
        caller can overflow it, and if the edit itself fails (typed message
        deleted first, flood-wait above the client's threshold, ...) the whole
        output is re-sent as new messages instead of vanishing — `help` must
        never fail with zero user-facing output while a log line is the only
        trace (the failure mode this method was added to close, v3.2.1).
        """
        safe: list[str] = []
        for chunk in chunks:
            safe.extend(self._split_help_chunks(chunk))
        if not safe:
            return

        try:
            await event.edit(safe[0])
        except errors.MessageNotModifiedError:
            # Identical text is already displayed — that is a success, not a
            # failure; fall through so follow-up chunks still go out.
            pass
        except Exception as exc:
            self._log_warning(
                "help: edit of the command message failed (%s) — re-sending "
                "the output as new messages.", exc,
            )
            try:
                for chunk in safe:
                    await event.respond(chunk)
            except Exception as exc2:
                self._log_error("help: failed to send help output: %s", exc2)
            return

        for chunk in safe[1:]:
            try:
                await event.respond(chunk)
            except Exception as exc:
                self._log_error("help: failed to send follow-up part: %s", exc)

    @staticmethod
    def _split_help_chunks(text: str, limit: int = _MAX_HELP_CHUNK) -> list[str]:
        """
        Split *text* into pieces of at most *limit* characters, preferring
        line boundaries; a hard character slice is the last resort for a
        single pathological line. Joining the pieces back with ``"\\n"``
        reproduces *text* exactly — no separators are dropped or invented.
        """
        chunks: list[str] = []
        current = ""
        for line in text.split("\n"):
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > limit and current:
                chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)

        hardened: list[str] = []
        for chunk in chunks:
            while len(chunk) > limit:
                hardened.append(chunk[:limit])
                chunk = chunk[limit:]
            if chunk:
                hardened.append(chunk)
        return hardened


# ── Help Texts (در انتهای ماژول طبق قوانین) ─────────────────────────────────





# ── Metadata ───────────────────────────────────────────────────────────────
#
# help_handler.py and core/loader.py both read these off the *instance*
# (getattr(instance, "help_text", "")), so they must be class attributes.
# The values live in the sibling info file so they can be edited without
# touching any logic.
HelpHandler.name = name
HelpHandler.category = category
HelpHandler.desc = desc
HelpHandler.help_text = help_text
HelpHandler.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return HelpHandler(context)
