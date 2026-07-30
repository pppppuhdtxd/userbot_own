"""
userbot_own/app
════════════════════════════════════════════════════════════════
Composition root and application lifecycle.

composition_root.py — builds the full object graph (config, registries,
                       event bus, per-account clients/loaders/reconnectors)
                       and wires it together. The ONE place allowed to
                       construct the application-scoped registries.
application.py      — Application class: owns start/stop orchestration
                       (migrated from the original main.py). Handles
                       SIGINT (Ctrl+C) and SIGTERM gracefully — no custom
                       restart logic.
════════════════════════════════════════════════════════════════
"""
