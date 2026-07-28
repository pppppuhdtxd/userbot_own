"""
userbot_own/modules
════════════════════════════════════════════════════════════════
Plugin modules (commands). Every .py file here (other than base.py,
router.py, and bridge.py, which are shared infrastructure) is a
hot-reloadable plugin, following the contract in modules/base.py:
a Module subclass plus a module-level `create_module(context)`
factory function.

Bug fix note: this file previously contained an accidental duplicate
of userbot/__init__.py's VERSION-reading logic — wrong docstring
header ("userbot/__init__.py"), a different and inconsistent
fallback version string, and no code anywhere that actually read
`userbot_own.modules.__version__`. It served no purpose and has been
replaced with a normal, minimal package init. The real, single
source of truth for `__version__` is userbot/__init__.py.
════════════════════════════════════════════════════════════════
"""
