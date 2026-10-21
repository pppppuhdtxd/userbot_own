"""
userbot_own/modules
════════════════════════════════════════════════════════════════
Plugin modules. Every module follows the contract in
``userbot_own/modules/_infra/base.py``: a ``Module`` subclass plus a
module-level ``create_module(context)`` factory.

Layout (v3.2.3)
────────────────
``_infra/``     shared infrastructure — NOT plugins. Skipped by
                discovery because of the leading underscore.
``single/``     one logical module per ``.py`` file.
``packages/``   one logical module per folder.

Both roots are module roots, and the loader treats a single file and
a package identically — the only difference is where the code lives.
A module in ``single/`` also has a sibling ``<name>_info.py`` with its
help texts and metadata; a package keeps the same data in its
``info.py``.

``base.py``, ``router.py`` and ``bridge.py`` at this level are
compatibility re-exports of the real files in ``_infra/``, so the old
import paths keep working for anything written against them.

Bug fix note: this file previously contained an accidental duplicate
of userbot/__init__.py's VERSION-reading logic — wrong docstring
header ("userbot/__init__.py"), a different and inconsistent
fallback version string, and no code anywhere that actually read
`userbot_own.modules.__version__`. It served no purpose and has been
replaced with a normal, minimal package init. The real, single
source of truth for `__version__` is userbot/__init__.py.
════════════════════════════════════════════════════════════════
"""
