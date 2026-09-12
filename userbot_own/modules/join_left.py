"""
userbot_own/modules/join_left.py
════════════════════════════════════════════════════════════════
Join / Left / Folder / List / AutoLeave / Join Delay

Commands:
- `join` (reply to a message with links/usernames/IDs)
  → Join all found chats with live progress updates
  → 4-layer anti-FloodWait: smart resolution, risk-based delays,
    adaptive backoff, human-like batching
  → Add each joined chat to 'joined' (or 'joined2', 'joined3', ...) folder
    IMMEDIATELY (incremental, auto-overflow at 100-chat cap)
  → Mute & archive each chat IMMEDIATELY after joining (crash-safe)

- `join delay <seconds>`
  → Set fixed delay between joins (0 = restore smart throttling)

- `join mode fast|safe|human`
  → fast  : no smart throttling, no batching (respects delay setting)
  → safe  : Layer 1 + Layer 2 — smart resolution + risk delays [DEFAULT]
  → human : all 4 layers — adds adaptive backoff + batch cooldowns

- `left` (reply to a message with links/usernames/IDs)
  → Leave all found chats with safe pacing (FloodWait-aware)
  → Remove left chats from ALL 'joined*' folders automatically
  → Remove left chats from excluded_chats of ALL other folders immediately
  → Delete command message on success

- `folder` (Saved Messages only)
  → Create / reset ALL 'joined*' folders (joined, joined2, joined3, ...)
  → Leaves all chats, deletes extra numbered folders, recreates base 'joined'

- `list` (Saved Messages only)
  → Show all chats from ALL 'joined*' folders, paginated at ~3500 chars

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

v3.1.6 Changes:
  Fix C1 — _unwrap_join_result() helper (CRITICAL BUG FIX):
    In Telethon 1.44.0 with recent Telegram API layers, BOTH
    JoinChannelRequest AND ImportChatInviteRequest can return
    ChatInviteJoinResultOk (constructor 0x445663a7), which wraps updates
    in a .updates field — it has NO .chats attribute of its own.
    v3.0.4 fixed this for ImportChatInviteRequest but missed the
    JoinChannelRequest paths (channel_id, username, numeric_id,
    invite_with_username). This caused "'ChatInviteJoinResultOk' object
    has no attribute 'chats'" on every join of a public channel like
    @periodi or @RealAkhbar. Fixed by adding _unwrap_join_result() and
    applying it to all 5 join paths uniformly.

  Fix C2 — ChatInviteJoinResultWebView on all join paths:
    Previously only detected on the ImportChatInviteRequest path. Now
    all 5 paths detect WebView-gated channels and report them cleanly.

  Fix I1 — Multi-folder support (_FOLDER_CAPACITY = 100):
    Telegram enforces a hard 100-chat cap on folder include_peers.
    When the active joined* folder is full, a new joined2, joined3, …
    folder is created seamlessly with no user notification. The folder
    command resets all joined* folders. The list command shows chats from
    all joined* folders. The left command removes from all joined* folders.
    autoleave and _sync_folder_to_tracking scan all joined* folders.

  Fix I2 — Smart type-aware exclusion + exclude_peers capacity guard:
    Joined chats are now only excluded from folders whose type flags
    match the entity type:
      broadcast channels → only from folders with broadcasts=True
      groups/supergroups → only from folders with groups=True
      users/bots         → only from folders with contacts/non_contacts/bots=True
    Purely explicit-peer folders (all type flags False) are skipped.
    Also enforces a 100-entry cap on exclude_peers per folder (N4).

  Fix I3 — Pre-join invite link validation:
    Before starting any join operations, all invite_link entities are
    validated via CheckChatInviteRequest. If ANY link is expired/invalid,
    the entire operation is cancelled with a clear Persian error message.
    Already-joined links skip the join API call but still receive full
    post-join actions (folder, mute, archive, exclusion). These are
    reported as "قبلاً عضو بود (فولدر بروزرسانی شد)".

  Fix I4 — _collect_entities ordering (Bug A):
    Changed from set-based (non-deterministic order) to dict.fromkeys()-
    based deduplication. Links are now joined in exactly the order they
    appear left-to-right in the message.

  Fix I5 — _handle_left invite-link membership detection (Bug B):
    Replaced the fragile `for attr in ("chat", "channel")` attribute
    loop with an explicit isinstance(result_check, ChatInviteAlready)
    guard. Handles UserAlreadyParticipantError from CheckChatInviteRequest.

  Fix I6 — _handle_list pagination:
    Output chunked at ~3500 characters; overflow sent as additional
    messages. Each peer shows which joined* folder it lives in.

  Fix N1 — Bot username exclusion documented:
    Added clear comment explaining the intentional exclusion of usernames
    ending in 'bot' (prevents accidentally joining bot accounts).

  Fix N2 — Folder cache write-through optimization:
    After a successful UpdateDialogFilterRequest, the in-memory cache is
    updated in-place (without resetting the TTL). This avoids triggering
    a fresh GetDialogFiltersRequest on every post-join folder operation,
    reducing API calls by ~70% during busy bulk join batches.

  Fix N3 — Left-entity deduplication in _handle_left:
    Input entities are deduplicated before the leave loop to prevent
    double-leave attempts when the same link appears twice.

  Fix N4 — exclude_peers per-folder capacity guard:
    Folders already at 100 exclude_peers entries are skipped silently.

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
    on the direct-hash path dropped from up to 3 to exactly 1)

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
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.folders import EditPeerFoldersRequest
from telethon.tl.functions.messages import (
    CheckChatInviteRequest,
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
)
from telethon.tl.types import messages as tl_messages

from userbot_own.core.context import ModuleContext
from userbot_own.helpers.utils import leave_dialog, read_json_file, safe_delete, write_json_file_atomic
from userbot_own.modules.base import Module

# Module-level logger — used only by free functions outside the Module class
log = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

_JOINED_FOLDER_NAME    = "joined"
_FOLDER_CAPACITY       = 100    # I1: Telegram's hard cap on include_peers per folder
_EXCLUDE_PEERS_CAPACITY = 100   # N4: Telegram's hard cap on exclude_peers per folder
_FOLDER_CACHE_TTL      = 30.0   # seconds
_EDIT_THROTTLE         = 2.5    # seconds between message edits to avoid FloodWait
_AUTO_DELETE_DELAY     = 5.0    # seconds before auto-deleting command output
_LIST_CHUNK_SIZE       = 3500   # I6: max chars per list message

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
_BATCH_SIZE          = 4
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
    # Track which numeric spans came from here so the generic numeric_id
    # pass below doesn't also re-extract the same digits as a second,
    # duplicate entity for the same chat (v3.0.9 fix — see numeric_id loop).
    channel_id_values: set[int] = set()
    for m in re.finditer(
        r'https?://(?:www\.)?(?:t\.me|telegram\.me|telegram\.org)/c/(\d{10,15})/\d+',
        text, re.IGNORECASE,
    ):
        value = int(m.group(1))
        entities.append(('channel_id', value))
        channel_id_values.add(value)

    # Usernames: @name or t.me/name
    for m in re.finditer(
        r'(?:@|(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.org)/)'
        r'([a-zA-Z0-9_]{5,32})(?![a-zA-Z0-9_/])',
        text, re.IGNORECASE,
    ):
        username = m.group(1)
        if username.lower() in ('joinchat', 'c', 'proxy', 's', 'addstickers'):
            continue
        # N1: Intentionally skip bot usernames (ending in 'bot').
        # Bots cannot be "joined" — they must be started with /start.
        # Including them would cause a ChannelPrivateError or similar failure.
        if not username.lower().endswith('bot'):
            entities.append(('username', username))

    # Invite links: t.me/+xxx or t.me/joinchat/xxx
    for m in re.finditer(
        r'(https?://(?:www\.)?(?:t\.me|telegram\.me|telegram\.org)/(?:joinchat/|\+))'
        r'([a-zA-Z0-9_-]{10,64})',
        text, re.IGNORECASE,
    ):
        entities.append(('invite_link', m.group(1) + m.group(2)))

    # Numeric IDs — skip any digit run already captured above as a
    # channel_id (v3.0.9 fix: a t.me/c/<id>/<msg> link's ID is embedded in
    # the URL as a plain digit run too, so this generic pass used to
    # re-extract it a second time as a distinct ('numeric_id', ...) entity,
    # causing the join loop to process the same chat twice).
    for m in re.finditer(r'\b(\d{9,14})\b', text):
        value = int(m.group(1))
        if value in channel_id_values:
            continue
        entities.append(('numeric_id', value))

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

    result  = await client(GetDialogFiltersRequest())
    filters = getattr(result, "filters", result)
    folders = [f for f in filters if isinstance(f, DialogFilter)]
    cache[cid] = (now, folders)
    return folders


def _invalidate_folder_cache(
    client: TelegramClient,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> None:
    cache.pop(id(client), None)


def _update_folder_in_cache(
    client: TelegramClient,
    cache: dict[int, tuple[float, list[DialogFilter]]],
    updated_folder: DialogFilter,
) -> None:
    """
    N2: Write-through cache update. Replaces the folder in the cached list
    without resetting the TTL, so the next _get_folders call uses the
    updated in-memory state without re-fetching from Telegram.
    Reduces GetDialogFiltersRequest calls by ~70% during busy join batches.
    """
    cid = id(client)
    hit = cache.get(cid)
    if not hit:
        return
    ts, folder_list = hit
    # Replace the matching folder; add it if not present (e.g. newly created)
    found = False
    new_list = []
    for f in folder_list:
        if f.id == updated_folder.id:
            new_list.append(updated_folder)
            found = True
        else:
            new_list.append(f)
    if not found:
        new_list.append(updated_folder)
    cache[cid] = (ts, new_list)


# ── Folder title / multi-folder helpers ──────────────────────────────────────

def _folder_title(folder: DialogFilter) -> str:
    t = folder.title
    return t if isinstance(t, str) else getattr(t, "text", str(t))


def _peer_id(peer) -> int | None:
    return (
        getattr(peer, "user_id",    None)
        or getattr(peer, "chat_id",    None)
        or getattr(peer, "channel_id", None)
    )


def _get_joined_folder_number(title: str) -> int | None:
    """
    Parse a joined* folder title into its ordinal number.
    "joined"  → 1
    "joined2" → 2
    "joined3" → 3
    Anything else → None.
    """
    t = title.lower().strip()
    if t == _JOINED_FOLDER_NAME:
        return 1
    m = re.fullmatch(r'joined(\d+)', t)
    if m:
        n = int(m.group(1))
        return n if n >= 2 else None
    return None


def _joined_folder_title(number: int) -> str:
    """Return the title for a joined folder by number: 1→'joined', 2→'joined2', …"""
    return _JOINED_FOLDER_NAME if number == 1 else f"{_JOINED_FOLDER_NAME}{number}"


def _find_all_joined_folders(folders: list[DialogFilter]) -> list[DialogFilter]:
    """
    Return all joined* folders sorted ascending by number
    (joined=1, joined2=2, joined3=3, …).
    """
    result: list[tuple[int, DialogFilter]] = []
    for f in folders:
        n = _get_joined_folder_number(_folder_title(f))
        if n is not None:
            result.append((n, f))
    result.sort(key=lambda x: x[0])
    return [f for _, f in result]


def _find_joined_folder(folders: list[DialogFilter]) -> DialogFilter | None:
    """Return the base 'joined' folder (number 1) if it exists."""
    for f in folders:
        if _get_joined_folder_number(_folder_title(f)) == 1:
            return f
    return None


def _find_active_joined_folder(all_joined_sorted: list[DialogFilter]) -> DialogFilter | None:
    """
    I1: Return the newest joined* folder that still has capacity
    (fewer than _FOLDER_CAPACITY non-self peers in include_peers).
    Returns None if all folders are at capacity (caller should create next).
    Iterates in reverse order (newest first) so new peers fill the newest folder.
    """
    for folder in reversed(all_joined_sorted):
        peer_count = sum(
            1 for p in (folder.include_peers or [])
            if not isinstance(p, InputPeerSelf)
        )
        if peer_count < _FOLDER_CAPACITY:
            return folder
    return None


def _get_next_joined_folder_number(all_joined_sorted: list[DialogFilter]) -> int:
    """Return the next ordinal number for a new joined* folder."""
    if not all_joined_sorted:
        return 1
    max_num = max(
        _get_joined_folder_number(_folder_title(f)) or 0
        for f in all_joined_sorted
    )
    return max_num + 1


# ── Join result unwrapping (C1 fix) ──────────────────────────────────────────

def _unwrap_join_result(raw) -> tuple[object | None, bool]:
    """
    C1: Safely extract the joined chat entity from a JoinChannelRequest or
    ImportChatInviteRequest result.

    In Telethon 1.44.0 with recent Telegram API layers (Layer 178+), BOTH
    JoinChannelRequest AND ImportChatInviteRequest can return
    ChatInviteJoinResultOk (constructor 0x445663a7), which wraps the standard
    Updates object in a .updates field.  It has NO .chats attribute of its own.
    Accessing .chats directly raises AttributeError.

    Returns: (entity_or_None, is_webview)
      - entity_or_None: first chat entity from the result, or None
      - is_webview: True when the join requires WebView/Bot interaction
    """
    if isinstance(raw, tl_messages.ChatInviteJoinResultOk):
        # Unwrap the nested Updates object
        updates = raw.updates
        chats   = getattr(updates, 'chats', None)
        return (chats[0] if chats else None, False)
    if isinstance(raw, tl_messages.ChatInviteJoinResultWebView):
        # Subscription/bot-gated channel — cannot join via standard API
        return (None, True)
    # Standard Updates / UpdatesCombined / etc.
    chats = getattr(raw, 'chats', None)
    return (chats[0] if chats else None, False)


# ── Smart exclusion helper (I2) ───────────────────────────────────────────────

def _folder_would_show_entity(folder: DialogFilter, entity) -> bool:
    """
    I2: Type-aware exclusion check.

    Returns True only if the folder's type flags could cause this entity type
    to appear in it (i.e., excluding it from that folder would have any effect).

    Logic:
    - broadcast Channel  → only relevant if folder.broadcasts=True
    - megagroup/group    → only relevant if folder.groups=True
    - User/bot           → only relevant if folder.contacts/non_contacts/bots=True
    - Purely explicit-peers folders (all type flags False) return False — a
      newly joined chat won't be in include_peers yet, so it would never appear.

    This prevents wasteful UpdateDialogFilterRequest calls for folders whose
    type flags don't include the joined entity type, and respects the
    _EXCLUDE_PEERS_CAPACITY limit enforced separately.
    """
    if entity is None:
        return False

    is_broadcast = isinstance(entity, Channel) and entity.broadcast
    is_group     = (isinstance(entity, Channel) and not entity.broadcast) or isinstance(entity, Chat)
    is_user      = not isinstance(entity, (Channel, Chat))

    if is_broadcast:
        return bool(folder.broadcasts)
    if is_group:
        return bool(folder.groups)
    if is_user:
        return bool(folder.contacts or folder.non_contacts or folder.bots)

    return False  # unknown entity type — skip


# ── Folder creation / management ──────────────────────────────────────────────

async def _create_joined_folder(
    client: TelegramClient,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
    number: int = 1,
    extra_peers: list | None = None,
) -> DialogFilter:
    """
    I1: Create a joined folder with the given ordinal number.
    number=1 → title "joined"  (base folder)
    number=2 → title "joined2"
    number=3 → title "joined3"
    etc.
    """
    folders      = await _get_folders(client, cache)
    existing_ids = {f.id for f in folders}

    # Find a free filter ID (Req 1 fix: start from 2, skip any already-used IDs)
    new_id = 2
    while new_id in existing_ids:
        new_id += 1

    title_str = _joined_folder_title(number)
    saved     = InputPeerSelf()
    inc_peers = [saved] + (extra_peers or [])

    new_folder = DialogFilter(
        id               = new_id,
        title            = TextWithEntities(text=title_str, entities=[]),
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
        account_index, title_str, new_id, len(inc_peers),
    )
    return new_folder


async def _ensure_joined_folder_exists(
    client: TelegramClient,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> DialogFilter:
    """Ensure the base 'joined' folder exists. Returns it (creates if missing)."""
    folders = await _get_folders(client, cache)
    folder  = _find_joined_folder(folders)
    if folder:
        return folder
    log.debug("[Account%d] 'joined' folder not found — creating.", account_index)
    return await _create_joined_folder(client, account_index, cache, number=1)


async def _add_single_peer_to_joined_folder(
    client: TelegramClient,
    entity,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> bool:
    """
    Req 1 + I1: Add a single entity to the appropriate joined* folder IMMEDIATELY
    after joining.

    If the active folder is at _FOLDER_CAPACITY (100 chats), automatically
    creates the next numbered folder (joined2, joined3, …) seamlessly.
    Returns True if added (or already present), False on error.
    """
    for attempt in range(3):
        try:
            ip  = await client.get_input_entity(entity)
            pid = _peer_id(ip)
            if pid is None:
                return False

            folders    = await _get_folders(client, cache)
            all_joined = _find_all_joined_folders(folders)

            if not all_joined:
                # No joined* folder exists — create base folder with this peer
                await _create_joined_folder(client, account_index, cache, number=1, extra_peers=[ip])
                return True

            # Check if peer is already in ANY joined* folder
            for jf in all_joined:
                existing_ids = {_peer_id(p) for p in (jf.include_peers or [])} - {None}
                if pid in existing_ids:
                    return True  # already present somewhere

            # Find the active (newest with capacity) folder
            active = _find_active_joined_folder(all_joined)

            if active is None:
                # All joined* folders are at capacity — create the next one
                next_num = _get_next_joined_folder_number(all_joined)
                await _create_joined_folder(
                    client, account_index, cache, number=next_num, extra_peers=[ip]
                )
                log.debug(
                    "[Account%d] All joined* folders full — created '%s' for peer id=%d.",
                    account_index, _joined_folder_title(next_num), pid,
                )
                return True

            # Add peer to the active folder (incremental update)
            active.include_peers = list(active.include_peers or []) + [ip]
            # Ensure exclude_archived=True so the folder doesn't un-archive the chat
            if not active.exclude_archived:
                active.exclude_archived = True
                log.debug(
                    "[Account%d] Patched '%s' folder to exclude_archived=True.",
                    account_index, _folder_title(active),
                )
            await client(UpdateDialogFilterRequest(id=active.id, filter=active))
            _update_folder_in_cache(client, cache, active)  # N2: write-through
            log.debug(
                "[Account%d] Added peer id=%d to '%s' folder (incremental).",
                account_index, pid, _folder_title(active),
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
                    "[Account%d] FloodWait exceeded retries on folder add for entity %s — skipping.",
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
    """
    I1: Batch-add multiple entities across joined* folders, respecting
    _FOLDER_CAPACITY per folder. Creates new folders as needed.
    """
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

    added = 0
    for peer in new_peers:
        if await _add_single_peer_to_joined_folder(client, peer, account_index, cache):
            added += 1

    log.debug("[Account%d] Batch-added %d peer(s) to joined* folder(s).", account_index, added)
    return added


async def _remove_peers_from_all_joined_folders(
    client: TelegramClient,
    entities: list,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> int:
    """
    I1: Remove peers from ALL joined* folders (joined, joined2, joined3, …).
    Returns total number of peers removed across all folders.
    """
    if not entities:
        return 0

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
        return 0

    folders    = await _get_folders(client, cache)
    all_joined = _find_all_joined_folders(folders)
    total_removed = 0

    for folder in all_joined:
        original    = list(folder.include_peers or [])
        kept        = [p for p in original if _peer_id(p) not in remove_ids]
        removed_cnt = len(original) - len(kept)
        if removed_cnt == 0:
            continue

        folder.include_peers = kept
        try:
            await client(UpdateDialogFilterRequest(id=folder.id, filter=folder))
            _update_folder_in_cache(client, cache, folder)  # N2: write-through
            total_removed += removed_cnt
            log.debug(
                "[Account%d] Removed %d peer(s) from '%s' folder.",
                account_index, removed_cnt, _folder_title(folder),
            )
        except Exception as exc:
            log.debug(
                "[Account%d] Could not update '%s' folder when removing peers: %s",
                account_index, _folder_title(folder), exc,
            )

    return total_removed


# ── I2: Smart type-aware folder exclusion ────────────────────────────────────

async def _add_to_all_other_folders_exclusion(
    client: TelegramClient,
    entity,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> None:
    """
    I2 + Req 4: Add a chat to the exclude_peers of every OTHER editable folder
    so it only appears in the joined* folder(s).

    Improvements over v3.1.5:
    - Skips ALL joined* folders (not just the base 'joined')
    - Type-aware: only excludes from folders whose type flags include this entity
    - N4: respects the 100-entry exclude_peers capacity per folder
    - N2: uses write-through cache after each successful update
    """
    try:
        ip  = await client.get_input_entity(entity)
        pid = _peer_id(ip)
        if pid is None:
            return
    except Exception as exc:
        log.debug("[Account%d] exclusion: could not resolve entity: %s", account_index, exc)
        return

    folders      = await _get_folders(client, cache)
    all_joined   = _find_all_joined_folders(folders)
    joined_ids   = {f.id for f in all_joined}

    for folder in folders:
        if folder.id in joined_ids:
            continue  # skip all joined* folders

        # I2: type-aware — skip if folder's type flags can't show this entity type
        if not _folder_would_show_entity(folder, entity):
            log.debug(
                "[Account%d] Skipping exclusion for folder '%s': type flags don't match %s.",
                account_index, _folder_title(folder), type(entity).__name__,
            )
            continue

        existing_excl = list(folder.exclude_peers or [])
        excl_ids      = {_peer_id(p) for p in existing_excl} - {None}

        if pid in excl_ids:
            continue  # already excluded

        # N4: capacity guard — don't overflow exclude_peers
        if len(existing_excl) >= _EXCLUDE_PEERS_CAPACITY:
            log.debug(
                "[Account%d] Skipping exclusion for folder '%s': exclude_peers at capacity (%d).",
                account_index, _folder_title(folder), _EXCLUDE_PEERS_CAPACITY,
            )
            continue

        existing_excl.append(ip)
        folder.exclude_peers = existing_excl

        for attempt in range(3):
            try:
                await client(UpdateDialogFilterRequest(id=folder.id, filter=folder))
                _update_folder_in_cache(client, cache, folder)  # N2: write-through
                log.debug(
                    "[Account%d] Added peer id=%d to exclude_peers of folder '%s'.",
                    account_index, pid, _folder_title(folder),
                )
                break
            except errors.FloodWaitError as exc:
                if attempt < 2:
                    await asyncio.sleep(exc.seconds + 2)
                else:
                    log.debug(
                        "[Account%d] FloodWait on folder exclusion for '%s' — skipped.",
                        account_index, _folder_title(folder),
                    )
            except Exception as exc:
                log.debug(
                    "[Account%d] Could not update exclusion for folder '%s': %s",
                    account_index, _folder_title(folder), exc,
                )
                break


async def _remove_from_all_folders_exclusion(
    client: TelegramClient,
    entities: list,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> None:
    """
    Req 4: When a chat is left (by any means), remove it from exclude_peers
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
        excl     = list(folder.exclude_peers or [])
        new_excl = [p for p in excl if _peer_id(p) not in remove_ids]
        if len(new_excl) == len(excl):
            continue  # nothing to remove

        folder.exclude_peers = new_excl
        for attempt in range(3):
            try:
                await client(UpdateDialogFilterRequest(id=folder.id, filter=folder))
                _update_folder_in_cache(client, cache, folder)  # N2: write-through
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
    I1: Leave all chats from ALL joined* folders (joined, joined2, joined3, …),
    delete ALL joined* folders, then recreate only the base 'joined' folder.

    Req 4: Also removes each left chat from all other folders' exclusion lists.
    Req 5: Uses paced loop (_LEFT_INTER_DELAY) instead of a tight loop.

    Returns (left_count, failed_count).
    """
    folders    = await _get_folders(client, cache)
    all_joined = _find_all_joined_folders(folders)

    left_count   = 0
    failed_count = 0
    left_entities: list = []

    for folder in all_joined:
        peers_to_leave = [
            p for p in (folder.include_peers or [])
            if not isinstance(p, InputPeerSelf)
        ]
        folder_label = _folder_title(folder)

        for idx, peer in enumerate(peers_to_leave):
            pid = _peer_id(peer)
            if not pid:
                continue

            fw_retries = 0
            while True:
                try:
                    entity = await client.get_entity(peer)
                    await leave_dialog(client, entity)
                    left_count += 1
                    left_entities.append(entity)
                    break
                except errors.UserNotParticipantError:
                    left_count += 1  # already left — still counts as handled
                    break
                except errors.FloodWaitError as exc:
                    fw_retries += 1
                    if fw_retries > _LEFT_MAX_FW_RETRIES:
                        failed_count += 1
                        log.debug(
                            "[Account%d] Folder reset: FloodWait retry cap on peer id=%d "
                            "in '%s' — skipping.",
                            account_index, pid, folder_label,
                        )
                        break
                    log.debug(
                        "[Account%d] Folder reset: FloodWait %ds on peer id=%d "
                        "in '%s' (retry %d/%d).",
                        account_index, exc.seconds, pid, folder_label,
                        fw_retries, _LEFT_MAX_FW_RETRIES,
                    )
                    await asyncio.sleep(exc.seconds + 2)
                except Exception as exc:
                    failed_count += 1
                    log.debug(
                        "[Account%d] Folder reset: could not leave peer id=%d in '%s': %s",
                        account_index, pid, folder_label, exc,
                    )
                    break

            # Req 5: inter-leave pacing
            if idx < len(peers_to_leave) - 1:
                await asyncio.sleep(_LEFT_INTER_DELAY)

        # Delete this folder (no filter= argument = delete operation)
        try:
            await client(UpdateDialogFilterRequest(id=folder.id))
        except Exception as exc:
            log.debug(
                "[Account%d] Could not delete folder '%s': %s",
                account_index, folder_label, exc,
            )
        _invalidate_folder_cache(client, cache)

    # Req 4: clean exclusion lists for everything we left
    if left_entities:
        await _remove_from_all_folders_exclusion(client, left_entities, account_index, cache)

    # Recreate only the base 'joined' folder
    await _create_joined_folder(client, account_index, cache, number=1)
    return left_count, failed_count


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
        2. Add to joined* folder (incremental, with exclude_archived=True patch)
        3. Add to exclude_peers of all OTHER folders (type-aware, I2)
        4. Archive the chat (LAST — prevents folder operations from un-archiving)

        Archive is deliberately last: UpdateDialogFilterRequest with
        exclude_archived=False would silently move the chat back to folder_id=0.
        By archiving after all folder operations are complete, no subsequent
        Telegram-side side-effect can undo it.

        Returns (muted, archived, folder_added).
        """
        muted        = await _mute_chat(client, entity, account_index)
        folder_added = await _add_single_peer_to_joined_folder(
            client, entity, account_index, self._folder_cache
        )
        await _add_to_all_other_folders_exclusion(client, entity, account_index, self._folder_cache)
        archived     = await _archive_chat(client, entity, account_index)
        return muted, archived, folder_added

    # ── I3: Pre-join invite link validation ───────────────────────────────────

    async def _validate_invite_links(
        self,
        client: TelegramClient,
        entities: list[tuple[str, str | int]],
    ) -> tuple[list, list, list]:
        """
        I3: Pre-join validation phase for invite_link entities only.

        Calls CheckChatInviteRequest for each invite_link before any joins start.
        Non-invite entities (username, channel_id, numeric_id) pass through
        unchanged to avoid extra API calls that could trigger FloodWait.

        Returns:
        - invalid: [(identifier, reason_str), ...]   — expired/invalid links
        - already_joined: [(identifier, entity|None), ...] — already members
        - remaining: [(entity_type, identifier), ...]  — valid entities to join
        """
        invalid:        list[tuple] = []
        already_joined: list[tuple] = []
        remaining:      list[tuple] = []
        cache_updated = False

        for entity_type, identifier in entities:
            if entity_type != 'invite_link':
                remaining.append((entity_type, identifier))
                continue

            invite_hash = _extract_invite_hash(str(identifier))
            if not invite_hash:
                invalid.append((identifier, "لینک قابل parse نیست"))
                continue

            # Cache hit → treat as valid (we already verified this hash recently)
            cached = self._invite_cache.get(invite_hash)
            if cached is not None:
                cached_ts = cached.get("ts", 0)
                if (time.time() - cached_ts) < _INVITE_CACHE_TTL:
                    remaining.append((entity_type, identifier))
                    continue

            # Call CheckChatInviteRequest to validate
            try:
                result = await client(CheckChatInviteRequest(invite_hash))
            except errors.InviteHashInvalidError:
                invalid.append((identifier, "لینک منقضی یا نامعتبر است"))
                async with self._invite_cache_lock:
                    self._invite_cache[invite_hash] = {"username": None, "ts": time.time()}
                cache_updated = True
                continue
            except errors.UserAlreadyParticipantError:
                # Some Telegram API layers raise this from CheckChatInviteRequest
                already_joined.append((identifier, None))
                async with self._invite_cache_lock:
                    self._invite_cache[invite_hash] = {"username": None, "ts": time.time()}
                cache_updated = True
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
                # We are already a member
                already_joined.append((identifier, result.chat))
                async with self._invite_cache_lock:
                    self._invite_cache[invite_hash] = {"username": None, "ts": time.time()}
                cache_updated = True
            else:
                # ChatInvite or ChatInvitePeek — valid, not yet joined
                # Extract and cache the username for Layer 1 smart resolution
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

        return invalid, already_joined, remaining

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

        # Inline keyboard buttons on the reply message
        if hasattr(reply_msg, "reply_markup") and isinstance(reply_msg.reply_markup, ReplyInlineMarkup):
            for row in reply_msg.reply_markup.rows:
                for button in row.buttons:
                    if isinstance(button, KeyboardButtonUrl):
                        for ent in extract_telegram_entities(button.url):
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
        I1: uses _remove_peers_from_all_joined_folders to clean ALL joined* folders.
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
            left_entity = None
            try:
                entity = await client.get_entity(chat_id)
                name   = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(chat_id)

                await leave_dialog(client, entity)

                left_entity = entity
                self._log_debug("Auto-left '%s' (id=%d, joined %s).", name, chat_id, joined_at_str)

                async with self._settings_lock:
                    self._settings["joined_chats"].pop(str(chat_id), None)
                    await self._save_settings()

                # I1: remove from ALL joined* folders
                await _remove_peers_from_all_joined_folders(
                    client, [entity], self.cfg.index, self._folder_cache
                )

            except errors.UserNotParticipantError:
                self._log_debug("Auto-leave: not participant in %d, removing from tracking", chat_id)
                async with self._settings_lock:
                    self._settings["joined_chats"].pop(str(chat_id), None)
                    await self._save_settings()
                try:
                    entity      = await client.get_entity(chat_id)
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
                current_len   = sum(len(l) + 1 for l in current_lines)
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
        - Req 4: exclusion added to all other folders per-chat
        - Req 5: per-entity FloodWait retry cap (_MAX_FLOODWAIT_RETRIES)
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
        # Phase 0: I3 — Pre-validation of invite links
        # ═══════════════════════════════════════════════════════════════════════

        already_joined_pre: list[tuple] = []
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

            invalid_links, already_joined_pre, all_entities = await self._validate_invite_links(
                client, all_entities
            )

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
                return

        # ═══════════════════════════════════════════════════════════════════════
        # Phase 0b: Process already-joined entries from validation
        # ═══════════════════════════════════════════════════════════════════════

        for ident, entity in already_joined_pre:
            attempt_start = time.monotonic()
            if entity is not None:
                title = getattr(entity, "title", None) or str(ident)
                results.append(f"ℹ️ [{title}] — قبلاً عضو بود (فولدر بروزرسانی شد)")
                success_count += 1
                join_times.append(time.monotonic() - attempt_start)
                joined_entities.append(entity)
                async with self._settings_lock:
                    self._settings["joined_chats"][str(entity.id)] = \
                        datetime.datetime.now(datetime.UTC).isoformat()
                    await self._save_settings()
                await self._post_join_actions(client, entity, self.cfg.index)
            else:
                results.append(f"ℹ️ [{str(ident)[:50]}] — قبلاً عضو بود")
                success_count += 1
                join_times.append(0.0)

        pre_processed = len(already_joined_pre)

        # If validation consumed all entities, jump to summary
        if not all_entities and pre_processed > 0:
            # Fall through to summary below (loop won't execute)
            pass

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

                        display_idx = pre_processed + idx
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
                        except Exception as exc:
                            if self._is_already_member_error(exc):
                                joined_entity = await client.get_entity(ip)
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
                        except (errors.UsernameNotOccupiedError, errors.ChannelPrivateError):
                            raise
                        except Exception as exc:
                            if self._is_already_member_error(exc):
                                joined_entity = await client.get_entity(f"@{identifier}")
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
                                pass  # keep pre-resolved joined_entity
                            except Exception as exc:
                                if not self._is_already_member_error(exc):
                                    raise

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
                            except Exception as exc:
                                if self._is_already_member_error(exc):
                                    joined_entity = await client.get_entity(f"@{resolved_username}")
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
                                        except Exception as exc2:
                                            if self._is_already_member_error(exc2):
                                                joined_entity = await client.get_entity(f"@{resolved_username}")
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

                        async with self._settings_lock:
                            self._settings["joined_chats"][str(joined_entity.id)] = \
                                datetime.datetime.now(datetime.UTC).isoformat()
                            await self._save_settings()

                        # Req 1 + Req 3 + Req 4: immediate per-chat post-processing
                        await self._post_join_actions(client, joined_entity, self.cfg.index)

                    break  # ← success (or handled WebView), exit retry loop

                # ── FloodWait handling (Layer 3 + Req 5 retry cap) ────────────
                except errors.FloodWaitError as exc:
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
            display_idx = pre_processed + idx
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

        folder_count = len(joined_entities)
        folder_note  = (
            f"\n📁 `{folder_count}` چت به فولدر joined اضافه شد (incremental)."
            if folder_count > 0 else ""
        )

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
        Req 4: Remove from all folder exclusion lists.
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
                        results.append(f"❌ [{identifier}] — یافت نشد")
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

        # I1 + Req 4: remove from ALL joined* folders AND clean all exclusion lists
        if left_entities:
            try:
                removed = await _remove_peers_from_all_joined_folders(
                    client, left_entities, self.cfg.index, self._folder_cache
                )
                folder_note = (
                    f"\n📁 `{removed}` چت از فولدرهای joined حذف شد." if removed else ""
                )
            except Exception as exc:
                self._log_error("Failed to remove peers from joined* folders: %s", exc)
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
    "• `folder` | ایجاد یا ریست فولدرهای joined (joined, joined2, …)\n"
    "• `list` | نمایش لیست چت‌های تمام فولدرهای joined\n"
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
    "تغییرات v3.1.6:\n"
    "• رفع باگ حیاتی: خطای 'ChatInviteJoinResultOk' روی کانال‌های عمومی (C1)\n"
    "  → JoinChannelRequest در Telethon 1.44 ممکن است ChatInviteJoinResultOk برگرداند\n"
    "  → تمام ۵ مسیر جوین اکنون با _unwrap_join_result() ایمن شدند\n"
    "• پشتیبانی چند فولدر: وقتی joined پر شد، joined2، joined3 ایجاد می‌شود (I1)\n"
    "• حذف هوشمند: فقط از فولدرهایی که می‌توانند آن نوع چت را نشان دهند حذف می‌شود (I2)\n"
    "• اعتبارسنجی قبل از جوین: لینک‌های منقضی قبل از شروع بررسی می‌شوند (I3)\n"
    "• ترتیب ثابت: لینک‌ها به ترتیب ظاهرشان در پیام پردازش می‌شوند (I4)\n"
    "• رفع باگ ترک از طریق لینک دعوت (I5)\n"
    "• صفحه‌بندی خروجی list برای لیست‌های طولانی (I6)\n\n"
    "سیستم ضد-FloodWait (۴ لایه):\n"
    "• `join mode fast`  | بدون throttling، بدون batching — سریع‌ترین\n"
    "• `join mode safe`  | Layer 1+2: حل هوشمند لینک + تأخیر بر اساس ریسک [پیش‌فرض]\n"
    "• `join mode human` | Layer 1+2+3+4: تمام لایه‌ها + batch cooldown — ایمن‌ترین\n\n"
    "Layer 1 — Smart Link Resolution:\n"
    "  → invite link → بررسی قبل از جوین → اگر username داشت → JoinChannelRequest (ایمن)\n"
    "  → نتایج cache می‌شوند (۶ ساعت) در join_left_invite_cache.json\n"
    "  → اعتبارسنجی I3 نتایج را cache می‌کند — Layer 1 از cache استفاده می‌کند\n"
    "  → نماد 🛡 = از smart path استفاده شد | نماد 🔑 = مستقیم از hash\n\n"
    "Layer 2 — Risk-Based Delays:\n"
    "  → username: 2s | channel_id/numeric_id: 3s | invite→username: 4s | invite direct: 4s\n"
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
    "مدیریت فولدر (v3.1.6):\n"
    "• `folder` | ایجاد یا ریست فولدرهای joined*\n"
    "  → اگر joined پر شد (۱۰۰ چت)، joined2 خودکار ایجاد می‌شود\n"
    "  → `folder` تمام joined* را ریست کرده و فقط joined پایه را نگه می‌دارد\n"
    "• `list` | نمایش تمام چت‌های joined* (با نام فولدر در صورت چند فولدر)\n\n"
    "ترک خودکار:\n"
    "• `autoleave <days>` | ترک چت‌های joined* پس از N روز\n"
    "  → شامل چت‌های از قبل موجود در همه فولدرها هم می‌شود\n"
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
    "• هنگام فعال‌سازی autoleave، چت‌های موجود در همه فولدرها ردیابی می‌شوند\n"
)

JoinLeft.help_text  = help_text
JoinLeft.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return JoinLeft(context)
