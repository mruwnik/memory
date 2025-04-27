#!/usr/bin/env bash
set -eo pipefail

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting development environment for Memory Knowledge Base...${NC}"

# Get the directory of the script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Create a temporary docker-compose override file to expose PostgreSQL
echo -e "${YELLOW}Creating docker-compose override to expose PostgreSQL...${NC}"
if [ ! -f docker-compose.override.yml ]; then
    cat > docker-compose.override.yml << EOL
version: "3.9"
services:
  postgres:
    ports:
      - "5432:5432"
EOL
fi

# Start the containers
echo -e "${GREEN}Starting docker containers...${NC}"
docker-compose up -d postgres rabbitmq qdrant

# Wait for PostgreSQL to be ready
echo -e "${YELLOW}Waiting for PostgreSQL to be ready...${NC}"
for i in {1..30}; do
    if docker-compose exec postgres pg_isready -U kb > /dev/null 2>&1; then
        echo -e "${GREEN}PostgreSQL is ready!${NC}"
        break
    fi
    echo -n "."
    sleep 1
done

# Initialize the database if needed
echo -e "${YELLOW}Checking if database needs initialization...${NC}"
if ! docker-compose exec postgres psql -U kb -d kb -c "SELECT 1 FROM information_schema.tables WHERE table_name = 'source_item'" | grep -q 1; then
    echo -e "${GREEN}Initializing database from schema.sql...${NC}"
    docker-compose exec postgres psql -U kb -d kb -f /docker-entrypoint-initdb.d/schema.sql
else
    echo -e "${GREEN}Database already initialized.${NC}"
fi

echo -e "${GREEN}Development environment is ready!${NC}"
echo -e "${YELLOW}PostgreSQL is available at localhost:5432${NC}"
echo -e "${YELLOW}Username: kb${NC}"
echo -e "${YELLOW}Password: (check secrets/postgres_password.txt)${NC}"
echo -e "${YELLOW}Database: kb${NC}"
echo ""
echo -e "${GREEN}To stop the environment, run:${NC}"
echo -e "${YELLOW}docker-compose down${NC}" 