"""
userbot_own/modules_extra/single
════════════════════════════════════════════════════════════════
Single-file extra modules — one logical module per .py file.

Same contract as the core modules in ``userbot_own/modules/single/``:
a ``Module`` subclass plus a module-level ``create_module(context)``
factory. Each module also has a sibling ``<name>_info.py`` with its
help texts and metadata — data only, no imports.

A module here is only loaded for accounts that have explicitly enabled
it (see ``enabled_modules.json``).
════════════════════════════════════════════════════════════════
"""
