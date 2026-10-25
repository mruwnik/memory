# Memory — Knowledge Base

A self-hosted knowledge base that ingests, indexes, and provides semantic search over emails, documents, notes, web pages, ebooks, comics, and more. Exposes an MCP (Model Context Protocol) endpoint so AI assistants can query it directly, with team-based access control and multi-user support.

## Features

- **Multi-modal ingestion**: emails, documents, ebooks, comics, web pages, transcripts, photos
- **Semantic + keyword search**: vector embeddings (Voyage AI / OpenAI) merged with PostgreSQL BM25
- **MCP server**: AI assistants (Claude Desktop, Claude Code, Cursor, …) connect over OAuth
- **Observation system**: assistants can record long-term notes about user preferences and patterns
- **Multi-user with teams + projects** (optional modules): role-based access on every search result
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
  - All three are required. **Voyage** does all embeddings (`voyage-3-large` for text, `voyage-multimodal-3` for mixed) and reranking (`rerank-2.5`). **Anthropic** does query analysis, HyDE expansion, and summarization. **OpenAI** is used by various LLM-backed features (notes processing, observation extraction, etc.).

### 1. Run the installer

```bash
git clone <this-repo> memory && cd memory
./tools/install.sh
```

The installer will:
- Generate `secrets/postgres_password.txt` and a Redis password
- Ask for the Voyage API key (optional: leave it blank to set it on the settings page) and write it to `secrets/voyage_key.txt`
- Set DB / Redis / Qdrant defaults in `.env` (Postgres on `localhost:15432`, etc.)
- Detect your Docker group GID (needed for Claude session orchestration)
- Optionally generate an SSH keypair for git-based notes sync

Re-run it any time — it skips anything already configured.

For unattended installs, run `./tools/install.sh --non-interactive`. It never prompts: `DOMAIN`, `ADMIN_EMAIL` and `ADMIN_PASSWORD` are required (it exits before writing anything if one is missing), `VOYAGE_API_KEY` is optional (an admin can set it on the settings page instead), and `POSTGRES_PASSWORD` and `REDIS_PASSWORD` are used instead of generating them. It also requires an explicit backup choice, and exits before writing anything without one: `S3_BACKUP_BUCKET` (optionally `S3_BACKUP_PREFIX`, `S3_BACKUP_REGION`, `BACKUP_ENCRYPTION_KEY`) to back up to S3, or `S3_BACKUP=off`. Supplied values may only use `A-Za-z0-9._@:/+-`, except `ADMIN_PASSWORD`. It binds the API to `127.0.0.1:8000` for a reverse proxy on the same host, sets `SERVER_URL=https://$DOMAIN`, creates the `memory-api-<stack>` Docker network that the `docker-compose.claude.yaml` overlay uses, starts the api, and creates the admin user (full access) if it doesn't exist. To add users by hand without putting a password in argv, set `ADD_USER_PASSWORD` instead of `--password` for `tools/add_user.py`.

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

The simplest path is to run `add_user.py` inside the API container, which already has all dependencies and DB credentials wired up:

```bash
docker compose exec api python tools/add_user.py \
  --email you@example.com --password 'yourpass' --name 'Your Name' --admin
```

`--admin` gives the user full access (the `*` scope). Without it, a new user gets the default `read` scope.

Unless the `multi_user` module is on (`ENABLE_MODULES=multi_user`), an install has one human user: `add_user.py` gives that user `*` and refuses a second human, as does `POST /users`, and the api refuses to start if several humans already exist. Bot users and API keys are unaffected.

If you'd rather run it on the host (e.g. as part of a script), `pip install -e ".[all]"` first — but note that the host CLI needs to reach Postgres, which is **not** exposed by default. See [Local development](#local-development-without-docker-for-the-app) for how to publish the data-service ports. The other extras defined in `setup.py` are `api`, `common`, `dev`, `ingesters`, `workers`, and `all` (a superset).

### 4. Use it

- Web UI: <http://localhost:8000/ui> — log in with the account you just created
- API docs (OpenAPI): <http://localhost:8000/docs> — requires an authenticated session
- MCP endpoint: <http://localhost:8000/mcp>

## Modules

Three optional features are off by default and switched on in `.env` with `ENABLE_MODULES` (comma-separated), then a restart. `DISABLE_MODULES` turns names off again; an unknown name, or one in both variables, stops every process from starting.

