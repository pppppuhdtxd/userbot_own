"""
userbot_own/modules/packages
════════════════════════════════════════════════════════════════
Package-style modules — one logical module per folder.

A module here is a folder whose ``__init__.py`` exports the same
contract every single-file module uses: a ``Module`` subclass and a
module-level ``create_module(context)`` factory. The loader discovers
it the same way and gives it the same lifecycle.

The reason a module becomes a package is size: the logic is split
into focused files (``const.py``, ``handlers.py``, ``info.py``, …) so
each part can be read on its own instead of scrolling through one
giant file.

v3.2.3: this folder is one of two module roots. See
``userbot_own/modules/__init__.py`` for the full layout.
════════════════════════════════════════════════════════════════
"""
