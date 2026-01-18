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
    echo "  orchestrator      Setup/update the Claude session orchestrator"
    echo "  session [opts]    Run a claude-cloud session (syncs + rebuilds first)"
    echo ""
    echo "Session options:"
    echo "  --snapshot PATH             Path to snapshot tarball (on server)"
    echo "  --environment NAME          Name of environment volume"
    echo "  --repo URL                  Git repository URL to clone"
    echo "  --github-token TOKEN        GitHub token for repo access (read)"
    echo "  --github-token-write TOKEN  GitHub token for differ (push/PR)"
    echo "  --cmd COMMAND               Command to run instead of claude (e.g. /verify-setup.sh)"
    echo "  --no-rebuild                Skip image rebuild"
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
        "$PROJECT_DIR/orchestrator" \
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

setup_orchestrator() {
    echo -e "${GREEN}Setting up Claude session orchestrator on $REMOTE_HOST...${NC}"

    # Sync orchestrator files first
    rsync -avz \
        "$PROJECT_DIR/orchestrator/" \
        "$REMOTE_HOST:$REMOTE_DIR/orchestrator/"

    # Run setup script
    ssh -t "$REMOTE_HOST" "sudo bash $REMOTE_DIR/orchestrator/setup.sh"

    echo -e "${GREEN}Orchestrator setup complete!${NC}"
}

run_session() {
    local snapshot=""
    local environment=""
    local repo_url=""
    local github_token=""
    local github_token_write=""
    local custom_cmd=""
    local rebuild=true

    # Parse options
    while [[ $# -gt 0 ]]; do
        case $1 in
            --snapshot)
                snapshot="$2"
                shift 2
                ;;
            --environment)
                environment="$2"
                shift 2
                ;;
            --repo)
                repo_url="$2"
                shift 2
                ;;
            --github-token)
                github_token="$2"
                shift 2
                ;;
            --github-token-write)
                github_token_write="$2"
                shift 2
                ;;
            --cmd)
                custom_cmd="$2"
                shift 2
                ;;
            --no-rebuild)
                rebuild=false
                shift
                ;;
            *)
                echo -e "${RED}Unknown session option: $1${NC}"
                usage
                ;;
        esac
    done

    # Sync docker/claude-cloud files
    echo -e "${GREEN}Syncing claude-cloud docker files to $REMOTE_HOST...${NC}"
    rsync -avz \
        "$PROJECT_DIR/docker/claude-cloud/" \
        "$REMOTE_HOST:$REMOTE_DIR/docker/claude-cloud/"

    # Build the image if requested
    if [[ "$rebuild" == "true" ]]; then
        echo -e "${GREEN}Rebuilding claude-cloud image...${NC}"
        ssh "$REMOTE_HOST" "cd $REMOTE_DIR && docker build -t claude-cloud:latest -f docker/claude-cloud/Dockerfile ."
    fi

    # Build docker run command
    echo -e "${GREEN}Running session...${NC}"
    local docker_cmd="docker run --rm -it"

    # Add custom command if specified
    if [[ -n "$custom_cmd" ]]; then
        docker_cmd="$docker_cmd -e CLAUDE_EXECUTABLE='$custom_cmd'"
    fi

    if [[ -n "$snapshot" ]]; then
        # Use unique temp file to avoid race condition with concurrent sessions
        local temp_snapshot="/tmp/snapshot-$$.tar.gz"
        ssh "$REMOTE_HOST" "cp '$snapshot' '$temp_snapshot'"
        docker_cmd="$docker_cmd -v $temp_snapshot:/snapshot/snapshot.tar.gz:ro"
        # Note: temp file cleanup is handled by the next run or system tmpfs cleanup
    fi

    if [[ -n "$environment" ]]; then
        docker_cmd="$docker_cmd -v claude-env-$environment:/home/claude"
    fi

    if [[ -n "$repo_url" ]]; then
        docker_cmd="$docker_cmd -e GIT_REPO_URL='$repo_url'"
    fi

    if [[ -n "$github_token" ]]; then
        docker_cmd="$docker_cmd -e GITHUB_TOKEN='$github_token'"
    fi

    if [[ -n "$github_token_write" ]]; then
        docker_cmd="$docker_cmd -e GITHUB_TOKEN_WRITE='$github_token_write'"
    fi

    docker_cmd="$docker_cmd claude-cloud:latest"

    # Run the session
    ssh "$REMOTE_HOST" "$docker_cmd"
    return $?
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
    orchestrator)
        setup_orchestrator
        ;;
    session)
        shift
        run_session "$@"
        ;;
    *)
        usage
        ;;
esac
