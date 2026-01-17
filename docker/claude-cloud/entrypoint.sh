#!/bin/bash
set -e

# User home directory
HOME_DIR="$HOME"

# Set up logging to persistent directory
LOG_DIR="/var/log/claude"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/session.log"

# Redirect all output to log file while still showing on stdout
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Claude session starting at $(date -Iseconds) ==="
echo "Running as user: $(whoami) (UID $(id -u))"

# Set up SSH private key if provided
if [ -n "$SSH_PRIVATE_KEY" ]; then
    mkdir -p "$HOME_DIR/.ssh"
    echo "$SSH_PRIVATE_KEY" > "$HOME_DIR/.ssh/id_ed25519"
    chmod 600 "$HOME_DIR/.ssh/id_ed25519"
    echo "SSH key configured"
fi

# Set up Claude config if provided
if [ -n "$CLAUDE_CONFIG" ]; then
    echo "$CLAUDE_CONFIG" > "$HOME_DIR/.claude.json"
    echo "Claude config configured"
fi

# Set up Happy credentials if provided
if [ -n "$HAPPY_ACCESS_KEY" ]; then
    mkdir -p "$HOME_DIR/.happy"
    echo "$HAPPY_ACCESS_KEY" > "$HOME_DIR/.happy/access.key"
    echo "Happy access key configured"
fi

if [ -n "$HAPPY_MACHINE_ID" ]; then
    mkdir -p "$HOME_DIR/.happy"
    cat > "$HOME_DIR/.happy/settings.json" << EOF
{
  "schemaVersion": 2,
  "onboardingCompleted": true,
  "profiles": [],
  "localEnvironmentVariables": {},
  "machineId": "$HAPPY_MACHINE_ID"
}
EOF
    echo "Happy settings configured"
fi

# Clear sensitive env vars after writing to files
unset SSH_PRIVATE_KEY HAPPY_ACCESS_KEY HAPPY_MACHINE_ID 2>/dev/null || true

# Unpack snapshot if provided
if [ -f /snapshot/snapshot.tar.gz ]; then
    echo "Snapshot found, contents:"
    tar -tzf /snapshot/snapshot.tar.gz | head -20

    # Ensure target directory exists
    mkdir -p "$HOME_DIR/.claude"

    tar -xzf /snapshot/snapshot.tar.gz -C "$HOME_DIR/.claude/"
    if [ -f "$HOME_DIR/.claude/claude.json" ]; then
        mv "$HOME_DIR/.claude/claude.json" "$HOME_DIR/.claude.json"
    fi

    # Move Happy config from snapshot to correct location
    if [ -d "$HOME_DIR/.claude/.happy" ]; then
        mkdir -p "$HOME_DIR/.happy"
        cp -r "$HOME_DIR/.claude/.happy/"* "$HOME_DIR/.happy/" 2>/dev/null || true
        rm -rf "$HOME_DIR/.claude/.happy"
        echo "Happy config extracted from snapshot"
    fi

    echo "Snapshot unpacked to $HOME_DIR/.claude/:"
    ls -la "$HOME_DIR/.claude/" 2>/dev/null || echo "(empty or missing)"
fi

# Set up git repo if URL provided
if [ -n "$GIT_REPO_URL" ]; then
    cd /workspace
    git init
    git remote add origin "$GIT_REPO_URL"
    echo "Git repo initialized with remote: $GIT_REPO_URL"

    echo "Fetching from remote..."
    git fetch origin || echo "Warning: fetch failed"

    DEFAULT_BRANCH=$(git remote show origin 2>/dev/null | grep 'HEAD branch' | cut -d: -f2 | tr -d ' ' || echo "")
    if [ -n "$DEFAULT_BRANCH" ]; then
        git checkout -t "origin/$DEFAULT_BRANCH" 2>/dev/null || \
        git checkout "$DEFAULT_BRANCH" 2>/dev/null || true
    fi
fi

# Default executable
: "${CLAUDE_EXECUTABLE:=claude}"

# Build claude args for allowed tools
# Validate and escape: only allow alphanumeric, underscore, hyphen, and space
CLAUDE_ARGS=""
if [ -n "$CLAUDE_ALLOWED_TOOLS" ]; then
    # Reject any tools containing shell metacharacters
    if echo "$CLAUDE_ALLOWED_TOOLS" | grep -qE '[^a-zA-Z0-9_ -]'; then
        echo "ERROR: CLAUDE_ALLOWED_TOOLS contains invalid characters" >&2
        exit 1
    fi
    # Use printf %q for proper shell escaping
    CLAUDE_ARGS="--allowedTools $(printf '%q' "$CLAUDE_ALLOWED_TOOLS")"
fi

# Use tmux to provide a PTY so claude runs interactively
# docker attach will connect directly to the tmux session with claude running
exec tmux new-session -s claude "$CLAUDE_EXECUTABLE $CLAUDE_ARGS $*"
