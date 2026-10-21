"""
userbot_own/core/loader.py
════════════════════════════════════════════════════════════════
Per-account plugin loader with hot-reload support.

Each account has its own independent `AccountLoader` instance:

• Modules are instantiated separately per account.
• Hot-reload of one account never affects the others.
• Each module has access to its own `ModuleContext` (cfg + injected
  application-scoped services).

`watch()` monitors the module roots for `.py` file changes and triggers
hot-reload automatically (requires the `watchdog` package).

`watch_files()` accepts additional `(path, callback)` pairs so external
files like `account.json` can also drive callbacks.

Module layout (v3.2.3 / v3.2.4)
──────────────────────────────
A module is a *logical unit with a name*, written either as one file
or as a package. The loader treats both identically — it only ever
looks for the `create_module(context)` factory and the `Module`
subclass:

    modules/single/<name>.py            one-file module
    modules/single/<name>_info.py       its metadata (data only)
    modules/packages/<name>/            package module
        __init__.py                     create_module() + public API
        info.py                         its metadata (data only)

`modules_extra/` has the SAME shape (`single/` + `packages/`), so a
user-written plugin is written exactly like a core one. The old flat
layout — a file or folder directly in the root — is still discovered,
so plugins written against 3.2.x keep working untouched.

`modules/_infra/` holds the shared infrastructure (`base.py`,
`router.py`, `bridge.py`). It is NOT a module root: the leading
underscore is the project's existing convention for "not a plugin",
and the loader skips it without any special-casing.

v3.1.0: optional second root, `extra_modules_dir` (`modules_extra/`).
Files there behave exactly like core `modules/` files (same `Module`
contract, same hot-reload) with one difference: a file is only ever
imported — and therefore only ever appears in `self._loaded`, `help`,
`.modules`, etc. — if its stem is in this account's own
`enabled_modules.json` (`data/settings/account{N}/`). Disabled extra
modules are never imported, so there is no per-event "is this
enabled?" check anywhere on the hot path; the gate is entirely a
load-time decision. Both roots feed the *same* `self._loaded` dict, so
every existing consumer of `list_modules()` / `get_module()`
(help_handler.py, system.py's `.modules`/`.stats`)
needed zero changes for this. See `enable_extra_module()` /
`disable_extra_module()` for the runtime toggle path, and
`_on_enabled_file_changed()` for what happens when the JSON file is
edited directly instead of via a chat command.

DI note: `create_module()` factories now always receive exactly one
argument — the account's `ModuleContext`. The original loader supported
two different factory signatures (`create_module(cfg)` or
`create_module(cfg, loader)`, detected via `inspect.signature`) because
most modules reached for a module-level `loader_registry` global instead
of using the two-argument form. Now that every module receives the same
`ModuleContext` (which already carries the application-scoped
loader_registry and account_registry), that branching is gone — there is
exactly one supported factory shape.

v3.0.11: this used to also call `context.plugin_store.upsert()` /
`.remove()` on every load/reload/unload to keep a `PluginMetadataStore`
up to date. That store had no reader anywhere in the codebase and has
been removed (see registry.py) — load/reload/unload no longer report to
anything beyond this loader's own logging.
════════════════════════════════════════════════════════════════
"""
import asyncio
import importlib.util
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from telethon import TelegramClient

from userbot_own.core.context import ModuleContext
from userbot_own.core.exceptions import ModuleImportError
from userbot_own.core.logging_setup import get_logger
from userbot_own.helpers.utils import read_json_file, write_json_file_atomic
from userbot_own.modules.base import Module

log = get_logger(__name__)

#: Files in modules/ that are shared infrastructure, not hot-reloadable
#: plugins — never imported as a Module by the loader. Originally this was
#: just {"base"} (checked separately in three places below); router.py and
#: bridge.py are new shared infrastructure introduced by this refactor, so
#: they're added here too, and all three checks now reference this single
#: set instead of three independent hardcoded comparisons.
_INFRASTRUCTURE_STEMS: frozenset[str] = frozenset({"base", "router", "bridge"})

#: File-watcher debounce: the window within which a repeated event for the
#: same path is ignored, and the maximum number of remembered paths before
#: the stale ones are pruned (v3.2.2 — B-28).
_DEBOUNCE_WINDOW = 1.5
_DEBOUNCE_MAX_ENTRIES = 256

#: v3.2.3: suffix of the metadata file that sits next to a single-file
#: module (``clearer.py`` → ``clearer_info.py``). It is DATA, not a
#: module, and is skipped by discovery.
_INFO_SUFFIX = "_info"

#: v3.2.4: the folder names that make up the module layout. They are
#: reserved — a module may not be called "single" or "packages", and
#: they are skipped when the flat (backwards-compatible) root is
#: scanned, so the layout folders are not mistaken for package modules.
_LAYOUT_FOLDERS: frozenset[str] = frozenset({"single", "packages"})

#: v3.2.3: the PACKAGE root (``.../userbot_own``), used to derive a
#: package's real dotted module name
#: (``userbot_own.modules.packages.join_left``) so the import cache can be
#: purged under that name as well as the synthetic one the loader uses.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


