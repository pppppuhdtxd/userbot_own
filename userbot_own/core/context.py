"""
userbot_own/core/context.py
════════════════════════════════════════════════════════════════
ModuleContext — everything a Module needs, assembled once by the
composition root and passed into every module's constructor.

This is the concrete mechanism behind constructor-injection for the
plugin layer. Before this refactor, module authors had two
inconsistent options: the loader's already-present but rarely-used
two-argument `create_module(cfg, loader)` hook, or (what most
modules actually did) importing the module-level `loader_registry`
global directly — system.py, help_handler.py, and
reaction_commands.py all did the latter. Every module now receives
the exact same ModuleContext shape, whether or not it uses every
field. That uniformity is what lets the loader instantiate any
module identically, and lets a new module author import one type
instead of guessing which globals happen to be needed.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from dataclasses import dataclass

from userbot_own.config.models import AccountConfig, Settings
from userbot_own.core.registry import AccountLoaderRegistry, AccountRegistry


@dataclass(frozen=True, slots=True)
class ModuleContext:
    """
    Bundle of everything a plugin module may depend on.

    Attributes:
        cfg:              This account's own immutable AccountConfig.
        settings:         Global runtime settings (backoff, history_limit,
                          log_level) — replaces reaching for `config.HISTORY_LIMIT`
                          etc. as module-level globals. Only clearer.py reads
                          one of these fields (history_limit) today, but the
                          whole Settings object is provided for the same
                          uniformity reason as the other fields here.
        loader_registry:  Application-scoped account-index -> AccountLoader map.
                          Deliberately cross-account, not per-account — see
                          registry.py's module docstring (system.py's `.stats`
                          command reports on every running account, not just
                          its own).
        account_registry: Application-scoped, mutable registry of every
                          configured AccountConfig — replaces the old
                          `config.ACCOUNTS` global list that account_manager.py
                          appended/removed from at runtime.
    """
    cfg: AccountConfig
    settings: Settings
    loader_registry: AccountLoaderRegistry
    account_registry: AccountRegistry


__all__ = ["ModuleContext"]
