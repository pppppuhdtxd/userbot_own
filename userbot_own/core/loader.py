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

Integration with the plugin registry
─────────────────────────────────────
After every successful load or reload the loader calls
`context.plugin_store.upsert()` to keep rich metadata up to date.

DI note: `create_module()` factories now always receive exactly one
argument — the account's `ModuleContext`. The original loader supported
two different factory signatures (`create_module(cfg)` or
`create_module(cfg, loader)`, detected via `inspect.signature`) because
most modules reached for a module-level `loader_registry` global instead
of using the two-argument form. Now that every module receives the same
`ModuleContext` (which already carries the application-scoped
loader_registry, plugin_store, and account_registry), that branching is
gone — there is exactly one supported factory shape.
════════════════════════════════════════════════════════════════
"""
import asyncio
import importlib.util
import logging
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import ModuleType

from telethon import TelegramClient

from userbot_own.core.context import ModuleContext
from userbot_own.core.exceptions import ModuleImportError
from userbot_own.core.logging_setup import get_logger
from userbot_own.core.registry import PluginMetadata
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
        context:     This account's ModuleContext (cfg + injected services),
                     passed unchanged to every plugin's `create_module()`.
        modules_dir: Directory containing ``.py`` plugin files.
    """

    def __init__(self, context: ModuleContext, modules_dir: Path) -> None:
        self.context:     ModuleContext = context
        self.cfg                        = context.cfg
        self.modules_dir: Path          = modules_dir
        self.label:       str           = f"Account{context.cfg.index}"
        self._client:     TelegramClient | None = None

        # stem → (Module instance, Python module object)
        self._loaded: dict[str, tuple[Module, ModuleType]] = {}
        self._log:    logging.Logger = get_logger(f"loader.account{context.cfg.index}")

        # Extra (path, callback) pairs registered via watch_files()
        self._extra_watches: list[tuple[Path, Callable[[Path], None]]] = []

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

        # Register metadata in the application-scoped plugin store
        self.context.plugin_store.upsert(
            self.cfg.index,
            stem,
            PluginMetadata(
                account_index = self.cfg.index,
                stem          = stem,
                name          = instance.name or stem,
                help_text     = instance.help_text,
                file_path     = str(path),
                loaded_at     = datetime.now(),
            ),
        )

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
        self.context.plugin_store.remove(self.cfg.index, stem)

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
        Bind *client*, unload all current plugins, and (re)load every
        ``.py`` file in ``modules_dir``.

        Args:
            client: The active ``TelegramClient`` for this account.
        """
        self._client = client

        # Unload existing plugins cleanly
        for stem in list(self._loaded.keys()):
            self._do_unload(stem)

        count = 0
        for path in sorted(self.modules_dir.glob("*.py")):
            if path.stem.startswith("_") or path.stem in _INFRASTRUCTURE_STEMS:
                continue
            if self._do_load(path.stem, path):
                count += 1

        self._log.info("[%s] %d plugin(s) loaded.", self.label, count)

    def reload_module(self, stem: str) -> bool:
        """
        Unload and reload a single plugin by its file stem.

        Args:
            stem: The module file stem, e.g. ``"clearer"`` for ``clearer.py``.

        Returns:
            ``True`` on success, ``False`` if the file does not exist or the
            load fails.
        """
        path = self.modules_dir / f"{stem}.py"
        if not path.exists():
            self._log.error("[%s] Module file not found: %s.py", self.label, stem)
            return False
        self._do_unload(stem)
        return self._do_load(stem, path)

    def reload_all(self) -> dict[str, bool]:
        """
        Reload all plugins — both currently loaded and any new ``.py`` files
        that appeared on disk since the last load.

        Returns:
            A dict mapping each stem to ``True`` (loaded) / ``False`` (failed).
        """
        existing = set(self._loaded.keys())
        on_disk = {
            p.stem
            for p in self.modules_dir.glob("*.py")
            if not p.stem.startswith("_") and p.stem not in _INFRASTRUCTURE_STEMS
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
        failed = 0

        for stem, (instance, _) in self._loaded.items():
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
                self._log.error(
                    "[%s] setup() failed for %s during reattach: %s",
                    self.label, stem, exc,
                )
                failed += 1

        self._log.info(
            "[%s] Reattached handlers: %d OK, %d failed.",
            self.label, reattached, failed,
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
        Watch ``modules_dir`` for ``.py`` changes (hot-reload) and any extra
        paths registered via ``watch_files()``.

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

                # Hot-reload for .py files inside modules_dir
                if (
                    path.suffix == ".py"
                    and path.parent.resolve() == loader_ref.modules_dir.resolve()
                    and not path.stem.startswith("_")
                    and path.stem not in _INFRASTRUCTURE_STEMS
                ):
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
        for watch_path, _ in self._extra_watches:
            abs_path = watch_path.resolve()
            watch_dirs.add(str(abs_path if abs_path.is_dir() else abs_path.parent))

        observer = Observer()
        handler  = _Handler()
        for d in watch_dirs:
            observer.schedule(handler, d, recursive=False)

        observer.start()
        self._log.info(
            "[%s] File watcher active — modules_dir=%s, extra_dirs=%d.",
            self.label,
            self.modules_dir.name,
            len(watch_dirs) - 1,
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
