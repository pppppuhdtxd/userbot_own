"""
userbot_own/core/exceptions.py
==================
Structured exception hierarchy for the entire userbot project.

All application-level exceptions inherit from ``UserbotError`` so callers
can catch the entire tree with a single ``except UserbotError`` clause while
still being able to target specific sub-types when needed.

Hierarchy
---------
UserbotError
├── LoaderError
│   └── ModuleImportError
└── RegistryError
    └── LoaderNotFoundError

v3.0.11: a full-repo grep-verified audit found that only
``ModuleImportError`` and ``LoaderNotFoundError`` (and the ``UserbotError``
/ ``LoaderError`` / ``RegistryError`` parents structurally required by
them) are actually raised or caught anywhere in the codebase. Nine classes
were removed as confirmed-unreferenced dead code: ``ConfigError``,
``AccountConfigError`` (config validation currently fails a different
way — see config/loader.py — this branch was never wired up),
``ConnectionManagerError``, ``ProxyError``, ``AuthError`` (leftover from
the MTProxy support dropped before this refactor), and ``FlowError``,
``FlowAlreadyActiveError``, ``FlowExpiredError`` (leftover from the
account-management flow system removed in v3.0.1 — see README FAQ). The
original audit that approved this change flagged 6 of these 9
(``ProxyError``, ``AuthError``, ``ConnectionManagerError``,
``FlowAlreadyActiveError``, ``FlowExpiredError``, ``ModuleSetupError``);
the other 3 (``ConfigError``, ``AccountConfigError``, and the ``FlowError``
parent of the two already-flagged flow exceptions) turned up during this
pass and are removed for the same reason and documented here for the
same transparency.
"""


class UserbotError(Exception):
    """Base class for all userbot application exceptions."""


# ── Loader / Plugin ───────────────────────────────────────────────────────────

class LoaderError(UserbotError):
    """Raised by AccountLoader when a module cannot be loaded."""


class ModuleImportError(LoaderError):
    """Raised when a module file cannot be imported."""

    def __init__(self, stem: str, cause: BaseException) -> None:
        self.stem  = stem
        self.cause = cause
        super().__init__(f"Cannot import module '{stem}': {cause}")


# ── Module registry ───────────────────────────────────────────────────────────

class RegistryError(UserbotError):
    """Raised by the module or account registry."""


class LoaderNotFoundError(RegistryError):
    """Raised when an AccountLoader is requested for an unknown account index."""

    def __init__(self, account_index: int) -> None:
        super().__init__(f"No loader registered for account #{account_index}.")
