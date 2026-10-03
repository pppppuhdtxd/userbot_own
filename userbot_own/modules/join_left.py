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
  → v3.2.0: after the batch, make sure every non-joined folder has
    exclude_archived (one API call per folder, only where missing)

- `join delay <seconds>`
  → Set fixed delay between joins (0 = restore smart throttling)

- `join mode fast|safe|human`
  → fast  : no smart throttling, no batching (respects delay setting)
  → safe  : Layer 1 + Layer 2 — smart resolution + risk delays [DEFAULT]
  → human : all 4 layers — adds adaptive backoff + batch cooldowns

- `left` (reply to a message with links/usernames/IDs)
  → Leave all found chats with safe pacing (FloodWait-aware)
  → Remove left chats from ALL 'joined*' folders automatically
  → v3.2.0: nothing to un-exclude — archived chats stay hidden through
    exclude_archived (legacy exclude_peers entries of the chat are swept)
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

v3.2.0 Changes:
  Redesign — Telegram's server-side `exclude_archived` replaces per-chat exclusion:
    Every joined chat is muted and archived (folder_id=1). Before 3.2.0 each chat
    was ALSO written, one UpdateDialogFilterRequest per folder per join, into the
    exclude_peers list of every other folder (100-entry cap, tracking, sync bugs).
    Now ensure_exclude_archived() makes sure every other regular folder carries
    the `exclude_archived` flag — once, only where it is missing. Verified in the
    Telegram Desktop (ChatFilter::contains) and Android (DialogFilter.includesDialog)
    clients: precedence is exclude_peers > include_peers > exclude_archived, so
    chats in a folder's include_peers (every joined* folder) stay visible even
    with the flag set. The flag only hides archived chats that match a folder by
    TYPE flags (contacts / non-contacts / groups / channels / bots).
    REMOVED: _add_to_all_other_folders_exclusion, _remove_from_all_folders_exclusion,
    _EXCLUDE_PEERS_CAPACITY, and the dead _ensure_joined_folder_exists,
    _find_joined_folder, _add_peers_to_joined_folder, _mute_and_archive.
    KEPT (it is NOT exclusion code): _sync_folder_to_tracking — it feeds auto-leave.
    VISIBLE SIDE EFFECT: because the flag is set on the user's own folders too,
    chats the user archived BY HAND no longer appear in folders that match by type
    flags (explicit-peer-only folders are unaffected). Documented in help_extra.

  ensure_exclude_archived() — idempotent, FloodWait-global, never silent:
    Reads the folder list fresh, skips DialogFilterDefault / DialogFilterChatlist /
    joined*, sets the flag on a COPY of each remaining folder, paces writes, waits
    a FloodWait once and resumes, re-raises when its wait budget is spent, counts
    and logs every failure. Runs at the end of every join batch, at startup and
    every 6 hours (so a folder created after a batch is protected too).

  Fix N1 (CRITICAL) — false "folder add succeeded" after a FloodWait:
    _add_single_peer_to_joined_folder appended the peer to the CACHED DialogFilter
    before calling the API. A FloodWait on the write left the cache claiming a
    state the server never had; the retry saw the peer "already present" and
    returned True although nothing was persisted. All folder writes now build the
    new state on a copy and update the cache only after Telegram accepted it
    (_write_folder_with_retry). _remove_peers_from_all_joined_folders had the same flaw (N3).

  Fix N2/N3 — mute / archive / folder results are first-class:
    A second FloodWait used to be swallowed with no log, and _post_join_actions'
    result was ignored at all call sites. Now: bounded, logged retries
    (_call_with_flood_retry); _post_join_actions returns a PostJoinResult;
    _record_join() (one shared path for all join outcomes) queues incomplete chats
    for an end-of-batch retry sweep and the 6-hour cycle; the summary reports real
    counts instead of len(joined_entities). Mute is load-bearing: an unmuted
    archived chat is moved back to the main list by Telegram on its next message.

  Fix N4 — Pattern 1.2 (sibling exceptions):
    FloodWaitError, FloodPremiumWaitError, FloodTestPhoneWaitError and
    SlowModeWaitError are siblings under errors.FloodError; only the first was
    caught. All sites now catch errors.FloodError (see _flood_seconds). Folder
    writes treat every errors.RPCError (the Filter* family and more) as a
    per-folder failure.

  Fix N5 — Pattern 1.3: unified post-leave cleanup:
    `left`, auto-leave and the folder reset share _remove_left_from_folders().
    "Not a participant" / private / not-found branches now clean the joined*
    folders too (they used to leave stale entries that consumed the 100-chat cap).

  Migration: NO bulk clearing of exclude_peers (user-authored exclusions cannot be
    told apart). Legacy entries are inert; the scoped sweep removes only the
    entries of a chat when that chat is left. The first startup reconcile flips
    the flag on existing folders — that run IS the migration; no marker needed.

  Also: K5 — the "already a member" wording now covers the hash-fallback branch of
    invite_with_username; docstring/comment corrections (_check_auto_leave is
    age-based; the unverified "folder update un-archives" claim removed); ruff
    I001/E741 clean.

