#!/bin/bash
set -euo pipefail

# Debug logging - conditional on DEBUG env var to avoid noise in production
if [[ "${DEBUG:-}" == "1" || "${DEBUG:-}" == "true" ]]; then
    trap 'echo "DEBUG: FAILED at line $LINENO: $BASH_COMMAND" >> "$LOG_DIR/session.log" 2>/dev/null || true' ERR
fi

#
# Claude Cloud Container Entrypoint
#
# Prepares environment from snapshot and optional env vars, then launches Claude in tmux.
#
# Snapshot (mounted at /snapshot/snapshot.tar.gz):
#   Extracts directly to $HOME: .claude/, .claude.json, .happy/, .git-credentials, .gitconfig
#
# Environment variables:
#   GITHUB_TOKEN       - Read-only token for .git-credentials (clone, fetch)
#   GITHUB_TOKEN_WRITE - Write token for differ (push, PR creation)
#   SYSTEM_ID          - Updates machineId in .happy/settings.json
#   GIT_REPO_URL       - Repository to clone into /workspace
#   CLAUDE_EXECUTABLE  - Claude binary (default: claude)
#   CLAUDE_ALLOWED_TOOLS - Space-separated list of allowed tools
#   CLAUDE_INITIAL_PROMPT - Initial prompt to start Claude with
#   CLAUDE_RUN_ID        - Unique ID for this run (used for branch name claude/<id>)
#

readonly LOG_DIR="/var/log/claude"
readonly SNAPSHOT="/snapshot/snapshot.tar.gz"

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

# Log to file only (entrypoint messages, not Claude output)
log() {
    echo "$@" >> "$LOG_DIR/session.log"
}

setup_logging() {
    mkdir -p "$LOG_DIR" 2>/dev/null || true
    log "=== Claude session starting at $(date -Iseconds) ==="
    log "User: $(whoami) (UID $(id -u))"
}

# -----------------------------------------------------------------------------
# Snapshot extraction
# -----------------------------------------------------------------------------

extract_snapshot() {
    [[ -f "$SNAPSHOT" ]] || return 0

    log "Extracting snapshot..."
    tar -xzf "$SNAPSHOT" -C /home/claude

    # Fix ownership if running as root
    if [[ "$(id -u)" -eq 0 ]]; then
        chown -R claude:workspace /home/claude
    fi

    log "Snapshot extracted"
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

    # Git credentials for clone/push (skip if already present)
    local entry="https://x-access-token:${GITHUB_TOKEN}@github.com"
    if ! grep -qxF "$entry" "$creds" 2>/dev/null; then
        echo "$entry" >> "$creds"
    fi
    chmod 600 "$creds"
    git config --global credential.helper store

    log "GitHub token configured (git + gh)"
}

update_happy_machine_id() {
    local settings="$HOME/.happy/settings.json"
    [[ -n "${SYSTEM_ID:-}" && -f "$settings" ]] || return 0

    if command -v jq &>/dev/null; then
        jq --arg id "$SYSTEM_ID" '.machineId = $id' "$settings" > "${settings}.tmp"
        mv "${settings}.tmp" "$settings"
        log "Happy machineId: $SYSTEM_ID"
    else
        log "Warning: jq unavailable, skipping machineId update"
    fi
}

clear_secrets() {
    unset GITHUB_TOKEN GITHUB_TOKEN_WRITE SYSTEM_ID 2>/dev/null || true
}

# -----------------------------------------------------------------------------
# Differ setup (code review and PR management)
# -----------------------------------------------------------------------------

# Fixed OAuth credentials for differ - must match what Claude Code expects.
# The key format is "server-name|hash" as used by Claude Code's credential storage.
# [NOTE TO CLAUDE]: Please don't change this without explicit prior approval. Unfortunately,
# this is needed to be like this. It's not a security risk, as this is only ever used locally.
# It's needed for Claude Code to connect to it via MCP - the credentials aren't actually used
# for anything other than this.
readonly DIFFER_OAUTH_KEY="differ-review|b12e4d1d0feb83e9"
readonly DIFFER_OAUTH_JSON="{\"$DIFFER_OAUTH_KEY\":{\"serverName\":\"differ-review\",\"serverUrl\":\"http://localhost:8576/mcp\",\"clientId\":\"client_12fb6937d6b0920a5ef95e2f48bf740a45290933da98e1ce7705c536db06e734\",\"accessToken\":\"f7744608-6cbd-4019-b171-4fd47abb56f7\",\"expiresAt\":1800295465766,\"refreshToken\":\"rt_b4df4b1cc86feb8da9cd080e369f3bf88939ef40e466f6a05659809e32003f06\",\"scope\":\"read\"}}"

