"""
userbot_own/modules/packages/join_left/handlers.py
════════════════════════════════════════════════════════════════
The JoinLeft module class — command dispatch and the join/left flows.

This is the behavioural core of the module. It holds the state
(settings, invite cache, pending post-join actions, folder cache) and
orchestrates the helpers in the sibling files:

- ``entities.py``   — pull chat identifiers out of a message
- ``folders.py``    — create / fill / clean the joined* folders
- ``post_join.py``  — mute and archive a freshly joined chat
- ``leave.py``      — leave everything and reset the folders
- ``const.py``      — every tunable number
- ``types.py``      — the result records
- ``info.py``       — help texts and metadata (no behaviour)

The class is deliberately the only stateful piece: every helper in
the sibling modules is a free function, so each can be read (and
tested) without instantiating the module.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import datetime
import random
import time
from typing import TYPE_CHECKING

from telethon import TelegramClient, errors, events
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import (
    CheckChatInviteRequest,
    ImportChatInviteRequest,
)
from telethon.tl.types import (
    Channel,
    Chat,
    ChatInviteAlready,
    DialogFilter,
    InputPeerSelf,
    ReplyInlineMarkup,
)

from userbot_own.core.context import ModuleContext
from userbot_own.helpers.utils import (
    flood_seconds as _flood_seconds,
)
from userbot_own.helpers.utils import (
    get_inline_button_url,
    leave_dialog,
    read_json_file,
    safe_delete,
    write_json_file_atomic,
)
from userbot_own.modules._infra.base import Module
from userbot_own.modules.packages.join_left.const import (
    _ADAPTIVE_DECAY,
    _ADAPTIVE_FAST_DECAY,
    _ADAPTIVE_FAST_DECAY_THRESHOLD,
    _ADAPTIVE_MAX_MULT,
    _AUTO_DELETE_DELAY,
    _AUTO_LEAVE_INTERVAL,
    _BATCH_SIZE,
    _BATCHES_BEFORE_LONG,
    _COOLDOWN_LONG,
    _COOLDOWN_SHORT,
    _EDIT_THROTTLE,
    _FLOOD_PAD,
    _INVITE_CACHE_TTL,
    _JITTER_FACTOR,
    _LEFT_INTER_DELAY,
    _LEFT_MAX_FW_RETRIES,
    _LIST_CHUNK_SIZE,
    _MAX_FLOODWAIT_RETRIES,
    _PENDING_MAX_SWEEPS,
    _SMART_DELAYS,
    _SWEEP_ROUND_DELAY,
    _SWEEP_ROUNDS,
    _SWEEP_TIME_BUDGET,
)
from userbot_own.modules.packages.join_left.entities import (
    _extract_invite_hash,
    extract_telegram_entities,
)
from userbot_own.modules.packages.join_left.folders import (
    _add_single_peer_to_joined_folder,
    _create_joined_folder,
    _find_all_joined_folders,
    _folder_title,
    _get_folders,
    _peer_id,
    _remove_left_from_folders,
    _unwrap_join_result,
    ensure_exclude_archived,
)
from userbot_own.modules.packages.join_left.leave import (
    _leave_and_reset_joined_folder,
)
from userbot_own.modules.packages.join_left.post_join import (
    _archive_chat,
    _mute_chat,
)
from userbot_own.modules.packages.join_left.types import (
    EnsureResult,
    PostJoinResult,
    _PendingActions,
)

if TYPE_CHECKING:
    pass

# B-JL-01 (strengthened): errors that prove a link is invalid/expired.
# When ANY of these is encountered during a join batch, the ENTIRE batch must
# stop immediately — not just skip the current entity. This is the product
# decision: one invalid link means the user's intent for *this batch* cannot
# be safely inferred, so no further joins in the same command are attempted.
# If the user re-sends the command, the previously-failed link is re-attempted.
_INVALID_LINK_ERRORS: tuple[type, ...] = (
    errors.InviteHashInvalidError,
    errors.InviteHashExpiredError,
    errors.UsernameNotOccupiedError,
    errors.ChannelPrivateError,
)

# ── Module ────────────────────────────────────────────────────────────────────

class JoinLeft(Module):
    """Join/leave chats with folder management, mute/archive, and auto-leave."""

    name = "join_left"
    category = "social"
    desc = "عضویت و ترک چت‌ها"
    _auto_delete_default_delay = _AUTO_DELETE_DELAY

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self._settings_file = self.cfg.settings_dir / "join_left.json"
        self._settings_lock = asyncio.Lock()
        self._settings: dict = {
            "delay": 0.0,
            "join_mode": "safe",
            "auto_leave_days": None,
            "joined_chats": {}
        }
        self._auto_leave_task: asyncio.Task | None = None

        self._folder_cache: dict[int, tuple[float, list[DialogFilter]]] = {}

        # v3.2.0: joined chats whose mute / folder-add / archive has not fully
        # succeeded yet. Per-account, in memory (an instance attribute, not module
        # state, so hot-reload and multi-account isolation are unaffected).
        self._pending_actions: dict[int, _PendingActions] = {}

        # Layer 1: persistent invite hash → username cache
        self._invite_cache: dict[str, dict] = {}
        self._invite_cache_file = self.cfg.settings_dir / "join_left_invite_cache.json"
        self._invite_cache_lock = asyncio.Lock()

    def setup(self, client: TelegramClient) -> None:
        self._add_handler(client, events.NewMessage(outgoing=True), self._dispatch)
        self._load_settings_sync()
        self._load_invite_cache_sync()

        self._auto_leave_task = asyncio.create_task(
            self._auto_leave_loop(client),
            name=f"auto_leave_a{self.cfg.index}"
        )
        self._log_info("JoinLeft ready (mode=%s).", self._settings.get("join_mode", "safe"))

    def teardown(self, client: TelegramClient) -> None:
        if self._auto_leave_task and not self._auto_leave_task.done():
            self._auto_leave_task.cancel()
        self._auto_leave_task = None
        self._folder_cache.clear()
        self._pending_actions.clear()
        super().teardown(client)

    # ── Settings I/O ──────────────────────────────────────────────────────────

    def _load_settings_sync(self) -> None:
        data, err = read_json_file(self._settings_file)
        if err is not None:
            self._log_error("Settings load error (using defaults): %s", err)
            return
        if data is None:
            return

        try:
            self._settings["delay"] = float(data.get("delay", 0.0))
            self._settings["auto_leave_days"] = data.get("auto_leave_days")

            raw_mode = data.get("join_mode", "safe")
            self._settings["join_mode"] = raw_mode if raw_mode in ("fast", "safe", "human") else "safe"

            joined = data.get("joined_chats", {})
            if not isinstance(joined, dict):
                self._log_warning("joined_chats was %s (not a dict), resetting to empty", type(joined).__name__)
                joined = {}
            self._settings["joined_chats"] = joined

        except Exception as exc:
            self._log_error("Settings load error (using defaults): %s", exc)

    async def _save_settings(self) -> None:
        err = write_json_file_atomic(self._settings_file, self._settings, indent=4)
        if err is not None:
            self._log_error("Settings save error: %s", err)

    # ── Invite Cache I/O (Layer 1) ────────────────────────────────────────────

    def _load_invite_cache_sync(self) -> None:
        data, err = read_json_file(self._invite_cache_file)
        if err is not None:
            self._log_debug("[Account%d] Invite cache load error (ignored): %s", self.cfg.index, err)
            self._invite_cache = {}
            return
        if isinstance(data, dict):
            self._invite_cache = data
            self._log_debug("[Account%d] Loaded %d invite cache entries.", self.cfg.index, len(self._invite_cache))

    async def _save_invite_cache(self) -> None:
        err = write_json_file_atomic(
            self._invite_cache_file, self._invite_cache, indent=2, tmp_suffix=".cache.tmp"
        )
        if err is not None:
            self._log_debug("[Account%d] Invite cache save error: %s", self.cfg.index, err)

    async def _prune_expired_invite_cache(self) -> None:
        now_ts = time.time()
        expired = [
            h for h, v in self._invite_cache.items()
            if (now_ts - v.get("ts", 0)) > _INVITE_CACHE_TTL
        ]
        if expired:
            async with self._invite_cache_lock:
                for h in expired:
                    self._invite_cache.pop(h, None)
            self._log_debug("[Account%d] Pruned %d expired invite cache entries.", self.cfg.index, len(expired))

    # ── Folder sync helper ────────────────────────────────────────────────────

    async def _sync_folder_to_tracking(self, client: TelegramClient) -> int:
        """I1: Sync peers from ALL joined* folders into auto-leave tracking."""
        days = self._settings.get("auto_leave_days")
        if days is None:
            return 0

        if not client.is_connected():
            return 0

        try:
            folders    = await _get_folders(client, self._folder_cache)
            all_joined = _find_all_joined_folders(folders)
            if not all_joined:
                return 0

            # Collect ALL non-self peers from ALL joined* folders
            all_peers = []
            for jf in all_joined:
                for p in (jf.include_peers or []):
                    if not isinstance(p, InputPeerSelf):
                        all_peers.append(p)

            if not all_peers:
                return 0

            old_timestamp = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days + 1)
            old_iso       = old_timestamp.isoformat()

            added = 0
            async with self._settings_lock:
                for peer in all_peers:
                    pid = _peer_id(peer)
                    if pid is None:
                        continue
                    chat_id_str = str(pid)
                    if chat_id_str not in self._settings["joined_chats"]:
                        self._settings["joined_chats"][chat_id_str] = old_iso
                        added += 1

                if added > 0:
                    await self._save_settings()

            if added > 0:
                self._log_debug(
                    "[Account%d] Synced %d existing folder chats into tracking.",
                    self.cfg.index, added,
                )

            return added

        except Exception as exc:
            self._log_debug("[Account%d] Folder sync failed: %s", self.cfg.index, exc)
            return 0

    # ── Layer 1: Smart Invite Resolution ─────────────────────────────────────

    async def _resolve_invite_to_username(
        self,
        client: TelegramClient,
        invite_hash: str,
    ) -> str | None:
        """
        Layer 1: Try to resolve an invite hash to a public @username.

        Req 5 fix: CheckChatInviteRequest for an "already member" invite
        was previously NOT short-circuiting, causing the join path to then
        also call ImportChatInviteRequest — two API calls for the same
        entity, which caused disproportionate FloodWait. Now we catch
        UserAlreadyParticipantError here and cache it as private (None),
        preventing the double-request.

        I3: If _validate_invite_links pre-cached the result, this will hit
        the cache and skip the API call entirely.
        """
        cached = self._invite_cache.get(invite_hash)
        if cached is not None:
            cached_ts = cached.get("ts", 0)
            if (time.time() - cached_ts) < _INVITE_CACHE_TTL:
                result = cached.get("username")
                self._log_debug(
                    "[Account%d] Invite cache HIT for hash %.8s...: username=%s",
                    self.cfg.index, invite_hash, result,
                )
                return result

        self._log_debug(
            "[Account%d] Invite cache MISS for hash %.8s..., calling CheckChatInviteRequest",
            self.cfg.index, invite_hash,
        )

        try:
            result = await client(CheckChatInviteRequest(invite_hash))
        except errors.InviteHashInvalidError:
            async with self._invite_cache_lock:
                self._invite_cache[invite_hash] = {"username": None, "ts": time.time()}
            return None
        except errors.UserAlreadyParticipantError:
            # Req 5 fix: cache as None (private/already-member), avoid double-request
            async with self._invite_cache_lock:
                self._invite_cache[invite_hash] = {"username": None, "ts": time.time()}
            # Signal caller that this is an "already member" case by re-raising
            raise
        except Exception as exc:
            self._log_debug(
                "[Account%d] CheckChatInviteRequest failed for %.8s...: %s",
                self.cfg.index, invite_hash, exc,
            )
            return None

        username: str | None = None

        for attr in ("chat", "channel"):
            obj = getattr(result, attr, None)
            if obj is not None and isinstance(obj, (Channel, Chat)):
                username = getattr(obj, "username", None) or None
                break

        if username == "":
            username = None

        self._log_debug(
            "[Account%d] Invite check result for %.8s...: username=%s (type=%s)",
            self.cfg.index, invite_hash, username, type(result).__name__,
        )

        async with self._invite_cache_lock:
            self._invite_cache[invite_hash] = {"username": username, "ts": time.time()}
        asyncio.create_task(self._save_invite_cache())

        return username

    # ── Req 2: "Already a member" detection ───────────────────────────────────

    @staticmethod
    def _is_already_member_error(exc: Exception) -> bool:
        """
        Detect "already a member" from known error forms:
        1. errors.UserAlreadyParticipantError (standard Telethon)
        2. "The authenticated user is already a part..." — string from some
           Telegram error responses on certain API layers.

        NOTE (v3.0.4): The old check for "'ChatInviteJoinResultOk' object
        has no attribute..." has been REMOVED. That AttributeError was a
        symptom of accessing .chats directly on the wrapper object instead
        of unwrapping .updates first. The root cause is now fixed in the
        join path (via _unwrap_join_result helper), so the workaround
        string-match is no longer needed or correct.
        """
        if isinstance(exc, errors.UserAlreadyParticipantError):
            return True
        if "already a part" in str(exc).lower():
            return True
        return False

    # ── Per-join post-processing (mute, folder, archive) ──────────────────────

    async def _post_join_actions(
        self,
        client: TelegramClient,
        entity,
        account_index: int,
    ) -> PostJoinResult:
        """
        Req 3: Immediately after a successful join, in this exact order:
        1. Mute the chat        — keeps it archived (an unmuted archived chat is moved
                                  back to the main list by Telegram on its next message)
        2. Add to joined* folder (incremental, N1-safe copy-then-commit write)
        3. Archive the chat     — with exclude_archived on every other folder, this IS
                                  what hides the chat from them

        v3.2.0: the old step "add to exclude_peers of every OTHER folder" is gone —
        ensure_exclude_archived() does that job once per batch instead. The
        order (archive last) is kept as it was: it costs nothing. (Older comments
        claimed a folder update with exclude_archived=False "silently moves the
        chat back to folder_id=0"; that was never verified and is not relied on —
        see the v3.2.0 CHANGELOG correction.)

        Returns a PostJoinResult. Every failed step has already been logged at
        WARNING by its helper; _record_join() queues the chat for the retry sweep.
        """
        muted        = await _mute_chat(client, entity, account_index)
        folder_added = await _add_single_peer_to_joined_folder(
            client, entity, account_index, self._folder_cache
        )
        archived     = await _archive_chat(client, entity, account_index)
        return PostJoinResult(muted=muted, folder_added=folder_added, archived=archived)

    async def _record_join(self, client: TelegramClient, entity) -> PostJoinResult:
        """
        The ONE place a joined chat is recorded (v3.2.0, Pattern 1.3 — three call sites
        used to repeat "write joined_chats, then run the post-join actions" and
        ignore the result): persist the tracking timestamp, run the post-join actions
        and, if any step failed, queue the chat in self._pending_actions for the
        retry sweep. Returns the PostJoinResult so the caller can report it.
        """
        async with self._settings_lock:
            self._settings["joined_chats"][str(entity.id)] = \
                datetime.datetime.now(datetime.UTC).isoformat()
            await self._save_settings()

        result = await self._post_join_actions(client, entity, self.cfg.index)
        if result.complete:
            self._pending_actions.pop(entity.id, None)
        else:
            missing = result.missing()
            self._pending_actions[entity.id] = _PendingActions(entity=entity, missing=missing)
            self._log_warning(
                "Post-join actions incomplete for chat id=%s (missing: %s) — queued for retry.",
                entity.id, ", ".join(sorted(missing)),
            )
        return result

    async def _forget_joined_chat(self, chat_id: int) -> None:
        """Drop a chat we are no longer a member of from tracking AND from the retry queue."""
        async with self._settings_lock:
            self._settings["joined_chats"].pop(str(chat_id), None)
            await self._save_settings()
        self._pending_actions.pop(chat_id, None)

    async def _run_missing_steps(
        self, client: TelegramClient, chat_id: int, pending: _PendingActions
    ) -> bool:
        """Re-run the still-missing post-join steps of one chat. True once all are done."""
        entity = pending.entity
        if "mute" in pending.missing and await _mute_chat(client, entity, self.cfg.index):
            pending.missing.discard("mute")
        if "folder" in pending.missing and await _add_single_peer_to_joined_folder(
            client, entity, self.cfg.index, self._folder_cache
        ):
            pending.missing.discard("folder")
        if "archive" in pending.missing and await _archive_chat(client, entity, self.cfg.index):
            pending.missing.discard("archive")

        if pending.missing:
            return False
        self._pending_actions.pop(chat_id, None)
        self._log_info("Retry completed the post-join actions of chat id=%s.", chat_id)
        return True

    async def _retry_missing_actions(self, client: TelegramClient, *, reason: str) -> tuple[int, int]:
        """
        Bounded retry sweep over chats whose mute / folder-add / archive failed.
        Runs at the end of every join batch and every 6 hours. Bounded three ways:
        _SWEEP_ROUNDS passes, a _SWEEP_TIME_BUDGET wall-clock ceiling, and
        _PENDING_MAX_SWEEPS sweeps per chat, after which the chat is given up on
        with a WARNING (it will not be retried again). Returns (resolved, still_pending).
        """
        if not self._pending_actions:
            return 0, 0

        for pending in self._pending_actions.values():
            pending.attempts += 1

        deadline = time.monotonic() + _SWEEP_TIME_BUDGET
        resolved = 0
        for round_no in range(_SWEEP_ROUNDS):
            if not self._pending_actions or time.monotonic() >= deadline:
                break
            if round_no > 0:
                await asyncio.sleep(_SWEEP_ROUND_DELAY)
            for chat_id, pending in list(self._pending_actions.items()):
                if time.monotonic() >= deadline:
                    self._log_warning(
                        "Retry sweep (%s) reached its %ds time budget — %d chat(s) left for later.",
                        reason, int(_SWEEP_TIME_BUDGET), len(self._pending_actions),
                    )
                    break
                if await self._run_missing_steps(client, chat_id, pending):
                    resolved += 1

        for chat_id, pending in list(self._pending_actions.items()):
            if pending.attempts >= _PENDING_MAX_SWEEPS:
                self._pending_actions.pop(chat_id, None)
                self._log_warning(
                    "Giving up on chat id=%s after %d sweeps — still missing: %s. "
                    "Fix it by hand (mute / archive / add to a joined* folder) or re-run `join`.",
                    chat_id, pending.attempts, ", ".join(sorted(pending.missing)),
                )

        remaining = len(self._pending_actions)
        if resolved or remaining:
            self._log_info(
                "Retry sweep (%s): resolved=%d still_pending=%d", reason, resolved, remaining
            )
        return resolved, remaining

    async def _reconcile_folders(self, client: TelegramClient, *, reason: str) -> EnsureResult:
        """
        Run ensure_exclude_archived() and absorb its outcome. Called at the end of every
        join batch, once at startup and every 6 hours — the last two cover folders the
        user creates (or edits to add type flags) after a batch. Never raises except
        CancelledError; a FloodError that escapes ensure_exclude_archived() (wait budget
        spent) is logged here at WARNING and the unfinished folders wait for the next run.
        """
        res = EnsureResult()
        try:
            await ensure_exclude_archived(
                client, self.cfg.index, self._folder_cache, result=res, context=reason
            )
        except errors.FloodError as exc:
            self._log_warning(
                "Folder reconcile (%s) deferred by FloodWait %ds (%s): %s",
                reason, _flood_seconds(exc), type(exc).__name__, res.summary(),
            )
        except Exception as exc:
            res.fetch_error = res.fetch_error or f"{type(exc).__name__}: {exc}"
            self._log_error("Folder reconcile (%s) failed: %s: %s", reason, type(exc).__name__, exc)
        return res

    # ── I3: Pre-join invite link validation ───────────────────────────────────

    async def _validate_invite_links(
        self,
        client: TelegramClient,
        entities: list[tuple[str, str | int]],
    ) -> tuple[list, list]:
        """
        I3: Pre-join validation phase for invite_link entities only.

        Calls CheckChatInviteRequest for each invite_link before any joins start.
        Non-invite entities (username, channel_id, numeric_id) pass through
        unchanged to avoid extra API calls that could trigger FloodWait.

        Purpose: ONLY detect invalid/expired links so the entire operation can
        be cancelled early.  ChatInviteAlready and UserAlreadyParticipantError
        are NOT treated as "already joined" here — they go to `remaining` so
        the main loop's existing robust handlers deal with them.  This avoids
        false "already a member" reports caused by stale Telegram server state
        or a previous session's left-then-rejoin scenario.

        Returns:
        - invalid:   [(identifier, reason_str), ...] — expired/revoked links
        - remaining: [(entity_type, identifier), ...] — all other entities
        """
        invalid:   list[tuple] = []
        remaining: list[tuple] = []
        cache_updated = False

        for entity_type, identifier in entities:
            if entity_type != 'invite_link':
                remaining.append((entity_type, identifier))
                continue

            invite_hash = _extract_invite_hash(str(identifier))
            if not invite_hash:
                invalid.append((identifier, "لینک قابل parse نیست"))
                continue

            # Cache hit → use cached username for Layer 1 resolution, but ALWAYS
            # validate the invite link status via CheckChatInviteRequest.
            # The cache stores username for Layer 1 smart resolution, NOT validation status.
            # Links can expire at any time, so we must re-validate every time.
            cached = self._invite_cache.get(invite_hash)
            cached_username = None
            if cached is not None:
                cached_ts = cached.get("ts", 0)
                if (time.time() - cached_ts) < _INVITE_CACHE_TTL:
                    cached_username = cached.get("username")
                    # Don't skip validation! Links can expire between cache writes.

            # Call CheckChatInviteRequest to validate
            #
            # v3.1.8 fix (G1): InviteHashExpiredError is a distinct Telethon
            # exception from InviteHashInvalidError — both are direct
            # subclasses of BadRequestError, siblings to each other, not
            # related by inheritance. Telegram raises InviteHashExpiredError
            # specifically when a link has run past its time/usage expiry
            # (the most common real-world "bad link" case), while
            # InviteHashInvalidError covers malformed/revoked hashes. Only
            # catching the latter meant expired links fell through to the
            # generic `except Exception` handler below, which explicitly
            # does NOT cancel the batch — the expired link was silently
            # treated as valid and the whole point of pre-validation (never
            # joining ANY chat if ANY link is bad) never triggered for this
            # exception type. Now both are caught identically.
            try:
                result = await client(CheckChatInviteRequest(invite_hash))
            except (errors.InviteHashInvalidError, errors.InviteHashExpiredError):
                invalid.append((identifier, "لینک منقضی یا نامعتبر است"))
                async with self._invite_cache_lock:
                    self._invite_cache[invite_hash] = {"username": None, "ts": time.time()}
                cache_updated = True
                continue
            except errors.UserAlreadyParticipantError:
                # We are (or recently were) a member.  Let the main loop handle
                # it — don't assume membership is still valid (the user may have
                # left since the last session and Telegram's server might still
                # transiently return this error).
                remaining.append((entity_type, identifier))
                continue
            except Exception as exc:
                # Unknown error — don't cancel; let the join phase handle it
                self._log_debug(
                    "[Account%d] Validation: unexpected error for %s: %s",
                    self.cfg.index, str(identifier)[:40], exc,
                )
                remaining.append((entity_type, identifier))
                continue

            if isinstance(result, ChatInviteAlready):
                # Telegram says we're a member — but this can be stale after
                # leaving and re-checking within the same Telegram server cache
                # window.  Route to remaining so the main loop verifies and
                # handles it (UserAlreadyParticipantError → post-join actions).
                remaining.append((entity_type, identifier))
            else:
                # ChatInvite or ChatInvitePeek — valid, not yet joined.
                # Cache the username for Layer 1 smart resolution so that
                # _resolve_invite_to_username hits the cache instead of making
                # another CheckChatInviteRequest API call.
                username: str | None = None
                for attr in ("chat", "channel"):
                    obj = getattr(result, attr, None)
                    if obj is not None and isinstance(obj, (Channel, Chat)):
                        username = getattr(obj, "username", None) or None
                        break
                async with self._invite_cache_lock:
                    self._invite_cache[invite_hash] = {"username": username, "ts": time.time()}
                cache_updated = True
                remaining.append((entity_type, identifier))

        if cache_updated:
            asyncio.create_task(self._save_invite_cache())

        return invalid, remaining

    # ── I-JL-03: Pre-validate username and numeric_id entities ─────────────────

    async def _prevalidate_entities(
        self,
        client: TelegramClient,
        entities: list[tuple[str, str | int]],
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """
        I-JL-03: Pre-validate username and numeric_id entities.

        Ensures that invalid usernames or nonexistent numeric IDs are caught
        before the main join loop, so the "whole-batch-stop" rule (B-JL-01)
        applies consistently regardless of entity type — not just for invite links.

        Returns two lists of (identifier, reason) tuples:
        - invalid_usernames: usernames that resolve to errors
        - invalid_numeric_ids: numeric IDs that resolve to errors

        Only truly invalid (UsernameNotOccupiedError, ChannelPrivateError) are
        flagged here; transient errors are left to the main loop.
        """
        invalid_usernames: list[tuple[str, str]] = []
        invalid_numeric_ids: list[tuple[str, str]] = []

        for entity_type, identifier in entities:
            if entity_type == "username":
                try:
                    await client.get_input_entity(f"@{identifier}")
                except errors.UsernameNotOccupiedError:
                    invalid_usernames.append((str(identifier), "یوزرنیم وجود ندارد"))
                except errors.ChannelPrivateError:
                    invalid_usernames.append((str(identifier), "خصوصی/محدود"))
                except Exception:
                    # Transient or other error — let the main loop handle
                    pass
            elif entity_type == "numeric_id":
                try:
                    await client.get_entity(identifier)
                except errors.UsernameNotOccupiedError:
                    invalid_numeric_ids.append((str(identifier), "چت یافت نشد"))
                except errors.ChannelPrivateError:
                    invalid_numeric_ids.append((str(identifier), "خصوصی/محدود"))
                except Exception:
                    # Transient or other error — let the main loop handle
                    pass

        return invalid_usernames, invalid_numeric_ids

    # ── Dispatcher ────────────────────────────────────────────────────────────

    async def _dispatch(self, event) -> None:
        text  = (event.raw_text or "").strip()
        lower = text.lower()
        parts = lower.split()
        if not parts:
            return

        cmd = parts[0]

        if cmd == "join":
            if len(parts) >= 3 and parts[1] == "delay":
                await self._handle_join_delay(event, parts[2])
            elif len(parts) >= 3 and parts[1] == "mode":
                await self._handle_join_mode(event, parts[2])
            elif event.is_reply:
                await self._handle_join(event)
            else:
                await self._safe_edit_with_auto_delete(
                    event,
                    "⚠️ لطفاً به پیامی که لینک دارد reply کنید یا `join delay <seconds>` یا `join mode fast|safe|human` را بفرستید."
                )
        elif cmd == "left" and event.is_reply:
            await self._handle_left(event)
        elif cmd == "folder":
            await self._handle_folder(event)
        elif cmd == "list":
            await self._handle_list(event)
        elif cmd == "autoleave":
            await self._handle_autoleave(event, parts)

    # ── Helper: collect entities (I4 fix) ─────────────────────────────────────

    @staticmethod
    def _collect_entities(reply_msg, command_msg) -> list[tuple[str, str | int]]:
        """
        I4: Collect and deduplicate Telegram entities from reply + command messages
        and any inline keyboard buttons.

        Changed from set() (non-deterministic order) to dict.fromkeys()-style
        deduplication using an ordered dict. Links now appear in exactly the
        left-to-right order they were found in the message, preserving the
        user's intended join order.
        """
        seen: dict[tuple, None] = {}

        # Reply message has priority — its order is preserved first
        for ent in extract_telegram_entities(reply_msg.message):
            seen[ent] = None

        # Command message entities (e.g. links in the join command itself)
        for ent in extract_telegram_entities(command_msg.message):
            seen.setdefault(ent, None)  # don't overwrite if already present

        # Inline keyboard buttons on the reply message (both Telethon
        # generations: KeyboardButtonUrl on <= 1.44, KeyboardInlineButton +
        # InlineButtonTypeUrl on >= 1.45 — via get_inline_button_url()).
        if hasattr(reply_msg, "reply_markup") and isinstance(reply_msg.reply_markup, ReplyInlineMarkup):
            for row in reply_msg.reply_markup.rows:
                for button in row.buttons:
                    btn_url = get_inline_button_url(button)
                    if btn_url:
                        for ent in extract_telegram_entities(btn_url):
                            seen.setdefault(ent, None)

        return list(seen.keys())

    # ── Auto-Leave Logic ──────────────────────────────────────────────────────

    async def _handle_autoleave(self, event, parts: list[str]) -> None:
        client = event.client
        if not await self._is_saved_messages(event):
            return

        if len(parts) == 1:
            await self._safe_edit_with_auto_delete(
                event,
                "❌ فرمت: `autoleave <days>` یا `autoleave off` یا `autoleave status`"
            )
            return

        arg = parts[1]

        if arg == "status":
            days  = self._settings["auto_leave_days"]
            count = len(self._settings["joined_chats"])
            state = f"✅ فعال ({days} روز)" if days else "❌ غیرفعال"
            await self._safe_edit_with_auto_delete(
                event,
                f"📊 **وضعیت Auto-Leave:**\n"
                f"• وضعیت: {state}\n"
                f"• چت‌های ردیابی‌شده: `{count}` چت"
            )
            return

        if arg == "off":
            async with self._settings_lock:
                self._settings["auto_leave_days"] = None
                await self._save_settings()
            await self._safe_edit_with_auto_delete(event, "✅ Auto-Leave غیرفعال شد.")
            self._log_debug("[Account%d] Auto-leave disabled", self.cfg.index)
            return

        try:
            days = int(arg)
            if days <= 0:
                raise ValueError
        except ValueError:
            await self._safe_edit_with_auto_delete(event, "❌ تعداد روز باید یک عدد مثبت باشد.")
            return

        async with self._settings_lock:
            self._settings["auto_leave_days"] = days
            await self._save_settings()
        await self._safe_edit_with_auto_delete(event, f"✅ Auto-Leave روی `{days}` روز تنظیم شد.")
        self._log_debug("[Account%d] Auto-leave set to %d days", self.cfg.index, days)

        synced = await self._sync_folder_to_tracking(client)
        if synced > 0:
            self._log_debug(
                "[Account%d] Synced %d existing folder chats into auto-leave tracking.",
                self.cfg.index, synced,
            )

    async def _auto_leave_loop(self, client: TelegramClient) -> None:
        for _ in range(60):
            if client.is_connected():
                break
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                return

        try:
            if self._settings.get("auto_leave_days") is not None:
                await self._sync_folder_to_tracking(client)

            first_cycle = True
            while True:
                if client.is_connected():
                    # v3.2.0: every cycle (the first one is the startup run) retries chats
                    # whose post-join actions failed and re-checks that every non-joined
                    # folder hides archived chats — this is what protects a folder the
                    # user created after the last `join`. Independent of auto_leave_days.
                    await self._retry_missing_actions(
                        client, reason="startup" if first_cycle else "periodic"
                    )
                    await self._reconcile_folders(
                        client, reason="startup" if first_cycle else "periodic"
                    )
                    await self._check_auto_leave(client)
                first_cycle = False
                await asyncio.sleep(_AUTO_LEAVE_INTERVAL)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._log_error("Auto-leave loop crashed: %s", exc)

    async def _check_auto_leave(self, client: TelegramClient) -> None:
        """
        Leave tracked chats that have been joined for at least `auto_leave_days` days.

        AGE-BASED ONLY: only chats whose stored join time has aged out are examined.
        (Older docstrings claimed this also "verifies manual leaves" for every
        tracked chat; it never did. A chat the account left by hand is cleaned up
        lazily — when it ages out, the leave attempt finds we are no longer a
        participant and the branches below clean tracking AND folders.)

        Req 5: paced loop with FloodWait retry cap.
        v3.2.0 (Pattern 1.3): EVERY branch that establishes "we are no longer a member"
        — successful leave, not-a-participant, private/inaccessible, not found —
        now removes the chat from tracking AND from all joined* folders through the
        shared _remove_left_from_folders(). Previously only the success branch
        touched the folders, so the other three left stale entries that used up
        the 100-chat cap and showed up in `list`.
        """
        if not client.is_connected():
            return

        days = self._settings.get("auto_leave_days")
        if days is None:
            return

        if not isinstance(self._settings.get("joined_chats"), dict):
            self._log_warning("joined_chats is not a dict, resetting")
            self._settings["joined_chats"] = {}
            await self._save_settings()
            return

        now      = datetime.datetime.now(datetime.UTC)
        to_leave: list[tuple[int, str]] = []

        async with self._settings_lock:
            for chat_id_str, joined_at_str in list(self._settings["joined_chats"].items()):
                try:
                    joined_at = datetime.datetime.fromisoformat(joined_at_str)
                    if joined_at.tzinfo is None:
                        joined_at = joined_at.replace(tzinfo=datetime.UTC)
                    if (now - joined_at).days >= days:
                        to_leave.append((int(chat_id_str), joined_at_str))
                except Exception:
                    continue

        for idx, (chat_id, joined_at_str) in enumerate(to_leave):
            gone = None   # entity (or raw id) of a chat this account is no longer a member of
            try:
                entity = await client.get_entity(chat_id)
                name   = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(chat_id)

                await leave_dialog(client, entity)

                gone = entity
                self._log_debug("Auto-left '%s' (id=%d, joined %s).", name, chat_id, joined_at_str)
                await self._forget_joined_chat(chat_id)

            except errors.UserNotParticipantError:
                self._log_debug("Auto-leave: not participant in %d, removing from tracking", chat_id)
                await self._forget_joined_chat(chat_id)
                try:
                    gone = await client.get_entity(chat_id)
                except Exception:
                    gone = chat_id

            except errors.ChannelPrivateError:
                self._log_debug("Auto-leave: channel %d is private/inaccessible, removing from tracking", chat_id)
                await self._forget_joined_chat(chat_id)
                gone = chat_id

            except (ValueError, errors.UsernameNotOccupiedError) as exc:
                self._log_debug("Auto-leave: entity %d not found (%s), removing from tracking", chat_id, exc)
                await self._forget_joined_chat(chat_id)
                gone = chat_id

            except errors.FloodError as exc:
                self._log_warning(
                    "Auto-leave FloodWait %ds (%s) for %d (will retry next cycle).",
                    _flood_seconds(exc), type(exc).__name__, chat_id,
                )
                await asyncio.sleep(min(_flood_seconds(exc) + _FLOOD_PAD, 60))

            except Exception as exc:
                self._log_warning("Auto-leave failed for %d (will retry): %s: %s", chat_id, type(exc).__name__, exc)

            # I1 + v3.2.0: remove from ALL joined* folders + scoped legacy sweep
            if gone is not None:
                await _remove_left_from_folders(client, [gone], self.cfg.index, self._folder_cache)

            # Req 5: pace the auto-leave loop
            if idx < len(to_leave) - 1:
                await asyncio.sleep(_LEFT_INTER_DELAY)

    # ── JOIN DELAY ────────────────────────────────────────────────────────────

    async def _handle_join_delay(self, event, arg: str) -> None:
        try:
            delay = float(arg)
            if delay < 0:
                raise ValueError
        except ValueError:
            await self._safe_edit_with_auto_delete(
                event,
                "❌ تاخیر باید یک عدد مثبت باشد (مثلاً `join delay 5`)."
            )
            return

        async with self._settings_lock:
            self._settings["delay"] = delay
            await self._save_settings()

        mode_note = ""
        if delay == 0.0:
            mode_note = f"\n💡 Smart throttling فعال شد (mode: `{self._settings.get('join_mode', 'safe')}`)."
        else:
            mode_note = "\n💡 Smart throttling غیرفعال شد (delay ثابت اعمال می‌شود)."

        await self._safe_edit_with_auto_delete(
            event,
            f"✅ تاخیر بین جوین‌ها روی `{delay}` ثانیه تنظیم شد.{mode_note}"
        )
        self._log_debug("[Account%d] Join delay set to %.2f seconds", self.cfg.index, delay)

    # ── JOIN MODE ─────────────────────────────────────────────────────────────

    async def _handle_join_mode(self, event, mode_arg: str) -> None:
        if mode_arg not in ("fast", "safe", "human"):
            await self._safe_edit_with_auto_delete(
                event,
                "❌ مقادیر معتبر: `fast` | `safe` | `human`\n"
                "• `fast`  — بدون throttling (سریع‌ترین)\n"
                "• `safe`  — حل هوشمند لینک + تأخیر بر اساس ریسک [پیش‌فرض]\n"
                "• `human` — تمام لایه‌های ضد-FloodWait (کندترین ولی ایمن‌ترین)"
            )
            return

        async with self._settings_lock:
            self._settings["join_mode"] = mode_arg
            await self._save_settings()

        desc = {
            "fast":  "بدون smart throttling و batching — سریع‌ترین",
            "safe":  "حل هوشمند لینک + تأخیر بر اساس ریسک — پیش‌فرض",
            "human": "تمام ۴ لایه ضد-FloodWait با jitter — ایمن‌ترین",
        }[mode_arg]
        await self._safe_edit_with_auto_delete(
            event,
            f"✅ حالت جوین تنظیم شد: `{mode_arg}`\n📋 {desc}"
        )
        self._log_debug("[Account%d] Join mode set to: %s", self.cfg.index, mode_arg)

    # ── FOLDER & LIST ─────────────────────────────────────────────────────────

    async def _handle_folder(self, event) -> None:
        """
        I1: Create or reset ALL joined* folders.
        - If none exist: creates base 'joined' folder.
        - If any exist: leaves all chats from all joined* folders, deletes
          joined2/joined3/…, and recreates only the base 'joined' folder.
        """
        client = event.client
        if not await self._is_saved_messages(event):
            return

        await self._safe_edit(event, "🔄 در حال بررسی فولدرهای joined...")
        folders    = await _get_folders(client, self._folder_cache)
        all_joined = _find_all_joined_folders(folders)

        if not all_joined:
            await _create_joined_folder(client, self.cfg.index, self._folder_cache, number=1)
            await self._safe_edit_with_auto_delete(
                event,
                "✅ فولدر **'joined'** ساخته شد.\n📌 Saved Messages پین شد."
            )
            self._log_debug("[Account%d] Created 'joined' folder", self.cfg.index)
        else:
            folder_names = "، ".join(f"'{_folder_title(f)}'" for f in all_joined)
            await self._safe_edit(
                event,
                f"🔄 در حال ترک تمام چت‌های {folder_names} و ریست...",
            )
            left, failed = await _leave_and_reset_joined_folder(
                client, self.cfg.index, self._folder_cache
            )
            # Clear tracking for all chats we just left
            async with self._settings_lock:
                self._settings["joined_chats"] = {}
                await self._save_settings()
            self._pending_actions.clear()  # v3.2.0: every tracked chat was just left

            extra_folders_note = ""
            if len(all_joined) > 1:
                extra_folders_note = (
                    f"\n🗑 `{len(all_joined) - 1}` فولدر اضافی "
                    f"({', '.join(_folder_title(f) for f in all_joined[1:])}) حذف شد."
                )

            msg = f"✅ فولدرهای joined ریست شد.\n• ترک شده: {left} چت\n"
            if failed:
                msg += f"• ناموفق: {failed} چت\n"
            msg += extra_folders_note
            msg += "\n📌 Saved Messages پین شد."
            await self._safe_edit_with_auto_delete(event, msg)
            self._log_debug(
                "[Account%d] Reset all joined* folders (count=%d, left=%d, failed=%d)",
                self.cfg.index, len(all_joined), left, failed,
            )

    async def _handle_list(self, event) -> None:
        """
        I1 + I6: Show all chats from ALL joined* folders, paginated at
        ~3500 chars. Each peer shows which folder it belongs to when there
        are multiple joined* folders.
        """
        client = event.client
        if not await self._is_saved_messages(event):
            return

        await self._safe_edit(event, "🔍 در حال بارگذاری محتوای فولدرهای joined...")
        folders    = await _get_folders(client, self._folder_cache)
        all_joined = _find_all_joined_folders(folders)

        if not all_joined:
            await self._safe_edit_with_auto_delete(event, "ℹ️ هیچ فولدر **joined** وجود ندارد.")
            return

        # Collect all non-self peers from all joined* folders
        all_peers: list[tuple] = []  # (peer, folder_title_str)
        for jf in all_joined:
            ftitle = _folder_title(jf)
            for p in (jf.include_peers or []):
                if not isinstance(p, InputPeerSelf):
                    all_peers.append((p, ftitle))

        if not all_peers:
            folder_names = "، ".join(f"**'{_folder_title(f)}'**" for f in all_joined)
            await self._safe_edit_with_auto_delete(
                event,
                f"ℹ️ فولدرهای {folder_names} خالی هستند (فقط Saved Messages)."
            )
            return

        total         = len(all_peers)
        multi_folder  = len(all_joined) > 1
        folder_header = (
            f"📁 **فولدرهای joined ({len(all_joined)} فولدر) — {total} چت در مجموع:**\n"
            if multi_folder else
            f"📁 **فولدر '{_folder_title(all_joined[0])}' — {total} چت:**\n"
        )

        lines: list[str] = [folder_header]
        for i, (peer, ftitle) in enumerate(all_peers, 1):
            pid = _peer_id(peer)
            try:
                entity = await client.get_entity(peer)
                name   = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(pid)
                uname  = getattr(entity, "username", None)
                tag    = f"@{uname}" if uname else f"`{pid}`"
                if multi_folder:
                    lines.append(f"{i}. [{ftitle}] **{name}** — {tag}")
                else:
                    lines.append(f"{i}. **{name}** — {tag}")
            except Exception:
                if multi_folder:
                    lines.append(f"{i}. [{ftitle}] `{pid}`")
                else:
                    lines.append(f"{i}. `{pid}`")

        # I6: Paginate at ~3500 characters
        chunks: list[str]      = []
        current_lines: list[str] = []
        current_len = 0

        for line in lines:
            line_len = len(line) + 1  # +1 for newline separator
            if current_len + line_len > _LIST_CHUNK_SIZE and current_lines:
                chunks.append("\n".join(current_lines))
                current_lines = ["_(ادامه در پیام بعدی...)_\n", line]
                current_len   = sum(len(ln) + 1 for ln in current_lines)
            else:
                current_lines.append(line)
                current_len += line_len

        if current_lines:
            chunks.append("\n".join(current_lines))

        # Send first chunk via the standard edit path
        await self._safe_edit_with_auto_delete(event, chunks[0])

        # Send overflow chunks as new messages (I6)
        for extra_chunk in chunks[1:]:
            try:
                await event.respond(extra_chunk, parse_mode="Markdown")
            except Exception:
                pass

        self._log_debug(
            "[Account%d] Listed %d chats from %d joined* folder(s)",
            self.cfg.index, total, len(all_joined),
        )

    # ── JOIN (4-layer anti-FloodWait) ─────────────────────────────────────────

    async def _handle_join(self, event) -> None:
        """
        Main join handler with 4-layer anti-FloodWait strategy.

        v3.1.6 additions:
        - I3: Pre-validation phase for invite links (before any join)
        - C1: _unwrap_join_result() applied to all 5 join paths
        - C2: WebView detection on all paths
        - I4: ordered entity collection via _collect_entities() returning list

        v3.0.3 base:
        - Req 1: folder addition is incremental (per-chat, immediately)
        - Req 2: already-member errors on ALL paths treated as success
        - Req 3: mute+archive immediately after each join
        - Req 4: [removed in v3.2.0 — exclude_archived replaces per-chat exclusion]
        - Req 5: per-entity FloodWait retry cap (_MAX_FLOODWAIT_RETRIES)

        v3.2.0:
        - every join outcome is recorded through _record_join() (one shared path;
          its PostJoinResult is kept and reported — Pattern 1.3 / 1.6)
        - after the loop: retry sweep for incomplete chats, then ONE
          ensure_exclude_archived() run, both BEFORE the summary is built
        """
        client    = event.client
        reply_msg = await event.get_reply_message()
        if not reply_msg:
            return

        # I4: ordered, deduplicated entity collection
        all_entities = self._collect_entities(reply_msg, event.message)
        if not all_entities:
            await self._safe_edit_with_auto_delete(event, "ℹ️ هیچ لینک، یوزرنیم یا ID تلگرامی یافت نشد.")
            return

        join_mode  = self._settings.get("join_mode", "safe")
        user_delay = self._settings.get("delay", 0.0)

        use_smart    = join_mode in ("safe", "human")
        use_adaptive = join_mode in ("safe", "human")
        use_batch    = (join_mode == "human")

        mode_label = {
            "fast":  "⚡ fast",
            "safe":  "🛡 safe",
            "human": "🧘 human",
        }.get(join_mode, join_mode)

        total_entities = len(all_entities)

        try:
            processing_msg = await event.edit(
                f"🔍 `{total_entities}` مورد یافت شد. "
                f"(mode: `{join_mode}`, delay: `{user_delay}s`)\n⏳ در حال جوین..."
            )
        except Exception as exc:
            self._log_error("Failed to create join progress message: %s", exc)
            return

        results: list[str]    = []
        # v3.2.2 (B-18): the old `joined_entities` list was appended to in
        # three places but never read — the v3.2.0 refactor replaced its use
        # with `post_results` (which carries the real per-chat outcome) and
        # left the list behind. Removed; `post_results` is the only record.
        post_results: list[tuple[object, PostJoinResult]] = []   # v3.2.0: (entity, outcome)

        start_time    = time.monotonic()
        join_times: list[float] = []
        success_count = 0
        fail_count    = 0
        flood_count   = 0

        adaptive_mult: float = 1.0

        consecutive_successes = 0  # I-JL-04: track for fast adaptive decay

        batch_join_count  = 0
        completed_batches = 0

        last_edit_time = 0.0

        async def safe_edit(text: str) -> None:
            nonlocal last_edit_time
            now = time.time()
            if now - last_edit_time > _EDIT_THROTTLE:
                try:
                    await processing_msg.edit(text, parse_mode="Markdown")
                    last_edit_time = now
                except errors.FloodError as e:
                    self._log_warning("Edit FloodWait %ds (%s)", _flood_seconds(e), type(e).__name__)
                    await asyncio.sleep(_flood_seconds(e))
                except Exception as exc:
                    self._log_error("Throttled edit failed: %s", exc)

        async def floodwait_countdown(total_seconds: int, label: str) -> None:
            remaining = total_seconds
            chunk     = 5 if total_seconds < 60 else 30
            while remaining > 0:
                sleep_for = min(remaining, chunk)
                try:
                    await processing_msg.edit(
                        f"⏳ **FloodWait** — `{label}`\n"
                        f"⏱ باقی‌مانده: **{remaining}s**\n"
                        f"_تا کنون: {success_count} موفق / {fail_count} ناموفق_",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
                await asyncio.sleep(sleep_for)
                remaining -= sleep_for

        async def handle_floodwait(exc: errors.FloodError, label: str) -> float:
            nonlocal adaptive_mult

            seconds = _flood_seconds(exc)
            self._log_debug(
                "[Account%d] FloodWait %ds for '%s' (current mult=%.1f)",
                self.cfg.index, seconds, label, adaptive_mult,
            )

            if seconds < 30:
                buffer, multiplier, severity = 2, 1.5, "خفیف"
            elif seconds < 300:
                buffer, multiplier, severity = 10, 3.0, "متوسط"
            else:
                buffer, multiplier, severity = 60, 5.0, "سنگین ⚠️"

            total_wait = seconds + buffer
            new_mult   = min(adaptive_mult * multiplier, _ADAPTIVE_MAX_MULT)

            await floodwait_countdown(total_wait, f"{label} [{severity}: {seconds}s]")
            return new_mult

        def compute_delay(effective_type: str) -> float:
            if user_delay > 0:
                return user_delay
            if join_mode == "fast":
                return 0.0
            base  = _SMART_DELAYS.get(effective_type, _SMART_DELAYS["invite_direct"])
            delay = base * adaptive_mult
            if use_batch:
                jitter = random.uniform(-_JITTER_FACTOR, _JITTER_FACTOR)
                delay  = delay * (1.0 + jitter)
            return max(0.5, delay)

        if use_smart and len(self._invite_cache) > 200:
            asyncio.create_task(self._prune_expired_invite_cache())

        # ═══════════════════════════════════════════════════════════════════════
        # Phase 0: I3 — Pre-validation of invite links (invalid/expired only)
        # ═══════════════════════════════════════════════════════════════════════

        has_invite_links = any(t == 'invite_link' for t, _ in all_entities)

        if has_invite_links:
            try:
                await processing_msg.edit(
                    f"🔍 `{total_entities}` مورد یافت شد.\n"
                    f"⏳ در حال بررسی اعتبار لینک‌های دعوت...",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

            # Returns only (invalid, remaining) — ChatInviteAlready and
            # UserAlreadyParticipantError go to remaining so the main loop
            # handles them; only truly expired/revoked links cancel the run.
            invalid_links, all_entities = await self._validate_invite_links(
                client, all_entities
            )

            # I-JL-03: pre-validate username and numeric_id entities
            invalid_usernames, invalid_numeric_ids = await self._prevalidate_entities(client, all_entities)
            invalid_links.extend(invalid_usernames + invalid_numeric_ids)

            if invalid_links:
                # I3: Cancel the ENTIRE operation if any invite link is invalid
                invalid_lines = "\n".join(
                    f"• `{str(ident)[:60]}` — {reason}"
                    for ident, reason in invalid_links
                )
                try:
                    await processing_msg.edit(
                        f"⛔ **عملیات لغو شد — لینک‌های نامعتبر یافت شد:**\n\n"
                        f"{invalid_lines}\n\n"
                        f"ℹ️ لطفاً لینک‌های نامعتبر را حذف یا اصلاح کنید و دوباره تلاش کنید.",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
                self._track_delete_task(processing_msg, _AUTO_DELETE_DELAY)
                return  # ← This is the Phase 0 abort (correct)
            else:
                # T-1 fix: Always show a completion message from Phase 0
                try:
                    await processing_msg.edit(
                        f"✅ **بررسی اعتبار لینک‌ها کامل شد — {len(all_entities)} مورد آمادهٔ جوین.**",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

        # ═══════════════════════════════════════════════════════════════════════
        # MAIN LOOP
        # ═══════════════════════════════════════════════════════════════════════

        # B-JL-01 (strengthened): flag to cancel the ENTIRE batch when any
        # invalid/expired link is encountered. Product decision: one invalid
        # link → batch stops. Using break (not return) so POST-LOOP summary
        # and cleanup (message edit, delete tracking) still run.
        stop_batch = False

        for idx, (entity_type, identifier) in enumerate(all_entities, 1):
            joined_entity  = None
            attempt_start  = time.monotonic()
            effective_type = entity_type
            resolved_username: str | None = None
            fw_retry_count = 0   # Req 5: per-entity FloodWait retry cap

            # ── Layer 1: resolve invite link to username (safe/human mode) ────
            if use_smart and entity_type == "invite_link":
                invite_hash = _extract_invite_hash(str(identifier))
                if invite_hash:
                    try:
                        resolved_username = await self._resolve_invite_to_username(client, invite_hash)
                        if resolved_username:
                            effective_type = "invite_with_username"
                    except errors.UserAlreadyParticipantError:
                        # Already a member detected at Layer 1 (CheckChatInviteRequest).
                        # Use CheckChatInviteRequest again to retrieve the entity.
                        resolved_username = None
                        try:
                            result_check = await client(CheckChatInviteRequest(invite_hash))
                            if isinstance(result_check, ChatInviteAlready):
                                joined_entity = result_check.chat
                        except Exception:
                            pass

                        if joined_entity is not None:
                            title = getattr(joined_entity, "title", None) or str(identifier)
                            results.append(f"ℹ️ [{title}] — قبلاً عضو بود (جوین موفق)")
                            success_count += 1
                            join_times.append(time.monotonic() - attempt_start)
                            post_results.append((joined_entity, await self._record_join(client, joined_entity)))
                        else:
                            results.append(f"ℹ️ [{identifier}] — قبلاً عضو بود (جوین موفق)")
                            success_count += 1
                            join_times.append(time.monotonic() - attempt_start)

                        display_idx = idx
                        await safe_edit(
                            f"🔄 در حال جوین... ({display_idx}/{total_entities}) {mode_label}\n"
                            f"آخرین: {results[-1] if results else '-'}"
                        )
                        if idx < len(all_entities):
                            this_delay = compute_delay(effective_type)
                            if this_delay > 0:
                                await asyncio.sleep(this_delay)
                        continue

            # ── RETRY loop for FloodWait (with Req 5 cap) ─────────────────────
            while True:
                try:
                    # v3.1.10: set by the "already a member" handlers below so the shared
                    # success block can word the result accordingly. Reset on EVERY attempt
                    # so neither the next item nor a FloodWait retry can inherit it.
                    already_member = False

                    # ── Join by effective type ─────────────────────────────────

                    if entity_type == "channel_id":
                        chan_id = int(f"-100{identifier}")
                        try:
                            ip = await client.get_input_entity(chan_id)
                        except Exception:
                            ip = await client.get_input_entity(identifier)
                        try:
                            raw = await client(JoinChannelRequest(ip))
                            # C1: unwrap ChatInviteJoinResultOk if returned
                            joined_entity, is_webview = _unwrap_join_result(raw)
                            if is_webview:
                                self._log_debug(
                                    "[Account%d] JoinChannelRequest (channel_id=%s) returned "
                                    "ChatInviteJoinResultWebView — WebView/Bot required.",
                                    self.cfg.index, identifier,
                                )
                                results.append(f"⚠️ [{identifier}] — نیاز به WebView/Bot دارد، رد شد")
                                fail_count += 1
                                break
                            if joined_entity is None:
                                joined_entity = await client.get_entity(ip)
                        except errors.UserAlreadyParticipantError:
                            joined_entity = await client.get_entity(ip)
                            already_member = True
                        except Exception as exc:
                            if self._is_already_member_error(exc):
                                joined_entity = await client.get_entity(ip)
                                already_member = True
                            else:
                                raise

                    elif entity_type == "username":
                        try:
                            ip  = await client.get_input_entity(f"@{identifier}")
                            raw = await client(JoinChannelRequest(ip))
                            # C1: unwrap ChatInviteJoinResultOk if returned by JoinChannelRequest
                            # This is the primary bug fix for @periodi / @RealAkhbar failures.
                            joined_entity, is_webview = _unwrap_join_result(raw)
                            if is_webview:
                                self._log_debug(
                                    "[Account%d] JoinChannelRequest (@%s) returned "
                                    "ChatInviteJoinResultWebView — WebView/Bot required.",
                                    self.cfg.index, identifier,
                                )
                                results.append(f"⚠️ [@{identifier}] — نیاز به WebView/Bot دارد، رد شد")
                                fail_count += 1
                                break
                            if joined_entity is None:
                                joined_entity = await client.get_entity(f"@{identifier}")
                        except errors.UserAlreadyParticipantError:
                            joined_entity = await client.get_entity(f"@{identifier}")
                            already_member = True
                        except (errors.UsernameNotOccupiedError, errors.ChannelPrivateError):
                            raise
                        except Exception as exc:
                            if self._is_already_member_error(exc):
                                joined_entity = await client.get_entity(f"@{identifier}")
                                already_member = True
                            else:
                                raise

                    elif entity_type == "numeric_id":
                        joined_entity = await client.get_entity(identifier)
                        if isinstance(joined_entity, Channel):
                            try:
                                ip  = await client.get_input_entity(joined_entity)
                                raw = await client(JoinChannelRequest(ip))
                                # C1: unwrap ChatInviteJoinResultOk if returned
                                joined_entity_new, is_webview = _unwrap_join_result(raw)
                                if is_webview:
                                    self._log_debug(
                                        "[Account%d] JoinChannelRequest (numeric_id=%s) returned "
                                        "ChatInviteJoinResultWebView — WebView/Bot required.",
                                        self.cfg.index, identifier,
                                    )
                                    results.append(f"⚠️ [{identifier}] — نیاز به WebView/Bot دارد، رد شد")
                                    fail_count += 1
                                    joined_entity = None
                                    break
                                if joined_entity_new is not None:
                                    joined_entity = joined_entity_new
                            except errors.UserAlreadyParticipantError:
                                already_member = True  # keep pre-resolved joined_entity
                            except Exception as exc:
                                if not self._is_already_member_error(exc):
                                    raise
                                already_member = True  # keep pre-resolved joined_entity

                    elif entity_type == "invite_link":

                        if effective_type == "invite_with_username" and resolved_username:
                            try:
                                ip  = await client.get_input_entity(f"@{resolved_username}")
                                raw = await client(JoinChannelRequest(ip))
                                # C1: unwrap ChatInviteJoinResultOk if returned
                                joined_entity, is_webview = _unwrap_join_result(raw)
                                if is_webview:
                                    self._log_debug(
                                        "[Account%d] JoinChannelRequest (@%s via invite) returned "
                                        "ChatInviteJoinResultWebView — WebView/Bot required.",
                                        self.cfg.index, resolved_username,
                                    )
                                    results.append(
                                        f"⚠️ [@{resolved_username}] — نیاز به WebView/Bot دارد، رد شد"
                                    )
                                    fail_count += 1
                                    break
                                if joined_entity is None:
                                    joined_entity = await client.get_entity(f"@{resolved_username}")
                            except errors.UserAlreadyParticipantError:
                                joined_entity = await client.get_entity(f"@{resolved_username}")
                                already_member = True
                            except Exception as exc:
                                if self._is_already_member_error(exc):
                                    joined_entity = await client.get_entity(f"@{resolved_username}")
                                    already_member = True
                                else:
                                    # Fall back to hash path
                                    self._log_debug(
                                        "[Account%d] Username join failed for @%s, falling back to hash",
                                        self.cfg.index, resolved_username,
                                    )
                                    effective_type  = "invite_direct"
                                    ih_fallback     = _extract_invite_hash(str(identifier))
                                    if ih_fallback:
                                        try:
                                            raw2 = await client(ImportChatInviteRequest(ih_fallback))
                                            # C1: unwrap via helper for consistency
                                            joined_entity, is_webview2 = _unwrap_join_result(raw2)
                                            if is_webview2:
                                                results.append(
                                                    f"⚠️ [{identifier}] — نیاز به WebView/Bot دارد، رد شد"
                                                )
                                                fail_count += 1
                                                break
                                        except errors.UserAlreadyParticipantError:
                                            joined_entity = await client.get_entity(f"@{resolved_username}")
                                            already_member = True   # v3.2.0 (K5): same wording as every other path
                                        except Exception as exc2:
                                            if self._is_already_member_error(exc2):
                                                joined_entity = await client.get_entity(f"@{resolved_username}")
                                                already_member = True   # v3.2.0 (K5)
                                            else:
                                                raise exc2

                        else:
                            # ── DIRECT HASH PATH: ImportChatInviteRequest ──────
                            # v3.0.4: This path is now a single API call.
                            # No pre-check (CheckChatInviteRequest) is made because
                            # truly private ChatInvite objects have no .username field.
                            # I3: invite links are pre-validated above, so we know
                            # they are valid and we are NOT yet members.
                            invite_hash = _extract_invite_hash(str(identifier))
                            if not invite_hash:
                                results.append(f"❌ [{identifier}] — لینک قابل parse نیست")
                                fail_count += 1
                                break

                            try:
                                raw = await client(ImportChatInviteRequest(invite_hash))

                                # C1: use _unwrap_join_result for consistency with all paths
                                joined_entity, is_webview = _unwrap_join_result(raw)
                                if is_webview:
                                    self._log_debug(
                                        "[Account%d] ImportChatInviteRequest returned "
                                        "ChatInviteJoinResultWebView for hash %.8s — "
                                        "join requires WebView/Bot interaction, skipping.",
                                        self.cfg.index, invite_hash,
                                    )
                                    results.append(
                                        f"⚠️ [{identifier}] — نیاز به WebView/Bot دارد، رد شد"
                                    )
                                    fail_count += 1
                                    break

                                # Update invite cache with username if we got an entity
                                if joined_entity is not None and use_smart:
                                    uname = getattr(joined_entity, "username", None)
                                    async with self._invite_cache_lock:
                                        self._invite_cache[invite_hash] = {
                                            "username": uname or None,
                                            "ts": time.time(),
                                        }
                                    asyncio.create_task(self._save_invite_cache())

                            except errors.UserAlreadyParticipantError:
                                # Already a member — retrieve entity via CheckChatInviteRequest
                                # v3.0.4 Fix D: use isinstance(ChatInviteAlready) for type-safe
                                # access to .chat. ChatInvite (not-yet-member) has no .chat attr.
                                try:
                                    result_check = await client(CheckChatInviteRequest(invite_hash))
                                    if isinstance(result_check, ChatInviteAlready):
                                        joined_entity = result_check.chat
                                except Exception:
                                    pass

                                # Fallback: if CheckChatInviteRequest failed to provide an
                                # entity (e.g. raised an exception or returned ChatInvite),
                                # and Layer 1 had resolved the invite to a public username,
                                # use get_entity to obtain the entity so _post_join_actions
                                # (mute, folder, archive) can still run.
                                if joined_entity is None and resolved_username:
                                    try:
                                        joined_entity = await client.get_entity(f"@{resolved_username}")
                                    except Exception:
                                        pass

                                title = getattr(joined_entity, "title", None) if joined_entity else None
                                results.append(f"ℹ️ [{title or identifier}] — قبلاً عضو بود (جوین موفق)")
                                success_count += 1
                                join_times.append(time.monotonic() - attempt_start)
                                consecutive_successes += 1  # I-JL-04
                                if joined_entity:
                                            post_results.append((joined_entity, await self._record_join(client, joined_entity)))
                                break

                    # ── Record success ─────────────────────────────────────────
                    if joined_entity:
                        title = getattr(joined_entity, "title", None) or str(identifier)

                        path_icon = {
                            "username":             "📢",
                            "channel_id":           "🔗",
                            "numeric_id":           "🔢",
                            "invite_with_username": "🛡",
                            "invite_direct":        "🔑",
                        }.get(effective_type, "✅")

                        if already_member:
                            # v3.1.10: same wording as the invite-hash, Layer-1 and last-chance
                            # paths for the same situation (was a plain "✅", which read as a
                            # fresh join). WORDING ONLY — counting, persistence, adaptive decay
                            # and post-join actions below are deliberately unchanged.
                            results.append(f"ℹ️ [{title}] — قبلاً عضو بود (جوین موفق)")
                        else:
                            results.append(f"{path_icon} [{title}] ✅")
                        success_count += 1
                        join_times.append(time.monotonic() - attempt_start)

                        if use_adaptive and adaptive_mult > 1.0:
                            # I-JL-04: use faster decay after sustained success
                            if consecutive_successes >= _ADAPTIVE_FAST_DECAY_THRESHOLD:
                                adaptive_mult = max(1.0, adaptive_mult * _ADAPTIVE_FAST_DECAY)
                            else:
                                adaptive_mult = max(1.0, adaptive_mult * _ADAPTIVE_DECAY)
                            consecutive_successes += 1

                        # Req 1 + Req 3: immediate per-chat recording + post-processing
                        # (v3.2.0: through the shared _record_join(); outcome is kept)
                        post_results.append((joined_entity, await self._record_join(client, joined_entity)))

                    break  # ← success (or handled WebView), exit retry loop

                # ── FloodWait handling (Layer 3 + Req 5 retry cap) ────────────
                except errors.FloodError as exc:
                    consecutive_successes = 0  # I-JL-04: reset on FloodWait
                    fw_retry_count += 1
                    flood_count    += 1

                    if fw_retry_count > _MAX_FLOODWAIT_RETRIES:
                        self._log_warning(
                            "[Account%d] FloodWait retry cap (%d) exceeded for '%s' — skipping.",
                            self.cfg.index, _MAX_FLOODWAIT_RETRIES, identifier,
                        )
                        results.append(f"⏳ [{identifier}] — FloodWait زیاد، رد شد")
                        fail_count += 1
                        break

                    if use_adaptive:
                        adaptive_mult = await handle_floodwait(exc, str(identifier))
                    else:
                        await floodwait_countdown(_flood_seconds(exc) + 2, str(identifier))
                    continue  # retry

                # ── Other errors ──────────────────────────────────────────────
                except Exception as exc:
                    # I-JL-06: Unified debug logging for all join path errors
                    self._log_debug(
                        "[Account%d] Join path '%s' for '%s' raised: %s: %s",
                        self.cfg.index, effective_type, str(identifier)[:40], type(exc).__name__, exc,
                    )
                    # Req 2: last-chance catch for any "already member" string
                    if self._is_already_member_error(exc):
                        results.append(f"ℹ️ [{identifier}] — قبلاً عضو بود (جوین موفق)")
                        success_count += 1
                        join_times.append(time.monotonic() - attempt_start)
                        break

                    err = str(exc)
                    fail_count += 1
                    if "INVITE_REQUEST_SENT" in err:
                        status = "⏳ درخواست ارسال شد"
                    elif (
                        # v3.1.8 fix (G3+G5): defense-in-depth for the rare
                        # race where a link expires between Phase 0
                        # validation and its turn in this loop. Mirrors the
                        # G1 fix in _validate_invite_links — both the typed
                        # exception (isinstance check, G3) and the raw error
                        # string (G5, in case a future Telethon version or
                        # unusual API response surfaces it as text instead
                        # of the typed exception) are checked.
                        isinstance(exc, (errors.InviteHashInvalidError, errors.InviteHashExpiredError))
                        or "INVITE_HASH_INVALID" in err
                        or "INVITE_HASH_EXPIRED" in err
                    ):
                        status = "❌ لینک منقضی یا نامعتبر"
                    elif isinstance(exc, errors.UsernameNotOccupiedError):
                        status = "❌ یوزرنیم وجود ندارد"
                    elif isinstance(exc, errors.ChannelPrivateError):
                        status = "🔒 خصوصی/محدود"
                    elif isinstance(exc, errors.FloodError):  # B-JL-04: typed check, no string matching
                        status = f"⏳ FloodWait"
                    else:
                        status = f"❌ خطا: {err[:40]}"
                    results.append(f"[{identifier}] — {status}")
                    
                    # B-JL-01 (strengthened): stop the ENTIRE batch on any
                    # invalid/expired link. This is a hard product requirement:
                    # one invalid link → the entire batch is cancelled.
                    if isinstance(exc, _INVALID_LINK_ERRORS):
                        self._log_debug(  # H-1: downgraded from _log_warning to _log_debug to reduce terminal noise
                            "[Account%d] Invalid/expired link '%s' — cancelling entire join batch (B-JL-01).",
                            self.cfg.index, identifier,
                        )
                        stop_batch = True  # H-2: flag for for-loop break (preserves POST-LOOP)
                    break  # exit while-loop only

            # B-JL-01 (strengthened): break out of for-loop if batch was cancelled.
            # This preserves POST-LOOP summary + result message.
            if stop_batch:
                break

            # ── Live progress update ───────────────────────────────────────────
            display_idx = idx
            await safe_edit(
                f"🔄 در حال جوین... ({display_idx}/{total_entities}) {mode_label}\n"
                f"آخرین: {results[-1] if results else '-'}\n"
                f"_mult: {adaptive_mult:.1f}×_"
                if use_adaptive and adaptive_mult > 1.0
                else
                f"🔄 در حال جوین... ({display_idx}/{total_entities}) {mode_label}\n"
                f"آخرین: {results[-1] if results else '-'}"
            )

            # ── Apply inter-join delay (Layers 2 + 3) ─────────────────────────
            if idx < len(all_entities):
                this_delay = compute_delay(effective_type)
                if this_delay > 0:
                    await asyncio.sleep(this_delay)

            # ── Layer 4: Batch cooldown (human mode only) ──────────────────────
            if use_batch and idx < len(all_entities):
                batch_join_count += 1
                if batch_join_count >= _BATCH_SIZE:
                    batch_join_count  = 0
                    completed_batches += 1

                    if completed_batches % _BATCHES_BEFORE_LONG == 0:
                        cooldown = _COOLDOWN_LONG
                        rest_msg = f"☕ استراحت طولانی بعد از {completed_batches} دسته"
                    else:
                        cooldown = _COOLDOWN_SHORT
                        rest_msg = f"☕ استراحت کوتاه (دسته {completed_batches})"

                    jitter   = random.uniform(-_JITTER_FACTOR, _JITTER_FACTOR)
                    cooldown = cooldown * (1.0 + jitter)

                    self._log_debug(
                        "[Account%d] Batch cooldown: %.0fs (batches=%d)",
                        self.cfg.index, cooldown, completed_batches,
                    )
                    try:
                        await processing_msg.edit(
                            f"🧘 {rest_msg} — {cooldown:.0f}s\n"
                            f"_تا کنون: {success_count} موفق / {fail_count} ناموفق_",
                            parse_mode="Markdown",
                        )
                    except Exception:
                        pass
                    await asyncio.sleep(cooldown)

        # ═══════════════════════════════════════════════════════════════════════
        # POST-LOOP: Summary
        # ═══════════════════════════════════════════════════════════════════════

        total_time = time.monotonic() - start_time
        avg_time   = sum(join_times) / len(join_times) if join_times else 0
        min_time   = min(join_times) if join_times else 0
        max_time   = max(join_times) if join_times else 0

        smart_wins = sum(1 for r in results if "🛡" in r)
        smart_note = (
            f"\n🛡 `{smart_wins}` لینک از invite به username تبدیل شد (Layer 1)"
            if use_smart and smart_wins > 0
            else ""
        )

        # ═══════════════════════════════════════════════════════════════════════
        # v3.2.0 POST-LOOP: retry sweep → exclude_archived reconcile (BEFORE the summary,
        # so the summary states real outcomes; it used to report a raw joined-count
        # as "added to folder" without looking at a single result — Pattern 1.6)
        # ═══════════════════════════════════════════════════════════════════════
        folder_note = ""

        # v3.2.2 (B-08): the exclude_archived reconcile used to run ONLY
        # inside `if post_results:` — i.e. only when at least one chat had
        # been joined. A `join` batch in which every entity failed (all
        # links invalid, all flood-waited, all already-member-without-entity)
        # produced post_results == [] and therefore SKIPPED the reconcile
        # entirely, leaving the user's folders unprotected with no report.
        # The reconcile is cheap (it only writes folders that are missing
        # the flag) and is exactly what protects archived chats, so it now
        # runs on every batch, joined or not.
        try:
            await processing_msg.edit(
                f"🧹 در حال نهایی‌سازی (mute / فولدر / archive)...\n"
                f"_تا کنون: {success_count} موفق / {fail_count} ناموفق_",
                parse_mode="Markdown",
            )
        except Exception:
            pass

        if post_results:
            await self._retry_missing_actions(client, reason="join batch")
        ensure_res = await self._reconcile_folders(client, reason="join batch")

        if post_results:
            unresolved = [e for e, _ in post_results if getattr(e, "id", None) in self._pending_actions]
            fully_done = len(post_results) - len(unresolved)
            folder_note = f"\n📁 `{fully_done}`/`{len(post_results)}` چت کامل پردازش شد (mute + فولدر + archive)."
            if unresolved:
                step_fa = {"mute": "mute", "folder": "فولدر", "archive": "archive"}
                missing_all = sorted({step_fa[m] for e in unresolved for m in self._pending_actions[e.id].missing})
                folder_note += (
                    f"\n⚠️ `{len(unresolved)}` چت ناقص ماند ({'، '.join(missing_all)}) — "
                    f"بعداً خودکار دوباره تلاش می‌شود (جزئیات در لاگ)."
                )
        if ensure_res.changed:
            folder_note += f"\n🗂 فولدرها: `{ensure_res.updated}` به‌روزرسانی شد (exclude_archived)"
            if ensure_res.problems:
                folder_note += f" | ⚠️ `{ensure_res.problems}` ناموفق/معوق (جزئیات در لاگ)"
            folder_note += "."

        summary = (
            f"--- **نتایج جوین** ({join_mode}) ---\n"
            f"{chr(10).join(results)}\n"
            f"------------------\n"
            f"📊 **آمار تفصیلی:**\n"
            f"• ✅ موفق: `{success_count}` | ❌ ناموفق: `{fail_count}` | ⏳ FloodWait: `{flood_count}`\n"
            f"• ⏱ زمان کل: `{total_time:.1f}s` | میانگین: `{avg_time:.2f}s`\n"
            f"• 🚀 سریع‌ترین: `{min_time:.2f}s` | 🐢 کندترین: `{max_time:.2f}s`"
            f"{smart_note}"
            f"{folder_note}"
        )

        try:
            await processing_msg.edit(summary, parse_mode="Markdown")
        except Exception:
            try:
                await event.respond(summary, parse_mode="Markdown")
            except Exception:
                pass

        self._track_delete_task(processing_msg, _AUTO_DELETE_DELAY)

        self._log_debug(
            "[Account%d] Join completed: mode=%s, success=%d, fail=%d, flood=%d, time=%.1fs, "
            "final_mult=%.1f",
            self.cfg.index, join_mode, success_count, fail_count,
            flood_count, total_time, adaptive_mult,
        )

    # ── LEFT ──────────────────────────────────────────────────────────────────

    async def _handle_left(self, event) -> None:
        """
        I1: After leaving, remove from ALL joined* folders.
        I5: Fixed invite-link membership detection (isinstance check).
        N3: Deduplicate input entities to prevent double-leave.
        v3.2.0: no exclusion lists to clean any more (exclude_archived keeps archived
        chats hidden); the shared _remove_left_from_folders() also sweeps legacy
        exclude_peers entries of the chats left, and now runs for the
        "not a participant" case too (Pattern 1.3).
        Req 5: Paced loop with FloodWait retry cap.
        """
        client    = event.client
        reply_msg = await event.get_reply_message()
        if not reply_msg:
            return

        # I4: use the updated ordered-list _collect_entities
        raw_entities = self._collect_entities(reply_msg, event.message)
        if not raw_entities:
            await self._safe_edit_with_auto_delete(event, "ℹ️ هیچ لینک، یوزرنیم یا ID تلگرامی یافت نشد.")
            return

        # N3: Deduplicate to prevent double-leave when same link appears twice
        all_entities = list(dict.fromkeys(raw_entities))

        try:
            processing_msg = await event.edit(f"🔍 `{len(all_entities)}` مورد یافت شد. در حال ترک...")
        except Exception as exc:
            self._log_error("Failed to create leave progress message: %s", exc)
            return

        results: list[str] = []
        left_entities: list = []
        any_successful_left = False

        for idx, (entity_type, identifier) in enumerate(all_entities):
            fw_retries = 0

            while True:
                try:
                    target_entity = None

                    if entity_type == 'channel_id':
                        chan_id = int(f"-100{identifier}")
                        try:
                            target_entity = await client.get_entity(chan_id)
                        except Exception:
                            target_entity = await client.get_entity(identifier)
                    elif entity_type == 'username':
                        target_entity = await client.get_entity(f"@{identifier}")
                    elif entity_type == 'numeric_id':
                        target_entity = await client.get_entity(identifier)
                    elif entity_type == 'invite_link':
                        invite_hash = _extract_invite_hash(str(identifier))
                        if not invite_hash:
                            results.append(f"❌ [{identifier}] — لینک قابل parse نیست")
                            break
                        # I5: Use isinstance(ChatInviteAlready) instead of fragile attr loop.
                        # ChatInviteAlready (already a member) has .chat.
                        # ChatInvite (not a member) does NOT have .chat — we can't leave
                        # a chat we haven't joined.
                        try:
                            result_check = await client(CheckChatInviteRequest(invite_hash))
                            if isinstance(result_check, ChatInviteAlready):
                                target_entity = result_check.chat
                            # else: ChatInvite → not a member → target_entity stays None
                        except errors.UserAlreadyParticipantError:
                            # Some Telegram API layers raise this instead of returning
                            # ChatInviteAlready. We know we're a member but can't
                            # retrieve the entity from the hash alone in this path.
                            results.append(
                                f"⚠️ [{str(identifier)[:40]}] — "
                                f"عضو هستید اما شناسه از طریق لینک قابل دریافت نیست"
                            )
                            break
                        except Exception as exc:
                            results.append(f"❌ [{identifier}] — خطا ({str(exc)[:40]})")
                            break

                    if target_entity is None:
                        # v3.1.10: the ONLY way to reach this check is an invite link that
                        # resolved (CheckChatInviteRequest answered) but where this account is
                        # NOT a member — there is nothing to leave. It used to report "not
                        # found" (❌), which is wrong: the link was found. Same wording as the
                        # UserNotParticipantError handler below, which is the same situation.
                        results.append(f"ℹ️ [{identifier}] — از قبل عضو نبود")
                        break

                    name = (
                        getattr(target_entity, "title", None)
                        or getattr(target_entity, "first_name", None)
                        or str(identifier)
                    )

                    await leave_dialog(client, target_entity)

                    results.append(f"✅ [{name}] — ترک شد")
                    any_successful_left = True
                    left_entities.append(target_entity)
                    await self._forget_joined_chat(target_entity.id)

                    break

                except errors.UserNotParticipantError:
                    results.append(f"ℹ️ [{identifier}] — از قبل عضو نبود")
                    # v3.2.0 (Pattern 1.3): we are not a member, so a stale joined* entry /
                    # tracking record / legacy exclusion for this chat must go too.
                    if target_entity is not None:
                        left_entities.append(target_entity)
                        await self._forget_joined_chat(target_entity.id)
                    break

                except errors.FloodError as exc:
                    fw_retries += 1
                    if fw_retries > _LEFT_MAX_FW_RETRIES:
                        self._log_warning(
                            "[Account%d] Left: FloodWait retry cap on '%s' — skipping.",
                            self.cfg.index, identifier,
                        )
                        results.append(f"⏳ [{identifier}] — FloodWait زیاد، رد شد")
                        break
                    self._log_debug("Left FloodWait %ds (%s)", _flood_seconds(exc), type(exc).__name__)
                    try:
                        await processing_msg.edit(f"⏳ Flood wait {_flood_seconds(exc)}s برای `{identifier}`...")
                    except Exception:
                        pass
                    await asyncio.sleep(_flood_seconds(exc) + _FLOOD_PAD)
                    continue

                except Exception as exc:
                    results.append(f"❌ [{identifier}] — {str(exc)[:40]}")
                    break

            # Req 5: inter-leave pacing
            if idx < len(all_entities) - 1:
                await asyncio.sleep(_LEFT_INTER_DELAY)

        # I1 + v3.2.0: remove from ALL joined* folders (+ scoped legacy sweep) through the
        # shared cleanup. It never raises and reports failures instead of hiding them.
        folder_note = ""
        if left_entities:
            removed, _swept, failed = await _remove_left_from_folders(
                client, left_entities, self.cfg.index, self._folder_cache
            )
            if removed:
                folder_note += f"\n📁 `{removed}` چت از فولدرهای joined حذف شد."
            if failed:
                folder_note += f"\n⚠️ پاکسازی `{failed}` فولدر ناموفق بود (جزئیات در لاگ)."

        final_text = "--- نتایج ترک ---\n" + "\n".join(results) + "\n------------------" + folder_note
        try:
            await processing_msg.edit(final_text, parse_mode="Markdown")
        except Exception:
            pass

        self._track_delete_task(processing_msg, _AUTO_DELETE_DELAY)

        if any_successful_left:
            # v3.2.2 (B-19): a reaction-triggered `left` arrives through
            # MockEvent, whose `message.id` is the deliberate placeholder 0
            # (see modules/bridge.py — a real id would make the reacted-to
            # message immune to its own reaction-triggered clear). Deleting
            # message id 0 is rejected by Telegram, so every reaction-
            # triggered `left` wrote a bogus ERROR line to the account log
            # ("safe_delete: permission denied — The specified message ID is
            # invalid"). There is no command message to delete in that case
            # anyway, so the call is simply skipped.
            command_msg_id = getattr(event.message, "id", 0) or 0
            if command_msg_id > 0:
                await safe_delete(client, event.chat_id, command_msg_id)
            if event.is_reply and reply_msg and reply_msg.out:
                try:
                    await client.edit_message(reply_msg, ".")
                except Exception:
                    pass

        self._log_debug(
            "[Account%d] Left completed: %d entities processed",
            self.cfg.index, len(all_entities)
        )