| Module | Adds |
|---|---|
| `claude_containers` | Claude Code containers: the `/claude` routes, the `containers_*` MCP tools, the `claude_session` scheduled-task type, the `claude_sessions` scope, and the Claude sessions and configurations pages |
| `multi_user` | More than one human user. Off, `tools/add_user.py` and `POST /users` allow one human, who gets `*` |
| `teams` | Teams, their members and projects: routes, MCP servers, scopes, and the team and project pickers |

Everything else (search, ingest, people, notes, reports, journal, tasks, polls, agents, keys, transcript search) is always on. Modules never change who can see what. Off means 404 routes, unmounted MCP servers (their tools disappear from `tools/list` and from agent tool catalogs) and hidden scopes; data and existing scope grants stay, so turning a module back on restores it.

`claude_containers` also needs the compose overlay and a running orchestrator. Add both to `.env`:

```
ENABLE_MODULES=claude_containers
COMPOSE_FILE=docker-compose.yaml:docker-compose.claude.yaml
```

The overlay mounts the orchestrator socket, adds the Docker group (`DOCKER_GID`, default 992; `tools/install.sh` detects it) and attaches the api to the external `memory-api-<MEMORY_STACK>` network. Without it the api refuses to start with the module on.

## Updating

```bash
tools/update.sh --dry-run origin/master   # show the plan
tools/update.sh origin/master             # back up Postgres, update, migrate, verify
```

There are no release tags yet. Once `v*` tags exist, `tools/update.sh` with no argument picks the newest. The script leaves the checkout on a detached HEAD.

See **[UPDATING.md](UPDATING.md)** for verification, rollback and post-update tasks, and [CHANGELOG.md](CHANGELOG.md) for what each release requires.

## Connecting an MCP client

The MCP server uses OAuth (Dynamic Client Registration per RFC 7591), so any compliant MCP client can enroll itself the first time it connects. Point your client at `http://<host>:8000/mcp` and it will walk you through the login flow on first use.

