# Memory - Personal Knowledge Base

A personal knowledge base system that ingests, indexes, and provides semantic search over various content types including emails, documents, notes, web pages, and more. Features MCP (Model Context Protocol) integration for AI assistants to access and learn from your personal data.

## Features

- **Multi-modal Content Ingestion**: Process emails, documents, ebooks, comics, web pages, and more
- **Semantic Search**: Vector-based search across all your content with relevance scoring
- **MCP Integration**: Direct integration with AI assistants via Model Context Protocol
- **Observation System**: AI assistants can record and search long-term observations about user preferences and patterns
- **Note Taking**: Create and organize markdown notes with full-text search
- **User Management**: Multi-user support with authentication
- **RESTful API**: Complete API for programmatic access
- **Real-time Processing**: Celery-based background processing for content ingestion

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for tools)

### 1. Start the Development Environment

```bash
# Clone the repository and navigate to it
cd memory

# Start the core services (PostgreSQL, RabbitMQ, Qdrant)
./dev.sh
```

This will:

- Start PostgreSQL (exposed on port 5432)
- Start RabbitMQ with management interface
- Start Qdrant vector database
- Initialize the database schema

It will also generate secrets in `secrets` and make a basic `.env` file for you.

### 2. Start the Full Application

```bash
# Start all services including API and workers
docker-compose up -d

# Check that services are healthy
docker-compose ps
```

The API will be available at `http://localhost:8000`

The is also an admin interface at `http://localhost:8000/admin` where you can see what the database
contains.

## User Management

### Create a User

```bash
# Install the package in development mode
pip install -e ".[all]"

# Create a new user
python tools/add_user.py --email user@example.com --password yourpassword --name "Your Name"
```

### Authentication

The API uses session-based authentication. Login via:

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "yourpassword"}'
```

This returns a session ID that should be included in subsequent requests as the `X-Session-ID` header.

## MCP Proxy Setup

Since MCP doesn't support basic authentication, use the included proxy for AI assistants that need to connect:

### Start the Proxy

```bash
python tools/simple_proxy.py \
  --remote-server http://localhost:8000 \
  --email user@example.com \
  --password yourpassword \
  --port 8080
```

### Configure Your AI Assistant

Point your MCP-compatible AI assistant to `http://localhost:8080` instead of the direct API endpoint. The proxy will:

- Handle authentication automatically
- Forward all requests to the main API
- Add the session header to each request

### Example MCP Configuration

For Claude Desktop or other MCP clients, add to your configuration:

```json
{
  "mcpServers": {
    "memory": {
        "type": "streamable-http",
        "url": "http://localhost:8001/mcp",
    }
  }
}
```

## Available MCP Tools

When connected via MCP, AI assistants have access to:

- `search_knowledge_base()` - Search your stored content
- `search_observations()` - Search recorded observations about you
- `observe()` - Record new observations about your preferences/behavior
- `create_note()` - Create and save notes
- `note_files()` - List existing notes
- `fetch_file()` - Read file contents
- `get_all_tags()` - Get all content tags
- `get_all_subjects()` - Get observation subjects

## Content Ingestion

### Via Workers

Content is processed asynchronously by Celery workers. Supported formats include:

- PDFs, DOCX, TXT files
- Emails (mbox, EML formats)
- Web pages (HTML)
- Ebooks (EPUB, PDF)
- Images with OCR
- And more...

## Development

### Environment Setup

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with auto-reload
RELOAD=true python -m memory.api.app
```

### Architecture

- **FastAPI**: REST API and MCP server
- **PostgreSQL**: Primary database for metadata and users
- **Qdrant**: Vector database for semantic search
- **RabbitMQ**: Message queue for background processing
- **Celery**: Distributed task processing
- **SQLAdmin**: Admin interface for database management

### Configuration

Key environment variables:

- `FILE_STORAGE_DIR`: Where uploaded files are stored
- `DB_HOST`, `DB_PORT`: Database connection
- `QDRANT_HOST`: Vector database connection
- `RABBITMQ_HOST`: Message queue connection

See `docker-compose.yaml` for full configuration options.

## Security Notes

- Never expose the main API directly to the internet without proper authentication
- Use the proxy for MCP connections to handle authentication securely
- Store secrets in the `secrets/` directory (see `docker-compose.yaml`)
- The application runs with minimal privileges in Docker containers

## Troubleshooting

### Common Issues

1. **Database connection errors**: Ensure PostgreSQL is running and accessible
2. **Vector search not working**: Check that Qdrant is healthy
3. **Background processing stalled**: Verify RabbitMQ and Celery workers are running
4. **MCP connection issues**: Use the proxy instead of direct API access

### Logs

```bash
# View API logs
docker-compose logs -f api

# View worker logs  
docker-compose logs -f worker

# View all logs
docker-compose logs -f
```

## Contributing

This is a personal knowledge base system. Feel free to fork and adapt for your own use cases.
