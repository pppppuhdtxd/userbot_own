#!/usr/bin/env bash
#
# update.sh — Safe updater for userbot_own.
#
# Usage:
#   bash ~/userbot_own/update.sh
#   OR via the launcher: userbot update
#
# What it does:
#   - git pull (fast-forward only)
#   - pip install -r requirements.txt (upgrades packages as needed)
#
# What it NEVER touches:
#   - accounts/      (Telegram sessions + credentials)
#   - .env           (user configuration)
#   - data/          (logs, per-account runtime settings)

set -euo pipefail

INSTALL_DIR="${HOME}/userbot_own"
VENV_PIP="${INSTALL_DIR}/.venv/bin/pip"
VENV_PY="${INSTALL_DIR}/.venv/bin/python"

G="\033[32m"; Y="\033[33m"; E="\033[31m"; R="\033[0m"; C="\033[36m"
log()  { printf "%b[update]%b %s\n" "$C" "$R" "$1"; }
ok()   { printf "%b[  ok  ]%b %s\n" "$G" "$R" "$1"; }
warn() { printf "%b[ warn ]%b %s\n" "$Y" "$R" "$1"; }
die()  { printf "%b[ fail ]%b %s\n" "$E" "$R" "$1"; exit 1; }

[ -d "${INSTALL_DIR}/.git" ] \
  || die "Installation not found at ${INSTALL_DIR}. Run the installer first:
  curl -fsSL https://raw.githubusercontent.com/pppppuhdtxd/userbot_own/main/install.sh | bash"

[ -x "$VENV_PY" ] \
  || die "Virtual environment not found at ${INSTALL_DIR}/.venv. Re-run the installer."

log "Pulling latest code..."
git -C "$INSTALL_DIR" pull --ff-only \
  || warn "git pull failed or had local changes; skipping code update."
ok "Code up to date."

log "Upgrading dependencies..."
"$VENV_PY" -m pip install --upgrade pip >/dev/null \
  || warn "pip self-upgrade failed; continuing."

REQUIREMENTS_FILE="${INSTALL_DIR}/requirements.txt"
if [ -f "$REQUIREMENTS_FILE" ]; then
  "$VENV_PIP" install -r "$REQUIREMENTS_FILE" \
    || die "Dependency upgrade failed. See error above."
  ok "Dependencies up to date."
else
  warn "No requirements.txt found; skipping dependency upgrade."
fi

ok "Update complete. Run 'userbot' to start."
