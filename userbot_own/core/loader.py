"""
userbot_own/core/loader.py
════════════════════════════════════════════════════════════════
Per-account plugin loader with hot-reload support.

Each account has its own independent `AccountLoader` instance:

• Modules are instantiated separately per account.
• Hot-reload of one account never affects the others.
• Each module has access to its own `ModuleContext` (cfg + injected
  application-scoped services).

`watch()` monitors `modules_dir` for `.py` file changes and triggers
hot-reload automatically (requires the `watchdog` package).

`watch_files()` accepts additional `(path, callback)` pairs so external
files like `account.json` can also drive callbacks.

v3.1.0: optional second root, `extra_modules_dir` (`modules_extra/`).
Files there behave exactly like core `modules/` files (same `Module`
contract, same hot-reload) with one difference: a file is only ever
imported — and therefore only ever appears in `self._loaded`, `help`,
`.modules`, etc. — if its stem is in this account's own
`enabled_modules.json` (`data/settings/account{N}/`). Disabled extra
modules are never imported, so there is no per-event "is this
enabled?" check anywhere on the hot path; the gate is entirely a
load-time decision. Both roots feed the *same* `self._loaded` dict, so
every existing consumer of `list_modules()` / `get_module()` /
`get_help_texts()` (help_handler.py, system.py's `.modules`/`.stats`)
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
        self.label:       str           = f"Account{context.cfg.index}"
        self._client:     TelegramClient | None = None

        # stem → (Module instance, Python module object). Populated from
        # BOTH modules_dir and extra_modules_dir — deliberately one dict,
        # not two, so every existing reader (list_modules(), get_module(),
        # get_help_texts()) keeps working unchanged regardless of which
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

    def _import(self, path: Path) -> tuple[Module, ModuleType] | None:
        """
        Import a plugin file and instantiate its ``Module``.

        Returns ``(instance, py_module)`` on success, ``None`` on failure.
        Errors are logged but never re-raised so a broken plugin does not
        bring down the whole account.
        """
        qual = f"_modules_a{self.cfg.index}.{path.stem}"
        sys.modules.pop(qual, None)

        try:
            spec = importlib.util.spec_from_file_location(qual, path)
            if spec is None or spec.loader is None:
                raise ModuleImportError(path.stem, ValueError("Cannot create module spec"))

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
                    "[%s] %s has no valid Module — skipped.", self.label, path.name
                )
                sys.modules.pop(qual, None)
                return None

            return instance, py_mod

        except ModuleImportError:
            raise
        except Exception as exc:
            self._log.exception("[%s] Import failed for %s: %s", self.label, path.name, exc)
            sys.modules.pop(qual, None)
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
                "[%s] setup() failed for %s: %s", self.label, stem, exc
            )
            sys.modules.pop(f"_modules_a{self.cfg.index}.{stem}", None)
            return False

        self._loaded[stem] = (instance, py_mod)

        self._log.info("[%s] Loaded: %s", self.label, stem)
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
                "[%s] teardown() error for %s: %s", self.label, stem, exc
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
                "[%s] enabled_modules.json unreadable — treating as no "
                "extra modules enabled: %s", self.label, err,
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
                "[%s] Failed to save enabled_modules.json: %s", self.label, err
            )

    def _resolve_path(self, stem: str) -> tuple[Path, bool] | None:
        """
        Find *stem*'s ``.py`` file, checking ``modules_dir`` first, then
        ``extra_modules_dir``.

        Returns ``(path, is_extra)``, or ``None`` if not found in either
        root. Core modules always take priority — see
        ``enable_extra_module()`` for the corresponding same-name-collision
        guard on the extra-module side.
        """
        core_path = self.modules_dir / f"{stem}.py"
        if core_path.exists():
            return core_path, False
        if self.extra_modules_dir is not None:
            extra_path = self.extra_modules_dir / f"{stem}.py"
            if extra_path.exists():
                return extra_path, True
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
                "[%s] enabled_modules.json unreadable after external edit "
                "— ignoring this change: %s", self.label, err,
            )
            return

        new_enabled = (
            {s for s in data.get("enabled", []) if isinstance(s, str)}
            if data is not None else set()
        )

        to_enable = sorted(new_enabled - self._enabled_extra)
        to_disable = sorted(self._enabled_extra - new_enabled)

        for stem in to_enable:
            candidate = self.extra_modules_dir / f"{stem}.py"
            if not candidate.exists():
                self._log.warning(
                    "[%s] enabled_modules.json now lists '%s', but no such "
                    "file exists in modules_extra/ — skipped.",
                    self.label, stem,
                )
                continue
            if stem in self._loaded:
                self._log.warning(
                    "[%s] enabled_modules.json now lists '%s', but that "
                    "name is already loaded (core-module collision?) — "
                    "skipped.", self.label, stem,
                )
                continue
            if self._do_load(stem, candidate):
                self._enabled_extra.add(stem)

        for stem in to_disable:
            self._do_unload(stem)
            self._enabled_extra.discard(stem)

        if to_enable or to_disable:
            self._log.info(
                "[%s] enabled_modules.json changed externally: +%d enabled, "
                "-%d disabled.", self.label, len(to_enable), len(to_disable),
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

    def unload_module(self, stem: str) -> bool:
        """
        Unload a single plugin by its file stem without reloading it.

        Calls ``teardown()`` on the module instance and removes it from the
        loaded set, but does NOT re-import or re-call ``setup()`` — unlike
        ``reload_module()``, which unloads and immediately reloads. Use this
        when a module should simply stop running (e.g. as part of removing
        an account entirely), not when it should be refreshed from disk.

        Args:
            stem: The module file stem to unload.

        Returns:
            ``True`` if the module was loaded and is now unloaded,
            ``False`` if it was not loaded to begin with.
        """
        if stem not in self._loaded:
            return False
        self._do_unload(stem)
        return True

    # ── Extra-module control (v3.1.0) ───────────────────────────────────────
    #
    # The only intended way for module_manager.py (or anything else) to
    # change or inspect extra-module enablement — mirrors get_module()'s
    # role as "the public replacement for reaching into _loaded directly".

    def list_available_extra(self) -> list[str]:
        """
        Return every ``.py`` stem present in ``extra_modules_dir``,
        regardless of enabled state — i.e. "available", not "active".

        Returns ``[]`` if this loader wasn't configured with an
        ``extra_modules_dir`` (never raises).
        """
        if self.extra_modules_dir is None:
            return []
        return sorted(
            p.stem for p in self.extra_modules_dir.glob("*.py")
            if not p.stem.startswith("_") and p.stem not in _INFRASTRUCTURE_STEMS
        )

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
        path = self.extra_modules_dir / f"{stem}.py"
        if not path.exists():
            return False, f"No such extra module: '{stem}'."
        if stem in self._loaded:
            return False, (
                f"'{stem}' conflicts with an already-loaded module of the "
                f"same name — refusing to shadow it."
            )
        if not self._do_load(stem, path):
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

    def unload_all(self) -> None:
        """
        Unload every currently loaded plugin.

        Calls ``teardown()`` on each module instance and clears the loaded
        set entirely. Intended for full cleanup before shutdown or before
        removing an account, where every module's handlers must be torn
        down and nothing should be reloaded afterward.
        """
        for stem in list(self._loaded.keys()):
            self._do_unload(stem)
        self._log.info("[%s] All plugins unloaded.", self.label)

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
        for path in sorted(self.modules_dir.glob("*.py")):
            if path.stem.startswith("_") or path.stem in _INFRASTRUCTURE_STEMS:
                continue
            if self._do_load(path.stem, path):
                count += 1

        # Extra modules — only the ones enabled for this account.
        if self.extra_modules_dir is not None:
            for path in sorted(self.extra_modules_dir.glob("*.py")):
                if path.stem.startswith("_") or path.stem in _INFRASTRUCTURE_STEMS:
                    continue
                if path.stem not in self._enabled_extra:
                    continue
                if path.stem in self._loaded:
                    # Same stem already loaded from modules_dir — core wins,
                    # an extra module can never shadow a core one.
                    self._log.error(
                        "[%s] modules_extra/%s.py has the same name as a "
                        "core module — skipped.", self.label, path.stem,
                    )
                    continue
                if self._do_load(path.stem, path):
                    count += 1

        self._log.info(
            "[%s] %d plugin(s) loaded (%d extra enabled).",
            self.label, count, len(self._enabled_extra),
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
            self._log.error("[%s] Module file not found: %s.py", self.label, stem)
            return False
        path, is_extra = resolved
        if is_extra and stem not in self._enabled_extra:
            self._log.warning(
                "[%s] '%s' is a disabled extra module — not reloading "
                "(use enable_extra_module() to enable it).", self.label, stem,
            )
            return False
        self._do_unload(stem)
        return self._do_load(stem, path)

    def reload_all(self) -> dict[str, bool]:
        """
        Reload all plugins — both currently loaded and any new ``.py`` files
        that appeared on disk since the last load (core modules
        unconditionally; extra modules only if already enabled).

        Returns:
            A dict mapping each stem to ``True`` (loaded) / ``False`` (failed).
        """
        existing = set(self._loaded.keys())
        on_disk = {
            p.stem
            for p in self.modules_dir.glob("*.py")
            if not p.stem.startswith("_") and p.stem not in _INFRASTRUCTURE_STEMS
        }
        if self.extra_modules_dir is not None:
            on_disk |= {
                p.stem
                for p in self.extra_modules_dir.glob("*.py")
                if not p.stem.startswith("_")
                and p.stem not in _INFRASTRUCTURE_STEMS
                and p.stem in self._enabled_extra
            }
        return {stem: self.reload_module(stem) for stem in sorted(existing | on_disk)}

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
                        "[%s] teardown error for %s during reattach: %s",
                        self.label, stem, exc,
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
                    "[%s] setup() failed for %s during reattach — module "
                    "unloaded (was silently left half-registered before "
                    "v3.0.12): %s",
                    self.label, stem, exc,
                )
                self._loaded.pop(stem, None)
                failed_stems.append(stem)

        if failed_stems:
            self._log.warning(
                "[%s] Reattached handlers: %d OK, %d FAILED and unloaded: %s.",
                self.label, reattached, len(failed_stems), ", ".join(failed_stems),
            )
        else:
            self._log.info(
                "[%s] Reattached handlers: %d OK, 0 failed.",
                self.label, reattached,
            )

    # ── Introspection ─────────────────────────────────────────────────────

    def list_modules(self) -> list[str]:
        """Return a sorted list of currently loaded module stems."""
        return sorted(self._loaded.keys())

    def modules(self) -> list[Module]:
        """Return all currently loaded ``Module`` instances."""
        return [inst for inst, _ in self._loaded.values()]

    def get_help_texts(self) -> list[str]:
        """Return all non-empty help texts from loaded modules."""
        return [inst.help_text for inst, _ in self._loaded.values() if inst.help_text]

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
                "[%s] watchdog not installed — hot-reload disabled.", self.label
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
                if now - self._last.get(key, 0.0) < 1.5:
                    return
                self._last[key] = now

                if (
                    path.suffix == ".py"
                    and not path.stem.startswith("_")
                    and path.stem not in _INFRASTRUCTURE_STEMS
                ):
                    parent = path.parent.resolve()

                    # Core modules/ — unconditional hot-reload, unchanged.
                    if parent == loader_ref.modules_dir.resolve():
                        asyncio.run_coroutine_threadsafe(
                            _hot_reload(loader_ref, path.stem), loop
                        )
                        return

                    # modules_extra/ (v3.1.0) — hot-reload ONLY if this
                    # stem is currently enabled. A disabled module's file
                    # changing is deliberately a no-op: nothing is loaded
                    # to reload, and importing it solely because it was
                    # edited would reintroduce a per-event-adjacent cost
                    # the enabled-set exists specifically to avoid.
                    if (
                        loader_ref.extra_modules_dir is not None
                        and parent == loader_ref.extra_modules_dir.resolve()
                    ):
                        if path.stem in loader_ref._enabled_extra:
                            asyncio.run_coroutine_threadsafe(
                                _hot_reload(loader_ref, path.stem), loop
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
        watch_dirs: set[str] = {str(self.modules_dir)}
        if self.extra_modules_dir is not None:
            if self.extra_modules_dir.exists():
                watch_dirs.add(str(self.extra_modules_dir))
            else:
                # Observer.schedule() raises FileNotFoundError on a missing
                # directory — degrade to "no extra-module hot-reload" rather
                # than taking the whole watcher down with it.
                self._log.warning(
                    "[%s] modules_extra/ does not exist on disk — extra "
                    "modules will still load if enabled, but hot-reload for "
                    "them is unavailable until the directory exists.",
                    self.label,
                )
        for watch_path, _ in self._extra_watches:
            abs_path = watch_path.resolve()
            watch_dirs.add(str(abs_path if abs_path.is_dir() else abs_path.parent))

        observer = Observer()
        handler  = _Handler()
        for d in watch_dirs:
            observer.schedule(handler, d, recursive=False)

        base_dirs = {str(self.modules_dir)}
        if self.extra_modules_dir is not None and str(self.extra_modules_dir) in watch_dirs:
            base_dirs.add(str(self.extra_modules_dir))

        observer.start()
        self._log.info(
            "[%s] File watcher active — modules_dir=%s, modules_extra=%s, extra_dirs=%d.",
            self.label,
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
        "[%s] Hot-reload %s: %s.",
        loader.label, stem, "OK" if ok else "FAILED",
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
