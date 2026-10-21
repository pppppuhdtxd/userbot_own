"""
userbot_own/modules/packages/auto_clearer/handlers.py
════════════════════════════════════════════════════════════════
The AutoClearer module class — the auto-delete filter and the commands.

Holds the global settings, the per-bot overrides, the entity cache and
the bot-detection cache.

v3.2.5: the type filters and the scope rules moved to ``filters.py``,
and the help texts to ``info.py``.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio

from telethon import TelegramClient, events
from telethon.tl.types import User
from telethon.utils import get_display_name

from userbot_own.core.context import ModuleContext
from userbot_own.core.logging_setup import get_logger
from userbot_own.helpers.utils import (
    MESSAGE_TYPES,
    batch_delete,
    read_json_file,
    safe_delete,
    write_json_file_atomic,
)
from userbot_own.modules._infra.base import Module
from userbot_own.modules.packages.auto_clearer.filters import (
    _DEFAULT,
    _SCOPES,
    _TYPES,
    _default_entry,
    _message_matches_filter,
    _scope_matches,
)

# Module-level logger — the free functions in filters.py have no
# `self._log` to write to.
log = get_logger(__name__)


# ── Module ───────────────────────────────────────────────────────────────────

class AutoClearer(Module):
    name = "auto_clearer"
    category = "cleaning"
    desc = "پاک‌سازی خودکار"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self._global:   dict[str, dict]             = _default_entry()
        # v3.2.2 (B-02): per-bot entries hold ONLY the types the user has
        # explicitly overridden for that bot — never a full snapshot of every
        # type. The effective settings for a bot are computed by merging:
        #     effective = {**self._global, **self._bots.get(bot_id, {})}
        # See _effective() and the module docstring's "Settings precedence"
        # section for why the old full-snapshot design was wrong.
        self._bots:     dict[int, dict[str, dict]]   = {}
        self._cache:    dict[int, object]            = {}
        self._settings_file = self.cfg.settings_dir / "autoclear.json"

    def setup(self, client: TelegramClient) -> None:
        self._load()
        self._add_handler(client, events.NewMessage(incoming=True),  self._on_incoming)
        self._add_handler(client, events.NewMessage(outgoing=True),  self._on_outgoing)
        self._add_handler(client, events.NewMessage(outgoing=True),  self._on_command)
        self._log_info("AutoClearer ready.")

    # ── Settings persistence ──────────────────────────────────────────────

    def _load(self) -> None:
        """
        Load settings from disk, auto-migrating old files missing `link` or `other`.

        v3.2.2 (B-02): per-bot entries are now stored as OVERRIDES ONLY —
        they contain just the types the user explicitly set for that bot.
        Files written by 3.2.1 and earlier stored a full snapshot of every
        type, which is indistinguishable from "the user set all of these" and
        therefore permanently shadowed the global settings. The migration
        below converts an old full-snapshot entry into the equivalent
        override set: a type is kept as an override only if it actually
        DIFFERS from the global value for that type. A type the user never
        touched (i.e. one that equals the global) is simply dropped, so the
        global applies to it again — which is exactly what the user meant.
        """
        data, err = read_json_file(self._settings_file)
        if err is not None:
            self._log_error("AutoClearer load error: %s", err)
            return
        if data is None:
            return  # no file yet — keep defaults

        try:
            g = data.get("global", {})
            migrated = False
            for k in _DEFAULT:
                src = g.get(k, {})
                self._global[k] = {
                    "state": bool(src.get("state", False)),
                    "scope": int(src.get("scope", 3)),
                }
                if k not in g:
                    migrated = True

            for bid_str, bsettings in data.get("bots", {}).items():
                try:
                    bid = int(bid_str)
                except (ValueError, TypeError):
                    continue
                if not isinstance(bsettings, dict):
                    continue
                entry: dict[str, dict] = {}
                for k in _DEFAULT:
                    src = bsettings.get(k)
                    if not isinstance(src, dict):
                        # Not present in this (possibly pre-3.2.2) file —
                        # no override for this type.
                        continue
                    value = {
                        "state": bool(src.get("state", False)),
                        "scope": int(src.get("scope", 3)),
                    }
                    # v3.2.2 (B-02): keep only genuine overrides. A stored
                    # value identical to the global is not an override.
                    if value != self._global[k]:
                        entry[k] = value
                if entry:
                    self._bots[bid] = entry
                else:
                    # Everything this bot stored matched the global —
                    # the bot has no overrides at all.
                    migrated = True

            # F6: also trigger migration if "other" is missing (in addition to "link")
            old_keys = set(g.keys()) if "global" in data else set()
            if old_keys and not (_DEFAULT.keys() <= old_keys):
                migrated = True

            if migrated:
                self._log_info("Settings migrated: added missing types (link, other).")
                self._save()

        except Exception as exc:
            self._log_error("AutoClearer load error: %s", exc)

    def _save(self) -> None:
        data = {
            "global": self._global,
            "bots":   {str(k): v for k, v in self._bots.items()},
        }
        # BUG FIX (v3.0.1): this previously wrote settings directly
        # (write_text) instead of atomically like every sibling module
        # (auto_forwarder / join_left / reaction_commands) — a crash
        # mid-write could have left a truncated, unparseable
        # autoclear.json. Now uses the same atomic tmp-file-then-rename
        # helper as the others.
        err = write_json_file_atomic(self._settings_file, data, indent=4)
        if err is not None:
            self._log_error("AutoClearer save error: %s", err)

    def _effective(self, bot_id: int) -> dict[str, dict]:
        """
        Return the effective settings for *bot_id*: the global settings with
        this bot's explicit overrides layered on top.

        v3.2.2 (B-02): this is the merge that the old design was missing.
        Previously it returned ``self._bots.get(bot_id, self._global)`` —
        i.e. a bot entry REPLACED the global wholesale. Because a bot entry
        used to be a full snapshot of every type, a single per-bot command
        (say ``autoclear pic on 1``) silently disabled every globally
        enabled type for that bot, and nothing in the UI showed it.

        The merge is now: ``{**self._global, **self._bots.get(bot_id, {})}``
        — the global applies unless the bot explicitly overrides that type.
        """
        overrides = self._bots.get(bot_id)
        if not overrides:
            return self._global
        return {**self._global, **overrides}

    def _set_bot_override(self, bot_id: int, ftype: str, value: dict) -> None:
        """
        Set (or clear) one type's override for a bot.

        v3.2.2 (B-02): when the new value equals the global value for that
        type, the override is REMOVED rather than stored — so a bot's
        override set can never grow a redundant entry that shadows a later
        global change. If the bot ends up with no overrides at all, its
        entry is removed entirely.

        Args:
            bot_id: The bot's user id.
            ftype:  The message type ("file", "vid", …).
            value:  The new {"state": bool, "scope": int} for that type.
        """
        overrides = dict(self._bots.get(bot_id, {}))
        if value == self._global[ftype]:
            overrides.pop(ftype, None)
        else:
            overrides[ftype] = dict(value)
        if overrides:
            self._bots[bot_id] = overrides
        else:
            self._bots.pop(bot_id, None)

    def _cleanup_stale_overrides(self) -> None:
        """
        Drop every bot override that no longer differs from the global, and
        remove bots whose override set becomes empty.

        v3.2.2 (B-02): called after a global change. An override that now
        matches the global is redundant — the global applies anyway — so
        keeping it would only risk shadowing a LATER global change.
        """
        for bot_id in list(self._bots):
            overrides = self._bots.get(bot_id)
            if not overrides:
                self._bots.pop(bot_id, None)
                continue
            remaining = {
                k: v for k, v in overrides.items() if v != self._global.get(k)
            }
            if remaining:
                self._bots[bot_id] = remaining
            else:
                self._bots.pop(bot_id, None)

    # ── Bot chat detection ────────────────────────────────────────────────

    async def _is_bot_chat(self, client: TelegramClient, chat_id: int) -> User | None:
        """Return the User entity if chat_id is a bot, else None."""
        if chat_id in self._cache:
            ent = self._cache[chat_id]
            return ent if isinstance(ent, User) and ent.bot else None
        try:
            ent = await client.get_entity(chat_id)
            self._cache[chat_id] = ent
            return ent if isinstance(ent, User) and ent.bot else None
        except Exception as exc:
            log.debug("[Account%d] _is_bot_chat(%s): %s", self.cfg.index, chat_id, exc)
            return None

    # ── Auto-delete on message ────────────────────────────────────────────

    async def _try_auto_delete(self, event, is_outgoing: bool) -> None:
        """Check all active filters and delete the message if any match."""
        msg = event.message
        if msg is None:
            return

        # v3.0.9 fix: help_extra explicitly promises pinned messages are
        # never auto-cleared, but this check never actually existed.
        if getattr(msg, "pinned", False):
            return

        # F7: Self-delete guard — prevent the `autoclear` command message
        # itself from being deleted by an active txt/link filter (scope=2/3).
        # When the user sends an `autoclear` command in a bot chat, both
        # _on_command and _on_outgoing fire. If the command text is classified
        # as `txt` or `link` and the matching filter + scope=2/3 is active,
        # safe_delete would fire on the command message immediately after
        # processing — the message would vanish with no confirmation visible.
        # Guard: if the outgoing message starts with "autoclear", skip
        # auto-deletion for this event.
        #
        # v3.2.2 (B-10): the "sent within the last 5 seconds" condition was
        # removed. It compared the message's SERVER timestamp against the
        # LOCAL clock, so any machine whose clock was more than 5 s ahead of
        # Telegram's silently lost the guard — and since _on_outgoing is
        # registered BEFORE _on_command (Telethon dispatches callbacks in
        # registration order), the delete attempt happens FIRST, so the
        # command message really can vanish before the user ever sees the
        # confirmation. The command text is distinctive enough on its own;
        # no age check is needed to make the guard reliable.
        if is_outgoing:
            msg_text_raw = (
                getattr(msg, "text", None) or getattr(msg, "message", None) or ""
            ).strip()
            if msg_text_raw.lower().startswith("autoclear"):
                return

        bot = await self._is_bot_chat(event.client, event.chat_id)
        if bot is None:
            return

        settings = self._effective(bot.id)
        for ftype, cfg in settings.items():
            if not cfg["state"]:
                continue
            if not _scope_matches(cfg["scope"], is_outgoing):
                continue
            if _message_matches_filter(msg, ftype):
                await safe_delete(event.client, event.chat_id, msg.id)
                return

    async def _on_incoming(self, event) -> None:
        await self._try_auto_delete(event, is_outgoing=False)

    async def _on_outgoing(self, event) -> None:
        await self._try_auto_delete(event, is_outgoing=True)

    # ── Command handling ──────────────────────────────────────────────────

    async def _on_command(self, event) -> None:
        text = (event.raw_text or "").strip()
        if not text.lower().startswith("autoclear"):
            return

        client = event.client
        me_id = await self._get_me_id(client)
        if me_id is None:
            self._log_error("Failed to resolve self ID.")
            return

        is_saved = (event.chat_id == me_id)
        parts    = text.split()

        if len(parts) >= 2 and parts[1].lower() == "status":
            if not is_saved:
                return
            await self._cmd_status(event, client)
            return

        if len(parts) != 4:
            await self._safe_edit(
                event,
                "❌ **فرمت نادرست.** استفاده:\n"
                "• `autoclear <type> <on/off> <1/2/3>`\n"
                "• `autoclear status`\n\n"
                "**انواع:** `file`, `vid`, `pic`, `link`, `txt`, `media`, `other`"
            )
            return

        ftype  = parts[1].lower()
        action = parts[2].lower()
        scope_s = parts[3]

        if ftype not in _TYPES:
            await self._safe_edit(
                event,
                f"❌ نوع نامعتبر: `{ftype}`.\n"
                f"**انواع مجاز:** `file`, `vid`, `pic`, `link`, `txt`, `media`, `other`"
            )
            return
        if action not in ("on", "off"):
            await self._safe_edit(event, "❌ عمل نامعتبر. از `on` یا `off` استفاده کنید.")
            return
        try:
            scope = int(scope_s)
            if scope not in _SCOPES:
                raise ValueError
        except ValueError:
            await self._safe_edit(
                event,
                "❌ scope نامعتبر. از `1` (بات)، `2` (شما)، یا `3` (هر دو) استفاده کنید."
            )
            return

        state = action == "on"

        target_chat = await event.get_chat()
        if isinstance(target_chat, User) and target_chat.bot:
            bid = target_chat.id

            # v3.2.2 (B-02): store an OVERRIDE for this one type, not a
            # full snapshot. _set_bot_override() drops the override again
            # when it matches the global, and removes the bot's entry
            # entirely once it holds no overrides.
            self._set_bot_override(bid, ftype, {"state": state, "scope": scope})
            self._save()

            # The effective value for this bot is the merged one — it is
            # what actually governs the live auto-delete filter.
            effective = self._effective(bid)

            if state:
                proc = await event.respond(
                    f"🗑️ در حال پاک کردن پیام‌های قدیمی `{ftype}` (scope {scope})..."
                )
                deleted = await self._clear_past(
                    event.client, target_chat, {ftype: effective[ftype]}
                )
                await self._safe_edit(
                    proc,
                    f"✅ `{deleted}` پیام `{ftype}` قدیمی پاک شد.\n"
                    f"Auto-clear برای این بات فعال شد (scope {scope})."
                )
            else:
                await self._safe_edit(
                    event,
                    f"✅ Auto-clear `{ftype}` برای این بات غیرفعال شد (scope {scope})."
                )

        elif is_saved:
            self._global[ftype]["state"] = state
            self._global[ftype]["scope"] = scope

            # v3.2.2 (B-02): a global change must not leave a bot's override
            # set pointing at a stale value. Any bot override that now
            # equals the global is no longer an override at all, so it is
            # dropped — otherwise the bot would keep shadowing the global
            # for a type the user never meant to differentiate.
            self._cleanup_stale_overrides()
            self._save()

            if state:
                proc = await event.respond(
                    f"🗑️ در حال پاک کردن پیام‌های قدیمی `{ftype}` در همه بات‌ها (scope {scope})..."
                )
                total = 0
                first = True
                async for dialog in event.client.iter_dialogs():
                    ent = dialog.entity
                    if isinstance(ent, User) and ent.bot:
                        # v3.0.9 fix: this used to fire a full-history scan
                        # + batch delete for every bot dialog back-to-back
                        # with no pacing — the single highest FloodWait
                        # risk in this module. A short pause between bots
                        # spreads the burst out.
                        if not first:
                            await asyncio.sleep(1.5)
                        first = False
                        # v3.2.2 (B-02/B-17): use the SAME merged view as
                        # the live filter (_effective), not a third,
                        # divergent precedence rule. A bot's own override
                        # wins; otherwise the global applies.
                        bot_setting = self._effective(ent.id)[ftype]
                        total += await self._clear_past(
                            event.client, ent, {ftype: bot_setting}
                        )
                await self._safe_edit(
                    proc,
                    f"✅ `{total}` پیام `{ftype}` قدیمی پاک شد.\n"
                    f"Auto-clear کلی فعال شد (scope {scope})."
                )
            else:
                await self._safe_edit(
                    event,
                    f"✅ Auto-clear کلی `{ftype}` غیرفعال شد (scope {scope})."
                )
        else:
            await self._safe_edit(
                event,
                "ℹ️ این دستور فقط در **Saved Messages** یا **چت یک بات** قابل استفاده است."
            )

    # ── Clear past messages matching filter ───────────────────────────────

    async def _clear_past(
        self, client: TelegramClient, entity, settings: dict[str, dict]
    ) -> int:
        """Delete historical messages matching the given filter settings.

        F1 FIX (v3.1.7): Added scope check using msg.out before calling
        _message_matches_filter. Previously, _clear_past deleted every message
        matching the type filter regardless of the configured scope (1=bot,
        2=user, 3=both), causing silent data loss when scope was not 3.
        Example: `autoclear txt on 1` (bot messages only) would retroactively
        delete the user's own text messages too. Now `_scope_matches()` is
        called identically to how it is called in `_try_auto_delete()`.
        """
        ids: list[int] = []
        history_limit = self.context.settings.history_limit
        try:
            # v3.0.9: wait_time mirrors clearer.py's FloodWait mitigation
            # for large scans, applied consistently here too.
            async for msg in client.iter_messages(entity, limit=history_limit, wait_time=1):
                if msg is None:
                    continue
                # v3.0.9 fix: help_extra promises pinned messages are never
                # cleared; this historical sweep never actually checked.
                if getattr(msg, "pinned", False):
                    continue
                for ftype, cfg in settings.items():
                    if not cfg["state"]:
                        continue
                    # F1: Respect the configured scope during historical cleanup.
                    # msg.out is True when the message was sent by this account.
                    if not _scope_matches(cfg["scope"], bool(getattr(msg, "out", False))):
                        continue
                    if _message_matches_filter(msg, ftype):
                        ids.append(msg.id)
                        break
        except Exception as exc:
            self._log_error("_clear_past scan error: %s", exc)

        if not ids:
            return 0

        # v3.0.9 fix: reuse the shared batch_delete helper (accurate
        # pts_count-based counting, consistent FloodWait retry) instead of
        # a separately-maintained, less accurate inline implementation.
        return await batch_delete(client, entity, ids)

    # ── Status display ────────────────────────────────────────────────────

    async def _cmd_status(self, event, client: TelegramClient) -> None:
        lines = ["📊 **وضعیت Auto-Clear:**\n", "**تنظیمات کلی:**"]

        # v3.2.2 (B-14): the display order is derived from the canonical
        # MESSAGE_TYPES list instead of a hand-maintained copy that had to be
        # updated separately from every other module's copy. The "media"
        # composite is inserted right after the real media types so the
        # listing keeps its familiar order.
        type_order = list(MESSAGE_TYPES)
        type_order.insert(type_order.index("pic") + 1, "media")
        type_labels = {
            "file":   "فایل",
            "vid":    "ویدیو",
            "pic":    "عکس",
            "link":   "لینک",
            "txt":    "متن",
            "media":  "رسانه (عکس+ویدیو+فایل)",
            "other":  "سایر (استیکر/ویس/نظرسنجی/...)",
        }
        scope_labels = {1: "بات", 2: "شما", 3: "هر دو"}

        for k in type_order:
            cfg = self._global.get(k, {"state": False, "scope": 3})
            state = "✅ ON" if cfg["state"] else "❌ OFF"
            scope_desc = scope_labels.get(cfg["scope"], "?")
            label = type_labels.get(k, k)
            lines.append(f"  • `{k}` ({label}): {state} (Scope: {scope_desc})")

        # v3.2.2 (B-02): the per-bot block now shows the MERGED effective
        # settings — what actually governs the live filter for that bot —
        # and marks which types are the bot's own OVERRIDE of the global.
        #
        # Before this fix, a bot entry was a full snapshot, so the status
        # output listed only the types that were ON in that snapshot. A
        # globally-enabled type that the bot's snapshot had turned off was
        # invisible in the output while silently disabling the feature —
        # the global block said "ON", the bot block said nothing, and the
        # user's own messages survived because the global rule was being
        # shadowed. Now the merged view is shown, and an explicit override
        # is marked as such.
        active_bots = {
            bid: s for bid, s in self._bots.items()
            if any(c["state"] for c in self._effective(bid).values())
        }
        if active_bots:
            lines.append("\n**تنظیمات مخصوص بات‌ها (ادغام‌شده با کلی):**")
            for bid, overrides in active_bots.items():
                try:
                    if bid in self._cache:
                        ent = self._cache[bid]
                    else:
                        ent = await client.get_entity(bid)
                        self._cache[bid] = ent
                    bot_name = get_display_name(ent)
                except Exception:
                    bot_name = f"ID {bid}"
                effective = self._effective(bid)
                lines.append(f"  • **{bot_name}** ([ID: {bid}](tg://user?id={bid})):")
                for k in type_order:
                    cfg = effective.get(k, {"state": False, "scope": 3})
                    if not cfg["state"]:
                        continue
                    scope_desc = scope_labels.get(cfg["scope"], "?")
                    label = type_labels.get(k, k)
                    mark = "✏️ " if k in overrides else ""
                    lines.append(
                        f"    - {mark}`{k}` ({label}): ✅ ON (Scope: {scope_desc})"
                    )
                if not overrides:
                    lines.append("    - _(مطابق تنظیمات کلی)_")
            lines.append("\n_✏️ = تنظیم مخصوص این بات (override)_")

        await self._safe_edit(event, "\n".join(lines), parse_mode="Markdown")
