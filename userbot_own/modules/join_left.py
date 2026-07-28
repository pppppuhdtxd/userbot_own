"""
userbot_own/modules/join_left.py
════════════════════════════════════════════════════════════════
Join / Left / Folder / List / AutoLeave / Join Delay

Commands:
- `join` (reply to a message with links/usernames/IDs)
  → Join all found chats with live progress updates
  → 4-layer anti-FloodWait: smart resolution, risk-based delays,
    adaptive backoff, human-like batching
  → Add each joined chat to 'joined' folder IMMEDIATELY (incremental)
  → Mute & archive each chat IMMEDIATELY after joining (crash-safe)

- `join delay <seconds>`
  → Set fixed delay between joins (0 = restore smart throttling)

- `join mode fast|safe|human`
  → fast  : no smart throttling, no batching (respects delay setting)
  → safe  : Layer 1 + Layer 2 — smart resolution + risk delays [DEFAULT]
  → human : all 4 layers — adds adaptive backoff + batch cooldowns

- `left` (reply to a message with links/usernames/IDs)
  → Leave all found chats with safe pacing (FloodWait-aware)
  → Remove left chats from the 'joined' folder automatically
  → Remove left chats from excluded_chats of ALL other folders immediately
  → Delete command message on success

- `folder` (Saved Messages only)
  → Create / reset the 'joined' folder

- `list` (Saved Messages only)
  → Show all chats currently in the 'joined' folder

- `autoleave <days>`
  → Automatically leave joined chats after N days
  → Syncs existing folder chats on activation

- `autoleave off`
  → Disable auto-leave

- `autoleave status`
  → Show current auto-leave status and tracked chats

Anti-FloodWait Layers:
  Layer 1 — Smart Link Resolution
    Before joining via invite hash, calls CheckChatInviteRequest to peek
    at the destination. If the chat has a public username, switches to
    JoinChannelRequest (much more lenient rate limit). Cache persisted to
    join_left_invite_cache.json across restarts.

  Layer 2 — Risk-Based Delays (safe + human mode)
    Different join operations carry different FloodWait risk. Delays are
    applied proportionally to risk when no manual `join delay` is set.
      username / channel_id / numeric_id : 2–3s
      invite resolved to username         : 4s
      invite direct (private group hash)  : 4s

  Layer 3 — Adaptive FloodWait Response (safe + human mode)
    FloodWait duration signals how aggressively Telegram is rate-limiting.
    Short (<30s) → wait + 1.5× delay multiplier
    Medium (<300s) → wait + extra cooldown + 3.0× multiplier
    Heavy (≥300s)  → long wait + 5.0× multiplier + all-joins pause
    Multiplier decays 10% per successful join back toward 1.0.

  Layer 4 — Batch & Cooldown / Human Pattern (human mode only)
    After BATCH_SIZE joins: short cooldown (30s ± jitter).
    After BATCHES_BEFORE_LONG batches: long cooldown (120s ± jitter).
    ±20% random jitter on all delays to avoid predictable timing.

v3.0.4 Changes (Task 2 — Telethon wrapper fix + FloodWait reduction):
  Fix A — Correct ImportChatInviteRequest result unwrapping:
    In Telethon 1.44.0, ImportChatInviteRequest returns a
    messages.ChatInviteJoinResultOk wrapper (CONSTRUCTOR_ID 0x445663a7)
    with a single .updates attribute of TypeUpdates. The .chats list lives
    on .updates.chats, NOT directly on the result. Old code accessed
    result.chats, causing AttributeError on every private join success.
    Fixed to unwrap: raw.updates.chats[0].

  Fix B — Type-safe "already a member" detection:
    Removed the fragile "'ChatInviteJoinResultOk' object has no attribute"
    string check from _is_already_member_error(). That string was a
    workaround for the AttributeError from Fix A. Root cause now fixed;
    workaround removed. isinstance(errors.UserAlreadyParticipantError) is
    the primary check; "already a part" string fallback is retained.

  Fix C — Eliminated redundant CheckChatInviteRequest pre-calls:
    ChatInvite objects (truly private groups/channels) have no .username
    field. The Layer-1 pre-check always returned None for these links and
    was pure overhead (wasted API call). Now the direct hash path goes
    straight to ImportChatInviteRequest (1 call vs. up to 3 before).

  Fix D — Type-safe ChatInviteAlready entity extraction:
    Replaced getattr(result_check, 'chat'/'channel', None) with an
    explicit isinstance(result_check, ChatInviteAlready) guard. Only
    ChatInviteAlready has .chat; ChatInvite does not.

  Fix E — ChatInviteJoinResultWebView handling:
    ImportChatInviteRequest can return ChatInviteJoinResultWebView for
    subscription/bot-gated channels. Now detected and reported as a clear
    skip with an explanatory message instead of silently failing.

  Fix F — Delay table rebalanced:
    _SMART_DELAYS["invite_direct"] reduced 8.0s → 4.0s (API call count
    on the direct-hash path dropped from up to 3 to exactly 1).

  Fix G — _post_join_actions exclusion now awaited:
    Changed fire-and-forget asyncio.create_task() for folder exclusion to
    a direct await, making all 4 post-join actions strictly sequential and
    crash-safe per chat.

v3.0.3 Changes (Task 2 — full overhaul):
  Req 1: Incremental folder addition — each chat added to 'joined' folder
    IMMEDIATELY after a successful join, with FloodWait handling.
    Fixed folder id==1 false-skip (was conflating Telegram filter IDs
    with our new-ID counter, causing valid filters to be skipped).

  Req 2: "Already a member" handling — both known error strings are
    treated as 100% successful joins on ALL join paths. Mute, archive,
    and folder-add proceed identically for already-member cases.
    Root cause of extra FloodWait fixed: duplicate requests from both
    CheckChatInviteRequest and ImportChatInviteRequest for "already member"
    cases — now we catch UserAlreadyParticipantError in CheckChatInviteRequest
    and short-circuit immediately, avoiding a second API call.

  Req 3: Mute & archive immediately after each join (per-chat, not batched).
    If the process dies mid-run, all completed joins are muted+archived.

  Req 4: Strict folder exclusion — joined chats added to excluded_chats
    of ALL other editable folders. Left chats cleaned from exclusion lists
    immediately and synchronously on `left` / `folder` reset / auto-leave.
    Periodic verification in _check_auto_leave() covers manual leaves.

  Req 5: Deep bug-fix sweep — FloodWait retry cap (max 5 retries per entity),
    paced left/folder-reset commands using same safe logic as join,
    fixed aggressive FloodWait from duplicate API calls on already-member.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import random
import re
import time

from telethon import TelegramClient, errors, events
from telethon.tl.functions.account import UpdateNotifySettingsRequest
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.folders import EditPeerFoldersRequest
from telethon.tl.functions.messages import (
    CheckChatInviteRequest,
    DeleteHistoryRequest,
    GetDialogFiltersRequest,
    ImportChatInviteRequest,
    UpdateDialogFilterRequest,
)
from telethon.tl.types import (
    Channel,
    Chat,
    ChatInviteAlready,
    DialogFilter,
    InputFolderPeer,
    InputNotifyPeer,
    InputPeerNotifySettings,
    InputPeerSelf,
    KeyboardButtonUrl,
    ReplyInlineMarkup,
    TextWithEntities,
    User,
)
from telethon.tl.types import messages as tl_messages
from telethon import utils as tl_utils

from userbot_own.core.context import ModuleContext
from userbot_own.helpers.utils import read_json_file, safe_delete, write_json_file_atomic
from userbot_own.modules.base import Module

# Module-level logger — used only by free functions outside the Module class
log = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

_JOINED_FOLDER_NAME = "joined"
_FOLDER_CACHE_TTL   = 30.0   # seconds
_EDIT_THROTTLE      = 2.5    # seconds between message edits to avoid FloodWait
_AUTO_DELETE_DELAY  = 5.0    # seconds before auto-deleting command output

# ── Anti-FloodWait constants ──────────────────────────────────────────────────

# Layer 2: risk-based delay per join type (seconds)
_SMART_DELAYS: dict[str, float] = {
    "username":             2.0,
    "channel_id":           3.0,
    "numeric_id":           3.0,
    "invite_with_username": 4.0,
    "invite_direct":        4.0,  # v3.0.4: reduced from 8.0s; call count dropped to 1
}

# Layer 3: adaptive FloodWait multiplier bounds
_ADAPTIVE_MAX_MULT  = 20.0   # never exceed 20× the base smart delay
_ADAPTIVE_DECAY     = 0.90   # per-successful-join decay factor toward 1.0

# Layer 4: batch / human-pattern parameters
_BATCH_SIZE          = 5
_COOLDOWN_SHORT      = 30.0
_COOLDOWN_LONG       = 120.0
_BATCHES_BEFORE_LONG = 3
_JITTER_FACTOR       = 0.20  # ±20% random jitter

# Invite cache TTL: 6 hours
_INVITE_CACHE_TTL   = 6 * 3600

# Req 5: per-entity FloodWait retry cap — prevents infinite loops on
# pathological cases. After this many FloodWaits on the same entity,
# skip it and continue.
_MAX_FLOODWAIT_RETRIES = 5

# Pacing for left/folder-reset loops (mirrors Layer 2 safe defaults)
_LEFT_INTER_DELAY    = 2.0   # seconds between successive leaves (safe mode)
_LEFT_MAX_FW_RETRIES = 3     # retries before skipping on leave FloodWait


# ── Entity extraction ─────────────────────────────────────────────────────────

def extract_telegram_entities(text: str | None) -> list[tuple[str, str | int]]:
    """
    Extract Telegram chat identifiers from free-form text.

    Returns list of (type, value) tuples where type is one of:
    'channel_id', 'username', 'invite_link', 'numeric_id'
    """
    if not text:
        return []

    entities: list[tuple[str, str | int]] = []

    # Private channel links: t.me/c/1234567890/123
    for m in re.finditer(
        r'https?://(?:www\.)?(?:t\.me|telegram\.me|telegram\.org)/c/(\d{10,15})/\d+',
        text, re.IGNORECASE,
    ):
        entities.append(('channel_id', int(m.group(1))))

    # Usernames: @name or t.me/name
    for m in re.finditer(
        r'(?:@|(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.org)/)'
        r'([a-zA-Z0-9_]{5,32})(?![a-zA-Z0-9_/])',
        text, re.IGNORECASE,
    ):
        username = m.group(1)
        if username.lower() in ('joinchat', 'c', 'proxy', 's', 'addstickers'):
            continue
        if not username.lower().endswith('bot'):
            entities.append(('username', username))

    # Invite links: t.me/+xxx or t.me/joinchat/xxx
    for m in re.finditer(
        r'(https?://(?:www\.)?(?:t\.me|telegram\.me|telegram\.org)/(?:joinchat/|\+))'
        r'([a-zA-Z0-9_-]{10,64})',
        text, re.IGNORECASE,
    ):
        entities.append(('invite_link', m.group(1) + m.group(2)))

    # Numeric IDs
    for m in re.finditer(r'\b(\d{9,14})\b', text):
        entities.append(('numeric_id', int(m.group(1))))

    return entities


def _extract_invite_hash(identifier: str) -> str | None:
    m = re.search(r'(?:\+|joinchat/)([a-zA-Z0-9_-]{10,64})$', str(identifier))
    return m.group(1) if m else None


# ── Folder cache helpers ─────────────────────────────────────────────────────

async def _get_folders(
    client: TelegramClient,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> list[DialogFilter]:
    """Fetch dialog filters with TTL-based caching."""
    cid = id(client)
    now = time.monotonic()
    hit = cache.get(cid)
    if hit and (now - hit[0]) < _FOLDER_CACHE_TTL:
        return hit[1]

    result = await client(GetDialogFiltersRequest())
    filters = getattr(result, "filters", result)
    folders = [f for f in filters if isinstance(f, DialogFilter)]
    cache[cid] = (now, folders)
    return folders


def _invalidate_folder_cache(
    client: TelegramClient,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> None:
    cache.pop(id(client), None)


# ── Folder helpers ────────────────────────────────────────────────────────────

def _folder_title(folder: DialogFilter) -> str:
    t = folder.title
    return t if isinstance(t, str) else getattr(t, "text", str(t))


def _peer_id(peer) -> int | None:
    return (
        getattr(peer, "user_id",    None)
        or getattr(peer, "chat_id",    None)
        or getattr(peer, "channel_id", None)
    )


def _find_joined_folder(folders: list[DialogFilter]) -> DialogFilter | None:
    return next(
        (f for f in folders if _folder_title(f).lower() == _JOINED_FOLDER_NAME),
        None,
    )


async def _create_joined_folder(
    client: TelegramClient,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
    extra_peers: list | None = None,
) -> DialogFilter:
    folders      = await _get_folders(client, cache)
    existing_ids = {f.id for f in folders}

    # Req 1 fix: start from 2 (id=1 is the reserved "All Chats" pseudo-filter
    # in Telegram's UI, but DialogFilter objects returned by GetDialogFiltersRequest
    # use their own ID namespace starting at 2). We must skip any ID already
    # in use by an ACTUAL existing DialogFilter — not skip id==1 universally,
    # which was a bug (it caused the first user-created folder to always get id=3
    # even when id=2 was free).
    new_id = 2
    while new_id in existing_ids:
        new_id += 1

    saved      = InputPeerSelf()
    inc_peers  = [saved] + (extra_peers or [])

    new_folder = DialogFilter(
        id               = new_id,
        title            = TextWithEntities(text=_JOINED_FOLDER_NAME, entities=[]),
        pinned_peers     = [saved],
        include_peers    = inc_peers,
        exclude_peers    = [],
        contacts         = False,
        non_contacts     = False,
        groups           = False,
        broadcasts       = False,
        bots             = False,
        exclude_muted    = False,
        exclude_read     = False,
        exclude_archived = True,
    )

    await client(UpdateDialogFilterRequest(id=new_id, filter=new_folder))
    _invalidate_folder_cache(client, cache)
    log.debug(
        "[Account%d] Created '%s' folder (id=%d) with %d peer(s).",
        account_index, _JOINED_FOLDER_NAME, new_id, len(inc_peers),
    )
    return new_folder


async def _ensure_joined_folder_exists(
    client: TelegramClient,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> DialogFilter:
    folders = await _get_folders(client, cache)
    folder  = _find_joined_folder(folders)
    if folder:
        return folder
    log.debug("[Account%d] '%s' folder not found — creating.", account_index, _JOINED_FOLDER_NAME)
    return await _create_joined_folder(client, account_index, cache)


async def _add_single_peer_to_joined_folder(
    client: TelegramClient,
    entity,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> bool:
    """
    Req 1: Add a single entity to the 'joined' folder IMMEDIATELY after joining.

    Returns True if the peer was added (or already present), False on error.
    FloodWait is handled explicitly — up to 2 retries with wait.
    """
    for attempt in range(3):
        try:
            ip  = await client.get_input_entity(entity)
            pid = _peer_id(ip)
            if pid is None:
                return False

            folders = await _get_folders(client, cache)
            folder  = _find_joined_folder(folders)

            if not folder:
                await _create_joined_folder(client, account_index, cache, extra_peers=[ip])
                return True

            existing_ids = {_peer_id(p) for p in (folder.include_peers or [])} - {None}
            if pid in existing_ids:
                return True  # already there

            folder.include_peers = list(folder.include_peers or []) + [ip]
            # Fix: ensure existing joined folders also have exclude_archived=True
            # so that UpdateDialogFilterRequest does not silently un-archive the chat.
            if not folder.exclude_archived:
                folder.exclude_archived = True
                log.debug(
                    "[Account%d] Patched '%s' folder to exclude_archived=True.",
                    account_index, _JOINED_FOLDER_NAME,
                )
            await client(UpdateDialogFilterRequest(id=folder.id, filter=folder))
            _invalidate_folder_cache(client, cache)
            log.debug(
                "[Account%d] Added peer id=%d to '%s' folder (incremental).",
                account_index, pid, _JOINED_FOLDER_NAME,
            )
            return True

        except errors.FloodWaitError as exc:
            if attempt < 2:
                log.debug(
                    "[Account%d] FloodWait %ds on folder add for entity %s (attempt %d/3), waiting...",
                    account_index, exc.seconds, getattr(entity, "id", entity), attempt + 1,
                )
                await asyncio.sleep(exc.seconds + 2)
            else:
                log.warning(
                    "[Account%d] FloodWait exceeded retries on folder add for entity %s — skipping folder add.",
                    account_index, getattr(entity, "id", entity),
                )
                return False
        except Exception as exc:
            log.debug(
                "[Account%d] Could not add entity to folder: %s",
                account_index, exc,
            )
            return False

    return False


async def _add_peers_to_joined_folder(
    client: TelegramClient,
    entities: list,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> int:
    """Batch-add multiple entities. Used by the post-join summary path."""
    if not entities:
        return 0

    new_peers: list   = []
    new_ids: set[int] = set()

    for entity in entities:
        try:
            ip  = await client.get_input_entity(entity)
            pid = _peer_id(ip)
            if pid and pid not in new_ids:
                new_peers.append(ip)
                new_ids.add(pid)
        except Exception as exc:
            log.debug(
                "[Account%d] Could not resolve peer for %s: %s",
                account_index, getattr(entity, "id", entity), exc,
            )

    if not new_peers:
        return 0

    folders = await _get_folders(client, cache)
    folder  = _find_joined_folder(folders)

    if not folder:
        await _create_joined_folder(client, account_index, cache, extra_peers=new_peers)
        return len(new_peers)

    existing_peers = list(folder.include_peers or [])
    existing_ids   = {_peer_id(p) for p in existing_peers} - {None}

    added = 0
    for peer in new_peers:
        pid = _peer_id(peer)
        if pid not in existing_ids:
            existing_peers.append(peer)
            existing_ids.add(pid)
            added += 1

    if added == 0:
        return 0

    folder.include_peers = existing_peers
    await client(UpdateDialogFilterRequest(id=folder.id, filter=folder))
    _invalidate_folder_cache(client, cache)
    log.debug("[Account%d] Batch-added %d peer(s) to '%s' folder.", account_index, added, _JOINED_FOLDER_NAME)
    return added


async def _remove_peers_from_joined_folder(
    client: TelegramClient,
    entities: list,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> int:
    if not entities:
        return 0

    folders = await _get_folders(client, cache)
    folder  = _find_joined_folder(folders)
    if not folder:
        return 0

    remove_ids: set[int] = set()
    for entity in entities:
        try:
            ip  = await client.get_input_entity(entity)
            pid = _peer_id(ip)
            if pid:
                remove_ids.add(pid)
        except Exception:
            # Try to get ID directly from the entity object
            pid = getattr(entity, "id", None)
            if pid:
                remove_ids.add(pid)

    if not remove_ids:
        return 0

    original    = list(folder.include_peers or [])
    kept        = [p for p in original if _peer_id(p) not in remove_ids]
    removed_cnt = len(original) - len(kept)

    if removed_cnt == 0:
        return 0

    folder.include_peers = kept
    await client(UpdateDialogFilterRequest(id=folder.id, filter=folder))
    _invalidate_folder_cache(client, cache)
    log.debug(
        "[Account%d] Removed %d peer(s) from '%s' folder.",
        account_index, removed_cnt, _JOINED_FOLDER_NAME,
    )
    return removed_cnt


# ── Req 4: Strict folder exclusion ───────────────────────────────────────────

async def _add_to_all_other_folders_exclusion(
    client: TelegramClient,
    entity,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> None:
    """
    Req 4: Add a chat to the excluded_chats of every OTHER editable folder
    so it only appears in the 'joined' folder.

    Skips the 'joined' folder itself and any folder that already excludes it.
    Folders are updated one by one so a FloodWait on one doesn't block others.
    """
    try:
        ip  = await client.get_input_entity(entity)
        pid = _peer_id(ip)
        if pid is None:
            return
    except Exception as exc:
        log.debug("[Account%d] exclusion: could not resolve entity: %s", account_index, exc)
        return

    folders = await _get_folders(client, cache)

    for folder in folders:
        title = _folder_title(folder)
        if title.lower() == _JOINED_FOLDER_NAME:
            continue  # skip the joined folder itself

        existing_excl = list(folder.exclude_peers or [])
        excl_ids      = {_peer_id(p) for p in existing_excl} - {None}
        if pid in excl_ids:
            continue  # already excluded

        existing_excl.append(ip)
        folder.exclude_peers = existing_excl

        for attempt in range(3):
            try:
                await client(UpdateDialogFilterRequest(id=folder.id, filter=folder))
                _invalidate_folder_cache(client, cache)
                log.debug(
                    "[Account%d] Added peer id=%d to exclude_peers of folder '%s'.",
                    account_index, pid, title,
                )
                break
            except errors.FloodWaitError as exc:
                if attempt < 2:
                    await asyncio.sleep(exc.seconds + 2)
                else:
                    log.debug(
                        "[Account%d] FloodWait on folder exclusion for '%s' — skipped.",
                        account_index, title,
                    )
            except Exception as exc:
                log.debug(
                    "[Account%d] Could not update exclusion for folder '%s': %s",
                    account_index, title, exc,
                )
                break


async def _remove_from_all_folders_exclusion(
    client: TelegramClient,
    entities: list,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> None:
    """
    Req 4: When a chat is left (by any means), remove it from excluded_chats
    of ALL folders immediately and synchronously.

    This is called from _handle_left(), folder reset, and _check_auto_leave().
    """
    remove_ids: set[int] = set()
    for entity in entities:
        try:
            ip  = await client.get_input_entity(entity)
            pid = _peer_id(ip)
            if pid:
                remove_ids.add(pid)
        except Exception:
            pid = getattr(entity, "id", None)
            if pid:
                remove_ids.add(pid)

    if not remove_ids:
        return

    folders = await _get_folders(client, cache)
    for folder in folders:
        excl = list(folder.exclude_peers or [])
        new_excl = [p for p in excl if _peer_id(p) not in remove_ids]
        if len(new_excl) == len(excl):
            continue  # nothing to remove

        folder.exclude_peers = new_excl
        for attempt in range(3):
            try:
                await client(UpdateDialogFilterRequest(id=folder.id, filter=folder))
                _invalidate_folder_cache(client, cache)
                log.debug(
                    "[Account%d] Removed %d peer(s) from exclusion list of folder '%s'.",
                    account_index, len(excl) - len(new_excl), _folder_title(folder),
                )
                break
            except errors.FloodWaitError as exc:
                if attempt < 2:
                    await asyncio.sleep(exc.seconds + 2)
                else:
                    break
            except Exception as exc:
                log.debug(
                    "[Account%d] Could not clean exclusion for folder '%s': %s",
                    account_index, _folder_title(folder), exc,
                )
                break


# ── Req 3: Mute and archive helpers ──────────────────────────────────────────

async def _mute_chat(client: TelegramClient, entity, account_index: int) -> bool:
    """
    Req 3: Mute a chat immediately after joining.
    Sets mute_until to year 2038 (effectively permanent).
    Returns True on success.
    """
    try:
        input_peer = await client.get_input_entity(entity)
        await client(UpdateNotifySettingsRequest(
            peer=InputNotifyPeer(peer=input_peer),
            settings=InputPeerNotifySettings(
                show_previews=False,
                silent=False,
                mute_until=2147483647,  # max Unix timestamp (year 2038)
            ),
        ))
        log.debug(
            "[Account%d] Muted chat id=%s.",
            account_index, getattr(entity, "id", entity),
        )
        return True
    except errors.FloodWaitError as exc:
        log.debug("[Account%d] FloodWait %ds on mute — waiting.", account_index, exc.seconds)
        await asyncio.sleep(min(exc.seconds + 2, 30))
        try:
            input_peer = await client.get_input_entity(entity)
            await client(UpdateNotifySettingsRequest(
                peer=InputNotifyPeer(peer=input_peer),
                settings=InputPeerNotifySettings(
                    show_previews=False,
                    silent=False,
                    mute_until=2147483647,
                ),
            ))
            return True
        except Exception:
            return False
    except Exception as exc:
        log.debug("[Account%d] Could not mute chat: %s", account_index, exc)
        return False


async def _archive_chat(client: TelegramClient, entity, account_index: int) -> bool:
    """
    Req 3: Archive a chat immediately after joining.
    Uses EditPeerFoldersRequest to move it to folder_id=1 (Archive).
    Returns True on success.
    """
    try:
        input_peer = await client.get_input_entity(entity)
        await client(EditPeerFoldersRequest(
            folder_peers=[InputFolderPeer(peer=input_peer, folder_id=1)],
        ))
        log.debug(
            "[Account%d] Archived chat id=%s.",
            account_index, getattr(entity, "id", entity),
        )
        return True
    except errors.FloodWaitError as exc:
        log.debug("[Account%d] FloodWait %ds on archive — waiting.", account_index, exc.seconds)
        await asyncio.sleep(min(exc.seconds + 2, 30))
        try:
            input_peer = await client.get_input_entity(entity)
            await client(EditPeerFoldersRequest(
                folder_peers=[InputFolderPeer(peer=input_peer, folder_id=1)],
            ))
            return True
        except Exception:
            return False
    except Exception as exc:
        log.debug("[Account%d] Could not archive chat: %s", account_index, exc)
        return False


async def _mute_and_archive(client: TelegramClient, entity, account_index: int) -> tuple[bool, bool]:
    """Mute and archive a chat. Returns (muted, archived)."""
    muted    = await _mute_chat(client, entity, account_index)
    archived = await _archive_chat(client, entity, account_index)
    return muted, archived


# ── Folder reset helper ───────────────────────────────────────────────────────

async def _leave_and_reset_joined_folder(
    client: TelegramClient,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> tuple[int, int]:
    """
    Leave all chats in the 'joined' folder, then recreate it.
    Req 4: Also removes each left chat from all other folders' exclusion lists.
    Req 5: Uses paced loop (_LEFT_INTER_DELAY) instead of a tight loop.
    """
    folders      = await _get_folders(client, cache)
    folder       = _find_joined_folder(folders)
    left_count   = 0
    failed_count = 0
    left_entities: list = []

    if folder:
        peers_to_leave = [
            p for p in (folder.include_peers or [])
            if not isinstance(p, InputPeerSelf)
        ]

        for idx, peer in enumerate(peers_to_leave):
            pid = _peer_id(peer)
            if not pid:
                continue

            fw_retries = 0
            while True:
                try:
                    entity = await client.get_entity(peer)
                    if isinstance(entity, Channel):
                        await client(LeaveChannelRequest(entity))
                    elif isinstance(entity, (Chat, User)):
                        await client(DeleteHistoryRequest(peer=entity, just_clear=False, max_id=0))
                    left_count += 1
                    left_entities.append(entity)
                    break
                except errors.UserNotParticipantError:
                    # Already left — still counts as "handled"
                    left_count += 1
                    break
                except errors.FloodWaitError as exc:
                    fw_retries += 1
                    if fw_retries > _LEFT_MAX_FW_RETRIES:
                        failed_count += 1
                        log.debug(
                            "[Account%d] Folder reset: FloodWait retry cap on peer id=%d — skipping.",
                            account_index, pid,
                        )
                        break
                    log.debug(
                        "[Account%d] Folder reset: FloodWait %ds on peer id=%d (retry %d/%d).",
                        account_index, exc.seconds, pid, fw_retries, _LEFT_MAX_FW_RETRIES,
                    )
                    await asyncio.sleep(exc.seconds + 2)
                except Exception as exc:
                    failed_count += 1
                    log.debug("[Account%d] Folder reset: could not leave peer id=%d: %s", account_index, pid, exc)
                    break

            # Req 5: inter-leave pacing
            if idx < len(peers_to_leave) - 1:
                await asyncio.sleep(_LEFT_INTER_DELAY)

        await client(UpdateDialogFilterRequest(id=folder.id))
        _invalidate_folder_cache(client, cache)

    # Req 4: clean exclusion lists for everything we left
    if left_entities:
        await _remove_from_all_folders_exclusion(client, left_entities, account_index, cache)

    await _create_joined_folder(client, account_index, cache)
    return left_count, failed_count


# ── Module ────────────────────────────────────────────────────────────────────

class JoinLeft(Module):
    """Join/leave chats with folder management, mute/archive, and auto-leave."""

    name = "join_left"
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
        days = self._settings.get("auto_leave_days")
        if days is None:
            return 0

        if not client.is_connected():
            return 0

        try:
            folders = await _get_folders(client, self._folder_cache)
            folder = _find_joined_folder(folders)
            if not folder:
                return 0

            peers = [p for p in (folder.include_peers or []) if not isinstance(p, InputPeerSelf)]
            if not peers:
                return 0

            old_timestamp = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days + 1)
            old_iso = old_timestamp.isoformat()

            added = 0
            async with self._settings_lock:
                for peer in peers:
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
            # Signal caller that this is an "already member" case
            # by raising — caller will handle it as a success.
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
        join path (ImportChatInviteRequest result is properly unwrapped),
        so the workaround string-match is no longer needed or correct.
        """
        if isinstance(exc, errors.UserAlreadyParticipantError):
            return True
        if "already a part" in str(exc).lower():
            return True
        return False

    # ── Per-join post-processing (mute, archive, folder, exclusion) ───────────

    async def _post_join_actions(
        self,
        client: TelegramClient,
        entity,
        account_index: int,
    ) -> tuple[bool, bool, bool]:
        """
        Req 3 + Req 4: Immediately after a successful join, in this exact order:
        1. Mute the chat
        2. Add to 'joined' folder (incremental, with exclude_archived=True patch)
        3. Add to excluded_chats of all OTHER folders
        4. Archive the chat (LAST — prevents folder operations from un-archiving)

        Archive is deliberately last: UpdateDialogFilterRequest with
        exclude_archived=False would silently move the chat back to folder_id=0.
        By archiving after all folder operations are complete, no subsequent
        Telegram-side side-effect can undo it.

        Returns (muted, archived, folder_added).
        """
        # Step 1: mute
        muted        = await _mute_chat(client, entity, account_index)
        # Step 2: add to joined folder (patching exclude_archived=True if needed)
        folder_added = await _add_single_peer_to_joined_folder(
            client, entity, account_index, self._folder_cache
        )
        # Step 3: exclusion — all 4 actions are strictly sequential per chat so
        # that a crash mid-run never leaves a chat partially processed.
        await _add_to_all_other_folders_exclusion(client, entity, account_index, self._folder_cache)
        # Step 4: archive last — no further folder operations follow that could undo this
        archived     = await _archive_chat(client, entity, account_index)
        return muted, archived, folder_added

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

    # ── Helper: collect entities ──────────────────────────────────────────────

    @staticmethod
    def _collect_entities(reply_msg, command_msg) -> set[tuple]:
        entities: set[tuple] = set()
        entities.update(extract_telegram_entities(reply_msg.message))
        entities.update(extract_telegram_entities(command_msg.message))

        if hasattr(reply_msg, "reply_markup") and isinstance(reply_msg.reply_markup, ReplyInlineMarkup):
            for row in reply_msg.reply_markup.rows:
                for button in row.buttons:
                    if isinstance(button, KeyboardButtonUrl):
                        entities.update(extract_telegram_entities(button.url))
        return entities

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
            days = self._settings["auto_leave_days"]
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

            while True:
                if client.is_connected():
                    await self._check_auto_leave(client)
                await asyncio.sleep(6 * 3600)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._log_error("Auto-leave loop crashed: %s", exc)

    async def _check_auto_leave(self, client: TelegramClient) -> None:
        """
        Check for expired chats and leave them.

        Req 4 extension: also verifies that chats we've ever joined are still
        joined — covers manual leaves (Telegram doesn't reliably push
        "you left" notifications). Any chat found to be no longer joined is
        cleaned from tracking AND from all folder exclusion lists.

        Req 5: paced loop with FloodWait retry cap.
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

        now = datetime.datetime.now(datetime.UTC)
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
            left_entity = None
            try:
                entity = await client.get_entity(chat_id)
                name = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(chat_id)

                if isinstance(entity, Channel):
                    await client(LeaveChannelRequest(entity))
                elif isinstance(entity, (Chat, User)):
                    await client(DeleteHistoryRequest(peer=entity, just_clear=False, max_id=0))

                left_entity = entity
                self._log_debug("Auto-left '%s' (id=%d, joined %s).", name, chat_id, joined_at_str)

                async with self._settings_lock:
                    self._settings["joined_chats"].pop(str(chat_id), None)
                    await self._save_settings()

                await _remove_peers_from_joined_folder(
                    client, [entity], self.cfg.index, self._folder_cache
                )

            except errors.UserNotParticipantError:
                self._log_debug("Auto-leave: not participant in %d, removing from tracking", chat_id)
                async with self._settings_lock:
                    self._settings["joined_chats"].pop(str(chat_id), None)
                    await self._save_settings()
                # Still need to clean exclusion lists
                try:
                    entity = await client.get_entity(chat_id)
                    left_entity = entity
                except Exception:
                    pass

            except errors.ChannelPrivateError:
                self._log_debug("Auto-leave: channel %d is private/inaccessible, removing from tracking", chat_id)
                async with self._settings_lock:
                    self._settings["joined_chats"].pop(str(chat_id), None)
                    await self._save_settings()

            except (ValueError, errors.UsernameNotOccupiedError) as exc:
                self._log_debug("Auto-leave: entity %d not found (%s), removing from tracking", chat_id, exc)
                async with self._settings_lock:
                    self._settings["joined_chats"].pop(str(chat_id), None)
                    await self._save_settings()

            except errors.FloodWaitError as exc:
                self._log_debug("Auto-leave FloodWait %ds for %d (will retry next cycle).", exc.seconds, chat_id)
                # Don't spin — wait and then move on to next entity
                await asyncio.sleep(min(exc.seconds + 2, 60))

            except Exception as exc:
                self._log_debug("Auto-leave failed for %d (will retry): %s", chat_id, exc)

            # Req 4: clean exclusion lists for anything we left
            if left_entity is not None:
                await _remove_from_all_folders_exclusion(
                    client, [left_entity], self.cfg.index, self._folder_cache
                )

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
        client = event.client
        if not await self._is_saved_messages(event):
            return

        await self._safe_edit(event, "🔄 Processing 'joined' folder...")
        folders = await _get_folders(client, self._folder_cache)
        folder  = _find_joined_folder(folders)

        if not folder:
            await _create_joined_folder(client, self.cfg.index, self._folder_cache)
            await self._safe_edit_with_auto_delete(
                event,
                f"✅ فولدر **'{_JOINED_FOLDER_NAME}'** ساخته شد.\n📌 Saved Messages پین شد."
            )
            self._log_debug("[Account%d] Created '%s' folder", self.cfg.index, _JOINED_FOLDER_NAME)
        else:
            await self._safe_edit(
                event,
                f"🔄 در حال ترک تمام چت‌های **'{_JOINED_FOLDER_NAME}'** و ریست..."
            )
            left, failed = await _leave_and_reset_joined_folder(
                client, self.cfg.index, self._folder_cache
            )
            # Clear tracking for all chats we just left
            async with self._settings_lock:
                self._settings["joined_chats"] = {}
                await self._save_settings()

            msg = f"✅ فولدر **'{_JOINED_FOLDER_NAME}'** ریست شد.\n• ترک شده: {left} چت\n"
            if failed:
                msg += f"• ناموفق: {failed} چت\n"
            msg += "📌 Saved Messages پین شد."
            await self._safe_edit_with_auto_delete(event, msg)
            self._log_debug(
                "[Account%d] Reset '%s' folder (left=%d, failed=%d)",
                self.cfg.index, _JOINED_FOLDER_NAME, left, failed,
            )

    async def _handle_list(self, event) -> None:
        client = event.client
        if not await self._is_saved_messages(event):
            return

        await self._safe_edit(event, "🔍 Loading 'joined' folder contents...")
        folders = await _get_folders(client, self._folder_cache)
        folder  = _find_joined_folder(folders)

        if not folder:
            await self._safe_edit_with_auto_delete(event, f"ℹ️ فولدر **'{_JOINED_FOLDER_NAME}'** وجود ندارد.")
            return

        peers = [p for p in (folder.include_peers or []) if not isinstance(p, InputPeerSelf)]
        if not peers:
            await self._safe_edit_with_auto_delete(
                event,
                f"ℹ️ فولدر **'{_JOINED_FOLDER_NAME}'** خالی است (فقط Saved Messages)."
            )
            return

        lines = [f"📁 **فولدر '{_JOINED_FOLDER_NAME}' — {len(peers)} چت:**\n"]
        for i, peer in enumerate(peers, 1):
            pid = _peer_id(peer)
            try:
                entity = await client.get_entity(peer)
                name   = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(pid)
                uname  = getattr(entity, "username", None)
                tag    = f"@{uname}" if uname else f"`{pid}`"
                lines.append(f"{i}. **{name}** — {tag}")
            except Exception:
                lines.append(f"{i}. `{pid}`")

        await self._safe_edit_with_auto_delete(event, "\n".join(lines))
        self._log_debug("[Account%d] Listed %d chats in '%s' folder", self.cfg.index, len(peers), _JOINED_FOLDER_NAME)

    # ── JOIN (4-layer anti-FloodWait) ─────────────────────────────────────────

    async def _handle_join(self, event) -> None:
        """
        Main join handler with 4-layer anti-FloodWait strategy.

        v3.0.3 changes:
        - Req 1: folder addition is incremental (per-chat, immediately)
        - Req 2: already-member errors on ALL paths treated as success
        - Req 3: mute+archive immediately after each join
        - Req 4: exclusion added to all other folders per-chat
        - Req 5: per-entity FloodWait retry cap (_MAX_FLOODWAIT_RETRIES)
        """
        client    = event.client
        reply_msg = await event.get_reply_message()
        if not reply_msg:
            return

        all_entities = list(self._collect_entities(reply_msg, event.message))
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

        try:
            processing_msg = await event.edit(
                f"🔍 `{len(all_entities)}` مورد یافت شد. "
                f"(mode: `{join_mode}`, delay: `{user_delay}s`)\n⏳ در حال جوین..."
            )
        except Exception as exc:
            self._log_error("Failed to create join progress message: %s", exc)
            return

        results: list[str]    = []
        joined_entities: list = []

        start_time    = time.monotonic()
        join_times: list[float] = []
        success_count = 0
        fail_count    = 0
        flood_count   = 0

        adaptive_mult: float = 1.0

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
                except errors.FloodWaitError as e:
                    self._log_warning("Edit FloodWait %ds", e.seconds)
                    await asyncio.sleep(e.seconds)
                except Exception as exc:
                    self._log_error("Throttled edit failed: %s", exc)

        async def floodwait_countdown(total_seconds: int, label: str) -> None:
            remaining = total_seconds
            chunk = 5 if total_seconds < 60 else 30
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

        async def handle_floodwait(exc: errors.FloodWaitError, label: str) -> float:
            nonlocal adaptive_mult

            seconds = exc.seconds
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
        # MAIN LOOP
        # ═══════════════════════════════════════════════════════════════════════

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
                        # v3.0.4: use isinstance(ChatInviteAlready) — type-safe access to
                        # .chat attribute (ChatInvite objects do NOT have .chat).
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
                            joined_entities.append(joined_entity)

                            async with self._settings_lock:
                                self._settings["joined_chats"][str(joined_entity.id)] = \
                                    datetime.datetime.now(datetime.UTC).isoformat()
                                await self._save_settings()
                            await self._post_join_actions(client, joined_entity, self.cfg.index)
                        else:
                            results.append(f"ℹ️ [{identifier}] — قبلاً عضو بود (جوین موفق)")
                            success_count += 1
                            join_times.append(time.monotonic() - attempt_start)

                        await safe_edit(
                            f"🔄 در حال جوین... ({idx}/{len(all_entities)}) {mode_label}\n"
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
                    # ── Join by effective type ─────────────────────────────────

                    if entity_type == "channel_id":
                        chan_id = int(f"-100{identifier}")
                        try:
                            joined_entity = await client.get_entity(chan_id)
                        except Exception:
                            joined_entity = await client.get_entity(identifier)

                    elif entity_type == "username":
                        try:
                            ip      = await client.get_input_entity(f"@{identifier}")
                            updates = await client(JoinChannelRequest(ip))
                            joined_entity = updates.chats[0] if updates.chats else None
                        except errors.UserAlreadyParticipantError:
                            # Req 2: already a member — treat as success
                            joined_entity = await client.get_entity(f"@{identifier}")
                        except (errors.UsernameNotOccupiedError, errors.ChannelPrivateError):
                            raise
                        except Exception as exc:
                            if self._is_already_member_error(exc):
                                joined_entity = await client.get_entity(f"@{identifier}")
                            else:
                                joined_entity = await client.get_entity(f"@{identifier}")

                    elif entity_type == "numeric_id":
                        joined_entity = await client.get_entity(identifier)

                    elif entity_type == "invite_link":

                        if effective_type == "invite_with_username" and resolved_username:
                            try:
                                ip      = await client.get_input_entity(f"@{resolved_username}")
                                updates = await client(JoinChannelRequest(ip))
                                joined_entity = updates.chats[0] if updates.chats else None
                                if joined_entity is None:
                                    joined_entity = await client.get_entity(f"@{resolved_username}")
                            except errors.UserAlreadyParticipantError:
                                # Req 2: already a member on username path
                                joined_entity = await client.get_entity(f"@{resolved_username}")
                            except Exception as exc:
                                if self._is_already_member_error(exc):
                                    joined_entity = await client.get_entity(f"@{resolved_username}")
                                else:
                                    # Fall back to hash path
                                    self._log_debug(
                                        "[Account%d] Username join failed for @%s, falling back to hash",
                                        self.cfg.index, resolved_username,
                                    )
                                    effective_type = "invite_direct"
                                    invite_hash = _extract_invite_hash(str(identifier))
                                    if invite_hash:
                                        try:
                                            # v3.0.4: unwrap ChatInviteJoinResultOk.updates
                                            raw2 = await client(ImportChatInviteRequest(invite_hash))
                                            if isinstance(raw2, tl_messages.ChatInviteJoinResultOk):
                                                joined_entity = raw2.updates.chats[0] if raw2.updates.chats else None
                                            elif isinstance(raw2, tl_messages.ChatInviteJoinResultWebView):
                                                joined_entity = None  # WebView-gated, can't join
                                            else:
                                                joined_entity = None
                                        except errors.UserAlreadyParticipantError:
                                            joined_entity = await client.get_entity(f"@{resolved_username}")
                                        except Exception as exc2:
                                            if self._is_already_member_error(exc2):
                                                joined_entity = await client.get_entity(f"@{resolved_username}")
                                            else:
                                                raise exc2

                        else:
                            # ── DIRECT HASH PATH: ImportChatInviteRequest ──────
                            # v3.0.4: This path is now a single API call.
                            # No pre-check (CheckChatInviteRequest) is made because
                            # ChatInvite objects (truly private groups/channels) have
                            # no .username field — the pre-check would always return
                            # None and waste an API call.
                            invite_hash = _extract_invite_hash(str(identifier))
                            if not invite_hash:
                                results.append(f"❌ [{identifier}] — لینک قابل parse نیست")
                                fail_count += 1
                                break

                            try:
                                # v3.0.4 Fix A: ImportChatInviteRequest returns
                                # messages.ChatInviteJoinResultOk (a wrapper with a single
                                # .updates attribute of TypeUpdates). The .chats list lives on
                                # .updates.chats, NOT directly on the result object.
                                # Accessing .chats on the wrapper caused AttributeError in v3.0.3.
                                raw = await client(ImportChatInviteRequest(invite_hash))

                                if isinstance(raw, tl_messages.ChatInviteJoinResultOk):
                                    # Standard success: unwrap the Updates object
                                    actual_updates = raw.updates
                                    joined_entity = actual_updates.chats[0] if actual_updates.chats else None
                                elif isinstance(raw, tl_messages.ChatInviteJoinResultWebView):
                                    # v3.0.4 Fix E: subscription/bot-gated channel.
                                    # Cannot join via standard API; requires WebView interaction.
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
                                else:
                                    # Unknown future result type
                                    self._log_debug(
                                        "[Account%d] ImportChatInviteRequest returned unexpected "
                                        "type %s for hash %.8s",
                                        self.cfg.index, type(raw).__name__, invite_hash,
                                    )
                                    joined_entity = None

                                if joined_entity is not None and use_smart:
                                    uname = getattr(joined_entity, "username", None)
                                    async with self._invite_cache_lock:
                                        self._invite_cache[invite_hash] = {
                                            "username": uname or None,
                                            "ts": time.time(),
                                        }
                                    asyncio.create_task(self._save_invite_cache())

                            except errors.UserAlreadyParticipantError:
                                # Already a member — retrieve entity via CheckChatInviteRequest.
                                # v3.0.4 Fix D: use isinstance(ChatInviteAlready) for type-safe
                                # access to .chat. ChatInvite (not-yet-member) has no .chat attr.
                                try:
                                    result_check = await client(CheckChatInviteRequest(invite_hash))
                                    if isinstance(result_check, ChatInviteAlready):
                                        joined_entity = result_check.chat
                                except Exception:
                                    pass

                                title = getattr(joined_entity, "title", None) if joined_entity else None
                                results.append(f"ℹ️ [{title or identifier}] — قبلاً عضو بود (جوین موفق)")
                                success_count += 1
                                join_times.append(time.monotonic() - attempt_start)
                                if joined_entity:
                                    joined_entities.append(joined_entity)
                                    async with self._settings_lock:
                                        self._settings["joined_chats"][str(joined_entity.id)] = \
                                            datetime.datetime.now(datetime.UTC).isoformat()
                                        await self._save_settings()
                                    await self._post_join_actions(client, joined_entity, self.cfg.index)
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

                        joined_entities.append(joined_entity)
                        results.append(f"{path_icon} [{title}] ✅")
                        success_count += 1
                        join_times.append(time.monotonic() - attempt_start)

                        if use_adaptive and adaptive_mult > 1.0:
                            adaptive_mult = max(1.0, adaptive_mult * _ADAPTIVE_DECAY)

                        # Track join timestamp
                        async with self._settings_lock:
                            self._settings["joined_chats"][str(joined_entity.id)] = \
                                datetime.datetime.now(datetime.UTC).isoformat()
                            await self._save_settings()

                        # Req 1 + Req 3 + Req 4: immediate per-chat post-processing
                        # v3.0.4: awaited (not fire-and-forget) for crash-safety
                        await self._post_join_actions(client, joined_entity, self.cfg.index)

                    break  # ← success, exit retry loop

                # ── FloodWait handling (Layer 3 + Req 5 retry cap) ────────────
                except errors.FloodWaitError as exc:
                    fw_retry_count += 1
                    flood_count += 1

                    if fw_retry_count > _MAX_FLOODWAIT_RETRIES:
                        # Req 5: give up on this entity rather than looping forever
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
                        await floodwait_countdown(exc.seconds + 2, str(identifier))
                    continue  # retry

                # ── Other errors ──────────────────────────────────────────────
                except Exception as exc:
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
                    elif isinstance(exc, errors.InviteHashInvalidError) or "INVITE_HASH_INVALID" in err:
                        status = "❌ لینک نامعتبر"
                    elif isinstance(exc, errors.UsernameNotOccupiedError):
                        status = "❌ یوزرنیم وجود ندارد"
                    elif isinstance(exc, errors.ChannelPrivateError):
                        status = "🔒 خصوصی/محدود"
                    elif "FLOOD_WAIT" in err:
                        status = f"⏳ FloodWait: {err[:40]}"
                    else:
                        status = f"❌ خطا: {err[:40]}"
                    results.append(f"[{identifier}] — {status}")
                    break

            # ── Live progress update ───────────────────────────────────────────
            await safe_edit(
                f"🔄 در حال جوین... ({idx}/{len(all_entities)}) {mode_label}\n"
                f"آخرین: {results[-1] if results else '-'}\n"
                f"_mult: {adaptive_mult:.1f}×_"
                if use_adaptive and adaptive_mult > 1.0
                else
                f"🔄 در حال جوین... ({idx}/{len(all_entities)}) {mode_label}\n"
                f"آخرین: {results[-1] if results else '-'}"
            )

            # ─── Apply inter-join delay (Layers 2 + 3) ────────────────────────
            if idx < len(all_entities):
                this_delay = compute_delay(effective_type)
                if this_delay > 0:
                    await asyncio.sleep(this_delay)

            # ── Layer 4: Batch cooldown (human mode only) ─────────────────────
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
        # POST-LOOP: Summary (folder already updated incrementally)
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

        # Req 1: folder was already updated incrementally, but report final count
        folder_count = len(joined_entities)
        folder_note  = f"\n📁 `{folder_count}` چت به فولدر '{_JOINED_FOLDER_NAME}' اضافه شد (incremental)." \
                       if folder_count > 0 else ""

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
        Req 4: After leaving, immediately remove from all folder exclusion lists.
        Req 5: Use paced loop with FloodWait retry cap.
        """
        client = event.client
        reply_msg = await event.get_reply_message()
        if not reply_msg:
            return

        all_entities = self._collect_entities(reply_msg, event.message)
        if not all_entities:
            await self._safe_edit_with_auto_delete(event, "ℹ️ هیچ لینک، یوزرنیم یا ID تلگرامی یافت نشد.")
            return

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
                        # For leave via invite, we need to be a member; try get_entity
                        try:
                            # ImportChatInviteRequest is too risky here (we may already be member)
                            # Use CheckChatInviteRequest to peek at the chat first
                            result_check = await client(CheckChatInviteRequest(invite_hash))
                            for attr in ("chat", "channel"):
                                obj = getattr(result_check, attr, None)
                                if obj is not None:
                                    target_entity = obj
                                    break
                        except Exception as exc:
                            results.append(f"❌ [{identifier}] — خطا ({exc})")
                            break

                    if target_entity is None:
                        results.append(f"❌ [{identifier}] — یافت نشد")
                        break

                    name = getattr(target_entity, "title", None) or getattr(target_entity, "first_name", None) or str(identifier)

                    if isinstance(target_entity, Channel):
                        await client(LeaveChannelRequest(target_entity))
                    elif isinstance(target_entity, (Chat, User)):
                        await client(DeleteHistoryRequest(peer=target_entity, just_clear=False, max_id=0))

                    results.append(f"✅ [{name}] — ترک شد")
                    any_successful_left = True
                    left_entities.append(target_entity)

                    async with self._settings_lock:
                        self._settings["joined_chats"].pop(str(target_entity.id), None)
                        await self._save_settings()

                    break

                except errors.UserNotParticipantError:
                    results.append(f"ℹ️ [{identifier}] — از قبل عضو نبود")
                    break

                except errors.FloodWaitError as exc:
                    fw_retries += 1
                    if fw_retries > _LEFT_MAX_FW_RETRIES:
                        self._log_debug(
                            "[Account%d] Left: FloodWait retry cap on '%s' — skipping.",
                            self.cfg.index, identifier,
                        )
                        results.append(f"⏳ [{identifier}] — FloodWait زیاد، رد شد")
                        break
                    self._log_debug("Left FloodWait %ds", exc.seconds)
                    try:
                        await processing_msg.edit(f"⏳ Flood wait {exc.seconds}s برای `{identifier}`...")
                    except Exception:
                        pass
                    await asyncio.sleep(exc.seconds + 2)
                    continue

                except Exception as exc:
                    results.append(f"❌ [{identifier}] — {str(exc)[:40]}")
                    break

            # Req 5: inter-leave pacing
            if idx < len(all_entities) - 1:
                await asyncio.sleep(_LEFT_INTER_DELAY)

        # Req 4: remove from joined folder AND clean all other folders' exclusion lists
        if left_entities:
            try:
                removed = await _remove_peers_from_joined_folder(
                    client, left_entities, self.cfg.index, self._folder_cache
                )
                folder_note = f"\n📁 `{removed}` چت از فولدر '{_JOINED_FOLDER_NAME}' حذف شد." if removed else ""
            except Exception as exc:
                self._log_error("Failed to remove peers from folder: %s", exc)
                folder_note = ""

            # Req 4: synchronous exclusion cleanup
            await _remove_from_all_folders_exclusion(
                client, left_entities, self.cfg.index, self._folder_cache
            )
        else:
            folder_note = ""

        final_text = "--- نتایج ترک ---\n" + "\n".join(results) + "\n------------------" + folder_note
        try:
            await processing_msg.edit(final_text, parse_mode="Markdown")
        except Exception:
            pass

        self._track_delete_task(processing_msg, _AUTO_DELETE_DELAY)

        if any_successful_left:
            await safe_delete(client, event.chat_id, event.message.id)
            if event.is_reply and reply_msg and reply_msg.out:
                try:
                    await client.edit_message(reply_msg, ".")
                except Exception:
                    pass

        self._log_debug(
            "[Account%d] Left completed: %d entities processed",
            self.cfg.index, len(all_entities)
        )


