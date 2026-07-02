# Memory — Personal Knowledge Base

A self-hosted, privacy-first knowledge base: it ingests, indexes, and provides semantic search across your digital life — emails, documents, notes, web pages, ebooks, Discord messages, photos — with MCP access for AI assistants and Team/Project-based access control. **See [README.md](README.md) for the feature overview, install/run instructions, and operator docs** (reverse proxy, security, troubleshooting, custom tasks).

This file — auto-loaded by Claude Code via the `CLAUDE.md` symlink — is the **contributor + AI-assistant guide**: how the code is laid out and the conventions to follow. It points at README for operator-facing material rather than duplicating it.

## Architecture

Component table and purposes: **[README.md § Architecture](README.md#architecture)**. In short: React frontend → FastAPI (REST + MCP server) → PostgreSQL, with FastAPI also writing Qdrant and dispatching ingestion to Celery workers over Redis (the broker; also session cache + rate-limit buckets).

## Project Structure

```
src/memory/
├── api/                    # FastAPI application
│   ├── app.py              # Main entry point
│   ├── search/             # Search (embeddings, BM25, HyDE, rerank, scorer)
│   ├── MCP/                # MCP server + per-domain subservers (under MCP/servers/)
│   ├── check/              # Check job queue HTTP router
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

### Host-side dev & service ports

To run the API/workers on the host while infra stays in Docker, publish the data-service ports via a gitignored `docker-compose.override.yml` — full instructions in **[README.md § Local development](README.md#local-development-without-docker-for-the-app)** (conventional ports: Postgres 15432, Redis 16379, Qdrant 6333, API 8000; only the API publishes a host port by default).

### Deployment

Manage the production server via `tools/deploy.sh` (not covered in README):

```bash
./tools/deploy.sh deploy           # git pull + restart services (most common)
./tools/deploy.sh pull | sync | restart
./tools/deploy.sh run "<command>"  # run a command on the server
./tools/deploy.sh orchestrator     # set up the Claude session orchestrator
./tools/deploy.sh session ...      # manage Claude sessions on the server
```

### Common commands

Install, user creation, health checks, and migrations are in **[README.md](README.md)** (Quick Start, User management). Server-side operations go through `tools/deploy.sh` (above). One-off Celery task:

```bash
python tools/run_celery_task.py <queue> <task-name> [args]
```

## Search Pipeline

Hybrid search combining vector (Voyage `voyage-3-large` 1024d text / `voyage-multimodal-3` mixed, in Qdrant) and PostgreSQL BM25, merged via Reciprocal Rank Fusion (`fuse_scores_rrf`, `RRF_K=60`), with optional query analysis, HyDE expansion, `rerank-2-lite` reranking, and recency/popularity/title-match scoring around the fusion. Each stage is toggleable via an `ENABLE_*` setting. **Authoritative, current detail (stages, files, toggles): [docs/SEARCH_INVESTIGATION.md](docs/SEARCH_INVESTIGATION.md).**

**Access control** is enforced at every layer (Qdrant, BM25, and the final merge) for defense in depth — users only see content their team/project memberships permit. The fail-closed gate is `require_access_filter` in `search/embeddings.py`.

## MCP Tools

The full tool list is available via `tools/list` on the MCP server; tools are organized by domain (core, teams, projects, check, …) and respect access control per authenticated user. Client setup (OAuth / DCR): [README.md § Connecting an MCP client](README.md#connecting-an-mcp-client).

## Content Types

The parser turns raw content into items; the Celery task module drives ingestion and routes to a queue.

| Type | Parser | Task module | Queue |
|------|--------|-------------|-------|
| Email | `parsers/email.py` | `workers/tasks/email.py` | `email` |
| Ebooks (EPUB/PDF) | `parsers/ebook.py` | `workers/tasks/ebook.py` | `ebooks` |
| Blog/Web | `parsers/blogs.py` | `workers/tasks/blogs.py` | `blogs` |
| Comics (SMBC, XKCD) | `parsers/comics.py` | `workers/tasks/comic.py` | `comic` |
| Discord | — | `workers/tasks/discord.py`, `discord_backfill.py` | `discord` |
| Forum posts | `parsers/lesswrong.py` | `workers/tasks/forums.py` | `forums` |
| Notes | — | `workers/tasks/notes.py` | `notes` |
| Photos/Images | — | `workers/tasks/photo.py` | `photos` |
| Claude sessions | `parsers/claude_sessions.py` | `workers/tasks/sessions.py` | `maintenance` |

Full queue list: the worker `QUEUES` env var in `docker-compose.yaml`.

## Adding a Celery Worker/Queue

1. **Create** `src/memory/workers/tasks/<name>.py` with tasks decorated `@app.task(name=TASK_NAME)`.
2. **Register** in `src/memory/common/celery_app.py`: add `<NAME>_ROOT = "memory.workers.tasks.<name>"`, task-name constants, a routing rule `f"{<NAME>_ROOT}.*": {"queue": f"{settings.CELERY_QUEUE_PREFIX}-<queue>"}`, and (if periodic) a `beat_schedule` entry.
3. **Export** by importing the module in `src/memory/workers/tasks/__init__.py`.
4. **Add the queue** to the worker `QUEUES` env var in both `docker-compose.yaml` and `docker/workers/Dockerfile`.

To avoid queue proliferation, prefer reusing an existing queue: `maintenance` (cleanup/metrics/admin), `scheduler` (timed tasks), or `generic` (catch-all). For *deployment-specific* tasks loaded at runtime (not committed to the repo), use the `CUSTOM_TASKS_DIR` mechanism instead — see [README.md § Custom tasks](README.md#custom-tasks-deployment-specific).

## Database Models

Key models in `src/memory/common/db/models/`:

- `source_item.py` — base content model (sha256, tags, mime_type, project_id)
- `source_items.py` — specific types (MailMessage, BlogPost, Book, …)
- `sources.py` — Person, Team, Project + membership relationships
- `observations.py` — AgentObservation
- `users.py` — User, HumanUser, BotUser, UserSession, APIKey (+ the `APIKeyType` enum)
- `sessions.py` — Claude session records (distinct from `UserSession` auth sessions)

More files exist (`deadlines.py`, `discord.py`, `journal.py`, `mcp.py`, `people.py`, `polls.py`, `slack.py`, …) — list the directory for the full set.

## Environment Variables

Full config table with defaults: **[README.md § Configuration](README.md#configuration)**; source of truth is `src/memory/common/settings.py`. The keys you'll touch most in development:

- `VOYAGE_API_KEY` — all embeddings + reranking
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
- **Key types** (`APIKeyType` enum in `common/db/models/users.py`): `internal`, `discord`, `google`, `github`, `mcp`, `one_time`
- **Scopes**: keys inherit user scopes unless overridden; one-time keys auto-delete after first use

Key file: `src/memory/api/auth.py`. Operator-facing security notes (OAuth, reverse proxy, secrets): [README.md § Security notes](README.md#security-notes).

## Check Job Queue

Async fire-and-forget queue: callers submit text to verify/research/link/deep-dive/investigation-team; remote worker sessions resolve it. **Pure-Redis — no Celery, no Postgres.** Core logic in `src/memory/common/check/`; the HTTP router (`src/memory/api/check/`) and MCP subserver (`src/memory/api/MCP/servers/check.py`) are thin wrappers.

- **Scope/ownership**: a single `check` scope gates everything (`*` also passes). Per-user: `GET /check/next` pulls only the caller's queue; reads/list/delete are owner-or-admin (404 "unknown job" for a non-owner — no existence leak; ownership checked *before* any wait).
- **MCP tools** (primary surface): `check_ask`, `check_wait_for_answer` (bounded poll, default 60s, cap `CHECK_MAX_WAIT_SEC`), `check_list_jobs`, `check_delete`. Bounded waits by design — re-call if still pending.
- **HTTP** (external consumers + worker): `POST /check`, `GET /check/{id}?wait=`, `GET /check/next?wait=[&mode=…&mode=…]` (worker long-poll ≤30s; repeat `mode` to claim any of those check types so distinct worker pools each pull their own — the doorbell stays per-user, so a mode-filtered claimer just re-scans on its own poll timeout), `POST /check/{id}/result` (must echo `lease_id`). No HTTP list/delete.
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
