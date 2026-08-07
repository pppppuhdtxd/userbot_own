#!/usr/bin/env bash
#
# install.sh — One-shot installer / updater for the userbot_own project.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/pppppuhdtxd/userbot_own/main/install.sh | bash
#
# Idempotent: re-running updates code and deps without touching accounts/, .env, or data/.
# Any pull failure is a hard stop — this script never reports success after
# a failed or skipped update.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration — all paths derived from these two constants
# ---------------------------------------------------------------------------
REPO_URL="https://github.com/pppppuhdtxd/userbot_own.git"
REPO_NAME="userbot_own"
INSTALL_DIR="${HOME}/${REPO_NAME}"          # ~/userbot_own — fixed, never guessed
LAUNCHER_NAME="userbot"

# Colors
if [ -t 1 ]; then
  R="\033[0m"; B="\033[1m"; G="\033[32m"; Y="\033[33m"; E="\033[31m"; C="\033[36m"
else
  R=""; B=""; G=""; Y=""; E=""; C=""
fi

log()  { printf "%b[install]%b %s\n" "$C" "$R" "$1"; }
ok()   { printf "%b[ OK ]%b %s\n"    "$G" "$R" "$1"; }
warn() { printf "%b[WARN]%b %s\n"    "$Y" "$R" "$1"; }
die()  { printf "%b[FAIL]%b %s\n"    "$E" "$R" "$1"; exit 1; }

# ---------------------------------------------------------------------------
# 1. Platform detection
# ---------------------------------------------------------------------------
IS_TERMUX=false
if [ -n "${PREFIX:-}" ] && [[ "$PREFIX" == *"com.termux"* ]] && command -v pkg >/dev/null 2>&1; then
  IS_TERMUX=true
fi

if $IS_TERMUX; then
  PLATFORM="termux"
  BIN_DIR="${PREFIX}/bin"       # always on PATH in Termux
else
  PLATFORM="linux"
  BIN_DIR="${HOME}/.local/bin"
fi

log "Detected platform: ${B}${PLATFORM}${R}"

# ---------------------------------------------------------------------------
# 2. Install Python + git
# ---------------------------------------------------------------------------
install_python() {
  if $IS_TERMUX; then
    log "Updating Termux package index..."
    pkg update -y >/dev/null 2>&1 || warn "pkg update had issues; continuing."
    log "Ensuring Python and git are installed (Termux)..."
    pkg install -y git python || die "Failed to install required Termux packages."
  else
    if command -v apt-get >/dev/null 2>&1; then
      log "Ensuring Python and venv support are installed (Debian/Ubuntu)..."
      SUDO=""
      command -v sudo >/dev/null 2>&1 && SUDO="sudo"
      $SUDO apt-get update -y || warn "apt-get update had issues; continuing."
      $SUDO apt-get install -y git python3 python3-venv python3-pip \
        || die "Failed to install packages via apt-get."
    else
      warn "Non-Debian/Ubuntu Linux: assuming python3/pip3/git already present."
      command -v python3 >/dev/null 2>&1 || die "python3 not found. Install Python 3.11+ and re-run."
      command -v git    >/dev/null 2>&1 || die "git not found. Install git and re-run."
    fi
  fi
}
install_python
ok "Python ready."

# ---------------------------------------------------------------------------
# Reactive build-toolchain installer (only called if pip fails)
# ---------------------------------------------------------------------------
install_build_toolchain() {
  if $IS_TERMUX; then
    log "Installing native build toolchain (Termux) to retry failed dep build..."
    pkg install -y clang make python-dev libffi-dev openssl-dev \
      || { warn "Failed to install Termux build toolchain."; return 1; }
  else
    if command -v apt-get >/dev/null 2>&1; then
      log "Installing native build toolchain (Debian/Ubuntu)..."
      SUDO=""
      command -v sudo >/dev/null 2>&1 && SUDO="sudo"
      $SUDO apt-get install -y build-essential libffi-dev libssl-dev python3-dev \
        || { warn "Failed to install build-essential via apt-get."; return 1; }
    else
      warn "Don't know how to install build toolchain on this distro."
      return 1
    fi
  fi
}

# Resolve python binary
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  die "No python interpreter found on PATH after installation step."
fi

