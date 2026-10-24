# Memory — Personal Knowledge Base

Self-hosted knowledge base: ingests email, documents, notes, web pages, ebooks, Discord, photos; hybrid semantic search; MCP access with Team/Project access control. Features, install, configuration and operator docs: **[README.md](README.md)**. This file (`CLAUDE.md` is a symlink to it) holds the conventions and gotchas for working on the code.

Layout: `src/memory/{api,common,parsers,workers,agents}`, `frontend/` (React + Vite; views are `<Page>`s built from `components/shared/ui`, see `frontend/STYLE.md`), `db/migrations/` (Alembic), `tools/`. Project and team operations are MCP tools (`api/MCP/servers/{projects,teams}.py`), not FastAPI routers.

## Development

```bash
~/.virtualenvs/memory/bin/python                                   # venv (workon memory)
~/.virtualenvs/memory/bin/pytest                                   # iterate (serial)
~/.virtualenvs/memory/bin/pytest --run-slow -n 4 --dist loadfile   # full slow suite
python tools/run_celery_task.py <queue> <task-name> [args]         # one-off Celery task
```

- `--dist loadfile` keeps each file on one worker; the default `--dist load` splits files and causes qdrant/session shared-state failures. Above `-n 4` hangs on docker/qdrant contention. Use serial when iterating on one test.
- Running the app on the host with infra in Docker: [README.md § Local development](README.md#local-development-without-docker-for-the-app) (Postgres 15432, Redis 16379, Qdrant 6333, API 8000).
- Deploying and updating: [UPDATING.md](UPDATING.md). Every release needs a [CHANGELOG.md](CHANGELOG.md) entry (new required env vars, slow migrations, re-runnable post-deploy commands). Never squash or rewrite the alembic chain: instances may skip many releases.
- Settings source of truth: `src/memory/common/settings.py`.
- New Celery tasks: route them in `common/celery_app.py`, import the module in `workers/tasks/__init__.py`, and add any new queue to `QUEUES` in both `docker-compose.yaml` and `docker/workers/Dockerfile`. Prefer reusing `maintenance` or `scheduler` over a new queue.

## Code Style

- No `from __future__ import annotations` unless there is a genuine forward reference. Without it, every type in a signature must be a real runtime import, not `TYPE_CHECKING`-only.
- `_name` means unsafe to call from outside (holds a lock, internal callback, module state), not "file-local".
- Comments and docstrings describe the present: what the code does and why. No archaeology about what it replaced. This applies doubly to MCP tool docstrings, which are surfaced via `tools/list`. Operator-facing migration notes are the exception.
- Red CI is yours to fix, including pre-existing breakage on your branch; fix the errors rather than disabling the check. For third-party stub gaps, use a scoped file-top pragma (`# pyright: reportAttributeAccessIssue=false`), not per-line `type: ignore` sweeps.

## Testing

Prefer the real DB/MCP fixtures in `tests/conftest.py` (`db_session`, `admin_user`, `regular_user`, `admin_session`, `user_session`) over `MagicMock`/`@patch`. For MCP tools use the `mcp_auth_context` context manager:

```python
from tests.conftest import mcp_auth_context

async def test_some_tool(db_session, admin_session):
    with mcp_auth_context(admin_session.id):
        result = await some_tool.fn(arg="value")
```

## Access Control

Visibility flows User → Person → Team → Project → content (`common/access_control.py`). An item is accessible if the user has `admin` scope, created it (for synced items, owns the source account), has their Person linked to it, it is `PUBLIC`, or a team role in its project grants its sensitivity level. Best role wins across teams.

- NULL `project_id` is superadmin-only.
- Filters apply at Qdrant, BM25 and the final merge (defense in depth). The fail-closed gate is `require_access_filter` in `api/search/embeddings.py`; don't add a search path that bypasses it.

## Gotchas

- Every request to a user-supplied URL goes through `common/safe_http.public_http_client`, which checks each address at dial time. Never use plain httpx/aiohttp for these.
- Image boundary: maintenance/scheduler code must not import `memory.agents.spec` or `memory.agents.enqueue`; anything that calls `enqueue_run` runs on the `agents` queue (tested in `tests/memory/agents/test_tasks.py`).
- Claude container reconcile and cleanup run in the API process (`claude_containers/claude_maintenance.py`), because only it holds the orchestrator socket.
- User LLM provider keys are write-only: only `key_hint` is ever returned.
- The check job queue (`common/check/`) is pure Redis: no Celery, no Postgres. Non-owners get 404 "unknown job", checked before any wait.

Search pipeline detail: [docs/SEARCH_INVESTIGATION.md](docs/SEARCH_INVESTIGATION.md).