class AccountLoader:
    """
    Manages all plugins for a specific account.

    Each ``AccountLoader`` maintains an independent set of ``Module``
    instances bound to a single ``TelegramClient``.

    Args:
        context:           This account's ModuleContext (cfg + injected
                            services), passed unchanged to every plugin's
                            `create_module()`.
        modules_dir:       Directory containing core ``.py`` plugin files —
                            always loaded, every account, no opt-out.
        extra_modules_dir: Optional (v3.1.0) second directory of ``.py``
                            plugin files that are only loaded if their stem
                            is in this account's own `enabled_modules.json`
                            (`context.cfg.settings_dir /
                            "enabled_modules.json"`). ``None`` (the default)
                            disables the extra-module system entirely for
                            this loader — `list_available_extra()` then
                            returns ``[]`` and `enable_extra_module()` /
                            `disable_extra_module()` fail cleanly rather
                            than raising.
    """

    def __init__(
        self,
        context: ModuleContext,
        modules_dir: Path,
        extra_modules_dir: Path | None = None,
    ) -> None:
        self.context:     ModuleContext = context
        self.cfg                        = context.cfg
        self.modules_dir: Path          = modules_dir
        self.extra_modules_dir: Path | None = extra_modules_dir
        # v3.1.9: the old `self.label` (f"Account{N}") text prefix that used
        # to be manually re-embedded into every log message below has been
        # removed. Account attribution now comes from two other places that
        # didn't exist when that pattern was introduced: this instance's own
        # `self._log` is already bound to a name containing the account
        # index (see below), and `logger.contextualize(account=...)`,
        # entered once per account in composition_root.start_account(),
        # attaches the exact account index to every record emitted while
        # that account's task tree is running — including this loader's.
        # See CHANGELOG v3.1.9 for the full rationale.
        self._client:     TelegramClient | None = None

        # stem → (Module instance, Python module object). Populated from
        # BOTH modules_dir and extra_modules_dir — deliberately one dict,
        # not two, so every existing reader (list_modules(), get_module())
        # keeps working unchanged regardless of which
        # root a given stem came from.
        self._loaded: dict[str, tuple[Module, ModuleType]] = {}
        self._log:    logging.Logger = get_logger(f"loader.account{context.cfg.index}")

        # Extra (path, callback) pairs registered via watch_files()
        self._extra_watches: list[tuple[Path, Callable[[Path], None]]] = []

        # v3.1.0 — extra-module enablement state. In-memory set, treated as
        # a cache: populated from enabled_modules.json by
        # _load_enabled_extra() (called from load_all()) and kept in sync
        # incrementally afterward by enable_extra_module() /
        # disable_extra_module() / _on_enabled_file_changed() — never
        # re-read from disk on a hot path.
        self._enabled_extra: set[str] = set()
        self._enabled_file: Path | None = (
            context.cfg.settings_dir / "enabled_modules.json"
            if extra_modules_dir is not None else None
        )
        if self._enabled_file is not None:
            # Registered here (not in load_all()) because watch_files()
            # only requires "before watch() starts", and __init__ always
            # runs before watch() is ever called — this keeps the whole
            # extra-module-file-watching detail self-contained inside
            # AccountLoader instead of leaking into composition_root.py.
            self.watch_files([(self._enabled_file, self._on_enabled_file_changed)])

    # ── Internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _real_package_name(path: Path) -> str | None:
        """
        Return the REAL dotted package name of a package-style module.

        v3.2.3: a package is registered in ``sys.modules`` under two
        names — the synthetic one the loader uses
        (``_modules_a1.join_left``) and the real dotted one Python
        creates itself the moment the package executes a relative import
        (``userbot_own.modules.packages.join_left``).

        This method returns the real one, so the cache purge can remove
        both. A single-file module has no package and returns ``None``.

        The name is derived from the file's location relative to the
        project root, which is how Python itself would name it.
        """
        if path.stem != "__init__" or not path.parent.is_dir():
            return None
        try:
            rel = path.parent.resolve().relative_to(_PACKAGE_ROOT)
        except (ValueError, OSError):
            return None
        return "userbot_own." + ".".join(rel.parts)

    @staticmethod
    def _module_name_for_entry(path: Path) -> str:
        """
        Return the module's name for an entry-point file.

        v3.2.3: a single-file module is named after its file
        (``clearer.py`` → ``clearer``), but a package's entry point is
        always ``__init__.py`` — whose stem is the useless string
        ``"__init__"``. The package's real name is its FOLDER's name
        (``join_left/__init__.py`` → ``join_left``).

        Using the wrong one breaks relative imports inside the package:
        the module would be registered as ``_modules_a1.__init__``, and
        ``from .logic import VALUE`` would fail with
        ``No module named '_modules_a1'`` — because the package's
        parent package never existed under that name.
        """
        if path.stem == "__init__" and path.parent.is_dir():
            return path.parent.name
        return path.stem

    @staticmethod
    def _purge_module_cache(qual: str, real_package: str | None = None) -> None:
        """
        Remove *qual* and every one of its children from ``sys.modules``.

        v3.2.3: a package module ends up in ``sys.modules`` under TWO
        names, and BOTH have to go:

        - ``_modules_a1.join_left`` — the synthetic name the loader
          imports the package's ``__init__.py`` under.
        - ``userbot_own.modules.packages.join_left`` (and its
          ``.folders``, ``.handlers``, … children) — the REAL dotted
          name, which Python registers on its own the moment the package
          executes a relative import (``from .folders import …``).

        Purging only the synthetic name leaves the real package cached,
        so the next import re-executes ``__init__.py`` but hands back the
        OLD submodule objects — a hot-reload that silently keeps running
        the previous code. This was caught by testing the reload against
        a real package module, not by inspection.

        Args:
            qual:          the synthetic name, e.g. ``_modules_a1.join_left``.
            real_package:  the real dotted package name, if the module is
                           a package. ``None`` for a single-file module.
        """
        prefixes = [qual]
        if real_package:
            prefixes.append(real_package)

        for name in [
            n for n in sys.modules
            if any(n == p or n.startswith(p + ".") for p in prefixes)
        ]:
            sys.modules.pop(name, None)

    def _import(self, path: Path) -> tuple[Module, ModuleType] | None:
        """
        Import a plugin and instantiate its ``Module``.

        *path* is the module's **entry point** — a ``.py`` file for a
        single-file module, or a package's ``__init__.py`` for a
        package-style module. Both are loaded identically.

        Returns ``(instance, py_module)`` on success, ``None`` on failure.
        Errors are logged but never re-raised so a broken plugin does not
        bring down the whole account.

        v3.2.3: two things changed here for package support.

        1. The whole module is purged from ``sys.modules`` before the
           import — under BOTH names it can be registered under. A
           package's submodules (``join_left.folders``,
           ``join_left.handlers``, …) are cached as children of the
           package, so popping only the top-level name leaves the
           CHILDREN cached. A hot-reload would then re-execute the
           package's ``__init__.py`` but hand back the OLD submodule
           objects — silently serving stale code. (Verified against a
           real package: without the purge, an edited submodule keeps
           its previous contents after a reload.)

        2. A relative import inside a package works because
           ``spec_from_file_location`` sets ``submodule_search_locations``
           for a package entry point, which is what makes ``from . import
           folders`` resolve.
        """
        qual = f"_modules_a{self.cfg.index}.{self._module_name_for_entry(path)}"
        self._purge_module_cache(qual, self._real_package_name(path))

        try:
            spec = importlib.util.spec_from_file_location(qual, path)
            if spec is None or spec.loader is None:
                raise ModuleImportError(
                    self._module_name_for_entry(path),
                    ValueError("Cannot create module spec"),
                )

            py_mod = importlib.util.module_from_spec(spec)
            sys.modules[qual] = py_mod
            spec.loader.exec_module(py_mod)  # type: ignore[union-attr]

            factory = getattr(py_mod, "create_module", None)
            if callable(factory):
                instance = factory(self.context)
            else:
                instance = getattr(py_mod, "module", None)

            if not isinstance(instance, Module):
                self._log.warning(
                    "%s has no valid Module — skipped.", path.name
                )
                self._purge_module_cache(qual, self._real_package_name(path))
                return None

            # v3.2.3: the module's own `name` should match the name it is
            # registered under (its file/folder name). A mismatch is not
            # fatal — the registry key is the path, not the attribute —
            # but it is a strong hint that something was renamed on one
            # side only, so it is surfaced rather than left to be
            # discovered later. The same pattern help_handler.py uses for
            # an unknown `category`.
            entry_name = self._module_name_for_entry(path)
            if instance.name and instance.name != entry_name:
                self._log.warning(
                    "Module '%s' declares name=%r but is registered as "
                    "'%s' — the registry key is '%s'.",
                    entry_name, instance.name, entry_name, entry_name,
                )

            return instance, py_mod

        except ModuleImportError:
            raise
        except Exception as exc:
            self._log.exception("Import failed for %s: %s", path.name, exc)
            self._purge_module_cache(qual, self._real_package_name(path))
            return None

    def _do_load(self, stem: str, path: Path) -> bool:
        """
        Import, instantiate, and call ``setup()`` for a single plugin.

        Returns ``True`` on success, ``False`` on any failure.
        """
        assert self._client is not None, "load_all() must be called before _do_load()"

        result = self._import(path)
        if result is None:
            return False

        instance, py_mod = result
        try:
            instance.setup(self._client)
        except Exception as exc:
            self._log.exception(
                "setup() failed for %s: %s", stem, exc
            )
            # v3.2.3: purge the whole module (package + its submodules),
            # not just the top-level name — see _purge_module_cache().
            self._purge_module_cache(
                f"_modules_a{self.cfg.index}.{stem}",
                self._real_package_name(path),
            )
            return False

        self._loaded[stem] = (instance, py_mod)

        # v3.2.1: Telegram rejects any message over ~4096 chars. `help
        # <module>` chunks oversized help_extra instead of failing silently
        # now, but a bloated help_extra still means a multi-message page the
        # author never looked at — flag it at load time. 3900 leaves margin
        # below the limit for markup and splitting overhead.
        extra_len = len(getattr(instance, "help_extra", "") or "")
        if extra_len > 3900:
            self._log.warning(
                "%s.help_extra is %d chars (> 3900) — `help %s` will be split "
                "across multiple messages; consider splitting the text itself.",
                stem, extra_len, stem,
            )

        self._log.info("Loaded: %s", stem)
        return True

    def _do_unload(self, stem: str) -> None:
        """Call ``teardown()`` and remove a plugin from the loaded set."""
        assert self._client is not None
        if stem not in self._loaded:
            return
        instance, _ = self._loaded.pop(stem)
        try:
            instance.teardown(self._client)
        except Exception as exc:
            self._log.warning(
                "teardown() error for %s: %s", stem, exc
            )

    # ── Extra-module enablement state (v3.1.0) ──────────────────────────────

    def _load_enabled_extra(self) -> None:
        """
        (Re)populate ``self._enabled_extra`` from ``enabled_modules.json``.

        Called once from ``load_all()``. A missing file (first run) or a
        corrupt one both degrade to "nothing enabled" rather than raising —
        same defensive shape as every other settings file in this project
        (``read_json_file`` never raises).
        """
        if self._enabled_file is None:
            return
        data, err = read_json_file(self._enabled_file)
        if err is not None:
            self._log.error(
                "enabled_modules.json unreadable — treating as no "
                "extra modules enabled: %s", err,
            )
            self._enabled_extra = set()
            return
        if data is None:
            self._enabled_extra = set()
            return
        raw = data.get("enabled", [])
        self._enabled_extra = {s for s in raw if isinstance(s, str)}

    def _save_enabled_extra(self) -> None:
        """Persist ``self._enabled_extra`` to ``enabled_modules.json`` atomically."""
        if self._enabled_file is None:
            return
        data = {"enabled": sorted(self._enabled_extra)}
        err = write_json_file_atomic(self._enabled_file, data, indent=2)
        if err is not None:
            self._log.error(
                "Failed to save enabled_modules.json: %s", err
            )

    def _resolve_path(self, stem: str) -> tuple[Path, bool] | None:
        """
        Find *stem*'s entry point, checking ``modules_dir`` first, then
        ``extra_modules_dir``.

        Returns ``(path, is_extra)``, or ``None`` if not found in either
        root. Core modules always take priority — see
        ``enable_extra_module()`` for the corresponding same-name-collision
        guard on the extra-module side.

        v3.2.3: a module may be a single file (``<stem>.py``) or a package
        (``<stem>/__init__.py``). Both are accepted, in either root, and
        the *file* form wins when both exist for the same name (the
        package is then reported as a conflict rather than silently
        shadowed).
        """
        for root, is_extra in self._module_roots():
            file_form = root / f"{stem}.py"
            if file_form.exists():
                return file_form, is_extra
            pkg_form = root / stem / "__init__.py"
            if pkg_form.exists():
                return pkg_form, is_extra
        return None

    def _module_roots(self) -> list[tuple[Path, bool]]:
        """
        Return the module roots to search, as ``(directory, is_extra)``.

        v3.2.3: ``modules/`` holds two sub-roots — ``single/`` for
        one-file modules and ``packages/`` for package-style modules.

        v3.2.4: ``modules_extra/`` now has the SAME shape — ``single/``
        and ``packages/`` — so a user-written plugin is written exactly
        like a core one. The OLD flat layout (a ``.py`` file or a folder
        directly inside ``modules_extra/``) is still accepted, so
        plugins written against 3.2.x keep working untouched.

        The order is the precedence order: a single-file module in
        ``single/`` beats a package of the same name in ``packages/``,
        and any core module beats an extra one.
        """
        roots: list[tuple[Path, bool]] = []
        for base, is_extra in (
            (self.modules_dir, False),
            (self.extra_modules_dir, True),
        ):
            if base is None or not base.is_dir():
                continue
            for sub in ("single", "packages"):
                candidate = base / sub
                if candidate.is_dir():
                    roots.append((candidate, is_extra))
            # Compatibility: the flat layout (module files directly in
            # the root) is still discovered, so a plugin written for an
            # earlier release keeps loading. The sub-roots are listed
            # first, so they take precedence.
            #
            # The flat root is only a module root when it actually holds
            # module files — otherwise the `single/` and `packages/`
            # FOLDERS themselves would be mistaken for modules (they are
            # directories, and a directory with an __init__.py looks
            # exactly like a package module to the discovery code).
            if any(
                p.suffix == ".py" and not p.stem.startswith("_")
                for p in base.glob("*.py")
            ):
                roots.append((base, is_extra))
        return roots

    def _discover_modules(self, root: Path) -> list[tuple[str, Path]]:
        """
        Find every module in *root*, as ``(stem, entry_point)`` pairs.

        v3.2.3: a module is either

        - a ``.py`` file (``<stem>.py``), or
        - a package (``<stem>/__init__.py``).

        A file and a package with the same name in the same root is a
        conflict: the file wins and the package is reported, because
        silently loading one of them would make the other unreachable
        and unexplainable.

        Names starting with ``_`` and the shared-infrastructure stems
        are skipped — the same rule the loader has always used.
        """
        found: dict[str, Path] = {}
        packages: dict[str, Path] = {}

        for path in sorted(root.glob("*.py")):
            if path.stem.startswith("_") or path.stem in _INFRASTRUCTURE_STEMS:
                continue
            # v3.2.3: `<name>_info.py` is the metadata sibling of a
            # single-file module, not a module itself. It holds no
            # `create_module`, so it would only produce a "no valid
            # Module — skipped" warning on every startup.
            if path.stem.endswith(_INFO_SUFFIX):
                continue
            found[path.stem] = path

        for path in sorted(root.glob("*/")):
            if not path.is_dir():
                continue
            if path.name.startswith("_") or path.name in _INFRASTRUCTURE_STEMS:
                continue
            # v3.2.4: the layout folder names are reserved. When the flat
            # root is scanned (the backwards-compatible layout), the
            # `single/` and `packages/` folders would otherwise look like
            # package modules — they have an __init__.py, so discovery
            # would try to load them and log "no valid Module — skipped".
            if path.name in _LAYOUT_FOLDERS:
                continue
            entry = path / "__init__.py"
            if not entry.exists():
                continue
            packages[path.name] = entry

        for name, entry in packages.items():
            if name in found:
                self._log.warning(
                    "Both %s.py and %s/ exist in %s — using the .py file; "
                    "the package is unreachable.",
                    name, name, root.name,
                )
                continue
            found[name] = entry

        return sorted(found.items())

    @staticmethod
    def _entry_name(entry: Path) -> str:
        """Return the module name for an entry point (file or package)."""
        if entry.stem == "__init__" and entry.parent.is_dir():
            return entry.parent.name
        return entry.stem

    def _module_for_path(self, path: Path) -> tuple[str, Path, bool] | None:
        """
        Work out which module a changed file belongs to.

        v3.2.3: with package-style modules, a changed file is not always
        the module itself. Given a path, this returns
        ``(stem, root, is_extra)`` for the module that owns it, or
        ``None`` when the file is not part of any module.

        The rules:
        - ``<root>/<stem>.py``            -> the module is ``<stem>``
        - ``<root>/<stem>/__init__.py``   -> the module is ``<stem>``
        - ``<root>/<stem>/anything.py``   -> the module is ``<stem>``
          (a file inside a package belongs to that package's module)

        Files that are not part of a module — the shared infrastructure
        in ``_infra/``, the compatibility shims, ``__init__.py`` files of
        the roots themselves — return ``None`` and are never reloaded as
        modules.
        """
        try:
            resolved = path.resolve()
        except OSError:
            return None

        for root, is_extra in self._module_roots():
            root_resolved = root.resolve()
            if resolved == root_resolved or root_resolved not in resolved.parents:
                continue

            relative = resolved.relative_to(root_resolved)
            parts = relative.parts

            if not parts:
                continue

            if len(parts) == 1:
                # <root>/<name>.py — a single-file module, or the metadata
                # sibling of one (`<name>_info.py` belongs to `<name>`).
                if not parts[0].endswith(".py"):
                    continue
                stem = parts[0][:-3]
                if stem.startswith("_") or stem in _INFRASTRUCTURE_STEMS:
                    return None
                if stem.endswith(_INFO_SUFFIX):
                    # The metadata file belongs to the module it sits next
                    # to — editing the help text must reload that module.
                    owner = stem[: -len(_INFO_SUFFIX)]
                    if not (root_resolved / f"{owner}.py").exists():
                        return None
                    return owner, root, is_extra
                return stem, root, is_extra

            # <root>/<name>/... — a package (or a file inside one).
            folder = parts[0]
            if folder.startswith("_") or folder in _INFRASTRUCTURE_STEMS:
                return None
            if folder in _LAYOUT_FOLDERS:
                # v3.2.4: the layout folders are not modules.
                return None
            # Only a real package counts as a module.
            if not (root_resolved / folder / "__init__.py").exists():
                return None
            return folder, root, is_extra

        return None

    def _on_enabled_file_changed(self, path: Path) -> None:
        """
        ``watch_files()`` callback for external edits to
        ``enabled_modules.json`` (i.e. not made via
        ``enable_extra_module()`` / ``disable_extra_module()``).

        Diffs the file's new contents against the in-memory enabled-set and
        applies exactly the same load/unload calls the chat commands use,
        so an external edit and an in-chat command are indistinguishable in
        their effect. Deliberately does NOT call ``_save_enabled_extra()``
        at the end — the file was just externally written; rewriting it
        immediately would be presumptuous, and it isn't needed: the second
        this callback re-reads the file, ``self._enabled_extra`` already
        matches it, so nothing is lost by not echoing it back.
        """
        if self._client is None or self.extra_modules_dir is None:
            return

        data, err = read_json_file(self._enabled_file)
        if err is not None:
            self._log.error(
                "enabled_modules.json unreadable after external edit "
                "— ignoring this change: %s", err,
            )
            return

        new_enabled = (
            {s for s in data.get("enabled", []) if isinstance(s, str)}
            if data is not None else set()
        )

        to_enable = sorted(new_enabled - self._enabled_extra)
        to_disable = sorted(self._enabled_extra - new_enabled)

        for stem in to_enable:
            # v3.2.3/v3.2.4: the extra module may be a file or a package,
            # in either the new roots or the old flat layout.
            candidate = self._find_entry(stem, extra_only=True)
            if candidate is None:
                self._log.warning(
                    "enabled_modules.json now lists '%s', but no such "
                    "file exists in modules_extra/ — skipped.",
                    stem,
                )
                continue
            if stem in self._loaded:
                self._log.warning(
                    "enabled_modules.json now lists '%s', but that "
                    "name is already loaded (core-module collision?) — "
                    "skipped.", stem,
                )
                continue
            if self._do_load(stem, candidate):
                self._enabled_extra.add(stem)

        for stem in to_disable:
            self._do_unload(stem)
            self._enabled_extra.discard(stem)

        if to_enable or to_disable:
            self._log.info(
                "enabled_modules.json changed externally: +%d enabled, "
                "-%d disabled.", len(to_enable), len(to_disable),
            )

    # ── Public API ────────────────────────────────────────────────────────

    def get_module(self, stem: str) -> Module | None:
        """
        Return the loaded ``Module`` instance for *stem*, or ``None`` if it
        is not currently loaded.

        This is the public replacement for reaching into ``loader._loaded``
        directly. Callers that previously did::

            entry = loader._loaded.get(stem)
            instance, _ = entry if entry else (None, None)

        should now do::

            instance = loader.get_module(stem)

        Args:
            stem: The module file stem, e.g. ``"clearer"`` for ``clearer.py``.

        Returns:
            The ``Module`` instance if loaded, otherwise ``None``.
        """
        entry = self._loaded.get(stem)
        return entry[0] if entry is not None else None

    @property
    def client(self) -> TelegramClient | None:
        """
        The ``TelegramClient`` this loader is currently attached to, or
        ``None`` if no client has been bound yet (before ``load_all()``)
        or after the loader has been torn down.

        Public replacement for direct ``loader._client`` access.
        """
        return self._client

    # v3.2.2 (B-23): `unload_module()` was removed. It had ZERO call sites
    # in the project. Its "unload one module without reloading it" role is
    # now covered by `reload_module()`'s new deleted-file path (B-05),
    # which unloads a module whose file is gone.

    # ── Extra-module control (v3.1.0) ───────────────────────────────────────
    #
    # The only intended way for module_manager.py (or anything else) to
    # change or inspect extra-module enablement — mirrors get_module()'s
    # role as "the public replacement for reaching into _loaded directly".

    def _find_entry(self, stem: str, extra_only: bool = False) -> Path | None:
        """
        Find the entry point (file or package ``__init__.py``) of a module.

        v3.2.4: searches every applicable module root, so the new
        ``single/`` + ``packages/`` layout of ``modules_extra/`` works
        exactly like the core layout — and the old flat layout still
        works too.

        Args:
            stem:       The module's name.
            extra_only: Restrict the search to the extra roots.

        Returns:
            The entry point path, or ``None`` if the module is not found.
        """
        for root, is_extra in self._module_roots():
            if extra_only and not is_extra:
                continue
            for candidate in (
                root / f"{stem}.py",
                root / stem / "__init__.py",
            ):
                if candidate.exists():
                    return candidate
        return None

    def list_available_extra(self) -> list[str]:
        """
        Return every module name present in the extra module roots,
        regardless of enabled state — i.e. "available", not "active".

        v3.2.3: an extra module may be a single file (``<stem>.py``) or a
        package (``<stem>/__init__.py``); both are listed.

        v3.2.4: the extra roots follow the core layout
        (``single/`` + ``packages/``), with the old flat layout still
        accepted for backwards compatibility.

        Returns ``[]`` if this loader wasn't configured with an
        ``extra_modules_dir`` (never raises).
        """
        if self.extra_modules_dir is None:
            return []
        names: set[str] = set()
        for root, is_extra in self._module_roots():
            if not is_extra:
                continue
            for stem, _ in self._discover_modules(root):
                names.add(stem)
        return sorted(names)

    def is_extra_enabled(self, stem: str) -> bool:
        """Return whether *stem* is currently in this account's enabled-set."""
        return stem in self._enabled_extra

    def enable_extra_module(self, stem: str) -> tuple[bool, str]:
        """
        Enable an extra module for this account: load it now, then persist.

        Returns ``(success, message)`` — *message* is written to explain
        either outcome and is safe to show directly to the user (e.g. from
        a chat command), including the real import/setup error text on
        failure rather than a generic "something went wrong".
        """
        if self.extra_modules_dir is None:
            return False, "Extra modules are not configured for this account."
        if stem in self._enabled_extra:
            return False, f"'{stem}' is already enabled."
        # v3.2.3/v3.2.4: the extra module may be a file or a package, in
        # either the new single/ + packages/ roots or the old flat layout.
        entry = self._find_entry(stem, extra_only=True)
        if entry is None:
            return False, f"No such extra module: '{stem}'."
        if stem in self._loaded:
            return False, (
                f"'{stem}' conflicts with an already-loaded module of the "
                f"same name — refusing to shadow it."
            )
        if not self._do_load(stem, entry):
            return False, (
                f"'{stem}' failed to load — check this account's log for "
                f"the import/setup error."
            )
        self._enabled_extra.add(stem)
        self._save_enabled_extra()
        return True, f"'{stem}' enabled."

    def disable_extra_module(self, stem: str) -> tuple[bool, str]:
        """
        Disable an extra module for this account: tear it down now, then
        persist. Returns ``(success, message)``, same shape as
        ``enable_extra_module()``.
        """
        if self.extra_modules_dir is None:
            return False, "Extra modules are not configured for this account."
        if stem not in self._enabled_extra:
            return False, f"'{stem}' is not currently enabled."
        self._do_unload(stem)
        self._enabled_extra.discard(stem)
        self._save_enabled_extra()
        return True, f"'{stem}' disabled."

    # v3.2.2 (B-23): `unload_all()` was removed. It had ZERO call sites —
    # `load_all()` already unloads every plugin before loading them again,
    # and shutdown tears the client down wholesale, so nothing ever needed
    # a standalone "unload everything" call.

    def load_all(self, client: TelegramClient) -> None:
        """
        Bind *client*, unload all current plugins, and (re)load every core
        ``.py`` file in ``modules_dir`` plus every *enabled* ``.py`` file in
        ``extra_modules_dir`` (if configured).

        Core modules are unconditional — every ``.py`` file in
        ``modules_dir`` loads regardless of any enabled-set. Extra modules
        only load if their stem is in ``enabled_modules.json`` as of this
        call; a disabled extra module's file is never opened, imported, or
        even glob-matched against a decision beyond a single set-membership
        check, so it costs nothing (see ``core/loader.py``'s module
        docstring).

        Args:
            client: The active ``TelegramClient`` for this account.
        """
        self._client = client

        # Unload existing plugins cleanly
        for stem in list(self._loaded.keys()):
            self._do_unload(stem)

        if self.extra_modules_dir is not None:
            self._load_enabled_extra()

        count = 0

        # Core modules — always loaded, unconditionally, exactly as before.
        #
        # v3.2.3: discovery now spans the module roots, and each root may
        # hold single-file modules AND package-style modules. The roots
        # are searched in precedence order, and the first root that
        # provides a given name wins — so a core module can never be
        # shadowed by an extra one of the same name.
        loaded_stems: set[str] = set()

        for root, is_extra in self._module_roots():
            for stem, entry in self._discover_modules(root):
                if is_extra:
                    # Extra modules are opt-in per account.
                    if stem not in self._enabled_extra:
                        continue
                    if stem in loaded_stems:
                        self._log.error(
                            "modules_extra/%s has the same name as a "
                            "core module — skipped.", stem,
                        )
                        continue
                if self._do_load(stem, entry):
                    count += 1
                    loaded_stems.add(stem)

        self._log.info(
            "%d plugin(s) loaded (%d extra enabled).",
            count, len(self._enabled_extra),
        )

    def reload_module(self, stem: str) -> bool:
        """
        Unload and reload a single plugin by its file stem, in whichever
        root (``modules_dir`` or ``extra_modules_dir``) it actually lives.

        A disabled extra module's stem resolves to a real file (so
        ``_resolve_path`` finds it) but is refused here — reloading it
        would mean importing a module nothing has asked to run, which is
        exactly what the enabled-set exists to prevent. Use
        ``enable_extra_module()`` to bring it up instead.

        Args:
            stem: The module file stem, e.g. ``"clearer"`` for ``clearer.py``.

        Returns:
            ``True`` on success, ``False`` if the file does not exist, is a
            disabled extra module, or the load fails.
        """
        resolved = self._resolve_path(stem)
        if resolved is None:
            # v3.2.2 (B-05): the file is gone (deleted on disk). The
            # module must be UNLOADED, not left loaded. The watchdog's
            # `on_deleted` handler calls this, and the old code only
            # logged "Module file not found" and returned False — so
            # deleting a module file left it loaded, its handlers still
            # registered on the client, its old code still running, and
            # it still listed in `help` / `.modules` / `.stats` until the
            # next full restart. The create/modify paths did reload
            # correctly, so this was an inconsistency between paths.
            if stem in self._loaded:
                self._do_unload(stem)
                self._log.info("Module file %s.py was removed — unloaded.", stem)
            else:
                self._log.error("Module file not found: %s.py", stem)
            return False
        path, is_extra = resolved
        if is_extra and stem not in self._enabled_extra:
            self._log.warning(
                "'%s' is a disabled extra module — not reloading "
                "(use enable_extra_module() to enable it).", stem,
            )
            return False
        self._do_unload(stem)
        return self._do_load(stem, path)

    # v3.2.2 (B-23): `reload_all()` was removed. It had ZERO call sites —
    # hot-reload is per-file (the watchdog calls `reload_module()` for the
    # single changed stem), and a full restart of every module was never
    # triggered from anywhere.

    def reattach(self, new_client: TelegramClient) -> None:
        """
        Re-register all loaded module handlers on a new client after reconnect.

        Called by the reconnector after a successful client rebuild. Tears down
        handlers on the old client (if any) and calls setup() on the new one.
        The module instances themselves are reused — no re-import needed.

        This fixes the critical bug where the bot becomes "deaf" after any
        network-triggered rebuild because handlers were attached to the old
        (now-disconnected) client instance.

        Args:
            new_client: The freshly connected Telegram Client to attach to.
        """
        old_client = self._client
        self._client = new_client

        reattached = 0
        failed_stems: list[str] = []

        # v3.0.12: iterate over a snapshot of the loaded items, not
        # self._loaded itself — a failed reattach below now removes the
        # stem from self._loaded mid-loop, which would otherwise raise
        # "dictionary changed size during iteration".
        for stem, (instance, _) in list(self._loaded.items()):
            # Remove handlers from old client (safe even if already disconnected)
            if old_client is not None:
                try:
                    instance.teardown(old_client)
                except Exception as exc:
                    self._log.debug(
                        "teardown error for %s during reattach: %s",
                        stem, exc,
                    )

            # Re-register on new client
            try:
                instance.setup(new_client)
                reattached += 1
            except Exception as exc:
                # v3.0.12: previously this only logged and incremented a
                # counter, leaving the module in self._loaded as a "loaded"
                # instance whose handlers were never actually registered on
                # the new client — a silent, permanent dead-handler bug that
                # would survive every future reconnect. Remove it from
                # self._loaded instead, so get_module(stem) correctly
                # returns None (diagnosable) rather than an instance that
                # looks alive but will never receive another event.
                self._log.error(
                    "setup() failed for %s during reattach — module "
                    "unloaded (was silently left half-registered before "
                    "v3.0.12): %s",
                    stem, exc,
                )
                self._loaded.pop(stem, None)
                failed_stems.append(stem)

        if failed_stems:
            self._log.warning(
                "Reattached handlers: %d OK, %d FAILED and unloaded: %s.",
                reattached, len(failed_stems), ", ".join(failed_stems),
            )
        else:
            self._log.info(
                "Reattached handlers: %d OK, 0 failed.",
                reattached,
            )

    # ── Introspection ─────────────────────────────────────────────────────

    def list_modules(self) -> list[str]:
        """Return a sorted list of currently loaded module stems."""
        return sorted(self._loaded.keys())

    def modules(self) -> list[Module]:
        """Return all currently loaded ``Module`` instances."""
        return [inst for inst, _ in self._loaded.values()]

    # v3.2.2 (B-23): `get_help_texts()` was removed. It had ZERO call sites —
    # help_handler.py builds its own listing straight from the module
    # instances (v3.0.13 moved category/desc onto the module itself and
    # made the loader's own helper unnecessary).

    # ── External file watches ─────────────────────────────────────────────

    def watch_files(
        self,
        entries: list[tuple[Path, Callable[[Path], None]]],
    ) -> None:
        """
        Register additional ``(path, callback)`` pairs to monitor.

        *callback(path)* is called on the event loop when the file or
        any direct child of a directory changes.  Must be called before
        ``watch()`` starts.

        Args:
            entries: List of ``(watched_path, callback_fn)`` tuples.
        """
        self._extra_watches.extend(entries)

    # ── File watcher (hot-reload + external files) ────────────────────────

    async def watch(self) -> None:
        """
        Watch ``modules_dir`` and ``extra_modules_dir`` (if configured) for
        ``.py`` changes (hot-reload), plus any extra paths registered via
        ``watch_files()`` — which, as of v3.1.0, always includes this
        account's own ``enabled_modules.json`` when extra modules are
        configured (registered in ``__init__``).

        A change to an *enabled* extra module hot-reloads exactly like a
        core module. A change to a *disabled* extra module's file is a
        no-op — there is nothing loaded to reload, and importing it just
        because the file changed would defeat the entire point of the
        enabled-set.

        Requires the ``watchdog`` package.  Logs a warning and exits if it is
        not installed.
        """
        try:
            from watchdog.events import FileSystemEventHandler  # type: ignore[import-untyped]
            from watchdog.observers import Observer  # type: ignore[import-untyped]
        except ImportError:
            self._log.warning(
                "watchdog not installed — hot-reload disabled."
            )
            return

        loop       = asyncio.get_running_loop()
        loader_ref = self

        # Build lookup tables for extra watches
        extra_file_map: dict[Path, Callable[[Path], None]] = {}
        extra_dir_map:  dict[Path, Callable[[Path], None]] = {}
        for watch_path, cb in self._extra_watches:
            abs_path = watch_path.resolve()
            if abs_path.is_dir():
                extra_dir_map[abs_path] = cb
            else:
                extra_file_map[abs_path] = cb

        class _Handler(FileSystemEventHandler):
            def __init__(self) -> None:
                self._last: dict[str, float] = {}

            def on_modified(self, event) -> None:   # type: ignore[override]
                if not event.is_directory:
                    self._trigger(Path(event.src_path))

            def on_created(self, event) -> None:    # type: ignore[override]
                self._trigger(Path(event.src_path))

            def on_deleted(self, event) -> None:    # type: ignore[override]
                self._trigger(Path(event.src_path))

            def on_moved(self, event) -> None:      # type: ignore[override]
                self._trigger(Path(event.dest_path))

            def _trigger(self, path: Path) -> None:
                now = time.monotonic()
                key = str(path)
                # Debounce — ignore events within 1.5 s of the last one
                if now - self._last.get(key, 0.0) < _DEBOUNCE_WINDOW:
                    return
                self._last[key] = now
                # v3.2.2 (B-28): the debounce map used to grow without
                # bound — one entry per distinct path ever touched, kept
                # for the lifetime of the process. On a long-running
                # Termux deployment that is a slow leak. Entries older
                # than the debounce window can never suppress anything
                # again, so they are simply forgotten.
                if len(self._last) > _DEBOUNCE_MAX_ENTRIES:
                    cutoff = now - _DEBOUNCE_WINDOW
                    self._last = {
                        k: v for k, v in self._last.items() if v > cutoff
                    }

                if path.suffix == ".py":
                    # v3.2.3: work out which MODULE this file belongs to.
                    #
                    # A changed file may be:
                    #   - a single-file module      -> the module IS the file
                    #   - a package's entry point    -> the module IS the folder
                    #   - a file INSIDE a package   -> the module is the
                    #     folder that contains it
                    #
                    # In every case the module is reloaded as a whole,
                    # because a module's state (settings cache, pending
                    # actions, …) lives on the instance, not on the file.
                    target = loader_ref._module_for_path(path)
                    if target is not None:
                        stem, root, is_extra = target

                        # modules_extra/ (v3.1.0) — hot-reload ONLY if this
                        # stem is currently enabled. A disabled module's
                        # file changing is deliberately a no-op: nothing is
                        # loaded to reload, and importing it solely because
                        # it was edited would reintroduce a per-event-adjacent
                        # cost the enabled-set exists specifically to avoid.
                        if is_extra and stem not in loader_ref._enabled_extra:
                            return

                        asyncio.run_coroutine_threadsafe(
                            _hot_reload(loader_ref, stem), loop
                        )
                        return

                # Extra file watch
                abs_path = path.resolve()
                cb = extra_file_map.get(abs_path)
                if cb:
                    asyncio.run_coroutine_threadsafe(
                        _run_callback(cb, path, loader_ref._log), loop
                    )
                    return

                # Extra directory watch (match by parent)
                cb = extra_dir_map.get(abs_path.parent)
                if cb:
                    asyncio.run_coroutine_threadsafe(
                        _run_callback(cb, path, loader_ref._log), loop
                    )

        # Collect all directories to schedule on the observer
        #
        # v3.2.3: every module root is watched, and so is the folder of
        # every package-style module — a file inside a package belongs to
        # that package's module, and changing it must hot-reload the
        # module. The observer stays non-recursive; the package folders
        # are scheduled explicitly, which keeps the event volume bounded
        # (no events from unrelated sub-directories).
        watch_dirs: set[str] = set()
        for root, _is_extra in self._module_roots():
            if not root.exists():
                continue
            watch_dirs.add(str(root))
            for pkg in sorted(root.glob("*/")):
                if not pkg.is_dir():
                    continue
                if pkg.name.startswith("_") or pkg.name in _INFRASTRUCTURE_STEMS:
                    continue
                if (pkg / "__init__.py").exists():
                    watch_dirs.add(str(pkg))

        if self.extra_modules_dir is not None and not self.extra_modules_dir.exists():
            # Observer.schedule() raises FileNotFoundError on a missing
            # directory — degrade to "no extra-module hot-reload" rather
            # than taking the whole watcher down with it.
            self._log.warning(
                "modules_extra/ does not exist on disk — extra "
                "modules will still load if enabled, but hot-reload for "
                "them is unavailable until the directory exists."
            )
        for watch_path, _ in self._extra_watches:
            abs_path = watch_path.resolve()
            watch_dirs.add(str(abs_path if abs_path.is_dir() else abs_path.parent))

        observer = Observer()
        handler  = _Handler()
        for d in watch_dirs:
            observer.schedule(handler, d, recursive=False)

        base_dirs = {str(root) for root, _ in self._module_roots()}

        observer.start()
        self._log.info(
            "File watcher active — modules_dir=%s, modules_extra=%s, extra_dirs=%d.",
            self.modules_dir.name,
            self.extra_modules_dir.name if self.extra_modules_dir else "(none)",
            len(watch_dirs - base_dirs),
        )

        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            observer.stop()
            observer.join()


# ── Async helpers ─────────────────────────────────────────────────────────

async def _hot_reload(loader: AccountLoader, stem: str) -> None:
    """Reload a single plugin and log the outcome."""
    ok = loader.reload_module(stem)
    loader._log.info(
        "Hot-reload %s: %s.",
        stem, "OK" if ok else "FAILED",
    )


async def _run_callback(
    cb: Callable[[Path], None],
    path: Path,
    log: logging.Logger,
) -> None:
    """Invoke a file-watch callback, awaiting it if it is a coroutine."""
    try:
        result = cb(path)
        if asyncio.iscoroutine(result):
            await result
    except Exception as exc:
        log.error("watch_files callback error for %s: %s", path, exc)
