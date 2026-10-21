"""
userbot_own/modules/packages/whois_handler/const.py
════════════════════════════════════════════════════════════════
Tunable limits for the whois command.

Pure data — the caption and message length limits, and how long a built
result stays in the cache.
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

# Logging is provided by Module._log_* helpers; no module-level logger needed.

# Telegram's caption limit for media messages is 1024 characters. If the
# built whois text would exceed this, attaching it as a photo caption would
# either fail or silently truncate server-side — safer to fall back to the
# existing text-only message in that case than to lose information.
_CAPTION_LIMIT = 1024

# Telegram's message-length limit for regular text messages is ~4096
# characters. This is the text-only-path equivalent safety net to
# `_CAPTION_LIMIT` above (v3.1.5) — without it, an entity with a maximal
# bio/description plus many flags/restriction reasons could in principle
# produce a message Telegram itself would reject or truncate server-side.
_MESSAGE_LIMIT = 4096

# How long a built whois result (info text + entity) stays in the local
# cache before a fresh lookup is forced again (v3.1.5). Short enough that
# it won't meaningfully mask real-time profile changes; long enough to
# absorb repeated/accidental re-runs of `whois` on the same target within
# a short window without re-issuing GetFull*Request/photo calls.
_CACHE_TTL_SECONDS = 90.0
