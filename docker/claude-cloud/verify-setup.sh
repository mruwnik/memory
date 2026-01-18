#!/bin/bash
# Verification script for Claude Cloud container setup
# Run as claude user to verify everything is correctly configured

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASS=0
FAIL=0
WARN=0

pass() {
    echo -e "${GREEN}✓${NC} $1"
    ((++PASS))
}

fail() {
    echo -e "${RED}✗${NC} $1"
    ((++FAIL))
}

warn() {
    echo -e "${YELLOW}!${NC} $1"
    ((++WARN))
}

section() {
    echo ""
    echo "=== $1 ==="
}

# -----------------------------------------------------------------------------
section "Environment"
# -----------------------------------------------------------------------------

echo "User: $(whoami) (UID $(id -u))"
echo "HOME: $HOME"
echo "PWD: $(pwd)"

# -----------------------------------------------------------------------------
section "Claude CLI"
# -----------------------------------------------------------------------------

if command -v claude &>/dev/null; then
    pass "Claude CLI found: $(which claude)"
    if claude --version &>/dev/null; then
        pass "Claude CLI version: $(claude --version 2>&1 | head -1)"
    else
        warn "Claude CLI found but --version failed"
    fi
else
    fail "Claude CLI not found in PATH"
    echo "  PATH: $PATH"
fi

# -----------------------------------------------------------------------------
section "Claude CLI Test Run"
# -----------------------------------------------------------------------------

# Show credentials state BEFORE running Claude
creds_file="$HOME/.claude/.credentials.json"
echo "Credentials state BEFORE claude run:"
if [[ -f "$creds_file" ]]; then
    before_count=$(jq '[.mcpOAuth // {} | keys[] | select(startswith("differ-review"))] | length' "$creds_file" 2>/dev/null || echo "0")
    echo "  differ-review entries in mcpOAuth: $before_count"
    jq -r '.mcpOAuth // {} | keys[] | select(startswith("differ-review"))' "$creds_file" 2>/dev/null | sed 's/^/    /'
else
    echo "  .credentials.json does not exist yet"
fi

echo ""
echo "Running 'claude -p' to ask about differ tools..."
echo "Current user: $(whoami) (UID $(id -u))"
# Script is already running as claude user at this point
if claude -p "List the differ-review MCP tools you have access to. Just list the tool names, nothing else. Then run the 'mcp__differ-review__get_review_state' tool" 2>&1; then
    pass "Claude CLI ran successfully"
else
    warn "Claude CLI returned non-zero (may be expected without full auth)"
fi

# Show credentials state AFTER running Claude
echo ""
echo "Credentials state AFTER claude run:"
if [[ -f "$creds_file" ]]; then
    after_count=$(jq '[.mcpOAuth // {} | keys[] | select(startswith("differ-review"))] | length' "$creds_file" 2>/dev/null || echo "0")
    echo "  differ-review entries in mcpOAuth: $after_count"
    jq -r '.mcpOAuth // {} | keys[] | select(startswith("differ-review"))' "$creds_file" 2>/dev/null | sed 's/^/    /'
    if [[ "$after_count" -gt "$before_count" ]]; then
        # This is expected behavior - Claude Code adds OAuth entries for HTTP MCP servers
        warn "Claude Code added $(( after_count - before_count )) new differ-review entry(ies) (expected)"
    else
        pass "Claude Code did not add any new entries"
    fi
else
    echo "  .credentials.json still does not exist"
fi

# -----------------------------------------------------------------------------
section "Claude Configuration"
# -----------------------------------------------------------------------------

if [[ -d "$HOME/.claude" ]]; then
    pass ".claude directory exists"
    echo "  Contents: $(ls -la "$HOME/.claude" 2>/dev/null | wc -l) items"
else
    warn ".claude directory missing (may be created on first run)"
fi

if [[ -f "$HOME/.claude.json" ]]; then
    pass ".claude.json exists"
    if jq -e . "$HOME/.claude.json" &>/dev/null; then
        pass ".claude.json is valid JSON"

        # Check for differ MCP config
        if jq -e '.mcpServers["differ-review"]' "$HOME/.claude.json" &>/dev/null; then
            pass "differ-review MCP configured"
            differ_url=$(jq -r '.mcpServers["differ-review"].url // "not set"' "$HOME/.claude.json")
            echo "  URL: $differ_url"
        else
            fail "differ-review MCP not configured"
        fi
    else
        fail ".claude.json is not valid JSON"
    fi
else
    fail ".claude.json missing"
fi

if [[ -f "$HOME/.claude/settings.json" ]]; then
    pass ".claude/settings.json exists"
else
    warn ".claude/settings.json missing (may be created on first run)"
fi

# Check credentials file for differ-review entries
creds_file="$HOME/.claude/.credentials.json"
if [[ -f "$creds_file" ]]; then
    pass ".credentials.json exists"
    if jq -e . "$creds_file" &>/dev/null; then
        pass ".credentials.json is valid JSON"

        # Count differ-review entries at root level (should be 0)
        root_count=$(jq '[keys[] | select(startswith("differ-review"))] | length' "$creds_file")
        if [[ "$root_count" -gt 0 ]]; then
            fail "Found $root_count differ-review entries at root level (should be in mcpOAuth)"
            jq -r 'keys[] | select(startswith("differ-review"))' "$creds_file" | sed 's/^/    /'
        else
            pass "No differ-review entries at root level"
        fi

        # Count differ-review entries inside mcpOAuth (should be exactly 1)
        if jq -e '.mcpOAuth' "$creds_file" &>/dev/null; then
            oauth_count=$(jq '[.mcpOAuth | keys[] | select(startswith("differ-review"))] | length' "$creds_file")
            if [[ "$oauth_count" -ge 1 ]]; then
                pass "Found $oauth_count differ-review entry(ies) in mcpOAuth"
                # Show all entries (Claude Code may add duplicates - this is expected)
                jq -r '.mcpOAuth | keys[] | select(startswith("differ-review"))' "$creds_file" | while read key; do
                    token_preview=$(jq -r ".mcpOAuth[\"$key\"].accessToken // \"\"" "$creds_file" | head -c 8)
                    echo "  $key -> ${token_preview}..."
                done
            else
                fail "No differ-review entry in mcpOAuth"
            fi
        else
            warn "mcpOAuth not present in .credentials.json"
        fi
    else
        fail ".credentials.json is not valid JSON"
    fi
