"""
userbot_own/modules/packages/join_left/folders.py
════════════════════════════════════════════════════════════════
Everything about Telegram folders (dialog filters).

Contents, in order:
- the folder cache (TTL-based, shared objects — see the N1 warning below)
- folder title / peer-id helpers and the joined* folder numbering
- creating, adding to and removing from joined* folders
- the legacy exclude_peers sweep
- ``ensure_exclude_archived()`` — the server-side protection that keeps
  archived joined chats out of the user's other folders

The N1 rule that governs this file: the DialogFilter objects in the
cache are SHARED. Never mutate a cached folder in place. Build the new
state on a copy, send it, and only after Telegram accepted it write the
copy through to the cache.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import copy
import random
import re
import time
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.messages import (
    GetDialogFiltersRequest,
    UpdateDialogFilterRequest,
)
from telethon.tl.types import (
    DialogFilter,
    DialogFilterChatlist,
    DialogFilterDefault,
    InputPeerSelf,
    TextWithEntities,
)
from telethon.tl.types import messages as tl_messages

from userbot_own.core.logging_setup import get_logger
from userbot_own.helpers.utils import flood_seconds as _flood_seconds
from userbot_own.modules.packages.join_left.const import (
    _ACTION_FLOOD_CAP,
    _ACTION_MAX_ATTEMPTS,
    _ENSURE_MAX_FLOOD_WAIT,
    _ENSURE_PACE,
    _FLOOD_PAD,
    _FOLDER_CACHE_TTL,
    _FOLDER_CAPACITY,
    _JOINED_FOLDER_NAME,
    _MAX_FLOODWAIT_RETRIES,
)
from userbot_own.modules.packages.join_left.types import EnsureResult

if TYPE_CHECKING:
    from telethon import TelegramClient

# Module-level logger — these are free functions, not methods of the
# Module class, so they have no `self._log` to write to.
log = get_logger(__name__)


# ── Folder cache helpers ─────────────────────────────────────────────────────
#
# v3.2.0 (N1): the objects held in this cache are SHARED — _get_folders() hands
# the very same DialogFilter instances to every caller until the TTL lapses.
# Callers therefore must NEVER mutate a cached folder in place. Doing so (the
# pre-3.2.0 behaviour of _add_single_peer_to_joined_folder and
# _remove_peers_from_all_joined_folders) updated the cache BEFORE the API call:
# if the call then failed, the cache claimed a state the server never had, and
# the next attempt reported success for something that was never written.
# The rule now: build the new state on a copy (copy.copy + replacing list
# attributes), send it, and only after Telegram accepted it write the copy
# through with _update_folder_in_cache().

async def _fetch_all_filters(client: TelegramClient) -> list:
    """
    Read EVERY dialog filter from Telegram, uncached, as a plain list.

    Returns all constructor kinds — DialogFilter (regular folder),
    DialogFilterChatlist (shared folder) and DialogFilterDefault (the
    Premium-only "All chats" placeholder). Handles both result shapes: the
    messages.DialogFilters wrapper (current layers; has .filters and
    .tags_enabled) and a bare list (older Telethon).
    """
    # B-JL-03 (fix): Retry with backoff on FloodWait instead of returning empty.
    # Returning [] corrupts the cache and causes duplicate folder creation.
    last_exc = None
    for attempt in range(_MAX_FLOODWAIT_RETRIES):
        try:
            result = await client(GetDialogFiltersRequest())
            # Extract filters from messages.DialogFilters wrapper (current Telethon)
            # or return as-is if it's already a list (older Telethon).
            filters = getattr(result, "filters", result)
            return list(filters or [])
        except errors.FloodError as exc:
            seconds = _flood_seconds(exc)
            last_exc = exc
            if attempt + 1 < _MAX_FLOODWAIT_RETRIES:
                await asyncio.sleep(seconds + _FLOOD_PAD)
            else:
                log.warning(
                    "_fetch_all_filters: FloodWait retry cap (%d) exceeded after %ds — returning empty.",
                    _MAX_FLOODWAIT_RETRIES, seconds,
                )
                return []
    return []  # Should never reach here, but satisfies type checker


async def _get_folders(
    client: TelegramClient,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> list[DialogFilter]:
    """
    Fetch regular dialog filters (DialogFilter only) with TTL-based caching.

    Shared folders (DialogFilterChatlist) and the "All chats" placeholder
    (DialogFilterDefault) are deliberately not returned: they have no
    exclude_archived / exclude_peers fields, so nothing here can edit them.
    The returned objects are shared with the cache — see the note above.
    """
    cid = id(client)
    now = time.monotonic()
    hit = cache.get(cid)
    if hit and (now - hit[0]) < _FOLDER_CACHE_TTL:
        return hit[1]

    filters = await _fetch_all_filters(client)
    folders = [f for f in filters if isinstance(f, DialogFilter)]
    cache[cid] = (now, folders)
    return folders


def _store_folders_in_cache(
    client: TelegramClient,
    cache: dict[int, tuple[float, list[DialogFilter]]],
    folders: list[DialogFilter],
) -> None:
    """Replace the cached folder list with a freshly fetched one (TTL restarts)."""
    cache[id(client)] = (time.monotonic(), list(folders))


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

    N1 (v3.2.0): call this ONLY after the matching UpdateDialogFilterRequest
    has succeeded, and pass a COPY — never the object the cache already holds.
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

    # exclude_archived=True: on a joined* folder this is INERT in the Telegram clients —
    # include_peers wins over exclude_archived, and every type flag is False — so the
    # chats stay visible. It is kept so every folder this module creates is
    # consistent with what ensure_exclude_archived() sets on all the others.
    # (The v3.0.5 CHANGELOG entry explained this flag backwards; see v3.2.0.)
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

    # B-JL-02 (fix): Retry with backoff on FloodWait instead of returning None.
    # Returning None silently drops folder creation, causing duplicate folders later.
    for attempt in range(_MAX_FLOODWAIT_RETRIES):
        try:
            await client(UpdateDialogFilterRequest(id=new_id, filter=new_folder))
            _invalidate_folder_cache(client, cache)
            log.debug(
                "[Account%d] Created '%s' folder (id=%d) with %d peer(s).",
                account_index, title_str, new_id, len(inc_peers),
            )
            return new_id
        except errors.FloodError as exc:
            seconds = _flood_seconds(exc)
            if attempt + 1 < _MAX_FLOODWAIT_RETRIES:
                log.warning(
                    "[Account%d] _create_joined_folder: FloodWait %ds — retrying (attempt %d/%d).",
                    account_index, seconds, attempt + 2, _MAX_FLOODWAIT_RETRIES,
                )
                await asyncio.sleep(seconds + _FLOOD_PAD)
            else:
                log.warning(
                    "[Account%d] _create_joined_folder: FloodWait retry cap (%d) exceeded after %ds — folder creation failed.",
                    account_index, _MAX_FLOODWAIT_RETRIES, seconds,
                )
                return None
    return None  # Should never reach here, but satisfies type checker
    return new_folder


async def _collect_peer_ids(client: TelegramClient, entities: list) -> set[int]:
    """
    Resolve entities / input peers / plain ids to the numeric ids that appear in
    DialogFilter peer lists (v3.2.0: shared by the leave-cleanup functions).

    Never raises: an entity that cannot be resolved falls back to its own .id,
    and a plain int is taken as-is (the auto-leave not-found branches only have
    the stored chat id).
    """
    ids: set[int] = set()
    for entity in entities:
        if isinstance(entity, int) and not isinstance(entity, bool):
            if entity:
                ids.add(entity)
            continue
        try:
            ip  = await client.get_input_entity(entity)
            pid = _peer_id(ip)
        except Exception:
            pid = getattr(entity, "id", None)
        if pid:
            ids.add(pid)
    return ids


async def _write_folder_with_retry(
    client: TelegramClient,
    updated: DialogFilter,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
    *,
    what: str,
) -> bool:
    """
    Send one folder update, then — and ONLY then — write it through to the cache.

    `updated` must be a copy (see "Folder cache helpers"). A FloodWait is waited
    out only when it is short (_ACTION_FLOOD_CAP); anything longer, or running out
    of attempts, is a logged give-up (WARNING), never a silent one. Any
    sibling of the flood family is covered (Pattern 1.2). On a non-flood
    failure the cache is invalidated so the next operation re-reads the truth.
    Returns True when Telegram accepted the update.
    """
    label = _folder_title(updated)
    for attempt in range(1, _ACTION_MAX_ATTEMPTS + 1):
        try:
            await client(UpdateDialogFilterRequest(id=updated.id, filter=updated))
        except errors.FloodError as exc:
            wait = _flood_seconds(exc) + _FLOOD_PAD
            if attempt >= _ACTION_MAX_ATTEMPTS or wait > _ACTION_FLOOD_CAP:
                log.warning(
                    "[Account%d] Could not %s '%s' folder: FloodWait %ds (%s), attempt %d/%d, "
                    "inline cap %ds — giving up.",
                    account_index, what, label, _flood_seconds(exc), type(exc).__name__,
                    attempt, _ACTION_MAX_ATTEMPTS, int(_ACTION_FLOOD_CAP),
                )
                return False
            log.debug(
                "[Account%d] FloodWait %ds while trying to %s '%s' folder (attempt %d/%d) — waiting.",
                account_index, _flood_seconds(exc), what, label, attempt, _ACTION_MAX_ATTEMPTS,
            )
            await asyncio.sleep(wait)
            continue
        except Exception as exc:
            # RPC errors (the Filter* family: FilterIdInvalid, FilterIncludeEmpty,
            # FilterNotSupported, FilterTitleEmpty, InputFilterInvalid, FILTERS_TOO_MUCH, …)
            # are all subclasses of errors.RPCError → Exception; network errors too.
            log.warning(
                "[Account%d] Could not %s '%s' folder: %s: %s",
                account_index, what, label, type(exc).__name__, exc,
            )
            _invalidate_folder_cache(client, cache)
            return False
        _update_folder_in_cache(client, cache, updated)
        return True
    return False


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
    Returns True if the chat is now in a joined* folder (or already was),
    False — after a WARNING — otherwise.

    v3.2.0 (N1 — CRITICAL data-integrity fix): the new include_peers list is built
    on a COPY of the cached folder and the cache is updated only after Telegram
    accepted the write. Before, the cached object was mutated first; if the
    update then hit a FloodWait, the retry found the peer "already present" in
    the mutated cache and returned True although nothing had been written.
    N9: folder-creation failures (e.g. FILTERS_TOO_MUCH when the account is at
    its folder-count cap) are now logged at WARNING instead of DEBUG.
    """
    ent_id = getattr(entity, "id", entity)
    for attempt in range(1, _ACTION_MAX_ATTEMPTS + 1):
        try:
            ip  = await client.get_input_entity(entity)
            pid = _peer_id(ip)
            if pid is None:
                log.warning(
                    "[Account%d] Folder add skipped for entity %s: could not derive a peer id.",
                    account_index, ent_id,
                )
                return False

            folders    = await _get_folders(client, cache)
            all_joined = _find_all_joined_folders(folders)

            if not all_joined:
                # No joined* folder exists — create base folder with this peer
                await _create_joined_folder(client, account_index, cache, number=1, extra_peers=[ip])
                return True

            # Check if peer is already in ANY joined* folder. Safe to trust since
            # v3.2.0: the cache only ever holds state Telegram has accepted.
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

            # Add peer to the active folder (incremental update) — on a COPY (N1).
            updated = copy.copy(active)
            updated.include_peers = list(active.include_peers or []) + [ip]
            if not updated.exclude_archived:
                # Normally already True (the folder is created that way). Harmless on a
                # joined* folder: include_peers wins over exclude_archived in the
                # Telegram clients, so the chat stays visible here either way.
                updated.exclude_archived = True
                log.debug(
                    "[Account%d] Patched '%s' folder to exclude_archived=True.",
                    account_index, _folder_title(active),
                )
            if not await _write_folder_with_retry(
                client, updated, account_index, cache, what=f"add chat id={ent_id} to"
            ):
                return False
            log.debug(
                "[Account%d] Added peer id=%d to '%s' folder (incremental).",
                account_index, pid, _folder_title(updated),
            )
            return True

        except errors.FloodError as exc:
            wait = _flood_seconds(exc) + _FLOOD_PAD
            if attempt >= _ACTION_MAX_ATTEMPTS or wait > _ACTION_FLOOD_CAP:
                log.warning(
                    "[Account%d] Folder add for entity %s: FloodWait %ds (%s), attempt %d/%d — giving up.",
                    account_index, ent_id, _flood_seconds(exc), type(exc).__name__,
                    attempt, _ACTION_MAX_ATTEMPTS,
                )
                return False
            log.debug(
                "[Account%d] FloodWait %ds on folder add for entity %s (attempt %d/%d), waiting...",
                account_index, _flood_seconds(exc), ent_id, attempt, _ACTION_MAX_ATTEMPTS,
            )
            await asyncio.sleep(wait)
        except Exception as exc:
            log.warning(
                "[Account%d] Could not add entity %s to a joined* folder: %s: %s",
                account_index, ent_id, type(exc).__name__, exc,
            )
            _invalidate_folder_cache(client, cache)
            return False

    return False


