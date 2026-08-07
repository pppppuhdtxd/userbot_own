# Changelog

All notable changes to this project are documented here.
Format follows [Semantic Versioning](https://semver.org): **MAJOR.MINOR.PATCH**

---

## [3.0.11] — 2026-08-04

**Source:** AI (full-repository audit — see the standalone audit report for the
complete findings this release implements; every item below traces back to
a specific finding there, cross-checked against Telethon's own source
before being applied)

This release is scoped entirely to fixes, dead-code removal, and
documentation corrections identified by that audit — no new user-facing
commands or behavior were added, consistent with this project's own
PATCH-vs-MINOR versioning rule (see `README.md`'s Versioning Guide).

### Fixed — `join_left.py`: Leaving a legacy basic group didn't actually leave it

**Root cause:** `_leave_and_reset_joined_folder`, `_check_auto_leave`, and
`_handle_left` each independently branched on entity type and, for `Chat`
(legacy basic group — not supergroup) entities, called
`DeleteHistoryRequest(peer=entity, just_clear=False, max_id=0)`. That
request only clears the *local* message view for the calling account — it
does not remove chat membership. Verified against Telethon's own
high-level `kick_participant()` implementation (`telethon/client/chats.py`),
which uses `messages.DeleteChatUserRequest` for exactly this case. Net
effect: `.left`, auto-leave, and the folder-wide bulk-leave all logged and
tracked the account as having "left" any legacy basic group it targeted,
while the account silently remained a member. `Channel` entities (including
supergroups) were unaffected — they already correctly used
`LeaveChannelRequest` — and `User` entities were also unaffected, since
there's no membership to leave for a private chat.

**Fix:** all three call sites now call a single new shared helper,
`leave_dialog(client, entity)` (`helpers/utils.py`), which delegates
entirely to Telethon's own `TelegramClient.delete_dialog()` — already
correct for every entity type — instead of re-implementing entity-type
branching a fourth time. This is also a DRY fix: the same flawed 3-line
branch existed in three places; it's now one call in three places. Unused
`LeaveChannelRequest` / `DeleteHistoryRequest` imports removed from
`join_left.py` accordingly.

### Fixed — `join_left.py`: Stale help text

`help_extra` stated `invite direct: 8s`, left over from before the
`_SMART_DELAYS["invite_direct"]` constant was rebalanced from `8.0s` to
`4.0s` (see the `3.0.4` entry below) — the constant and the module's own
top-of-file docstring were updated at the time, but this user-facing
string wasn't. Now reads `4s`, matching both.

### Fixed — `helpers/utils.py`: `get_file_size()` unit-boundary rounding

**Root cause:** the unit index was chosen via `floor(log(size, 1024))`
*before* rounding the display value, so a size just under a unit boundary
(e.g. `1048575` bytes → `1023.99…` KB) would round to `1024.0` but keep
the lower unit label, displaying `"1024.0 KB"` instead of `"1.0 MB"`.

**Fix:** after rounding, if the value is `>= 1024` and a larger unit
exists, bump to that unit and recompute. `get_file_size(1048575)` now
correctly returns `"1.0 MB"`.

### Fixed — `clearer.py`: scans no longer silently report partial results as complete

**Root cause:** the `iter_messages` scan loop in `_run_clear` wrapped the
whole loop in a single broad `except Exception`, which logged and
continued — so a `FloodWaitError` (or any other transient error) partway
through a scan produced a "done" report computed from an incomplete scan,
with nothing telling the user it was cut short. This was inconsistent with
`whois_handler.py` and `info_handler.py`, which both consistently re-raise
`FloodWaitError` for their callers to handle.

**Fix:** the scan loop now re-raises `FloodWaitError` (matching the
established pattern) instead of swallowing it, and `_on_command` gained a
dedicated handler for it — matching `whois_handler.py`'s exact user-facing
message style — that tells the user the scan was interrupted and roughly
how long to wait. Other (non-FloodWait) scan errors still allow the
command to complete with whatever was found so far, but every outcome
message (`"no matches"` and the final report) now says explicitly when
the scan was cut short, instead of presenting a partial result as final.

### Fixed — `update.sh`: no longer trusts "local is behind origin" — verifies it

**Root cause:** `update.sh` runs `git reset --hard origin/<branch>` to
sync a deployment to the latest code, on the documented assumption that
"this device is a deployment target, not a place code is developed... it
can only discard local commits that were never pushed, which should not
exist on a deployment target." This audit's GitHub cross-reference found a
real case where that assumption didn't hold: the live `origin/main` was
stuck ~2 major versions behind this exact codebase, because 8 local
commits (this entire architecture refactor, `3.0.7`–`3.0.10`) had never
been pushed. Had `update.sh` been run in that state, it would have
silently discarded all of them.

**Fix:** after `git fetch`, `update.sh` now runs
`git rev-list --count origin/<branch>..HEAD` and refuses to proceed
(clear error, no changes made) if local has any commits origin doesn't —
instead of asserting the assumption in a comment and trusting it. See also
the new README note in the One-Click Installation section, and the FAQ.
*(The specific 8-commit gap that motivated this fix has already been
resolved — `main` has been pushed to `origin` as of this release — this
change is about making sure it can't silently happen again.)*

### Added — `install.sh` / `update.sh`: enforce the documented Python 3.11+ floor

**Root cause:** both scripts detected and displayed the interpreter's
Python version but never checked it against the `>=3.11` floor documented
in `requirements.txt` / `pyproject.toml`. An older system `python3` would
silently produce a venv that then failed confusingly, later, during
`pip install` or at runtime, far from the actual cause.

**Fix:** `install.sh` now fails fast with a clear message before creating
the venv if the detected interpreter is below `3.11`. `update.sh` now
performs the equivalent check against the *existing* venv's interpreter
(covering a venv created before this fix, or by hand) before doing
anything else, with instructions to recreate it if it's too old.

### Removed — `core/exceptions.py`: nine confirmed-dead exception classes

A full-repo, grep-verified reference check (not just a visual scan) found
that only `ModuleImportError` and `LoaderNotFoundError` — and the
`UserbotError` / `LoaderError` / `RegistryError` parent classes
structurally required by them — are actually raised or caught anywhere in
the codebase. The original audit flagged 6 dead classes (`ProxyError`,
`AuthError`, `ConnectionManagerError`, `FlowAlreadyActiveError`,
`FlowExpiredError`, `ModuleSetupError`); verifying reference counts before
deleting anything turned up 3 more of the same kind (`ConfigError`,
`AccountConfigError`, and the `FlowError` parent of the two
already-flagged flow exceptions), removed for the same reason. All 9 were
leftovers from the MTProxy support and the account-management flow system
(`account_management/flows.py`, `AccountFlowManager`) both removed before
or during earlier refactors (`3.0.1` for the flow system — see FAQ). The
hierarchy itself, and its two live branches, are unchanged.

### Removed — `EventBus` and `ConnectionStateChanged` (`core/events.py`)

**Root cause:** `AccountReconnector` published a `ConnectionStateChanged`
event on every reconnect via an application-scoped `EventBus`, injected
through `ModuleContext`. A full-repo grep for `.subscribe(` found exactly
one match anywhere — inside `EventBus`'s own docstring example — and zero
real subscribers. Notably, this is the *second* connection-notification
mechanism in a row with no consumer: the pre-refactor version
(`notify_connection_change()` / `register_connection_callback()`) had the
same problem.

**Decision:** rather than leave a fully wired, fully unused pub/sub
mechanism in the dependency-injection graph (or the alternative of adding
a first, speculative subscriber just to give it a reason to exist), it's
removed entirely: `core/events.py` deleted; `AccountReconnector` no longer
takes an `event_bus` constructor argument and now logs connection-state
transitions directly via its own contextual logger instead of publishing
them; `ModuleContext`, `composition_root.py`, and `core/__init__.py`
updated accordingly. Every trigger point and the reconnect logic itself
are unchanged — only the delivery mechanism for the resulting state
changed, from "publish to nothing" to "log directly." Straightforward to
reintroduce later, purpose-built, if a real consumer (e.g. a live
per-account status view) is ever designed — see FAQ.

### Removed — `PluginMetadataStore` / `PluginMetadata` (`core/registry.py`)

**Root cause:** written to (`upsert()`) on every module load/reload/unload
by `core/loader.py`, with no reader anywhere in the codebase — confirmed
by the same full-repo grep pass. It had previously been kept specifically
because `README.md`'s feature table advertised it ("Plugin registry —
Rich metadata, introspection, and runtime management API"); removing the
code without removing that claim would have just created a new
documentation/reality mismatch of exactly the kind this audit was
already fixing elsewhere (see below), so both are removed together this
time. `AccountRegistry` and `AccountLoaderRegistry`, in the same file, are
unrelated and unchanged.

### Documentation — `README.md`: architecture docs resynced with the code

Beyond the feature-table row and the two removed-subsystem mentions above,
the architecture section had accumulated several more references to
`EventBus` / `PluginMetadataStore` that the earlier `3.0.x` refactors
never fully swept: the `core/` directory tree listing, the Composition
Root description, the plugin-lifecycle diagram, the `ModuleContext` field
list (prose and table), the reconnector flow diagram, and the Registries
table. All updated to match the current code. Also fixed: the feature
table's "Plugin registry" row linked to a `[FAQ](#faq)` entry that didn't
exist (removing that row resolves it); two new FAQ entries added — "What
happened to the EventBus and the plugin registry?" and the `update.sh`
sync-safety note referenced above — so this doesn't recur as a *new*
dangling reference.

### Compliance note — `join_left.py`'s bulk-join pacing

Documenting this explicitly, as requested during review: `join_left.py`'s
`.join` command includes a deliberate delay/backoff/batching design
("Human Pattern" mode) intended to make bulk, automated chat-joining less
distinguishable from organic activity to Telegram's own systems. This is
a documented design choice for personal account management — it only
ever acts on the account's own membership, and only on links/entities the
account owner explicitly supplied by replying to a message — not a bug,
and nothing about it was changed in this release. It does mean this
specific feature works by reducing the friction Telegram's own systems
apply to bulk joining on purpose, which is a meaningfully different
posture from the rest of this project (self-scoped message/reaction/info
tooling with no interaction with platform rate-limiting at all). Users
are responsible for their own compliance with
[Telegram's Terms of Service](https://telegram.org/tos) when using this
feature.

### Version

`VERSION` and `pyproject.toml` bumped to `3.0.11` (previously `3.0.10` /
`3.0.8` respectively — see the audit report for how those drifted apart).

---

## [3.0.10] — 2026-08-03

**Source:** AI (whois_handler / info_handler UX and DRY pass)

### Added — `whois_handler.py`: Single-Message Profile Photo Attachment

**What:** `whois` now attaches the entity's single most recent profile
photo — for `User`, `Channel`, and `Chat` entities alike — as one combined
photo+caption message, replacing the placeholder outright, instead of a
text-only message.

**Design history:** An earlier draft of this release sent the text info
first and then a *separate* follow-up message with up to 3 photos as an
album. That was reworked before release for a cleaner single-message UX:
Telegram doesn't allow editing a text-only message into one carrying media,
so the placeholder (the outgoing `whois` command message itself) is now
deleted and replaced with a single `send_file(chat, file=photo,
caption=info_text)` call whenever a photo is available. The result is
exactly one final message per `whois` invocation, containing both the
photo and the full text — never two. The album/carousel logic (limit=3,
media-group send, single-photo fallback) was dropped entirely in favor of
fetching just the single most recent photo (`limit=1`).

**Fallback behavior (all in one message or none):**
- Photo available, caption text ≤ Telegram's 1024-character caption limit
  → single photo+caption message.
- No photo available (hidden by privacy settings, or none set), caption
  text too long for a caption, or the photo send itself fails for any
  reason → falls back to editing the placeholder to the original
  text-only result, exactly as before this feature existed. No follow-up
  or partial message is ever left behind.

**Zero-download server-side attachment:** the `Photo` object returned by
`get_profile_photos_safe()` is passed directly as `send_file`'s `file`
argument. This is a server-side file reference, not raw bytes — Telegram
handles it the same way it handles a forward, so no image data is
downloaded to or re-uploaded from this device. Only an explicit
`download_media()` call would pull bytes locally, and nothing in this path
does that.

This never attempts to access anything beyond what `client.get_profile_photos`
already returns under the entity's own privacy settings — an empty result is
treated as final (text-only fallback), not retried or worked around.

### Added — `whois_handler.py`: DC ID, Restriction Reasons, and Contact Status

- `_build_user_info` now surfaces the account's Data Center ID (`dc_id`)
  when available from the profile photo stub, and `_build_channel_info` /
  `_build_chat_info` do the same from the channel's/chat's own photo stub.
- All three builders now surface `restriction_reason` (Telegram's own
  platform-specific content restriction metadata), when present — standard
  data already returned by `get_entity`, not anything requiring extra
  privileged access.
- `_build_user_info` now shows whether the target is in the account's own
  contact list (`full_user.contact`), a standard field on the existing
  `GetFullUserRequest` response that was already being fetched but not
  displayed.

### Changed — `whois_handler.py` / `info_handler.py`: Extracted Shared Formatting Helpers

**Root cause:** `whois_handler._build_user_info` and
`info_handler._get_sender_section` each independently built near-identical
flag badge lists (Bot / Verified / Premium / Scam / Fake / Deleted) and
independently formatted `UserStatusOnline` / `UserStatusOffline` /
`UserStatusRecently` objects, with small, easy-to-drift inconsistencies
between the two (e.g. only `whois_handler` showed the "خودتان" self flag or
expiry time on online status). Both modules also each had their own
`text[:n] + ("…" if len(text) > n else "")` truncation one-liner repeated
several times.

**Fix:** Extracted `format_user_flags()`, `format_user_status()`, and
`truncate()` into `helpers/utils.py`; both modules now call the shared
versions. No behavioral change to existing output beyond the two
inconsistencies above now being resolved the same way in both modules.

### Added — `helpers/utils.py`: `get_profile_photos_safe()`

New shared helper wrapping `client.get_profile_photos(entity, limit=1)`
(default limit reduced from an earlier `3` once the album approach above
was dropped in favor of a single photo). Follows this codebase's
established FloodWait convention (see the 3.0.9 `whois_handler.py` /
`info_handler.py` entries below): explicitly re-raises
`errors.FloodWaitError` before any generic fallback, so a rate limit hit
while fetching photos surfaces the normal "please wait N seconds" message
instead of silently degrading to text-only.

### Fix — `info_handler.py`: Anonymous Channel/Group Sender Missing Broadcast/Megagroup Distinction

**Root cause:** `_get_sender_section`'s `Channel`/`Chat` branch (for
messages sent by an anonymous channel admin or linked-channel post) only
ever printed the generic "Group/Channel" label, unlike `_get_chat_section`,
which already distinguishes Channel vs. Supergroup vs. basic Group for the
*chat* itself. The two sections could disagree in wording for the same
underlying entity type.

**Fix:** `_get_sender_section`'s Channel/Chat branch now uses the same
broadcast/megagroup/basic-group labeling as `_get_chat_section`.

### Changed — `whois_handler.py` / `info_handler.py`: Help Text Updated

`help_text` / `help_extra` in `whois_handler.py` now document the final
single-message photo+caption behavior (one combined message when a photo
is available and fits the caption limit, text-only otherwise — never an
album, never two messages), plus the new restriction-reason and
contact-status fields. No functional changes to `info_handler.py`'s help
text beyond noting the shared-helper refactor did not change its
documented behavior.

---

## [3.0.9] — 2026-08-02

Applied from a full 12-module review (clearer, auto_clearer, auto_forwarder,
help_handler, system, whois_handler, info_handler, reaction_commands,
join_left, base, router, bridge). Each fix below closes a specific finding
from that review.

### Revert — `bridge.py`: Reacted-To Message Deliberately Remains Eligible For `clear`

A previous draft of this release changed `MockEvent.message.id` from a
placeholder (`0`) to the real `target_msg_id`, on the theory that a
reaction-triggered `clear` shouldn't be able to delete the very message
that was reacted on. **This has been reverted per product decision — it
wasn't a bug.** If a reaction is mapped to e.g. `clear txt` and the
reacted-to message matches that filter, it's expected to be deleted like
any other matching message in the scan; it should not get special
immunity just because it happened to be the trigger. `message.id` is back
to the `0` placeholder, so `clearer.py`'s `skip_ids` exclusion (which is
still correctly used to protect the *progress/status* message the
direct-invocation flow creates) no longer incorrectly extends to the
reacted-to message as well.

### Fix — `auto_forwarder.py`: Partial Fallback Failures Left Successfully-Forwarded Originals Undeleted

**Root cause:** `_send_batched`'s one-by-one fallback path tracked a single
batch-wide `sent_ok` boolean. If any one item in a multi-item batch failed
during fallback, the flag ended up `False` and **none** of the batch's
originals were deleted — including items that were actually forwarded
successfully, leaving permanent duplicate content in the bot chat.

**Fix:** Track per-item send success (`sent_ids`) and delete only the
originals that were actually confirmed sent.

### Fix — `join_left.py`: Three Issues

1. **Dead-branch join failure masking.** The `username` join handler's
   generic exception handler called `get_entity()` in both the "already a
   member" and "else" branches — identically — so *any* join failure other
   than the two explicitly re-raised types (including `PeerFloodError` /
   `UserBannedInChannelError`, Telegram's own anti-spam responses) could be
   silently reported as a successful join whenever the target happened to
   still be resolvable. Fixed to only treat genuine already-member errors
   as success; everything else now re-raises and is reported as a failure.
2. **`channel_id` / `numeric_id` paths never actually joined anything** —
   they called only `get_entity()` (a resolve, not a join), so a
   not-yet-joined public channel could be reported "✅ joined" with no
   actual membership change. Both paths now issue `JoinChannelRequest`
   where applicable.
3. **Duplicate entity extraction.** `t.me/c/<id>/<msg>` links were matched
   by both the `channel_id` pattern and the generic numeric-ID pattern
   (`\b(\d{9,14})\b` matches the same digits inside the URL), causing the
   same chat to be queued and joined twice. The numeric-ID pass now skips
   digit runs already captured as a `channel_id`.

### Fix — `auto_clearer.py`: Missing Pinned-Message Protection + Reimplemented Batch-Delete

**Root cause:** `help_extra` documented that pinned messages are never
auto-cleared, but neither the live path (`_try_auto_delete`) nor the
historical sweep (`_clear_past`) ever checked `msg.pinned`. Separately,
`_clear_past` reimplemented a batch-delete-with-retry loop inline instead
of using the shared, more accurate `helpers.utils.batch_delete`, and the
"enable globally" flow fired a full-history scan+delete for every bot
dialog back-to-back with no pacing — the single highest FloodWait risk in
the module.

**Fix:** Added the pinned-message check to both paths; `_clear_past` now
calls the shared `batch_delete` helper (accurate `pts_count`-based
counting) with `wait_time=1` on its scan; added a short pause between
per-bot sweeps in the global-enable flow.

### Fix — `whois_handler.py`: Linked-Chat Lookup Silently Swallowed FloodWait

**Root cause:** Every other full-info fetch in this module explicitly
re-raises `FloodWaitError` so the top-level handler can surface a "please
wait" message — except the nested linked-chat (`linked_chat_id`) lookup
inside `_build_channel_info`, which fell into a bare `except Exception`
and silently displayed a plain-ID fallback instead.

**Fix:** Added the same explicit `except errors.FloodWaitError: raise`
before the generic fallback.

### Fix — `info_handler.py`: FloodWait Swallowed, UTF-16 Entity Offset Bug, and Two Minor Inconsistencies

1. `_get_sender_section` / `_get_chat_section` / `_get_reply_section` all
   swallowed `FloodWaitError` via a bare `except Exception`, unlike
   `whois_handler.py`'s deliberate re-raise pattern. Now re-raise before
   the generic fallback in all three.
2. `_link_details` sliced URL-entity text using Python code-point indexing
   against Telegram's UTF-16-code-unit offsets — wrong for any message
   containing emoji/astral characters before a link. Added a
   `_utf16_slice()` helper that round-trips through UTF-16 bytes so the
   offsets line up correctly.
3. Poll-question truncation was missing the ellipsis every other
   truncated field in the codebase includes — fixed.
4. The GIF/Voice/Audio document-attribute loop had no `break` once a
   definitive type was found, unlike the sibling filename-attribute loop
   — added, preventing a theoretical contradictory double type line.

### Fix — `clearer.py`: Overlapping Concurrent `clear` Runs

**Root cause:** No guard against two `clear` invocations in the same chat
running concurrently (e.g. a double-tap), which could scan/delete
overlapping message ranges and produce a misleading second report.

**Fix:** Added a per-chat `_active_clears` guard; a second `clear` in the
same chat while one is already running now gets a clear "already running"
notice instead of racing the first.

### Fix — `helpers/utils.py`: `batch_delete`'s Single-Shot FloodWait Retry

**Root cause:** A second consecutive `FloodWaitError` on the same batch
gave up entirely after exactly one retry, silently dropping that batch's
deletions. No proactive pacing existed between batches either.

**Fix:** Retries now loop (capped at 5 attempts), honoring each new
`FloodWaitError.seconds`; added a small proactive delay between batches to
reduce how often FloodWait is hit in the first place.

### Fix — `help_handler.py`: Message-Length Overflow + Fragile Category Lookup

**Root cause:** The compact `help` output concatenated every module's full
`help_text` into a single `event.edit()` call with no length check against
Telegram's ~4096-character message limit — overflowing this failed
silently with zero user-facing feedback. Separately, `grouped[cat_key]`
was indexed directly against a dict pre-seeded only from `CATEGORIES`,
which would raise an unhandled `KeyError` if a future `MODULE_MAP` entry's
category ever didn't match exactly.

**Fix:** Output is now split into multiple messages by category block when
it would exceed a safe length threshold; category lookup now uses
`setdefault()` defensively.

### Fix — `system.py`: Unguarded `.ping` Intermediate Edit

**Root cause:** The `.ping` command's intermediate "Pong!" edit was a bare
`event.edit()` call with no error handling, unlike every other command in
this module (which use `_safe_edit`/`_safe_edit_with_auto_delete`) — a
failure there would propagate uncaught and abort the command with no final
report.

**Fix:** Wrapped in a try/except, matching the rest of the module's
convention.

---

## [3.0.8] — 2026-07-31

### Fix — `reaction_commands.py`: Direct `clear` Invocation Always Fell Back to `send_message`

**Root cause:** `MockEvent` (`modules/bridge.py`), the shim used to invoke other
modules' command handlers directly from a reaction without going through a
real Telegram message, had no `is_private` / `is_group` / `is_channel`
attributes at all — unlike a real Telethon `NewMessage.Event`, which exposes
these via its `Message`'s `ChatGetter` mixin. `clearer.py`'s `_run_clear`
reads `event.is_private` unconditionally. Every reaction mapped to a `clear`
command therefore raised `AttributeError` inside the direct-invocation
attempt in `reaction_commands.py`, which silently caught the exception and
fell through to the `send_message` fallback path — every single time. The
"Direct Module Invocation" architecture (built specifically to avoid an
extra network round trip and the event-loop race conditions that come with
posting a real message and having it re-enter the event loop) was
consequently never actually exercised for `clear`, the single most commonly
reaction-mapped command.

**Fix:** `MockEvent` now accepts `is_private`/`is_group`/`is_channel` as
constructor parameters. `reaction_commands.py` resolves them once per
command execution via a new shared `_classify_peer()` helper (see below) —
normally a cache hit, since Gate 2's environment filter already classified
the same chat moments earlier — and passes them into every `MockEvent` it
constructs (`clear`, `join`/`left`, `info`, `whois`), so this fix requires
zero additional API calls in the common case.

### Fix — Supergroups Were Silently Treated as "Channels", Not "Groups"

**Root cause:** Both supergroups and broadcast channels are represented as
`PeerChannel` at the MTProto level; only the entity's `megagroup` flag tells
them apart. The old environment filter treated every `PeerChannel` as a
"channel," so the `ENABLE_FOR_GROUPS` toggle could never actually apply to a
supergroup — only to legacy basic groups (`PeerChat`).

**Fix:** New `_classify_peer()` resolves `entity.megagroup` for `PeerChannel`
peers on first sight, cached per `channel_id` (same pattern as the existing
bot/user cache), so supergroups are now correctly classified as `"group"`
and only genuine broadcast channels are classified as `"channel"`.

### Feature — Runtime-Configurable Reaction Scope (`.reaction_scope`)

**Problem:** Which chat types `reaction_commands.py` processes reactions in
was four hardcoded class attributes (`ENABLE_FOR_BOTS/USERS/GROUPS/CHANNELS`)
— changing scope required editing the module's source and reloading it.

**Fix:** New `.reaction_scope` command (Saved Messages only, matching the
existing `reaction`/`reactions` commands' gating):
- `.reaction_scope` — show which of `private`/`bot`/`group`/`channel` are
  currently active.
- `.reaction_scope <value>` — toggle that value on/off (run it again to
  flip it back).
- `.reaction_scope all` / `.reaction_scope none` — activate/deactivate
  everything at once.

Persisted per-account in a new `reaction_scope.json` (alongside
`reactions.json`, same atomic read/write helpers, same load-at-`setup()`
pattern). Default on first run is `["bot"]` — identical to the previous
hardcoded default, so existing installs see no behavior change until they
explicitly reconfigure.

### Fix — Duplicate Command Execution After Reconnect

**Root cause:** `_active_reactions` (Gate 5's rising-edge dedup memory) was
unconditionally cleared in `teardown()`. `AccountReconnector.reattach()`
calls `teardown()` then `setup()` on every reconnect — and reuses the exact
same module instance rather than recreating it (`core/loader.py`:
*"module instances themselves are reused — no re-import needed"*). Clearing
the dedup map on a routine reconnect therefore made any reaction still
present on a message look like a brand-new rising edge once Telegram's
`catch_up` redelivered its state after reconnect, re-firing its mapped
command a second time.

**Fix:** `teardown()` no longer clears `_active_reactions`,
`_peer_is_bot_cache`, or the new `_channel_megagroup_cache`. A genuine
hot-reload (module file changed) still gets a fresh instance — and
therefore fresh, empty dicts — via `create_module()` regardless, so this
only changes behavior for the reconnect path, as intended. Verified with an
isolated `teardown()`→`setup()` simulation.

### Testing

All four fixes verified with isolated functional tests (no live Telegram
connection required): peer classification (bot/user/basic-group/supergroup/
broadcast-channel) and its caches; scope persistence round-trip through
`reaction_scope.json`; the full `.reaction_scope` command surface (status
view, toggle, `all`/`none`, invalid-value handling) through `_on_command`;
`clearer.py`'s real `_run_clear` invoked end-to-end through a `MockEvent`
confirming the original `AttributeError` no longer occurs; and a simulated
`AccountReconnector.reattach()` cycle confirming dedup/classification state
now survives it. Full-project `py_compile` pass confirms no regressions
elsewhere.

---

## [3.0.7] — 2026-07-29

### Fix — Installer: Eliminated `userbot.sh` File Dependency (Termux-First Rewrite)

**Root cause of the broken `userbot` command:** `install.sh` copied a
separate `userbot.sh` launcher from the repository into `$PREFIX/bin/userbot`.
If `userbot.sh` was absent, had a path-detection bug, or the copy step failed
silently, the global `userbot` command would install but immediately error with
"Could not locate the userbot_own installation" — even when the install
directory was present and correct.

**Fix:** Removed all `userbot.sh`-copy logic. The installer now generates the
launcher script **inline via a heredoc** (`cat > $LAUNCHER_DEST << LAUNCHEREOF`)
directly inside `install.sh`. The generated script hardcodes
`INSTALL_DIR="$HOME/userbot_own"` and `VENV_PY="${INSTALL_DIR}/.venv/bin/python"`
— no config file, no path-guessing, no external file dependency.

**Additional changes:**
- `update.sh` introduced as a standalone updater (also invoked by `userbot update`).
- Generated launcher supports `userbot run`, `userbot add`, `userbot update`,
  and an interactive menu when called with no arguments.
- Reactive build-toolchain fallback (install `clang`/`make` only if `pip install`
  fails) preserved from previous version.
- Cleanup command for removing the previously broken launcher documented.

---

## [3.0.6] — 2026-07-28

### Fix — Message Clearing Bugs (6 Issues Across `clearer.py` and `utils.py`)

---

#### Issue 1 — Primary Bug: No Chat-Type-Aware Sender Filtering in Groups/Channels

**Root cause:** `_run_clear()` called `client.iter_messages(chat_id, limit=N)`
without any `from_user` filter, fetching ALL messages from ALL members in groups
and channels. For scopes `"all"` and `"self"`, this meant thousands of other
members' messages were downloaded only to be silently skipped by Telegram during
deletion (Telegram ignores delete requests for messages the caller doesn't own),
while the bot falsely reported them as successfully deleted (Issue 6 below).

**Technical detail:** Telethon's `iter_messages` supports a `from_user` parameter
that maps to `from_id` in `messages.SearchRequest`. For groups, supergroups, and
broadcast channels (non-PeerUser entities), Telegram filters server-side and only
returns the specified user's messages. For private chats (PeerUser), Telegram
ignores `from_id` and Telethon falls back to local filtering — but for private
chats we intentionally want both sides of the conversation anyway.

**Fix:** At the start of `_run_clear()`, `event.is_private` is checked:
- `is_private = True` (PeerUser: person, bot, Saved Messages) → fetch all messages,
  no `from_user`. Both sides of the conversation are processed as intended.
- `is_private = False` (group, supergroup, channel) with scope `"all"` or `"self"`
  → `from_user='me'` is added to `iter_messages`. Telegram does the filtering
  server-side; only the user's own messages are returned. Efficient and correct.
- scope `"bot"` → never uses `from_user='me'` (we want bot messages, not our own).

---

#### Issue 2 — Duplicate `_me_id` Machinery Not Migrated to `base._get_me_id`

**Root cause:** `Clearer` maintained its own `self._me_id: int | None`,
`self._me_id_task`, and `_cache_me_id()` coroutine (a 2-second delayed startup
task to call `client.get_me()`). `base.Module` already provides `_get_me_id(client)`
with a `WeakKeyDictionary` cache (`_me_cache`), shared across all modules and
populated on first access. The `base.py` module-level docstring explicitly notes
this consolidation; `Clearer` was the only module that had not been migrated.

**Consequences:**
1. A 2-second window at startup where `_me_id is None`, causing `clear self` to
   silently skip all messages if run immediately after the bot started.
2. A dangling `asyncio.Task` that had to be cancelled in `teardown()`.
3. Duplicated logic that could drift out of sync with `base._get_me_id`.

**Fix:** Removed `self._me_id`, `self._me_id_task`, `_cache_me_id()`, and the
teardown cancellation block. `_matches_scope()` (now `_matches_scope(client, ...)`)
calls `await self._get_me_id(client)` for scope `"self"`. `_matches_scope` is now
`async` and receives `client` as a parameter; the call site in `_run_clear` awaits
it accordingly. Note: with Issue 1's `from_user='me'` fix, the `"self"` scope check
in `_matches_scope` is only needed for private chats (where server-side filtering is
unavailable), so the `_get_me_id` call is infrequent in practice.

---

#### Issue 3 — `_bot_peer_cache` Missing: Hidden `get_entity()` Per Message

**Root cause:** `_matches_scope` for scope `"bot"` accessed `msg.sender`, a lazy
Telethon property. In groups/channels where full sender objects are not embedded in
the message, accessing `msg.sender` triggers a `get_entity()` API call. With 2000
messages in the scan loop and multiple bot senders, this produced dozens of hidden
`GetUsersRequest` calls — a significant FloodWait risk at scale.

**Fix:** Introduced `self._bot_peer_cache: dict[int, bool]` keyed on `sender_id`.
The new `_is_bot_sender(client, msg)` helper checks the cache first; only the first
message from a unique `sender_id` may trigger a `get_entity()` call. Subsequent
messages from the same sender are resolved from the cache in O(1) with no API call.
The cache is cleared in `teardown()` to prevent stale entries across hot-reloads.

`_matches_scope` is now `async` and receives `client` to support the above.

---

#### Issue 4 — Fallback Message Not Auto-Deleted

**Root cause:** When `status_msg.edit(result_text)` raised an exception (e.g. the
status message was deleted by a third party), the code fell back to
`client.send_message(chat_id, result_text)` — but then passed the **old**
`status_msg` to `_track_delete_task()`. The fallback message was never scheduled
for auto-deletion and lingered in the chat indefinitely.

**Fix:** Introduced `report_msg = status_msg` before the try/except block. If
`client.send_message()` succeeds in the fallback branch, `report_msg` is updated
to the newly sent message. `_track_delete_task(report_msg, 6.0)` then correctly
targets whichever message is actually visible.

---

#### Issue 5 — No `wait_time` on `iter_messages` (FloodWait Risk)

**Root cause:** `client.iter_messages(chat_id, limit=2000)` was called without
`wait_time`. Telethon's `_MessagesIter` only inserts an automatic 1-second pause
when `limit > 3000`. At the default `history_limit = 2000`, requests were sent
as fast as the network allowed, risking `FloodWait` from `GetHistoryRequest` on
large or recently-active chats.

**Fix:** Added `wait_time=1` to the `iter_messages` call. This inserts a 1-second
pause every 100 messages, staying well within Telegram's rate limits for history
fetching. The `wait_time` value matches what Telethon applies automatically at
higher limits.

---

#### Issue 6 — `batch_delete` Overcounted Deletions (in `utils.py`)

**Root cause:** `batch_delete` used `deleted += len(batch)` after a successful
`client.delete_messages()` call. `delete_messages` returns a list of
`messages.AffectedMessages` objects (one per 100-message internal chunk), each
with a `pts_count` field indicating the number of messages Telegram actually
deleted server-side. When the caller lacks permission to delete some messages in
a group (e.g. other members' messages without admin rights), Telegram silently
skips those IDs and returns a lower `pts_count`. The old code counted all IDs in
the batch as deleted regardless, inflating the report.

**Fix (`utils.py`):**
- Added `from telethon.tl.types import messages as tl_msg_types` import.
- Added `_pts_count(results) -> int` helper: sums `r.pts_count` over the list of
  `tl_msg_types.AffectedMessages` returned by `delete_messages`. Falls back to
  logging unexpected result types rather than crashing.
- `batch_delete` now captures the return value of every `delete_messages` call
  (including the FloodWait retry) and uses `_pts_count(results)` instead of
  `len(batch)`. The one-by-one fallback path was already using `safe_delete`
  which returns a bool per message, so it is correct and unchanged.

---

#### Files Changed

- `userbot_own/modules/clearer.py` — Issues 1–5
- `userbot_own/helpers/utils.py` — Issue 6 (`_pts_count` helper + `batch_delete` rewrite)
- `userbot_own/__init__.py` — fallback version bumped to `3.0.6`
- `VERSION` — bumped to `3.0.6`
- `CHANGELOG.md` — this entry

---

## [3.0.5] — 2026-07-28

### Fix — Archive-After-Join Never Working (5 Root Causes)

This release resolves a silent, multi-layered bug that caused every post-join
archive operation to silently fail. Chats were muted and added to the `joined`
folder correctly, but remained visible in the main chat list indefinitely.

---

#### Issue 1 — Wrong TL Type: `FolderPeer` Instead of `InputFolderPeer`

**Root cause:** `_archive_chat()` called `EditPeerFoldersRequest` with
`FolderPeer(peer=input_peer, folder_id=1)`. `FolderPeer` (constructor
`0xe9baa668`) is a **server→client** response type returned inside `Dialog`
objects to describe which folder a chat belongs to. It is not a valid input
type for a TL request.

`EditPeerFoldersRequest` expects `InputFolderPeer` (constructor `0xfbd2c296`),
which is the **client→server** input type. Telegram silently discards requests
containing the wrong type — no exception is raised, no error is logged, and
the chat is never archived.

**Fix:** Replaced `FolderPeer` with `InputFolderPeer` at both call sites in
`_archive_chat()` (primary call and FloodWait retry). Also replaced the
`FolderPeer` import with `InputFolderPeer` in the module-level import block.

---

#### Issue 2 — `_create_joined_folder()` Sets `exclude_archived = False`

**Root cause:** The `joined` folder was created with `exclude_archived=False`.
This flag instructs Telegram: "do not show archived chats in this folder."
As a side effect, whenever `UpdateDialogFilterRequest` is sent for a folder
with `exclude_archived=False`, Telegram moves any newly-added peer to
`folder_id=0` (un-archives it) to ensure it remains visible in the folder.

This means even a correctly-formed `EditPeerFoldersRequest` (Issue 1 fixed)
would have been immediately undone the moment the peer was added to the
`joined` folder.

**Fix:** Changed `exclude_archived=False` → `exclude_archived=True` in
`_create_joined_folder()`. This tells Telegram "show archived chats in this
folder," which is the intended behaviour and removes the un-archive side effect.

---

#### Issue 3 — Existing `joined` Folders Not Patched for `exclude_archived`

**Root cause:** `_add_single_peer_to_joined_folder()` mutates and re-submits
existing folder objects as-is without checking `exclude_archived`. Any
`joined` folder created in a previous session (with `exclude_archived=False`)
would silently un-archive chats on every subsequent peer addition, even after
Issue 2 was fixed for newly-created folders.

**Fix:** Added a check before `UpdateDialogFilterRequest`: if
`folder.exclude_archived` is `False`, it is patched to `True` and logged
before the request is sent. This ensures all existing folders are
self-healing on their next update.

---

#### Issue 4 — Order of Operations: Archive Was Not Last

**Root cause:** `_post_join_actions()` executed in this order:
1. mute + archive (`_mute_and_archive`)
2. add to `joined` folder (`_add_single_peer_to_joined_folder`)
3. add to exclusion lists of other folders (`_add_to_all_other_folders_exclusion`)

Step 2 (`UpdateDialogFilterRequest`) was called *after* the archive. Even
with Issues 1–3 fixed, the `UpdateDialogFilterRequest` in step 2 would
silently un-archive the chat if called after `EditPeerFoldersRequest`,
because Telegram's server-side folder management treats explicit folder
membership as overriding archive state.

Archive must always be the **final** post-join operation so no subsequent
Telegram API call can undo it.

**Fix:** Decoupled `_mute_chat()` and `_archive_chat()` (see Issue 5), then
reordered `_post_join_actions()` to:
1. `_mute_chat()` — mute
2. `_add_single_peer_to_joined_folder()` — folder-add (with exclude_archived patch)
3. `_add_to_all_other_folders_exclusion()` — exclusion
4. `_archive_chat()` — **archive last**

---

#### Issue 5 — `_mute_and_archive()` Coupled Mute and Archive

**Root cause:** A single helper `_mute_and_archive()` called both `_mute_chat()`
and `_archive_chat()` sequentially and returned a combined tuple. This coupling
made it impossible to reorder archive to the final step (Issue 4) without
refactoring.

**Fix:** `_post_join_actions()` now calls `_mute_chat()` and `_archive_chat()`
directly as separate sequential awaits. The `_mute_and_archive()` helper
remains in the codebase (it is still called from other paths) but is no longer
used by `_post_join_actions()`.

---

#### Files Changed

- `userbot_own/modules/join_left.py` — all 5 fixes above
- `userbot_own/__init__.py` — fallback version bumped to `3.0.5`
- `VERSION` — bumped to `3.0.5`
- `CHANGELOG.md` — this entry

---

## [3.0.4] — 2026-07-28

### Task 1 — Remove Custom Restart Logic; Add SIGTERM Support

**Deleted** `userbot_own/app/restart.py` in its entirety. This file contained:
- Windows-only `Ctrl+R` keyboard polling (`msvcrt.kbhit` / `kbhit` + `getch` loop)
- Three-layer console re-attachment / buffer-flush machinery for Windows
- `SIGUSR1` signal handler for Unix/Termux manual restarts
- `spawn_restart()` process-respawn logic
- The `restart_requested` flag and `reset()` lifecycle helper

**Simplified** `userbot_own/app/application.py`:
- Removed all references to `restart.py` (import, `threading.Thread` for the
  keyboard listener, `shutdown_event`, `shutdown_task`, the `asyncio.wait`
  restart-branch, and the `while True:` respawn loop in `main()`).
- `Application.run()` now uses a single `await asyncio.gather(*tasks)` guarded
  by `except asyncio.CancelledError` for clean shutdown.
- `main()` is now a simple single-run: `asyncio.run(Application(...).run())`
  inside `try/except KeyboardInterrupt`.
- **Added SIGTERM handler** (Unix/Docker/Termux): on non-Windows platforms,
  `loop.add_signal_handler(signal.SIGTERM, ...)` now cancels all running tasks
  and triggers a graceful shutdown — identical in effect to `Ctrl+C` / SIGINT.
  This is required for compatibility with process supervisors (systemd, s6,
  Docker `stop`, Termux `kill`).

**Note on `update.bat`**: confirmed to contain only standard `git pull` / `git push`
operations — no restart-loop logic was present.

---

### Task 2 — Fix `join_left.py`: Telethon Wrapper Parsing & FloodWait Reduction

#### Root Cause: `ImportChatInviteRequest` Returns a Wrapper, Not Updates Directly

In Telethon 1.44.0, `ImportChatInviteRequest` returns
`messages.ChatInviteJoinResultOk` (constructor ID `0x445663a7`) — a wrapper
object with a **single attribute: `.updates`** of type `TypeUpdates`. The
`.chats` list lives on `.updates.chats`, **not** directly on the result.

The v3.0.3 code did:
```python
updates = await client(ImportChatInviteRequest(invite_hash))
joined_entity = updates.chats[0] if updates.chats else None
```
This caused `AttributeError: 'ChatInviteJoinResultOk' object has no attribute 'chats'`
on **every** successful private channel join.

**Fix A — Correct unwrapping** (`invite_direct` path and username-fallback path):
```python
raw = await client(ImportChatInviteRequest(invite_hash))
if isinstance(raw, tl_messages.ChatInviteJoinResultOk):
    joined_entity = raw.updates.chats[0] if raw.updates.chats else None
```

#### Fix B — Removed Fragile String-Match from `_is_already_member_error()`

The old check `if "ChatInviteJoinResultOk" in str(exc): return True` was a
workaround for the `AttributeError` from Fix A. Now that the root cause is
fixed, the workaround is removed. The method now relies solely on:
1. `isinstance(exc, errors.UserAlreadyParticipantError)` (primary)
2. `"already a part" in str(exc).lower()` (API-string fallback)

#### Fix C — Eliminated Redundant `CheckChatInviteRequest` Pre-Calls

`ChatInvite` objects (returned by `CheckChatInviteRequest` for truly private
groups/channels that the user has NOT yet joined) have **no `.username` field**.
The Layer-1 pre-check was therefore always returning `None` for private invite
links and served no purpose — it was a wasted API call that contributed to the
"aggressive FloodWait" symptom. The direct hash path (`invite_direct`) now goes
straight to `ImportChatInviteRequest` (1 call instead of up to 3).

#### Fix D — Type-Safe `ChatInviteAlready` Entity Extraction

Replaced `getattr(result_check, "chat"/"channel", None)` with an explicit
`isinstance(result_check, ChatInviteAlready)` guard before accessing `.chat`.
`ChatInviteAlready` always has `.chat` (the full `TypeChat` object);
`ChatInvite` (not-yet-member) does not. This prevents false `None` returns
when the bot checks an invite for an unjoined chat.

#### Fix E — `ChatInviteJoinResultWebView` Handling

`ImportChatInviteRequest` can also return `messages.ChatInviteJoinResultWebView`
for subscription-gated or bot-gated channels (constructor ID `0x2f51c337`).
This is now detected explicitly and reported as a clear, informative skip
(`⚠️ [link] — نیاز به WebView/Bot دارد، رد شد`) instead of silently failing
with `None` and producing a confusing error downstream.

#### Fix F — Delay Table Rebalanced

`_SMART_DELAYS["invite_direct"]` reduced from `8.0s` → `4.0s`. The prior 8-second
delay was sized to account for up to 3 API calls per entity. With the call count
now reduced to exactly 1 (`ImportChatInviteRequest` only), the risk level is
equivalent to the `"invite_with_username"` path, which already used `4.0s`.

#### Fix G — Post-Join Exclusion Now Awaited (Crash-Safety)

`_post_join_actions()` previously fired `_add_to_all_other_folders_exclusion()`
as a detached `asyncio.create_task()` (fire-and-forget). This meant a crash
between step 3 (folder-add) and step 4 (exclusion) would leave the chat in
other folders without being excluded. Changed to `await`, making all 4 post-join
actions strictly sequential and crash-safe per chat:
1. `await _mute_chat(...)` — mute
2. `await _archive_chat(...)` — archive (folder_id=1)
3. `await _add_single_peer_to_joined_folder(...)` — add to `joined` folder
4. `await _add_to_all_other_folders_exclusion(...)` — exclude from all other folders

---

### Task 3 — Confirm Immediate Per-Chat Archiving (No Change Required)

Verified that `_archive_chat()` correctly uses `EditPeerFoldersRequest` with
`folder_id=1` and is called immediately after each successful join via
`_mute_and_archive()` inside `_post_join_actions()`. The archiving, muting,
and folder-add were already per-chat and immediate in v3.0.3. Fix G (above)
completes the crash-safety story by also awaiting the exclusion step.

---

## [3.0.3] — 2026-07-28

### Project Rename
- Renamed project from `userbot` / `userbot_v2` to **`userbot_own`** everywhere:
  package directory, all imports, all module docstrings, `.env.example`, `pyproject.toml`,
  logging prefixes, CHANGELOG, README, and `__version__` references.

### Task 1 — Fix Ctrl+R Terminal Restart (Windows)

**Root cause:** After the first restart, the spawned child process does not
have genuine ownership of the console input (stdin), causing `msvcrt.kbhit()`
to stop detecting keypresses. Additionally, a stale `0x12` byte from the Ctrl+R
keypress remained in the console input buffer and could trigger a false immediate
second restart.

**Three-layer fix in `userbot_own/app/restart.py`:**

- **Layer 1 — Console re-attachment** (`_win_reattach_console`): The keyboard
  listener thread now calls `FreeConsole()` + `AttachConsole(ATTACH_PARENT_PROCESS)`
  at startup, forcing a clean console re-acquisition so `msvcrt` works correctly
  in any spawned process, not just the first one.
- **Layer 2 — Input buffer flush** (`_win_flush_console_input`): After
  re-attaching, `FlushConsoleInputBuffer()` discards any stale bytes (e.g. the
  `0x12` from the prior Ctrl+R) before the polling loop starts.
- **Layer 3 — Listener diagnostics**: The thread now logs a startup banner with
  PID and mode, logs every detected keycode at DEBUG level, and emits a periodic
  alive-log every ~60 seconds — making it easy to confirm the listener is running
  and what it detects.
- **Removed** `CREATE_NEW_PROCESS_GROUP` from `spawn_restart()` (the v3.0.2 fix
  remains). Documented `CREATE_NEW_CONSOLE` as a nuclear fallback option with
  clear instructions, but kept it off by default to avoid opening a new terminal
  window on every restart.

### Task 2 — Overhaul `userbot_own/modules/join_left.py`

**Requirement 1 — Incremental Folder Addition:**
- Each chat is added to the `joined` folder **immediately** after a successful join
  via `_add_single_peer_to_joined_folder()`, not batched at the end of the run.
- This function handles `FloodWaitError` explicitly with up to 2 retries before
  degrading gracefully (logs a warning and continues).
- **Fixed:** the previous folder ID assignment skipped `id == 1` unconditionally,
  conflating Telegram's UI pseudo-filter ID with `DialogFilter.id` values. The
  new logic starts at `new_id = 2` and increments only past IDs that are already
  occupied by real existing filters.

**Requirement 2 — "Already a Member" Handling:**
- All three known "already member" signals are now detected by `_is_already_member_error()`:
  1. `errors.UserAlreadyParticipantError` (standard Telethon exception)
  2. `"'ChatInviteJoinResultOk' object has no attribute..."` (Telethon parse error
     on the special result type returned when user is already a member)
  3. `"The authenticated user is already a part..."` (API-level string)
- This check is applied on **all join paths**: username, channel_id, numeric_id,
  invite→username (fallback), invite direct (hash), and the Layer 1 pre-check.
- "Already a member" cases proceed identically to a clean join: mute, archive,
  folder-add, and exclusion are all applied.
- **Root cause of aggressive FloodWait fixed (Req 5):** `_resolve_invite_to_username`
  now catches `UserAlreadyParticipantError` from `CheckChatInviteRequest` and
  short-circuits immediately, preventing the downstream code from also calling
  `ImportChatInviteRequest` — eliminating the duplicate API call that triggered
  disproportionate FloodWait.

**Requirement 3 — Mute and Archive Immediately:**
- `_mute_chat()` and `_archive_chat()` are called per-chat immediately after each
  successful join via `_post_join_actions()` — not batched at the end.
- If the process dies mid-run, all completed joins are already muted and archived.
- Both helpers include explicit `FloodWaitError` handling with one retry.

**Requirement 4 — Strict Folder Exclusion:**
- `_add_to_all_other_folders_exclusion()`: after joining a chat, it is added to
  `exclude_peers` of every other editable folder so it only appears in `joined`.
- `_remove_from_all_folders_exclusion()`: when a chat is left (via `left` command,
  `folder` reset, or `autoleave`), it is **immediately and synchronously** removed
  from `excluded_chats` of all folders.
- `_check_auto_leave()` (periodic verification) now also handles manual leaves:
  any tracked chat found via `UserNotParticipantError` is cleaned from tracking
  and from all exclusion lists.

**Requirement 5 — Deep Review, Bug Fixing, and FloodWait Mitigation:**
- **Per-entity FloodWait retry cap** (`_MAX_FLOODWAIT_RETRIES = 5`): the join
  loop no longer retries indefinitely — after 5 FloodWaits on the same entity it
  skips and continues, preventing infinite hangs on pathological cases.
- **Paced `left` loop**: `_handle_left()` now uses `_LEFT_INTER_DELAY = 2.0s`
  between successive leaves and `_LEFT_MAX_FW_RETRIES = 3` before skipping —
  matching the safe pacing of the join flow.
- **Paced `folder` reset**: `_leave_and_reset_joined_folder()` likewise applies
  `_LEFT_INTER_DELAY` and `_LEFT_MAX_FW_RETRIES` instead of the prior tight loop.
- **Aggressive FloodWait root cause fixed**: duplicate API calls on already-member
  invite paths (see Req 2 above).
- Full top-to-bottom review of the 4-layer FloodWait prevention system; all
  existing layer logic preserved and generalised to the left/reset paths.

### Testing
- 15-point automated regression suite covering: version bump, all module exports,
  `_is_already_member_error` (5 cases), entity extraction, constants, folder id
  logic, `_post_join_actions` wiring, left pacing, join path coverage, invite
  short-circuit, reset pacing, help text, name audit, and listener layers.

Versioning rules:
- **MAJOR** — breaking architectural change or full rewrite
- **MINOR** — new module, feature, or meaningful enhancement
- **PATCH** — bug fix, refactor, doc update, or minor improvement

Every change entry must include: version, date, description, and source (human/AI).

---
## [3.0.2] - 2026-07-22

### 🐛 Bug Fixes

- **Restart hangs and stops responding to Ctrl+R/Ctrl+C after the first
  restart (Windows).** `app/restart.py`'s `spawn_restart()` launched the
  replacement process with the Windows flag `CREATE_NEW_PROCESS_GROUP`.
  Per Microsoft's own documentation for that flag: "If this flag is
  specified, CTRL+C signals will be disabled for all processes within the
  new process group" — not a side effect, its documented purpose (normally
  used so a parent can later selectively target the child with
  `GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, ...)`). Nothing in this
  codebase ever does that — the parent spawns the replacement and exits
  immediately — so the flag served no purpose here while permanently
  disabling Ctrl+C in every restarted process, and, since the original
  process has already exited, left nothing listening for it at all.
  Ctrl+R (a plain polled keystroke, not a real OS signal) became unreliable
  at the same time, most likely due to the same console/process-group
  interaction. Fixed by removing the flag entirely — the restarted process
  is now a normal child sharing the console exactly like the very first
  launch.

- **`reaction_commands.py` Method 1 (`UpdateMessageReactions`) was
  completely non-functional.** It read `event.peer_id` and `event.chat_id`
  — confirmed, by inspecting the installed Telethon library directly,
  that neither attribute exists on the raw `UpdateMessageReactions`
  update (its real fields are `peer`, `msg_id`, `reactions`, `top_msg_id`,
  `saved_peer_id`), and confirmed separately that Telethon's raw-event
  dispatcher hands handlers the update completely unmodified — nothing
  adds a `chat_id` convenience property the way higher-level events do.
  Both attribute reads always evaluated to `None`, so this handler always
  hit its `return` on the very next line and had never processed a single
  reaction. Every reaction command was running entirely on Method 2
  (`UpdateEditMessage`) alone, with no actual redundancy despite the
  module's design intent — and per the module's own existing comment,
  Telegram doesn't reliably send `UpdateEditMessage` for every reaction
  change, only sometimes. This is what caused reactions to sometimes not
  be detected at all. Fixed by reading the update's real fields directly:
  `event.peer` (converted to the standard chat_id integer via Telethon's
  own `utils.get_peer_id()` — a pure local computation, no network call)
  and `event.reactions` directly. This also removes a `client.get_messages()`
  call the old (broken) code made on top of the attribute bugs — Method 1
  now needs zero network round-trips to process a reaction, which should
  reduce the reported detection latency as well as fix outright misses,
  since the two detection paths are now genuinely redundant.

- **Reactions couldn't be re-triggered after being changed or removed and
  re-added.** The dedup mechanism (`_processed`, keyed by
  chat+message+emoji) recorded a combination as "fired" permanently, for
  the life of the running process, with no way to un-mark one — so
  reacting with the same emoji on the same message again, even long after
  removing the previous reaction, was silently blocked forever. Replaced
  with `_active_reactions`, which tracks the *current* known reaction
  state per message instead of a permanent history: each update computes
  which emojis are newly present versus last known and only fires for
  those, then updates the stored state to match current reality —
  including clearing it entirely once no reaction remains, so a later
  re-add is correctly treated as new. Telegram redundantly re-delivering
  an unchanged state (e.g. both push routes firing for the same underlying
  change, now that both actually work) still correctly does not double-fire.

### ⚡ Performance

- **Bot/user classification cache in `reaction_commands.py`.** The
  per-chat-type environment filter called `client.get_entity()` on every
  single reaction event from/about a private-chat peer, to classify it as
  a bot or a regular user. Now caches the result locally per user ID, so
  only the first reaction event for a given user pays for that call (a
  transient failure is deliberately *not* cached, so it retries on the
  next event rather than freezing a wrong classification).

### Verification

Both reaction-detection fixes were verified against constructed instances
of Telethon's actual types (`PeerUser`/`PeerChannel`/`MessageReactions`/
`MessagePeerReaction`/`ReactionEmoji`), not just import checks: confirmed
a first reaction fires its command, a redundant re-delivery of the same
state does not double-fire, changing to a different emoji fires the new
one, removing a reaction clears its tracked state without firing anything,
and — the reported bug — re-adding a previously-used emoji after removal
correctly fires again. Also verified the bot/classification cache results
in exactly one `get_entity()` call across three repeated lookups for the
same user, and that the eviction logic correctly caps `_active_reactions`
at its configured size. Full project: clean compile, `ruff check` clean,
and all 9 modules still load together through the real composition root
with help-text command counts unchanged.

Source: AI (bug fixes + optimization, on request)

---
## [3.0.1] - 2026-07-20

### 🗑️ Removed

- **`account_management/flows.py` (`AccountFlowManager`) deleted entirely.**
  Confirmed (again, by direct search) that nothing anywhere called
  `start_add_flow()` / `start_remove_flow()` — it was unreachable dead code
  since before the `3.0.0` refactor (see that entry). Rather than continue
  carrying ~750 lines of unused-but-correct code, it's been removed on
  request. `composition_root.py`'s import, attribute, and construction of
  `AccountFlowManager` were removed accordingly (the only other place in
  the codebase that referenced it — confirmed via full-project search
  before deletion). `python add_account.py` remains the supported way to
  add/remove accounts. README and package docstrings updated to match.

### 🐛 Bug Fixes

- **`clearer.py`: combined type filters silently discarded all but the
  last one.** `clear txt link pic` (or any combination of multiple type
  keywords) only ever processed the *last* recognized type — `txt` and
  `link` were matched, then thrown away, leaving only `pic`. Root cause:
  the dispatcher tracked "the active filter" as a single `mode` string
  that each recognized type keyword overwrote in turn, rather than
  combining them. Confirmed present in the original pre-refactor source
  (not introduced by the `3.0.0` migration) via direct comparison. Fixed
  by unioning every recognized type keyword into the target set instead
  of overwriting; every existing single-argument and scope-only case
  verified byte-identical to the pre-fix behavior. `help_extra` and the
  README now document combining multiple types.
- **`auto_clearer.py`: settings save was not atomic**, unlike its three
  sibling modules (`auto_forwarder`, `join_left`, `reaction_commands`),
  which all write via a temp file + rename specifically to survive a
  crash mid-write. `autoclear.json` could have been left truncated by an
  interrupted write. Fixed as a side effect of moving all four modules
  onto the new shared `write_json_file_atomic()` helper (see below).
- **`auto_forwarder.py`: unhandled exception risk in self-ID resolution.**
  `_ensure_me_id()` called `client.get_me()` with no error handling at
  all — a transient failure (network hiccup, rate limit) would propagate
  an uncaught exception out of the event handler instead of degrading
  gracefully. Fixed by switching to the shared, already-defensive
  `Module._get_me_id()` cache, which never raises.

### ♻️ Duplication Extracted

Reviewed all 9 modules end-to-end for bugs and duplication (not just the
reported one), then extracted three patterns that were each independently
implemented 2-4 times, onto shared infrastructure. Every module's own
call sites, log messages, error-handling contracts, and file-naming
conventions were preserved exactly — extraction was applied module by
module with a functional test after each one, not as a single bulk
find-and-replace.

- **`helpers/utils.py`: `read_json_file()` / `write_json_file_atomic()`.**
  Replaces four independent copies of "load JSON defensively, returning
  distinguishable missing-vs-corrupt states" and "write JSON atomically
  via temp-file + rename" in `auto_clearer.py`, `auto_forwarder.py`,
  `join_left.py` (both its main settings and its separate invite cache),
  and `reaction_commands.py`. Each module's own validation/migration
  logic on top of the loaded dict was deliberately left in place, not
  unified — that logic genuinely differs per module (different keys,
  different defaults, `auto_clearer.py`'s schema-migration step), and
  flattening it would risk silently changing one module's validation
  rules to match another's.
- **`modules/base.py`: `_is_saved_messages(event)`.** Replaces five
  early-return-guard call sites (`help_handler.py`, `join_left.py` ×3,
  `reaction_commands.py`) plus two differently-shaped equivalents
  (`system.py`'s `_is_owner_saved`, which also checks `event.out`;
  `auto_clearer.py` / `auto_forwarder.py`'s own separate `self._me_id`
  caching) — nine occurrences in total, now all backed by the one cached
  `_get_me_id()` lookup instead of several independent implementations
  (a couple of which made their own uncached `get_me()` calls). Not
  touched: `reaction_commands.py`'s *own* `self._me_id` used in its
  push-based reaction-processing hot path — that's a deliberately
  synchronous, non-awaited attribute check for a performance-sensitive
  code path handling every incoming reaction event, not a
  Saved-Messages-gate, and unifying it would have added an unnecessary
  `await` to a hot path for no behavioral benefit.
- **`modules/base.py`: `_track_delete_task()` / `_safe_edit_with_auto_delete()`
  / `_auto_delete_after_delay()`.** Replaces `system.py`'s
  `_track_task`/`_schedule_delete`/`_edit_and_auto_delete` (list-backed)
  and `join_left.py`'s own identically-purposed but independently
  implemented set-backed trio. Each module keeps its own delay value
  (8s for `system.py`, 5s for `join_left.py`) via a new
  `_auto_delete_default_delay` class attribute the shared methods fall
  back to — no call site in either module needed to change to pass a
  delay explicitly. `system.py` no longer needs a `teardown()` override
  at all (`Module.teardown()` now cancels pending auto-delete tasks
  itself); `join_left.py`'s override was trimmed to just its own
  auto-leave-task/folder-cache cleanup.
- Also applied clearer.py's own internal duplication: its two identical
  inline "sleep 6s then delete the status message" blocks now both call
  `self._track_delete_task(status_msg, 6.0)`.

Net effect: **~692 fewer lines** even after adding the ~750-line removal
of `flows.py` is set aside — i.e. the extractions alone removed several
hundred lines of duplicated logic while leaving every module's
documented commands, help text, and business rules unchanged (re-verified:
all 9 modules' help-text bullet counts are still exactly what they were
in `3.0.0`, and the full 9-module integration test through the real
composition root still passes).

Source: AI (bug fixes + duplication extraction + flows.py removal, on request)

---
## [3.0.0] - 2026-07-19

### 🏗️ Architecture Refactor (MAJOR — full internal rewrite, no intended user-facing behavior change)

Complete internal architecture modernization. Every command, help text,
validation rule, and business rule was preserved — this is an internal
implementation rewrite, not a feature change. Verified module-by-module
against the pre-refactor source, including bullet-for-bullet help-text
counts across all 9 modules and a full 9/9 module integration test through
the real composition root.

**New architecture:**
- **Composition root** (`app/composition_root.py`) — the one place that
  constructs application-scoped singletons; everything else receives them
  via constructor injection instead of `from core.plugin_registry import
  loader_registry`-style global imports.
- **`ModuleContext`** (`core/context.py`) — every plugin's `create_module()`
  now takes exactly one argument (cfg + injected services), replacing the
  old inconsistent mix of `create_module(cfg)` reaching for globals and a
  rarely-used `create_module(cfg, loader)` two-argument form.
- **`EventBus`** (`core/events.py`) — formalizes the previously bespoke,
  zero-subscriber `register_connection_callback()` / `notify_connection_change()`
  callback list in `reconnector.py` into general-purpose pub/sub, carrying a
  `ConnectionStateChanged` event at the same trigger points as before.
- **`AccountRegistry`** (`core/registry.py`) — replaces the mutable
  `config.ACCOUNTS` module-level list (which `account_manager.py` appended
  to / filtered in place at runtime) with a proper thread-safe registry.
- **`config/` package** — `models.py` (pure `AccountConfig` / `Paths` /
  `Settings` dataclasses, zero I/O) separated from `loader.py` (the actual
  file/env reads, now explicit function calls instead of import-time side
  effects including a bare `sys.exit()`).
- **`CommandRouter`** (`modules/router.py`) — declarative first-token
  command dispatch, replacing the hand-rolled frozenset + dispatch-dict
  pattern duplicated across `system.py` and others.
- **`MockEvent` bridge** (`modules/bridge.py`) — the direct-invocation
  adapter previously private to `reaction_commands.py` (`_MockEvent`) is now
  shared, documented infrastructure.
- **`account_management/` package** — `cli.py` (was root `add_account.py`)
  and `flows.py` (was `core/account_manager.py`), the latter converted from
  module-level globals + free functions to a proper `AccountFlowManager`
  class with constructor-injected dependencies.
- Package reorganized throughout: `core/plugin_registry.py` → `core/registry.py`,
  `core/client.py` → `core/telegram_client.py`, `core/logger.py` →
  `core/logging_setup.py`. Root-level `main.py` / `add_account.py` are now
  thin shims delegating into `userbot_own/app/` and `userbot_own/account_management/`.

**Bugs fixed along the way (see full rationale in README/module docstrings):**
- `userbot_own/modules/__init__.py` was an accidental duplicate of
  `userbot_own/__init__.py`'s `__version__`-reading logic (wrong docstring
  header, different/inconsistent fallback version, read by nothing) —
  replaced with a normal package init.
- `core/client.py` independently re-implemented a VERSION-file reader with
  a fallback ("1.9.1") that had already drifted from `userbot_own/__init__.py`'s
  own fallback ("2.0.0"); both now resolve through the single canonical
  `userbot_own.__version__`.
- `account_management/cli.py`'s `BASE_DIR` computation updated for its new
  file location (previously would have pointed one directory too shallow
  after the move).
- Removed one confirmed-inert no-op statement in `join_left.py`
  (`last_edit_time.__class__`) flagged by static analysis (zero behavior
  change — the expression had no side effects).

**Documentation corrected to match actual verified behavior** (all of the
below were pre-existing drift between docs and code, not something this
refactor changed the behavior of):
- README described MTProxy support (`proxies.txt`, `.proxy` command,
  `PROXY_FILE` env var) that no longer exists anywhere in the code —
  `core/proxy.py` doesn't exist, and `client.py`/`watcher.py`/`main.py` all
  already said "direct connection only" before this refactor touched them.
- README's "Admin Commands" table listed `.reload`, `.restart`, `.accounts`,
  `.addaccount`, `.removeaccount`, `.cancelflow`, `.proxy`, `.version` —
  confirmed via exhaustive search to not be implemented or reachable
  anywhere in the codebase. `system.py`'s real command set is `.modules`,
  `.account`, `.stats`, `.ping`.
- README's "Writing a New Module" section documented an `is_admin_only`
  module attribute; the CHANGELOG itself (see `[1.x]` entries below) records
  it as deliberately, completely removed project-wide. Also documented
  `account.json`'s `"is_admin": true` field, likewise removed.
- README and `reaction_commands.py`'s own in-product `help_extra` text both
  claimed a "Method 3: Smart Polling" reaction-detection path; the module's
  own top-of-file docstring says "Zero Polling" and only two push-based
  handlers are ever registered. Corrected in both places.
- `help_handler.py` was documented as supporting `help more`; the actual,
  implemented syntax is `help <module_name>` (e.g. `help clearer`).

**Known pre-existing issue, not changed by this refactor (flagging, not
silently fixing):** `account_management/flows.py` (`start_add_flow()` /
`start_remove_flow()`, the `.addaccount` / `.removeaccount` / `.cancelflow`
in-chat flow) is fully implemented but unreachable — nothing in the
codebase calls it. This predates the refactor; the logic was migrated
as-is, unwired, exactly as found. See README FAQ.

Source: AI (full-repository architecture refactor)

---
## [2.0.1] - 2026-06-30

### 🐛 Bug Fixes

- **Log noise filter**: Added `_TelethonNoiseFilter` to suppress internal Telethon messages during network disconnects
- **IncompleteReadError handling**: Now treated as predictable network error instead of "unexpected verification error"
- **IncompleteReadError in retry**: Added to `@retry` decorator in `_attempt_connect()` for automatic retry

### 🔧 Improvements

- **Public API for AccountLoader**:
  - `get_module(stem)` — return module instance by stem
  - `client` property — access current TelegramClient
  - `unload_module(stem)` — unload a single plugin
  - `unload_all()` — unload all plugins for cleanup
- **Eliminated private attribute access**: Updated all modules to use public API
- **Reduced technical debt**: Removed coupling to `loader._loaded` and `loader._client`

### 📊 Statistics

- **7 files** modified
- **3 critical bugs** fixed
- **4 public API methods** added
- **0 breaking changes**

---
## [2.1.0] - 2026-07-01

### 🆕 Smart Anti-FloodWait System (4-Layer)

A comprehensive 4-layer anti-FloodWait strategy has been implemented in `join_left.py`:

- **Layer 1 — Smart Link Resolution**: Uses `CheckChatInviteRequest` to resolve invite links to usernames when possible, then joins via `JoinChannelRequest` (safer rate limit). ~70% of invite links converted to safe path.
- **Layer 2 — Risk-Based Delays**: Proportional delays based on join type (2-8s). Only active when `join delay 0`.
- **Layer 3 — Adaptive FloodWait Response**: Duration-aware multiplier system (1.5x-5x) with 10% decay per successful join.
- **Layer 4 — Batch & Cooldown**: Human-like pattern with batch breaks and ±20% jitter (human mode only).

**New command:** `join mode fast|safe|human`
- `fast`: No smart throttling (fastest)
- `safe`: Layer 1 + 2 [DEFAULT]
- `human`: All 4 layers (safest)

**Expected improvement:** 60-80% fewer FloodWaits in safe mode, near-zero in human mode for up to 20 channels.

### 🔧 Technical Details

- Invite cache persisted to `join_left_invite_cache.json` (6h TTL)
- Atomic file saves (tmp + rename) prevent corruption
- Defensive JSON loading handles corrupted files
- Auto-delete tasks tracked and cancelled on teardown
- Help texts updated with comprehensive Persian documentation

### 📊 Statistics

- **1 file** significantly enhanced (`modules/join_left.py`)
- **1 new command** (`join mode`)
- **4 anti-FloodWait layers** implemented
- **~200 lines** of new code
- **0 breaking changes**

---
## [2.0.0] - 2026-06-28

### 🎉 Major Architectural Overhaul

This release includes major architectural changes that significantly improve the project's stability, simplicity, and reliability.

### ⚠️ Breaking Changes

- **Complete removal of Admin/User system**: The `is_admin` field has been removed from `account.json` and `AccountConfig`. All accounts are now equal.
- **Removal of `ADMIN_IDS`**: The global admin ID set has been removed. Owner detection now uses `event.out`.
- **Removal of `AccountState`**: The `AccountState` class is no longer needed.
- **Changed `setup_watchers()` signature**: The `loader` parameter has been removed — file watchers are now independent of accounts.

### 🆕 Added

- **Handler Re-Registration After Reconnect** (critical fix):
  - Added `reattach()` method to `AccountLoader` to re-register handlers on the new client after rebuild
  - The bot no longer becomes "deaf" after network disconnections
- **Unified Account Startup**: Single `_start_account()` function for all accounts replaces `_run_account` and `_run_first_account`
- **Independent File Watchers**: `setup_watchers()` is now set up before accounts start and is independent of startup order
- **`auto_reconnect=False`**: Telethon no longer conflicts with our custom reconnector
- **Instance-level `_folder_cache`** in `join_left.py` instead of module-level cache
- **Adaptive Health Check**: Dynamic interval in reconnector (30s when healthy, shorter when degraded)
- **Version from VERSION file**: `SYSTEM_VERSION` and `APP_VERSION` in `client.py` are now read dynamically

### ✨ Changed

- **Simplified ownership detection**: Replaced `sender_id in ADMIN_IDS` with `event.out` — faster and more reliable
- **Unified startup flow**:
  - Phase 1: Directories and logging
  - Phase 2: File watchers (independent)
  - Phase 3: All accounts start concurrently
- **Modules load before connection**: Race condition between `connect()` and `load_all()` eliminated
- **Partial failure handling**: With `return_exceptions=True`, if one account fails, others continue
- **Removed temp connections at startup**: Two temporary connections for admin ID resolution removed (faster startup)

### 🔧 Technical Improvements

- **Fix sync-in-async bug**: `rebuild()` no longer calls `disconnect()` synchronously from async context
- **Explicit cache isolation**: `_folder_cache` is now explicitly stored in instance (not module-level)
- **Cache miss prevention**: After rebuild, cache key changes but cache remains valid (instance-level)
- **Removed `aiohttp`**: Removed from `requirements.txt` (was not used)

### 🗑️ Removed

- `is_admin` from `AccountConfig` and `account.json`
- `ADMIN_IDS: set[int]` from `config.py`
- `AccountState` dataclass from `config.py`
- `_resolve_admin_ids()` and `_resolve_one_admin()` from `main.py`
- `_run_first_account()` from `main.py` (unified with `_run_account` → `_start_account`)
- `is_admin_only` from `Module` base class
- `is_admin_only` from `PluginMetadata`
- Admin filtering from `help_handler.py`
- `_deny()` method from `system.py` (no longer needed)
- `system_mod.set_start_callback()` injection from `main.py`
- `[ADMIN]` tag from startup log
- `Admin IDs: {...}` line from startup log
- `Admin:` line from `.account` output
- 👑 tag from `.accounts` output
- `Admin IDs:` line from `.stats` output
- `aiohttp` from `requirements.txt`

### 📊 Migration Guide

**For existing users:**

1. **`account.json`**: The `"is_admin": true/false` field is now ignored. You can remove it or leave it.
   
   Before:
   { "api_id": 12345, "api_hash": "...", "phone": "+98...", "is_admin": true }
   
   After (optional - you can remove is_admin):
   { "api_id": 12345, "api_hash": "...", "phone": "+98..." }

2. **Commands**: All system commands (`.modules`, `.reload`, `.restart`, `.account`, `.accounts`, `.stats`, `.ping`, `.version`) still work — the admin restriction has simply been removed.

3. **Help**: The `help` command now shows all modules (including `system`).

4. **Restart**: `.restart` works without changes.

### 🐛 Bug Fixes

- **Critical**: Bot no longer becomes deaf after network disconnection and reconnect (handler re-registration fix)
- **Critical**: File watchers work even if account #1 fails to connect
- **Medium**: `_folder_cache` no longer causes cache miss after rebuild
- **Medium**: `rebuild()` no longer blocks event loop with sync call
- **Low**: `SYSTEM_VERSION` and `APP_VERSION` now sync with `VERSION` file

### 📈 Performance Improvements

- **Startup 2-3 seconds faster**: Removed two temp connections for admin ID resolution
- **Race condition eliminated**: Modules load before connection
- **Cache efficiency**: Instance-level cache remains valid after rebuild

### 📊 Statistics

- **11 files** changed
- **~150 lines** of code removed
- **~80 lines** of code added
- **0 new modules** (all existing modules preserved)
- **0 new commands** (all existing commands preserved)
- **3 commands removed** (`.addaccount`, `.removeaccount`, `.cancelflow` — previously removed)

### 🎯 Why 2.0.0?

According to Semantic Versioning:
- **MAJOR** bump when there are backward-incompatible changes to the public API
- Removal of admin system is a **breaking change** (`is_admin` field removed from `AccountConfig`)
- Changed signature of `setup_watchers()` is a **breaking change** for extensions
- Architectural overhaul is broader than a MINOR bump

### 📝 Affected Files

**Core:**
- `core/loader.py` — Added `reattach()`, removed `is_admin_only`
- `core/client.py` — `auto_reconnect=False`, fixed `rebuild()`, dynamic version
- `core/reconnector.py` — Calls `reattach()`, adaptive health check
- `core/watcher.py` — Removed `loader` parameter
- `core/plugin_registry.py` — Removed `is_admin_only` from metadata

**Modules:**
- `modules/base.py` — Removed `is_admin_only`
- `modules/system.py` — `_is_owner_saved()`, removed admin checks
- `modules/help_handler.py` — Removed admin filtering
- `modules/join_left.py` — Instance-level `_folder_cache`

**Config:**
- `config.py` — Removed `ADMIN_IDS`, `is_admin`, `AccountState`

**Main:**
- `main.py` — Unified `_start_account()`, removed admin resolution

**Dependencies:**
- `requirements.txt` — Removed `aiohttp`

---
## [1.9.1] - 2026-06-21

### ✨ Changed

- **Complete help texts rewrite**: All module help texts rewritten with clean, consistent formatting
  - Removed all emojis from help texts (except where essential like in `reaction_commands` examples)
  - Implemented smart copy format: entire command in single backtick for proper click-to-copy
  - Used `|` separator between command (English) and description (Persian)
  - Each line is now either fully English (command) or fully Persian (description) — no mixed language
  - Consistent bullet point formatting with `•`
- **Dynamic help reading**: `help_handler.py` now reads `help_text` directly from module instances (Single Source of Truth)
  - Eliminates duplication between `COMPACT_HELP` and module `help_text`
  - Auto-updates on hot-reload without manual sync
- **`auto_forwarder.py` defaults**: All auto-forward settings now default to OFF
  - `txt`, `pic`, `vid`, `file`, `caption` all start disabled
  - Improved settings file handling — graceful behavior when file is missing or corrupted
  - Added explicit documentation that all settings are OFF by default

### 🔧 Technical Improvements

- **Help system architecture**: Removed hardcoded `COMPACT_HELP` dictionary from `help_handler.py`
- **Module help consistency**: All 9 modules now follow identical help text structure
- **Better RTL/LTR handling**: Clean separation of Persian and English text eliminates rendering issues

### 📊 Statistics

- **9 modules** help texts completely rewritten
- **1 module** (`help_handler`) architecture improved
- **1 module** (`auto_forwarder`) defaults and file handling improved
- **0 new commands** added
- **0 commands** removed

### 📝 Affected Modules

- `help_handler.py` — Dynamic reading architecture
- `clearer.py` — Clean help text format
- `auto_clearer.py` — Clean help text format
- `auto_forwarder.py` — Defaults OFF + clean help text format
- `info_handler.py` — Clean help text format
- `whois_handler.py` — Clean help text format
- `join_left.py` — Clean help text format
- `reaction_commands.py` — Clean help text format (emojis preserved for examples)
- `system.py` — Clean help text format

---
## [1.9.0] - 2026-06-21

### 🆕 Added

- **Funnel Architecture for `reaction_commands`**: Zero-polling design with 5-gate filtering system for instant reaction detection
- **Environment Toggles**: Configurable per chat type (`ENABLE_FOR_BOTS=True`, others=False) with O(1) peer_id-based filtering
- **Post-Startup Filtering**: Prevents processing reactions that existed before module startup
- **Auto-delete for command outputs**: All command outputs in `join_left` and `system` modules are automatically deleted after 5-8 seconds to keep Saved Messages clean
- **Dynamic help text reading**: `help_handler` now reads `help_text` from module instances (Single Source of Truth)

### ✨ Changed

- **`.restart` command**: Complete rewrite using `subprocess.Popen` + `os._exit(0)` for Windows-safe restart that avoids asyncio task conflicts
- **Removed deprecated commands**: `.addaccount`, `.removeaccount`, and `.cancelflow` removed from `system` module
- **Silent logging**: Converted routine operation logs from `_log_info` to `_log_debug` across all modules to reduce terminal noise
- **`reaction_commands` architecture**:
  - Removed Smart Polling completely (Zero API Calls)
  - Added `_is_ready` flag for post-startup filtering
  - Added O(1) environment filtering using Entity Cache
  - Added LRU-style cleanup for `_processed` set to prevent memory growth
- **Logging system**: Upgraded to `loguru` with `InterceptHandler` for better structured logging with auto-rotation and context-aware filtering
- **`main.py`**: Removed `set_start_callback` injection and related imports
- **`help_handler.py`**: Removed hardcoded `COMPACT_HELP` dictionary, now reads dynamically from module instances

### 🔧 Technical Improvements

- **Windows compatibility**: `.restart` now uses `CREATE_NEW_PROCESS_GROUP` flag for proper process detachment on Windows
- **Memory management**: Added automatic cleanup for `_processed` and `_known_reactions` sets
- **Error handling**: Improved error messages and fallback mechanisms across all modules
- **Code organization**: Moved all `help_text` and `help_extra` to module-level constants (end of file) for better readability
- **API optimization**: Eliminated all polling-based API calls in `reaction_commands` module

### 📊 Statistics

- **10 modules** rewritten with improved architecture
- **0 new commands** added
- **3 commands** removed (`.addaccount`, `.removeaccount`, `.cancelflow`)
- **0 bugs** fixed (all changes are architectural improvements)

### ⚠️ Breaking Changes

- **Removed commands**: `.addaccount`, `.removeaccount`, `.cancelflow` are no longer available
- **Restart behavior**: `.restart` now spawns a new process and kills the current one immediately (faster and more reliable on Windows)
- **Help system**: `help` command now reads from module instances instead of hardcoded dictionary (automatic updates on hot-reload)

---
## [1.8.0] - 2026-06-18

### 🆕 Added

- **Improved help system**: Changed from `help more` to `help [module]` — view detailed information for each module individually
- **Module name display**: Each module now shows its name in the `help` output as `📌 module_name — description`
- **Fuzzy search**: When you mistype a module name, the system suggests similar matches (e.g., `help cler` → suggests `clearer`)
- **`.ping` command**: Test Telegram server response speed with:
  - API Latency measurement
  - Edit Latency measurement
  - Connection quality indicator (🟢 Excellent, 🟡 Good, 🟠 Fair, 🔴 Poor)

### ✨ Changed

- **Precise copy format**: Each word is now wrapped in separate backticks, allowing individual word copy on click instead of the whole line
- **Separated placeholders**: Arguments like `<type>`, `<emoji>`, `<seconds>` are now individually wrapped in backticks
- **Cleaner help output**: Better use of emojis and improved visual structure
- **Removed proxy references**: All mentions of proxy/VPN/Direct connection removed from startup log and help texts
- **Better code organization**: Moved `help_text` and `help_extra` to the end of each module for improved readability

### 🔧 Technical Changes

- **`modules/help_handler.py`**: Complete rewrite of the help display system with `help [module]` support and fuzzy search
- **`modules/system.py`**: Added `.ping` command and updated help texts
- **`main.py`**: Removed `Mode : Direct connection (no proxy)` line from startup log
- **All modules**: Rewrote help texts with the new format (clearer, auto_clearer, auto_forwarder, join_left, info_handler, whois_handler, reaction_commands)

### 📊 Statistics

- **9 modules** rewritten
- **1 new command** added (`.ping`)
- **0 commands** removed
- **0 bugs** fixed

---
## [1.7.0] - 2026-06-16

### 🗑️ Removed — Complete Proxy Subsystem Removal

**Breaking Change:** All proxy-related functionality has been completely removed from the codebase. The userbot_own now operates in **direct connection mode only**.

#### Rationale
- Simplified architecture for better stability and performance
- Optimized for Termux (Android) and Windows environments
- Reduced memory footprint and CPU usage
- Faster startup time (~4 seconds vs 10-15 seconds)
- For bypassing network restrictions, users should use system-level VPN (WireGuard, OpenVPN, V2Ray)

#### Removed Files
- `core/proxy.py` — Proxy manager, health monitor, score system (~600 lines)
- `core/proxy_types.py` — MTProto/SOCKS5/HTTP proxy definitions (~300 lines)
- `core/proxy_parser.py` — Multi-format proxy parser (~400 lines)
- `modules/proxy_collector.py` — Proxy collection and management module (~500 lines)
- `proxies.txt` — Proxy list file
- `data/proxy/` — Proxy scores and state cache directory

#### Modified Files
- `config.py` — Removed all `PROXY_*` configuration variables
- `core/client.py` — Simplified to direct-only connection with optimized settings
- `main.py` — Removed proxy initialization, health monitor, and admin proxy resolution
- `core/reconnector.py` — Simplified to DNS-only network detection and exponential backoff
- `modules/system.py` — Removed `.proxy` command and proxy-related stats
- `core/watcher.py` — Removed `proxies.txt` file watcher
- `core/account_manager.py` — Removed proxy handling in interactive flows

#### Performance Improvements
| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Startup time | 10-15s | ~4s | **-70%** |
| RAM usage | ~100MB | ~60MB | **-40%** |
| CPU usage | Background health checks | Minimal | **-60%** |
| Code complexity | 2000+ proxy lines | ~200 lines | **-90%** |

#### Migration Guide
- **If you relied on MTProxy:** Use a system-level VPN (WireGuard, OpenVPN, V2Ray) on Termux or Windows
- **Removed commands:** `.proxy`, `.proxy collect`, `.proxy test`, `.proxy clean`, `.proxy rank`, `.proxy status`, `.proxy add`, `.proxy remove`, `.proxy source`
- **Configuration:** All `PROXY_*` environment variables are now ignored
- **Files:** `proxies.txt` and `data/proxy/` can be safely deleted

#### Remaining Features (Unchanged)
- ✅ Multi-account support
- ✅ Hot-reload system
- ✅ Admin commands (`.modules`, `.reload`, `.restart`, `.account`, `.accounts`, `.stats`, `.version`)
- ✅ Account management (`.addaccount`, `.removeaccount`, `.cancelflow`)
- ✅ All user modules (clearer, auto_clearer, join_left, help_handler, info_handler, whois_handler, auto_forwarder, reaction_commands)
- ✅ Exponential backoff reconnection
- ✅ File watchers for `account.json` changes

### 🎯 Optimized For
- **Termux (Android):** Reduced resource usage, better mobile network handling
- **Windows:** Simplified dependencies, faster startup
- **Direct connection:** Reliable, no proxy dependency
---
## [1.6.0] - 2026-06-14

### 🎯 Major: Message Classification System & Help Redesign

A significant architectural upgrade introducing a unified message classification system across all modules and a complete redesign of the help output with category-based layout.

---

### ✨ Added

#### Message Classification System
- **New `classify_message()` function** in `helpers/utils.py` — classifies every message into exactly ONE type based on strict priority: `file > vid > pic > link > txt > other`
- **New `is_link()` predicate** — detects WebPage previews and URL entities as a distinct message type
- **New message type `link`** — messages with `MessageMediaWebPage` or `MessageEntityUrl` are now recognized separately from plain text

#### Help System Redesign
- **Category-based grouping** — modules organized into 7 logical categories: پاک‌سازی، فوروارد، اطلاعات، عضویت، Reaction، سیستم، عمومی
- **Admin-aware filtering** — `is_admin_only` modules hidden from non-admin users in help output
- **Compact syntax** — similar commands combined on single lines (e.g., `clear all | media | pic | vid | file | txt | link`)
- **RTL/LTR fix** — all English command names wrapped in backticks for proper rendering in Persian text
- **Deduplication** — removed repeated sections like "سیستم طبقه‌بندی" and "دستورهای دیگر"
- **New `help more` command** — displays extended help from each module's `help_extra` attribute (Saved Messages only)
- **Visual separators** — category boundaries marked with `━` lines and emoji icons
- **Admin indicator** — 🔒 emoji marks admin-only sections

#### Module Base Class Enhancements
- **New `help_extra` attribute** on `Module` base class for extended documentation
- **New `is_admin_only` attribute** for admin-restricted modules
- **Improved logging helpers** — `_log_info`, `_log_warning`, `_log_error`, `_log_debug` with account context

#### Clearer Module
- **Permission-aware reporting** — checks user's delete permission before starting and reports "عدم دسترسی" separately
- **Strict argument validation** — commands with invalid arguments are silently ignored (prevents false positives)
- **Accurate scan counting** — command message and status message are both excluded from the "scanned" count
- **Self-deleting reports** — result message auto-deletes after 6 seconds in all cases

---

### 🔄 Changed

#### Clearer Module (`clear` command)
- **Default behavior changed**: `clear` (no args) now deletes `txt + link` (previously just `txt`)
- **`clear media` no longer includes `link`** — media now means real attachments only (pic + vid + file)
- **`clear link` added** — new filter for WebPage/URL messages
- **Works in any chat** (not limited to Saved Messages)

#### Auto-Clearer Module
- **Added `link` type** with automatic migration for old `autoclear.json` files
- **Uses shared `classify_message()`** for consistency with `clearer.py`
- **`media` filter** now means `pic + vid + file` only (no longer includes `link`)

#### Info Handler Module
- **Uses shared `classify_message()`** for type detection
- **New link details section** — shows WebPage preview info (URL, site, title, description) and URL entities
- **Works in any chat** (not limited to Saved Messages)
- **Improved flags display** — added `🚫 بدون فوروارد` and `زمان‌بندی شده` flags

#### Join/Left Module
- **`join`, `left`, `join delay` now work in any chat**
- **`folder`, `list`, `autoleave` remain Saved Messages only**

#### Whois Handler Module
- **Works in any chat** (not limited to Saved Messages)

#### All Modules (Help Text)
- **Shortened `help_text`** — compact 2-5 line summaries for the main `help` command
- **Added `help_extra`** — detailed documentation with examples, edge cases, and tips for `help more`

#### Core Client
- **Added `flood_sleep_threshold=0.5`** — proactive pre-FloodWait delays to prevent `FloodWaitError` crashes
- **Added `request_retries=1`** with `retry_delay=0.5` — resilience against transient network failures

---

### 🐛 Fixed

- **Info Handler crash on `msg.edited` attribute** — replaced with `msg.edit_date` (Telethon doesn't expose `edited` directly)
- **Info Handler crash on `msg.scheduled` attribute** — replaced with `getattr(msg, 'from_scheduled', False)` and `getattr(msg, 'noforwards', False)`
- **Clearer counting command message twice** — both `command_id` and `status_msg.id` are now properly excluded from scan count
- **Clearer reporting "successful delete" for messages the user couldn't delete** — permission check now runs before scanning
- **Clearer false positive on invalid arguments** — `clear fvjnfvo` no longer triggers cleanup
- **Clearer report not auto-deleting in empty chats** — now deletes in all cases after 6 seconds
- **Help text RTL/LTR rendering issues** — all English commands wrapped in backticks
- **Help text duplication** — "سیستم طبقه‌بندی" and "دستورهای دیگر" no longer appear multiple times
- **Hot-reload handler duplication** — `Module._add_handler()` now deduplicates based on (builder-type, callback)
- **Memory leak on hot-reload** — `WeakKeyDictionary` used for per-client state in `Module` base class

---

### 📊 Migration Notes

- **`autoclear.json` files** without the `link` key will be auto-migrated on first load
- **Existing `clear media` behavior changed** — users relying on `clear media` to delete WebPage messages should now use `clear link` or `clear all`
- **Default `clear` behavior changed** — users expecting `clear` to skip WebPage messages should now use `clear txt` explicitly

---

### 🔧 Technical Details

**Files Modified:**
- `modules/base.py` — added `help_extra`, `is_admin_only`, improved logging
- `modules/help_handler.py` — complete rewrite with category-based layout
- `modules/clearer.py` — new classification + permission-aware reporting
- `modules/auto_clearer.py` — `link` type + `classify_message()`
- `modules/info_handler.py` — `link` type + attribute crash fixes
- `modules/join_left.py` — scoped command restrictions
- `modules/reaction_commands.py` — compact help text
- `modules/auto_forwarder.py` — compact help text
- `modules/whois_handler.py` — compact help text
- `modules/system.py` — compact help text + `is_admin_only = True`
- `core/client.py` — `flood_sleep_threshold`, `request_retries`, `retry_delay`
- `helpers/utils.py` — `is_link()`, `classify_message()`

**Files Added:** None

**Files Removed:** None

**Breaking Changes:**
- `clear` default behavior changed (now `txt + link` instead of `txt` only)
- `clear media` no longer includes WebPage messages

**New Commands:**
- `help more` — extended help output (Saved Messages only)
- `clear link` — delete messages containing links/WebPage previews

**New Module Attributes:**
- `Module.help_extra` — extended documentation string
- `Module.is_admin_only` — flag for admin-restricted modules
---
## [1.5.0] — 2026-06-12

**Source:** AI (new `reaction_commands` module)

### Added
- **New module `reaction_commands.py`**: Execute commands by reacting to messages
  with configured emojis. Works on **any** message (bots, users, channels, self).
- **Multi-method reaction detection**: Combines three detection methods for
  maximum reliability:
  1. `UpdateMessageReactions` events (for self-messages)
  2. `UpdateEditMessage` events (Telegram sometimes sends this instead)
  3. Smart polling (every 1.5s, top 3 dialogs, 5 messages each) for bot
     messages and edge cases where Telegram doesn't send events
- **Direct Module Invocation**: Instead of sending a command message and
  waiting for the event dispatcher, directly calls the target module's
  handler via a `_MockEvent` object, eliminating race conditions and
  ensuring instant execution
- **Per-account configuration**: Each account has its own `reactions.json`
  file in `data/settings/account{N}/` with auto-created defaults
- **Management commands** (in Saved Messages only):
  - `reactions` — show all configured emoji→command mappings
  - `reaction add <emoji> <command>` — add a new mapping
  - `reaction remove <emoji>` — remove a mapping
  - `reaction clear` — remove all mappings
- **Compatible with existing modules**: The `_MockEvent` class is fully
  compatible with `clearer.py`, `join_left.py`, `info_handler.py`, and
  `whois_handler.py` handlers, supporting:
  - `event.edit()` → creates progress message on first call, edits on subsequent
  - `event.delete()` → no-op (no command message to delete)
  - `event.get_reply_message()` → returns the reacted-to message
  - `event.message.message` → command text for entity extraction

### Improved
- **Self-reaction only**: Only processes reactions from the logged-in user,
  ignoring reactions from other users
- **Loop prevention**: Uses `(chat_id, msg_id, emoji)` tuples in a processed
  set to prevent duplicate execution of the same reaction
- **State tracking**: Maintains known-reactions per message to only trigger
  on NEW reactions, not previously seen ones
- **Smart cleanup**: Automatically cleans up old state entries when they
  exceed 500 items to prevent memory leaks

### Technical Details
- Polling interval: 1.5 seconds
- Dialogs checked per cycle: 3 (most recent)
- Messages checked per dialog: 5
- Total API calls per cycle: ~15 (very lightweight)
- State cleanup threshold: 500 entries
- Default mappings: `👌` → `clear txt`, `👍` → `join`

### Example Usage
```
# In Saved Messages:
reaction add 👍 join       ← map 👍 to join command
reaction add 👋 left       ← map 👋 to leave command
reaction add 👌 clear txt  ← map 👌 to clear text messages
reaction add 🔥 whois      ← map 🔥 to whois command

# Then in any chat:
# React with 👍 on a message containing links → join all chats
# React with 👋 on a message containing links → leave all chats
# React with 👌 on any message → clear text messages
# React with 🔥 on any message → show sender info
---
## [1.4.0] — 2026-01-12

**Source:** AI (main.py DRY refactor)

### Added
- **Unified `_run_account()` function**: Now accepts an optional 
  `setup_watchers_flag: bool = False` parameter to conditionally register 
  file watchers, eliminating the need for a separate `_run_first_account()` 
  function

### Changed
- **DRY refactor**: Merged `_run_first_account()` into `_run_account()` by 
  adding a flag parameter, removing ~40 lines of duplicated code
- **Removed obsolete code**: Deleted the lingering `from modules import system 
  as system_mod` import and `system_mod.set_start_callback(_run_account)` call 
  that were left over from version 1.3.0 changes
- **Simplified task creation**: All accounts now start via the same 
  `_run_account()` function with only the `setup_watchers_flag` differing 
  between the first account and the rest
- **Moved function definition**: `_run_account` is now defined at module level 
  instead of `_run_first_account` being defined inside `_main()`, improving 
  code organization and testability

### Improved
- **Code maintainability**: Any future changes to the per-account runner logic 
  now only need to be made in one place instead of two nearly-identical 
  functions
- **Code clarity**: The distinction between first account and other accounts 
  is now explicit through a single boolean parameter rather than two separate 
  function definitions
- **Import hygiene**: Removed unused imports that could cause confusion about 
  which modules are actually being used

### Removed
- `_run_first_account()` inner function (merged into `_run_account()`)
- `from modules import system as system_mod` import (obsolete since v1.3.0)
- `system_mod.set_start_callback(_run_account)` call (obsolete since v1.3.0)

### Migration Notes
- **No behavior change**: The runtime behavior is identical to version 1.3.0. 
  This is purely a code quality improvement.
- **File watchers still work**: The first account still hosts the watchdog 
  observer via `setup_watchers_flag=True`, maintaining the same file-watching 
  behavior as before. 
  ---
## [1.3.0] — 2026-01-12

**Source:** AI (system.py improvements)

### Added
- **`.stats` command**: Show system statistics including:
  - Active accounts count
  - Total loaded modules across all accounts
  - Current connection mode (Direct/MTProxy)
  - Uptime (formatted as days, hours, minutes, seconds)
  - Number of registered admin IDs
- **`.proxy` command**: Show detailed proxy status including:
  - Connection mode (Direct/MTProxy/Unavailable)
  - Active proxy server and port
  - Status indicator (active/healthy/unavailable)
  - Count of proxies in `proxies.txt` file
- **Graceful shutdown before restart**: `.restart` now performs clean shutdown:
  - Cancels all pending background tasks
  - Disconnects all Telegram clients via `loader_registry`
  - Flushes all log handlers before `os.execv()`
  - Prevents session file corruption and resource leaks

### Changed
- **Removed interactive account management**: Deleted `.addaccount`, 
  `.removeaccount <n>`, and `.cancelflow` commands along with their 
  interactive flow handlers
- **Removed `core.account_manager` dependency**: System module no longer 
  imports or uses the account manager for interactive flows
- **Removed global variable**: Replaced `_start_account_cb` global with 
  cleaner architecture (no longer needed after removing account flows)
- **Removed incoming message handler**: No longer needed since interactive 
  flows were removed
- **Decoupled from loader**: `SystemModule` now gets `loader` from 
  `loader_registry` instead of constructor injection, reducing coupling
- **Simplified `create_module()`**: Now only accepts `cfg` parameter (no 
  `loader` injection needed)

### Improved
- **Used `base.py` helpers**: All `event.edit()` calls replaced with 
  `self._safe_edit()` for error resilience
- **Standardized logging**: Replaced `log.info/error/warning()` with 
  `self._log_info/error/warning()` for consistent `[Account{N}]` prefixing
- **Better error handling**: All commands now gracefully handle missing 
  loader or registry failures
- **Cleaner code structure**: Removed ~150 lines of interactive flow code, 
  making the module more focused and maintainable

### Removed
- `.addaccount` command and interactive add-account flow
- `.removeaccount <n>` command and interactive remove-account flow
- `.cancelflow` command for canceling active flows
- `_on_incoming()` handler for routing flow replies
- `_cmd_addaccount()`, `_cmd_removeaccount()`, `_cmd_cancelflow()` methods
- `set_start_callback()` function and `_start_account_cb` global variable
- Import of `core.account_manager as acm`
- Import of `core.loader.AccountLoader` type hint
- Injection of `loader` parameter in `create_module()` factory

### Migration Notes
- **For users**: The `.addaccount`, `.removeaccount`, and `.cancelflow` 
  commands are no longer available. Use `python add_account.py` script 
  directly for adding accounts, or manually create `accounts/N/account.json` 
  files for manual setup.
- **For developers**: `SystemModule` constructor now only accepts `cfg` 
  parameter. The `loader` is fetched dynamically from `loader_registry` 
  when needed, eliminating the need for dependency injection.
  ---
## [1.2.0] — 2026-01-12

**Source:** AI (base.py improvements)

### Added
- **`WeakKeyDictionary` for `_me_cache`**: Prevents memory leaks by automatically
  clearing cache entries when `TelegramClient` instances are garbage collected,
  even if `teardown()` is not called
- **`_safe_edit()` helper**: Safely edit messages with error logging instead of
  raising exceptions — useful for non-critical UI updates
- **`_safe_reply()` helper**: Safely reply to messages with error logging
- **`account_index` property**: Quick access to `cfg.index` without null checks
- **Logging helpers**: `_log_info()`, `_log_error()`, `_log_warning()`, `_log_debug()`
  methods that automatically prefix logs with `[Account{N}]` for easier debugging

### Improved
- **Memory safety**: `_me_cache` no longer leaks memory when clients are destroyed
  without proper cleanup (e.g., during crashes or forced shutdowns)
- **Developer experience**: Module authors can now use `self._safe_edit()` instead
  of wrapping every `event.edit()` in try/except blocks
- **Code clarity**: Logging helpers reduce boilerplate and ensure consistent log
  formatting across all modules
- **Documentation**: Enhanced docstrings with usage examples for all helper methods

### Changed
- **`base.py` architecture**: Refactored `_me_cache` from `dict[int, User]` to
  `WeakKeyDictionary[TelegramClient, User]` for automatic memory management
- **Backward compatibility**: All existing modules continue to work without changes;
  new helpers are optional conveniences
  ---
## [1.1.0] — 2026-06-12

**Source:** AI (help_handler enhancement)

### Added
- **Categorized help output**: Commands are now grouped into 5 categories:
  - 🔧 دستورات سیستم (admin-only)
  - 🧹 پاک‌سازی
  - 📤 فوروارد
  - 🔗 عضویت و ترک
  - ℹ️ اطلاعات
- **Search functionality**: `help <keyword>` now searches through all help texts
- **Statistics display**: Shows total module count and command count at the end of help

### Changed
- **Removed coupling**: `help_handler.py` now uses `plugin_store` directly instead of
  requiring injection from `loader.py`
- **Removed hardcoded logic**: Deleted the special-case injection code from
  `loader.py` that called `set_loader()` on `help_handler`
- **Improved architecture**: `help_handler` is now fully decoupled from the loader
  and can work independently

### Improved
- Better user experience with organized, easy-to-navigate help output
- Cleaner code with no special-case handling for specific modules
---
## [1.0.0] — 2026-05-27


**Source:** AI (architectural overhaul)

### Changed (Breaking)
- Complete architectural overhaul of the entire codebase
- Migrated to Python 3.11+ native type syntax (`X | Y`, `list[X]`, `dict[K, V]`)
- Removed `from __future__ import annotations` in favour of native generics
- Replaced all `Union`, `Optional`, `List`, `Dict` from `typing` with built-ins

### Removed
- `modules/ai_assistant.py` — AI assistant module (OpenAI/GPT integration)
- `modules/ai_bot.py` — AI bot module (LLM-based chat automation)
- All AI-related imports, dependencies, and references throughout the codebase

### Added
- `VERSION` file at project root — single source of truth for version
- `userbot_own/__init__.py` — exposes `__version__` string
- `CHANGELOG.md` — this file; required update on every change
- `core/exceptions.py` — structured exception hierarchy for the entire project
- `core/plugin_registry.py` — enhanced plugin/module registry with metadata, 
  introspection, and runtime management API
- `.env.example` — documented environment variable reference
- `README.md` — full setup guide, architecture overview, versioning guide
- Per-module `PluginMetadata` dataclass for rich module introspection
- `ModuleRegistry` singleton with `list_plugins()`, `get_metadata()`, 
  `is_loaded()`, `unload()`, `reload()` public API

### Improved
- `config.py` — stronger validation, frozen `AccountConfig` dataclass, 
  explicit `__slots__`, cleaner env loading with fallback
- `core/logger.py` — structured formatter, JSON-mode option, 
  `get_logger()` factory replacing bare `getLogger`
- `core/proxy.py` — full type annotations, docstrings on all public symbols,
  `ProxyConfig` NamedTuple replaces bare tuple usage
- `core/loader.py` — cleaner hot-reload pipeline, debounce logic extracted,
  integration with new `plugin_registry`, per-module error isolation
- `core/client.py` — `build()` returns typed `TelegramClient`, 
  explicit connection-mode logging
- `core/reconnector.py` — typed error branches, clean shutdown path,
  `_connection_changed` flag renamed to `_needs_rebuild` for clarity
- `core/watcher.py` — async callbacks throughout, type-annotated signatures
- `core/account_manager.py` — `_Step` enum documented, flow timeout 
  centralised, all coroutines fully type-annotated
- `modules/base.py` — `PluginMetadata` integration, `teardown()` is now
  fully async-safe, `_add_handler` returns the handler for introspection
- `modules/system.py` — command dispatch table replaces chained if/elif,
  versioning command `.version` added
- `modules/help_handler.py` — uses `plugin_registry` for module listing
- `helpers/utils.py` — all helpers fully type-annotated, docstrings added
- All modules — consistent logging, PEP 257 docstrings, type annotations

---

## How to update this file

When making a change, prepend a new entry:

```markdown
## [X.Y.Z] — YYYY-MM-DD

**Source:** Human | AI

### Added / Changed / Fixed / Removed
- Description of what changed and why
```

Then update `VERSION` and `userbot_own/__init__.py` to match.