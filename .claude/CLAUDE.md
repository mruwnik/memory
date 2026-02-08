# Memory - Personal Knowledge Base

## What This Project Is

Memory is a **self-hosted, privacy-first knowledge base** supporting both personal and collaborative use. It ingests, indexes, and provides semantic search across your digital life - emails, documents, notes, web pages, ebooks, Discord messages, photos, and more - while keeping all data fully under your control.

### Core Purpose

- **Semantic search** across personal and shared content using vector embeddings
- **AI assistant integration** via Model Context Protocol (MCP) so Claude/other assistants can access your knowledge
- **Teams & Projects** for collaborative knowledge management with role-based access
- **Observation system** for AI assistants to record and recall patterns about your preferences
- **Privacy-first** - self-hosted, all data under your control

## Architecture Overview

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Frontend  │────▶│   FastAPI   │────▶│  PostgreSQL │
│  (React 19) │     │     API     │     │   (metadata)│
└─────────────┘     └──────┬──────┘     └─────────────┘
                          │
                    ┌─────┴─────┐
                    ▼           ▼
              ┌─────────┐  ┌─────────┐
              │  Qdrant │  │ Celery  │
              │(vectors)│  │ Workers │
              └─────────┘  └────┬────┘
                               │
                          ┌────┴────┐
                          │  Redis  │
                          └─────────┘
```

### Key Components

| Component | Purpose |
|-----------|---------|
| **FastAPI** | REST API + MCP server for AI assistants |
| **PostgreSQL** | Stores metadata, users, teams, projects, content, observations |
| **Qdrant** | Vector database for semantic similarity search |
| **Celery + Redis** | Async background processing for content ingestion |
| **Redis** | Message broker, session cache, LLM usage tracking |
| **React Frontend** | Search interface and dashboard |

## Project Structure

```
src/memory/
├── api/                    # FastAPI application
│   ├── app.py              # Main entry point
│   ├── search/             # Search implementation (embeddings, BM25, HyDE, reranking)
│   ├── MCP/                # Model Context Protocol tools
│   ├── projects.py         # Project management API
│   └── teams.py            # Team management API
├── common/                 # Shared code
│   ├── db/models/          # SQLAlchemy ORM models
│   ├── access_control.py   # Role-based access control
│   ├── celery_app.py       # Task queue configuration
│   ├── qdrant.py           # Vector DB client
│   └── settings.py         # Environment configuration
├── parsers/                # Content type parsers (email, ebook, comics, etc.)
└── workers/tasks/          # Celery tasks for content processing

frontend/                   # React 19 + TypeScript + Vite
db/migrations/              # Alembic database migrations
tools/                      # CLI utilities (add_user, run tasks, etc.)
```

## Development Environment

### Python

Use the project's virtual environment:

```bash
workon memory
# or run directly:
~/.virtualenvs/memory/bin/python
# Run tests
~/.virtualenvs/memory/bin/pytest
```

### Service Ports (from docker-compose.override.yml)

| Service | Local Port | Container Port |
|---------|------------|----------------|
| PostgreSQL | 15432 | 5432 |
| Redis | 16379 | 6379 |
| Qdrant | 6333 | 6333 |
| API | 8000 | 8000 |

### Deployment

Use `tools/deploy.sh` to manage the production server:

```bash
./tools/deploy.sh deploy          # git pull + restart services (most common)
./tools/deploy.sh sync            # rsync local code (bypasses git)
./tools/deploy.sh restart         # restart docker without pulling
./tools/deploy.sh run "<command>" # run command on server
```

### Common Commands

These should be run via the deployment tool.

```bash
# Start services
docker-compose up -d --build

# View logs
docker-compose logs -f api
docker-compose logs -f worker

# Run a Celery task manually
python tools/run_celery_task.py <queue> <task-name> [args]

# Create a user
python tools/add_user.py --email user@example.com --password pass --name "Name"

