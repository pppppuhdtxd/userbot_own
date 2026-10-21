"""
userbot_own/modules/base.py
════════════════════════════════════════════════════════════════
Compatibility re-export — the real module now lives in
``userbot_own/modules/_infra/base.py``.

v3.2.3: the shared plugin infrastructure moved into a dedicated
``_infra/`` package so that ``modules/`` could hold two kinds of
module side by side:

- ``modules/single/<name>.py``  — a module in one file
- ``modules/packages/<name>/``  — a module as a package

The underscore prefix is the project's existing convention for
"not a plugin": the loader skips any file whose name starts with
``_`` (``core/loader.py``), so ``_infra/`` is invisible to module
discovery without any special-casing.

This file keeps the old import path working, so third-party code —
and ``modules_extra/`` plugins written against the previous release —
can still do ``from userbot_own.modules.base import Module`` and
keep working unchanged.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from userbot_own.modules._infra.base import Module, ModuleContext

__all__ = ["Module", "ModuleContext"]
