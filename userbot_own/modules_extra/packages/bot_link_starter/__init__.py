"""
userbot_own/modules_extra/packages/bot_link_starter
════════════════════════════════════════════════════════════════
Start the Telegram bot links found in a Quote & Reply, one after another.

What it does
------------
Quote a message that contains one or more bot links, then send `start`.
This module finds the links in the QUOTED part of that message only —
never in the rest of it — and sends each bot its `/start` command, in
the order the links appear, so the files or media the links offer are
delivered.

The one exception: a quote that carries no text of its own (replying
with the quote gesture over a whole message) has nothing to read, so
the full message being replied to is used instead. A quote that does
carry text is never widened.

Both kinds of link are recognised:

- a plain link — ``https://t.me/MyBot?start=ref123``
- a link behind text — text the user attached with "create link",
  which is what you get when you click it and land in the bot

Layout
------
``links.py``     finding the links in the quoted part, and turning them
                 into something startable
``handlers.py``  the BotLinkStarter class — the command and the start loop
``info.py``      the help texts and metadata (data only, no imports)

The module contract is the same as every other module: a ``Module``
subclass plus a module-level ``create_module(context)`` factory. The
loader discovers the package through this ``__init__.py``.

The metadata lives in ``info.py`` (data only, no imports), and is
copied onto the class below — because ``help_handler.py`` and
``core/loader.py`` read it off the *instance*.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from userbot_own.core.context import ModuleContext
from userbot_own.modules._infra.base import Module
from userbot_own.modules_extra.packages.bot_link_starter.handlers import (
    BotLinkStarter,
)
from userbot_own.modules_extra.packages.bot_link_starter.info import (
    category,
    desc,
    help_extra,
    help_text,
    name,
)

# Copy the metadata onto the class — `help_handler.py` and
# `core/loader.py` both read these off the instance.
BotLinkStarter.name = name
BotLinkStarter.category = category
BotLinkStarter.desc = desc
BotLinkStarter.help_text = help_text
BotLinkStarter.help_extra = help_extra


def create_module(context: ModuleContext) -> Module:
    """Factory the loader calls to build this module for one account."""
    return BotLinkStarter(context)


__all__ = [
    "BotLinkStarter",
    "create_module",
    # Metadata
    "name",
    "category",
    "desc",
    "help_text",
    "help_extra",
]
