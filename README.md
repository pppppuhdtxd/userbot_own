## One-Click Installation (Termux / Linux)

The fastest way to get running — no manual `venv` setup, no remembering
paths. Works on **Android (Termux)** and **Debian/Ubuntu-based Linux**.

```bash
curl -fsSL https://raw.githubusercontent.com/pppppuhdtxd/userbot_own/main/install.sh | bash
```

This single command will:

1. Detect whether you're on Termux or a standard Linux distro.
2. Install Python (and, on Termux, the native build tools some dependencies
   need to compile) if it isn't already present.
3. Clone this repository to `~/userbot_own`.
4. Create an isolated virtual environment and install everything in
   `requirements.txt` into it.
5. Register a global `userbot` command so you can launch things from
   anywhere in your terminal — no `cd`-ing into the project folder required.

Once installed, just run:

```bash
userbot
```

You'll get an interactive menu:

```
=== userbot_own ===
1) Run bot
2) Manage accounts
3) Update
4) Quit
Select an option:
```

- **Run bot** — starts `main.py`.
- **Manage accounts** — runs `add_account.py` to add/configure a Telegram account.
- **Update** — pulls the latest code and refreshes dependencies. This is
  always safe to run: it **never touches your `accounts/`, `.env`, or
  `data/` folders** — only the codebase and installed packages.
- **Quit** — exits without doing anything.

You can also skip the menu with direct shortcuts:

```bash
userbot run      # start the bot directly
userbot add      # jump straight to account management
userbot update   # update directly
```

> Re-running the install command above at any time is safe — it's
> idempotent and will simply refresh your existing installation rather
> than creating a duplicate or overwriting your account data.

