"""
userbot_own/modules/packages/join_left/types.py
════════════════════════════════════════════════════════════════
Result records for the join_left module.

These are the *outcomes* of the module's work, not its behaviour:

- ``PostJoinResult`` — what happened to one joined chat (mute / folder /
  archive). Returned by ``_post_join_actions()`` and reported in the
  join summary.
- ``_PendingActions`` — a joined chat whose post-join steps have not
  all succeeded yet, queued for the retry sweep.
- ``EnsureResult`` — what one ``ensure_exclude_archived()`` run did.

Keeping them in one place makes "what state is a chat in?" answerable
by reading a single file.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from dataclasses import dataclass, field


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


