"""
userbot_own/modules/reaction_commands.py
════════════════════════════════════════════════════════════════
Reaction-Based Commands — Funnel Architecture (Zero Polling)

Architecture:
- Push-Based Detection: Only UpdateMessageReactions + UpdateEditMessage
- Zero Polling: No get_dialogs calls; Method 1 no longer calls
  get_messages() either as of v3.0.2 (see below)
- Funnel Filtering: 5 gates with O(1)/cached-lookup complexity
- Post-Startup Only: Ignores reactions from before module start
- Self-Only: Only processes reactions from the userbot account
- Dynamic Scope: Which chat types are processed is configurable at
  runtime via the `.reaction_scope` command (see below) — persisted
  per-account in reaction_scope.json, no source edits or restart
  required. Replaces the old hardcoded ENABLE_FOR_* class attributes.

Executes commands via Direct Module Invocation (compatible with clearer.py,
join_left.py, info_handler.py, whois_handler.py) instead of send_message
to avoid event-loop race conditions.

Features:
- Map emoji reactions to commands or text
- Push-based instant detection (no polling delay)
- Direct module invocation (faster, more reliable than send_message)
- Per-account configuration (reactions.json - auto-created)
- Self-reaction only
- Duplicate-delivery protection without permanently blocking re-triggers
  (see "Reaction state tracking" below)
- Environment-aware filtering, user-configurable at runtime via
  `.reaction_scope` (private chats / bot chats / groups+supergroups /
  broadcast channels)

v3.0.8 fixes:
- `MockEvent` (modules/bridge.py) had no `is_private`/`is_group`/
  `is_channel` attributes at all, unlike a real Telethon NewMessage
  event. `clearer.py`'s `_run_clear` reads `event.is_private`
  unconditionally, so every reaction-triggered `clear` command raised
  AttributeError inside the direct-invocation attempt and silently
  fell back to send_message every time — the most commonly
  reaction-mapped command never actually used the "Direct Module
  Invocation" path its architecture was built around. Fixed by having
  `_execute_command_directly` classify the target chat once (via the
  same peer-classification helper Gate 2 uses, so it's normally a
  cache hit — no extra API calls in the common case) and pass the
  three flags into MockEvent's constructor.
- Environment scope was a set of four hardcoded class attributes
  (ENABLE_FOR_BOTS/USERS/GROUPS/CHANNELS) requiring a source edit and
  reload to change, despite the module's own former documentation
  calling them "Environment Toggles" as if they were already
  externally configurable. Replaced with `.reaction_scope <value>`
  (private/bot/group/channel/all/none), persisted per-account in
  reaction_scope.json, loaded at setup() exactly like reactions.json.
- The old PeerChannel branch of the environment filter treated every
  channel-type peer identically, silently lumping supergroups in with
  broadcast channels (both are `PeerChannel` at the MTProto level; only
  `entity.megagroup` tells them apart). A supergroup could never
  actually be reached by the old "groups" toggle. Fixed via a new
  `_classify_peer()` helper that resolves `entity.megagroup` for
  PeerChannel peers (cached per channel_id, same pattern as the
  existing bot/user cache), so `.reaction_scope group` now correctly
  covers both legacy basic groups and supergroups, and
  `.reaction_scope channel` means broadcast channels only.
- `_active_reactions` (Gate 5's rising-edge dedup memory) was
  unconditionally wiped in teardown(), which runs on every reconnect
  via AccountReconnector.reattach() — not just on a genuine hot-reload.
  Since reattach() reuses the same module instance (confirmed in
  core/loader.py: "module instances themselves are reused — no
  re-import needed"), clearing this dict there served no purpose for
  reconnects and created a real duplicate-fire window: a reaction still
  present on a message before a disconnect would look like a fresh
  rising edge once catch_up redelivered it after reconnect, re-firing
  its mapped command. teardown() no longer clears `_active_reactions`,
  `_peer_is_bot_cache`, or the new `_channel_megagroup_cache` — a
  genuine hot-reload gets a fresh instance (and therefore fresh, empty
  dicts) via `create_module()` regardless, so this only changes
  behavior for the reattach()/reconnect path, as intended.

v3.0.2 fixes (see individual method docstrings for full detail):
- Method 1 (_on_reaction_update) read event.peer_id and event.chat_id,
  neither of which exist on the raw UpdateMessageReactions update
  (confirmed against the Telethon library itself) — it silently did
  nothing, ever, relying entirely on Method 2 for all detection. Now
  reads the update's real peer/msg_id/reactions fields directly, with
  no extra network call at all (previously called get_messages() on top
  of the broken field reads).
- The reaction→dedup tracking never forgot a message once a reaction on
  it had fired once, so removing and re-adding the same reaction later
  (or changing to a different one, in some cases) would never re-fire.
  Replaced with a "what's my current reaction state on this message"
  tracker instead of a permanent "everything ever fired" record — see
  Reaction state tracking below.
- Bot/user classification (used by the per-chat-type environment toggles)
  now caches its client.get_entity() lookups locally instead of repeating
  one on every single reaction event for the same peer.

Reaction state tracking (Gate 5, replacing the old permanent `_processed`
set): `_active_reactions` maps (chat_id, msg_id) → the set of emojis you
currently have reacted with on that message, as of the most recent update
processed. Each update computes which emojis are newly present compared to
that stored state (only those fire a command), then overwrites the stored
state to match the new current reality — including clearing it entirely
when you have no more of your own reactions on that message. This still
prevents a single unchanged state being redelivered twice from firing a
command twice, but no longer permanently blocks a legitimate later
re-trigger the way a one-way "ever fired" record did.

Commands (in Saved Messages):
- `reactions`              — show all configured reactions
- `reaction add <emoji> <command>`   — add a reaction mapping
- `reaction remove <emoji>`          — remove a reaction mapping
- `reaction clear`         — remove all reactions
- `.reaction_scope`                  — show which chat types are active
- `.reaction_scope <value>`          — toggle a chat type on/off
  (value: private | bot | group | channel | all | none)
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import time

from telethon import TelegramClient, events
from telethon import utils as telethon_utils
from telethon.tl.types import (
    Message,
    MessageReactions,
    PeerChannel,
    PeerChat,
    PeerUser,
    ReactionEmoji,
    UpdateEditMessage,
    UpdateMessageReactions,
)

from userbot_own.core.context import ModuleContext
from userbot_own.core.exceptions import LoaderNotFoundError
from userbot_own.helpers.utils import read_json_file, write_json_file_atomic
from userbot_own.modules.base import Module
from userbot_own.modules.bridge import MockEvent

# Logging is provided by Module._log_* helpers; no module-level logger needed.
# MockEvent (the Simple Bridge layer used by _execute_command_directly below)
# now lives in modules/bridge.py, shared rather than private to this file.

#: Soft cap on how many distinct (chat, message) reaction states to keep in
#: memory at once. Evicted oldest-first, in a batch (down to half the cap),
#: same pattern the old permanent _processed set used for its own eviction.
_MAX_TRACKED_MESSAGES = 1000

#: v3.0.12 (Gate 5b): how long, in seconds, an identical (chat_id, msg_id,
#: emoji) trigger is suppressed after it fires — a time-based debounce
#: layered on top of Gate 5's state-based rising-edge check. Gate 5 alone
#: intentionally allows a genuine remove-then-re-add of the same emoji to
#: re-fire (that's the whole point of its v3.0.2 fix), but that means a
#: rapid double-tap / flicker looks identical to a deliberate re-trigger
#: minutes later. This cooldown distinguishes the two without touching
#: Gate 5's own semantics.
_REACTION_COOLDOWN_SECONDS = 30.0

#: Soft cap on how many (chat, msg, emoji) cooldown entries to keep in
#: memory at once — same oldest-first, evict-to-half pattern as
#: _MAX_TRACKED_MESSAGES / _remember_reaction_state.
_MAX_TRACKED_COOLDOWNS = 1000

#: v3.0.12: how often (seconds) the "still alive" heartbeat log line is
#: emitted, so a module that has gone silently deaf (e.g. via a failed
#: reattach, or a swallowed exception) is discoverable within one interval
#: instead of only by manually testing it.
_HEARTBEAT_INTERVAL_SECONDS = 300.0


# ── Module ────────────────────────────────────────────────────────────────────

class ReactionCommands(Module):
    """Execute commands via emoji reactions (Funnel Architecture, zero polling)."""

    name = "reaction_commands"

    # ── Dynamic scope (v3.0.8) ──────────────────────────────────────────────
    # Replaces the old hardcoded ENABLE_FOR_BOTS/USERS/GROUPS/CHANNELS class
    # attributes. Scope is now runtime-configurable via `.reaction_scope`
    # and persisted per-account in reaction_scope.json — see
    # _ensure_scope_file()/_load_scope()/_save_scope() and the module
    # docstring's v3.0.8 fix notes.
    #
    # "group" deliberately covers BOTH legacy basic groups (PeerChat) and
    # supergroups/megagroups (PeerChannel with megagroup=True) — see
    # _classify_peer(). "channel" means broadcast channels only
    # (PeerChannel with megagroup=False).
    VALID_SCOPES: frozenset[str] = frozenset({"private", "bot", "group", "channel"})

    #: Scope active on first run / if reaction_scope.json is missing.
    #: Matches the pre-v3.0.8 hardcoded defaults exactly (ENABLE_FOR_BOTS
    #: was the only one that defaulted to True), so upgrading an existing
    #: install without reconfiguring changes nothing observable.
    _DEFAULT_SCOPES: frozenset[str] = frozenset({"bot"})

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self._settings_file = self.cfg.settings_dir / "reactions.json"
        self._reactions: dict[str, str] = {}
        self._me_id: int | None = None

        # ── Dynamic scope state (v3.0.8) ────────────────────────────────
        self._scope_settings_file = self.cfg.settings_dir / "reaction_scope.json"
        self._active_scopes: set[str] = set(self._DEFAULT_SCOPES)

        # channel_id -> is this PeerChannel a supergroup/megagroup (True)
        # or a broadcast channel (False)? Populated on first sight per
        # channel, same caching pattern as _peer_is_bot_cache below.
        self._channel_megagroup_cache: dict[int, bool] = {}

        # (chat_id, msg_id) -> currently-active self-reaction emojis, as of
        # the most recently processed update. Replaces the old permanent
        # "every emoji ever fired" set — see module docstring's "Reaction
        # state tracking" section for the full rationale.
        self._active_reactions: dict[tuple[int, int], frozenset[str]] = {}

        # v3.0.12 (Gate 5b): (chat_id, msg_id, emoji) -> time.monotonic()
        # timestamp of the last time this exact trigger actually executed a
        # command. See _REACTION_COOLDOWN_SECONDS above for the rationale.
        # Deliberately in-memory only, same as _active_reactions — a restart
        # clears the cooldown along with the rising-edge state, which is
        # consistent with existing behavior rather than a new inconsistency.
        self._last_fired: dict[tuple[int, int, str], float] = {}

        # user_id -> is this peer a bot. Populated on first sight per user,
        # avoiding a repeated client.get_entity() call for every reaction
        # event from/about a user we've already classified.
        self._peer_is_bot_cache: dict[int, bool] = {}

        self._client: TelegramClient | None = None

        # Post-Startup Flag: only process reactions after module is fully ready
        self._is_ready: bool = False

        # Track background tasks created in setup() so teardown() can
        # cancel them on hot-reload, preventing a stale task from
        # overwriting _me_id / _is_ready on a freshly-reloaded instance.
        self._me_id_task: asyncio.Task | None = None
        self._ready_task: asyncio.Task | None = None

        # v3.0.12: periodic "still alive" heartbeat task — see setup()/
        # teardown() and _heartbeat_loop() below.
        self._heartbeat_task: asyncio.Task | None = None

    def setup(self, client: TelegramClient) -> None:
        self._client = client

        # v3.0.12: each of these four calls used to run unwrapped, back to
        # back — a single bad disk read (permissions glitch, transient I/O
        # error, full disk, etc.) in any one of them would raise out of
        # setup() entirely and skip the _add_handler() calls below it,
        # silently leaving this module with no registered handlers at all
        # (see loader.py's reattach()/_do_load() — a setup() exception is
        # exactly the failure mode that produces a module that looks loaded
        # but never receives another event). Wrapping each call
        # individually means a bad settings/scope read degrades to
        # "defaults for this run" instead of "module never wakes up".
        try:
            self._ensure_settings_file()
        except Exception as exc:
            self._log_error("ensure_settings_file failed during setup(): %s", exc)
        try:
            self._load_settings()
        except Exception as exc:
            self._log_error("load_settings failed during setup(): %s", exc)
        try:
            self._ensure_scope_file()
        except Exception as exc:
            self._log_error("ensure_scope_file failed during setup(): %s", exc)
        try:
            self._load_scope()
        except Exception as exc:
            self._log_error("load_scope failed during setup(): %s", exc)

        # Register command handler
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_command)

        # Method 1: UpdateMessageReactions (primary push route)
        self._add_handler(client, events.Raw(UpdateMessageReactions), self._on_reaction_update)

        # Method 2: UpdateEditMessage (secondary push route)
        self._add_handler(client, events.Raw(UpdateEditMessage), self._on_edit_update)

        # Cache self ID — task reference stored for teardown() cancellation.
        self._me_id_task = asyncio.create_task(
            self._cache_me_id(client), name=f"reaction_me_a{self.cfg.index}"
        )

        # Schedule readiness flag (after 3s to skip catch-up wave) —
        # task reference stored for teardown() cancellation.
        self._ready_task = asyncio.create_task(
            self._set_ready(), name=f"reaction_ready_a{self.cfg.index}"
        )

        # v3.0.12: periodic "still alive" heartbeat — see _heartbeat_loop().
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name=f"reaction_heartbeat_a{self.cfg.index}"
        )

        self._log_info(
            "ReactionCommands ready (Funnel Architecture). %d reactions configured. "
            "Active scope: %s",
            len(self._reactions), sorted(self._active_scopes) or "none",
        )

    def teardown(self, client: TelegramClient) -> None:
        if self._me_id_task is not None and not self._me_id_task.done():
            self._me_id_task.cancel()
        if self._ready_task is not None and not self._ready_task.done():
            self._ready_task.cancel()
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._me_id_task = None
        self._ready_task = None
        self._heartbeat_task = None

        # v3.0.8: _active_reactions (Gate 5 dedup state), _peer_is_bot_cache
        # and _channel_megagroup_cache are deliberately NOT cleared here.
        # teardown() runs on every AccountReconnector.reattach() call, not
        # just on a genuine hot-reload — and reattach() reuses this exact
        # instance (core/loader.py reattach()'s own docstring: "module
        # instances themselves are reused — no re-import needed"). Clearing
        # these on a routine reconnect served no purpose and caused a real
        # duplicate-fire bug: a reaction still present on a message across
        # a disconnect would look like a brand-new rising edge once
        # catch_up redelivered its state after reconnect. A genuine
        # hot-reload gets a fresh instance (and therefore fresh, empty
        # dicts from __init__) via create_module() regardless of what
        # happens here, so this change only affects the reconnect path.
        self._me_id = None
        self._is_ready = False
        self._client = None
        super().teardown(client)

    # ── Post-Startup Readiness ────────────────────────────────────────────

    async def _set_ready(self) -> None:
        """Wait for catch-up wave to pass, then enable processing."""
        try:
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            return
        self._is_ready = True
        self._log_info("Module marked as ready — processing new reactions.")

    # ── Heartbeat (v3.0.12) ──────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """
        Periodically log that this module is still alive and receiving
        control.

        Task 2B's silent-failure symptom ("no matter how many reactions are
        added, no command is executed, and no log output appears — not even
        a debug log") is only diagnosable from the outside if *something*
        logs on a schedule regardless of whether any reaction ever arrives.
        This loop is that something: if it stops appearing in the logs, the
        module's own event loop / task scheduling has died (extremely
        unlikely); if reactions stop working but this keeps appearing, the
        problem is upstream (handlers not receiving updates) rather than
        this module having gone entirely dark, which narrows debugging
        immediately.
        """
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
                self._log_info(
                    "alive — %d reactions configured, ready=%s",
                    len(self._reactions), self._is_ready,
                )
        except asyncio.CancelledError:
            return

    # ── Self ID cache ─────────────────────────────────────────────────────

    async def _cache_me_id(self, client: TelegramClient) -> None:
        try:
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            return
        try:
            if client.is_connected():
                me = await client.get_me()
                if me:
                    self._me_id = me.id
                    self._log_info("Cached self ID: %d", self._me_id)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._log_error("Failed to cache me_id: %s", exc)

    # ── Environment Filter (Gate 2) ───────────────────────────────────────

    async def _check_environment_filter(self, peer_id, client: TelegramClient) -> bool:
        """
        Gate 2: Check if the environment (chat type) is enabled.

        Returns True if the environment is ENABLED (should process).
        Returns False if the environment is DISABLED (should drop).

        v3.0.8: delegates classification to _classify_peer() (shared with
        _execute_command_directly's MockEvent chat-type flags — see its
        own docstring) and checks membership in the dynamically
        configurable _active_scopes set instead of the old hardcoded
        ENABLE_FOR_* booleans. Same caching characteristics as before:
        the first reaction event from/about a given user or channel pays
        for one client.get_entity() call, every subsequent one is a plain
        dict lookup with no possibility of a network call at all.
        """
        kind = await self._classify_peer(peer_id, client)
        if not kind:
            return False  # Unknown peer type — drop
        return kind in self._active_scopes

    # ── Settings I/O ──────────────────────────────────────────────────────

    def _ensure_settings_file(self) -> None:
        if not self._settings_file.exists():
            default_reactions = {"👌": "clear txt", "👍": "join"}
            err = write_json_file_atomic(self._settings_file, default_reactions, indent=2)
            if err is not None:
                self._log_error("Failed to create reactions.json: %s", err)
            else:
                self._log_info("Created default reactions.json")

    def _load_settings(self) -> None:
        data, err = read_json_file(self._settings_file)
        if err is not None:
            self._log_error("Failed to load reactions.json: %s", err)
            self._reactions = {}
            return
        if data is None:
            self._reactions = {}
            return
        self._reactions = {str(k): str(v) for k, v in data.items()}
        self._log_info("Loaded %d reaction mappings", len(self._reactions))

    def _save_settings(self) -> bool:
        """
        Atomically persist settings to disk via a temp-file + rename, so a
        crash mid-write can never leave a truncated reactions.json.
        """
        err = write_json_file_atomic(self._settings_file, self._reactions, indent=2)
        if err is not None:
            self._log_error("Failed to save reactions.json: %s", err)
            return False
        return True

    # ── Scope settings I/O (v3.0.8) ─────────────────────────────────────────

    def _ensure_scope_file(self) -> None:
        if not self._scope_settings_file.exists():
            default_data = {"active_scopes": sorted(self._DEFAULT_SCOPES)}
            err = write_json_file_atomic(self._scope_settings_file, default_data, indent=2)
            if err is not None:
                self._log_error("Failed to create reaction_scope.json: %s", err)
            else:
                self._log_info("Created default reaction_scope.json (scope: bot)")

    def _load_scope(self) -> None:
        data, err = read_json_file(self._scope_settings_file)
        if err is not None:
            self._log_error("Failed to load reaction_scope.json: %s", err)
            self._active_scopes = set(self._DEFAULT_SCOPES)
            return
        if data is None:
            self._active_scopes = set(self._DEFAULT_SCOPES)
            return

        raw_scopes = data.get("active_scopes", [])
        if not isinstance(raw_scopes, list):
            self._log_warning("reaction_scope.json: 'active_scopes' is not a list — using default.")
            self._active_scopes = set(self._DEFAULT_SCOPES)
            return

        # Silently drop any value that isn't currently valid (e.g. a stale
        # value from a future/older format) rather than failing to load —
        # same defensive spirit as _load_settings' str()-coercion above.
        self._active_scopes = {v for v in raw_scopes if v in self.VALID_SCOPES}
        self._log_info("Loaded reaction scope: %s", sorted(self._active_scopes) or "none")

    def _save_scope(self) -> bool:
        """Atomically persist the active scope set (see _save_settings)."""
        data = {"active_scopes": sorted(self._active_scopes)}
        err = write_json_file_atomic(self._scope_settings_file, data, indent=2)
        if err is not None:
            self._log_error("Failed to save reaction_scope.json: %s", err)
            return False
        return True

    # ── Command handler ───────────────────────────────────────────────────

    async def _on_command(self, event) -> None:
        text = (event.raw_text or "").strip()
        parts = text.split(maxsplit=1)

        if not parts:
            return

        cmd = parts[0].lower()

        if cmd in ("reactions", "reaction", ".reaction_scope"):
            if not await self._is_saved_messages(event):
                return

        if cmd == "reactions":
            await self._cmd_list(event)
        elif cmd == "reaction" and len(parts) > 1:
            subcmd = parts[1].split(maxsplit=1)
            if subcmd[0].lower() == "add" and len(subcmd) > 1:
                await self._cmd_add(event, subcmd[1])
            elif subcmd[0].lower() == "remove" and len(subcmd) > 1:
                await self._cmd_remove(event, subcmd[1].strip())
            elif subcmd[0].lower() == "clear":
                await self._cmd_clear(event)
        elif cmd == ".reaction_scope":
            arg = parts[1].strip() if len(parts) > 1 else ""
            await self._cmd_scope(event, arg)

    async def _cmd_list(self, event) -> None:
        if not self._reactions:
            await self._safe_edit(
                event,
                "ℹ️ هیچ reaction ای تنظیم نشده است.\n\n"
                "**نحوه استفاده:**\n"
                "`reaction add 👌 clear txt`\n"
                "`reaction add 👍 join`"
            )
            return

        lines = ["📋 **لیست Reaction های فعال:**\n"]
        for emoji, command in sorted(self._reactions.items()):
            lines.append(f"• `{emoji}` → `{command}`")

        lines.append(f"\n📊 **تعداد:** {len(self._reactions)} reaction")
        lines.append("\n💡 **نحوه استفاده:** روی یک پیام react کنید، دستور اجرا می‌شود.")

        await self._safe_edit(event, "\n".join(lines))

    async def _cmd_add(self, event, args: str) -> None:
        parts = args.split(maxsplit=1)

        if len(parts) < 2:
            await self._safe_edit(
                event,
                "❌ **فرمت نادرست**\n\n"
                "**استفاده:** `reaction add <emoji> <command>`\n"
                "**مثال:** `reaction add 👍 join`"
            )
            return

        emoji = parts[0].strip()
        command = parts[1].strip()

        if not emoji or not command:
            await self._safe_edit(event, "❌ emoji و command نمی‌توانند خالی باشند.")
            return

        self._reactions[emoji] = command

        if self._save_settings():
            await self._safe_edit(
                event,
                f"✅ **Reaction اضافه شد!**\n\n"
                f"• `{emoji}` → `{command}`\n\n"
                f"💡 حالا روی هر پیامی که `{emoji}` react کنید، `{command}` اجرا می‌شود."
            )
        else:
            await self._safe_edit(event, "❌ خطا در ذخیره تنظیمات.")

    async def _cmd_remove(self, event, emoji: str) -> None:
        if emoji not in self._reactions:
            await self._safe_edit(
                event,
                f"❌ Reaction `{emoji}` یافت نشد.\n\n"
                f"برای دیدن لیست: `reactions`"
            )
            return

        command = self._reactions.pop(emoji)

        if self._save_settings():
            await self._safe_edit(
                event,
                f"✅ **Reaction حذف شد!**\n\n"
                f"• `{emoji}` → `{command}`"
            )
        else:
            await self._safe_edit(event, "❌ خطا در ذخیره تنظیمات.")

    async def _cmd_clear(self, event) -> None:
        if not self._reactions:
            await self._safe_edit(event, "ℹ️ هیچ reaction ای برای حذف وجود ندارد.")
            return

        count = len(self._reactions)
        self._reactions.clear()

        if self._save_settings():
            await self._safe_edit(
                event,
                f"✅ **همه reaction ها پاک شدند!**\n\n"
                f"📊 تعداد حذف‌شده: {count}"
            )
        else:
            await self._safe_edit(event, "❌ خطا در ذخیره تنظیمات.")

    async def _cmd_scope(self, event, arg: str) -> None:
        """
        `.reaction_scope`            — show currently active chat types.
        `.reaction_scope <value>`    — toggle one of private/bot/group/channel
                                        on or off (add if absent, remove if
                                        present — so running the same command
                                        twice is a clean on/off switch).
        `.reaction_scope all`        — activate every chat type.
        `.reaction_scope none`       — deactivate every chat type.
        """
        if not arg:
            await self._safe_edit(
                event,
                "📋 **Scope فعال ری‌اکشن‌ها:**\n\n"
                f"{self._format_scope_lines()}\n\n"
                "💡 برای تغییر: `.reaction_scope <private|bot|group|channel|all|none>`"
            )
            return

        value = arg.lower()

        if value == "all":
            self._active_scopes = set(self.VALID_SCOPES)
        elif value == "none":
            self._active_scopes = set()
        elif value in self.VALID_SCOPES:
            if value in self._active_scopes:
                self._active_scopes.discard(value)
            else:
                self._active_scopes.add(value)
        else:
            await self._safe_edit(
                event,
                f"❌ مقدار نامعتبر: `{arg}`\n\n"
                "**مقادیر مجاز:** `private`, `bot`, `group`, `channel`, `all`, `none`"
            )
            return

        if self._save_scope():
            await self._safe_edit(
                event,
                "✅ **Scope به‌روزرسانی شد!**\n\n"
                f"{self._format_scope_lines()}"
            )
        else:
            await self._safe_edit(event, "❌ خطا در ذخیره تنظیمات scope.")

    def _format_scope_lines(self) -> str:
        labels = {
            "private": "چت خصوصی (کاربران)",
            "bot":     "چت خصوصی (ربات‌ها)",
            "group":   "گروه و سوپرگروه",
            "channel": "کانال (broadcast)",
        }
        lines = []
        for value in ("private", "bot", "group", "channel"):
            mark = "✅" if value in self._active_scopes else "◻️"
            lines.append(f"{mark} `{value}` — {labels[value]}")
        return "\n".join(lines)

    # ── Peer classification (v3.0.8) ────────────────────────────────────────

    async def _classify_peer(self, peer_id, client: TelegramClient) -> str:
        """
        Classify a peer into one of the four scope buckets: "private",
        "bot", "group", "channel". Returns "" if the peer type is
        unrecognized (caller should treat that as "drop").

        - PeerUser: "bot" or "private", via the existing bot/user cache
          (unchanged from the pre-v3.0.8 _check_environment_filter logic).
        - PeerChat: always "group" (legacy basic groups are unambiguous).
        - PeerChannel: "group" if the channel is a supergroup/megagroup,
          "channel" if it's a broadcast channel — resolved via
          client.get_entity() on first sight per channel_id, then cached
          (same shape as the bot/user cache; see _channel_megagroup_cache
          in __init__). A transient lookup failure is NOT cached — same
          reasoning as the existing bot/user classification fallback
          below — and defaults to "channel" (the more restrictive,
          off-by-default bucket) rather than guessing "group".
        """
        if isinstance(peer_id, PeerUser):
            user_id = peer_id.user_id
            is_bot = self._peer_is_bot_cache.get(user_id)
            if is_bot is None:
                try:
                    entity = await client.get_entity(user_id)
                    is_bot = bool(getattr(entity, 'bot', False))
                except Exception:
                    return "private"  # safer default; not cached (retry next time)
                self._peer_is_bot_cache[user_id] = is_bot
            return "bot" if is_bot else "private"

        elif isinstance(peer_id, PeerChat):
            return "group"

        elif isinstance(peer_id, PeerChannel):
            channel_id = peer_id.channel_id
            is_megagroup = self._channel_megagroup_cache.get(channel_id)
            if is_megagroup is None:
                try:
                    entity = await client.get_entity(peer_id)
                    is_megagroup = bool(getattr(entity, 'megagroup', False))
                except Exception:
                    return "channel"  # safer/off-by-default guess; not cached
                self._channel_megagroup_cache[channel_id] = is_megagroup
            return "group" if is_megagroup else "channel"

        return ""

    # ── Method 1: UpdateMessageReactions (Primary Push Route) ─────────────

    async def _on_reaction_update(self, event) -> None:
        """
        Handle UpdateMessageReactions — the primary push route.

        v3.0.12: this is now a thin try/except wrapper around
        _on_reaction_update_impl(). Telethon's own docs note that
        exceptions raised inside event-handler callbacks are hidden by
        default unless the caller has separately configured logging for
        Telethon's internal logger — which means a bug inside this handler
        could previously produce *zero* log output anywhere, matching Task
        2B's "not even a debug log" symptom exactly. This wrapper logs via
        this project's own logger unconditionally, regardless of how
        Telethon's logging is configured elsewhere, so that failure mode
        can never be silent again.
        """
        try:
            await self._on_reaction_update_impl(event)
        except Exception as exc:
            self._log_error("Unhandled error in _on_reaction_update: %s", exc)

    async def _on_reaction_update_impl(self, event) -> None:
        """
        Handle UpdateMessageReactions — the primary push route.

        BUG FIX (v3.0.2): this used to read `event.peer_id` and
        `event.chat_id`. Neither exists on the raw UpdateMessageReactions
        update — its actual fields (confirmed against the Telethon
        library's own type definition) are `peer`, `msg_id`, `reactions`,
        `top_msg_id`, `saved_peer_id`. Reading the wrong names meant both
        always evaluated to None via getattr's default, and this handler
        always hit an early `return` — it never processed a single
        reaction, silently, the entire time. All detection was actually
        coming from Method 2 alone.

        Now reads the update's real fields directly: `event.peer` (the
        chat, converted to the standard chat_id integer via Telethon's own
        get_peer_id() — a pure local computation, no network call) and
        `event.reactions` (the reaction data itself, already attached to
        the update — no need to re-fetch the message via get_messages()
        the way the old, broken code path did on top of its two attribute
        bugs). This removes the one unconditional extra API round-trip
        from the hot path entirely, on top of making it functional.

        Applies Funnel Architecture:
        Gate 1: Post-Startup check (_is_ready)
        Gate 2: Environment filter (peer type)
        Gate 3: Self-Only filter (recent_reactions)
        Gate 4: Mapping filter (emoji in _reactions)
        Gate 5: Reaction-state tracking (_active_reactions)
        Gate 5b: Per-trigger cooldown (_last_fired) — v3.0.12
        """
        # Gate 1: Post-Startup Filter
        if not self._is_ready:
            return

        if not self._reactions or self._me_id is None:
            return

        client = self._client
        if client is None:
            # teardown() has already run on this instance (hot-reload race);
            # there is nothing safe left to operate on.
            return

        peer = getattr(event, 'peer', None)
        if peer is None:
            return

        # Gate 2: Environment Filter
        if not await self._check_environment_filter(peer, client):
            return

        msg_id = getattr(event, 'msg_id', None)
        if msg_id is None:
            return

        reactions = getattr(event, 'reactions', None)
        chat_id = telethon_utils.get_peer_id(peer)

        await self._process_reaction_update(chat_id, msg_id, reactions)

    # ── Method 2: UpdateEditMessage (Secondary Push Route) ────────────────

    async def _on_edit_update(self, event) -> None:
        """
        Handle UpdateEditMessage — secondary push route.

        v3.0.12: thin try/except wrapper around _on_edit_update_impl() —
        see _on_reaction_update's wrapper docstring for why this matters
        (Telethon hides handler exceptions by default).
        """
        try:
            await self._on_edit_update_impl(event)
        except Exception as exc:
            self._log_error("Unhandled error in _on_edit_update: %s", exc)

    async def _on_edit_update_impl(self, event) -> None:
        """
        Handle UpdateEditMessage — secondary push route.

        Telegram sometimes sends UpdateEditMessage instead of
        UpdateMessageReactions when reactions change.

        Only processes if message.reactions exists in the payload.
        """
        # Gate 1: Post-Startup Filter
        if not self._is_ready:
            return

        if not self._reactions or self._me_id is None:
            return

        client = self._client
        if client is None:
            return

        message = getattr(event, 'message', None)
        if not message:
            return

        # Only process if reactions exist in the payload
        if not hasattr(message, 'reactions') or not message.reactions:
            return

        # Extract peer_id from message
        peer_id = getattr(message, 'peer_id', None)
        if peer_id is None:
            # peer_id was missing on the message itself. We cannot safely
            # assume PeerUser here — the chat could just as easily be a
            # channel or basic group, and wrapping a channel/group ID in
            # PeerUser would make the environment filter misclassify it.
            # Resolve the real peer type via the client instead.
            chat_id = getattr(message, 'chat_id', None)
            if chat_id is None:
                return
            try:
                entity = await client.get_entity(chat_id)
            except Exception:
                return
            if getattr(entity, 'broadcast', False) or getattr(entity, 'megagroup', False):
                peer_id = PeerChannel(chat_id)
            elif hasattr(entity, 'participants_count') and not hasattr(entity, 'access_hash'):
                # Basic Chat objects don't carry access_hash; Channels do.
                peer_id = PeerChat(chat_id)
            else:
                peer_id = PeerUser(chat_id)

        if peer_id is None:
            return

        # Gate 2: Environment Filter
        if not await self._check_environment_filter(peer_id, client):
            return

        # Method 2 already has the full Message, so pass it through as
        # target_msg — commands that need the actual message content
        # (join/left/info/whois) then skip the lazy re-fetch in
        # _execute_command_directly() entirely, same as before.
        await self._process_reaction_update(
            message.chat_id, message.id, message.reactions, target_msg=message
        )

    # ── Core Reaction Processing (Gates 3-5) ──────────────────────────────

    async def _process_reaction_update(
        self,
        chat_id: int,
        msg_id: int,
        reactions,
        target_msg: Message | None = None,
    ) -> None:
        """
        Process a reaction-state snapshot for one message.

        Takes the reactions data directly (chat_id/msg_id/reactions) rather
        than a full Message object, since Method 1 no longer fetches one
        (see _on_reaction_update's docstring). `target_msg` is the actual
        Message when the caller already has one (Method 2 always does;
        Method 1 never does) — passed straight through to
        _execute_command_directly(), which only fetches it lazily itself if
        the specific triggered command actually needs message content.

        Applies Gates 3-5 of the Funnel:
        Gate 3: Self-Only filter (check recent_reactions for self._me_id)
        Gate 4: Mapping filter (emoji in self._reactions)
        Gate 5: Reaction-state tracking (rising-edge trigger; see module
                docstring's "Reaction state tracking" section)
        """
        if reactions is None or not isinstance(reactions, MessageReactions):
            return

        recent_reactions = getattr(reactions, 'recent_reactions', None) or []

        # Gate 3: Self-Only Filter — the set of emojis I have reacted with
        # on this message *right now*, per this update.
        current_self_emojis: set[str] = set()

        for recent in recent_reactions:
            peer_id = getattr(recent, 'peer_id', None)
            if not peer_id or not isinstance(peer_id, PeerUser):
                continue

            user_id = getattr(peer_id, 'user_id', None)
            if user_id != self._me_id:
                continue

            recent_reaction = getattr(recent, 'reaction', None)
            if not recent_reaction or not isinstance(recent_reaction, ReactionEmoji):
                continue

            emoji_str = getattr(recent_reaction, 'emoticon', None)
            if emoji_str:
                current_self_emojis.add(emoji_str)

        # Gate 5: compare against what we last knew for this message, and
        # only the emojis that are newly present count as a trigger. This
        # is a rising-edge check, not a permanent "have I ever seen this"
        # check — a redundant re-delivery of the SAME current state (e.g.
        # both Method 1 and Method 2 firing for one underlying change)
        # produces an empty `newly_added` and does nothing, same
        # duplicate-prevention guarantee as before.
        key = (chat_id, msg_id)
        previous_self_emojis = self._active_reactions.get(key, frozenset())
        newly_added = current_self_emojis - previous_self_emojis

        # Update tracked state to match current reality. Doing this
        # unconditionally (including clearing it when current_self_emojis
        # is empty) is the actual v3.0.2 fix for "changing or re-adding a
        # reaction doesn't retrigger": a message this account no longer has
        # a reaction on is forgotten entirely, so reacting with the same
        # emoji again later is correctly treated as new, not as something
        # already "used up" by the old permanent record.
        #
        # Theoretical edge case worth knowing about: Telegram truncates
        # `recent_reactions` to a limited "recent" list for messages with
        # many distinct reactors, so on a very popular message your own
        # entry could in principle fall out of that window and look like
        # "no reaction" here even though it's still technically active.
        # Not a concern for this bot's normal use (your own messages, small
        # group/DM chats) but noted for completeness.
        if current_self_emojis:
            self._remember_reaction_state(key, current_self_emojis)
        elif key in self._active_reactions:
            del self._active_reactions[key]

        if not newly_added:
            return

        # Gate 4: Mapping filter + execute
        for emoji_str in newly_added:
            if emoji_str not in self._reactions:
                continue

            # Gate 5b (v3.0.12): per-trigger cooldown. Gate 5 above is a
            # pure state-comparison rising-edge check — it deliberately
            # allows a genuine remove-then-re-add of the same emoji to
            # fire again, which is correct for a deliberate re-trigger
            # minutes later, but looks identical to a rapid double-tap /
            # flicker seconds later. This gate suppresses an identical
            # (chat_id, msg_id, emoji) trigger for _REACTION_COOLDOWN_SECONDS
            # after it last actually fired, without affecting a different
            # emoji on the same message or the same emoji on a different
            # message (both use a different key and are unaffected).
            cooldown_key = (chat_id, msg_id, emoji_str)
            now = time.monotonic()
            last_fired = self._last_fired.get(cooldown_key)
            if last_fired is not None and (now - last_fired) < _REACTION_COOLDOWN_SECONDS:
                self._log_debug(
                    "⏳ Cooldown active, skipping: emoji=%s, chat=%d, msg=%d "
                    "(%.1fs remaining)",
                    emoji_str, chat_id, msg_id,
                    _REACTION_COOLDOWN_SECONDS - (now - last_fired),
                )
                continue
            self._remember_last_fired(cooldown_key, now)

            command_text = self._reactions[emoji_str]

            self._log_debug(
                "✅ Reaction detected: emoji=%s, chat=%d, msg=%d, action=%s",
                emoji_str, chat_id, msg_id, command_text
            )

            # Execute command DIRECTLY
            try:
                await self._execute_command_directly(chat_id, msg_id, command_text, target_msg)
            except Exception as exc:
                self._log_error("Failed to execute command: %s", exc)

    def _remember_reaction_state(self, key: tuple[int, int], emojis: set[str]) -> None:
        """
        Record *emojis* as the current known reaction state for *key*, and
        evict the oldest tracked messages in a batch if the total count
        exceeds `_MAX_TRACKED_MESSAGES` — same oldest-first, evict-to-half
        pattern the old `_processed` set used, just keyed per-message
        instead of per-message-per-emoji (so it naturally holds far fewer
        entries for the same usage pattern). Plain dicts preserve insertion
        order in Python 3.7+, so no separate ordering list is needed the
        way the old design required.
        """
        is_new_key = key not in self._active_reactions
        self._active_reactions[key] = frozenset(emojis)

        if is_new_key and len(self._active_reactions) > _MAX_TRACKED_MESSAGES:
            overflow = len(self._active_reactions) - (_MAX_TRACKED_MESSAGES // 2)
            for old_key in list(self._active_reactions.keys())[:overflow]:
                del self._active_reactions[old_key]

    def _remember_last_fired(self, key: tuple[int, int, str], when: float) -> None:
        """
        Record *when* (a time.monotonic() timestamp) as the last-fired time
        for the (chat_id, msg_id, emoji) cooldown *key* — see
        _REACTION_COOLDOWN_SECONDS / Gate 5b — and evict the oldest tracked
        cooldown entries in a batch if the total count exceeds
        `_MAX_TRACKED_COOLDOWNS`, same oldest-first, evict-to-half pattern
        as `_remember_reaction_state`.
        """
        is_new_key = key not in self._last_fired
        self._last_fired[key] = when

        if is_new_key and len(self._last_fired) > _MAX_TRACKED_COOLDOWNS:
            overflow = len(self._last_fired) - (_MAX_TRACKED_COOLDOWNS // 2)
            for old_key in list(self._last_fired.keys())[:overflow]:
                del self._last_fired[old_key]

    # ── Direct command execution ──────────────────────────────────────────

    async def _execute_command_directly(
        self,
        chat_id: int,
        target_msg_id: int,
        command_text: str,
        target_msg: Message | None = None,
    ) -> None:
        """
        Execute a command by directly invoking the target module's handler.
        Uses a MockEvent (modules/bridge.py) that is fully compatible with
        clearer.py, join_left.py, info_handler.py, and whois_handler.py.

        Falls back to send_message if direct invocation is unavailable.
        """
        try:
            loader = self.context.loader_registry.get(self.cfg.index)
        except LoaderNotFoundError:
            self._log_warning(
                "No loader registered for account #%d — cannot execute '%s'.",
                self.cfg.index, command_text,
            )
            return

        client = loader.client

        if not client or not client.is_connected():
            self._log_warning("Client not connected")
            return

        command_parts = command_text.strip().split()
        if not command_parts:
            return

        command_name = command_parts[0].lower()
        self._log_debug("Executing directly: %s", command_text)

        # v3.0.8: resolve is_private/is_group/is_channel for MockEvent.
        # telethon_utils.resolve_id() is a pure local computation on the
        # marked chat_id integer (no API call) that reconstructs the peer
        # type; _classify_peer() then resolves bot-vs-user /
        # supergroup-vs-channel, hitting the same caches Gate 2 already
        # warmed for this chat a moment earlier in the common case — see
        # both methods' docstrings. This normally costs zero extra API
        # calls; a cache miss (first time this chat is ever seen) costs
        # at most one client.get_entity() call, same as Gate 2 would have
        # already paid.
        real_id, peer_cls = telethon_utils.resolve_id(chat_id)
        chat_kind = await self._classify_peer(peer_cls(real_id), client)
        mock_is_private = chat_kind in ("private", "bot")
        mock_is_group = chat_kind == "group"
        mock_is_channel = chat_kind == "channel"

        # ── clear (compatible with clearer.py) ────────────────────────────
        if command_name == "clear":
            clearer_instance = loader.get_module("clearer")
            if clearer_instance is not None:
                try:
                    mock_event = MockEvent(
                        client, chat_id, target_msg_id, command_text,
                        is_private=mock_is_private, is_group=mock_is_group,
                        is_channel=mock_is_channel,
                    )
                    await clearer_instance._on_command(mock_event)
                    self._log_debug("✅ Invoked clearer directly")
                    return
                except Exception as exc:
                    self._log_error("Direct clearer invocation failed: %s", exc)

        # ── join / left (compatible with join_left.py) ────────────────────
        elif command_name in ("join", "left"):
            join_left_instance = loader.get_module("join_left")
            if join_left_instance is not None:
                try:
                    # Fetch the target message if not already provided
                    if target_msg is None:
                        target_msg = await client.get_messages(chat_id, ids=target_msg_id)

                    mock_event = MockEvent(
                        client, chat_id, target_msg_id, command_text,
                        target_msg=target_msg,
                        is_private=mock_is_private, is_group=mock_is_group,
                        is_channel=mock_is_channel,
                    )
                    # Call _dispatch which routes to _handle_join or _handle_left
                    await join_left_instance._dispatch(mock_event)
                    self._log_debug("✅ Invoked join_left.%s directly", command_name)
                    return
                except Exception as exc:
                    self._log_error("Direct join_left invocation failed: %s", exc)

        # ── info (compatible with info_handler.py) ────────────────────────
        elif command_name == "info":
            info_instance = loader.get_module("info_handler")
            if info_instance is not None:
                try:
                    if target_msg is None:
                        target_msg = await client.get_messages(chat_id, ids=target_msg_id)
                    mock_event = MockEvent(
                        client, chat_id, target_msg_id, command_text,
                        target_msg=target_msg,
                        is_private=mock_is_private, is_group=mock_is_group,
                        is_channel=mock_is_channel,
                    )
                    await info_instance._on_command(mock_event)
                    self._log_debug("✅ Invoked info_handler directly")
                    return
                except Exception as exc:
                    self._log_error("Direct info_handler invocation failed: %s", exc)

        # ── whois (compatible with whois_handler.py) ──────────────────────
        elif command_name == "whois":
            whois_instance = loader.get_module("whois_handler")
            if whois_instance is not None:
                try:
                    if target_msg is None:
                        target_msg = await client.get_messages(chat_id, ids=target_msg_id)
                    mock_event = MockEvent(
                        client, chat_id, target_msg_id, command_text,
                        target_msg=target_msg,
                        is_private=mock_is_private, is_group=mock_is_group,
                        is_channel=mock_is_channel,
                    )
                    await whois_instance._on_command(mock_event)
                    self._log_debug("✅ Invoked whois_handler directly")
                    return
                except Exception as exc:
                    self._log_error("Direct whois_handler invocation failed: %s", exc)

        # ── Fallback: send as message ─────────────────────────────────────
        self._log_warning(
            "Direct invocation not available for '%s', falling back to send_message",
            command_name
        )
        try:
            await client.send_message(
                chat_id,
                command_text,
                reply_to=target_msg_id
            )
            self._log_debug("Sent command as message (fallback)")
        except Exception as exc:
            self._log_error("Failed to send command: %s", exc)


# ── Help Texts (در انتهای ماژول طبق قوانین) ─────────────────────────────────

help_text = (
    "• `reactions` | لیست reaction های تنظیم‌شده\n"
    "• `reaction add <emoji> <command>` | افزودن mapping\n"
    "• `reaction remove <emoji>` | حذف یک mapping\n"
    "• `reaction clear` | حذف همه mapping ها\n"
    "• `.reaction_scope` | نمایش/تغییر محدوده فعال (چت خصوصی/ربات/گروه/کانال)\n"
)

# Bug fix (this refactor): the previous help_extra claimed a "Method 3 |
# Smart Polling" detection path and "polling هوشمند با آگاهی از FloodWait" in
# نکات مهم. Neither exists anywhere in this file — the module's own top
# docstring says "Zero Polling" / "No get_dialogs/get_messages calls", and
# only Method 1 (UpdateMessageReactions) and Method 2 (UpdateEditMessage) are
# ever registered as handlers. That was stale documentation left over from
# an earlier version of the module; removed here so `help reaction_commands`
# describes what the code actually does.
help_extra = (
    "Reaction Commands - اجرای دستورات با ری‌اکشن\n\n"
    "دستورات اصلی:\n"
    "• `reactions` | نمایش لیست همه reaction های فعال\n"
    "• `reaction add <emoji> <command>` | افزودن mapping جدید\n"
    "• `reaction remove <emoji>` | حذف یک mapping\n"
    "• `reaction clear` | حذف همه mapping ها\n\n"
    "محدوده فعال (Scope) — از نسخه ۳.۰.۸:\n"
    "دیگر نیازی به ویرایش فایل ماژول نیست؛ محدوده چت‌هایی که ری‌اکشن در آن‌ها\n"
    "پردازش می‌شود کاملاً از طریق دستور زیر و در زمان اجرا قابل تنظیم است:\n"
    "• `.reaction_scope` | نمایش وضعیت فعلی محدوده\n"
    "• `.reaction_scope private` | فعال/غیرفعال‌سازی چت خصوصی با کاربران\n"
    "• `.reaction_scope bot` | فعال/غیرفعال‌سازی چت خصوصی با ربات‌ها\n"
    "• `.reaction_scope group` | فعال/غیرفعال‌سازی گروه‌ها **و سوپرگروه‌ها**\n"
    "• `.reaction_scope channel` | فعال/غیرفعال‌سازی کانال‌های broadcast\n"
    "• `.reaction_scope all` | فعال‌سازی همه محدوده‌ها\n"
    "• `.reaction_scope none` | غیرفعال‌سازی همه محدوده‌ها\n"
    "هر بار اجرای دستور با یک مقدار، همان مقدار را toggle می‌کند (روشن↔خاموش).\n"
    "پیش‌فرض کارخانه‌ای: فقط `bot` فعال است. تنظیمات در `reaction_scope.json`\n"
    "(به‌ازای هر اکانت، جدا از `reactions.json`) ذخیره می‌شود.\n\n"
    "روش‌های تشخیص ری‌اکشن:\n"
    "این ماژول از ۲ روش push-based استفاده می‌کند (بدون polling):\n"
    "• Method 1 | `UpdateMessageReactions` برای پیام‌های خودتان\n"
    "• Method 2 | `UpdateEditMessage` که گاهی تلگرام این را می‌فرستد\n\n"
    "اجرای مستقیم ماژول‌ها:\n"
    "دستورات به‌جای `send_message` مستقیماً اجرا می‌شوند:\n"
    "• `clear` | اجرای مستقیم `clearer.py`\n"
    "• `join` / `left` | اجرای مستقیم `join_left.py`\n"
    "• `info` | اجرای مستقیم `info_handler.py`\n"
    "• `whois` | اجرای مستقیم `whois_handler.py`\n\n"
    "مثال‌ها:\n"
    "• `reaction add 👌 clear txt` | پاک کردن متن‌ها با ری‌اکشن 👌\n"
    "• `reaction add 👍 join` | عضویت با ری‌اکشن 👍\n"
    "• `reaction add 🔍 info` | نمایش اطلاعات پیام با 🔍\n"
    "• `reaction add 👤 whois` | نمایش اطلاعات فرستنده با 👤\n"
    "• `reaction remove 👌` | حذف mapping 👌\n"
    "• `reaction clear` | حذف همه mapping ها\n"
    "• `.reaction_scope group` | فعال‌سازی ری‌اکشن در گروه‌ها/سوپرگروه‌ها\n\n"
    "نکات مهم:\n"
    "• فقط ری‌اکشن‌های خودتان (self-reaction) تشخیص داده می‌شوند\n"
    "• تنظیمات mapping در `reactions.json` و scope در `reaction_scope.json` ذخیره می‌شوند\n"
    "• Loop prevention از اجرای تکراری جلوگیری می‌کند — حتی پس از قطعی و اتصال مجدد شبکه\n"
    "• اگر اجرای مستقیم ممکن نباشد، به `send_message` fallback می‌شود\n"
)

ReactionCommands.help_text = help_text
ReactionCommands.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return ReactionCommands(context)
