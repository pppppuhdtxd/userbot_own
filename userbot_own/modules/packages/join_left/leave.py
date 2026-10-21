"""
userbot_own/modules/packages/join_left/leave.py
════════════════════════════════════════════════════════════════
Leaving every chat in the joined* folders and resetting the folders.

Used by the `folder` command (a full reset) and by the post-leave
cleanup path. Paced and FloodWait-aware, and every failure is counted
and logged rather than swallowed.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.messages import UpdateDialogFilterRequest
from telethon.tl.types import DialogFilter, InputPeerSelf

from userbot_own.core.logging_setup import get_logger
from userbot_own.helpers.utils import flood_seconds as _flood_seconds
from userbot_own.helpers.utils import leave_dialog
from userbot_own.modules.packages.join_left.const import (
    _FLOOD_PAD,
    _LEFT_INTER_DELAY,
    _LEFT_MAX_FW_RETRIES,
)
from userbot_own.modules.packages.join_left.folders import (
    _create_joined_folder,
    _find_all_joined_folders,
    _folder_title,
    _get_folders,
    _invalidate_folder_cache,
    _peer_id,
    _sweep_legacy_exclusions,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

# Module-level logger — these are free functions, not methods of the
# Module class, so they have no `self._log` to write to.
log = get_logger(__name__)

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


