"""
userbot_own/modules_extra
════════════════════════════════════════════════════════════════
Optional plugin modules (v3.1.0). Every ``.py`` file here follows the
exact same contract as ``userbot_own/modules/`` — a ``Module``
subclass plus a module-level ``create_module(context)`` factory (see
``modules/base.py``) — with one difference: a file in this directory
is only loaded for accounts where its stem appears in that account's
own ``data/settings/account{N}/enabled_modules.json``. New extra
modules are disabled by default; enable one with
``.extra enable <name>`` (see ``modules/module_manager.py``).

Disabled modules are never imported — no import cost, no handler
registration, no `help` visibility — so dropping a large collection of
optional modules here has no effect on accounts that haven't opted
into them. See ``core/loader.py``'s module docstring for the full
mechanics.
════════════════════════════════════════════════════════════════
"""
