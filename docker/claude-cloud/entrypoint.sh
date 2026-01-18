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
#   GITHUB_TOKEN       - Read-only token for .git-credentials (clone, fetch)
#   GITHUB_TOKEN_WRITE - Write token for differ (push, PR creation)
#   SYSTEM_ID          - Updates machineId in .happy/settings.json
#   GIT_REPO_URL       - Repository to clone into /workspace
#   CLAUDE_EXECUTABLE  - Claude binary (default: claude)
#   CLAUDE_ALLOWED_TOOLS - Space-separated list of allowed tools
#   CLAUDE_INITIAL_PROMPT - Initial prompt to start Claude with
#   DIFFER_API_KEY     - API key for differ MCP (generated if not provided)
#

readonly LOG_DIR="/var/log/claude"
readonly SNAPSHOT="/snapshot/snapshot.tar.gz"

echo "Starting claude-cloud entrypoint" > "$LOG_DIR/session.log"

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
    # Preview first 10 files (|| true to handle SIGPIPE from head with pipefail)
    tar -tzf "$SNAPSHOT" | head -10 || true
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
    unset GITHUB_TOKEN GITHUB_TOKEN_WRITE SYSTEM_ID DIFFER_API_KEY 2>/dev/null || true
}

# -----------------------------------------------------------------------------
# Differ setup (code review and PR management)
# Runs as root, uses su to run differ commands as differ user
# -----------------------------------------------------------------------------

start_differ() {
    # Only setup differ if we have a repo to work with
    [[ -n "${GIT_REPO_URL:-}" ]] || return 0

    echo "Configuring differ..."

    # Generate API key for differ authentication
    local api_key
    api_key="$(openssl rand -hex 16)"

    # Build environment for differ server
    local differ_env="DIFFER_API_KEY=$api_key"
    differ_env="$differ_env DIFFER_PORT=8576"

    # Add GitHub token if provided (for push/PR operations)
    if [[ -n "${GITHUB_TOKEN_WRITE:-}" ]]; then
        differ_env="$differ_env GITHUB_TOKEN=$GITHUB_TOKEN_WRITE"
    fi

    # Start differ server as differ user (background)
    # Pass config via environment variables
    echo "Starting differ MCP server..."
    su - differ -c "cd /opt/differ && $differ_env nohup node target/server.js > /var/log/claude/differ.log 2>&1 &"

    # Wait for differ to be ready
    local differ_ready=false
    for i in {1..30}; do
        if curl -s http://localhost:8576/health >/dev/null 2>&1; then
            echo "Differ ready"
            differ_ready=true
            break
        fi
        sleep 0.5
    done

    if [[ "$differ_ready" != "true" ]]; then
        echo "WARNING: Differ failed to start within 15 seconds" >&2
        # Show last few lines of differ log for debugging
        tail -20 /var/log/claude/differ.log 2>/dev/null || true
    fi

    # Save API key for claude user to configure MCP.
    # Security model: The key is stored world-readable (644) because:
    # - Container is single-tenant: only claude and differ users exist
    # - Claude needs to read it to configure MCP settings
    # - Differ owns the server that validates the key
    # - Key is ephemeral (regenerated each container start) and container-scoped
    echo "$api_key" > /run/differ-api-key
    chmod 644 /run/differ-api-key
}

# -----------------------------------------------------------------------------
# Configure Claude's MCP to use differ (runs as claude user)
# -----------------------------------------------------------------------------

configure_differ_mcp() {
    [[ -f /run/differ-api-key ]] || return 0

    local api_key
    api_key=$(cat /run/differ-api-key)

    local settings="$HOME/.claude/settings.json"
    mkdir -p "$(dirname "$settings")"

    local differ_config
    differ_config=$(cat <<EOF
{
  "mcpServers": {
    "differ-review": {
      "type": "http",
      "url": "http://localhost:8576/mcp",
      "headers": {
        "Authorization": "Bearer $api_key"
      }
    }
  }
}
EOF
)

    if [[ -f "$settings" ]]; then
        # Deep merge: preserve existing mcpServers while adding differ-review
        jq --argjson new "$differ_config" '
            . * $new |
            .mcpServers = ((.mcpServers // {}) * ($new.mcpServers // {}))
        ' "$settings" > "${settings}.tmp"
        mv "${settings}.tmp" "$settings"
    else
        echo "$differ_config" > "$settings"
    fi

    echo "Differ MCP configured"
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

setup_git_repo() {
    [[ -n "${GIT_REPO_URL:-}" ]] || return 0

    local url
    url=$(transform_ssh_to_https "$GIT_REPO_URL")
    [[ "$url" != "$GIT_REPO_URL" ]] && echo "Transformed URL: $url"

    local repo_name
    repo_name=$(extract_repo_name "$url")
    if [[ -z "$repo_name" ]]; then
        echo "ERROR: Could not extract repository name from URL" >&2
        return 1
    fi
    local repo_dir="/workspace/$repo_name"

    mkdir -p "$repo_dir"
    cd "$repo_dir"
    git init -q
    git remote add origin "$url"
    echo "Git remote: $url -> $repo_dir"

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

launch_claude() {
    local executable="${CLAUDE_EXECUTABLE:-claude}"

    # Build command as an array to prevent command injection via CLAUDE_INITIAL_PROMPT
    # or other environment variables. Array elements are passed to tmux without
    # shell interpretation.
    local -a cmd=("$executable")

    if [[ -n "${CLAUDE_ALLOWED_TOOLS:-}" ]]; then
        # Validate: only alphanumeric, underscore, hyphen, space
        if [[ "$CLAUDE_ALLOWED_TOOLS" =~ [^a-zA-Z0-9_\ -] ]]; then
            echo "ERROR: CLAUDE_ALLOWED_TOOLS contains invalid characters" >&2
            exit 1
        fi
        cmd+=(--allowedTools "$CLAUDE_ALLOWED_TOOLS")
    fi

    # Add initial prompt as a positional argument.
    # NOTE: Very long prompts may hit ARG_MAX limits (~2MB on Linux). For prompts
    # exceeding ~100KB, consider using a file-based approach instead.
    if [[ -n "${CLAUDE_INITIAL_PROMPT:-}" ]]; then
        cmd+=(-p "$CLAUDE_INITIAL_PROMPT")
    fi

    # Append any positional arguments passed to entrypoint
    cmd+=("$@")

    # tmux provides PTY for interactive mode; docker attach connects to session
    # Using array expansion ensures each element is passed as a separate argument
    exec tmux new-session -s claude -- "${cmd[@]}"
}

# -----------------------------------------------------------------------------
# Main (runs as claude user)
# -----------------------------------------------------------------------------

main_as_claude() {
    setup_logging
    extract_snapshot
    setup_github_token
    update_happy_machine_id
    configure_differ_mcp
    setup_git_repo
    clear_secrets
    launch_claude "$@"
}

# -----------------------------------------------------------------------------
# Entrypoint (runs as root initially)
# -----------------------------------------------------------------------------

if [[ "$(id -u)" -eq 0 ]]; then
    echo "Starting claude-cloud entrypoint as root" > "$LOG_DIR/session.log"
    # Root: start differ, then switch to claude user
    start_differ
    echo "Differ started" >> "$LOG_DIR/session.log"
    # Use runuser to drop privileges while preserving env and TTY
    export HOME=/home/claude
    cd /workspace
    exec runuser -p -u claude -- /entrypoint.sh "$@"
else
    # Claude user: run main
    main_as_claude "$@"
fi
