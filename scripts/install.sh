#!/usr/bin/env bash
#
# HiLite installer.
#
# Installs the `hilite` command globally (via `uv tool install`) so it can be
# run from anywhere, like `claude`. macOS-first; HiLite reads credentials from
# the macOS keychain.
#
# Usage:
#   ./scripts/install.sh [options]
#   curl -fsSL <raw-url>/scripts/install.sh | bash
#
# Options:
#   --no-mcp          Install without the optional MCP extra.
#   --no-editable     Pin the install instead of editable (default: editable).
#   --api-key KEY     Store KEY in the macOS keychain (service "hilite").
#   --prompt-key      Prompt for an API key and store it in the keychain.
#   --dir PATH        Where to clone the repo if not run from a checkout
#                     (default: ~/.hilite/src).
#   -h, --help        Show this help and exit.
#
# Env:
#   HILITE_REPO_URL   Git URL to clone when not run from a checkout.

set -euo pipefail

# --- configuration ----------------------------------------------------------
REPO_URL="${HILITE_REPO_URL:-https://github.com/kevin36524/hilite.git}"
KEYCHAIN_SERVICE="hilite"

INSTALL_MCP=1
EDITABLE=1
API_KEY=""
PROMPT_KEY=0
CLONE_DIR="${HOME}/.hilite/src"

# --- logging ----------------------------------------------------------------
if [ -t 1 ]; then
  C_RESET="\033[0m"; C_INFO="\033[36m"; C_OK="\033[32m"
  C_WARN="\033[33m"; C_ERR="\033[31m"; C_BOLD="\033[1m"; C_MAGENTA="\033[35m"
else
  C_RESET=""; C_INFO=""; C_OK=""; C_WARN=""; C_ERR=""; C_BOLD=""; C_MAGENTA=""
fi
log_info()    { printf "${C_INFO}→${C_RESET} %s\n" "$*"; }
log_success() { printf "${C_OK}✓${C_RESET} %s\n" "$*"; }
log_warn()    { printf "${C_WARN}⚠${C_RESET} %s\n" "$*"; }
log_error()   { printf "${C_ERR}✗${C_RESET} %s\n" "$*" >&2; }
die()         { log_error "$*"; exit 1; }

banner() {
  printf "${C_MAGENTA}${C_BOLD}"
  printf "┌───────────────────────────────┐\n"
  printf "│         HiLite installer      │\n"
  printf "└───────────────────────────────┘\n"
  printf "${C_RESET}\n"
}

usage() { sed -n '3,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

# --- arg parsing ------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --no-mcp)      INSTALL_MCP=0 ;;
    --no-editable) EDITABLE=0 ;;
    --api-key)     API_KEY="${2:-}"; shift ;;
    --prompt-key)  PROMPT_KEY=1 ;;
    --dir)         CLONE_DIR="${2:-}"; shift ;;
    -h|--help)     usage ;;
    *)             die "Unknown option: $1 (use --help)" ;;
  esac
  shift
done

banner

# --- 1. OS check ------------------------------------------------------------
OS="$(uname -s)"
if [ "$OS" = "Darwin" ]; then
  log_success "macOS detected."
else
  log_warn "Non-macOS ($OS): HiLite installs, but keychain credential lookup is"
  log_warn "macOS-only — you'll need to set ANTHROPIC_API_KEY yourself."
fi

# --- 2. ensure uv -----------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  log_success "uv found ($(uv --version))."
else
  log_info "uv not found — installing via the official installer…"
  if command -v brew >/dev/null 2>&1; then
    brew install uv
  else
    curl -fsSL https://astral.sh/uv/install.sh | sh
  fi
  # The installer puts uv in ~/.local/bin or ~/.cargo/bin; make it visible now.
  export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
  command -v uv >/dev/null 2>&1 || die "uv install failed; see https://docs.astral.sh/uv/"
  log_success "uv installed ($(uv --version))."
fi

# --- 3. locate the repo (local checkout or clone) ---------------------------
REPO_ROOT=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  candidate="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  [ -f "${candidate}/pyproject.toml" ] && REPO_ROOT="$candidate"
fi

if [ -z "$REPO_ROOT" ]; then
  log_info "Not run from a checkout — cloning ${REPO_URL} into ${CLONE_DIR}…"
  if [ -d "${CLONE_DIR}/.git" ]; then
    git -C "$CLONE_DIR" pull --ff-only
  else
    mkdir -p "$(dirname "$CLONE_DIR")"
    git clone "$REPO_URL" "$CLONE_DIR"
  fi
  REPO_ROOT="$CLONE_DIR"
fi
log_success "Using repo at ${REPO_ROOT}."

# --- 4. install the hilite tool ---------------------------------------------
TARGET="."
[ "$INSTALL_MCP" -eq 1 ] && TARGET=".[mcp]"
INSTALL_ARGS=(tool install --force)
[ "$EDITABLE" -eq 1 ] && INSTALL_ARGS+=(--editable)
INSTALL_ARGS+=("$TARGET")

log_info "Installing hilite ($([ "$EDITABLE" -eq 1 ] && echo editable || echo pinned)${INSTALL_MCP:+, mcp=$INSTALL_MCP})…"
( cd "$REPO_ROOT" && uv "${INSTALL_ARGS[@]}" )

# Ensure the tool bin dir is on PATH in the user's shell config.
uv tool update-shell >/dev/null 2>&1 || true

# --- 5. optional: store API key in the keychain (macOS) ---------------------
if [ "$PROMPT_KEY" -eq 1 ] && [ -z "$API_KEY" ]; then
  if [ -r /dev/tty ]; then
    printf "Paste your Anthropic API key (sk-ant-…): " > /dev/tty
    read -rs API_KEY < /dev/tty; printf "\n" > /dev/tty
  else
    log_warn "--prompt-key requires a terminal; skipping."
  fi
fi

if [ -n "$API_KEY" ]; then
  if [ "$OS" = "Darwin" ]; then
    security add-generic-password -U -s "$KEYCHAIN_SERVICE" -a "$USER" -w "$API_KEY"
    log_success "Stored API key in keychain (service '${KEYCHAIN_SERVICE}')."
  else
    log_warn "Keychain is macOS-only; set ANTHROPIC_API_KEY instead."
  fi
fi

# --- 6. verify --------------------------------------------------------------
if command -v hilite >/dev/null 2>&1; then
  hilite --list-sessions >/dev/null 2>&1 \
    && log_success "hilite installed at $(command -v hilite) and runs." \
    || log_warn "hilite installed at $(command -v hilite) but a test invocation failed."
else
  log_warn "hilite is installed but not on PATH yet."
  log_warn "Open a new terminal, or add the uv tool bin dir to PATH:"
  log_warn "  export PATH=\"\$($([ -x "$(command -v uv)" ] && echo uv) tool dir --bin 2>/dev/null || echo \$HOME/.local/bin):\$PATH\""
fi

# --- 7. summary -------------------------------------------------------------
printf "\n${C_BOLD}Done.${C_RESET}\n"
printf "  Run:        ${C_INFO}hilite -p \"List the files here\"${C_RESET}\n"
printf "  Credentials: ANTHROPIC_API_KEY env, or keychain service '${KEYCHAIN_SERVICE}'\n"
printf "  Update:     re-run this script, or ${C_INFO}uv tool install --editable \"${TARGET}\" --force${C_RESET}\n"
printf "  Uninstall:  ${C_INFO}uv tool uninstall hilite${C_RESET}\n"
