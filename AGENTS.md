# Memory — Personal Knowledge Base

A self-hosted, privacy-first knowledge base for personal and collaborative use. It ingests, indexes, and provides semantic search across your digital life — emails, documents, notes, web pages, ebooks, Discord messages, photos — while keeping all data self-hosted. AI assistants reach it via the Model Context Protocol (MCP); Teams & Projects provide role-based collaborative access; an observation system lets assistants record and recall patterns about your preferences.

## Architecture

| Component | Purpose |
|-----------|---------|
| **FastAPI** (`src/memory/api/`) | REST API + MCP server for AI assistants |
| **PostgreSQL** | Metadata, users, teams, projects, content, observations |
| **Qdrant** | Vector database for semantic similarity search |
| **Celery** (`src/memory/workers/`) | Async background processing for content ingestion |
| **Redis** | Celery broker, session cache, LLM usage tracking |
| **React 19 frontend** (`frontend/`) | Search interface and dashboard |

Flow: Frontend → FastAPI → PostgreSQL, with FastAPI also writing Qdrant and dispatching to Celery (via Redis) for ingestion.

## Project Structure

```
src/memory/
├── api/                    # FastAPI application
│   ├── app.py              # Main entry point
│   ├── search/             # Search (embeddings, BM25, HyDE, rerank, scorer)
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
│   ├── check/              # Check job queue core logic
│   └── settings.py         # Environment configuration
├── parsers/                # Content type parsers (email, ebook, comics, etc.)
└── workers/tasks/          # Celery tasks for content processing

frontend/                   # React 19 + TypeScript + Vite
db/migrations/              # Alembic database migrations
tools/                      # CLI utilities (add_user, run tasks, deploy, etc.)
```

Project- and team-level operations are MCP tools, not FastAPI routers — see `src/memory/api/MCP/servers/{projects,teams}.py`.

## Development Environment

### Python

```bash
workon memory                       # or: ~/.virtualenvs/memory/bin/python
~/.virtualenvs/memory/bin/pytest    # run tests
```

### Service Ports

Only the API publishes a host port by default (`${API_PORT:-8000}:8000`); Postgres, Redis, and Qdrant stay on the internal `kbnet` network. To reach them from the host, create a gitignored `docker-compose.override.yml` that publishes them. Conventional ports (written into `.env` by `tools/install.sh`): **Postgres 15432→5432, Redis 16379→6379, Qdrant 6333→6333, API 8000→8000**.

### Deployment

Manage the production server via `tools/deploy.sh`:

```bash
./tools/deploy.sh deploy           # git pull + restart services (most common)
./tools/deploy.sh pull | sync | restart
./tools/deploy.sh run "<command>"  # run a command on the server
./tools/deploy.sh orchestrator     # set up the Claude session orchestrator
./tools/deploy.sh session ...      # manage Claude sessions on the server
```

### Common Commands

Run via the deployment tool on the server:

```bash
docker-compose up -d --build                              # start services
docker-compose logs -f api | worker                       # logs
python tools/run_celery_task.py <queue> <task-name> [args]  # run a task
python tools/add_user.py --email a@b.com --password pw --name "Name"
alembic upgrade head                                      # migrations
alembic revision --autogenerate -m "description"
```

## Search Pipeline

Combines multiple strategies, each toggleable via an `ENABLE_*` setting:

1. **Vector** — Voyage embeddings (`voyage-3-large` 1024d text; `voyage-multimodal-3` mixed) in Qdrant
2. **BM25** — PostgreSQL tsvector keyword match (`ENABLE_BM25_SEARCH`)
3. **HyDE** — Hypothetical Document Embeddings query expansion (`ENABLE_HYDE_EXPANSION`)
4. **Query Analysis** — LLM understands search intent (`ENABLE_QUERY_ANALYSIS`)
5. **Reranking** — Voyage `rerank-2-lite` cross-encoder reorders candidates (`ENABLE_RERANKING`)
6. **Scoring** — recency / popularity / title-match boosts (`ENABLE_SEARCH_SCORING`)

Vector and BM25 lists merge via Reciprocal Rank Fusion (`fuse_scores_rrf`, `RRF_K=60`); HyDE, query analysis, reranking, and scoring are stages around that fusion, not inputs to it. **Access control** is enforced at every layer (Qdrant, BM25, and the final merge) for defense in depth — users only see content their team/project memberships permit.

## MCP Tools

The full tool list is available via `tools/list` on the MCP server. Tools are organized by domain (core, teams, projects, check, etc.) and respect access control based on the authenticated user's permissions.

## Content Types

The parser turns raw content into items; the Celery task module drives ingestion and routes to a queue.

