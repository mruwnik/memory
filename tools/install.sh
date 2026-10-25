#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SECRETS_DIR="$PROJECT_DIR/secrets"
ENV_FILE="$PROJECT_DIR/.env"

# --non-interactive never prompts. Required: DOMAIN. Optional: ADMIN_EMAIL with
# ADMIN_PASSWORD (else the first account is claimed with the setup token),
# CLAUDE_CONTAINERS=on (wires up the Claude Code containers overlay; an admin then
# turns the module on in the setup wizard or on the Server settings page),
# VOYAGE_API_KEY (else an admin sets it on the settings page), and,
# used instead of generating, POSTGRES_PASSWORD, REDIS_PASSWORD, APP_NAME,
# BACKUP_ENCRYPTION_KEY. Anything already installed is
# kept. S3 backups need an explicit choice: S3_BACKUP_BUCKET (with optional
# S3_BACKUP_PREFIX and S3_BACKUP_REGION) to enable them, or S3_BACKUP=off.
NON_INTERACTIVE=false
for arg in "$@"; do
    case "$arg" in
        --non-interactive) NON_INTERACTIVE=true ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done

if [ "$NON_INTERACTIVE" = true ]; then
    if [ -z "${DOMAIN:-}" ]; then
        echo "✗ DOMAIN is required with --non-interactive" >&2
        exit 1
    fi
    if [[ -n "${ADMIN_EMAIL:-}${ADMIN_PASSWORD:-}" && ( -z "${ADMIN_EMAIL:-}" || -z "${ADMIN_PASSWORD:-}" ) ]]; then
        echo "✗ Set both ADMIN_EMAIL and ADMIN_PASSWORD, or neither" >&2
        exit 1
    fi

    # Values land unquoted in .env and in sed expressions; ADMIN_PASSWORD only
    # travels through the environment, so it is unrestricted.
    for var in DOMAIN ADMIN_EMAIL VOYAGE_API_KEY POSTGRES_PASSWORD \
        REDIS_PASSWORD APP_NAME S3_BACKUP_BUCKET \
        S3_BACKUP_PREFIX S3_BACKUP_REGION BACKUP_ENCRYPTION_KEY KBNET_SUBNET \
        KBNET_GATEWAY; do
        if [[ ! "${!var:-}" =~ ^[A-Za-z0-9._@:/+-]*$ ]]; then
            echo "✗ $var contains characters outside A-Za-z0-9._@:/+-" >&2
            exit 1
        fi
    done
    if [[ ! "${CLAUDE_CONTAINERS:-off}" =~ ^(on|off)$ ]]; then
        echo "✗ CLAUDE_CONTAINERS must be on or off" >&2
        exit 1
    fi
fi

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

# Whether .env already records the S3 backup choice (a bucket, or off)
backup_choice_made() {
    [ -n "$(get_env_value 'S3_BACKUP_BUCKET')" ] || [ "$(get_env_value 'S3_BACKUP')" = off ]
}

# Whether this run's environment supplies exactly one valid choice
backup_choice_supplied() {
    if [ -n "${S3_BACKUP_BUCKET:-}" ]; then
        [ -z "${S3_BACKUP:-}" ]
    else
        [ "${S3_BACKUP:-}" = off ]
    fi
}

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
        (umask 077 && touch "$ENV_FILE")
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

# Like read, but answers empty when non-interactive
ask() {
    local var="$1" prompt="$2"
    if [ "$NON_INTERACTIVE" = true ]; then
        printf -v "$var" ''
        return 0
    fi
    read -r -p "$prompt" "$var"
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
    (umask 077 && echo -n "$value" > "$file")
    chmod 600 "$file"
}

# Fail before anything is written: backing up to S3 must be chosen, never assumed
if [ "$NON_INTERACTIVE" = true ] && ! backup_choice_made; then
    if ! backup_choice_supplied; then
        error "Choose S3 backups with --non-interactive: set S3_BACKUP_BUCKET (optionally S3_BACKUP_PREFIX, S3_BACKUP_REGION) to back up to S3, or S3_BACKUP=off for no backups" >&2
        exit 1
    fi
fi

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
    if [ "$NON_INTERACTIVE" = true ]; then
        app_name="${APP_NAME:-}"
    else
        read -r -p "Application name [memory]: " app_name
    fi
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
(umask 077 && mkdir -p "$SECRETS_DIR")
chmod 700 "$SECRETS_DIR"
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
    write_secret "postgres_password.txt" "${POSTGRES_PASSWORD:-$(generate_secret)}"
    success "Generated PostgreSQL password"
fi

# Redis password (stored in .env, not secrets/)
if [ -n "$(get_env_value 'REDIS_PASSWORD')" ]; then
    success "Redis password already configured"
else
    set_env_value "REDIS_PASSWORD" "${REDIS_PASSWORD:-$(generate_secret)}"
    success "Generated Redis password"
