#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SECRETS_DIR="$PROJECT_DIR/secrets"
ENV_FILE="$PROJECT_DIR/.env"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info() { echo -e "${CYAN}$1${NC}"; }
success() { echo -e "${GREEN}✓ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
error() { echo -e "${RED}✗ $1${NC}"; }

# Generate a random password/secret
generate_secret() {
    openssl rand -base64 32 | tr -d '/+=' | head -c 32
}

# Read a value from .env file
get_env_value() {
    local key="$1"
    if [ -f "$ENV_FILE" ]; then
        grep "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- | sed 's/^"//' | sed 's/"$//'
    fi
}

# Set or update a value in .env file
set_env_value() {
    local key="$1"
    local value="$2"

    if [ ! -f "$ENV_FILE" ]; then
        touch "$ENV_FILE"
    fi

    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        # Update existing value (macOS compatible sed)
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
        else
            sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
        fi
    else
        echo "${key}=${value}" >> "$ENV_FILE"
    fi
}

# Prompt for a value if not already set
prompt_if_missing() {
    local key="$1"
    local prompt="$2"
    local required="${3:-false}"
    local current_value

    current_value=$(get_env_value "$key")

    if [ -n "$current_value" ]; then
        success "$key already configured"
        return 0
    fi

    echo ""
    if [ "$required" = "true" ]; then
        echo -e "${YELLOW}[Required]${NC} $prompt"
    else
        echo -e "${CYAN}[Optional]${NC} $prompt"
    fi

    read -r -p "Enter value (or press Enter to skip): " value

    if [ -n "$value" ]; then
        set_env_value "$key" "$value"
        success "$key configured"
    elif [ "$required" = "true" ]; then
        warn "$key skipped - you'll need to set this before running"
    else
        info "$key skipped"
    fi
}

# Check if a secret file exists and has content
secret_exists() {
    local file="$SECRETS_DIR/$1"
    [ -f "$file" ] && [ -s "$file" ]
}

# Write a secret file
write_secret() {
    local file="$SECRETS_DIR/$1"
    local value="$2"
    echo -n "$value" > "$file"
    chmod 600 "$file"
}

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "           Memory Knowledge Base - Installation Setup          "
echo "═══════════════════════════════════════════════════════════════"
echo ""

# ─────────────────────────────────────────────────────────────────────
# Application Name
# ─────────────────────────────────────────────────────────────────────

if [ -n "$(get_env_value 'APP_NAME')" ]; then
    success "APP_NAME already configured: $(get_env_value 'APP_NAME')"
else
    echo ""
    info "The app name is used for MCP server name, Celery queues, Discord channels, etc."
    read -r -p "Application name [memory]: " app_name
    if [ -n "$app_name" ]; then
        set_env_value "APP_NAME" "$app_name"
        success "APP_NAME set to $app_name"
    else
        set_env_value "APP_NAME" "memory"
        info "APP_NAME using default (memory)"
    fi
fi

# Create directories
info "Creating directories..."
mkdir -p "$SECRETS_DIR"
mkdir -p "$PROJECT_DIR/memory_files"

# ─────────────────────────────────────────────────────────────────────
# Auto-generated secrets (no user input needed)
# ─────────────────────────────────────────────────────────────────────

echo ""
info "Checking auto-generated secrets..."

# PostgreSQL password
if secret_exists "postgres_password.txt"; then
    success "PostgreSQL password already exists"
else
    write_secret "postgres_password.txt" "$(generate_secret)"
    success "Generated PostgreSQL password"
fi

# JWT secret
if secret_exists "jwt_secret.txt"; then
    success "JWT secret already exists"
else
    write_secret "jwt_secret.txt" "$(generate_secret)"
    success "Generated JWT secret"
fi

# Redis password (stored in .env, not secrets/)
if [ -n "$(get_env_value 'REDIS_PASSWORD')" ]; then
    success "Redis password already configured"
else
    set_env_value "REDIS_PASSWORD" "$(generate_secret)"
    success "Generated Redis password"
fi

# ─────────────────────────────────────────────────────────────────────
# API Keys (require user input)
# ─────────────────────────────────────────────────────────────────────

echo ""
echo "─────────────────────────────────────────────────────────────────"
echo "                         API Keys                                "
echo "─────────────────────────────────────────────────────────────────"
echo ""
info "The following API keys are needed for core functionality."
info "You can get them from the respective provider dashboards."
echo ""

# OpenAI - for embeddings fallback and some LLM features
prompt_if_missing "OPENAI_API_KEY" \
    "OpenAI API Key (https://platform.openai.com/api-keys)
    Used for: embeddings fallback, some LLM features" \
    "true"

# Also write to secrets file for Docker
openai_key=$(get_env_value "OPENAI_API_KEY")
if [ -n "$openai_key" ]; then
    write_secret "openai_key.txt" "$openai_key"
fi

# Anthropic - for query analysis, reranking, summarization
prompt_if_missing "ANTHROPIC_API_KEY" \
    "Anthropic API Key (https://console.anthropic.com/settings/keys)
    Used for: query analysis, reranking, summarization" \
    "true"

# Also write to secrets file for Docker
anthropic_key=$(get_env_value "ANTHROPIC_API_KEY")
if [ -n "$anthropic_key" ]; then
    write_secret "anthropic_key.txt" "$anthropic_key"
fi

# Voyage AI - primary embedding provider
prompt_if_missing "VOYAGE_API_KEY" \
    "Voyage AI API Key (https://dash.voyageai.com/api-keys)
    Used for: primary embeddings (recommended for best search quality)" \
    "true"

# ─────────────────────────────────────────────────────────────────────
# Set defaults for services
# ─────────────────────────────────────────────────────────────────────

echo ""
info "Setting service defaults..."

# Only set if not already configured
[ -z "$(get_env_value 'DB_HOST')" ] && set_env_value "DB_HOST" "localhost"
[ -z "$(get_env_value 'DB_PORT')" ] && set_env_value "DB_PORT" "15432"
[ -z "$(get_env_value 'QDRANT_HOST')" ] && set_env_value "QDRANT_HOST" "localhost"
[ -z "$(get_env_value 'REDIS_HOST')" ] && set_env_value "REDIS_HOST" "localhost"
[ -z "$(get_env_value 'FILE_STORAGE_DIR')" ] && set_env_value "FILE_STORAGE_DIR" "$PROJECT_DIR/memory_files"

# Docker group GID - needed for orchestrator socket access
if [ -z "$(get_env_value 'DOCKER_GID')" ]; then
    docker_gid=$(getent group docker 2>/dev/null | cut -d: -f3)
    if [ -n "$docker_gid" ]; then
        set_env_value "DOCKER_GID" "$docker_gid"
        success "DOCKER_GID set to $docker_gid"
    else
        warn "Docker group not found - DOCKER_GID not set (needed for Claude sessions)"
    fi
else
    success "DOCKER_GID already configured: $(get_env_value 'DOCKER_GID')"
fi

# API port - only write to .env if user wants non-default
if [ -n "$(get_env_value 'API_PORT')" ]; then
    success "API_PORT already configured: $(get_env_value 'API_PORT')"
else
    echo ""
    read -r -p "API port [8000]: " api_port
    if [ -n "$api_port" ] && [ "$api_port" != "8000" ]; then
        set_env_value "API_PORT" "$api_port"
        success "API_PORT set to $api_port"
    else
        info "API_PORT using default (8000)"
    fi
fi

success "Service defaults configured"

# ─────────────────────────────────────────────────────────────────────
# Deployment Settings (optional)
# ─────────────────────────────────────────────────────────────────────

echo ""
echo "─────────────────────────────────────────────────────────────────"
echo "                  Deployment Settings (Optional)                 "
echo "─────────────────────────────────────────────────────────────────"
echo ""

info "These settings are used by tools/deploy.sh and tools/diagnose.sh"
echo ""

if [ -n "$(get_env_value 'DEPLOY_HOST')" ]; then
    success "DEPLOY_HOST already configured: $(get_env_value 'DEPLOY_HOST')"
else
    read -r -p "SSH host for deployment (e.g., 'myserver' or 'user@host') [skip]: " deploy_host
    if [ -n "$deploy_host" ]; then
        set_env_value "DEPLOY_HOST" "$deploy_host"
        success "DEPLOY_HOST set to $deploy_host"
    else
        info "DEPLOY_HOST skipped"
    fi
fi

if [ -n "$(get_env_value 'DEPLOY_DIR')" ]; then
    success "DEPLOY_DIR already configured: $(get_env_value 'DEPLOY_DIR')"
else
    read -r -p "Remote directory path [skip]: " deploy_dir
    if [ -n "$deploy_dir" ]; then
        set_env_value "DEPLOY_DIR" "$deploy_dir"
        success "DEPLOY_DIR set to $deploy_dir"
    else
        info "DEPLOY_DIR skipped"
    fi
fi

# ─────────────────────────────────────────────────────────────────────
# SSH keys for git operations (optional)
# ─────────────────────────────────────────────────────────────────────

echo ""
echo "─────────────────────────────────────────────────────────────────"
echo "                     SSH Keys (Optional)                         "
echo "─────────────────────────────────────────────────────────────────"
echo ""

if [ -f "$SECRETS_DIR/ssh_private_key" ] && [ -f "$SECRETS_DIR/ssh_public_key" ]; then
    success "SSH keys already exist"
else
    info "SSH keys are needed for git push operations (e.g., notes sync)."
    read -r -p "Generate SSH key pair? [y/N]: " generate_ssh

    if [[ "$generate_ssh" =~ ^[Yy]$ ]]; then
        ssh-keygen -t ed25519 -C "memory-kb" -f "$SECRETS_DIR/ssh_private_key" -N ""
        mv "$SECRETS_DIR/ssh_private_key.pub" "$SECRETS_DIR/ssh_public_key"
        success "SSH keys generated"
        echo ""
        info "Add this public key to your git provider:"
        echo ""
        cat "$SECRETS_DIR/ssh_public_key"
        echo ""

        # Generate known_hosts for common providers
        info "Generating known_hosts for GitHub..."
        ssh-keyscan github.com > "$SECRETS_DIR/ssh_known_hosts" 2>/dev/null
        success "Known hosts configured"
    else
        # Create empty placeholder files so docker-compose doesn't fail
        touch "$SECRETS_DIR/ssh_private_key"
        touch "$SECRETS_DIR/ssh_public_key"
        touch "$SECRETS_DIR/ssh_known_hosts"
        info "SSH keys skipped - placeholder files created"
    fi
fi

# ─────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "                      Setup Complete!                          "
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Check what's configured
missing=()
[ -z "$(get_env_value 'OPENAI_API_KEY')" ] && missing+=("OPENAI_API_KEY")
[ -z "$(get_env_value 'ANTHROPIC_API_KEY')" ] && missing+=("ANTHROPIC_API_KEY")
[ -z "$(get_env_value 'VOYAGE_API_KEY')" ] && missing+=("VOYAGE_API_KEY")

if [ ${#missing[@]} -gt 0 ]; then
    warn "Missing required keys: ${missing[*]}"
    echo ""
    info "Edit $ENV_FILE to add them, then re-run this script."
else
    success "All required keys configured!"
fi

echo ""
info "Next steps:"
echo "  1. Review configuration: $ENV_FILE"
echo "  2. Start services: docker compose up -d"
echo "  3. Create a user: python tools/add_user.py --email you@example.com --password yourpass --name 'Your Name'"
echo "  4. Access the app: http://localhost:8000"
echo ""
