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

docker volume create memory_file_storage
docker run --rm -v memory_file_storage:/data busybox chown -R 1000:1000 /data

POSTGRES_PASSWORD=543218ZrHw8Pxbs3YXzaVHq8YKVHwCj6Pz8RQkl8
echo $POSTGRES_PASSWORD > secrets/postgres_password.txt

# Create a temporary docker-compose override file to expose PostgreSQL
echo -e "${YELLOW}Creating docker-compose override to expose PostgreSQL...${NC}"
if [ ! -f docker-compose.override.yml ]; then
    cat > docker-compose.override.yml << EOL
version: "3.9"
services:
  qdrant:
    ports:
      - "6333:6333"

  postgres:
    ports:
      # PostgreSQL port for local Celery result backend
      - "15432:5432"

  rabbitmq:
    ports:
      # UI only on localhost
      - "15672:15672"
      # AMQP port for local Celery clients (for local workers)
      - "15673:5672"
EOL
fi

if [ ! -f .env ]; then
    echo $POSTGRES_PASSWORD > .env
    cat >> .env << EOL
CELERY_BROKER_PASSWORD=543218ZrHw8Pxbs3YXzaVHq8YKVHwCj6Pz8RQkl8

RABBITMQ_HOST=localhost
QDRANT_HOST=localhost
DB_HOST=localhost

VOYAGE_API_KEY=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

DB_PORT=15432
RABBITMQ_PORT=15673
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