v3.1.8 Changes:
  Fix G1 — Catch InviteHashExpiredError in _validate_invite_links (CRITICAL):
    InviteHashExpiredError is a distinct Telethon exception from
    InviteHashInvalidError — both are direct subclasses of BadRequestError,
    siblings to each other, not related by inheritance. Telegram raises
    InviteHashExpiredError specifically for links that have run past their
    time/usage expiry (the most common real-world "bad link" scenario),
    while InviteHashInvalidError covers malformed/revoked hashes. Only
    catching InviteHashInvalidError meant expired links fell through to
    the generic `except Exception` handler in _validate_invite_links,
    which explicitly does NOT cancel the batch — the expired link was
    silently treated as valid, joined other valid links first, then
    failed with an unclear message when its turn came, exactly matching
    the reported "some chats got joined before the operation should have
    been cancelled" behavior. Now both exception types are caught
    identically and correctly trigger full-batch cancellation.

  Fix G3 — Catch InviteHashExpiredError in the main join loop's fallback
  classification:
    Defense-in-depth for the rare race where a link expires in the brief
    window between Phase 0 validation and its turn in the main join loop.
    Mirrors the G1 fix — the isinstance check in the generic exception
    handler now includes InviteHashExpiredError alongside
    InviteHashInvalidError, producing the clear "❌ لینک منقضی یا نامعتبر"
    message instead of a garbled generic error.

  Fix G5 — INVITE_HASH_EXPIRED string check in the generic fallback:
    Secondary safety net alongside G3's isinstance check, mirroring the
    existing "INVITE_HASH_INVALID" in err string-fallback pattern, in case
    a future Telethon version or an unusual API response surfaces the
    error as a raw string rather than the typed exception.

  Note: no additional validation criteria were added beyond expired/
  invalid link detection. _validate_invite_links still validates ONLY
  invite_link entities via CheckChatInviteRequest, checking exclusively
  for InviteHashInvalidError / InviteHashExpiredError. No chat-fullness
  check, no channel-vs-group check, no separate username verification —
  the scope of pre-join validation is unchanged from v3.1.7 except for
  recognizing this one additional, previously-missed exception type.

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
    v3.2.0: the whole exclusion system described here was REMOVED (superseded
    by exclude_archived, see above); this entry is kept as history only.
    v3.1.10 correction: the type-aware half of this fix (introduced in v3.1.6)
    no longer exists. It was reverted afterwards in an unversioned hotfix
    (CHANGELOG [3.1.8] mentions it) because it suppressed exclusion for
    explicit-peer folders with no type flags set — the common case. A joined
    chat is now excluded from EVERY other editable folder unconditionally (see
    _add_to_all_other_folders_exclusion). This paragraph used to describe the
    reverted behaviour as current — "only excluded from folders whose type
    flags match the entity type", "purely explicit-peer folders are skipped" —
    and its helper, _folder_would_show_entity, was left behind as dead code
    until v3.1.10.
    Still in force: a 100-entry cap on exclude_peers per folder (N4).

  Fix I3 — Pre-join invite link validation:
    Before starting any join operations, all invite_link entities are
    validated via CheckChatInviteRequest. If ANY link is expired/invalid,
    the entire operation is cancelled with a clear Persian error message.
    Already-joined links skip the join API call but still receive full
    post-join actions (folder, mute, archive; exclusion until v3.2.0). These are
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
    (Removed in v3.2.0 together with the exclusion system.)

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

  Fix G — _post_join_actions exclusion now awaited (exclusion removed in v3.2.0):
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

  Req 4: [SUPERSEDED in v3.2.0 by exclude_archived — see above]
    Strict folder exclusion — joined chats added to excluded_chats
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
import copy
import datetime
import random
import re
import time
from dataclasses import dataclass, field

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
    DialogFilterChatlist,
    DialogFilterDefault,
    InputFolderPeer,
    InputNotifyPeer,
    InputPeerNotifySettings,
    InputPeerSelf,
    ReplyInlineMarkup,
    TextWithEntities,
)
from telethon.tl.types import messages as tl_messages

