"""
userbot_own/modules/reaction_commands.py
════════════════════════════════════════════════════════════════
Reaction-Based Commands — Funnel Architecture (Zero Polling)

Architecture:
- Push-Based Detection: Only UpdateMessageReactions + UpdateEditMessage
- Zero Polling: No get_dialogs calls; Method 1 no longer calls
  get_messages() either as of v3.0.2 (see below)
- Funnel Filtering: 5 gates with O(1) complexity
- Post-Startup Only: Ignores reactions from before module start
- Self-Only: Only processes reactions from the userbot account
- Environment Toggles: Configurable per chat type (bots/users/groups/channels)

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
- Environment-aware filtering (bots/users/groups/channels)

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
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio

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


# ── Module ────────────────────────────────────────────────────────────────────

class ReactionCommands(Module):
    """Execute commands via emoji reactions (Funnel Architecture, zero polling)."""

    name = "reaction_commands"

    # ── Environment toggles (edit these to change scope) ──────────────────
    ENABLE_FOR_BOTS: bool = True       # Private chats with bots
    ENABLE_FOR_USERS: bool = False      # Private chats with regular users
    ENABLE_FOR_GROUPS: bool = False    # Basic groups and supergroups
    ENABLE_FOR_CHANNELS: bool = False  # Channels (broadcast)

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self._settings_file = self.cfg.settings_dir / "reactions.json"
        self._reactions: dict[str, str] = {}
        self._me_id: int | None = None

        # (chat_id, msg_id) -> currently-active self-reaction emojis, as of
        # the most recently processed update. Replaces the old permanent
        # "every emoji ever fired" set — see module docstring's "Reaction
        # state tracking" section for the full rationale.
        self._active_reactions: dict[tuple[int, int], frozenset[str]] = {}

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

    def setup(self, client: TelegramClient) -> None:
        self._client = client
        self._ensure_settings_file()
        self._load_settings()

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

        self._log_info(
            "ReactionCommands ready (Funnel Architecture). %d reactions configured.",
            len(self._reactions),
        )

    def teardown(self, client: TelegramClient) -> None:
        if self._me_id_task is not None and not self._me_id_task.done():
            self._me_id_task.cancel()
        if self._ready_task is not None and not self._ready_task.done():
            self._ready_task.cancel()
        self._me_id_task = None
        self._ready_task = None

        self._active_reactions.clear()
        self._peer_is_bot_cache.clear()
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

        Uses O(1) peer_id type check. For private chats (PeerUser), bot-vs-
        user classification is cached locally per user_id (v3.0.2) — the
        first reaction event from/about a given user pays for one
        client.get_entity() call (itself often already cache-hit inside
        Telethon), every subsequent one for that same user is a plain dict
        lookup with no possibility of a network call at all.
        """
        if isinstance(peer_id, PeerChannel):
            # Channels and Supergroups
            return self.ENABLE_FOR_CHANNELS

        elif isinstance(peer_id, PeerChat):
            # Basic Groups (legacy)
            return self.ENABLE_FOR_GROUPS

        elif isinstance(peer_id, PeerUser):
            # Private chat — need to determine if bot or user
            user_id = peer_id.user_id
            is_bot = self._peer_is_bot_cache.get(user_id)
            if is_bot is None:
                try:
                    entity = await client.get_entity(user_id)
                    is_bot = bool(getattr(entity, 'bot', False))
                except Exception:
                    # If we can't determine, assume user (safer default).
                    # Deliberately NOT cached: a transient failure here
                    # shouldn't permanently freeze this user's classification
                    # as "not a bot" — retry on the next reaction instead.
                    return self.ENABLE_FOR_USERS
                self._peer_is_bot_cache[user_id] = is_bot

            return self.ENABLE_FOR_BOTS if is_bot else self.ENABLE_FOR_USERS

        # Unknown peer type — drop
        return False

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

    # ── Command handler ───────────────────────────────────────────────────

    async def _on_command(self, event) -> None:
        text = (event.raw_text or "").strip()
        parts = text.split(maxsplit=1)

        if not parts:
            return

        cmd = parts[0].lower()

        if cmd in ("reactions", "reaction"):
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

    # ── Method 1: UpdateMessageReactions (Primary Push Route) ─────────────

    async def _on_reaction_update(self, event) -> None:
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

        # ── clear (compatible with clearer.py) ────────────────────────────
        if command_name == "clear":
            clearer_instance = loader.get_module("clearer")
            if clearer_instance is not None:
                try:
                    mock_event = MockEvent(client, chat_id, target_msg_id, command_text)
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
                        target_msg=target_msg
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
                        target_msg=target_msg
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
                        target_msg=target_msg
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
    "• `reaction clear` | حذف همه mapping ها\n\n"
    "نکات مهم:\n"
    "• فقط ری‌اکشن‌های خودتان (self-reaction) تشخیص داده می‌شوند\n"
    "• تنظیمات در `reactions.json` ذخیره می‌شوند\n"
    "• Loop prevention از اجرای تکراری جلوگیری می‌کند\n"
    "• اگر اجرای مستقیم ممکن نباشد، به `send_message` fallback می‌شود\n"
)

ReactionCommands.help_text = help_text
ReactionCommands.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return ReactionCommands(context)
