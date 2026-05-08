# Memory — Knowledge Base

A self-hosted knowledge base that ingests, indexes, and provides semantic search over emails, documents, notes, web pages, ebooks, comics, and more. Exposes an MCP (Model Context Protocol) endpoint so AI assistants can query it directly, with team-based access control and multi-user support.

## Features

- **Multi-modal ingestion**: emails, documents, ebooks, comics, web pages, transcripts, photos
- **Semantic + keyword search**: vector embeddings (Voyage AI / OpenAI) merged with PostgreSQL BM25
- **MCP server**: AI assistants (Claude Desktop, Claude Code, Cursor, …) connect over OAuth
- **Observation system**: assistants can record long-term notes about user preferences and patterns
- **Multi-user with teams + projects**: role-based access on every search result
- **Background ingestion**: Celery workers process content asynchronously

## Architecture

| Component       | Purpose                                                          |
| --------------- | ---------------------------------------------------------------- |
| **FastAPI**     | REST API + MCP streamable HTTP endpoint                          |
| **PostgreSQL**  | Metadata, users, teams, projects, content, observations          |
| **Qdrant**      | Vector database for semantic similarity search                   |
| **Redis**       | Celery broker, session cache, rate-limit buckets                 |
| **Celery**      | Background processing for content ingestion                      |
| **React + Vite**| Search UI and dashboard, served by the API container             |

## Quick Start

The repo ships with an interactive installer that handles secrets, prompts for API keys, and sets sensible defaults. The whole flow takes about five minutes if you have your API keys ready.

### Prerequisites