from userbot_own.core.context import ModuleContext
from userbot_own.core.logging_setup import get_logger
from userbot_own.helpers.utils import (
    get_inline_button_url,
    leave_dialog,
    read_json_file,
    safe_delete,
    write_json_file_atomic,
)
from userbot_own.modules.base import Module

# Module-level logger — used only by free functions outside the Module class
# (the Module class itself logs through self._log_* / modules/base.py).
#
# v3.1.10: this used to be a raw stdlib `logging.getLogger(__name__)`. A stdlib
# logger reaches loguru through InterceptHandler WITHOUT the `name` key that
# core/logging_setup.py's file sinks require before they accept a record, so
# every call below (28 debug + 1 warning) was silently dropped from main.log
# and from every per-account log — and only the single warning ever reached
# the console, indistinguishable from third-party noise. It is the same root
# cause v3.1.9 already fixed in app/application.py and core/reconnector.py;
# this file was missed. get_logger() binds `name`, and the `account` context
# entered by CompositionRoot.start_account() routes each record to the right
# per-account file. (The "[Account%d]" text prefixes on the messages are kept
# as they were — auto_clearer.py uses the same module-level pattern.)
log = get_logger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

_JOINED_FOLDER_NAME    = "joined"
_FOLDER_CAPACITY       = 100    # I1: Telegram's hard cap on include_peers per folder
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

# v3.2.0: post-join action (mute / archive / folder-add) retry policy.
# A FloodWait is waited out inline only while it is short; a longer one is NOT
# slept through in the middle of a join batch — the action is handed to the
# end-of-batch retry sweep instead (and, if still unresolved, to the 6-hour
# reconcile). Every give-up is logged at WARNING and surfaced in the summary.
_ACTION_MAX_ATTEMPTS = 3       # attempts per mute / archive / folder write
_ACTION_FLOOD_CAP    = 60.0    # seconds: longest FloodWait slept through inline
_FLOOD_PAD           = 2       # seconds added on top of the FloodWait Telegram asks for
_SWEEP_ROUNDS        = 2       # passes of the end-of-batch retry sweep
_SWEEP_ROUND_DELAY   = 3.0     # seconds between sweep passes
_SWEEP_TIME_BUDGET   = 180.0   # seconds: hard ceiling on one sweep
_PENDING_MAX_SWEEPS  = 4       # sweeps before an unresolved chat is given up on
_ARCHIVE_FOLDER_ID   = 1       # Telegram: folder_id 1 = Archive (no other id is allowed)

# v3.2.0: ensure_exclude_archived() pacing
_ENSURE_PACE            = 1.2    # seconds between consecutive folder writes (±20% jitter)
_ENSURE_MAX_FLOOD_WAIT  = 180.0  # seconds: total FloodWait one ensure run may sleep through

# Auto-leave / reconcile cadence
_AUTO_LEAVE_INTERVAL = 6 * 3600


# ── v3.2.0: FloodWait helper + result types ───────────────────────────────────