If you'd rather install manually (or you're on a distro other than
Termux/Debian/Ubuntu), see the [Quick Start](#quick-start) section below.

---
# Multi-Account Telegram Userbot

A professional, async, hot-reload-capable Telegram account management system
built with Python 3.11+ and [Telethon](https://docs.telethon.dev/).

**Current version:** `3.0.7`

See [CHANGELOG.md](CHANGELOG.md) for the full history.

> **About this revision.** This README was rewritten as part of an internal
> architecture refactor. The previous copy described a `1.6.0`-era version of
> the bot — MTProxy support, an admin-permission system (`is_admin`,
> `is_admin_only`), `.reload`/`.restart`/`.accounts`/`.addaccount`/
> `.removeaccount`/`.cancelflow`/`.proxy`/`.version` commands, and "smart
> polling" for reaction detection — none of which exist in the codebase this
> refactor is based on (confirmed by reading every module directly, not by
> assumption). Every section below has been checked against the actual code.

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Project Structure](#project-structure)
- [Architecture Overview](#architecture-overview)
- [Message Classification System](#message-classification-system)
- [Account Setup](#account-setup)
- [Configuration](#configuration)
- [Available Commands](#available-commands)
- [Hot-Reload System](#hot-reload-system)
- [Writing a New Module](#writing-a-new-module)
- [Versioning Guide](#versioning-guide)
- [FAQ](#faq)

---

## Features

| Feature | Description |
| --- | --- |
| **Multi-account** | Run any number of Telegram accounts simultaneously, all equal (no admin/owner tiering) |
| **Hot-reload** | Add, remove, or edit module files while the bot is running — no restart |
| **Direct connection** | No proxy layer to configure or fail; for restricted networks, use a system-level VPN (WireGuard, OpenVPN, V2Ray) |
| **Message classification** | Unified priority system (`file > vid > pic > link > txt > other`) across all modules |
| **Category-based help** | RTL-friendly help output with 7 logical categories, plus per-module detail via `help <module>` |
| **Reaction commands** | Execute commands by reacting to messages with emojis — push-based (zero polling), works on bots, users, groups, and channels per-type toggle |
| **Plugin registry** | Metadata store tracking every loaded module (currently populated but not yet surfaced by any command — see [FAQ](#faq)) |
| **Live config reload** | `account.json` edits and new `accounts/N/` folders are picked up by a file watcher without a restart |
| **Semantic versioning** | Every change tracked in `VERSION` + `CHANGELOG.md` |
| **Python 3.11+** | Native union types, `match` statements, `slots=True` dataclasses |
| **Dependency injection** | Composition root wires config, event bus, and registries into every module's constructor — no global state reached for by import |

---

## Requirements

- Python 3.11 or newer
- A Telegram API app from [my.telegram.org/apps](https://my.telegram.org/apps)

---

## Quick Start

```bash
# 1. Clone / extract the project
cd userbot_own

# 2. Create a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Add your first account
python add_account.py

# 5. (Optional) Configure environment variables
cp userbot_own/.env.example userbot_own/.env
# Edit userbot_own/.env with your settings

# 6. Run
python main.py
```

Both commands run from the **repository root** — there's no need to `cd
userbot_own/` first (earlier versions required this because of how imports
resolved; this version uses proper package imports throughout).

---

## Project Structure

```
userbot_own/
├── VERSION                        ← single source of truth for version
├── CHANGELOG.md                   ← all changes since the beginning
├── README.md                      ← this file
├── requirements.txt
├── pyproject.toml                 ← ruff configuration + project metadata
├── main.py                        ← thin launcher → userbot_own.app.application.main()
├── add_account.py                 ← thin launcher → userbot_own.account_management.cli.main()
│
└── userbot_own/
    ├── __init__.py                ← exposes __version__ (reads VERSION)
    ├── .env.example                ← copy to .env to override settings below
    │
    ├── app/                        ← composition root & application lifecycle
    │   ├── composition_root.py     ← builds the object graph; owns per-account startup
    │   ├── application.py          ← run-loop orchestration + restart-spawn entry point
    │   └── restart.py              ← Ctrl+R / SIGUSR1 restart mechanics
    │
    ├── config/                     ← config models separated from config loading
    │   ├── models.py                ← AccountConfig, Paths, Settings (pure data)
    │   ├── loader.py                ← discover_accounts(), load_settings() (I/O)
    │   └── env.py                   ← typed env-var helpers
    │
    ├── core/
    │   ├── exceptions.py            ← structured exception hierarchy
    │   ├── events.py                 ← application-scoped EventBus
    │   ├── context.py                ← ModuleContext (constructor-injection payload)
    │   ├── registry.py               ← AccountRegistry, AccountLoaderRegistry, PluginMetadataStore
    │   ├── telegram_client.py        ← TelegramClient factory (direct connection)
    │   ├── loader.py                 ← per-account plugin loader + hot-reload
    │   ├── logging_setup.py          ← centralized structured logging
    │   ├── reconnector.py            ← per-account reconnect loop
    │   └── watcher.py                ← file-change callbacks (account.json, new accounts)
    │
    ├── helpers/
    │   └── utils.py                  ← shared utilities + classify_message()
    │
    ├── modules/
    │   ├── base.py                   ← abstract Module base class
    │   ├── router.py                 ← CommandRouter (declarative command dispatch)
    │   ├── bridge.py                 ← MockEvent (bridges reaction triggers into normal handlers)
    │   ├── system.py                 ← owner commands: .modules .account .stats .ping
    │   ├── help_handler.py           ← `help` + `help <module>` commands
    │   ├── clearer.py                 ← manual message clearing
    │   ├── auto_clearer.py            ← automatic message clearing
    │   ├── auto_forwarder.py          ← auto-forward bot messages
    │   ├── join_left.py               ← join/leave chats + folder management
    │   ├── reaction_commands.py       ← execute commands via emoji reactions
    │   ├── info_handler.py            ← message info (reply)
    │   └── whois_handler.py           ← user/chat info
    │
    ├── account_management/
    │   └── cli.py                    ← interactive `python add_account.py` menu
    │
    ├── accounts/
    │   ├── 1/
    │   │   ├── account.json          ← credentials (api_id, api_hash, phone)
    │   │   └── session.session       ← Telethon session (auto-created)
    │   └── 2/ …
    │
    └── data/
        ├── logs/                     ← rotating log files
        └── settings/                 ← per-account runtime settings
            ├── account1/
            │   ├── join_left.json
            │   ├── reactions.json
            │   ├── autoclear.json
            │   └── autoforward.json
            └── account2/ …
```

---

## Architecture Overview

### Composition root

`userbot_own/app/composition_root.py` is the only place that constructs the
application-scoped singletons: the `EventBus`, `AccountLoaderRegistry`,
`PluginMetadataStore`, and `AccountRegistry`. Everything else — every plugin
module, the loader, the reconnector — receives what it needs through its
constructor. Nothing reaches for a global import to get at shared state.

### Plugin lifecycle

```
AccountLoader.load_all(client)
    └─ for each .py in modules/ (except base.py / router.py / bridge.py)
        ├─ importlib.util.spec_from_file_location()
        ├─ create_module(context)      ← factory call, context = this account's ModuleContext
        ├─ instance.setup(client)      ← handler registration
        └─ plugin_store.upsert(metadata)
```

`ModuleContext` bundles everything a plugin might need: its own
`AccountConfig`, the shared `EventBus`, `AccountLoaderRegistry`,
`PluginMetadataStore`, `AccountRegistry`, and global `Settings`. Every module
receives the same shape, whether or not it uses every field — see
[Writing a New Module](#writing-a-new-module).

### Hot-reload (watchdog triggers)

```
AccountLoader.reload_module(stem)
    ├─ instance.teardown(client)   ← remove handlers
    └─ [re-import + re-setup]
```

### Connection resilience

Each account runs an independent `AccountReconnector` loop:

```
AccountReconnector._reconnect_cycle()   (every ~30s when healthy)
    ├─ lightweight API call to verify the connection
    └─ on failure → _recover_connection()
           ├─ detect_network_state()   → ONLINE / NO_INTERNET / TELEGRAM_DOWN / UNKNOWN
           ├─ NO_INTERNET / TELEGRAM_DOWN → exponential backoff, retry state detection
           └─ ONLINE → rebuild client (tenacity-retried) → loader.reattach(new_client)
                  └─ publishes ConnectionStateChanged via the EventBus
```

There is no proxy layer — connections are always direct. If your network
blocks Telegram, use a system-level VPN (WireGuard, OpenVPN, V2Ray); the bot
has no visibility into or control over that layer.

### Registries

| Component | Module | Purpose |
| --- | --- | --- |
| `AccountRegistry` | `core.registry` | Mutable, live collection of every configured `AccountConfig` |
| `AccountLoaderRegistry` | `core.registry` | `int` → `AccountLoader`, shared across every account (used by `.stats` for cross-account totals) |
| `PluginMetadataStore` | `core.registry` | `(int, stem)` → `PluginMetadata` |
| `EventBus` | `core.events` | Synchronous pub/sub; currently carries `ConnectionStateChanged` |

All four are built once by the composition root and injected — never
imported as globals.

---

## Message Classification System

Every message processed by the bot is classified into **exactly ONE type**
based on strict priority. This ensures consistent behavior across all modules
(`clearer`, `auto_clearer`, `info_handler`, etc.).

### Priority Order (highest → lowest)

| Priority | Type | Description |
| --- | --- | --- |
| 🥇 1 | `file` | Document with filename, no video/audio/sticker attributes |
| 🥈 2 | `vid` | Document with `DocumentAttributeVideo` |
| 🥉 3 | `pic` | Photo (`MessageMediaPhoto`) or photo-like document |
| 4 | `link` | WebPage preview, URL entity, or inline-keyboard URL button |
| 5 | `txt` | Plain text message (no media, no links) |
| 6 | `other` | Sticker, voice, contact, location, poll, etc. |

### Example Classification

| Message | Classified As |
| --- | --- |
| `"Hello world"` | `txt` |
| `"Check out https://github.com/file.exe"` (with WebPage) | `link` |
| `"Hello"` + photo | `pic` |
| `"Check this"` + video | `vid` |
| PDF attachment | `file` |
| Sticker | `other` |

---

## Account Setup

### Automatic (recommended)

```bash
python add_account.py
```

Follow the interactive prompts to enter your API credentials and phone number.

### Manual

Create `userbot_own/accounts/N/account.json` (replace `N` with a number):

```json
{
    "api_id":   12345678,
    "api_hash": "your_api_hash_here",
    "phone":    "+989123456789"
}
```

Then restart the bot.

An optional `"label"` field is supported for your own reference when using
`python add_account.py`'s account list (option 5); it isn't read by the bot
itself.

---

## Configuration

All settings can be overridden via environment variables or a `.env` file
in the `userbot_own/` directory (copy `userbot_own/.env.example` to `userbot_own/.env`).

| Variable | Default | Description |
| --- | --- | --- |
| `LOG_LEVEL` | `DEBUG` | Root logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `BACKOFF_START` | `1` | Initial reconnect back-off in seconds |
| `BACKOFF_MAX` | `300` | Maximum reconnect back-off in seconds |
| `HISTORY_LIMIT` | `2000` | Max messages scanned by clearer / auto_clearer modules |

---

## Available Commands

All commands are sent as outgoing messages (i.e. messages *you* send). This
is a single-owner tool — every configured account is equal, and any command
below works from any of your own accounts without a separate permission
system.

### System (Saved Messages only)

| Command | Description |
| --- | --- |
| `.modules` | List all loaded plugins for this account |
| `.account` | Show current account info |
| `.stats` | System statistics — accounts configured, accounts connected, total modules loaded, uptime |
| `.ping` | API + edit latency, connection quality rating |

### Help System

| Command | Description |
| --- | --- |
| `help` | Category-based compact help (7 logical groups) |
| `help <module>` | Extended help for one module, e.g. `help clearer` |

### Reaction Commands (`reaction_commands` module)

Execute commands by reacting to messages with emojis. Detection is entirely
push-based (`UpdateMessageReactions` + `UpdateEditMessage` — no polling of
any kind). Enabled by default for bots and regular users; disabled by
default for groups/channels (toggle via the module's `ENABLE_FOR_*` class
attributes). Configuration is per-account in
`data/settings/account{N}/reactions.json`.

**Management commands (in Saved Messages only):**

| Command | Description |
| --- | --- |
| `reactions` | Show all configured emoji→command mappings |
| `reaction add <emoji> <command>` | Add a new mapping |
| `reaction remove <emoji>` | Remove a mapping |
| `reaction clear` | Remove all mappings |

**Example usage:**

```
# Setup (in Saved Messages):
reaction add 👍 join       ← react with 👍 on any message → execute "join"
reaction add 👋 left       ← react with 👋 → execute "left"
reaction add 👌 clear txt  ← react with 👌 → clear text messages
reaction add 🔥 whois      ← react with 🔥 → show sender info

# Then react with the configured emoji on any message where reactions are enabled!
```

Reacted-to commands run through a direct-invocation bridge (`modules/bridge.py`,
`MockEvent`) rather than a real round-trip message — currently wired up for
`clear`, `join`/`left`, `info`, and `whois`.

### Clearer (`clear`)

Works in **any chat**. Does not pre-check admin rights — it attempts every
deletion and reports successes/failures/skips from the actual API result,
which the module's own comments note is simpler and more accurate than
guessing permissions up front.

| Command | Description |
| --- | --- |
| `clear` | Delete text + link messages (default) |
| `clear all` | Delete all messages (including stickers, voice, etc.) |
| `clear media` | Delete real media only (pic + vid + file, **no links**) |
| `clear pic` | Photos only |
| `clear vid` | Videos / GIFs only |
| `clear file` | File attachments only |
| `clear txt` | Text-only messages (no links) |
| `clear link` | Messages with WebPage previews or URL entities |
| `clear self` | Your own messages |
| `clear bot` | Bot-sent messages |

**Combining filters:**
- `clear txt self` → only your text messages
- `clear media bot` → only bot's real media
- `clear all self` → all of your messages
- `clear txt link pic` → text, link, **and** photo messages together (multiple
  type keywords combine; fixed in `3.0.1` — they previously overwrote each
  other so only the last one took effect)

**Strict validation:** Invalid arguments (e.g. `clear fvjnfvo`) are silently
ignored to prevent false positives.

### Auto-Clearer (`autoclear`)

Automatically delete bot messages based on type and scope.

| Command | Description |
| --- | --- |
| `autoclear <type> <on/off> <1/2/3>` | Enable/disable auto-clear |
| `autoclear status` | Show current settings (Saved Messages only) |

**Types:** `pic`, `txt`, `vid`, `file`, `link`, `media` (pic+vid+file)

**Scopes:**
- `1` → bot messages only
- `2` → your messages only
- `3` → both

**Context:**
- In **Saved Messages** → global setting (all bots)
- In a **bot chat** → bot-specific setting

### Auto-Forwarder (`autofor`)

Automatically forward bot messages back to the same bot.

| Command | Description |
| --- | --- |
| `autofor <type> <on/off>` | Enable/disable auto-forward |
| `forward status` | Show current settings (Saved Messages only) |

**Types:** `txt`, `pic`, `vid`, `file`, `caption`, `all`

### Join / Left

| Command | Description |
| --- | --- |
| `join` (reply) | Join all chats found in the replied message |
| `left` (reply) | Leave all chats found in the replied message |
| `join delay <sec>` | Fixed delay between joins (`0` restores smart throttling) |
| `join mode fast\|safe\|human` | Anti-FloodWait aggressiveness (see module help for the 4-layer strategy) |
| `folder` | Create / reset the `joined` folder (Saved Messages only) |
| `list` | List chats in the `joined` folder (Saved Messages only) |
| `autoleave <days>` | Auto-leave joined chats after N days |
| `autoleave off` | Disable auto-leave |
| `autoleave status` | Show auto-leave status |

### Info & Whois (work in any chat)

| Command | Description |
| --- | --- |
| `info` (reply) | Show message / media metadata + classification |
| `whois` | Info about current chat |
| `whois @username` | Info about a specific user/channel/group |
| `whois 123456789` | Info by numeric ID |
| `whois` (reply) | Info about replied message sender |

---

## Hot-Reload System

The bot monitors the `modules/` directory with `watchdog`. When a `.py`
file is created, modified, or deleted:

1. The old module instance's `teardown()` is called (handlers removed).
2. The file is re-imported from disk.
3. A new instance is created (via `create_module(context)`) and `setup()` is called.
4. Plugin metadata in `plugin_store` is updated.

This means you can iterate on module code without restarting the bot.
Syntax errors in a module are caught and logged — the rest of the plugins
continue running normally. `base.py`, `router.py`, and `bridge.py` are
shared infrastructure, not plugins, and are never imported as one.

There is currently no in-chat command to trigger a manual reload — edit the
file and the watcher picks it up automatically.

---

## Writing a New Module

1. Create `userbot_own/modules/my_module.py`.
2. Implement the module:

```python
from telethon import TelegramClient, events

from userbot_own.core.context import ModuleContext
from userbot_own.modules.base import Module


class MyModule(Module):
    name = "my_module"

    # Compact help text shown in `help` (2-5 lines max)
    help_text = "• `mycommand` — does something cool\n"

    # Extended help shown via `help my_module` (optional)
    help_extra = (
        "🎯 **My Module - Extended Info:**\n\n"
        "**Commands:**\n"
        "• `mycommand` — detailed description\n"
        "• `mycommand arg` — with arguments\n\n"
        "**Examples:**\n"
        "• `mycommand` → does X\n"
        "• `mycommand foo` → does Y\n\n"
        "**Notes:**\n"
        "• Important edge case 1\n"
        "• Important edge case 2\n"
    )

    def setup(self, client: TelegramClient) -> None:
        self._add_handler(client, events.NewMessage(outgoing=True), self._on_msg)

    async def _on_msg(self, event) -> None:
        if (event.raw_text or "").strip().lower() != "mycommand":
            return
        await event.edit("Hello from my_module!")


def create_module(context: ModuleContext) -> Module:
    return MyModule(context)
```

3. The file is picked up automatically (hot-reload) — no restart needed.

### Key Module Attributes

| Attribute | Required | Description |
| --- | --- | --- |
| `name` | ✅ | Short identifier (used in logs and the plugin store) |
| `help_text` | ✅ | Compact help shown in `help` (2-5 lines) |
| `help_extra` | ❌ | Extended help shown via `help <module_name>` |

There is no admin/permission attribute — every account is equal, and every
module is visible and usable from every account.

### What `context` gives you

`create_module(context: ModuleContext)` receives:

| Field | Type | Notes |
| --- | --- | --- |
| `context.cfg` | `AccountConfig` | Also available as `self.cfg` after `super().__init__(context)` |
| `context.settings` | `Settings` | `backoff_start/max`, `history_limit`, `log_level` |
| `context.event_bus` | `EventBus` | `.subscribe(EventType, handler)` / `.publish(event)` |
| `context.loader_registry` | `AccountLoaderRegistry` | Cross-account — every running account's loader |
| `context.plugin_store` | `PluginMetadataStore` | Cross-account plugin metadata |
| `context.account_registry` | `AccountRegistry` | Every configured account (live — reflects add/remove) |

Most modules only ever touch `self.cfg`. The cross-account fields exist for
the handful of modules that genuinely need them (`system.py`'s `.stats`,
`help_handler.py`, `reaction_commands.py`'s direct-invocation bridge).

### Recommended methods to use

| Method | Purpose |
| --- | --- |
| `self._add_handler(client, builder, callback)` | Register event handler (dedup-safe) |
| `self._safe_edit(message, text)` | Edit message with error handling |
| `self._get_me_id(client)` | Cached self ID lookup |
| `self._is_saved_messages(event)` | True if `event` was sent in this account's own Saved Messages |
| `self._safe_edit_with_auto_delete(event, text, delay=None)` | Edit, then auto-delete after `delay` (falls back to `self._auto_delete_default_delay`, override per-subclass) |
| `self._track_delete_task(message, delay=None)` | Schedule an auto-delete without editing first; tracked so `teardown()` cancels it on hot-reload |
| `self._log_info/warning/error/debug(msg)` | Structured per-account logging |

If your module persists its own settings to a JSON file, use
`userbot_own.helpers.utils.read_json_file(path)` /
`write_json_file_atomic(path, data)` instead of hand-rolling file I/O —
they return `(data, error)` / `error` pairs rather than raising, and the
write is atomic (temp file + rename), so a crash mid-write can never
leave a truncated settings file. See `auto_clearer.py`, `auto_forwarder.py`,
`join_left.py`, or `reaction_commands.py` for examples.

For a multi-command module, `userbot_own.modules.router.CommandRouter` gives you
declarative first-token dispatch instead of a hand-rolled `if/elif` chain —
see `system.py` for a small example.

---

## Versioning Guide

This project follows [Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`).

| Part | When to bump |
| --- | --- |
| **MAJOR** | Breaking architectural change or full rewrite |
| **MINOR** | New module, feature, or meaningful enhancement |
| **PATCH** | Bug fix, refactor, doc update, or minor improvement |

### After every change

1. Update `VERSION` (project root)
2. `userbot_own/__init__.py` reads the version automatically from `VERSION`
3. Prepend a new entry to `CHANGELOG.md`

### Version rule for AI-assisted changes

Every time an AI assistant applies a change, it must:

- Increment the version (PATCH for fixes, MINOR for features)
- Update `VERSION` and `CHANGELOG.md`
- State explicitly: **"Change applied. New version: X.Y.Z"**

---

## FAQ

**Q: Can I run this on Windows?**

A: Yes. Replace `source .venv/bin/activate` with `.venv\Scripts\activate`.

---

**Q: Does this support proxies?**

A: No — direct connection only. If your network blocks Telegram, use a
system-level VPN (WireGuard, OpenVPN, V2Ray) on Termux or Windows; the bot
has no proxy layer of its own to configure.

---

**Q: Where are the log files?**

A: `userbot_own/data/logs/main.log` and per-account `account1.log`, `account2.log`, etc.

---

**Q: How do reaction commands work?**

A: `reaction_commands.py` registers two push-based Telethon event handlers
— `UpdateMessageReactions` and `UpdateEditMessage` (Telegram sometimes sends
the latter instead of the former). There is no polling of any kind. When
your own reaction matches a configured emoji, the module invokes the target
command's handler directly via a small adapter (`MockEvent`, in
`modules/bridge.py`) rather than sending a real message — faster and avoids
a round trip through the event loop.

*(`3.0.2` note: the `UpdateMessageReactions` path was silently non-functional
before this version — it read two attributes that don't exist on that
update type, so it always did nothing, and every reaction command was
actually running through `UpdateEditMessage` alone. Fixed; both paths are
genuinely redundant now, which should mean faster and more reliable
detection.)*

---

**Q: I reacted again (or changed my reaction) on a message I already used — why didn't it re-trigger, before `3.0.2`?**

A: Before `3.0.2`, once an emoji had triggered its command on a given
message, that exact (message, emoji) combination was blocked from ever
triggering again, permanently, for the life of the running process — even
if you removed the reaction and added it back later. Fixed in `3.0.2`: the
module now tracks your *current* reaction state per message instead of a
permanent history, so removing a reaction and reacting again (with the same
or a different emoji) correctly re-triggers. Telegram re-delivering the
same still-active reaction (e.g. because both push routes above fired for
one underlying change) still correctly does *not* double-execute.

---

**Q: Can I use reaction commands on bot messages?**

A: Yes, bot chats are enabled by default (`ENABLE_FOR_BOTS = True`), via the
same two push-based handlers above — not a separate mechanism.

---

**Q: What's the difference between `clear txt` and `clear link`?**

A: `clear txt` deletes only plain text messages (no media, no links).
`clear link` deletes messages with WebPage previews or URL entities.
A message containing a download link like `https://github.com/file.exe`
is classified as `link`, not `txt`.

---

**Q: Why does `clear media` not delete link messages?**

A: `media` refers to **real attachments** only (photos, videos, files).
WebPage previews are metadata, not media. Use `clear link` or `clear all`
for link messages.

---

**Q: What happened to the AI modules?**

A: `ai_assistant.py` and `ai_bot.py` were removed in version `1.0.0`.
See `CHANGELOG.md` for the full rationale.

---

**Q: What happened to the admin/permission system?**

A: Removed — see `CHANGELOG.md` for the version where `is_admin`,
`is_admin_only`, and related permission checks were stripped from
`config.py`, `modules/base.py`, `system.py`, `help_handler.py`, and the
plugin loader. This is now a single-owner tool: every configured account is
equal, and every command is available from every account.

---

**Q: Is there an in-chat way to add or remove accounts?**

A: No. An earlier version of this codebase (`account_management/flows.py`,
`AccountFlowManager`) implemented a full interactive in-chat add/remove
flow, but it was never wired to any command — nothing ever called
`start_add_flow()` / `start_remove_flow()`, so `.addaccount`,
`.removeaccount <n>`, and `.cancelflow` never actually worked as live
commands. Since it was confirmed unreachable and unused, it was removed
entirely in version `3.0.1` rather than kept as dormant code.
`python add_account.py` (interactive CLI) is the supported way to add or
remove accounts.

---

**Q: How do I write a custom module?**

A: See [Writing a New Module](#writing-a-new-module). The minimum
requirement is a class inheriting from `Module` with a `name`, `help_text`,
and a `create_module(context)` factory function.
