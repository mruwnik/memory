#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load .env if it exists
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

# Configuration (can be overridden in .env)
REMOTE_HOST="${DEPLOY_HOST:-memory}"
REMOTE_DIR="${DEPLOY_DIR:-/home/ec2-user/memory}"
DEFAULT_BRANCH="${DEPLOY_BRANCH:-master}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
    echo "Usage: $0 <command> [options]"
    echo ""
    echo "Commands:"
    echo "  sync              Rsync local code to server"
    echo "  pull [branch]     Git checkout and pull (default: master)"
    echo "  restart           Restart docker services"
    echo "  deploy [branch]   Pull + restart"
    echo "  run <command>     Run command on server (with venv activated)"
    exit 1
}

sync_code() {
    echo -e "${GREEN}Syncing code to $REMOTE_HOST...${NC}"

    rsync -avz --delete \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='*.pyo' \
        --exclude='.git' \
        --exclude='memory_files' \
        --exclude='secrets' \
        --exclude='Books' \
        --exclude='clean_books' \
        --exclude='.env' \
        --exclude='venv' \
        --exclude='.venv' \
        --exclude='*.egg-info' \
        --exclude='node_modules' \
        --exclude='.DS_Store' \
        --exclude='docker-compose.override.yml' \
        --exclude='.pytest_cache' \
        --exclude='.mypy_cache' \
        --exclude='.ruff_cache' \
        --exclude='htmlcov' \
        --exclude='.coverage' \
        --exclude='*.log' \
        "$PROJECT_DIR/src" \
        "$PROJECT_DIR/tests" \
        "$PROJECT_DIR/tools" \
        "$PROJECT_DIR/db" \
        "$PROJECT_DIR/docker" \
        "$PROJECT_DIR/frontend" \
        "$PROJECT_DIR/requirements" \
        "$PROJECT_DIR/setup.py" \
        "$PROJECT_DIR/docker-compose.yaml" \
        "$PROJECT_DIR/pytest.ini" \
        "$REMOTE_HOST:$REMOTE_DIR/"

    echo -e "${GREEN}Sync complete!${NC}"
}

git_pull() {
    local branch="${1:-$DEFAULT_BRANCH}"
    echo -e "${GREEN}Pulling branch '$branch' on $REMOTE_HOST...${NC}"

    ssh "$REMOTE_HOST" "cd $REMOTE_DIR && \
        git stash --quiet 2>/dev/null || true && \
        git fetch origin && \
        git checkout $branch && \
        git pull origin $branch"

    echo -e "${GREEN}Pull complete!${NC}"
}

restart_services() {
    echo -e "${GREEN}Restarting services on $REMOTE_HOST...${NC}"

    ssh "$REMOTE_HOST" "cd $REMOTE_DIR && docker compose up --build -d"

    echo -e "${GREEN}Services restarted!${NC}"
}

deploy() {
    local branch="${1:-$DEFAULT_BRANCH}"
    git_pull "$branch"
    restart_services
}

run_remote() {
    if [ $# -eq 0 ]; then
        echo -e "${RED}Error: No command specified${NC}"
        exit 1
    fi
    ssh "$REMOTE_HOST" "cd $REMOTE_DIR && source venv/bin/activate && $*"
}

# Main
case "${1:-}" in
    sync)
        sync_code
        ;;
    pull)
        git_pull "${2:-}"
        ;;
    restart)
        restart_services
        ;;
    deploy)
        deploy "${2:-}"
        ;;
    run)
        shift
        run_remote "$@"
        ;;
    *)
        usage
        ;;
esac
