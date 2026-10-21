"""
userbot_own/modules_extra/packages
════════════════════════════════════════════════════════════════
Package-style extra modules — one logical module per folder.

Same contract as the core modules in ``userbot_own/modules/packages/``:
the folder's ``__init__.py`` exports a ``Module`` subclass and a
module-level ``create_module(context)`` factory, and keeps the module's
help texts and metadata in a sibling ``info.py`` (data only, no imports).

A module here is only loaded for accounts that have explicitly enabled
it (see ``enabled_modules.json``).
════════════════════════════════════════════════════════════════
"""
