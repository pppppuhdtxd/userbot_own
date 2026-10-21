"""
userbot_own/modules/single
════════════════════════════════════════════════════════════════
Single-file modules — one logical module per .py file.

Each module here follows the contract in
``userbot_own/modules/_infra/base.py``: a ``Module`` subclass plus a
module-level ``create_module(context)`` factory.

Every module in this folder also has a sibling ``<name>_info.py``
holding its help texts and metadata — data only, no imports. The
module imports those values and copies them onto the class, which is
how ``help_handler.py`` and ``core/loader.py`` read them.

v3.2.3: this folder is one of two module roots. See
``userbot_own/modules/__init__.py`` for the full layout.
════════════════════════════════════════════════════════════════
"""
