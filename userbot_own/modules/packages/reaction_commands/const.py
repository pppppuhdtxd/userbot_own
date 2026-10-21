"""
userbot_own/modules/packages/reaction_commands/const.py
════════════════════════════════════════════════════════════════
Tunable numbers for the reaction funnel.

Pure data — the caps on how much reaction state is remembered, the
cooldown between identical triggers, and the heartbeat interval.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

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
#:
#: v3.1.9: this heartbeat is now written at DEBUG level (file-only — see
#: _heartbeat_loop() below) instead of INFO (terminal-visible), which was
#: spamming the console every 5 minutes. It still fires on exactly this
#: schedule and still reaches the per-account log file unchanged, so the
#: v3.0.12 diagnostic value for a post-incident file review is fully
#: preserved. For an immediate, on-demand answer to "is this module still
#: alive?" without waiting for the interval or opening a log file, see the
#: `reactions` / `.reactions` command output (_format_liveness_line()),
#: which reports the same ready/last-processed facts synchronously.
_HEARTBEAT_INTERVAL_SECONDS = 300.0