fi

# Compose mounts this file as a secret, so the stack won't start without it
if secret_exists "secrets_encryption_key.txt"; then
    success "Secrets encryption key already exists"
else
    write_secret "secrets_encryption_key.txt" "$(generate_secret)"
    success "Generated secrets encryption key"
fi

# ─────────────────────────────────────────────────────────────────────
# API Keys
# ─────────────────────────────────────────────────────────────────────

echo ""
echo "─────────────────────────────────────────────────────────────────"
echo "                         API Keys                                "
echo "─────────────────────────────────────────────────────────────────"
echo ""
info "Get it from the provider's dashboard, or set it later on the settings page."
echo ""

# Voyage AI - embeddings + reranking. Only the secret file holds it: the stack reads
# that file in preference to .env. Compose won't start without the file, so it is
# written even when empty, which leaves the key to the settings page.
if [ -s "$SECRETS_DIR/voyage_key.txt" ]; then
    success "VOYAGE_API_KEY already configured"
else
    # Older installs also wrote it to .env.
    voyage_key=$(get_env_value "VOYAGE_API_KEY")
    if [ -z "$voyage_key" ] && [ "$NON_INTERACTIVE" = true ]; then
        voyage_key=${VOYAGE_API_KEY:-}
    elif [ -z "$voyage_key" ]; then
        echo ""
        echo -e "${CYAN}[Optional]${NC} Voyage AI API Key (https://dash.voyageai.com/api-keys)
    Used for: all embeddings (voyage-3-large / voyage-multimodal-3) and reranking"
        read -r -p "Enter value (or leave blank to set it in the web UI): " voyage_key
    fi
    write_secret "voyage_key.txt" "$voyage_key"
fi

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

# API port - only write to .env if user wants non-default
if [ -n "$(get_env_value 'API_PORT')" ]; then
    success "API_PORT already configured: $(get_env_value 'API_PORT')"
else
    echo ""
    ask api_port "API port [8000]: "
    if [ -n "$api_port" ] && [ "$api_port" != "8000" ]; then
        set_env_value "API_PORT" "$api_port"
        success "API_PORT set to $api_port"
    else
        info "API_PORT using default (8000)"
    fi
fi

if [ "$NON_INTERACTIVE" = true ]; then
    # Loopback only: Caddy on this host is the sole public entry point and the
    # only thing allowed to set X-Forwarded-*
    set_env_value "API_PORT" "127.0.0.1:8000"
    set_env_value "SERVER_URL" "https://$DOMAIN"
    # Docker hands the proxy's loopback connections to the api from kbnet's
    # gateway; the override pins it (deploy/aws/compose.proxy.yaml)
    if [ -z "$(get_env_value 'COMPOSE_FILE')" ]; then
        set_env_value "COMPOSE_FILE" "docker-compose.yaml:deploy/aws/compose.proxy.yaml"
    elif [[ "$(get_env_value 'COMPOSE_FILE')" != *deploy/aws/compose.proxy.yaml* ]]; then
        warn "COMPOSE_FILE is set without deploy/aws/compose.proxy.yaml; the proxy won't be trusted"
    fi
    kbnet_subnet="$(get_env_value 'KBNET_SUBNET')"
    kbnet_subnet="${kbnet_subnet:-${KBNET_SUBNET:-10.213.47.0/24}}"
    kbnet_base="${kbnet_subnet%/*}"
    kbnet_gateway="$(get_env_value 'KBNET_GATEWAY')"
    kbnet_gateway="${kbnet_gateway:-${KBNET_GATEWAY:-${kbnet_base%.*}.$(( ${kbnet_base##*.} + 1 ))}}"
    set_env_value "KBNET_SUBNET" "$kbnet_subnet"
    set_env_value "KBNET_GATEWAY" "$kbnet_gateway"
    set_env_value "FORWARDED_ALLOW_IPS" "127.0.0.1,::1,$kbnet_gateway"
    set_env_value "RATE_LIMIT_TRUSTED_PROXIES" "127.0.0.1,::1,$kbnet_gateway"
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
    ask deploy_host "SSH host for deployment (e.g., 'myserver' or 'user@host') [skip]: "
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
    ask deploy_dir "Remote directory path [skip]: "
    if [ -n "$deploy_dir" ]; then
        set_env_value "DEPLOY_DIR" "$deploy_dir"
        success "DEPLOY_DIR set to $deploy_dir"
    else
        info "DEPLOY_DIR skipped"
    fi
fi

# ─────────────────────────────────────────────────────────────────────
# S3 backups (explicit choice)
# ─────────────────────────────────────────────────────────────────────

echo ""
echo "─────────────────────────────────────────────────────────────────"
echo "                          S3 Backups                             "
echo "─────────────────────────────────────────────────────────────────"
echo ""

