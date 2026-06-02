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
│   ├── search/             # Search implementation (embeddings, BM25, HyDE, rerank, scorer)
│   ├── MCP/                # MCP server + per-domain subservers (under MCP/servers/)
│   ├── auth.py             # Sessions, API keys, OAuth
│   └── ...                 # Other route modules
├── common/                 # Shared code
│   ├── db/models/          # SQLAlchemy ORM models
│   ├── access_control.py   # Role-based access control
│   ├── celery_app.py       # Task queue configuration
│   ├── embedding.py        # Voyage embedding client
│   ├── llms/               # OpenAI / Anthropic LLM providers
│   ├── qdrant.py           # Vector DB client
│   └── settings.py         # Environment configuration
├── parsers/                # Content type parsers (email, ebook, comics, etc.)
└── workers/tasks/          # Celery tasks for content processing

frontend/                   # React 19 + TypeScript + Vite
db/migrations/              # Alembic database migrations
tools/                      # CLI utilities (add_user, run tasks, etc.)
```

Project- and team-level operations are exposed as MCP tools rather than dedicated FastAPI routers; see `src/memory/api/MCP/servers/projects.py` and `src/memory/api/MCP/servers/teams.py`.

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

### Service Ports

Only the API publishes a host port by default (`${API_PORT:-8000}:8000` in `docker-compose.yaml`). Postgres, Redis, and Qdrant stay on the internal `kbnet` network. To reach them from the host for local development, create a `docker-compose.override.yml` (gitignored) that publishes them:

| Service | Suggested Local Port | Container Port |
|---------|----------------------|----------------|
| PostgreSQL | 15432 | 5432 |
| Redis | 16379 | 6379 |
| Qdrant | 6333 | 6333 |
| API | 8000 | 8000 |

`tools/install.sh` writes `DB_PORT=15432` etc. into `.env`, so those are the conventional ports — but the override file that actually publishes them is not checked in.

### Deployment

Use `tools/deploy.sh` to manage the production server:

```bash
./tools/deploy.sh deploy          # git pull + restart services (most common)
./tools/deploy.sh pull            # git pull on the server only
./tools/deploy.sh sync            # rsync local code (bypasses git)
./tools/deploy.sh restart         # restart docker without pulling
./tools/deploy.sh run "<command>" # run command on server
./tools/deploy.sh orchestrator    # set up the Claude session orchestrator
./tools/deploy.sh session ...     # manage Claude sessions on the server
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

The search system combines multiple strategies (each toggleable via an `ENABLE_*` setting):

1. **Vector Search** - Voyage embeddings (`voyage-3-large`, 1024d for text; `voyage-multimodal-3` for mixed) in Qdrant
2. **BM25 Full-text** - PostgreSQL tsvector for keyword matching (`ENABLE_BM25_SEARCH`)
3. **HyDE** - Hypothetical Document Embeddings for query expansion (`ENABLE_HYDE_EXPANSION`)
4. **Query Analysis** - LLM understands search intent (`ENABLE_QUERY_ANALYSIS`)
5. **Reranking** - Voyage `rerank-2-lite` cross-encoder reorders candidates (`ENABLE_RERANKING`)
6. **Scoring** - recency / popularity / title-match boosts applied to ranked results (`ENABLE_SEARCH_SCORING`)

Vector and BM25 result lists are merged with Reciprocal Rank Fusion (`fuse_scores_rrf`, `RRF_K=60`). HyDE, query analysis, reranking, and scoring are separate stages around that fusion, not inputs to it.

**Access Control in Search**: Filters are applied at multiple layers (Qdrant vector queries, BM25 full-text, and final result merge) for defense in depth. Users only see content they have access to based on their team/project memberships.

## MCP Tools (for AI Assistants)

The full list of available MCP tools can be fetched via `tools/list` on the MCP server. Tools are organized by domain (core, teams, projects, etc.) and respect access control based on the authenticated user's permissions.

## Content Types Supported

The parser turns raw content into items; the Celery task module drives ingestion and routes to a queue.

| Type | Parser | Task module | Celery Queue |
|------|--------|-------------|--------------|
| Email | `parsers/email.py` | `workers/tasks/email.py` | `email` |
| Ebooks (EPUB/PDF) | `parsers/ebook.py` | `workers/tasks/ebook.py` | `ebooks` |
| Blog/Web pages | `parsers/blogs.py` | `workers/tasks/blogs.py` | `blogs` |
| Comics (SMBC, XKCD) | `parsers/comics.py` | `workers/tasks/comic.py` | `comic` |
| Discord messages | — | `workers/tasks/discord.py` | `discord` |
| Forum posts | `parsers/lesswrong.py` | `workers/tasks/forums.py` | `forums` |
| Notes | — | `workers/tasks/notes.py` | `notes` |
| Photos/Images | — | `workers/tasks/photo.py` | `photos` |