def _flood_seconds(exc: BaseException) -> int:
    """
    Seconds Telegram asks us to wait, for ANY member of Telethon's flood family.

    errors.FloodWaitError, FloodPremiumWaitError, FloodTestPhoneWaitError and
    SlowModeWaitError are SIBLINGS under errors.FloodError — none is a subclass
    of another — so `except errors.FloodWaitError` silently misses the other
    three (Pattern 1.2). Callers now catch errors.FloodError and read the wait
    through this helper; the bare FloodError base class carries no `.seconds`,
    hence the defensive getattr. Always returns at least 1.
    """
    try:
        seconds = int(getattr(exc, "seconds", 0) or 0)
    except (TypeError, ValueError):
        seconds = 0
    return max(1, seconds)


@dataclass(frozen=True, slots=True)
class PostJoinResult:
    """Outcome of the three per-chat post-join actions (v3.2.0: first-class result)."""
    muted: bool
    folder_added: bool
    archived: bool

    @property
    def complete(self) -> bool:
        return self.muted and self.folder_added and self.archived

    def missing(self) -> set[str]:
        """Names of the actions that did NOT succeed: subset of {mute, folder, archive}."""
        out: set[str] = set()
        if not self.muted:
            out.add("mute")
        if not self.folder_added:
            out.add("folder")
        if not self.archived:
            out.add("archive")
        return out


@dataclass(slots=True)
class _PendingActions:
    """A joined chat whose post-join actions are not all done yet (per-account, in-memory)."""
    entity: object
    missing: set[str]
    attempts: int = 0


@dataclass(slots=True)
class EnsureResult:
    """
    What one ensure_exclude_archived() run did. Mutable so the function can fill
    it in as it goes — a caller that receives a FloodError out of the function
    (budget exhausted) therefore still has an accurate partial result.
    """
    updated:     int = 0                    # folders flipped to exclude_archived=True
    already_ok:  int = 0                    # folders that already had the flag
    skipped:     int = 0                    # ineligible: default / shared / joined* / unknown
    failed:      int = 0                    # folders whose write was rejected
    deferred:    int = 0                    # folders not attempted (FloodWait budget spent)
    fetch_error: str | None = None          # set when the folder list could not be read
    failures:    list[str] = field(default_factory=list)

    @property
    def problems(self) -> int:
        return self.failed + self.deferred + (1 if self.fetch_error else 0)

    @property
    def changed(self) -> bool:
        """True when the run did anything worth telling the user / the log at INFO."""
        return bool(self.updated or self.problems)

    def summary(self) -> str:
        parts = [
            f"updated={self.updated}", f"already_ok={self.already_ok}",
            f"skipped={self.skipped}", f"failed={self.failed}", f"deferred={self.deferred}",
        ]
        if self.fetch_error:
            parts.append(f"fetch_error={self.fetch_error}")
        return " ".join(parts)


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
    result = await client(GetDialogFiltersRequest())
    return list(getattr(result, "filters", result) or [])


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

    await client(UpdateDialogFilterRequest(id=new_id, filter=new_folder))
    _invalidate_folder_cache(client, cache)
    log.debug(
        "[Account%d] Created '%s' folder (id=%d) with %d peer(s).",
        account_index, title_str, new_id, len(inc_peers),
    )
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


# ── Req 3: Mute and archive helpers (v3.2.0: first-class, never silent) ───────