# Database migrations
alembic upgrade head
alembic revision --autogenerate -m "description"
```

## Search Pipeline

The search system combines multiple strategies:

1. **Vector Search** - OpenAI embeddings (text-embedding-3-small, 1536d) in Qdrant
2. **BM25 Full-text** - PostgreSQL tsvector for keyword matching
3. **HyDE** - Hypothetical Document Embeddings for query expansion
4. **Query Analysis** - LLM understands search intent (via Claude Haiku)
5. **Reranking** - Scores consider recency, popularity, title matches

Results are merged using Reciprocal Rank Fusion (RRF).

**Access Control in Search**: Filters are applied at multiple layers (Qdrant vector queries, BM25 full-text, and final result merge) for defense in depth. Users only see content they have access to based on their team/project memberships.

## MCP Tools (for AI Assistants)

The full list of available MCP tools can be fetched via `tools/list` on the MCP server. Tools are organized by domain (core, teams, projects, etc.) and respect access control based on the authenticated user's permissions.

## Content Types Supported

| Type | Parser Location | Celery Queue |
|------|-----------------|--------------|
| Email | `parsers/email.py` | `email` |
| Ebooks (EPUB/PDF) | `parsers/ebook.py` | `ebook` |
| Blog/Web pages | `parsers/blogs.py` | `blogs` |
| Comics (SMBC, XKCD) | `parsers/comics.py` | `comics` |
| Discord messages | `workers/tasks/discord.py` | `discord` |
| Forum posts | `parsers/lesswrong.py` | `forums` |
| Notes | `workers/tasks/notes.py` | `notes` |
| Photos/Images | `workers/tasks/content_processing.py` | `generic` |

## Adding a New Celery Worker/Queue

When adding a new worker type or queue, update these locations:

### Checklist

1. **Create the task module**: `src/memory/workers/tasks/<name>.py`
   - Define tasks with `@app.task(name=TASK_NAME)`

2. **Register in celery_app.py** (`src/memory/common/celery_app.py`):
   - Add `<NAME>_ROOT = "memory.workers.tasks.<name>"` constant
   - Add task name constants (e.g., `SYNC_<NAME> = f"{<NAME>_ROOT}.sync_<name>"`)
   - Add routing rule: `f"{<NAME>_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-<queue>"}`
   - If periodic, add to `app.conf.beat_schedule`

3. **Export tasks** in `src/memory/workers/tasks/__init__.py`:
   - Import the task module

4. **Update docker-compose.yaml**:
   - Add queue name to worker service `QUEUES` env var (line ~240)
   - Format: comma-separated list like `"email,blogs,<newqueue>"`

5. **Update worker Dockerfile** (`docker/workers/Dockerfile`):
   - Add queue to default `QUEUES` env var (line ~47)

### Example: Adding a "reports" queue

```python
# celery_app.py
REPORTS_ROOT = "memory.workers.tasks.reports"
GENERATE_REPORT = f"{REPORTS_ROOT}.generate_report"

# In task_routes:
f"{REPORTS_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-reports"},

