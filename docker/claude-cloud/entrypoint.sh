#!/bin/bash
set -euo pipefail

#
# Claude Cloud Container Entrypoint
#
# Prepares environment from snapshot and optional env vars, then launches Claude in tmux.
#
# Snapshot (mounted at /snapshot/snapshot.tar.gz):
#   Extracts directly to $HOME: .claude/, .claude.json, .happy/, .git-credentials, .gitconfig
#
# Environment variables:
#   GITHUB_TOKEN      - Appended to .git-credentials for HTTPS auth
#   SYSTEM_ID         - Updates machineId in .happy/settings.json
#   GIT_REPO_URL      - Repository to clone into /workspace
#   CLAUDE_EXECUTABLE - Claude binary (default: claude)
#   CLAUDE_ALLOWED_TOOLS - Space-separated list of allowed tools
#

readonly LOG_DIR="/var/log/claude"
readonly SNAPSHOT="/snapshot/snapshot.tar.gz"

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

setup_logging() {
    mkdir -p "$LOG_DIR" 2>/dev/null || true
    exec > >(tee -a "$LOG_DIR/session.log") 2>&1
    echo "=== Claude session starting at $(date -Iseconds) ==="
    echo "User: $(whoami) (UID $(id -u))"
}

# -----------------------------------------------------------------------------
# Snapshot extraction
# -----------------------------------------------------------------------------

extract_snapshot() {
    [[ -f "$SNAPSHOT" ]] || return 0

    echo "Extracting snapshot..."
    tar -tzf "$SNAPSHOT" | head -10
    tar -xzf "$SNAPSHOT" -C "$HOME"
    echo "Snapshot extracted to $HOME"
}

# -----------------------------------------------------------------------------
# Credentials setup
# -----------------------------------------------------------------------------

setup_github_token() {
    [[ -n "${GITHUB_TOKEN:-}" ]] || return 0

    local creds="$HOME/.git-credentials"

    # Ensure file ends with newline before appending (if it exists)
    if [[ -f "$creds" && -s "$creds" ]]; then
        # Add newline if file doesn't end with one
        [[ $(tail -c1 "$creds" | wc -l) -eq 0 ]] && echo >> "$creds"
    fi

    # Git credentials for clone/push
    echo "https://x-access-token:${GITHUB_TOKEN}@github.com" >> "$creds"
    chmod 600 "$creds"
    git config --global credential.helper store

    echo "GitHub token configured (git + gh)"
}

update_happy_machine_id() {
    local settings="$HOME/.happy/settings.json"
    [[ -n "${SYSTEM_ID:-}" && -f "$settings" ]] || return 0

    if command -v jq &>/dev/null; then
        jq --arg id "$SYSTEM_ID" '.machineId = $id' "$settings" > "${settings}.tmp"
        mv "${settings}.tmp" "$settings"
        echo "Happy machineId: $SYSTEM_ID"
    else
        echo "Warning: jq unavailable, skipping machineId update"
    fi
}

clear_secrets() {
    unset GITHUB_TOKEN SYSTEM_ID 2>/dev/null || true
}

# -----------------------------------------------------------------------------
# Git repository setup
# -----------------------------------------------------------------------------

transform_ssh_to_https() {
    local url="$1"

    # git@github.com:user/repo.git -> https://github.com/user/repo.git
    if [[ "$url" =~ ^git@([^:]+):(.+)$ ]]; then
        echo "https://${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
    # ssh://git@github.com/user/repo.git -> https://github.com/user/repo.git
    elif [[ "$url" =~ ^ssh://git@([^/]+)/(.+)$ ]]; then
        echo "https://${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
    else
        echo "$url"
    fi
}

setup_git_repo() {
    [[ -n "${GIT_REPO_URL:-}" ]] || return 0

    local url
    url=$(transform_ssh_to_https "$GIT_REPO_URL")
    [[ "$url" != "$GIT_REPO_URL" ]] && echo "Transformed URL: $url"

    cd /workspace
    git init -q
    git remote add origin "$url"
    echo "Git remote: $url"

    if git fetch -q origin 2>/dev/null; then
        local branch
        branch=$(git remote show origin 2>/dev/null | sed -n 's/.*HEAD branch: //p')
        if [[ -n "$branch" ]]; then
            git checkout -q "$branch" 2>/dev/null || true
            echo "Checked out: $branch"
        fi
    else
        echo "Warning: git fetch failed"
    fi
}

# -----------------------------------------------------------------------------
# Claude launch
# -----------------------------------------------------------------------------

build_claude_args() {
    local args=""

    if [[ -n "${CLAUDE_ALLOWED_TOOLS:-}" ]]; then
        # Validate: only alphanumeric, underscore, hyphen, space
        if [[ "$CLAUDE_ALLOWED_TOOLS" =~ [^a-zA-Z0-9_\ -] ]]; then
            echo "ERROR: CLAUDE_ALLOWED_TOOLS contains invalid characters" >&2
            exit 1
        fi
        args="--allowedTools $(printf '%q' "$CLAUDE_ALLOWED_TOOLS")"
    fi

    echo "$args"
}

launch_claude() {
    local executable="${CLAUDE_EXECUTABLE:-claude}"
    local args
    args=$(build_claude_args)

    # tmux provides PTY for interactive mode; docker attach connects to session
    # Note: Arguments are string-interpolated for tmux's command parser.
    # Spaces in arguments would require additional quoting/escaping.
    exec tmux new-session -s claude "$executable $args $@"
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

main() {
    setup_logging
    extract_snapshot
    setup_github_token
    update_happy_machine_id
    clear_secrets
    setup_git_repo
    launch_claude "$@"
}

main "$@"