| Type | Parser | Task module | Queue |
|------|--------|-------------|-------|
| Email | `parsers/email.py` | `workers/tasks/email.py` | `email` |
| Ebooks (EPUB/PDF) | `parsers/ebook.py` | `workers/tasks/ebook.py` | `ebooks` |
| Blog/Web | `parsers/blogs.py` | `workers/tasks/blogs.py` | `blogs` |
| Comics (SMBC, XKCD) | `parsers/comics.py` | `workers/tasks/comic.py` | `comic` |
| Discord | — | `workers/tasks/discord.py` | `discord` |
| Forum posts | `parsers/lesswrong.py` | `workers/tasks/forums.py` | `forums` |
| Notes | — | `workers/tasks/notes.py` | `notes` |
| Photos/Images | — | `workers/tasks/photo.py` | `photos` |

Full queue list: the worker `QUEUES` env var in `docker-compose.yaml`.

## Adding a Celery Worker/Queue

1. **Create** `src/memory/workers/tasks/<name>.py` with tasks decorated `@app.task(name=TASK_NAME)`.
2. **Register** in `src/memory/common/celery_app.py`: add `<NAME>_ROOT = "memory.workers.tasks.<name>"`, task-name constants, a routing rule `f"{<NAME>_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-<queue>"}`, and (if periodic) a `beat_schedule` entry.
3. **Export** by importing the module in `src/memory/workers/tasks/__init__.py`.
4. **Add the queue** to the worker `QUEUES` env var in both `docker-compose.yaml` and `docker/workers/Dockerfile`.

To avoid queue proliferation, prefer reusing an existing queue: `maintenance` (cleanup/metrics/admin), `scheduler` (timed tasks), or `generic` (catch-all).

## Database Models

Key models in `src/memory/common/db/models/`:

- `source_item.py` — base content model (sha256, tags, mime_type, project_id)
- `source_items.py` — specific types (MailMessage, BlogPost, Book, …)
- `sources.py` — Person, Team, Project + membership relationships
- `observations.py` — AgentObservation
- `users.py` — User, HumanUser, BotUser, UserSession, APIKey
- `sessions.py` — Claude session records (distinct from `UserSession` auth sessions)

More files exist (`deadlines.py`, `discord.py`, `journal.py`, `mcp.py`, `people.py`, `polls.py`, `slack.py`, …) — list the directory for the full set.

## Environment Variables

See `src/memory/common/settings.py`:

- `VOYAGE_API_KEY` — all embeddings (`voyage-3-large` / `voyage-multimodal-3`) and reranking (`rerank-2-lite`)
- `ANTHROPIC_API_KEY` — query analysis, HyDE, summarization
- `OPENAI_API_KEY` — misc LLM features (notes, observation extraction)
- `FILE_STORAGE_DIR` — uploaded file storage
- `ENABLE_BM25_SEARCH`, `ENABLE_HYDE_EXPANSION`, `ENABLE_RERANKING`, `ENABLE_QUERY_ANALYSIS`, `ENABLE_SEARCH_SCORING` — search toggles

## Code Style

- **No `from __future__ import annotations` by default.** Python 3.12 already evaluates PEP 604 unions (`int | None`) and builtin generics (`list[int]`) at runtime. Add it only for a genuine forward reference. Without it, annotations evaluate eagerly — every type in a signature must be a real runtime import, not `TYPE_CHECKING`-only.
- **Underscore prefix `_name` means "unsafe to call from outside"** (holds a lock, internal callback, module state not to be touched directly) — *not* "file-local". Safe-to-call file-local helpers/exceptions/constants get regular names. Renaming on the day a helper is first imported elsewhere is churn worth avoiding.
- **Comments describe the present, not the past.** Say what the code does and why it must be this way; don't narrate what it replaced or what a prior version got wrong — that's git's job. Keep security/correctness *rationale*, drop *archaeology*. Applies doubly to MCP tool docstrings (surfaced via `tools/list`, with no file context on the wire). Exception: operator-facing migration notes are documentation, not archaeology.
- **Red CI is yours to fix**, even pre-existing breakage on your branch — fix the real errors, don't disable the check, and note the scope expansion in the commit. For third-party stub gaps, use a scoped file-top pragma (`# pyright: reportAttributeAccessIssue=false`), not a per-line `type: ignore` sweep or a project-wide relaxation.

## Testing

```bash
~/.virtualenvs/memory/bin/pytest                          # iterate (serial)
~/.virtualenvs/memory/bin/pytest --run-slow -n 4 --dist loadfile   # full slow suite (~2:00 vs ~4:30 serial)
```

`--dist loadfile` keeps each file on one worker (the default `--dist load` splits files and triggers qdrant/session shared-state failures). `-n 4` is the sweet spot — higher hangs on docker/qdrant contention. Use serial when iterating on a single test (xdist hides per-test output until the end).

### Fixtures (`tests/conftest.py`)