# If periodic, in beat_schedule:
"generate-daily-report": {
    "task": GENERATE_REPORT,
    "schedule": crontab(hour=6, minute=0),
},
```

```yaml
# docker-compose.yaml worker service
QUEUES: "backup,blogs,...,reports"
```

### Alternative: Reuse existing queues

To avoid queue proliferation, route to an existing queue:

- `maintenance` - cleanup, metrics, admin tasks
- `scheduler` - scheduled/timed tasks
- `generic` - catch-all for misc processing

## Database Models

Key models in `src/memory/common/db/models/`:

- `source_item.py` - Base content model (sha256, tags, mime_type, project_id)
- `source_items.py` - Specific types (MailMessage, BlogPost, Book, etc.)
- `sources.py` - Person, Team, Project models with membership relationships
- `observations.py` - AgentObservation for AI-recorded insights
- `users.py` - User, HumanUser, BotUser, UserSession, APIKey

## Environment Variables

Key settings (see `src/memory/common/settings.py`):

- `OPENAI_API_KEY` - For embeddings and LLM responses
- `ANTHROPIC_API_KEY` - For Claude (query analysis, reranking)
- `FILE_STORAGE_DIR` - Where uploaded files are stored
- `ENABLE_BM25_SEARCH`, `ENABLE_HYDE_EXPANSION`, `ENABLE_RERANKING`, `ENABLE_QUERY_ANALYSIS` - Search feature toggles

## Testing

```bash
/home/ec2-user/memory/venv/bin/pytest
```

### Test Fixtures (`tests/conftest.py`)

Use the existing DB and MCP fixtures instead of mocking. They provide real database sessions with automatic cleanup:

| Fixture | Description |
|---------|-------------|
| `db_session` | Real DB session, tables truncated after each test |
| `admin_user` | `HumanUser` with `scopes=["*"]`, email `admin@example.com` |
| `regular_user` | `HumanUser` with `scopes=["teams"]`, email `regular@example.com` |
| `admin_session` | `UserSession` for `admin_user` (token: `"admin-session-token"`) |
| `user_session` | `UserSession` for `regular_user` (token: `"test-session-token"`) |

For MCP tool tests, use `mcp_auth_context` (a context manager, not a fixture) to set auth state:

```python
from tests.conftest import mcp_auth_context

async def test_some_tool(db_session, admin_session):
    with mcp_auth_context(admin_session.id):
        result = await some_tool.fn(arg="value")
    assert result["authenticated"] is True
```

Prefer these over `MagicMock`/`@patch` for testing MCP tools — they exercise the real auth and DB code paths.

## Access Control

Content visibility is managed through a User → Person → Team → Project hierarchy.

### Entity Relationships

```
User (auth) ──── Person (identity) ──── Team ──── Project ──── Content
              1:0..1              M:N         M:N
```

- **User**: Authentication identity (email, password, API keys, scopes)
- **Person**: Real-world identity with contact info; links to Discord/GitHub/Slack accounts
- **Team**: Access control group with roles (`member`, `lead`, `admin`)
- **Project**: Content boundary; teams are assigned to projects

### Access Rules

Content is accessible if ANY of these conditions are met:

1. User has `admin` scope (superadmin)
2. User created the item
3. User's Person is linked to the item (person override)
4. Item has `PUBLIC` sensitivity
5. User has team membership in item's project with sufficient role

### Roles & Sensitivity Levels

| Team Role | Project Role | Can Access |
|-----------|--------------|------------|
| member | contributor | public, basic |
| lead | manager | public, basic, internal |
| admin | admin | public, basic, internal, confidential |

### Key Invariants

- **NULL project_id = superadmin-only**: Content without a project assignment is only visible to admins (prevents accidental exposure during migration)
- **Defense in depth**: Access filters applied at Qdrant, BM25, AND final result merge
- **Best role wins**: If a user is in multiple teams for the same project, they get the highest role

Key file: `src/memory/common/access_control.py`

## Authentication

- **Sessions**: UUID tokens via cookie or `Authorization: Bearer` header
- **API Keys**: Type-prefixed keys (`mcp_`, `discord_`, `github_`, etc.) for integrations
- **Key Types**: `internal`, `discord`, `google`, `github`, `mcp`, `one_time`
- **Scopes**: Keys inherit user scopes unless overridden
- **One-time keys**: Auto-deleted after first use for single-use operations

Key file: `src/memory/api/auth.py`

## Key Design Decisions

1. **Multi-user with privacy** - Supports collaboration while keeping data self-hosted
2. **Chunked content** - Large documents split into searchable chunks
3. **Async processing** - Heavy work happens in Celery workers
4. **Deduplication** - SHA256-based to avoid duplicate content
5. **Tag-based organization** - Flexible categorization via PostgreSQL arrays
6. **Person abstraction** - Separates auth (User) from identity (Person) to support external contacts and multiple accounts
