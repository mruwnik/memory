# Memory - Knowledge Base

A self-hosted knowledge base system that ingests, indexes, and provides semantic search over various content types including emails, documents, notes, web pages, and more. Features MCP (Model Context Protocol) integration for AI assistants, team-based access control, and multi-user support.

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

Because of how MCP can't yet handle basic auth,

## User Management

### Create a User

```bash
# Install the package in development mode
pip install -e ".[all]"

# Create a new user
python tools/add_user.py --email user@example.com --password yourpassword --name "Your Name"
```

### Notes synchronisation

You can set up notes to be automatically pushed to a git repo whenever they are modified.
Run the following job to do so:

```bash
python tools/run_celery_task.py notes setup-git-notes --origin ssh://git@github.com/some/repo.git --email bla@ble.com --name <user to send commits>
```

For this to work you need to make sure you have set up the ssh keys in `secrets` (see the README.md
in that folder), and you will need to add the public key that is generated there to your git server.

## Discord integration

If you want to have notifications sent to discord, you'll have to [create a bot for that](https://discord.com/developers/applications).
Once you have the bot's token, run

```bash
python tools/discord_setup.py generate-invite --bot-token <your bot token>
```

to get an url that can be used to connect your Discord bot.

Next you'll have to set at least the following in your `.env` file:

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

When connected via MCP, AI assistants have access to tools organized by domain (core, teams, projects, etc.). Use `tools/list` on the MCP server to see all available tools. Access control is enforced based on the authenticated user's team memberships and roles.

## MCP Client Library for HTML Reports

For creating interactive HTML reports that can call MCP tools, use the included JavaScript client library. This provides a simple API for making authenticated MCP calls from browser-based reports.

### Usage

Include the library in your HTML report:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset='utf-8'>
  <title>My Report</title>
  <script src="/ui/mcp-client.js"></script>
</head>
<body>
  <h1>Interactive Report</h1>
  <div id="results"></div>

  <script>
    // Fetch people and display
    MCP.people.list({ limit: 10 })
      .then(people => {
        const list = people.map(p => p.display_name).join(', ');
        document.getElementById('results').innerHTML = `People: ${list}`;
      })
      .catch(error => {
        console.error('Error:', error);
      });
  </script>
</body>
</html>
```

### Available Methods

**All MCP tools should be supported** with convenient shortcuts organized by category, e.g.:

```javascript
MCP.meta.getUser()
```

If you want to do a manual call, you can do it like this:

```javascript
// Generic call for any method
MCP.call('method_name', { params })

// Batch multiple calls in parallel
MCP.batch([
  { method: 'people_list_all', params: { limit: 10 } },
  { method: 'github_list_entities', params: { type: 'issue', limit: 5 } }
]).then(([people, issues]) => {
  console.log('Got both:', people, issues);
});
```

### Requirements

- Reports must have `allow_scripts=True` in the database (set via the UI or API when creating the report)
- The library uses the `access_token` cookie for authentication
- All MCP calls are made to `/mcp/{method}` endpoints

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

There's also `tools/diagnose.sh` that you can use for this.

## Custom Tasks (Deployment-Specific)

Add scheduled tasks specific to a deployment (deadline reports, activity digests, etc.) without committing them to the main repo. Custom tasks have full access to DB, Discord, GitHub, and all other infrastructure.

### Setup

1. Create a directory for your custom tasks anywhere on the host:
   ```bash
   mkdir /path/to/my/custom_tasks
   ```

2. Add to `.env`:
   ```
   CUSTOM_TASKS_DIR=/path/to/my/custom_tasks
   ```

3. Restart services (`docker compose up -d --build`).

If `CUSTOM_TASKS_DIR` is not set, the feature is completely inert.

### Writing a Task

Each `.py` file in the directory is a self-contained Celery task. Files starting with `_` are ignored (use for templates or to disable a task).

See `custom_tasks/_example.py` and `custom_tasks/_example_manual.py` for full annotated templates.

**Periodic task** (runs on a schedule via Celery Beat):

```python
# my_custom_tasks/deadline_check.py
from celery.schedules import crontab
from memory.common.celery_app import app, register_custom_beat
from memory.common.db.connection import make_session

# register_custom_beat does two things:
#   1. Builds the task name: "custom_tasks.deadline_check.run"
#   2. Adds a Celery Beat schedule entry so it runs automatically
# First arg must match this file's name (without .py).
TASK_NAME = register_custom_beat(
    "deadline_check",
    crontab(hour=9, minute=0, day_of_week="mon-fri"),
)

@app.task(name=TASK_NAME)
def run():
    with make_session() as session:
        ...
    return {"status": "success"}
```

**Manual/on-demand task** (no schedule, triggered explicitly):

```python
# my_custom_tasks/generate_report.py
from memory.common.celery_app import app, custom_task_name

TASK_NAME = custom_task_name("generate_report")

@app.task(name=TASK_NAME)
def run():
    ...
```

Trigger manually:
```bash
celery -A memory.workers.ingest call custom_tasks.generate_report.run
```

### How It Works

1. At startup, `celery_app.py` calls `load_custom_tasks()` which scans `CUSTOM_TASKS_DIR`
2. Each `.py` file (not starting with `_`) is imported via `importlib`
3. Importing executes `@app.task` registration and any `register_custom_beat()` calls
4. All custom tasks route to the `custom` queue (`memory-custom`)
5. One broken file won't prevent others from loading — errors are logged per-file

## Contributing

This is a personal knowledge base system. Feel free to fork and adapt for your own use cases.