The full queue list lives in `docker-compose.yaml` (worker `QUEUES` env var) — see [Adding a New Celery Worker/Queue](#adding-a-new-celery-workerqueue).

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
   - Add queue name to the worker service's `QUEUES` env var (search for `QUEUES:`)
   - Format: comma-separated list like `"email,blogs,<newqueue>"`

5. **Update worker Dockerfile** (`docker/workers/Dockerfile`):
   - Add queue to the default `QUEUES` env var (search for `ENV QUEUES=`)

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
- `sessions.py` - Claude session records (separate from `UserSession` auth sessions)

The directory holds many more model files (`deadlines.py`, `discord.py`, `journal.py`, `mcp.py`, `people.py`, `polls.py`, `slack.py`, etc.) — list it for the full set.

## Environment Variables

Key settings (see `src/memory/common/settings.py`):

- `VOYAGE_API_KEY` - All embeddings (`voyage-3-large` / `voyage-multimodal-3`) and reranking (`rerank-2-lite`)
- `ANTHROPIC_API_KEY` - Query analysis, HyDE expansion, summarization
- `OPENAI_API_KEY` - Misc LLM-backed features (notes, observation extraction)
- `FILE_STORAGE_DIR` - Where uploaded files are stored
- `ENABLE_BM25_SEARCH`, `ENABLE_HYDE_EXPANSION`, `ENABLE_RERANKING`, `ENABLE_QUERY_ANALYSIS`, `ENABLE_SEARCH_SCORING` - Search feature toggles

## Code Style

### `from __future__ import annotations`

Don't add it by default. The project runs Python 3.12, where PEP 604 unions
(`int | None`) and builtin generics (`list[int]`, `dict[str, int]`) already
work at runtime. Only add the future import when you genuinely need deferred
annotation evaluation (e.g. a real forward reference to a not-yet-defined name).
Note that dropping it means annotations are evaluated eagerly, so any type used
in a signature must be a real runtime import — not a `TYPE_CHECKING`-only one.

### Naming Conventions

#### Underscore Prefix (`_name`)

Reserve underscore-prefixed names for **truly private** items that are unsafe or incorrect to use from outside their immediate context:

- Functions that require preconditions (e.g., caller must hold a lock)
- Internal thread targets or callbacks
- Module-level state that must not be accessed directly

**Don't** use underscores merely because a function/exception/constant is only used within one file. File-local helpers that are safe to call should use regular names — the leading underscore should signal "calling this from outside is unsafe", not "this lives in the same file".

```python
# Good — underscore for truly private (requires lock held)
def _start_writer_locked() -> None:
    """Caller must hold _writer_lock."""
    ...

# Good — no underscore for file-local helpers that are safe to call
def truncate_value(value: Any, max_length: int) -> Any:
    """Truncate large values for storage."""
    ...

CSP_FORBIDDEN_CHARS = frozenset({";", "\r", "\n"})

class UnsafeSubpathError(Exception):
    """Raised when a request subpath contains traversal or empty segments."""

# Bad — underscore just because used in one file today
def _truncate_value(value: Any, max_length: int) -> Any: ...
class _UnsafeSubpathError(Exception): ...
_CSP_FORBIDDEN_CHARS = frozenset(...)
```

This matters because today's "file-private" helper often becomes tomorrow's "imported from another file" helper, and renaming on import day generates churn that obscures the actual change.

### Comments describe the present, not the past

Comments and docstrings should describe **what the current code does and
why it must be this way**. They should **not** narrate what the code
used to be, what a previous implementation got wrong, or what was
"replaced". Git history (``git log``, ``git blame``, PR descriptions)
is where archaeology belongs.

Keep the security/correctness *rationale* — the constraint on the code
("comparing parsed origins is load-bearing because a prefix match would
let an attacker register `localhost.evil.com/cb`"). Drop the
*archaeology* ("the previous implementation used ``str.startswith``
which let an attacker…").

```python
# Bad — narrates history
def get_cipher() -> Fernet:
    """Replaces the previous bare-SHA-256 derivation which had no work
    factor, no salt, and was inconsistent with the codebase…"""

# Good — describes the current constraint
def get_cipher() -> Fernet:
    """PBKDF2-HMAC-SHA256 with 480k iterations (OWASP-recommended) so a
    leaked backup ciphertext is not feasibly brute-forceable. The pinned
    salt is distinct from ``SECRETS_ENCRYPTION_SALT`` so backup and
    at-rest ciphertexts cannot be cross-attacked under a shared
    passphrase."""
```

The same applies to NOTE/FIXME blocks that exist only to explain why a
dead entry was removed — once it's removed, the explanation belongs in
the commit, not the file. The exception is operator-facing migration
guidance (e.g. "v1 archives need the matching code revision to
decrypt") — that's operational documentation, not archaeology.

This also applies to MCP tool docstrings specifically: those are
surfaced to clients via ``tools/list`` and must describe what the tool
does, not how it's implemented or what it used to do. The MCP client
has no visibility into the rest of the file, so references to
"the gate comment below" or "previous behavior" are dead text on the
wire.

### Red CI is yours to fix, even if you didn't break it

If ``lint-and-test`` is failing on the branch you're working on — for
any reason, regardless of whose change introduced the failure — fix
it before merging your own PR. "Pre-existing master breakage" is not
an exemption; the next PR after this one will inherit the same red
CI and the cycle continues. Fix the actual errors (typing gaps,
missing stubs, real bugs), don't disable the check, and call out the
unrelated fix in the commit message so reviewers see the scope
expansion is deliberate.

When the failure is a third-party library type-stub gap (e.g.
``discord.py``'s ``from .x import *`` re-exports that pyright can't
resolve), the right fix is a scoped pragma at the top of the
affected file — ``# pyright: reportAttributeAccessIssue=false`` — not
a per-line ``type: ignore`` sweep, and not a project-wide config
relaxation. The pragma's scope makes it greppable when the upstream
library ships proper stubs and the suppression should be removed.

## Testing

```bash
/home/ec2-user/memory/venv/bin/pytest
```

### Running the slow suite faster

The full slow suite (`pytest --run-slow`) takes ~4:30 serially. With `pytest-xdist` (in `requirements-dev.txt`) it drops to ~2:00:

```bash
~/.virtualenvs/memory/bin/pytest --run-slow -n 4 --dist loadfile
```

- `--dist loadfile` keeps every test from a file on the same worker. The default `--dist load` splits files across workers and triggers shared-state failures (qdrant container collisions, session-factory swap conflicts).
- `-n 4` is the empirical sweet spot. `-n 8` was no faster (DB/qdrant contention dominates) and `-n auto` (10 on this Mac) hangs because docker can't bring up that many qdrant containers.
- Stick with serial (`pytest`) when iterating on a single test — xdist hides per-test output until the run ends.

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

## Check Job Queue

An async fire-and-forget "check" queue (`src/memory/api/check/`) lets callers
submit text to verify/research/link and remote worker sessions resolve it.
Pure-Redis, **no Celery, no Postgres**.

- **Scope**: `check` gates all endpoints (submit, read own, and work jobs).
  Per-user: `GET /check/next` pulls only the caller's own queue (always, even
  admins); `GET /check/{id}` is owner-or-admin.
- **Endpoints**: `POST /check` (submit → `chk_<uuid4>`), `GET /check` (list own),
  `GET /check/next?wait=` (worker long-poll, ≤30s), `GET /check/{id}` (poll),
  `POST /check/{id}/result` (worker completes, must echo `lease_id`),
  `DELETE /check/{id}` (owner-or-admin hard-delete; doesn't stop an in-flight worker).
- **Redis keys**: `check:job:{id}` HASH (record incl. result/callback/lease_id),
  `check:open:{uid}` ZSET (claimable job ids, FIFO by submit time),
  `check:lease:{id}` STRING with TTL (in-flight marker; value = fencing token),
  `check:wake:{uid}` LIST (doorbell), `check:jobs:{uid}` ZSET (per-user index
  for listing).
- **No Celery, no reaper**: claiming = scan `check:open`, `SET NX EX` a lease on
  the first free id; the lease TTL auto-expires to make a stuck job claimable
  again (no background sweep). `/check/next` blocks via BLPOP on the doorbell,
  which `submit` RPUSHes into; the woken claimer re-scans (the token is just a
  wake signal). A job claimed more than `CHECK_MAX_REQUEUE_ATTEMPTS` times is
  marked `expired`. Callbacks are best-effort in-process `asyncio` tasks
  (SSRF-guarded), with polling as the durable fallback.
- **Fencing**: claiming mints a `lease_id` stored as the `check:lease:{id}`
  value; `complete_job` accepts the result only if the held lease still equals
  the caller's `lease_id` (mismatch/expiry → 410) so an expired-then-reassigned
  job can't be clobbered by the old worker.
- **Config**: `CHECK_*` settings (lease 1h, retention 14d, queue depth,
  rate limit, callback attempts) in `settings.py`.

## Key Design Decisions

1. **Multi-user with privacy** - Supports collaboration while keeping data self-hosted
2. **Chunked content** - Large documents split into searchable chunks
3. **Async processing** - Heavy work happens in Celery workers
4. **Deduplication** - SHA256-based to avoid duplicate content
5. **Tag-based organization** - Flexible categorization via PostgreSQL arrays
6. **Person abstraction** - Separates auth (User) from identity (Person) to support external contacts and multiple accounts
