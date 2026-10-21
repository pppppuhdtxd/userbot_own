"""
userbot_own/modules/_infra
════════════════════════════════════════════════════════════════
Shared plugin infrastructure — NOT a module root.

Nothing in here is ever loaded as a plugin. The underscore prefix is
the project's existing convention for exactly that: the loader skips
every path whose name starts with ``_`` (``core/loader.py``), so this
package is invisible to module discovery without any special-casing
in the discovery code.

Contents
--------
``base.py``    the ``Module`` base class and the module contract
``router.py``  ``CommandRouter`` — declarative first-token dispatch
``bridge.py``  ``MockEvent`` — the adapter that lets a reaction
               trigger run a normal command handler

The old import paths (``userbot_own.modules.base`` and friends) still
work: thin re-export shims live one level up.
════════════════════════════════════════════════════════════════
"""
