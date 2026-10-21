"""
userbot_own/modules_extra
════════════════════════════════════════════════════════════════
Optional plugin modules. Every module here follows the exact same
contract as ``userbot_own/modules/`` — a ``Module`` subclass plus a
module-level ``create_module(context)`` factory (see
``modules/_infra/base.py``) — with one difference: a module in this
directory is only loaded for accounts where its name appears in that
account's own ``data/settings/account{N}/enabled_modules.json``. New
extra modules are disabled by default; enable one with
``.extra enable <name>`` (see ``modules/single/module_manager.py``).

Layout (v3.2.4)
---------------
``single/``     one logical module per ``.py`` file
``packages/``   one logical module per folder

The layout mirrors ``userbot_own/modules/`` exactly, so an extra module
is written the same way as a core one.

Two forms are accepted, and both work identically:

- a single file — ``my_mod.py``
- a package   — ``my_mod/__init__.py`` (+ any helper files beside it)

The old flat layout (a file or folder directly in ``modules_extra/``) is
still discovered, so a plugin written against an earlier release keeps
working without being moved.

Disabled modules are never imported — no import cost, no handler
registration, no `help` visibility — so dropping a large collection of
optional modules here has no effect on accounts that haven't opted
into them. See ``core/loader.py``'s module docstring for the full
mechanics.
════════════════════════════════════════════════════════════════
"""
