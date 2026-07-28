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
├── ConfigError
│   └── AccountConfigError
├── ConnectionError         (not to be confused with built-in ConnectionError)
│   ├── ProxyError
│   └── AuthError
├── LoaderError
│   ├── ModuleImportError
│   └── ModuleSetupError
├── FlowError
│   ├── FlowAlreadyActiveError
│   └── FlowExpiredError
└── RegistryError
"""


class UserbotError(Exception):
    """Base class for all userbot application exceptions."""


# ── Configuration ─────────────────────────────────────────────────────────────

class ConfigError(UserbotError):
    """Raised when the configuration is invalid or cannot be loaded."""


class AccountConfigError(ConfigError):
    """Raised when a specific account's configuration is invalid."""

    def __init__(self, index: int, reason: str) -> None:
        self.index  = index
        self.reason = reason
        super().__init__(f"Account #{index}: {reason}")


# ── Connection ────────────────────────────────────────────────────────────────

class ConnectionManagerError(UserbotError):
    """Raised for connection-manager failures (distinct from built-in ConnectionError)."""


class ProxyError(ConnectionManagerError):
    """Raised when no working proxy is available and direct connection fails."""


class AuthError(ConnectionManagerError):
    """Raised when an account cannot be authenticated."""


# ── Loader / Plugin ───────────────────────────────────────────────────────────

class LoaderError(UserbotError):
    """Raised by AccountLoader when a module cannot be loaded."""


class ModuleImportError(LoaderError):
    """Raised when a module file cannot be imported."""

    def __init__(self, stem: str, cause: BaseException) -> None:
        self.stem  = stem
        self.cause = cause
        super().__init__(f"Cannot import module '{stem}': {cause}")


class ModuleSetupError(LoaderError):
    """Raised when a module's setup() raises."""

    def __init__(self, stem: str, cause: BaseException) -> None:
        self.stem  = stem
        self.cause = cause
        super().__init__(f"Module '{stem}' setup() failed: {cause}")


# ── Interactive flows ─────────────────────────────────────────────────────────

class FlowError(UserbotError):
    """Base class for account management flow errors."""


class FlowAlreadyActiveError(FlowError):
    """Raised when an admin tries to start a flow while one is already running."""

    def __init__(self, admin_id: int) -> None:
        super().__init__(f"Admin {admin_id} already has an active flow.")


class FlowExpiredError(FlowError):
    """Raised when a flow is accessed after its timeout."""


# ── Module registry ───────────────────────────────────────────────────────────

class RegistryError(UserbotError):
    """Raised by the module or account registry."""


class LoaderNotFoundError(RegistryError):
    """Raised when an AccountLoader is requested for an unknown account index."""

    def __init__(self, account_index: int) -> None:
        super().__init__(f"No loader registered for account #{account_index}.")
