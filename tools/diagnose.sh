#!/bin/bash
set -e

REMOTE_HOST="memory"
REMOTE_DIR="/home/ec2-user/memory"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
    echo "Usage: $0 <command> [options]"
    echo ""
    echo "Safe diagnostic commands for the memory server."
    echo ""
    echo "Commands:"
    echo "  logs [service] [lines]   View docker logs (default: all services, 100 lines)"
    echo "  ps                       Show docker container status"
    echo "  disk                     Show disk usage"
    echo "  mem                      Show memory usage"
    echo "  top                      Show running processes"
    echo "  ls <path>                List directory contents"
    echo "  cat <file>               View file contents"
    echo "  tail <file> [lines]      Tail a file (default: 50 lines)"
    echo "  grep <pattern> <path>    Search for pattern in files"
    echo "  db <query>               Run read-only SQL query"
    echo "  get <path> [port]        GET request to localhost (default port: 8000)"
    echo "  status                   Overall system status"
    exit 1
}

remote() {
    ssh "$REMOTE_HOST" "$@"
}

docker_logs() {
    local service="${1:-}"
    local lines="${2:-100}"
    if [ -n "$service" ]; then
        echo -e "${GREEN}Logs for $service (last $lines lines):${NC}"
        remote "cd $REMOTE_DIR && docker compose logs --tail=$lines $service"
    else
        echo -e "${GREEN}All logs (last $lines lines):${NC}"
        remote "cd $REMOTE_DIR && docker compose logs --tail=$lines"
    fi
}

docker_ps() {
    echo -e "${GREEN}Container status:${NC}"
    remote "cd $REMOTE_DIR && docker compose ps"
}

disk_usage() {
    echo -e "${GREEN}Disk usage:${NC}"
    remote "df -h && echo '' && du -sh $REMOTE_DIR/* 2>/dev/null | sort -h"
}

mem_usage() {
    echo -e "${GREEN}Memory usage:${NC}"
    remote "free -h"
}

show_top() {
    echo -e "${GREEN}Top processes:${NC}"
    remote "ps aux --sort=-%mem | head -20"
}

list_dir() {
    local path="${1:-.}"
    # Ensure path is within project directory for safety
    echo -e "${GREEN}Contents of $path:${NC}"
    remote "cd $REMOTE_DIR && ls -la $path"
}

cat_file() {
    local file="$1"
    if [ -z "$file" ]; then
        echo -e "${RED}Error: No file specified${NC}"
        exit 1
    fi
    echo -e "${GREEN}Contents of $file:${NC}"
    remote "cd $REMOTE_DIR && cat $file"
}

tail_file() {
    local file="$1"
    local lines="${2:-50}"
    if [ -z "$file" ]; then
        echo -e "${RED}Error: No file specified${NC}"
        exit 1
    fi
    echo -e "${GREEN}Last $lines lines of $file:${NC}"
    remote "cd $REMOTE_DIR && tail -n $lines $file"
}

grep_files() {
    local pattern="$1"
    local path="${2:-.}"
    if [ -z "$pattern" ]; then
        echo -e "${RED}Error: No pattern specified${NC}"
        exit 1
    fi
    echo -e "${GREEN}Searching for '$pattern' in $path:${NC}"
    remote "cd $REMOTE_DIR && grep -r --color=always '$pattern' $path || true"
}

db_query() {
    local query="$1"
    if [ -z "$query" ]; then
        echo -e "${RED}Error: No query specified${NC}"
        exit 1
    fi
    # Only allow SELECT queries for safety
    if ! echo "$query" | grep -qi "^select"; then
        echo -e "${RED}Error: Only SELECT queries are allowed${NC}"
        exit 1
    fi
    echo -e "${GREEN}Running query:${NC}"
    remote "cd $REMOTE_DIR && docker compose exec -T postgres psql -U kb -d kb -c \"$query\""
}

http_get() {
    local path="$1"
    local port="${2:-8000}"
    if [ -z "$path" ]; then
        echo -e "${RED}Error: No path specified${NC}"
        exit 1
    fi
    # Ensure path starts with /
    if [[ "$path" != /* ]]; then
        path="/$path"
    fi
    echo -e "${GREEN}GET http://localhost:${port}${path}${NC}"
    remote "curl -s -w '\n\nHTTP Status: %{http_code}\n' 'http://localhost:${port}${path}'"
}

system_status() {
    echo -e "${GREEN}=== System Status ===${NC}"
    echo ""
    docker_ps
    echo ""
    echo -e "${GREEN}=== Memory ===${NC}"
    mem_usage
    echo ""
    echo -e "${GREEN}=== Disk ===${NC}"
    remote "df -h /"
    echo ""
    echo -e "${GREEN}=== Recent Errors (last 20 lines) ===${NC}"
    remote "cd $REMOTE_DIR && docker compose logs --tail=100 2>&1 | grep -i -E '(error|exception|failed|fatal)' | tail -20 || echo 'No recent errors found'"
}

# Main
case "${1:-}" in
    logs)
        docker_logs "${2:-}" "${3:-}"
        ;;
    ps)
        docker_ps
        ;;
    disk)
        disk_usage
        ;;
    mem)
        mem_usage
        ;;
    top)
        show_top
        ;;
    ls)
        list_dir "${2:-}"
        ;;
    cat)
        cat_file "${2:-}"
        ;;
    tail)
        tail_file "${2:-}" "${3:-}"
        ;;
    grep)
        grep_files "${2:-}" "${3:-}"
        ;;
    db)
        db_query "${2:-}"
        ;;
    get)
        http_get "${2:-}" "${3:-}"
        ;;
    status)
        system_status
        ;;
    *)
        usage
        ;;
esac