start_differ_server() {
    touch /var/log/claude/differ.log
    chown differ:differ /var/log/claude/differ.log

    # Allow pushing to claude/* branches on any repo
    local push_whitelist='{"*": ["claude/*"]}'
    su - differ -c "cd /opt/differ && DIFFER_PORT=8576 PUSH_WHITELIST='$push_whitelist' nohup node target/server.js >> /var/log/claude/differ.log 2>&1 &"

    # Wait for server to be ready (up to 15 seconds)
    local ready=false
    for _ in {1..30}; do
        curl -s http://localhost:8576/ >/dev/null 2>&1 && ready=true && break
        sleep 0.5
    done
    if [[ "$ready" != "true" ]]; then
        log "ERROR: Differ failed to start"
        return 1
    fi
    log "Differ server running"
}

register_differ_credentials() {
    local oauth_file="/tmp/oauth-creds.json"

    # Write credentials to temp file (avoids shell quoting issues with nested JSON)
    echo "$DIFFER_OAUTH_JSON" | jq -c ".\"$DIFFER_OAUTH_KEY\"" > "$oauth_file"
    chmod 644 "$oauth_file"

    local setup_cmd="./scripts/setup.sh --oauth-file $oauth_file"
    if [[ -n "${GITHUB_TOKEN_WRITE:-}" ]]; then
        setup_cmd="$setup_cmd --github-pat '$GITHUB_TOKEN_WRITE'"
        log "Registering OAuth + GitHub token with differ"
    else
        log "Registering OAuth credentials with differ"
    fi

    local setup_result=0
    su - differ -c "cd /opt/differ && $setup_cmd >> /var/log/claude/differ.log 2>&1" || setup_result=$?
    rm -f "$oauth_file"

    # Exit 141 is SIGPIPE from piping to log file
    if [[ $setup_result -ne 0 && $setup_result -ne 141 ]]; then
        log "WARNING: setup.sh exit code: $setup_result"
    fi
}