- Claude Code: `claude mcp add --transport http memory http://localhost:8000/mcp`
- Claude Desktop and claude.ai: in Settings, under Connectors, add a custom connector with the URL.
- Cursor (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "memory": {
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

A proxy on the same host that talks to a published port (for example `API_PORT=127.0.0.1:8000`) doesn't arrive from loopback. Docker forwards it from the gateway of the api's default-gateway network, which has no fixed address. `deploy/aws/compose.proxy.yaml` fixes that. It makes `kbnet` that network, at `KBNET_SUBNET`/`KBNET_GATEWAY` (default `10.213.47.0/24` and `10.213.47.1`), and needs Docker Engine 28 or later. `tools/install.sh --non-interactive` enables it with `COMPOSE_FILE=docker-compose.yaml:deploy/aws/compose.proxy.yaml` in `.env` and trusts `127.0.0.1,::1,$KBNET_GATEWAY`. Give each stack on one host its own subnet.

The `docker-compose.yaml` passes both variables through to the container with safe loopback defaults (`127.0.0.1,::1`), so you don't need to touch the compose file — setting them in `.env` is enough. Restart with `docker compose up -d --build` to apply.

> **Why this is a footgun**: `docker compose --env-file foo.env` only feeds *YAML interpolation*, not container runtime environment. Vars set there reach a service only if the service's `environment:` mapping passes them through. `FORWARDED_ALLOW_IPS` and `RATE_LIMIT_TRUSTED_PROXIES` are explicitly passed through; vars not in that mapping won't reach the container regardless of what's in `.env`.
>
> **CIDR support requires uvicorn ≥ 0.31** — earlier versions did plain string-set membership and silently ignored CIDR strings. The repo now pins `uvicorn>=0.34`. If you're on a deployment with `uvicorn==0.29`, set `FORWARDED_ALLOW_IPS` to either `*` (acceptable when the api container has no host port mapping and is only reachable through the reverse proxy) or to the proxy's exact IP, not a CIDR.

## Local development (without Docker for the app)

If you want to run the API or workers on the host while keeping infrastructure in Docker, you first need to publish the data-service ports — `docker-compose.yaml` keeps them on the internal `kbnet` network by default. Create a `docker-compose.override.yml` (gitignored) like:

```yaml
services:
  postgres:
    ports: ["15432:5432"]
  redis:
    ports: ["16379:6379"]
  qdrant:
    ports: ["6333:6333"]
```

Then:

```bash
# Bring up only postgres, redis, qdrant
./dev.sh

# Install Python deps in a virtualenv
pip install -e ".[all]"

# Apply migrations (the docker-compose `migrate` service does this for the
# Docker flow; for host-side dev you run it yourself)
alembic -c db/migrations/alembic.ini upgrade head

# Run the API with auto-reload
RELOAD=true python -m memory.api.app

# In another terminal, run a worker (queue names from docker-compose.yaml)
celery -A memory.common.celery_app worker -Q memory-email,memory-blogs,memory-notes
```

On macOS, run host-side workers with `--pool=solo` (one per concurrent task): forked prefork children segfault in libpq and the system TLS code. Linux and the Docker images are unaffected.

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

This requires SSH keys in `secrets/` — `tools/install.sh` can generate them (option in the SSH section). To create them by hand: generate an ed25519 keypair into `secrets/ssh_private_key` / `secrets/ssh_public_key` and populate `secrets/ssh_known_hosts` (e.g. `ssh-keyscan github.com`). Add the public key to your git provider before the first push.

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

Queues live in `src/memory/common/celery_app.py` (`task_routes`) and the full set is enumerated in `docker-compose.yaml` (the worker's `QUEUES` env var). At time of writing: `backfill, backup, blogs, calendar, comic, custom, discord, ebooks, email, forums, github, google, maintenance, meetings, notes, people, photos, reports, scheduler, slack, verification`.

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
  { method: 'core_list_items', params: { limit: 5 } }
]).then(([people, items]) => { /* … */ });
```

Reports must be created with `allow_scripts=True` for this to load (set via the UI / API on the report record). The library uses the `access_token` cookie for auth, so reports inherit the viewing user's permissions.

## Configuration

Configuration lives in `.env` (or environment variables on the host). LLM features (summaries, HyDE, query analysis, meeting notes, agents) run on each user's own Anthropic or OpenAI key, added under LLM API keys (`/ui/keys`); the server holds none. Summaries and search steps use the default Anthropic key, else the default OpenAI one. An admin can also set the Voyage key, the search `ENABLE_*` switches and `DISABLED_MCP_SERVERS` on the settings page (`/settings` API); a value set in the env wins and shows there read-only. Key settings:

| Variable                          | Default              | Purpose                                                        |
| --------------------------------- | -------------------- | -------------------------------------------------------------- |
| `SERVER_URL`                      | `http://localhost:8000` | Public origin; used for OAuth redirects, CORS, cookies      |
| `ENABLE_MODULES`, `DISABLE_MODULES` | unset (all modules off) | Comma-separated modules to turn on or off: `claude_containers`, `multi_user`, `teams` (see Modules) |
| `ORCHESTRATOR_SOCKET`             | set by `docker-compose.claude.yaml` | Unix socket of the Claude container orchestrator; required by `claude_containers` |
| `PASSWORD_RESET_FROM_EMAIL`       | unset (reset off)    | Address of an admin-owned, send-enabled email account that mails "Forgot password?" links; its owner can read every link |
| `DB_HOST`, `DB_PORT`              | `postgres` / `5432`  | PostgreSQL — set to `localhost` / `15432` for host-side dev   |
| `REDIS_HOST`, `REDIS_PORT`        | `redis` / `6379`     | Celery broker + cache                                          |
| `QDRANT_HOST`, `QDRANT_PORT`      | `qdrant` / `6333`    | Vector store (workers also accept `QDRANT_URL` for the full URL)|
| `VOYAGE_API_KEY`                  | unset                | All embeddings + reranking. Without it search is keyword-only and new items aren't embedded until an admin sets it on the settings page |
| `TEXT_EMBEDDING_MODEL`            | `voyage-3-large`     | Voyage text embedding model (1024d)                            |
| `MIXED_EMBEDDING_MODEL`           | `voyage-multimodal-3`| Voyage mixed text+image embedding model                        |
| `RERANK_MODEL`                    | `rerank-2.5`         | Voyage reranker used after candidate retrieval                 |
| `FILE_STORAGE_DIR`                | `/tmp/memory_files`  | Where uploaded content is stored                               |
| `S3_BACKUP_BUCKET`                | unset (backups off)  | Bucket for daily encrypted backups; setting it turns them on and requires `BACKUP_ENCRYPTION_KEY` (the workers refuse to start without it). The workers also need AWS credentials |
| `S3_BACKUP_PREFIX`, `S3_BACKUP_REGION` | `memory` / `eu-central-1` | Key prefix inside the bucket and the bucket's region |
| `BACKUP_ENCRYPTION_KEY`           | unset                | Passphrase for the backups; `tools/install.sh` generates one when you choose S3 backups. Keep a copy elsewhere: backups can't be decrypted without it |
| `FORWARDED_ALLOW_IPS`             | `127.0.0.1,::1`      | Trusted-proxy IPs/CIDRs for `X-Forwarded-*` (see proxy section)|
| `RATE_LIMIT_TRUSTED_PROXIES`      | `127.0.0.1,::1`      | Trusted-proxy IPs/CIDRs for the rate-limit bucket key          |
| `KBNET_SUBNET`, `KBNET_GATEWAY`   | `10.213.47.0/24`, `.1` | Internal network's address with `deploy/aws/compose.proxy.yaml`; give each stack on a host its own |
| `ENABLE_BM25_SEARCH`              | `true`               | Postgres BM25 alongside vector search                          |
| `ENABLE_HYDE_EXPANSION`           | `false`              | Hypothetical Document Embeddings query expansion               |
| `ENABLE_QUERY_ANALYSIS`           | `true`               | LLM intent extraction from queries                             |
| `ENABLE_RERANKING`                | `true`               | Voyage reranking of fused results                              |
| `ENABLE_SEARCH_SCORING`           | `true`               | Recency / popularity / title-match score boosts                |
| `DISABLED_MCP_SERVERS`            | unset                | Comma-separated MCP servers to leave unmounted (set on the settings page, it hides their tools instead) |
| `API_SELF_RESTART`                | `false` (`1` in compose) | Lets an admin restart the api from the settings page; the container's restart policy brings it back |
| `CUSTOM_TASKS_DIR`                | unset                | Path to a directory of deployment-specific Celery tasks        |
| `LATEST_RELEASE_URL`              | unset                | URL returning the latest release tag; enables `update_available` in `/health` |
| `CLAUDE_MAINTENANCE_INTERVAL_SEC` | `300`                | How often the api settles scheduled Claude sessions and removes exited containers; `0` turns it off |
| `CLAUDE_SESSION_TIMEOUT_HOURS`    | `24`                 | A scheduled Claude session still running after this is marked failed (container left running) |
| `AGENT_DEFAULT_USER_KEY_RUN_COST_USD` | `1.00`           | Per-run cap for runs on a user's own provider key when the agent sets no `max_cost_usd` |
| `AGENT_DEFAULT_USER_KEY_DAILY_COST_USD` | `20.00`        | Daily cap on a user's own provider key when neither the key nor the agent sets a daily cap |
| `AGENT_MAX_TIMEOUT_SEC`           | `1800`               | Longest `timeout_sec` an agent spec may set                    |
| `AGENT_MAX_WAIT_SEC`              | `300`                | Longest `agent_wait` block                                     |
| `AGENT_LLM_TIMEOUT_SEC`           | `600`                | Timeout for one agent model request                            |
| `AGENT_TOOL_RESULT_MAX_CHARS`     | `50000`              | Tool results longer than this are truncated                    |
| `AGENT_CONTEXT_TRIM_AT_TOKENS`    | `100000`             | Context size at which old tool results are stubbed             |
| `AGENT_CONTEXT_TRIM_TO_TOKENS`    | `60000`              | Context size trimming aims for                                 |
| `AGENT_STEP_RETENTION_DAYS`       | `90`                 | Days before unstarred run transcripts are deleted              |
| `AGENT_MAX_RUNS_PER_HOUR`         | `60`                 | Most runs one agent definition may start per hour; definitions can only lower it |
| `AGENT_MAX_TRIGGERS_PER_USER`     | `20`                 | Most ingest triggers a user may have enabled at once           |
| `AGENT_WORKER_CONCURRENCY`        | `2`                  | Concurrent agent runs in `agents-worker` (~270MB each; raise its memory limit with it) |
| `MCP_BLOCKED_NETWORKS`            | *(empty)*            | Comma-separated CIDRs external MCP servers may never reach, on top of the built-in list (see Security notes) |
| `MCP_BLOCK_CONTAINER_NETWORKS`    | `false` (`1` in the images) | Also block every network the container is attached to; fails closed if they can't be read |
| `MCP_MAX_SERVERS_PER_USER`        | `20`                 | External MCP servers one user may register                     |
| `MCP_MAX_TOOLS_PER_SERVER`        | `200`                | Tools cached per external server; extras are reported, not offered |
| `MCP_MAX_TOOL_DESCRIPTION_CHARS`  | `4000`               | External tool descriptions are truncated to this               |
| `MCP_MAX_TOOL_SCHEMA_BYTES`       | `16384`              | External tools with a larger input schema are unavailable      |
| `MCP_MAX_RESPONSE_BYTES`          | `10485760`           | Largest response (or SSE event) read from an external server   |
| `MCP_CALL_TIMEOUT_SEC`            | `120`                | Timeout for one external tool call                             |
| `MCP_CONNECT_TIMEOUT_SEC`         | `30`                 | Deadline for connecting to an external server and listing its tools |
| `MCP_TOKEN_REFRESH_SKEW_SEC`      | `300`                | Refresh OAuth tokens this long before they expire              |
| `MCP_OAUTH_STATE_TTL_SEC`         | `600`                | How long an external server's authorization link stays valid   |
| `MCP_SERVER_CHANGES_PER_HOUR`     | `30`                 | Registrations, authorizations and tool refreshes per user per hour |

See `src/memory/common/settings.py` for the full list and `docker-compose.yaml` for how variables map into containers.

## Security notes

- The MCP endpoint uses OAuth; the rest of the API uses session cookies + bearer tokens. Don't put the API on the public internet without a reverse proxy that terminates TLS — the OAuth handshake won't complete over plain HTTP for clients that enforce HTTPS.
- Secrets live in `secrets/` (file-mounted) and `.env` (env-var-mounted). Both are gitignored.
- The OAuth redirect-uri allowlist defaults to `http://localhost,http://127.0.0.1` and accepts ephemeral ports for those loopback hosts (RFC 8252 native-app flow). Set `OAUTH_REDIRECT_URI_ALLOWLIST` to add additional origins for browser-based MCP clients.
- Containers run as non-root with `no-new-privileges=true`.
- `/health` is unauthenticated and reports the running version and alembic revision (never content), so anyone who can reach it can tell which release you run.
- **External MCP servers can reach your LAN.** Users register servers by URL, and private and LAN addresses are allowed so a server on your own network (a Raspberry Pi, say) works. That means **every user of the instance** can make the server dial hosts on those networks. Always blocked, with no override: loopback, link-local and cloud metadata (169.254.0.0/16, fe80::/10, fd00:ec2::254, 100.100.100.200), unspecified/multicast/broadcast, the default docker and podman pools (172.16.0.0/12, 10.88.0.0/16, 10.89.0.0/16), this instance's own host on every port, the compose services by name and address, and (in the images) every network the container is attached to. Each address is checked when it's dialed, so DNS rebinding doesn't get around it. Block more with `MCP_BLOCKED_NETWORKS`; if the host publishes ports on `0.0.0.0`, add its own LAN address (`MCP_BLOCKED_NETWORKS=192.168.1.10/32`), and on a multi-network host add each network it shouldn't reach. Fine for single-owner deployments; think before giving other people the `agents` scope.
- External MCP tool descriptions and results are untrusted text, like `web_fetch` output. An agent whose allowlist combines an external tool, your private data (e.g. `core_search`) and a way out (`web_fetch`, or any tool with side effects, external or not) can be steered by injected instructions into leaking that data. Ingest triggers feed unvetted content to an agent the same way. The editor warns about these combinations; it doesn't block them. Registration and connection errors are deliberately generic ("couldn't reach an MCP server at that URL"); the details are in the api logs.

## Troubleshooting

| Symptom                                                | Likely cause                                                                                                                              |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Container exits with `Refusing to start: missing required settings` | Set the listed settings in `.env` (or as `NAME_FILE` secrets) and restart. |
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
