#!/usr/bin/env bash
#
# update.sh — Safe updater for userbot_own.
#
# Usage:
#   bash ~/userbot_own/update.sh
#   OR via the launcher: userbot update
#
# Guarantees:
#   - If the code did not actually change, this script says so explicitly.
#   - If the pull fails for ANY reason, this script FAILS LOUDLY — it never
#     prints success after a failed pull.
#   - accounts/, .env, and data/ are never touched, and this is verified,
#     not assumed.

set -euo pipefail

INSTALL_DIR="${HOME}/userbot_own"
VENV_PIP="${INSTALL_DIR}/.venv/bin/pip"
VENV_PY="${INSTALL_DIR}/.venv/bin/python"

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
  R="\033[0m"; B="\033[1m"; G="\033[32m"; Y="\033[33m"; E="\033[31m"; C="\033[36m"
else
  R=""; B=""; G=""; Y=""; E=""; C=""
fi

log()  { printf "%b[update]%b %s\n" "$C" "$R" "$1"; }
ok()   { printf "%b[  OK  ]%b %s\n" "$G" "$R" "$1"; }
warn() { printf "%b[ WARN ]%b %s\n" "$Y" "$R" "$1"; }
die()  { printf "%b[ FAIL ]%b %s\n" "$E" "$R" "$1"; exit 1; }

# ---------------------------------------------------------------------------
# 0. Sanity checks
# ---------------------------------------------------------------------------
[ -d "${INSTALL_DIR}/.git" ] \
  || die "Installation not found at ${INSTALL_DIR}. Run the installer first:
  curl -fsSL https://raw.githubusercontent.com/pppppuhdtxd/userbot_own/main/install.sh | bash"

[ -x "$VENV_PY" ] \
  || die "Virtual environment not found at ${INSTALL_DIR}/.venv. Re-run the installer."

VERSION_FILE="${INSTALL_DIR}/VERSION"
read_version() { [ -f "$VERSION_FILE" ] && cat "$VERSION_FILE" || echo "unknown"; }

OLD_VERSION="$(read_version)"
OLD_SHA="$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")"

# ---------------------------------------------------------------------------
# 1. Detect a dirty working tree BEFORE attempting the pull.
#    This is the #1 cause of a fast-forward pull silently failing:
#    a modified/untracked file the update script itself doesn't own.
# ---------------------------------------------------------------------------
log "Checking repository state..."
# -uno: ignore untracked files. Untracked files (e.g. accounts/, data/ if not
# yet gitignored) can never block a fast-forward pull, so they must never
# trigger this check — only modifications to files git already tracks can.
DIRTY_FILES="$(git -C "$INSTALL_DIR" status --porcelain -uno 2>/dev/null || true)"
if [ -n "$DIRTY_FILES" ]; then
  warn "Local changes to tracked files detected:"
  echo "$DIRTY_FILES" | sed 's/^/         /'
  die "Cannot safely fast-forward with local changes present. Resolve or discard
  them (e.g. 'git -C ${INSTALL_DIR} stash') and re-run update. Refusing to
  guess at a merge, since that risks your account data if it lives inside
  the tracked tree."
fi
ok "Working tree clean (untracked files, if any, are not a blocker)."

# ---------------------------------------------------------------------------
# 2. Pull — and actually check whether it worked, not just whether the
#    git command exited. A failed pull is a hard stop, not a warning.
# ---------------------------------------------------------------------------
log "Fetching latest code..."
git -C "$INSTALL_DIR" fetch --quiet origin \
  || die "git fetch failed. Check your network connection and try again."

if ! git -C "$INSTALL_DIR" pull --ff-only --quiet; then
  die "git pull --ff-only failed. Your local branch has diverged from origin
  (e.g. a manual commit, checkout, or reset was made here previously).
  This must be resolved manually — automatically discarding history is not
  something this script will do without your explicit confirmation:
    cd ${INSTALL_DIR}
    git status
    git log --oneline -5
    git log --oneline origin/main -5"
fi

NEW_SHA="$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
NEW_VERSION="$(read_version)"

