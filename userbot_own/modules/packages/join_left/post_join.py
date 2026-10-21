"""
userbot_own/modules/packages/join_left/post_join.py
════════════════════════════════════════════════════════════════
The per-chat actions that run right after a successful join:
mute the chat, then archive it.

Both are load-bearing, and both report an honest boolean. The mute
matters because Telegram moves an *unmuted* archived chat back to the
main list when a new message arrives; a muted one stays archived. The
archive matters because, under the exclude_archived design, an
un-archived joined chat is visible in every type-flag folder.

The shared retry helper logs every give-up at WARNING — a silent
failure here is no longer acceptable, because the chat it affects
stays visible where it should not be.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.account import UpdateNotifySettingsRequest
from telethon.tl.functions.folders import EditPeerFoldersRequest
from telethon.tl.types import InputFolderPeer, InputNotifyPeer, InputPeerNotifySettings

from userbot_own.core.logging_setup import get_logger
from userbot_own.helpers.utils import flood_seconds as _flood_seconds
from userbot_own.modules.packages.join_left.const import (
    _ACTION_FLOOD_CAP,
    _ACTION_MAX_ATTEMPTS,
    _ARCHIVE_FOLDER_ID,
    _FLOOD_PAD,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

# Module-level logger — these are free functions, not methods of the
# Module class, so they have no `self._log` to write to.
log = get_logger(__name__)

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


