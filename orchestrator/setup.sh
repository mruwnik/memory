#!/bin/bash
set -e

# Claude Session Orchestrator Setup Script
# Run this on the server to install the orchestrator

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/claude-orchestrator"
SOCKET_DIR="/var/run/claude-sessions"
LOG_DIR="/var/log/claude-sessions"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== Claude Session Orchestrator Setup ===${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root (sudo)${NC}"
    exit 1
fi

# Check Docker is available
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Docker is not installed${NC}"
    exit 1
fi

echo -e "${YELLOW}1. Creating directories...${NC}"
mkdir -p "$INSTALL_DIR"
mkdir -p "$SOCKET_DIR"
chmod 755 "$SOCKET_DIR"
mkdir -p "$LOG_DIR"
chmod 755 "$LOG_DIR"

echo -e "${YELLOW}2. Copying files...${NC}"
cp "$SCRIPT_DIR/orchestrator.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/orchestrator.py"

# Copy Dockerfiles for claude-cloud image
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
mkdir -p "$INSTALL_DIR/docker/claude-cloud"
cp "$PROJECT_DIR/docker/claude-cloud/Dockerfile" "$INSTALL_DIR/docker/claude-cloud/"
cp "$PROJECT_DIR/docker/claude-cloud/Dockerfile.happy" "$INSTALL_DIR/docker/claude-cloud/" 2>/dev/null || true
cp "$PROJECT_DIR/docker/claude-cloud/entrypoint.sh" "$INSTALL_DIR/docker/claude-cloud/"
chmod +x "$INSTALL_DIR/docker/claude-cloud/entrypoint.sh"
echo "  Copied docker/claude-cloud/ to $INSTALL_DIR/docker/claude-cloud/"

# Remove old images so they get rebuilt with new Dockerfiles
echo "  Removing old claude-cloud images (will rebuild on first use)..."
docker rmi claude-cloud:latest 2>/dev/null && echo "    Removed claude-cloud:latest" || true
docker rmi claude-cloud-happy:latest 2>/dev/null && echo "    Removed claude-cloud-happy:latest" || true

echo -e "${YELLOW}3. Setting up Python virtual environment...${NC}"
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

echo -e "${YELLOW}4. Installing systemd service...${NC}"
cp "$SCRIPT_DIR/claude-orchestrator.service" /etc/systemd/system/
systemctl daemon-reload

echo -e "${YELLOW}5. Creating external networks (if they don't exist)...${NC}"
docker network create memory-api-dev 2>/dev/null || echo "  memory-api-dev already exists"
docker network create memory-api-prod 2>/dev/null || echo "  memory-api-prod already exists"

echo -e "${YELLOW}6. Enabling and starting service...${NC}"
systemctl enable claude-orchestrator
systemctl restart claude-orchestrator

echo -e "${GREEN}=== Setup complete! ===${NC}"
echo ""
echo "Service status:"
systemctl status claude-orchestrator --no-pager || true
echo ""
echo -e "Socket: ${GREEN}$SOCKET_DIR/orchestrator.sock${NC}"
echo ""
echo "Useful commands:"
echo "  systemctl status claude-orchestrator    # Check status"
echo "  journalctl -u claude-orchestrator -f    # Follow logs"
echo "  systemctl restart claude-orchestrator   # Restart"
echo ""
echo "Test the orchestrator:"
echo '  echo '"'"'{"action": "ping"}'"'"' | nc -U /var/run/claude-sessions/orchestrator.sock'
