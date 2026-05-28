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
mkdir -p secrets
echo "$POSTGRES_PASSWORD" > secrets/postgres_password.txt

# Host-side dev needs the data services published on localhost. They are
# NOT exposed by docker-compose.yaml — create a docker-compose.override.yml
# (gitignored) publishing postgres→15432, redis→16379, qdrant→6333.
# See the "Local development" section of README.md for an example.

if [ ! -f .env ]; then
    cat > .env << EOL
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
QDRANT_HOST=localhost
DB_HOST=localhost
REDIS_HOST=localhost

VOYAGE_API_KEY=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

DB_PORT=15432
REDIS_PORT=16379
EOL
fi

# Start infrastructure services. The api/worker/migrate containers are not
# started here — run `docker compose up -d` after this script when you want
# the full stack, or run the API/workers locally against these services.
echo -e "${GREEN}Starting docker containers...${NC}"
docker compose up -d postgres redis qdrant

# Wait for PostgreSQL to be ready
echo -e "${YELLOW}Waiting for PostgreSQL to be ready...${NC}"
for i in {1..30}; do
    if docker compose exec -T postgres pg_isready -U kb > /dev/null 2>&1; then
        echo -e "${GREEN}PostgreSQL is ready!${NC}"
        break
    fi
    echo -n "."
    sleep 1
done

echo -e "${GREEN}Development environment is ready!${NC}"
echo -e "${YELLOW}Apply DB migrations before first use:${NC}"
echo -e "${YELLOW}  alembic -c db/migrations/alembic.ini upgrade head${NC}"
echo -e "${YELLOW}PostgreSQL: localhost:15432  (user=kb, db=kb, password in secrets/postgres_password.txt)${NC}"
echo -e "${YELLOW}Redis:      localhost:16379${NC}"
echo -e "${YELLOW}Qdrant:     localhost:6333${NC}"
echo ""
echo -e "${GREEN}To bring up the API and workers as well, run:${NC}"
echo -e "${YELLOW}docker compose up -d${NC}"
echo ""
echo -e "${GREEN}To stop the environment, run:${NC}"
echo -e "${YELLOW}docker compose down${NC}"