#!/usr/bin/env python3
"""
userbot_own/account_management/cli.py  (was add_account.py at the repo root)
════════════════════════════════════════════════════════════════
Interactive account manager for the Multi-Account Userbot.

Menu options:
  1) Add new account         — two ways to add: full login, or import
                                an existing string session (no login)
  2) Re-login account        — recreate session (backup taken first)
  3) Edit account details    — modify api_id / api_hash / phone / label
  4) Remove account          — delete account folder entirely
  5) List accounts           — show all accounts with status
  6) Verify session          — check if the account's session is valid
  7) Exit

Account schema (account.json):
  {
      "api_id":         12345678,
      "api_hash":       "abcdef...",
      "phone":          "+989123456789",
      "label":          "اکانت شخصی",   ← optional nickname
      "session_string": "1BVtsO..."     ← optional, Telethon StringSession
  }

  "session_string" is optional and additive: when present, the account
  connects via telethon.sessions.StringSession instead of the on-disk
  session.session file. When absent, behavior is 100% unchanged from
  before — the account uses the file-based session exactly as it always
  has. An account can have a file session, a string session, or both;
  the string session (if present) always takes priority when building
  the client (see core/telegram_client.py).

  On every successful login (new account or re-login), the string
  session equivalent of the freshly-created file session is
  automatically derived and saved into account.json too — no extra
  step required. This does NOT create a new Telegram login/auth key by
  itself; it only serializes the auth key that the login step already
  created. Its purpose is purely so that account.json alone (independent
  of the .session file on disk) is always enough to recover access if
  the .session file is ever lost/corrupted, without repeating phone +
  code + 2FA.

Run:  python add_account.py   (a thin repo-root shim that calls main() here)

Migration notes:
- BASE_DIR now resolves two directories up from this file's new location
  (userbot/account_management/cli.py → userbot/account_management →
  userbot/) instead of one, since the file itself moved. It points at the
  exact same userbot/ directory — and therefore the exact same accounts/
  folder — as before the refactor.
- The header's version string previously hardcoded "v2.1.0+" as a string
  literal (a second, independent copy of the same number duplicated in
  the old core/client.py and userbot/__init__.py). It now imports the
  single canonical userbot.__version__ instead.
- Everything else — every prompt, validation rule, retry count, and menu
  flow — is unchanged, except where noted in the v3.1.3 section of
  CHANGELOG.md (string session support, richer session-status reporting,
  and the empty-account-folder cleanup fix).
════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from userbot_own import __version__

# ── Enable ANSI colors on Windows 10+ ─────────────────────────────────────────
if sys.platform == "win32":
    os.system("")


# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent  # → userbot/
ACCOUNTS_DIR = BASE_DIR / "accounts"

# ── Validation Constants ──────────────────────────────────────────────────────
_PHONE_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")
_API_HASH_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_API_ID_MIN = 1000
_API_ID_MAX = 99_999_999
_LABEL_MAX_LEN = 40
# Telethon StringSession values are base64-ish and, in practice, always
# well over 100 chars. This is just a sanity floor to catch obvious
# copy-paste mistakes (truncated/empty paste) before we ever try to
# connect with it — not a strict format check.
_SESSION_STRING_MIN_LEN = 50

# Network timeouts (seconds)
_CONNECT_TIMEOUT = 30.0
_CODE_REQUEST_TIMEOUT = 30.0
_SIGNIN_TIMEOUT = 30.0
_GET_ME_TIMEOUT = 10.0

# Max retries for user inputs
_MAX_RETRIES = 5


# ── Terminal Colors ───────────────────────────────────────────────────────────
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"


def _ok(msg: str) -> None:
    print(f"{C.GREEN}  ✔  {msg}{C.RESET}")


def _err(msg: str) -> None:
    print(f"{C.RED}  ✘  {msg}{C.RESET}")


def _info(msg: str) -> None:
    print(f"{C.CYAN}  ›  {msg}{C.RESET}")


def _warn(msg: str) -> None:
    print(f"{C.YELLOW}  ⚠  {msg}{C.RESET}")


def _sep(char: str = "─", width: int = 70) -> None:
    print(f"{C.DIM}{char * width}{C.RESET}")


def _ask(prompt: str, default: str = "", secret: bool = False) -> str:
    """Safely ask user for input with optional secret mode."""
    suffix = f" [{default}]" if default else ""
    display = f"{C.BOLD}  {prompt}{suffix}: {C.RESET}"
    try:
        if secret:
            import getpass
            val = getpass.getpass(display)
        else:
            val = input(display).strip()
        return val or default
    except (KeyboardInterrupt, EOFError):
        print()
        raise


def _confirm(prompt: str, default: bool = False) -> bool:
    """Ask for yes/no confirmation."""
    hint = "Y/n" if default else "y/N"
    try:
        reply = _ask(f"{prompt} ({hint})", default="y" if default else "n").lower()
        return reply in ("y", "yes", "1", "true")
    except (KeyboardInterrupt, EOFError):
        return False


# ── Validation Helpers ────────────────────────────────────────────────────────

def _validate_api_id(raw: str) -> int | None:
    try:
        api_id = int(raw)
        if not (_API_ID_MIN <= api_id <= _API_ID_MAX):
            return None
        return api_id
    except (ValueError, TypeError):
        return None


def _validate_api_hash(raw: str) -> str | None:
    raw = raw.strip()
    if _API_HASH_RE.match(raw):
        return raw
    return None


def _normalize_phone(raw: str) -> str | None:
    """Normalize phone number to E.164 format."""
    raw = re.sub(r"[\s\-\(\)]", "", raw)
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    elif not raw.startswith("+"):
        raw = "+" + raw

    if _PHONE_E164_RE.match(raw):
        return raw
    return None


def _validate_label(raw: str) -> str:
    """
    Validate and normalize label (nickname).
    - Strips leading/trailing whitespace
    - Truncates to _LABEL_MAX_LEN characters
    - Returns empty string if invalid
    """
    label = raw.strip()
    # Remove control characters
    label = re.sub(r"[\x00-\x1f\x7f]", "", label)
    if len(label) > _LABEL_MAX_LEN:
        label = label[:_LABEL_MAX_LEN]
    return label


# ── Account Helpers ───────────────────────────────────────────────────────────

def _next_index() -> int:
    """Return the next available account slot number."""
    ACCOUNTS_DIR.mkdir(exist_ok=True)
    existing = [
        int(p.name)
        for p in ACCOUNTS_DIR.iterdir()
        if p.is_dir() and p.name.isdigit()
    ]
    return max(existing, default=0) + 1


def _all_account_dirs() -> list[Path]:
    ACCOUNTS_DIR.mkdir(exist_ok=True)
    return sorted(
        [p for p in ACCOUNTS_DIR.iterdir() if p.is_dir() and p.name.isdigit()],
        key=lambda p: int(p.name),
    )


def _read_cfg(folder: Path) -> dict | None:
    cfg_file = folder / "account.json"
    if not cfg_file.exists():
        return None
    try:
        return json.loads(cfg_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_cfg(folder: Path, cfg: dict) -> bool:
    """Atomically write account.json via temp file + rename."""
    cfg_file = folder / "account.json"
    tmp_file = folder / "account.json.tmp"
    try:
        tmp_file.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_file.replace(cfg_file)
        return True
    except OSError as exc:
        _err(f"Failed to write account.json: {exc}")
        return False


def _backup_cfg(folder: Path) -> Path | None:
    """Create a timestamped backup of account.json before modifications."""
    cfg_file = folder / "account.json"
    if not cfg_file.exists():
        return None
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = folder / f"account.json.backup_{ts}"
        shutil.copy2(cfg_file, backup)
        return backup
    except OSError as exc:
        _warn(f"Could not create backup: {exc}")
        return None


def _session_files(account_dir: Path) -> list[Path]:
    """Return all session-related files (handles WAL/SHM modes)."""
    files = []
    for ext in (".session", ".session-journal", ".session-shm", ".session-wal"):
        p = account_dir / f"session{ext}"
        if p.exists():
            files.append(p)
    return files


def _has_file_session(account_dir: Path) -> bool:
    """True if the main session.session file exists (the file the client
    actually needs — journal/shm/wal are just SQLite side-files)."""
    return (account_dir / "session.session").exists()


def _has_string_session(cfg: dict | None) -> bool:
    """True if account.json carries a non-empty session_string."""
    return bool(cfg and str(cfg.get("session_string", "")).strip())


def _session_status(account_dir: Path, cfg: dict | None) -> tuple[str, bool]:
    """
    Build a short colored status tag describing what login material this
    account has available, and whether it has *any* usable session at all.

    Four possible states:
      - both file + string session  → most resilient, "✔ file+string"
      - file session only            → "✔ file"
      - string session only          → "✔ string" (no local .session yet;
                                         one will be created on next start)
      - neither                      → "⏳ no session"
    """
    has_file = _has_file_session(account_dir)
    has_string = _has_string_session(cfg)

    if has_file and has_string:
        return f"{C.GREEN}✔ file+string{C.RESET}", True
    if has_file:
        return f"{C.GREEN}✔ file{C.RESET}", True
    if has_string:
        return f"{C.GREEN}✔ string{C.RESET}", True
    return f"{C.YELLOW}⏳ no session{C.RESET}", False


# ── Display ───────────────────────────────────────────────────────────────────

def _print_header(title: str) -> None:
    print()
    _sep("═")
    print(f"{C.BOLD}{C.CYAN}  {title}{C.RESET}")
    _sep("═")
    print()


def _format_label(label: str | None, phone: str) -> str:
    """Format label for display, falling back to phone."""
    if label:
        return label
    # Fallback: use last 4 digits of phone for compact display
    if phone and phone != "N/A":
        return f"({phone[-4:]})"
    return "—"


def _list_accounts(verbose: bool = True) -> list[Path]:
    """List all accounts with status, session info, and label."""
    dirs = _all_account_dirs()
    if not dirs:
        _warn("No accounts found.")
        return dirs

    if verbose:
        # Dynamic column widths based on content
        print(f"\n{C.BOLD}  {'#':<4} {'Label':<20} {'Phone':<18} {'API ID':<12} {'Session'}{C.RESET}")
        _sep()

        for d in dirs:
            cfg = _read_cfg(d)
            sess_tag, _has_any = _session_status(d, cfg)

            if cfg:
                phone = cfg.get("phone", "N/A")
                api_id = str(cfg.get("api_id", "N/A"))
                label = cfg.get("label", "") or ""

                # Colorize label: cyan if set, dim if fallback
                if label:
                    label_display = f"{C.CYAN}{label[:18]}{C.RESET}"
                    # Pad to 20 chars accounting for ANSI codes
                    padding = 20 - len(label[:18])
                    label_display += " " * padding
                else:
                    fallback = _format_label("", phone)
                    label_display = f"{C.DIM}{fallback[:18]}{C.RESET}"
                    padding = 20 - len(fallback[:18])
                    label_display += " " * padding

                print(f"  {d.name:<4} {label_display} {phone:<18} {api_id:<12} {sess_tag}")
            else:
                print(f"  {d.name:<4} {C.RED}missing or broken account.json{C.RESET}")

        print()

    return dirs


# ── Collect Account Info ─────────────────────────────────────────────────────

def _collect_info(index: int, existing: dict | None = None) -> dict | None:
    """
    Interactively collect account info with retry loops and validation.
    Returns dict with api_id/api_hash/phone/label, or None if user cancels.
    """
    _print_header(
        f"{'✏️  Edit' if existing else '➕ New'} Account #{index}"
    )
    if not existing:
        _info("Get your credentials at https://my.telegram.org/apps")
        print()

    # ── api_id ──────────────────────────────────────────────────────
    api_id = None
    default_id = str(existing.get("api_id", "")) if existing else ""
    for attempt in range(1, _MAX_RETRIES + 1):
        raw = _ask("api_id", default=default_id)
        api_id = _validate_api_id(raw)
        if api_id is not None:
            break
        _err(
            f"api_id must be an integer between {_API_ID_MIN:,} and "
            f"{_API_ID_MAX:,}. (Attempt {attempt}/{_MAX_RETRIES})"
        )
    else:
        _err("Too many invalid attempts. Aborting.")
        return None

    # ── api_hash ────────────────────────────────────────────────────
    api_hash = None
    default_hash = existing.get("api_hash", "") if existing else ""
    for attempt in range(1, _MAX_RETRIES + 1):
        raw = _ask("api_hash", default=default_hash)
        api_hash = _validate_api_hash(raw)
        if api_hash is not None:
            break
        _err(
            "api_hash must be exactly 32 hexadecimal characters. "
            f"(Attempt {attempt}/{_MAX_RETRIES})"
        )
    else:
        _err("Too many invalid attempts. Aborting.")
        return None

    # ── phone ───────────────────────────────────────────────────────
    phone = None
    default_phone = existing.get("phone", "") if existing else ""
    for attempt in range(1, _MAX_RETRIES + 1):
        raw = _ask("Phone number (e.g. +989123456789)", default=default_phone)
        phone = _normalize_phone(raw)
        if phone is not None:
            break
        _err(
            "Phone must be in E.164 format (7-15 digits after +). "
            f"(Attempt {attempt}/{_MAX_RETRIES})"
        )
    else:
        _err("Too many invalid attempts. Aborting.")
        return None

    # ── label (nickname) ────────────────────────────────────────────
    # Optional field — user can skip with Enter
    default_label = existing.get("label", "") if existing else ""
    print()
    _info(f"Label (nickname) is optional. Press Enter to {'keep current' if existing else 'skip'}.")
    _info(f"Used to identify this account easily (max {_LABEL_MAX_LEN} chars).")
    raw_label = _ask("Label / Nickname", default=default_label)
    label = _validate_label(raw_label)
    if raw_label and not label:
        _warn("Label was empty after cleanup. Label will not be set.")

    # ── Summary ─────────────────────────────────────────────────────
    print()
    _ok(f"api_id   : {api_id}")
    _ok(f"api_hash : {api_hash[:6]}…{api_hash[-4:]}")
    _ok(f"phone    : {phone}")
    if label:
        _ok(f"label    : {label}")
    else:
        _info("label    : (not set)")
    print()

    return {
        "api_id": api_id,
        "api_hash": api_hash,
        "phone": phone,
        "label": label,
    }


# ── Telegram Login ────────────────────────────────────────────────────────────

def _build_client(cfg: dict, account_dir: Path):
    """
    Build a TelegramClient for this account.

    Priority: if account.json already carries a session_string, connect
    with that (telethon.sessions.StringSession) — no local .session file
    is required for this to work. Otherwise, fall back to the file-based
    session at accounts/N/session.session, exactly as before.

    Raises ImportError if telethon is not installed (caller handles it).
    """
    from telethon import TelegramClient
    from telethon.network.connection import ConnectionTcpAbridged

    session_string = str(cfg.get("session_string", "")).strip()
    if session_string:
        from telethon.sessions import StringSession
        session = StringSession(session_string)
    else:
        session = str(account_dir / "session")

    return TelegramClient(
        session,
        cfg["api_id"],
        cfg["api_hash"],
        connection=ConnectionTcpAbridged,
    )


def _save_string_session(cfg: dict, account_dir: Path, client) -> str | None:
    """
    Derive the StringSession equivalent of `client`'s current (already
    authorized) session and persist it into account.json.

    This does NOT talk to Telegram and does NOT create a new auth key —
    it only serializes the auth key the current session already holds.
    Safe to call after any successful login/re-login. Returns the string
    on success, or None if it could not be saved (non-fatal either way;
    the file-based session still works normally).
    """
    try:
        from telethon.sessions import StringSession
        session_string = StringSession.save(client.session)
    except Exception as exc:
        _warn(f"Could not derive string session (non-fatal): {exc}")
        return None

    cfg["session_string"] = session_string
    if _write_cfg(account_dir, cfg):
        _ok("String session saved to account.json (backup of your login).")
        return session_string

    _warn("Could not save string session to account.json.")
    return None


async def _login(index: int, cfg: dict, account_dir: Path) -> bool:
    """Connect to Telegram and authenticate. Returns True on success."""
    try:
        from telethon import errors as tl_errors
    except ImportError:
        _err("telethon is not installed. Run: pip install telethon")
        return False

    label_or_phone = cfg.get("label") or cfg.get("phone", "?")
    print()
    _sep()
    _info(f"Connecting to Telegram for account #{index} ({label_or_phone})...")
    _sep()
    print()

    client = _build_client(cfg, account_dir)

    try:
        # ── Step 1: Connect with timeout ────────────────────────────
        try:
            await asyncio.wait_for(client.connect(), timeout=_CONNECT_TIMEOUT)
        except TimeoutError:
            _err(f"Connection timed out after {_CONNECT_TIMEOUT}s. Check your network.")
            return False
        except Exception as exc:
            _err(f"Connection failed: {exc}")
            return False

        # ── Step 2: Already authorized? ─────────────────────────────
        try:
            authorized = await asyncio.wait_for(
                client.is_user_authorized(), timeout=_GET_ME_TIMEOUT
            )
        except TimeoutError:
            _err("Authorization check timed out.")
            return False

        if authorized:
            try:
                me = await asyncio.wait_for(client.get_me(), timeout=_GET_ME_TIMEOUT)
                _ok(f"Session already exists — {me.first_name} (ID: {me.id})")
            except Exception as exc:
                _warn(f"Authorized but couldn't fetch user info: {exc}")
            _save_string_session(cfg, account_dir, client)
            return True

        # ── Step 3: Send code with retry ────────────────────────────
        _info(f"Sending verification code to {cfg['phone']}...")
        code_request_ok = False
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                await asyncio.wait_for(
                    client.send_code_request(cfg["phone"]),
                    timeout=_CODE_REQUEST_TIMEOUT,
                )
                code_request_ok = True
                break
            except tl_errors.FloodWaitError as exc:
                _warn(f"Telegram rate-limited. Waiting {exc.seconds}s...")
                await asyncio.sleep(exc.seconds)
            except tl_errors.PhoneNumberInvalidError:
                _err("Phone number is not registered with Telegram.")
                return False
            except TimeoutError:
                _warn(f"Code request timed out. (Attempt {attempt}/{_MAX_RETRIES})")
            except Exception as exc:
                _err(f"Failed to send code: {exc}")
                return False

        if not code_request_ok:
            _err("Could not send verification code after multiple attempts.")
            return False

        # ── Step 4: Enter code with retry ───────────────────────────
        code = ""
        for attempt in range(1, _MAX_RETRIES + 1):
            code = _ask("Verification code (spaces allowed)").replace(" ", "")
            if not code or not code.isdigit():
                _err(f"Code must contain only digits. (Attempt {attempt}/{_MAX_RETRIES})")
                continue

            try:
                await asyncio.wait_for(
                    client.sign_in(cfg["phone"], code),
                    timeout=_SIGNIN_TIMEOUT,
                )
                break
            except tl_errors.SessionPasswordNeededError:
                break
            except tl_errors.PhoneCodeInvalidError:
                _err(f"Invalid code. (Attempt {attempt}/{_MAX_RETRIES})")
                if attempt == _MAX_RETRIES:
                    _err("Too many invalid code attempts.")
                    return False
            except tl_errors.PhoneCodeExpiredError:
                _err("Code has expired. Please run this script again to request a new code.")
                return False
            except TimeoutError:
                _err(f"Sign-in timed out. (Attempt {attempt}/{_MAX_RETRIES})")
            except Exception as exc:
                _err(f"Sign-in failed: {exc}")
                return False

        # ── Step 5: 2FA password (if needed) with retry ─────────────
        try:
            authorized = await asyncio.wait_for(
                client.is_user_authorized(), timeout=_GET_ME_TIMEOUT
            )
        except Exception:
            authorized = False

        if not authorized:
            print()
            _warn("Two-step verification (2FA) is enabled.")
            password_ok = False
            for attempt in range(1, _MAX_RETRIES + 1):
                password = _ask("2FA password")
                if not password:
                    _err("Password cannot be empty.")
                    continue
                try:
                    await asyncio.wait_for(
                        client.sign_in(password=password),
                        timeout=_SIGNIN_TIMEOUT,
                    )
                    password_ok = True
                    break
                except tl_errors.PasswordHashInvalidError:
                    _err(f"Wrong password. (Attempt {attempt}/{_MAX_RETRIES})")
                except TimeoutError:
                    _err(f"Password sign-in timed out. (Attempt {attempt}/{_MAX_RETRIES})")
                except Exception as exc:
                    _err(f"Password sign-in failed: {exc}")

            if not password_ok:
                _err("Could not verify 2FA password.")
                return False

        # ── Step 6: Success ─────────────────────────────────────────
        try:
            me = await asyncio.wait_for(client.get_me(), timeout=_GET_ME_TIMEOUT)
            _ok(f"Login successful — {me.first_name} (ID: {me.id})")
        except Exception as exc:
            _warn(f"Login succeeded but couldn't fetch user info: {exc}")

        # Derive and persist the string-session equivalent of the fresh
        # login. Purely additive: does not create another auth key, just
        # backs up the one that was just created so account.json alone
        # can recover access if the .session file is ever lost.
        _save_string_session(cfg, account_dir, client)
        return True

    except KeyboardInterrupt:
        print()
        _warn("Login cancelled by user.")
        return False

    except Exception as exc:
        _err(f"Unexpected error: {exc}")
        return False

    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


# ── Menu Actions ──────────────────────────────────────────────────────────────

def _cleanup_if_empty(account_dir: Path) -> None:
    """
    Remove account_dir if it exists and is completely empty.

    Used to clean up after a cancelled "Add new account" flow so that an
    aborted attempt never leaves behind a dangling empty folder that
    would otherwise show up as a phantom account (e.g. "accounts/3/"
    with nothing in it) in the list/menu screens.

    Safe to call unconditionally — silently does nothing if the folder
    is missing, non-empty, or can't be removed for some reason (e.g. a
    permissions issue); this is best-effort cleanup, never a hard error.
    """
    try:
        if account_dir.exists() and account_dir.is_dir() and not any(account_dir.iterdir()):
            account_dir.rmdir()
    except OSError:
        pass


async def _action_add() -> None:
    """
    Option 1: Add a brand-new account.

    Offers two independent ways to add an account:
      1) Full login  — phone + verification code (+ 2FA if enabled).
                        Talks to Telegram and creates a new auth key.
      2) String session — paste an existing Telethon StringSession you
                        already have (e.g. backed up from another
                        account.json, or generated elsewhere). No new
                        login/auth key is created; the account is simply
                        verified and registered.
    """
    _print_header("Add New Account")
    print(f"  {C.BOLD}How do you want to add this account?{C.RESET}")
    print(f"    {C.WHITE}1{C.RESET}  Full login (phone + code, new session)")
    print(f"    {C.WHITE}2{C.RESET}  Import existing string session (no login)")
    print(f"    {C.WHITE}3{C.RESET}  Cancel")
    print()

    try:
        choice = _ask("Choice", default="1")
    except (KeyboardInterrupt, EOFError):
        print()
        _info("Add cancelled.")
        return

    print()

    if choice == "1":
        await _action_add_full()
    elif choice == "2":
        await _action_add_string()
    elif choice == "3":
        _info("Add cancelled.")
    else:
        _err(f"Unknown choice: '{choice}'.")


async def _action_add_full() -> None:
    """Add-flow 1: create account.json, then run the full interactive login."""
    index = _next_index()
    account_dir = ACCOUNTS_DIR / str(index)
    account_dir.mkdir(parents=True, exist_ok=True)

    try:
        cfg = _collect_info(index)
        if cfg is None:
            return

        if not _write_cfg(account_dir, cfg):
            return

        _ok(f"Saved accounts/{index}/account.json")
        print()

        if _confirm("Create session now?", default=True):
            success = await _login(index, cfg, account_dir)
            if success:
                print()
                _sep("═")
                print(f"{C.GREEN}{C.BOLD}  Account #{index} added successfully!{C.RESET}")
                _sep("═")
                print(f"  📁  Path : accounts/{index}/")
                print(f"  📱  Phone: {cfg['phone']}")
                if cfg.get("label"):
                    print(f"  🏷   Label: {cfg['label']}")
                print()
            else:
                _warn(f"Account #{index} saved but session was not created.")
                _info("Run this script again and choose option 2 to retry login.")
        else:
            _info(f"Account #{index} saved. Run this script again to create the session.")
    finally:
        # Runs on every exit path — normal return, cancelled input, or a
        # raised KeyboardInterrupt/EOFError from anywhere inside
        # _collect_info() — so a folder created above never survives
        # empty. If account.json was written, the folder is non-empty
        # and this is a no-op.
        _cleanup_if_empty(account_dir)


async def _action_add_string() -> None:
    """
    Add-flow 2: register an account from an existing Telethon
    StringSession, with no phone/code/2FA login step at all.

    api_id/api_hash/session_string are required. Phone number and name
    are fetched automatically from Telegram after connecting (via
    get_me()) so the user doesn't have to re-type anything they've
    already authenticated with — the phone field is only ever used
    for display/reference in this project, never for a new sign-in.
    """
    index = _next_index()
    account_dir = ACCOUNTS_DIR / str(index)
    account_dir.mkdir(parents=True, exist_ok=True)

    try:
        _print_header(f"➕ New Account #{index} — String Session Import")
        print()

        # ── api_id ──────────────────────────────────────────────────
        api_id = None
        for attempt in range(1, _MAX_RETRIES + 1):
            raw = _ask("api_id")
            api_id = _validate_api_id(raw)
            if api_id is not None:
                break
            _err(
                f"api_id must be an integer between {_API_ID_MIN:,} and "
                f"{_API_ID_MAX:,}. (Attempt {attempt}/{_MAX_RETRIES})"
            )
        else:
            _err("Too many invalid attempts. Aborting.")
            return

        # ── api_hash ────────────────────────────────────────────────
        api_hash = None
        for attempt in range(1, _MAX_RETRIES + 1):
            raw = _ask("api_hash")
            api_hash = _validate_api_hash(raw)
            if api_hash is not None:
                break
            _err(
                "api_hash must be exactly 32 hexadecimal characters. "
                f"(Attempt {attempt}/{_MAX_RETRIES})"
            )
        else:
            _err("Too many invalid attempts. Aborting.")
            return

        # ── session_string ──────────────────────────────────────────
        session_string = None
        for attempt in range(1, _MAX_RETRIES + 1):
            raw = _ask("String session").strip()
            if len(raw) >= _SESSION_STRING_MIN_LEN:
                session_string = raw
                break
            _err(
                f"That doesn't look like a valid string session (too short). "
                f"(Attempt {attempt}/{_MAX_RETRIES})"
            )
        else:
            _err("Too many invalid attempts. Aborting.")
            return

        # ── label (nickname, optional) ─────────────────────────────
        print()
        _info(f"Label (nickname) is optional. Press Enter to skip (max {_LABEL_MAX_LEN} chars).")
        label = _validate_label(_ask("Label / Nickname"))

        cfg = {
            "api_id": api_id,
            "api_hash": api_hash,
            "phone": "",  # filled in automatically below, on success
            "label": label,
            "session_string": session_string,
        }

        # ── Connect and verify the string session actually works ────
        try:
            from telethon import errors as tl_errors
        except ImportError:
            _err("telethon is not installed. Run: pip install telethon")
            return

        print()
        _info(f"Connecting to Telegram for account #{index} to verify the session...")
        client = _build_client(cfg, account_dir)

        try:
            await asyncio.wait_for(client.connect(), timeout=_CONNECT_TIMEOUT)

            try:
                authorized = await asyncio.wait_for(
                    client.is_user_authorized(), timeout=_GET_ME_TIMEOUT
                )
            except TimeoutError:
                _err("Authorization check timed out.")
                return

            if not authorized:
                _err(
                    "This string session is not authorized (expired, revoked, "
                    "or logged out). Please generate/obtain a fresh one."
                )
                return

            try:
                me = await asyncio.wait_for(client.get_me(), timeout=_GET_ME_TIMEOUT)
                cfg["phone"] = f"+{me.phone}" if me.phone else ""
                _ok(f"String session verified — {me.first_name} (ID: {me.id})")
                if cfg["phone"]:
                    _ok(f"Phone: {cfg['phone']}")
            except TimeoutError:
                _warn("Session authorized but couldn't fetch user info (timed out).")
            except Exception as exc:
                _warn(f"Session authorized but couldn't fetch user info: {exc}")

        except tl_errors.AuthKeyError:
            _err("This string session's auth key is invalid.")
            return
        except TimeoutError:
            _err(f"Connection timed out after {_CONNECT_TIMEOUT}s.")
            return
        except Exception as exc:
            _err(f"Verification failed: {exc}")
            return
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

        if not _write_cfg(account_dir, cfg):
            return

        print()
        _sep("═")
        print(f"{C.GREEN}{C.BOLD}  Account #{index} added successfully (via string session)!{C.RESET}")
        _sep("═")
        print(f"  📁  Path : accounts/{index}/")
        if cfg.get("phone"):
            print(f"  📱  Phone: {cfg['phone']}")
        if cfg.get("label"):
            print(f"  🏷   Label: {cfg['label']}")
        print(f"  {C.DIM}No local session file needed — this account connects directly")
        print(f"  from the session_string stored in account.json.{C.RESET}")
        print()

    except (KeyboardInterrupt, EOFError):
        print()
        _warn("Add via string session cancelled.")
    finally:
        # Same guarantee as the full-login flow: never leave an empty
        # accounts/N/ folder behind after a cancelled/failed attempt.
        _cleanup_if_empty(account_dir)


async def _action_relogin() -> None:
    """Option 2: Re-create session for an existing account."""
    _print_header("Re-login Account")
    dirs = _list_accounts(verbose=True)
    if not dirs:
        return

    raw = _ask("Account number to re-login")
    if not raw.isdigit():
        _err("Invalid account number.")
        return

    idx = int(raw)
    account_dir = ACCOUNTS_DIR / str(idx)
    cfg = _read_cfg(account_dir)
    if cfg is None:
        _err(f"accounts/{idx}/account.json not found or unreadable.")
        return

    label_or_phone = cfg.get("label") or cfg.get("phone", "?")
    print()
    _warn(f"You are about to re-login account #{idx} ({label_or_phone}).")
    _info("This will delete the current session and request a new code.")
    if cfg.get("session_string"):
        _info(
            "The stored session_string will also be cleared and replaced "
            "with a fresh one from the new login, so this always forces a "
            "brand-new auth key instead of silently reusing the old one."
        )
    if not _confirm("Continue?", default=True):
        _info("Re-login cancelled.")
        return

    backup = _backup_cfg(account_dir)
    if backup:
        _info(f"Backup created: {backup.name}")

    # Force a genuinely fresh login: drop any existing string session too,
    # otherwise _build_client() would happily reconnect with the old
    # (still-valid) string session and skip the new login entirely — which
    # would defeat the purpose of explicitly asking to re-login.
    cfg["session_string"] = ""
    _write_cfg(account_dir, cfg)

    sess_files = _session_files(account_dir)
    for p in sess_files:
        try:
            p.unlink()
            _info(f"Deleted: {p.name}")
        except PermissionError:
            _err(
                f"Could not delete {p.name} — file is locked. "
                "Make sure main.py is not running for this account."
            )
            return
        except OSError as exc:
            _err(f"Could not delete {p.name}: {exc}")
            return

    success = await _login(idx, cfg, account_dir)
    if success:
        _ok(f"Account #{idx} session recreated successfully.")


async def _action_edit() -> None:
    """Option 3: Edit existing account details."""
    _print_header("Edit Account")
    dirs = _list_accounts(verbose=True)
    if not dirs:
        return

    raw = _ask("Account number to edit")
    if not raw.isdigit():
        _err("Invalid account number.")
        return

    idx = int(raw)
    account_dir = ACCOUNTS_DIR / str(idx)
    cfg = _read_cfg(account_dir)
    if cfg is None:
        _err(f"accounts/{idx}/account.json not found or unreadable.")
        return

    # Show current config summary
    print()
    _info("Current configuration:")
    print(f"    api_id   : {cfg.get('api_id', 'N/A')}")
    print(f"    api_hash : {str(cfg.get('api_hash', ''))[:6]}…")
    print(f"    phone    : {cfg.get('phone', 'N/A')}")
    print(f"    label    : {cfg.get('label') or f'{C.DIM}(not set){C.RESET}'}")
    sess_tag, _has_any = _session_status(account_dir, cfg)
    print(f"    session  : {sess_tag}")
    print()

    # Ask user which field to edit
    print(f"  {C.BOLD}What to edit?{C.RESET}")
    print(f"    {C.WHITE}1{C.RESET}  All fields (full re-entry)")
    print(f"    {C.WHITE}2{C.RESET}  Label only (quick rename)")
    print(f"    {C.WHITE}3{C.RESET}  Cancel")
    print()

    try:
        choice = _ask("Choice", default="3")
    except (KeyboardInterrupt, EOFError):
        _info("Edit cancelled.")
        return

    if choice == "3":
        _info("Edit cancelled.")
        return

    if choice == "2":
        # Quick label-only edit
        print()
        _info(f"Current label: {cfg.get('label') or '(not set)'}")
        _info("Press Enter without typing to remove the label.")
        raw_label = _ask("New label", default=cfg.get("label", ""))
        new_label = _validate_label(raw_label)

        if new_label == cfg.get("label", ""):
            _info("Label unchanged.")
            return

        backup = _backup_cfg(account_dir)
        if backup:
            _info(f"Backup created: {backup.name}")

        cfg["label"] = new_label
        if _write_cfg(account_dir, cfg):
            if new_label:
                _ok(f"Label updated to: {new_label}")
            else:
                _ok("Label removed.")
        return

    if choice == "1":
        # Full re-entry
        print()
        _info("Current values will be shown as defaults. Press Enter to keep them.")
        print()

        new_cfg = _collect_info(idx, existing=cfg)
        if new_cfg is None:
            return

        # Check if anything actually changed
        if all(new_cfg.get(k) == cfg.get(k) for k in new_cfg):
            _info("No changes detected.")
            return

        # Warn if credentials changed (session will need re-login)
        credentials_changed = (
            new_cfg["api_id"] != cfg.get("api_id")
            or new_cfg["api_hash"] != cfg.get("api_hash")
        )
        if credentials_changed:
            _warn(
                "Changing api_id or api_hash will invalidate the existing session. "
                "You will need to re-login (option 2) afterwards."
            )
            # A string session's auth key is tied to the api_id/api_hash it
            # was created with — drop the now-stale one instead of leaving
            # an invalid value sitting in account.json.
            new_cfg["session_string"] = ""
        else:
            # _collect_info() only returns api_id/api_hash/phone/label; it
            # doesn't know about session_string. Since credentials didn't
            # change, carry the existing string session forward instead
            # of silently dropping it when this dict overwrites the file.
            existing_session_string = cfg.get("session_string", "")
            if existing_session_string:
                new_cfg["session_string"] = existing_session_string

        backup = _backup_cfg(account_dir)
        if backup:
            _info(f"Backup created: {backup.name}")

        if _write_cfg(account_dir, new_cfg):
            _ok(f"Account #{idx} updated.")
            if new_cfg.get("label"):
                _ok(f"Label: {new_cfg['label']}")
            if credentials_changed:
                _info("Run option 2 (re-login) to create a new session.")
        return

    _err(f"Unknown choice: '{choice}'.")


async def _action_remove() -> None:
    """Option 4: Permanently remove an account folder."""
    _print_header("Remove Account")
    dirs = _list_accounts(verbose=True)
    if not dirs:
        return

    raw = _ask("Account number to remove")
    if not raw.isdigit():
        _err("Invalid account number.")
        return

    idx = int(raw)
    account_dir = ACCOUNTS_DIR / str(idx)
    if not account_dir.exists():
        _err(f"accounts/{idx}/ does not exist.")
        return

    cfg = _read_cfg(account_dir)
    label_or_phone = (cfg.get("label") or cfg.get("phone", "unknown")) if cfg else "unknown"
    print()
    _warn(f"You are about to PERMANENTLY delete account #{idx} ({label_or_phone}).")
    _warn("This will remove the session file, settings, and all account data.")
    print()

    confirm_text = _ask(f"Type the account number '{idx}' to confirm")
    if confirm_text != str(idx):
        _info("Removal cancelled.")
        return

    try:
        shutil.rmtree(account_dir)
        _ok(f"Account #{idx} removed.")
    except PermissionError:
        _err(
            "Could not remove folder — some files are locked. "
            "Make sure main.py is not running for this account."
        )
    except OSError as exc:
        _err(f"Failed to remove accounts/{idx}/: {exc}")


async def _action_list() -> None:
    """Option 5: Display all accounts."""
    _print_header("All Accounts")
    _list_accounts(verbose=True)


async def _action_verify() -> None:
    """Option 6: Verify if a session file is still valid."""
    _print_header("Verify Session")
    dirs = _list_accounts(verbose=True)
    if not dirs:
        return

    raw = _ask("Account number to verify")
    if not raw.isdigit():
        _err("Invalid account number.")
        return

    idx = int(raw)
    account_dir = ACCOUNTS_DIR / str(idx)
    cfg = _read_cfg(account_dir)
    if cfg is None:
        _err(f"accounts/{idx}/account.json not found or unreadable.")
        return

    if not _has_file_session(account_dir) and not _has_string_session(cfg):
        _err(f"No session (file or string) found for account #{idx}. Use option 2 to login.")
        return

    try:
        from telethon import errors as tl_errors
    except ImportError:
        _err("telethon is not installed. Run: pip install telethon")
        return

    label_or_phone = cfg.get("label") or cfg.get("phone", "?")
    sess_tag, _has_any = _session_status(account_dir, cfg)
    _info(f"Verifying session for account #{idx} ({label_or_phone}) — source: {sess_tag}...")

    client = _build_client(cfg, account_dir)

    try:
        await asyncio.wait_for(client.connect(), timeout=_CONNECT_TIMEOUT)

        try:
            authorized = await asyncio.wait_for(
                client.is_user_authorized(), timeout=_GET_ME_TIMEOUT
            )
        except TimeoutError:
            _err("Authorization check timed out.")
            return

        if not authorized:
            _warn("Session exists but is not authorized. Use option 2 to re-login.")
            return

        try:
            me = await asyncio.wait_for(client.get_me(), timeout=_GET_ME_TIMEOUT)
            _ok(f"Session valid — {me.first_name} (ID: {me.id})")
            _ok(f"Username: @{me.username or 'N/A'}")
            if cfg.get("label"):
                _ok(f"Label: {cfg['label']}")
        except TimeoutError:
            _warn("Could not fetch user info (timed out), but session is authorized.")
        except Exception as exc:
            _warn(f"Session authorized but couldn't fetch info: {exc}")

    except tl_errors.AuthKeyError:
        _err("Session auth key is invalid. Use option 2 to re-login.")
    except TimeoutError:
        _err(f"Connection timed out after {_CONNECT_TIMEOUT}s.")
    except Exception as exc:
        _err(f"Verification failed: {exc}")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


# ── Main Menu ─────────────────────────────────────────────────────────────────

async def _main() -> None:
    _print_header(f"Multi-Account Userbot — Account Manager (v{__version__}+)")

    while True:
        print(f"  {C.BOLD}Options:{C.RESET}")
        print(f"    {C.WHITE}1{C.RESET}  Add new account (full login or string session)")
        print(f"    {C.WHITE}2{C.RESET}  Re-login / recreate session")
        print(f"    {C.WHITE}3{C.RESET}  Edit account details (incl. label)")
        print(f"    {C.WHITE}4{C.RESET}  Remove account")
        print(f"    {C.WHITE}5{C.RESET}  List accounts")
        print(f"    {C.WHITE}6{C.RESET}  Verify session")
        print(f"    {C.WHITE}7{C.RESET}  Exit")
        print()

        try:
            choice = _ask("Choice", default="1")
        except (KeyboardInterrupt, EOFError):
            print()
            _info("Goodbye.")
            break

        print()

        try:
            if choice == "1":
                await _action_add()
            elif choice == "2":
                await _action_relogin()
            elif choice == "3":
                await _action_edit()
            elif choice == "4":
                await _action_remove()
            elif choice == "5":
                await _action_list()
            elif choice == "6":
                await _action_verify()
            elif choice in ("7", "q", "exit", "quit"):
                _info("Goodbye.")
                break
            else:
                _err(f"Unknown option: '{choice}'. Enter 1–7.")
        except (KeyboardInterrupt, EOFError):
            print()
            _warn("Operation cancelled.")

        print()
        try:
            if not _confirm("Return to main menu?", default=True):
                _info("Goodbye.")
                break
        except (KeyboardInterrupt, EOFError):
            break


def main() -> None:
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}  Interrupted.{C.RESET}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