insert_differ_credentials() {
    local creds_file="/home/claude/.claude/.credentials.json"
    local claude_json="/home/claude/.claude.json"
    local mcp_config='{"mcpServers":{"differ-review":{"type":"http","url":"http://localhost:8576/mcp"}}}'

    # Add differ MCP server to .claude.json
    mkdir -p "$(dirname "$claude_json")"
    if [[ -f "$claude_json" ]]; then
        jq --argjson new "$mcp_config" '. * $new | .mcpServers = ((.mcpServers // {}) * $new.mcpServers)' \
            "$claude_json" > "${claude_json}.tmp" && mv "${claude_json}.tmp" "$claude_json"
    else
        echo "$mcp_config" | jq '.' > "$claude_json"
    fi
    chown claude:workspace "$claude_json"

    # Add OAuth credentials to .credentials.json (remove old differ entries, add new)
    mkdir -p "$(dirname "$creds_file")"
    if [[ -s "$creds_file" ]]; then
        jq --argjson new "$DIFFER_OAUTH_JSON" '
            .mcpOAuth = ((.mcpOAuth // {}) | with_entries(select(.key | startswith("differ-review") | not))) * $new
        ' "$creds_file" > "${creds_file}.tmp" && mv "${creds_file}.tmp" "$creds_file"
    else
        jq -n --argjson new "$DIFFER_OAUTH_JSON" '{mcpOAuth: $new}' > "$creds_file"
    fi
    chmod 600 "$creds_file"
    chown claude:workspace "$creds_file"

    log "Differ credentials configured"
}

setup_differ() {
    log "Setting up differ..."
    # Allow differ user to access repos owned by other users (claude)
    su - differ -c "git config --global --add safe.directory '*'"

    # Configure git credentials for differ user (for push operations)
    if [[ -n "${GITHUB_TOKEN_WRITE:-}" ]]; then
        local differ_creds="/home/differ/.git-credentials"
        echo "https://x-access-token:${GITHUB_TOKEN_WRITE}@github.com" > "$differ_creds"
        chown differ:differ "$differ_creds"
        chmod 600 "$differ_creds"
        su - differ -c "git config --global credential.helper store"
        log "Git credentials configured for differ user"
    fi

    start_differ_server || return 1
    register_differ_credentials
    insert_differ_credentials
    log "Differ setup complete"
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

extract_repo_name() {
    local url="$1"
    local name
    # Extract last path component and strip .git suffix
    name=$(basename "${url%.git}")
    # Validate: only allow safe characters (alphanumeric, dash, underscore, dot)
    if [[ "$name" =~ ^[a-zA-Z0-9._-]+$ ]]; then
        echo "$name"
    else
        echo "repo"  # Safe fallback for invalid names
    fi
}

generate_run_id() {
    # Use CLAUDE_RUN_ID if provided, otherwise generate unique fallback
    if [[ -n "${CLAUDE_RUN_ID:-}" ]]; then
        # Validate: only allow safe characters
        if [[ "$CLAUDE_RUN_ID" =~ ^[a-zA-Z0-9_-]+$ ]]; then
            echo "$CLAUDE_RUN_ID"
            return
        fi
        log "Warning: CLAUDE_RUN_ID contains invalid characters, generating fallback"
    fi
    # Fallback: timestamp + random suffix (e.g., 20240115-143052-a7b3)
    echo "$(date +%Y%m%d-%H%M%S)-$(head -c4 /dev/urandom | xxd -p | head -c4)"
}

setup_git_repo() {
    [[ -n "${GIT_REPO_URL:-}" ]] || return 0

    local url
    url=$(transform_ssh_to_https "$GIT_REPO_URL")
    [[ "$url" != "$GIT_REPO_URL" ]] && log "Transformed URL: $url"

    local repo_name
    repo_name=$(extract_repo_name "$url")
    if [[ -z "$repo_name" ]]; then
        log "ERROR: Could not extract repository name from URL"
        return 1
    fi
    local repo_dir="/workspace/$repo_name"

    mkdir -p "$repo_dir"
    cd "$repo_dir"
    git init -q
    git remote add origin "$url"
    log "Git remote: $url -> $repo_dir"

    if git fetch -q origin 2>/dev/null; then
        local branch
        branch=$(git remote show origin 2>/dev/null | sed -n 's/.*HEAD branch: //p')
        if [[ -n "$branch" ]]; then
            git checkout -q "$branch" 2>/dev/null || true
            log "Checked out: $branch"

            # Create claude/<run_id> branch for this session
            local run_id
            run_id=$(generate_run_id)
            local claude_branch="claude/$run_id"
            git checkout -q -b "$claude_branch"
            log "Created branch: $claude_branch"
        fi
    else
        log "Warning: git fetch failed"
    fi
}

# -----------------------------------------------------------------------------
# Claude launch
# -----------------------------------------------------------------------------

launch_claude() {
    local executable="${CLAUDE_EXECUTABLE:-claude}"
    log "Launching Claude: CLAUDE_EXECUTABLE='$executable'"

    # Validate to prevent glob expansion or command injection
    if [[ "$executable" =~ [^a-zA-Z0-9\ _./-] ]]; then
        log "ERROR: CLAUDE_EXECUTABLE contains invalid characters"
        exit 1
    fi

    # Build command array (unquoted $executable allows word splitting for multi-word commands)
    local -a cmd=($executable)

    # Prompt must come before flags
    [[ -n "${CLAUDE_INITIAL_PROMPT:-}" ]] && cmd+=("$CLAUDE_INITIAL_PROMPT")
    cmd+=("$@")

    if [[ -n "${CLAUDE_ALLOWED_TOOLS:-}" ]]; then
        if [[ "$CLAUDE_ALLOWED_TOOLS" =~ [^a-zA-Z0-9_\ -] ]]; then
            log "ERROR: CLAUDE_ALLOWED_TOOLS contains invalid characters"
            exit 1
        fi
        # Convert space-separated tools to comma-separated (what --allowedTools expects)
        local tools_csv="${CLAUDE_ALLOWED_TOOLS// /,}"
        cmd+=(--allowedTools "$tools_csv")
    fi

    log "Full command: ${cmd[*]}"

    # Non-interactive mode: run directly without tmux
    if [[ ! -t 0 ]]; then
        log "No TTY detected, running directly"
        exec "${cmd[@]}"
    fi

    # Clean up relay and tmux on signals
    cleanup() {
        log "Caught signal, shutting down..."
        kill "$RELAY_PID" 2>/dev/null || true
        tmux kill-session -t claude 2>/dev/null || true
        exit 0
    }
    trap cleanup TERM INT

    # Start tmux detached with mouse support (so scroll events reach Claude Code)
    tmux new-session -d -s claude -- "${cmd[@]}"
    tmux set -t claude mouse on
    log "tmux session started"

    # Start terminal relay (fast path for screen capture/input)
    python3 /opt/terminal_relay.py &
    RELAY_PID=$!
    log "Terminal relay started (PID $RELAY_PID)"

    # Wait for tmux session to end. No client attached so the relay has
    # full control over window sizing (attach would create a client whose
    # PTY constrains resize-window).
    while tmux has-session -t claude 2>/dev/null; do
        sleep 1
    done
    log "tmux session ended"

    # Clean up relay
    kill "$RELAY_PID" 2>/dev/null || true
    wait "$RELAY_PID" 2>/dev/null || true
}

# -----------------------------------------------------------------------------
# Main (runs as claude user)
# -----------------------------------------------------------------------------

install_claude_if_missing() {
    if command -v claude &>/dev/null; then
        log "Claude CLI found: $(which claude)"
        return 0
    fi

    log "Claude CLI not found, installing..."
    local install_log="$LOG_DIR/claude-install.log"

    if ! curl -fsSL https://claude.ai/install.sh > /tmp/install-claude.sh 2>> "$install_log"; then
        log "ERROR: Failed to download install script"
        cat "$install_log" >> "$LOG_DIR/session.log" 2>/dev/null || true
        exit 1
    fi

    if ! bash /tmp/install-claude.sh >> "$install_log" 2>&1; then
        log "ERROR: Install script failed. See $install_log for details"
        tail -50 "$install_log" >> "$LOG_DIR/session.log" 2>/dev/null || true
        exit 1
    fi
    rm -f /tmp/install-claude.sh

    # Refresh PATH to pick up new install
    export PATH="$HOME/.local/bin:$PATH"

    if command -v claude &>/dev/null; then
        log "Claude CLI installed: $(which claude)"
    else
        log "ERROR: Claude CLI installation failed - not found in PATH after install"
        log "PATH=$PATH"
        ls -la "$HOME/.local/bin/" >> "$LOG_DIR/session.log" 2>/dev/null || true
        exit 1
    fi
}

main_as_claude() {
    setup_logging
    mkdir -p "$HOME/.local/bin" "$HOME/.local/share/claude"
    install_claude_if_missing
    setup_github_token
    update_happy_machine_id
    setup_git_repo
    clear_secrets
    launch_claude "$@"
}

# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------

if [[ "$(id -u)" -eq 0 ]]; then
    # Running as root: setup environment, then switch to claude user
    mkdir -p "$LOG_DIR" 2>/dev/null || true
    echo "=== Entrypoint starting at $(date -Iseconds) ===" > "$LOG_DIR/session.log"
    log "[root] Starting as root"

    extract_snapshot
    setup_differ

    chown claude:workspace "$LOG_DIR/session.log"

    # Fix ownership of /home/claude (needed when using environment volumes)
    # The volume root may be owned correctly, but subdirectories from snapshots may not be
    if [[ -d /home/claude ]]; then
        log "[root] Fixing /home/claude ownership..."
        chown -R claude:workspace /home/claude
    fi

    export HOME=/home/claude
    export PATH="/home/claude/.local/bin:${PATH}"
    cd /workspace
    log "[root] Switching to claude user"
    exec runuser -p -u claude -- /entrypoint.sh "$@"
else
    # Running as claude user: launch Claude
    main_as_claude "$@"
fi