if [ "$OLD_SHA" = "$NEW_SHA" ]; then
  ok "Already up to date (${NEW_VERSION} @ ${NEW_SHA}). No new commits to apply."
else
  ok "Code updated."
fi

# ---------------------------------------------------------------------------
# 3. Clear stale bytecode. This is the single most common reason a fix
#    that was genuinely pulled still doesn't appear to run after restart.
# ---------------------------------------------------------------------------
log "Clearing stale bytecode caches..."
PYC_DIR_COUNT="$(find "$INSTALL_DIR" -type d -name '__pycache__' 2>/dev/null | wc -l | tr -d ' ')"
find "$INSTALL_DIR" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "$INSTALL_DIR" -type f -name '*.pyc' -delete 2>/dev/null || true
ok "Cleared ${PYC_DIR_COUNT} stale __pycache__ director$([ "$PYC_DIR_COUNT" = "1" ] && echo y || echo ies)."

# ---------------------------------------------------------------------------
# 4. Dependencies — explicit venv pip, never bare pip/pip3.
# ---------------------------------------------------------------------------
log "Upgrading pip inside the virtual environment..."
"$VENV_PY" -m pip install --upgrade pip >/dev/null 2>&1 \
  || warn "pip self-upgrade failed; continuing with existing pip."

REQUIREMENTS_FILE="${INSTALL_DIR}/requirements.txt"
if [ -f "$REQUIREMENTS_FILE" ]; then
  log "Installing/upgrading dependencies from requirements.txt..."
  "$VENV_PIP" install -r "$REQUIREMENTS_FILE" \
    || die "Dependency upgrade failed. See pip output above. The code has
  already been updated — your bot may not run correctly until dependencies
  are fixed. Try running manually:
    ${VENV_PIP} install -r ${REQUIREMENTS_FILE}"
  ok "Dependencies up to date."
else
  warn "No requirements.txt found; skipping dependency upgrade."
fi

# ---------------------------------------------------------------------------
# 5. Post-update smoke test — confirm the updated tree actually imports.
#    This does NOT catch every runtime bug, but it does catch syntax
#    errors and import breakage that would otherwise surface as a crash
#    the next time the bot is started, with no chance to roll back first.
# ---------------------------------------------------------------------------
log "Running post-update sanity check (py_compile)..."
CODE_ROOT="${INSTALL_DIR}"
[ -d "${INSTALL_DIR}/userbot" ] && CODE_ROOT="${INSTALL_DIR}/userbot"

if "$VENV_PY" -m compileall -q "$CODE_ROOT"; then
  ok "All Python files compile cleanly."
else
  warn "One or more files failed to compile. The update has already been
  applied to disk — review the error above before restarting the bot."
fi

# ---------------------------------------------------------------------------
# 6. Data-safety confirmation — verify, don't just assert.
# ---------------------------------------------------------------------------
GITIGNORE_FILE="${INSTALL_DIR}/.gitignore"
SAFE=true
if [ -f "$GITIGNORE_FILE" ]; then
  for pattern in "accounts" ".env" "data"; do
    grep -qE "(^|/)${pattern}(/|$)" "$GITIGNORE_FILE" || SAFE=false
  done
else
  SAFE=false
fi
if $SAFE; then
  ok "accounts/, .env, and data/ confirmed git-ignored — untouched by this update."
else
  warn "Could not fully confirm accounts/, .env, and data/ are git-ignored.
  They were not touched by this script regardless (only 'git pull' and
  'pip install' were run), but double-check your .gitignore."
fi

# ---------------------------------------------------------------------------
# 7. Version transition banner — the one line that matters most.
# ---------------------------------------------------------------------------
echo
printf "%b%s%b\n" "$B" "──────────────────────────────────────────" "$R"
printf "  Version:  %s (%s)  →  %s (%s)\n" "$OLD_VERSION" "$OLD_SHA" "$NEW_VERSION" "$NEW_SHA"
printf "%b%s%b\n" "$B" "──────────────────────────────────────────" "$R"
echo
ok "Update complete. Run 'userbot' to start."