Prefer these real DB/MCP fixtures over `MagicMock`/`@patch` — they exercise the real auth and DB paths with automatic cleanup:

| Fixture | Description |
|---------|-------------|
| `db_session` | Real DB session, tables truncated after each test |
| `admin_user` | `HumanUser`, `scopes=["*"]`, `admin@example.com` |
| `regular_user` | `HumanUser`, `scopes=["teams"]`, `regular@example.com` |
| `admin_session` | `UserSession` for `admin_user` (token `"admin-session-token"`) |
| `user_session` | `UserSession` for `regular_user` (token `"test-session-token"`) |

For MCP tool tests use `mcp_auth_context` (a context manager, not a fixture):

```python
from tests.conftest import mcp_auth_context

async def test_some_tool(db_session, admin_session):
    with mcp_auth_context(admin_session.id):
        result = await some_tool.fn(arg="value")
    assert result["authenticated"] is True
```

## Access Control

Content visibility flows through a User → Person → Team → Project hierarchy:

```
User (auth) ──1:0..1── Person (identity) ──M:N── Team ──M:N── Project ──── Content
```

- **User**: auth identity (email, password, API keys, scopes)
- **Person**: real-world identity + contact info; links Discord/GitHub/Slack accounts
- **Team**: access group with roles `member` / `lead` / `admin`
- **Project**: content boundary; teams are assigned to projects

**Content is accessible if ANY hold:** user has `admin` scope; user created the item; user's Person is linked to the item; item is `PUBLIC`; or the user has sufficient team role in the item's project.

| Team role | Project role | Can access |
|-----------|--------------|------------|
| member | contributor | public, basic |
| lead | manager | public, basic, internal |
| admin | admin | public, basic, internal, confidential |

**Invariants:** NULL `project_id` = superadmin-only (prevents accidental exposure during migration); filters applied at Qdrant + BM25 + final merge (defense in depth); best role wins across multiple teams. Key file: `src/memory/common/access_control.py`.

## Authentication

- **Sessions**: UUID tokens via cookie or `Authorization: Bearer`
- **API keys**: type-prefixed (`mcp_`, `discord_`, `github_`, …) for integrations
- **Key types**: `internal`, `discord`, `google`, `github`, `mcp`, `one_time`
- **Scopes**: keys inherit user scopes unless overridden; one-time keys auto-delete after first use

Key file: `src/memory/api/auth.py`.

## Check Job Queue

Async fire-and-forget queue: callers submit text to verify/research/link; remote worker sessions resolve it. **Pure-Redis — no Celery, no Postgres.** Core logic in `src/memory/common/check/`; the HTTP router (`src/memory/api/check/`) and MCP subserver (`src/memory/api/MCP/servers/check.py`) are thin wrappers.

- **Scope/ownership**: a single `check` scope gates everything (`*` also passes). Per-user: `GET /check/next` pulls only the caller's queue; reads/list/delete are owner-or-admin (404 "unknown job" for a non-owner — no existence leak; ownership checked *before* any wait).
- **MCP tools** (primary surface): `check_ask`, `check_wait_for_answer` (bounded poll, default 60s, cap `CHECK_MAX_WAIT_SEC`), `check_list_jobs`, `check_delete`. Bounded waits by design — re-call if still pending.
- **HTTP** (external consumers + worker): `POST /check`, `GET /check/{id}?wait=`, `GET /check/next?wait=` (worker long-poll ≤30s), `POST /check/{id}/result` (must echo `lease_id`). No HTTP list/delete.
- **Redis keys**: `check:job:{id}` (HASH), `check:open:{uid}` (claimable ZSET, FIFO), `check:lease:{id}` (TTL fencing token), `check:wake:{uid}` (doorbell LIST), `check:jobs:{uid}` (per-user index).
- **Claiming/fencing, no reaper**: claim = scan `check:open`, `SET NX EX` a lease (minting a `lease_id`) on the first free id; the TTL auto-expires to reclaim stuck jobs. `/check/next` BLPOPs the doorbell that `submit` RPUSHes. `complete_job` accepts a result only if the held lease still equals the caller's `lease_id` (else 410). Over `CHECK_MAX_REQUEUE_ATTEMPTS` claims → `expired`. Callbacks are best-effort in-process asyncio (SSRF-guarded); polling is the durable fallback.
- **Config**: `CHECK_*` in `settings.py` (lease 1h, retention 14d, queue depth, rate limit, callback attempts, wait 60 / max 300).

## Key Design Decisions

1. **Multi-user with privacy** — collaboration while staying self-hosted
2. **Chunked content** — large documents split into searchable chunks
3. **Async processing** — heavy work in Celery workers
4. **Deduplication** — SHA256-based
5. **Tag-based organization** — PostgreSQL arrays
6. **Person abstraction** — separates auth (User) from identity (Person) to support external contacts and multiple accounts