- Docker + Docker Compose (Compose v2 / `docker compose ...` syntax)
- Python 3.12+ (for the helper CLIs in `tools/`)
- API keys for [OpenAI](https://platform.openai.com/api-keys), [Anthropic](https://console.anthropic.com/settings/keys), and [Voyage AI](https://dash.voyageai.com/api-keys)
  - All three are required: OpenAI for fallback embeddings, Anthropic for query analysis / reranking / summarization, Voyage for primary embeddings.

### 1. Run the installer

```bash
git clone <this-repo> memory && cd memory
./tools/install.sh
```

The installer will:
- Generate `secrets/postgres_password.txt`, `secrets/jwt_secret.txt`, and a Redis password
- Prompt for the three API keys and write them to `.env` + `secrets/*.txt`
- Set DB / Redis / Qdrant defaults in `.env` (Postgres on `localhost:15432`, etc.)
- Detect your Docker group GID (needed for Claude session orchestration)
- Optionally generate an SSH keypair for git-based notes sync

Re-run it any time — it skips anything already configured.

### 2. Start the stack

```bash
docker compose up -d
```

This builds and starts everything: postgres, redis, qdrant, the migration runner (which applies `alembic upgrade head` automatically and exits), the API, and the Celery workers. First boot takes a couple of minutes for the build.

Check health:

```bash
docker compose ps
curl http://localhost:8000/health
```

### 3. Create a user

```bash
pip install -e ".[all]"
python tools/add_user.py --email you@example.com --password 'yourpass' --name 'Your Name'
```

The `[all]` extra pulls in everything the in-repo scripts and tests need; `[api]` / `[workers]` / `[dev]` are also available if you only need a subset.

### 4. Use it

- Web UI: <http://localhost:8000/ui> — log in with the account you just created
- API docs (OpenAPI): <http://localhost:8000/docs> — requires an authenticated session
- MCP endpoint: <http://localhost:8000/mcp>

## Connecting an MCP client

The MCP server uses OAuth (Dynamic Client Registration per RFC 7591), so any compliant MCP client can enroll itself the first time it connects. Point your client at `http://<host>:8000/mcp` and it will walk you through the login flow on first use.

Example for Claude Desktop / Cursor (`mcpServers` config):

```json
{
  "mcpServers": {
    "memory": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

For production deployments you'll want this on HTTPS — see [Running behind a reverse proxy](#running-behind-a-reverse-proxy).

When connected, the assistant has access to tools organized by domain (core, teams, projects, github, slack, …). Use `tools/list` on the MCP server to see everything that's available; access control is enforced per-call based on the authenticated user's team memberships and roles.

## Running behind a reverse proxy

If you put the API behind Traefik, nginx, Caddy, or any reverse proxy that terminates TLS, **you must tell uvicorn and the rate limiter which upstream IPs to trust**. Without this:

- Uvicorn ignores `X-Forwarded-Proto`, so any HTTP redirect uses `http://` instead of `https://` and HTTPS clients break (this is a common cause of MCP clients failing to complete the OAuth handshake).
- The SlowAPI rate limiter keys on the proxy's IP, so every user shares one bucket.

Set both in `.env`:

```bash
# CIDR of the proxy hops you trust to set X-Forwarded-* headers.
# Use the actual subnet your reverse proxy runs in.
FORWARDED_ALLOW_IPS=10.89.0.0/24
RATE_LIMIT_TRUSTED_PROXIES=10.89.0.0/24

# Public origin used for OAuth redirects, CORS, and cookie scoping.
SERVER_URL=https://memory.example.com
```

The `docker-compose.yaml` passes both variables through to the container with safe loopback defaults (`127.0.0.1,::1`), so you don't need to touch the compose file — setting them in `.env` is enough. Restart with `docker compose up -d --build` to apply.

> **Why this is a footgun**: `docker compose --env-file foo.env` only feeds *YAML interpolation*, not container runtime environment. Vars set there reach a service only if the service's `environment:` mapping passes them through. `FORWARDED_ALLOW_IPS` and `RATE_LIMIT_TRUSTED_PROXIES` are explicitly passed through; vars not in that mapping won't reach the container regardless of what's in `.env`.
>
> **CIDR support requires uvicorn ≥ 0.31** — earlier versions did plain string-set membership and silently ignored CIDR strings. The repo now pins `uvicorn>=0.34`. If you're on a deployment with `uvicorn==0.29`, set `FORWARDED_ALLOW_IPS` to either `*` (acceptable when the api container has no host port mapping and is only reachable through the reverse proxy) or to the proxy's exact IP, not a CIDR.

## Local development (without Docker for the app)

If you want to run the API or workers on the host while keeping infrastructure in Docker:

```bash
# Bring up only postgres, redis, qdrant (exposed on localhost)
./dev.sh

# Install Python deps in a virtualenv
pip install -e ".[all]"

# Apply migrations (the docker-compose `migrate` service does this for the
# Docker flow; for host-side dev you run it yourself)
alembic -c db/migrations/alembic.ini upgrade head

# Run the API with auto-reload
RELOAD=true python -m memory.api.app

# In another terminal, run a worker
celery -A memory.common.celery_app worker -Q memory-email,memory-blogs,memory-generic
```

`dev.sh` only starts the data services and writes a `.env` pointing at `localhost`. To go back to the all-in-Docker setup, run `docker compose up -d` afterwards — that brings up the API, workers, and migration runner.

## User management & ingestion

### Notes synchronisation

You can have notes pushed to a git repository whenever they're modified:

```bash
python tools/run_celery_task.py notes setup-git-notes \
  --origin ssh://git@github.com/some/repo.git \
  --email bla@ble.com \
  --name 'commit author'
```

This requires SSH keys in `secrets/` — `tools/install.sh` can generate them (option in the SSH section), or see `secrets/README.md` to create them by hand. Add the public key to your git provider before the first push.

### Discord integration

To get notifications in Discord, [create a bot](https://discord.com/developers/applications), then run:

```bash
python tools/discord_setup.py generate-invite --bot-token <your bot token>
```

That returns an invite URL for adding the bot to your server.

### Manually triggering Celery tasks

```bash
python tools/run_celery_task.py <queue> <task-name> [args]
```

Queues live in `src/memory/common/celery_app.py` (`task_routes`); typical ones are `email`, `blogs`, `comics`, `ebook`, `notes`, `generic`.

## MCP client library for HTML reports

If you're embedding interactive content in stored reports, the API serves a small JS client at `/ui/mcp-client.js` that authenticates via the user's session cookie and exposes the MCP tool surface as a JS API:

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
    MCP.people.list({ limit: 10 })
      .then(people => {
        const list = people.map(p => p.display_name).join(', ');
        document.getElementById('results').innerHTML = `People: ${list}`;
      });
  </script>
</body>
</html>
```

Manual / generic / batched calls:

```javascript
MCP.call('method_name', { params })

MCP.batch([
  { method: 'people_list_all', params: { limit: 10 } },
  { method: 'github_list_entities', params: { type: 'issue', limit: 5 } }
]).then(([people, issues]) => { /* … */ });
```

Reports must be created with `allow_scripts=True` for this to load (set via the UI / API on the report record). The library uses the `access_token` cookie for auth, so reports inherit the viewing user's permissions.

## Configuration

Configuration lives in `.env` (or environment variables on the host). Key settings:

| Variable                          | Default              | Purpose                                                        |
| --------------------------------- | -------------------- | -------------------------------------------------------------- |
| `SERVER_URL`                      | `http://localhost:8000` | Public origin; used for OAuth redirects, CORS, cookies      |
| `DB_HOST`, `DB_PORT`              | `postgres` / `5432`  | PostgreSQL — set to `localhost` / `15432` for host-side dev   |
| `REDIS_HOST`, `REDIS_PORT`        | `redis` / `6379`     | Celery broker + cache                                          |
| `QDRANT_HOST`, `QDRANT_URL`       | `qdrant` / `6333`    | Vector store                                                   |
| `OPENAI_API_KEY`                  | required             | Fallback embeddings, some LLM features                         |
| `ANTHROPIC_API_KEY`               | required             | Query analysis, reranking, summarization                       |
| `VOYAGE_API_KEY`                  | required             | Primary embedding provider                                     |
| `FILE_STORAGE_DIR`                | `/tmp/memory_files`  | Where uploaded content is stored                               |
| `FORWARDED_ALLOW_IPS`             | `127.0.0.1,::1`      | Trusted-proxy IPs/CIDRs for `X-Forwarded-*` (see proxy section)|
| `RATE_LIMIT_TRUSTED_PROXIES`      | `127.0.0.1,::1`      | Trusted-proxy IPs/CIDRs for the rate-limit bucket key          |
| `ENABLE_BM25_SEARCH`              | `true`               | Enable Postgres BM25 alongside vector search                   |
| `CUSTOM_TASKS_DIR`                | unset                | Path to a directory of deployment-specific Celery tasks        |

See `src/memory/common/settings.py` for the full list and `docker-compose.yaml` for how variables map into containers.

## Security notes

- The MCP endpoint uses OAuth; the rest of the API uses session cookies + bearer tokens. Don't put the API on the public internet without a reverse proxy that terminates TLS — the OAuth handshake won't complete over plain HTTP for clients that enforce HTTPS.
- Secrets live in `secrets/` (file-mounted) and `.env` (env-var-mounted). Both are gitignored.
- The OAuth redirect-uri allowlist defaults to `http://localhost,http://127.0.0.1` and accepts ephemeral ports for those loopback hosts (RFC 8252 native-app flow). Set `OAUTH_REDIRECT_URI_ALLOWLIST` to add additional origins for browser-based MCP clients.
- Containers run as non-root with `no-new-privileges=true`.

## Troubleshooting

| Symptom                                                | Likely cause                                                                                                                              |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `docker compose up` build fails                        | Missing API keys in `.env` — re-run `./tools/install.sh`. Migration container failures usually mean Postgres isn't healthy yet.            |
| `tools/add_user.py` import errors                      | You haven't run `pip install -e ".[all]"`, or you're not using Python 3.12+.                                                              |
| MCP client redirects to `http://...` and dies          | You're behind a reverse proxy and `FORWARDED_ALLOW_IPS` isn't set to the proxy's CIDR. See [Running behind a reverse proxy](#running-behind-a-reverse-proxy). |
| MCP client gets `400 invalid_redirect_uri` on register | Your client is using a non-loopback redirect URI not in `OAUTH_REDIRECT_URI_ALLOWLIST`. Add the host (with port if non-standard) to that env var. |
| Search returns nothing                                 | Workers haven't ingested your content yet — `docker compose logs -f worker`. Or Qdrant isn't reachable; check `curl http://localhost:6333/collections`. |
| `tools/run_celery_task.py` says queue doesn't exist    | The queue name is `<APP_NAME>-<queue>` (default prefix `memory`). Run `python tools/run_celery_task.py --help` to see the queues this script wraps. |

### Logs

```bash
docker compose logs -f api          # API + MCP server
docker compose logs -f worker       # Celery workers
docker compose logs -f migrate      # one-shot migration runner
docker compose logs -f              # everything
```

`tools/diagnose.sh` runs a battery of read-only diagnostic commands against a deployed server.

## Custom tasks (deployment-specific)

Add scheduled or on-demand Celery tasks specific to your deployment without committing them to the main repo. Tasks have full access to the DB, Discord, GitHub, and the rest of the infrastructure.

### Setup

1. Create a directory for your custom tasks:
   ```bash
   mkdir /path/to/my/custom_tasks
   ```
2. Add to `.env`:
   ```
   CUSTOM_TASKS_DIR=/path/to/my/custom_tasks
   ```
3. Restart: `docker compose up -d --build`.

If `CUSTOM_TASKS_DIR` is not set, the feature is inert.

### Writing a task

Each `.py` file in the directory is a self-contained Celery task. Files starting with `_` are ignored (use them for templates or to disable a task).

**Periodic** (Celery Beat schedule):

```python
# my_custom_tasks/deadline_check.py
from celery.schedules import crontab
from memory.common.celery_app import app, register_custom_beat
from memory.common.db.connection import make_session

# register_custom_beat builds the task name "custom_tasks.deadline_check.run"
# and registers a Beat schedule. First arg must match the file name.
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

**On-demand** (no schedule):

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
celery -A memory.common.celery_app call custom_tasks.generate_report.run
```

See `custom_tasks/_example.py` and `custom_tasks/_example_manual.py` for full annotated templates.

### How loading works

1. At Celery startup, `celery_app.py` calls `load_custom_tasks()` which scans `CUSTOM_TASKS_DIR`.
2. Each `.py` file (not starting with `_`) is imported via `importlib`.
3. Importing executes `@app.task` registration and any `register_custom_beat()` calls.
4. All custom tasks route to the `custom` queue (`<APP_NAME>-custom`).
5. One broken file won't prevent others from loading — errors are logged per-file.

## Contributing

This is a personal knowledge base system. Feel free to fork and adapt for your own use cases.
