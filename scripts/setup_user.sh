#!/bin/bash
#
# Minimal User Environment Setup (unguided) — per-user half of the machine
# provisioning for ai-circus-framework; run scripts/setup_sudo.sh first.
#
# Usage (from the repo root):
#   ./scripts/setup_user.sh          # then: source ~/.bashrc   (recommended)
#   source scripts/setup_user.sh     # also works: safe to source, never exits your shell
#
# Description:
#   Configures a minimal personal development environment for a user on Ubuntu.
#   The script is designed to be idempotent and safe to re-run.
#
# Main Features:
#   • Ensures ~/.local/bin is in the PATH
#   • Configures basic Git settings (default branch; warns if user.name/email are unset)
#   • Sets a simple, colored bash prompt
#   • Adds a convenient alias: `setup` → re-run this script (absolute path)
#   • Installs or updates NVM (Node Version Manager) and ensures Node.js 22 (LTS)
#   • Installs/updates `uv` CLI tool
#
# Notes:
#   - Everything persistent goes through ~/.bashrc, so executing it is enough;
#     sourcing additionally updates the current shell's PATH.
#   - Requires `curl` and `git` to be installed.
#   - Adds blocks to ~/.bashrc only if missing (idempotent).
#   - Logs actions with color-coded feedback.

# NOTE: no `set -e` here on purpose — this file may be sourced, and `set -e`/`exit`
# inside a sourced script apply to the caller's interactive shell (they close the
# terminal). Each step reports its own failure and main() stops on the first one.
SOURCED=false
[[ ${BASH_SOURCE[0]} != "$0" ]] && SOURCED=true
NODE_VERSION=22   # matches ai-circus-framework's CI (.github/workflows/ci.yml); Node 20 is EOL

BASHRC="$HOME/.bashrc"
LOCAL_BIN="$HOME/.local/bin"
NVM_DIR="$HOME/.nvm"

GREEN='\033[0;32m'; BLUE='\033[0;34m'; RED='\033[0;31m'; NC='\033[0m'
log(){ echo -e "${GREEN}✓ $1${NC}"; }
info(){ echo -e "${BLUE}ℹ $1${NC}"; }
err(){ echo -e "${RED}✗ $1${NC}" >&2; return 1; }

# Append a block to .bashrc if missing
append() {
    local tag="$1" text="$2"
    grep -qF "$tag" "$BASHRC" 2>/dev/null || { echo -e "$tag\n$text" >> "$BASHRC"; log "Added: $tag"; }
}

# Ensure ~/.local/bin is in PATH
ensure_path() {
    append "# PATH from setup_user" 'export PATH="$HOME/.local/bin:$PATH"'
    export PATH="$LOCAL_BIN:$PATH"
    log "PATH ensured."
}

# Configure Git basics automatically if missing
configure_git() {
    command -v git &>/dev/null || err "Git missing."

    git config --global init.defaultBranch main

    if ! git config --global user.name >/dev/null || ! git config --global user.email >/dev/null; then
        echo -e "${RED}⚠ git identity not set — commits would be misattributed. Run:${NC}"
        echo '    git config --global user.name  "Your Name"'
        echo '    git config --global user.email "you@example.com"'
    fi

    log "Git configured."
}

# Simple prompt
set_prompt() {
    append "# prompt from setup_user" 'export PS1="\[\e[32m\]\u@\h \[\e[34m\]\w\[\e[0m\]\$ "'
    log "Prompt set."
}

# Add alias
add_alias() {
    local self; self="$(realpath "${BASH_SOURCE[0]}")"
    append "# alias from setup_user" "alias setup=\"source $self\""
    log "Alias added."
}

# Install/update nvm + Node $NODE_VERSION
install_nvm() {
    local latest
    latest=$(curl -fsS https://api.github.com/repos/nvm-sh/nvm/releases/latest | grep tag_name | cut -d'"' -f4)
    [[ -n $latest ]] || { err "could not resolve latest nvm release (GitHub API rate-limited or offline)"; return 1; }

    # Install/update nvm (installer log kept for diagnosis instead of discarded)
    curl -fsSo- "https://raw.githubusercontent.com/nvm-sh/nvm/$latest/install.sh" | bash >"$HOME/.nvm-install.log" 2>&1 \
        || { err "nvm installer failed — see ~/.nvm-install.log"; return 1; }
    . "$NVM_DIR/nvm.sh" || { err "nvm.sh missing after install"; return 1; }

    nvm install "$NODE_VERSION" --latest-npm >/dev/null 2>&1 || { err "nvm install $NODE_VERSION failed"; return 1; }
    nvm alias default "$NODE_VERSION" >/dev/null
    log "nvm + Node $NODE_VERSION ready ($(node --version))."
}

# Install/update uv
install_uv() {
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || { err "uv installer failed"; return 1; }
    log "uv installed/updated ($("$LOCAL_BIN/uv" --version))."
}

main() {
    command -v curl &>/dev/null || { err "curl missing."; return 1; }

    log "Starting user setup..."
    ensure_path    &&
    configure_git  &&
    set_prompt     &&
    add_alias      &&
    install_nvm    &&
    install_uv     || return 1

    log "Setup complete. Run: source ~/.bashrc"
}

if main; then
    :
elif $SOURCED; then
    return 1
else
    exit 1
fi