else
    warn ".credentials.json missing"
fi

# -----------------------------------------------------------------------------
section "Happy Configuration"
# -----------------------------------------------------------------------------

if [[ -d "$HOME/.happy" ]]; then
    pass ".happy directory exists"
    if [[ -f "$HOME/.happy/settings.json" ]]; then
        pass ".happy/settings.json exists"
        if jq -e . "$HOME/.happy/settings.json" &>/dev/null; then
            pass ".happy/settings.json is valid JSON"
            machine_id=$(jq -r '.machineId // "not set"' "$HOME/.happy/settings.json")
            echo "  machineId: $machine_id"
        else
            fail ".happy/settings.json is not valid JSON"
        fi
    else
        warn ".happy/settings.json missing"
    fi
else
    warn ".happy directory missing (may be from snapshot)"
fi

# -----------------------------------------------------------------------------
section "Git Configuration"
# -----------------------------------------------------------------------------

if [[ -f "$HOME/.git-credentials" ]]; then
    pass ".git-credentials exists"
    cred_count=$(wc -l < "$HOME/.git-credentials")
    echo "  Entries: $cred_count"
else
    warn ".git-credentials missing (may not have GITHUB_TOKEN)"
fi

if [[ -f "$HOME/.gitconfig" ]]; then
    pass ".gitconfig exists"
else
    warn ".gitconfig missing"
fi

# -----------------------------------------------------------------------------
section "Git Repository"
# -----------------------------------------------------------------------------

if [[ -n "${GIT_REPO_URL:-}" ]]; then
    echo "GIT_REPO_URL: $GIT_REPO_URL"

    # Find repo in /workspace
    repo_count=$(find /workspace -maxdepth 1 -type d -name ".git" -o -type d ! -name workspace | wc -l)
    if [[ $repo_count -gt 1 ]]; then
        pass "Repository directory found in /workspace"
        for dir in /workspace/*/; do
            if [[ -d "$dir/.git" ]]; then
                echo "  Repo: $dir"
                cd "$dir"
                if git remote -v | grep -q origin; then
                    pass "Git remote 'origin' configured"
                    git remote -v | head -2 | sed 's/^/    /'
                else
                    fail "Git remote 'origin' not configured"
                fi

                branch=$(git branch --show-current 2>/dev/null || echo "")
                if [[ -n "$branch" ]]; then
                    pass "On branch: $branch"
                else
                    warn "Not on any branch (detached HEAD or empty repo)"
                fi
            fi
        done
    else
        fail "No repository found in /workspace"
    fi
else
    echo "GIT_REPO_URL not set (skipping repo checks)"
fi

# -----------------------------------------------------------------------------
section "Differ MCP Server"
# -----------------------------------------------------------------------------

if curl -s http://localhost:8576/health &>/dev/null; then
    pass "Differ health endpoint responding"
    health=$(curl -s http://localhost:8576/health)
    echo "  Response: $health"
else
    fail "Differ health endpoint not responding"
fi

# Check if we can make an MCP request (with auth)
if [[ -f "$HOME/.claude.json" ]]; then
    auth_header=$(jq -r '.mcpServers["differ-review"].headers.Authorization // ""' "$HOME/.claude.json")
    if [[ -n "$auth_header" ]]; then
        # Try a simple MCP request
        response=$(curl -s -X POST http://localhost:8576/mcp \
            -H "Content-Type: application/json" \
            -H "Authorization: $auth_header" \
            -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' 2>/dev/null || echo "")
        if echo "$response" | jq -e '.result.tools' &>/dev/null; then
            tool_count=$(echo "$response" | jq '.result.tools | length')
            pass "Differ MCP responding with $tool_count tools"
        else
            warn "Differ MCP request failed or returned unexpected response"
            echo "  Response: ${response:0:200}"
        fi
    fi
fi

# -----------------------------------------------------------------------------
section "Logs"
# -----------------------------------------------------------------------------

if [[ -f /var/log/claude/session.log ]]; then
    pass "Session log exists"
    echo "  Size: $(wc -c < /var/log/claude/session.log) bytes"
    echo "  Last 5 lines:"
    tail -5 /var/log/claude/session.log | sed 's/^/    /'
else
    warn "Session log missing"
fi

if [[ -f /var/log/claude/differ.log ]]; then
    pass "Differ log exists"
    echo "  Size: $(wc -c < /var/log/claude/differ.log) bytes"
else
    warn "Differ log missing"
fi

# -----------------------------------------------------------------------------
section "Summary"
# -----------------------------------------------------------------------------

echo ""
echo -e "${GREEN}Passed:${NC} $PASS"
echo -e "${YELLOW}Warnings:${NC} $WARN"
echo -e "${RED}Failed:${NC} $FAIL"

if [[ $FAIL -gt 0 ]]; then
    exit 1
else
    exit 0
fi