PY_VERSION="$($PYTHON_BIN -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "unknown")"
log "Using interpreter: ${PYTHON_BIN} (Python ${PY_VERSION})"

# v3.0.11: this used to only detect and display PY_VERSION without ever
# checking it against the >=3.11 floor documented in requirements.txt /
# pyproject.toml — an older system python3 would silently create a venv
# that then failed confusingly, later, during `pip install` or at runtime.
# Enforce the floor here instead, with a clear message up front.
if [[ ! "$PY_VERSION" =~ ^[0-9]+\.[0-9]+$ ]]; then
  die "Could not determine ${PYTHON_BIN}'s version. Python 3.11+ is required."
fi
PY_MAJOR="${PY_VERSION%%.*}"
PY_MINOR="${PY_VERSION##*.}"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  die "Python ${PY_VERSION} found (${PYTHON_BIN}), but Python 3.11+ is required.
Install a newer Python (e.g. 'pkg install python' on Termux, or your distro's
python3.11+ package) and re-run this script."
fi

# ---------------------------------------------------------------------------
# 3. Clone or update the repository
#    A failed pull is a HARD STOP, never a silent warn-and-continue.
# ---------------------------------------------------------------------------
VERSION_FILE="${INSTALL_DIR}/VERSION"
read_version() { [ -f "$VERSION_FILE" ] && cat "$VERSION_FILE" || echo "unknown"; }

OLD_VERSION="unknown"
OLD_SHA="none (fresh install)"