# ── Help Texts ────────────────────────────────────────────────────────────────

help_text = (
    "• `join` (reply) | عضویت در چت‌های reply شده\n"
    "• `left` (reply) | ترک چت‌های reply شده\n"
    "• `join delay <seconds>` | تنظیم تاخیر ثابت (0 = بازگشت به smart)\n"
    "• `join mode fast|safe|human` | تنظیم حالت ضد-FloodWait\n"
    "• `folder` | ایجاد یا ریست فولدر joined\n"
    "• `list` | نمایش لیست چت‌های فولدر\n"
    "• `autoleave <days>` | ترک خودکار پس از N روز\n"
    "• `autoleave off` | غیرفعال‌سازی ترک خودکار\n"
    "• `autoleave status` | نمایش وضعیت فعلی\n"
)

help_extra = (
    "عضویت و ترک - مدیریت چت‌ها با فولدر، mute، archive و ترک خودکار\n\n"
    "دستورات اصلی:\n"
    "• `join` (reply) | عضویت در همه چت‌های یافت‌شده در پیام reply\n"
    "• `left` (reply) | ترک همه چت‌های یافت‌شده در پیام reply\n\n"
    "انواع لینک‌های پشتیبانی‌شده:\n"
    "• لینک‌های عمومی | `t.me/username`\n"
    "• لینک‌های خصوصی جدید | `t.me/+AbCdEfGh`\n"
    "• لینک‌های خصوصی قدیمی | `t.me/joinchat/AbCdEfGh`\n"
    "• شناسه عددی | `1234567890`\n"
    "• لینک‌های خصوصی کانال | `t.me/c/1234567890/123`\n\n"
    "رفتارهای جدید v3.0.3:\n"
    "• Mute و Archive فوری پس از هر جوین (نه در دسته‌های انتها)\n"
    "• افزودن به فولدر 'joined' فوری پس از هر جوین (incremental)\n"
    "• اضافه شدن به excluded_chats فولدرهای دیگر (چت فقط در 'joined' نشان داده می‌شود)\n"
    "• 'قبلاً عضو' روی همه مسیرها به‌عنوان موفقیت کامل تلقی می‌شود\n"
    "• حداکثر تعداد retry روی FloodWait برای هر entity (بجای loop بی‌نهایت)\n\n"
    "سیستم ضد-FloodWait (۴ لایه):\n"
    "• `join mode fast`  | بدون throttling، بدون batching — سریع‌ترین\n"
    "• `join mode safe`  | Layer 1+2: حل هوشمند لینک + تأخیر بر اساس ریسک [پیش‌فرض]\n"
    "• `join mode human` | Layer 1+2+3+4: تمام لایه‌ها + batch cooldown — ایمن‌ترین\n\n"
    "Layer 1 — Smart Link Resolution:\n"
    "  → invite link→ بررسی قبل از جوین → اگر username داشت → JoinChannelRequest (ایمن)\n"
    "  → نتایج cache می‌شوند (۶ ساعت) در join_left_invite_cache.json\n"
    "  → نماد 🛡 = از smart path استفاده شد | نماد 🔑 = مستقیم از hash\n\n"
    "Layer 2 — Risk-Based Delays:\n"
    "  → username: 2s | channel_id/numeric_id: 3s | invite→username: 4s | invite direct: 8s\n"
    "  → وقتی `join delay` روی ۰ باشد (پیش‌فرض) فعال است\n\n"
    "Layer 3 — Adaptive FloodWait (safe/human):\n"
    "  → FloodWait خفیف (<30s): ضریب ×1.5\n"
    "  → FloodWait متوسط (<300s): ضریب ×3.0\n"
    "  → FloodWait سنگین (≥300s): ضریب ×5.0\n"
    "  → با هر جوین موفق، ضریب ۱۰٪ کاهش می‌یابد\n\n"
    "Layer 4 — Batch & Cooldown (human only):\n"
    "  → بعد از هر ۵ جوین: استراحت ۳۰s\n"
    "  → بعد از هر ۳ دسته: استراحت ۱۲۰s\n"
    "  → jitter ±۲۰٪ روی تمام تأخیرها\n\n"
    "تنظیمات تاخیر:\n"
    "• `join delay <seconds>` | تأخیر ثابت — smart throttling را غیرفعال می‌کند\n"
    "• `join delay 0` | بازگشت به smart throttling\n"
    "• پیش‌فرض | ۰ ثانیه + smart mode = safe\n\n"
    "مدیریت فولدر:\n"
    "• `folder` | ایجاد یا ریست فولدر `joined` — چت‌های موجود را ترک و حذف می‌کند\n"
    "• `list` | نمایش لیست چت‌های موجود در فولدر `joined`\n\n"
    "ترک خودکار:\n"
    "• `autoleave <days>` | ترک چت‌های فولدر `joined` پس از N روز\n"
    "  → شامل چت‌های از قبل موجود در فولدر هم می‌شود\n"
    "• `autoleave off` | غیرفعال‌سازی ترک خودکار\n"
    "• `autoleave status` | نمایش وضعیت فعلی\n\n"
    "مثال‌ها:\n"
    "• یک پیام با چند لینک چت را reply کنید و `join` بفرستید\n"
    "• `join mode human` | حالت کاملاً ایمن برای جوین انبوه\n"
    "• `join delay 3` | تأخیر ثابت ۳ ثانیه (smart را غیرفعال می‌کند)\n"
    "• `join delay 0` | بازگشت به smart throttling\n"
    "• `autoleave 7` | ترک خودکار بعد از یک هفته\n\n"
    "نکات مهم:\n"
    "• `join`, `left`, `join delay`, `join mode` در هر چتی کار می‌کنند\n"
    "• `folder`, `list`, `autoleave` فقط در Saved Messages کار می‌کنند\n"
    "• چت‌های 'قبلاً عضو' مانند جوین موفق مدیریت می‌شوند (mute، archive، folder)\n"
    "• در صورت FloodWait، شمارش معکوس زنده نمایش داده می‌شود\n"
    "• هر entity حداکثر ۵ بار retry FloodWait دارد — بعد skip می‌شود\n"
    "• پس از `left` موفق، پیام دستور به‌صورت خودکار حذف می‌شود\n"
    "• هنگام فعال‌سازی autoleave، چت‌های موجود در فولدر هم ردیابی می‌شوند\n"
)

JoinLeft.help_text = help_text
JoinLeft.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return JoinLeft(context)