async def _call_with_flood_retry(
    client: TelegramClient,
    entity,
    build_request,
    *,
    action: str,
    account_index: int,
) -> bool:
    """
    Run one per-chat action (mute / archive) with a bounded, LOGGED retry.

    v3.2.0 (N2): the previous helpers waited once (capped at 30 s), retried once, and
    swallowed a second FloodWait with `except Exception: return False` — no log at
    all. Under the exclude_archived design a chat whose archive or mute failed is
    visible in every type-flag folder (or pops out of the archive on its next
    message), so a silent failure is no longer acceptable. Now: up to
    _ACTION_MAX_ATTEMPTS attempts; a FloodWait (any flood sibling) is slept through
    only if it is <= _ACTION_FLOOD_CAP; every give-up is a WARNING; the caller gets
    an honest bool and queues the chat for the retry sweep.

    `build_request(input_peer)` returns a fresh request object per attempt.
    """
    ent_id = getattr(entity, "id", entity)
    try:
        input_peer = await client.get_input_entity(entity)
    except Exception as exc:
        log.warning(
            "[Account%d] Could not %s chat id=%s: peer resolution failed (%s: %s)",
            account_index, action, ent_id, type(exc).__name__, exc,
        )
        return False

    for attempt in range(1, _ACTION_MAX_ATTEMPTS + 1):
        try:
            await client(build_request(input_peer))
            log.debug("[Account%d] %s ok for chat id=%s.", account_index, action.capitalize(), ent_id)
            return True
        except errors.FloodError as exc:
            wait = _flood_seconds(exc) + _FLOOD_PAD
            if attempt >= _ACTION_MAX_ATTEMPTS or wait > _ACTION_FLOOD_CAP:
                log.warning(
                    "[Account%d] Could not %s chat id=%s: FloodWait %ds (%s), attempt %d/%d, "
                    "inline cap %ds — queued for the retry sweep.",
                    account_index, action, ent_id, _flood_seconds(exc), type(exc).__name__,
                    attempt, _ACTION_MAX_ATTEMPTS, int(_ACTION_FLOOD_CAP),
                )
                return False
            log.debug(
                "[Account%d] FloodWait %ds on %s for chat id=%s (attempt %d/%d) — waiting.",
                account_index, _flood_seconds(exc), action, ent_id, attempt, _ACTION_MAX_ATTEMPTS,
            )
            await asyncio.sleep(wait)
        except Exception as exc:
            log.warning(
                "[Account%d] Could not %s chat id=%s: %s: %s",
                account_index, action, ent_id, type(exc).__name__, exc,
            )
            return False
    return False


async def _mute_chat(client: TelegramClient, entity, account_index: int) -> bool:
    """
    Req 3: Mute a chat immediately after joining.
    Sets mute_until to year 2038 (effectively permanent).

    The mute is LOAD-BEARING (v3.2.0): Telegram moves an UNMUTED archived chat back
    to the main list when a new message arrives (unless the account has
    keep_archived_unmuted set); a muted one stays archived. Without a successful
    mute the exclude_archived protection would evaporate on the first message.
    Returns True on success.
    """
    def build(input_peer):
        return UpdateNotifySettingsRequest(
            peer=InputNotifyPeer(peer=input_peer),
            settings=InputPeerNotifySettings(
                show_previews=False,
                silent=False,
                mute_until=2147483647,  # max Unix timestamp (year 2038)
            ),
        )

    return await _call_with_flood_retry(
        client, entity, build, action="mute", account_index=account_index
    )


async def _archive_chat(client: TelegramClient, entity, account_index: int) -> bool:
    """
    Req 3: Archive a chat immediately after joining.
    Uses EditPeerFoldersRequest to move it to folder_id=1 (Archive — the only
    folder id Telegram allows besides 0).

    Under exclude_archived this IS the protection: an un-archived joined chat is
    visible in every type-flag folder. Returns True on success.
    """
    def build(input_peer):
        return EditPeerFoldersRequest(
            folder_peers=[InputFolderPeer(peer=input_peer, folder_id=_ARCHIVE_FOLDER_ID)],
        )

    return await _call_with_flood_retry(
        client, entity, build, action="archive", account_index=account_index
    )


# ── Folder reset helper ───────────────────────────────────────────────────────

