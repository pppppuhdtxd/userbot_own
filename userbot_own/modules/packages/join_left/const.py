"""
userbot_own/modules/packages/join_left/const.py
════════════════════════════════════════════════════════════════
Tunable constants for the join_left module.

Pure data — no imports from the project, no side effects. Everything
here is a named knob (delays, caps, thresholds) that the rest of the
package imports by name, so changing a value never means editing
logic.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

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
_ADAPTIVE_FAST_DECAY = 0.80   # faster decay toward 1.0 after sustained success
_ADAPTIVE_FAST_DECAY_THRESHOLD = 5  # consecutive successful joins needed before fast decay kicks in

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