enable_backups() {
    local bucket="$1" prefix="$2" region="$3"
    if [[ ! "${BACKUP_ENCRYPTION_KEY:-}" =~ ^[A-Za-z0-9._@:/+-]*$ ]]; then
        error "BACKUP_ENCRYPTION_KEY contains characters outside A-Za-z0-9._@:/+-" >&2
        exit 1
    fi
    set_env_value "S3_BACKUP_BUCKET" "$bucket"
    set_env_value "S3_BACKUP_PREFIX" "$prefix"
    set_env_value "S3_BACKUP_REGION" "$region"
    if [ -z "$(get_env_value 'BACKUP_ENCRYPTION_KEY')" ]; then
        set_env_value "BACKUP_ENCRYPTION_KEY" "${BACKUP_ENCRYPTION_KEY:-$(generate_secret)}"
        warn "BACKUP_ENCRYPTION_KEY is in .env: keep a copy elsewhere, backups can't be decrypted without it"
    fi
    info "The workers also need AWS credentials (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or an instance role)"
    success "S3 backups go to s3://$bucket/$prefix"
}

# Ask until the answer is usable: value must be non-empty and use safe characters
ask_backup_value() {
    local var="$1" prompt="$2" default="${3:-}" answer
    while true; do
        read -r -p "$prompt" answer
        answer="${answer:-$default}"
        if [[ -n "$answer" && "$answer" =~ ^[A-Za-z0-9._@:/+-]+$ ]]; then
            printf -v "$var" '%s' "$answer"
            return 0
        fi
        warn "Use only A-Za-z0-9._@:/+- and don't leave it empty"
    done
}

if backup_choice_made; then
    success "S3 backups already configured"
    if [ -n "${S3_BACKUP_BUCKET:-}" ] && [ "${S3_BACKUP_BUCKET}" != "$(get_env_value 'S3_BACKUP_BUCKET')" ]; then
        warn "Ignoring supplied S3_BACKUP_BUCKET: .env already records a backup choice; edit .env to change it"
    elif [ "${S3_BACKUP:-}" = off ] && [ -n "$(get_env_value 'S3_BACKUP_BUCKET')" ]; then
        warn "Ignoring supplied S3_BACKUP=off: .env already has S3_BACKUP_BUCKET; edit .env to change it"
    fi
elif [ "$NON_INTERACTIVE" = true ]; then
    if [ -n "${S3_BACKUP_BUCKET:-}" ]; then
        enable_backups "$S3_BACKUP_BUCKET" "${S3_BACKUP_PREFIX:-memory}" "${S3_BACKUP_REGION:-eu-central-1}"
    else
        set_env_value "S3_BACKUP" "off"
        info "S3 backups off"
    fi
else
    info "Memory can back up its databases and files, encrypted, to an S3 bucket you own."
    while true; do
        read -r -p "Back up to S3? [y/n]: " backup_answer
        case "$backup_answer" in
            [Yy] | [Yy][Ee][Ss]) backup_on=true; break ;;
            [Nn] | [Nn][Oo]) backup_on=false; break ;;
            *) warn "Please answer y or n" ;;
        esac
    done
    if [ "$backup_on" = true ]; then
        ask_backup_value backup_bucket "S3 bucket: "
        ask_backup_value backup_prefix "Key prefix inside the bucket [memory]: " memory
        ask_backup_value backup_region "AWS region [eu-central-1]: " eu-central-1
        enable_backups "$backup_bucket" "$backup_prefix" "$backup_region"
    else
        set_env_value "S3_BACKUP" "off"
        info "S3 backups off"
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
    ask generate_ssh "Generate SSH key pair? [y/N]: "

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
# Claude Code containers
# ─────────────────────────────────────────────────────────────────────

# Modules are switched in the web UI (setup wizard, Server settings); only the
# claude_containers module needs host wiring first: the overlay, its network and DOCKER_GID.
echo ""
echo "─────────────────────────────────────────────────────────────────"
echo "                    Claude Code containers                       "
echo "─────────────────────────────────────────────────────────────────"
echo ""

# Yes only to y/yes; blank is no
ask_yes() {
    local answer
    read -r -p "$1 [y/N]: " answer
    [[ "$answer" =~ ^([Yy]|[Yy][Ee][Ss])$ ]]
}

claude_wired() {
    [[ ":$(get_env_value 'COMPOSE_FILE'):" == *:docker-compose.claude.yaml:* ]]
}

if claude_wired; then
    wire_claude=true
    success "Claude Code containers already wired up"
elif [ "$NON_INTERACTIVE" = true ]; then
    [ "${CLAUDE_CONTAINERS:-off}" = on ] && wire_claude=true || wire_claude=false
elif ask_yes "Wire up Claude Code containers (needs the orchestrator on this host)?"; then
    wire_claude=true
else
    wire_claude=false
