"""
userbot_own/modules_extra/example_module
════════════════════════════════════════════════════════════════
ExampleModule — template / smoke-test module for `modules_extra/`.

Ships DISABLED. A module in `modules_extra/` only runs for an account
that has explicitly enabled it, so this one is never imported — and
cannot affect anything — until you run `.extra enable example_module`
in Saved Messages.

What it does, deliberately almost nothing:
• `پینگ اضافی` (typed in Saved Messages) → the message becomes `🏓 pong`,
  which is then auto-deleted after a few seconds.

That is enough to verify the whole extras system end to end on a real
account: `.extra` lists it as disabled → `.extra enable example_module`
loads it live → the command answers → `help` shows it →
`.extra disable example_module` tears it down and the command goes quiet
again. The README's "Extra Modules" section walks through exactly this.

Use it as a template: copy the folder, rename the class and `name`,
replace the handler. Everything a module needs is here — the `Module`
subclass attributes (`name`, `category`, `desc`, `help_text`,
`help_extra`), `setup()` registering handlers through
`self._add_handler()` (which `Module.teardown()` removes for you), and
the module-level `create_module(context)` factory the loader looks for.
Delete this folder once you no longer need it.

v3.1.10: restored. v3.1.0 introduced this file and documented it (README,
the in-app `.extra` help) as shipping with the project, but it was missing
from the v3.1.9 package; nothing in the CHANGELOG records a removal.

v3.2.3: converted from a single file to a package, matching the new
module layout. A single-file extra module still works exactly the same
— this folder simply shows the packaged form.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from userbot_own.core.context import ModuleContext
from userbot_own.modules.base import Module
from userbot_own.modules_extra.example_module.handlers import ExampleModule
from userbot_own.modules_extra.example_module.info import (
    category,
    desc,
    help_extra,
    help_text,
    name,
)

# Copy the metadata onto the class — help_handler.py and core/loader.py
# both read these off the instance.
ExampleModule.name = name
ExampleModule.category = category
ExampleModule.desc = desc
ExampleModule.help_text = help_text
ExampleModule.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    """Factory the loader calls to build this module for one account."""
    return ExampleModule(context)


__all__ = ["ExampleModule", "create_module"]
