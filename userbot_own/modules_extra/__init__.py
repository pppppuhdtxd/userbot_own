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

Two forms are accepted, and both work identically:

- a single file — ``my_mod.py``
- a package   — ``my_mod/__init__.py`` (+ any helper files beside it)

Disabled modules are never imported — no import cost, no handler
registration, no `help` visibility — so dropping a large collection of
optional modules here has no effect on accounts that haven't opted
into them. See ``core/loader.py``'s module docstring for the full
mechanics.
════════════════════════════════════════════════════════════════
"""