async def _leave_and_reset_joined_folder(
    client: TelegramClient,
    account_index: int,
    cache: dict[int, tuple[float, list[DialogFilter]]],
) -> tuple[int, int]:
    """
    I1: Leave all chats from ALL joined* folders (joined, joined2, joined3, …),
    delete ALL joined* folders, then recreate only the base 'joined' folder.

    v3.2.0: the per-chat exclusion cleanup is gone with the exclusion system; what
    remains is the scoped legacy sweep for the chats just left (see
    _sweep_legacy_exclusions). Failures to delete a folder are logged at WARNING.
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
                    left_entities.append(peer)  # its legacy exclusions still need sweeping
                    break
                except errors.FloodError as exc:
                    fw_retries += 1
                    if fw_retries > _LEFT_MAX_FW_RETRIES:
                        failed_count += 1
                        log.warning(
                            "[Account%d] Folder reset: FloodWait retry cap on peer id=%d "
                            "in '%s' — skipping.",
                            account_index, pid, folder_label,
                        )
                        break
                    log.debug(
                        "[Account%d] Folder reset: FloodWait %ds on peer id=%d "
                        "in '%s' (retry %d/%d).",
                        account_index, _flood_seconds(exc), pid, folder_label,
                        fw_retries, _LEFT_MAX_FW_RETRIES,
                    )
                    await asyncio.sleep(_flood_seconds(exc) + _FLOOD_PAD)
                except Exception as exc:
                    failed_count += 1
                    log.warning(
                        "[Account%d] Folder reset: could not leave peer id=%d in '%s': %s: %s",
                        account_index, pid, folder_label, type(exc).__name__, exc,
                    )
                    break

            # Req 5: inter-leave pacing
            if idx < len(peers_to_leave) - 1:
                await asyncio.sleep(_LEFT_INTER_DELAY)

        # Delete this folder (no filter= argument = delete operation)
        try:
            await client(UpdateDialogFilterRequest(id=folder.id))
        except Exception as exc:
            log.warning(
                "[Account%d] Could not delete folder '%s': %s: %s",
                account_index, folder_label, type(exc).__name__, exc,
            )
        _invalidate_folder_cache(client, cache)

    # v3.2.0: scoped legacy sweep for everything we left (never raises)
    if left_entities:
        try:
            await _sweep_legacy_exclusions(client, left_entities, account_index, cache)
        except Exception as exc:
            log.warning(
                "[Account%d] Legacy-exclusion sweep after folder reset failed: %s: %s",
                account_index, type(exc).__name__, exc,
            )

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

            # Cache hit → already verified recently; skip the API call
            cached = self._invite_cache.get(invite_hash)
            if cached is not None:
                cached_ts = cached.get("ts", 0)
                if (time.time() - cached_ts) < _INVITE_CACHE_TTL:
                    remaining.append((entity_type, identifier))
                    continue

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
        joined_entities: list = []
        post_results: list[tuple[object, PostJoinResult]] = []   # v3.2.0: (entity, outcome)

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
                                if joined_entity:
                                    joined_entities.append(joined_entity)
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

                        joined_entities.append(joined_entity)
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
                            adaptive_mult = max(1.0, adaptive_mult * _ADAPTIVE_DECAY)

                        # Req 1 + Req 3: immediate per-chat recording + post-processing
                        # (v3.2.0: through the shared _record_join(); outcome is kept)
                        post_results.append((joined_entity, await self._record_join(client, joined_entity)))

                    break  # ← success (or handled WebView), exit retry loop

                # ── FloodWait handling (Layer 3 + Req 5 retry cap) ────────────
                except errors.FloodError as exc:
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
                    elif "FLOOD_WAIT" in err:
                        status = f"⏳ FloodWait: {err[:40]}"
                    else:
                        status = f"❌ خطا: {err[:40]}"
                    results.append(f"[{identifier}] — {status}")
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
        # so the summary states real outcomes; it used to report len(joined_entities)
        # as "added to folder" without looking at a single result — Pattern 1.6)
        # ═══════════════════════════════════════════════════════════════════════
        folder_note = ""
        if post_results:
            try:
                await processing_msg.edit(
                    f"🧹 در حال نهایی‌سازی (mute / فولدر / archive)...\n"
                    f"_تا کنون: {success_count} موفق / {fail_count} ناموفق_",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

            await self._retry_missing_actions(client, reason="join batch")
            ensure_res = await self._reconcile_folders(client, reason="join batch")

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
    "تغییرات v3.2.0:\n"
    "• سیستم جدید پنهان‌سازی: هر چت جوین‌شده mute و archive می‌شود و فولدرهای دیگر شما\n"
    "  به‌جای فهرست exclusion تک‌تک چت‌ها، تنظیم رسمی تلگرام `exclude_archived` را می‌گیرند\n"
    "  → دیگر برای هر جوین یک درخواست API به ازای هر فولدر لازم نیست و سقف ۱۰۰ تایی exclusion حذف شد\n"
    "  → این بررسی در پایان هر `join`، هنگام شروع ربات و هر ۶ ساعت انجام می‌شود\n"
    "    (فولدرهایی که بعداً می‌سازید هم پوشش داده می‌شوند)\n"
    "• ⚠️ توجه: چون این تنظیم روی فولدرهای شما هم اعمال می‌شود، چت‌هایی که خودتان دستی\n"
    "  archive کرده‌اید دیگر در فولدرهایی که بر اساس نوع (گروه/کانال/ربات/مخاطب) فیلتر می‌کنند\n"
    "  نمایش داده نمی‌شوند. فولدرهای joined* و فولدرهایی که فقط چت‌های مشخص دارند تغییری نمی‌کنند\n"
    "• رفع باگ حیاتی: اگر هنگام افزودن چت به فولدر FloodWait می‌آمد، چت گاهی «اضافه‌شده» گزارش می‌شد\n"
    "  ولی هرگز ذخیره نمی‌شد (N1) — اکنون فقط پس از تأیید تلگرام ثبت می‌شود\n"
    "• mute / archive / فولدر حالا نتیجه‌شان بررسی و لاگ می‌شود؛ موارد ناقص خودکار دوباره تلاش می‌شوند\n"
    "• ترک چت (left / autoleave) در همه حالت‌ها چت را از فولدرهای joined* هم پاک می‌کند\n\n"
    "تغییرات v3.1.8:\n"
    "• رفع باگ حیاتی: لینک‌های منقضی گاهی عملیات جوین را لغو نمی‌کردند (G1)\n"
    "  → InviteHashExpiredError نوع متفاوتی از خطا نسبت به InviteHashInvalidError است\n"
    "  → اکنون هر دو نوع خطا به‌درستی شناسایی و کل عملیات لغو می‌شود\n"
    "• پیام خطای واضح‌تر برای لینک‌های منقضی در حلقه اصلی جوین (G3, G5)\n\n"
    "تغییرات v3.1.6:\n"
    "• رفع باگ حیاتی: خطای 'ChatInviteJoinResultOk' روی کانال‌های عمومی (C1)\n"
    "  → JoinChannelRequest ممکن است ChatInviteJoinResultOk برگرداند\n"
    "  → تمام ۵ مسیر جوین اکنون با _unwrap_join_result() ایمن شدند\n"
    "• پشتیبانی چند فولدر: وقتی joined پر شد، joined2، joined3 ایجاد می‌شود (I1)\n"
    "• اعتبارسنجی قبل از جوین: لینک‌های منقضی/نامعتبر قبل از شروع بررسی می‌شوند (I3)\n"
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
    "  → بعد از هر ۴ جوین: استراحت ۳۰s\n"
    "  → بعد از هر ۳ دسته: استراحت ۱۲۰s\n"
    "  → jitter ±۲۰٪ روی تمام تأخیرها\n\n"
    "مدیریت فولدر و پنهان‌سازی (v3.2.0):\n"
    "• هر چت جوین‌شده به فولدر joined* اضافه، mute و archive می‌شود\n"
    "• `folder` | ایجاد یا ریست فولدرهای joined*\n"
    "  → اگر joined پر شد (۱۰۰ چت)، joined2 خودکار ایجاد می‌شود\n"
    "  → `folder` تمام joined* را ریست کرده و فقط joined پایه را نگه می‌دارد\n"
    "• `list` | نمایش تمام چت‌های joined* (با نام فولدر در صورت چند فولدر)\n"
    "• فولدرهای غیر-joined شما `exclude_archived` می‌گیرند تا چت‌های archive شده در آن‌ها دیده نشوند\n"
    "• هر چت mute می‌شود چون تلگرام چت‌های بی‌صدا نشده را با پیام جدید از archive بیرون می‌آورد\n\n"
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
    "• چت‌های 'قبلاً عضو' مانند جوین موفق مدیریت می‌شوند (mute، folder، archive)\n"
    "• در صورت FloodWait، شمارش معکوس زنده نمایش داده می‌شود\n"
    "• هر entity حداکثر ۵ بار retry FloodWait دارد — بعد skip می‌شود\n"
    "• پس از `left` موفق، پیام دستور به‌صورت خودکار حذف می‌شود\n"
    "• هنگام فعال‌سازی autoleave، چت‌های موجود در همه فولدرها ردیابی می‌شوند\n"
)

JoinLeft.help_text  = help_text
JoinLeft.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    return JoinLeft(context)