fi

if [ "$wire_claude" = true ]; then
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

    # Appended, so an overlay already listed (the proxy one) stays in effect
    compose_file="$(get_env_value 'COMPOSE_FILE')"
    if [ -z "$compose_file" ]; then
        set_env_value "COMPOSE_FILE" "docker-compose.yaml:docker-compose.claude.yaml"
    elif [[ ":$compose_file:" != *:docker-compose.claude.yaml:* ]]; then
        set_env_value "COMPOSE_FILE" "$compose_file:docker-compose.claude.yaml"
    fi
    success "COMPOSE_FILE includes docker-compose.claude.yaml"
    info "Turn on the claude_containers module in the setup wizard or on the Server settings page."

    # docker-compose.claude.yaml declares this network external, so compose fails without it there
    stack="${MEMORY_STACK:-$(get_env_value 'MEMORY_STACK')}"
    api_network="memory-api-${stack:-prod}"
    if ! command -v docker >/dev/null 2>&1; then
        warn "docker not found - create network $api_network before starting the stack"
    elif docker network inspect "$api_network" >/dev/null 2>&1; then
        success "Docker network $api_network exists"
    else
        docker network create "$api_network" >/dev/null
        success "Created Docker network $api_network"
    fi
fi

# Files written by earlier versions of this script may be looser; secrets/previous
# (the keys tools/ops/rotate-keys.sh keeps for backups) is a directory and keeps its mode.
chmod 600 "$ENV_FILE" 2>/dev/null || true
find "$SECRETS_DIR" -maxdepth 1 -type f -exec chmod 600 {} + 2>/dev/null || true

# ─────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "                      Setup Complete!                          "
echo "═══════════════════════════════════════════════════════════════"
echo ""

if [ ! -s "$SECRETS_DIR/voyage_key.txt" ]; then
    info "No Voyage key yet: search is keyword-only until an admin sets it on the settings page."
fi

if [ "$NON_INTERACTIVE" != true ]; then
    echo ""
    info "Next steps:"
    echo "  1. Review configuration: $ENV_FILE"
    echo "  2. Migrate, then start services: docker compose run --rm migrate && docker compose up -d"
    echo "  3. Create the first account at http://localhost:8000/ui/setup with the setup token"
    echo "     from: docker compose logs api | grep 'setup token'"
    echo "     or create an admin: ADD_USER_PASSWORD=yourpass docker compose exec -e ADD_USER_PASSWORD api \\"
    echo "       python tools/add_user.py --email you@example.com --name 'Your Name' --admin"
    echo "  4. Access the app: http://localhost:8000"
    echo ""
fi

if [ "$NON_INTERACTIVE" = true ]; then
    # Supplied values already live in .env and secrets/; compose interpolates the
    # shell environment ahead of .env, so a stale one would override what is stored.
    unset POSTGRES_PASSWORD REDIS_PASSWORD SERVER_URL API_PORT \
        FORWARDED_ALLOW_IPS RATE_LIMIT_TRUSTED_PROXIES
    cd "$PROJECT_DIR"
    # compose builds only missing images, so a re-run on a newer checkout would start the old ones.
    docker compose build --pull
    # The api's healthcheck passes on an empty schema, so migrate first, as update.sh does
    docker compose run --rm migrate
    docker compose up -d --wait api
    docker compose up -d
    if [ -n "${ADMIN_EMAIL:-}" ]; then
        # The password travels through the environment and no API key is made, so
        # neither ends up in argv, logs or output.
        ADD_USER_PASSWORD="$ADMIN_PASSWORD" docker compose exec -T -e ADD_USER_PASSWORD api \
            python tools/add_user.py --email "$ADMIN_EMAIL" --name "$ADMIN_EMAIL" \
            --admin --no-api-key --if-missing > /dev/null
        success "First user ready: $ADMIN_EMAIL"
        exit 0
    fi
    setup_status=$(docker compose exec -T api curl -fsS http://localhost:8000/setup/status) || setup_status=unknown
    if [[ "$setup_status" == *'"needs_account":false'* ]]; then
        exit 0
    fi
    token=""
    # Token lines outlive the account in the container's log, so only an unclaimed
    # instance's is live; and only a terminal shows it, as a captured log would keep it.
    if [ -t 1 ] && [[ "$setup_status" == *'"needs_account":true'* ]]; then
        token=$(docker compose logs api | grep -o 'setup token: [A-Za-z0-9_-]*' | tail -n 1 | cut -d' ' -f3)
    fi
    if [ -n "$token" ]; then
        info "Create the first account at https://$DOMAIN/ui/setup with setup token: $token"
    else
        info "Create the first account at https://$DOMAIN/ui/setup with the setup token from: docker compose logs api | grep 'setup token'"
    fi
    info "The api makes a new token each time it restarts, until the account exists."
fi