if [ -d "${INSTALL_DIR}/.git" ]; then
  OLD_VERSION="$(read_version)"
  OLD_SHA="$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")"

  log "Existing installation found at ${INSTALL_DIR}. Checking for local changes..."
  # -uno: ignore untracked files. accounts/ and data/ are expected to be
  # untracked (or gitignored) and must never block an update on that basis
  # alone — only modifications to already-tracked files can break a
  # fast-forward pull.
  DIRTY_FILES="$(git -C "$INSTALL_DIR" status --porcelain -uno 2>/dev/null || true)"
  if [ -n "$DIRTY_FILES" ]; then
    warn "Local changes to tracked files detected:"
    echo "$DIRTY_FILES" | sed 's/^/       /'
    die "Refusing to pull over local changes. Resolve them first, e.g.:
    git -C ${INSTALL_DIR} stash
  then re-run this installer."
  fi

  log "Pulling latest code..."
  git -C "${INSTALL_DIR}" fetch --quiet origin \
    || die "git fetch failed. Check your network connection and try again."
  BRANCH="$(git -C "${INSTALL_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
  git -C "${INSTALL_DIR}" reset --hard "origin/${BRANCH}" --quiet \
    || die "git reset --hard origin/${BRANCH} failed. Check the error above."
elif [ -e "${INSTALL_DIR}" ]; then
  die "${INSTALL_DIR} exists but is not a git repo. Move it aside and re-run."
else
  log "Cloning repository into ${INSTALL_DIR}..."
  git clone "${REPO_URL}" "${INSTALL_DIR}" || die "git clone failed."
fi

NEW_VERSION="$(read_version)"
NEW_SHA="$(git -C "${INSTALL_DIR}" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
ok "Repository ready at ${INSTALL_DIR}"

# ---------------------------------------------------------------------------
# 4. Guard user data (accounts/, .env, data/) — never touched
# ---------------------------------------------------------------------------
for p in \
  "${INSTALL_DIR}/userbot_own/accounts" \
  "${INSTALL_DIR}/userbot_own/.env" \
  "${INSTALL_DIR}/userbot_own/data" \
  "${INSTALL_DIR}/userbot/accounts" \
  "${INSTALL_DIR}/userbot/.env" \
  "${INSTALL_DIR}/userbot/data"
do
  [ -e "$p" ] && log "Preserving existing user data (untouched): ${p}"
done

# ---------------------------------------------------------------------------
# 5. Clear stale bytecode before (re)installing dependencies.
#    Prevents a rare-but-real class of bug where an old .pyc from a prior
#    install lingers alongside freshly pulled .py source.
# ---------------------------------------------------------------------------
log "Clearing stale bytecode caches..."
find "$INSTALL_DIR" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "$INSTALL_DIR" -type f -name '*.pyc' -delete 2>/dev/null || true
ok "Bytecode caches cleared."

# ---------------------------------------------------------------------------
# 6. Create venv and install dependencies
# ---------------------------------------------------------------------------
VENV_DIR="${INSTALL_DIR}/.venv"
if [ ! -x "${VENV_DIR}/bin/python" ]; then
  log "Creating virtual environment at ${VENV_DIR}..."
  "$PYTHON_BIN" -m venv "${VENV_DIR}" \
    || die "Failed to create venv. On Debian/Ubuntu: sudo apt-get install python3-venv"
else
  log "Virtual environment already exists, reusing it."
fi

VENV_PY="${VENV_DIR}/bin/python"
VENV_PIP="${VENV_DIR}/bin/pip"

log "Upgrading pip inside the virtual environment..."
"$VENV_PY" -m pip install --upgrade pip >/dev/null 2>&1 \
  || warn "pip self-upgrade failed; continuing with existing pip."

REQUIREMENTS_FILE="${INSTALL_DIR}/requirements.txt"
if [ -f "$REQUIREMENTS_FILE" ]; then
  log "Installing dependencies from requirements.txt..."
  if "$VENV_PIP" install -r "$REQUIREMENTS_FILE"; then
    ok "Dependencies installed."
  else
    warn "Dependency install failed — trying native build toolchain fallback..."
    if install_build_toolchain && "$VENV_PIP" install -r "$REQUIREMENTS_FILE"; then
      ok "Dependencies installed after installing the build toolchain."
    else
      die "Dependency installation failed even after build toolchain. See error above."
    fi
  fi
else
  warn "No requirements.txt found at ${REQUIREMENTS_FILE}; skipping."
fi

# ---------------------------------------------------------------------------
# 7. Post-install/update sanity check
# ---------------------------------------------------------------------------
log "Running sanity check (py_compile)..."
CODE_ROOT="${INSTALL_DIR}"
[ -d "${INSTALL_DIR}/userbot" ] && CODE_ROOT="${INSTALL_DIR}/userbot"
if "$VENV_PY" -m compileall -q "$CODE_ROOT"; then
  ok "All Python files compile cleanly."
else
  warn "One or more files failed to compile. Review the error above before running the bot."
fi

# ---------------------------------------------------------------------------
# 8. Generate the launcher script directly via heredoc
#    Hardcoded paths — no config file, no guessing. Shows current version
#    in the interactive menu header.
# ---------------------------------------------------------------------------
mkdir -p "$BIN_DIR"
LAUNCHER_DEST="${BIN_DIR}/${LAUNCHER_NAME}"

# Resolve the two entry points (support both old and new repo layouts)
if [ -f "${INSTALL_DIR}/main.py" ]; then
  MAIN_PY="${INSTALL_DIR}/main.py"
  ADD_ACCOUNT_PY="${INSTALL_DIR}/add_account.py"
  RUN_DIR="${INSTALL_DIR}"
elif [ -f "${INSTALL_DIR}/userbot/main.py" ]; then
  MAIN_PY="${INSTALL_DIR}/userbot/main.py"
  ADD_ACCOUNT_PY="${INSTALL_DIR}/userbot/add_account.py"
  RUN_DIR="${INSTALL_DIR}/userbot"
else
  # Fallback — write paths anyway; user will see a clear error at runtime
  MAIN_PY="${INSTALL_DIR}/main.py"
  ADD_ACCOUNT_PY="${INSTALL_DIR}/add_account.py"
  RUN_DIR="${INSTALL_DIR}"
fi

cat > "$LAUNCHER_DEST" << LAUNCHEREOF
#!/usr/bin/env bash
# userbot launcher — generated by install.sh
# Edit install.sh and re-run the installer to regenerate this file.

INSTALL_DIR="${INSTALL_DIR}"
VENV_PY="\${INSTALL_DIR}/.venv/bin/python"
MAIN_PY="${MAIN_PY}"
ADD_ACCOUNT_PY="${ADD_ACCOUNT_PY}"
RUN_DIR="${RUN_DIR}"
VERSION_FILE="\${INSTALL_DIR}/VERSION"

G="\033[32m"; Y="\033[33m"; E="\033[31m"; B="\033[1m"; R="\033[0m"

current_version() {
  [ -f "\$VERSION_FILE" ] && cat "\$VERSION_FILE" || echo "unknown"
}

# Sanity check — clear error instead of a cryptic Python traceback
if [ ! -d "\$INSTALL_DIR" ]; then
  printf "\${E}[fail]\${R} Installation directory not found: %s\n" "\$INSTALL_DIR"
  printf "Re-run the installer:\n  curl -fsSL https://raw.githubusercontent.com/pppppuhdtxd/userbot_own/main/install.sh | bash\n"
  exit 1
fi

if [ ! -x "\$VENV_PY" ]; then
  printf "\${E}[fail]\${R} Virtual environment not found: %s\n" "\$VENV_PY"
  printf "Re-run the installer:\n  curl -fsSL https://raw.githubusercontent.com/pppppuhdtxd/userbot_own/main/install.sh | bash\n"
  exit 1
fi

# --- Dispatch on first argument (optional shortcuts) ---
case "\${1:-}" in
  run)
    cd "\$RUN_DIR"
    exec "\$VENV_PY" "\$MAIN_PY"
    ;;
  add)
    cd "\$RUN_DIR"
    exec "\$VENV_PY" "\$ADD_ACCOUNT_PY"
    ;;
  update)
    # Fast path: run the lightweight updater already on disk (git sync +
    # deps + cache clear), NOT the full installer. Falls back to the full
    # installer only if update.sh is missing (e.g. a very old install).
    if [ -f "\${INSTALL_DIR}/update.sh" ]; then
      exec bash "\${INSTALL_DIR}/update.sh"
    else
      exec bash -c 'curl -fsSL https://raw.githubusercontent.com/pppppuhdtxd/userbot_own/main/install.sh | bash'
    fi
    ;;
  reinstall)
    # Full reinstall: re-checks Python/git/venv from scratch. Slower —
    # use this only if something is actually broken, not for routine updates.
    exec bash -c 'curl -fsSL https://raw.githubusercontent.com/pppppuhdtxd/userbot_own/main/install.sh | bash'
    ;;
  version)
    printf "userbot_own version %s\n" "\$(current_version)"
    exit 0
    ;;
  "")
    # Interactive menu
    printf "\n\${B}=== userbot_own — v%s ===\${R}\n" "\$(current_version)"
    printf "1) Run bot\n"
    printf "2) Manage accounts\n"
    printf "3) Update\n"
    printf "4) Quit\n"
    printf "Select an option: "
    read -r choice
    case "\$choice" in
      1) cd "\$RUN_DIR" && exec "\$VENV_PY" "\$MAIN_PY" ;;
      2) cd "\$RUN_DIR" && exec "\$VENV_PY" "\$ADD_ACCOUNT_PY" ;;
      3)
        if [ -f "\${INSTALL_DIR}/update.sh" ]; then
          exec bash "\${INSTALL_DIR}/update.sh"
        else
          exec bash -c 'curl -fsSL https://raw.githubusercontent.com/pppppuhdtxd/userbot_own/main/install.sh | bash'
        fi
        ;;
      4) exit 0 ;;
      *) printf "Invalid option.\n"; exit 1 ;;
    esac
    ;;
  *)
    printf "Usage: userbot [run|add|update|reinstall|version]\n"
    exit 1
    ;;
esac
LAUNCHEREOF

chmod +x "$LAUNCHER_DEST"
ok "Launcher generated and installed at ${LAUNCHER_DEST}"

# ---------------------------------------------------------------------------
# 9. PATH sanity check (Linux only)
# ---------------------------------------------------------------------------
echo
printf "%b%s%b\n" "$B" "──────────────────────────────────────────" "$R"
printf "  Version:  %s (%s)  →  %s (%s)\n" "$OLD_VERSION" "$OLD_SHA" "$NEW_VERSION" "$NEW_SHA"
printf "%b%s%b\n" "$B" "──────────────────────────────────────────" "$R"
echo

case ":$PATH:" in
  *":$BIN_DIR:"*)
    ok "Install complete! Type '${LAUNCHER_NAME}' to get started."
    ;;
  *)
    warn "Install complete, but ${BIN_DIR} is not currently on your PATH."
    printf '  Add this to ~/.bashrc or ~/.zshrc:\n'
    printf '  \033[1mexport PATH="%s:$PATH"\033[0m\n' "$BIN_DIR"
    printf '  Then open a new terminal and type: %s\n' "$LAUNCHER_NAME"
    ;;
esac