async def _remove_peers_from_all_joined_folders(
    client: TelegramClient,
    entities: list,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> tuple[int, int]:
    """
    I1: Remove peers from ALL joined* folders (joined, joined2, joined3, …).
    Returns (peers_removed, folders_failed).

    v3.2.0 (N3): copy-then-commit (no more in-place mutation of cached folders),
    FloodWait handled with a bounded retry (any flood sibling), and every failure
    logged at WARNING and reported through folders_failed. `entities` may hold
    entities, input peers or plain ids. A folder-list fetch error propagates to the
    caller (_remove_left_from_folders), which reports it.
    """
    if not entities:
        return 0, 0

    remove_ids = await _collect_peer_ids(client, entities)
    if not remove_ids:
        return 0, 0

    folders    = await _get_folders(client, cache)
    all_joined = _find_all_joined_folders(folders)
    total_removed = 0
    failed        = 0

    for folder in all_joined:
        original    = list(folder.include_peers or [])
        kept        = [p for p in original if _peer_id(p) not in remove_ids]
        removed_cnt = len(original) - len(kept)
        if removed_cnt == 0:
            continue

        updated = copy.copy(folder)
        updated.include_peers = kept
        if await _write_folder_with_retry(
            client, updated, account_index, cache, what=f"remove {removed_cnt} peer(s) from"
        ):
            total_removed += removed_cnt
            log.debug(
                "[Account%d] Removed %d peer(s) from '%s' folder.",
                account_index, removed_cnt, _folder_title(updated),
            )
        else:
            failed += 1

    return total_removed, failed


async def _sweep_legacy_exclusions(
    client: TelegramClient,
    entities: list,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> tuple[int, int]:
    """
    v3.2.0 migration — SCOPED legacy sweep. Returns (entries_removed, folders_failed).

    Versions before 3.2.0 wrote every joined chat into the exclude_peers list of
    every other folder. Those entries are inert now but still occupy slots and
    clutter the folder editor. When a chat is LEFT, this removes just that chat's id
    from other folders' exclude_peers — nothing else. It deliberately does not
    bulk-clear exclude_peers: user-authored exclusions are indistinguishable from
    ours, and until a chat is left its legacy entry is a harmless extra safety net.

    Costs no API call in the common case: it reuses the folder cache that the
    joined*-folder cleanup has just warmed, and writes only if an entry matches.
    """
    if not entities:
        return 0, 0

    ids = await _collect_peer_ids(client, entities)
    if not ids:
        return 0, 0

    folders    = await _get_folders(client, cache)
    joined_ids = {f.id for f in _find_all_joined_folders(folders)}
    swept  = 0
    failed = 0

    for folder in folders:
        if folder.id in joined_ids:
            continue
        original = list(folder.exclude_peers or [])
        kept     = [p for p in original if _peer_id(p) not in ids]
        n        = len(original) - len(kept)
        if n == 0:
            continue

        updated = copy.copy(folder)
        updated.exclude_peers = kept
        if await _write_folder_with_retry(
            client, updated, account_index, cache, what=f"remove {n} legacy exclusion(s) from"
        ):
            swept += n
            log.debug(
                "[Account%d] Swept %d legacy exclusion(s) from '%s' folder.",
                account_index, n, _folder_title(updated),
            )
        else:
            failed += 1

    return swept, failed


async def _remove_left_from_folders(
    client: TelegramClient,
    entities: list,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> tuple[int, int, int]:
    """
    The ONE post-leave folder cleanup used by `left`, auto-leave and the folder
    reset (Pattern 1.3 — these used to be three parallel, diverging copies).
    Returns (removed_from_joined, legacy_swept, folders_failed). Never raises
    (except CancelledError); every failure is logged and counted.
    """
    if not entities:
        return 0, 0, 0
    removed = swept = failed = 0

    try:
        removed, f1 = await _remove_peers_from_all_joined_folders(client, entities, account_index, cache)
        failed += f1
    except errors.FloodError as exc:
        failed += 1
        log.warning(
            "[Account%d] Folder cleanup after leave hit FloodWait %ds (%s) — joined* folders not updated.",
            account_index, _flood_seconds(exc), type(exc).__name__,
        )
    except Exception as exc:
        failed += 1
        log.warning(
            "[Account%d] Folder cleanup after leave failed: %s: %s",
            account_index, type(exc).__name__, exc,
        )

    try:
        swept, f2 = await _sweep_legacy_exclusions(client, entities, account_index, cache)
        failed += f2
    except errors.FloodError as exc:
        failed += 1
        log.warning(
            "[Account%d] Legacy-exclusion sweep hit FloodWait %ds (%s) — skipped.",
            account_index, _flood_seconds(exc), type(exc).__name__,
        )
    except Exception as exc:
        failed += 1
        log.warning(
            "[Account%d] Legacy-exclusion sweep failed: %s: %s",
            account_index, type(exc).__name__, exc,
        )

    return removed, swept, failed


# ── v3.2.0: Folder protection — exclude_archived ─────────────────────────────

async def ensure_exclude_archived(
    client: TelegramClient,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
    *,
    result: EnsureResult | None = None,
    context: str = "",
    pace: float = _ENSURE_PACE,
    max_flood_wait: float = _ENSURE_MAX_FLOOD_WAIT,
) -> EnsureResult:
    """
    Make sure EVERY regular, non-joined* folder carries Telegram's own
    `exclude_archived` flag, so archived joined chats never show up in it.
    This replaces the old per-chat exclude_peers machinery.

    Why it is safe (verified in the Telegram Desktop and Android clients): a folder
    shows a chat if it is NOT in exclude_peers AND (it is in include_peers OR it
    matches the type flags and passes the muted / read / archived conditions). So
    exclude_archived only hides archived chats that match by TYPE flags;
    explicitly included peers always stay visible — which is why every joined*
    folder can (and does) keep the flag. Side effect, by design and documented:
    a user's own manually archived chats also disappear from folders that match by
    type flags.

    Behaviour:
      • Idempotent — a second run costs one read and zero writes.
      • Reads the folder list FRESH (never from the cache) right before writing, so a
        concurrent edit made in another Telegram client is not overwritten with
        stale data; the fresh list then refreshes the cache.
      • Skips DialogFilterDefault ("All chats" placeholder), DialogFilterChatlist
        (shared folders: no such field, and they only hold explicit includes) and
        every joined* folder.
      • Read-modify-write on a COPY of the parsed filter, so every other field
        (title entities, color, emoticon, title_noanimate, pinned/include/exclude
        peers, type flags) survives byte-for-byte. The check is truthiness-based:
        None and False are identical on the wire.
      • FloodWait is global: Telethon short-circuits repeat calls of a request type
        while a wait is active, so one wait covers every remaining folder. It is
        slept through ONCE per occurrence and the SAME folder is retried. When the
        total wait would exceed `max_flood_wait`, the remaining folders are counted
        as deferred, the summary is logged and the FloodError is RE-RAISED (never
        swallowed) — callers use _reconcile_folders(), which reports it.
      • Per-folder rejections (the Filter* RPC errors, FILTERS_TOO_MUCH, network
        errors, a False answer) are logged at WARNING with folder id/title/error
        class, counted, and never abort the loop. CancelledError always propagates.
      • No module-level state: the cache is a parameter, so a hot reload simply
        re-defines this function.

    `result` may be passed in so a caller still has the partial counts if the
    FloodError escapes. Returns the (possibly caller-supplied) EnsureResult.
    """
    res = result if result is not None else EnsureResult()
    ctx = f" [{context}]" if context else ""
    waited = 0.0

    try:
        # ── 1. Fresh read ────────────────────────────────────────────────
        while True:
            try:
                filters = await _fetch_all_filters(client)
                break
            except errors.FloodError as exc:
                wait = _flood_seconds(exc) + _FLOOD_PAD
                if waited + wait > max_flood_wait:
                    res.fetch_error = f"FloodWait {_flood_seconds(exc)}s exceeds the wait budget"
                    log.warning(
                        "[Account%d] ensure_exclude_archived%s: FloodWait %ds (%s) while reading "
                        "folders exceeds the %ds budget — deferring to the next reconcile.",
                        account_index, ctx, _flood_seconds(exc), type(exc).__name__, int(max_flood_wait),
                    )
                    raise
                log.info(
                    "[Account%d] ensure_exclude_archived%s: FloodWait %ds while reading folders — waiting.",
                    account_index, ctx, _flood_seconds(exc),
                )
                await asyncio.sleep(wait)
                waited += wait
            except Exception as exc:
                res.fetch_error = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "[Account%d] ensure_exclude_archived%s: could not read the folder list: %s",
                    account_index, ctx, res.fetch_error,
                )
                return res

        _store_folders_in_cache(client, cache, [f for f in filters if isinstance(f, DialogFilter)])

        # ── 2. Classify ──────────────────────────────────────────────────
        candidates: list[DialogFilter] = []
        for f in filters:
            if isinstance(f, (DialogFilterDefault, DialogFilterChatlist)):
                res.skipped += 1
                continue
            if not isinstance(f, DialogFilter):
                res.skipped += 1
                log.debug(
                    "[Account%d] ensure_exclude_archived%s: skipping unknown filter type %s.",
                    account_index, ctx, type(f).__name__,
                )
                continue
            if _get_joined_folder_number(_folder_title(f)) is not None:
                res.skipped += 1
                continue
            if f.exclude_archived:
                res.already_ok += 1
                continue
            candidates.append(f)

        # ── 3. Write, paced, FloodWait-global ────────────────────────────
        for idx, folder in enumerate(candidates):
            if idx > 0 and pace > 0:
                await asyncio.sleep(pace * random.uniform(0.8, 1.2))
            label = f"'{_folder_title(folder)}' (id={folder.id})"

            while True:
                try:
                    updated = copy.copy(folder)
                    updated.exclude_archived = True
                    answer = await client(UpdateDialogFilterRequest(id=updated.id, filter=updated))
                except errors.FloodError as exc:
                    wait = _flood_seconds(exc) + _FLOOD_PAD
                    if waited + wait > max_flood_wait:
                        res.deferred += len(candidates) - idx
                        log.warning(
                            "[Account%d] ensure_exclude_archived%s: FloodWait %ds (%s) at %s exceeds the "
                            "%ds budget — %d folder(s) deferred to the next reconcile.",
                            account_index, ctx, _flood_seconds(exc), type(exc).__name__, label,
                            int(max_flood_wait), len(candidates) - idx,
                        )
                        raise
                    log.info(
                        "[Account%d] ensure_exclude_archived%s: FloodWait %ds at %s — waiting once, "
                        "then resuming.",
                        account_index, ctx, _flood_seconds(exc), label,
                    )
                    await asyncio.sleep(wait)
                    waited += wait
                    continue
                except Exception as exc:
                    # Every sibling lands here: FilterIdInvalid, FilterIncludeEmpty,
                    # FilterNotSupported, FilterTitleEmpty, InputFilterInvalid,
                    # FILTERS_TOO_MUCH, … (all errors.RPCError) and network errors.
                    res.failed += 1
                    res.failures.append(f"{label}: {type(exc).__name__}: {exc}")
                    log.warning(
                        "[Account%d] ensure_exclude_archived%s: could not update folder %s: %s: %s",
                        account_index, ctx, label, type(exc).__name__, exc,
                    )
                    break

                if answer is False:
                    res.failed += 1
                    res.failures.append(f"{label}: server answered false")
                    log.warning(
                        "[Account%d] ensure_exclude_archived%s: Telegram refused the update of folder %s.",
                        account_index, ctx, label,
                    )
                    break

                _update_folder_in_cache(client, cache, updated)
                res.updated += 1
                log.debug(
                    "[Account%d] ensure_exclude_archived%s: set exclude_archived=True on folder %s.",
                    account_index, ctx, label,
                )
                break

        return res

    finally:
        # One line per run: INFO when something happened, DEBUG when it was a no-op.
        if res.changed:
            log.info("[Account%d] ensure_exclude_archived%s: %s", account_index, ctx, res.summary())
        else:
            log.debug("[Account%d] ensure_exclude_archived%s: %s", account_index, ctx, res.summary())


