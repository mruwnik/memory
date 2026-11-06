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
- Optionally generate an SSH keypair for git-based notes sync

[Modules](#modules) are switched on in the setup wizard or on the Server settings page.

Re-run it any time — it skips anything already configured.

For unattended installs, run `./tools/install.sh --non-interactive`. It never prompts: `DOMAIN` is required (it exits before writing anything without it), `ADMIN_EMAIL` and `ADMIN_PASSWORD` are optional but go together, `VOYAGE_API_KEY` is optional (an admin can set it on the settings page instead), and `POSTGRES_PASSWORD` and `REDIS_PASSWORD` are used instead of generating them. It also requires an explicit backup choice, and exits before writing anything without one: `S3_BACKUP_BUCKET` (optionally `S3_BACKUP_PREFIX`, `S3_BACKUP_REGION`, `BACKUP_ENCRYPTION_KEY`) to back up to S3, or `S3_BACKUP=off`. Supplied values may only use `A-Za-z0-9._@:/+-`, except `ADMIN_PASSWORD`. It binds the API to `127.0.0.1:8000` for a reverse proxy on the same host, sets `SERVER_URL=https://$DOMAIN`, runs the migrations, starts the api, and creates the admin user (full access) from `ADMIN_EMAIL` and `ADMIN_PASSWORD` if it doesn't exist. Without them it prints the setup token to claim the instance with at `https://$DOMAIN/setup` (or, if the token can't be read from the api log, the command that shows it). To add users by hand without putting a password in argv, set `ADD_USER_PASSWORD` instead of `--password` for `tools/add_user.py`.

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

Open <http://localhost:8000/setup> to create the first account as an admin. It asks for the setup token the api logs at startup while no account exists:

```bash
docker compose logs api | grep "setup token"
```

Or run `add_user.py` inside the API container, which already has all dependencies and DB credentials wired up. Run `docker compose run --rm migrate` first if the stack has only just started, so the tables exist:

```bash
docker compose exec api python tools/add_user.py \
  --email you@example.com --password 'yourpass' --name 'Your Name' --admin
```

`--admin` gives the user full access (the `*` scope). Without it, a new user gets the default `read` scope.

Unless the `multi_user` module is on (see [Modules](#modules)), an install has one human user: `add_user.py` gives that user `*` and refuses a second human, as does `POST /api/users`, and the api refuses to start if several humans already exist. Bot users and API keys are unaffected.

If you'd rather run it on the host (e.g. as part of a script), `pip install -e ".[all]"` first — but note that the host CLI needs to reach Postgres, which is **not** exposed by default. See [Local development](#local-development-without-docker-for-the-app) for how to publish the data-service ports. The other extras defined in `setup.py` are `api`, `common`, `dev`, `ingesters`, `workers`, and `all` (a superset).

### 4. Use it

- Web UI: <http://localhost:8000> — log in with the account you just created
- API docs (OpenAPI): <http://localhost:8000/docs> — requires an authenticated session
- MCP endpoint: <http://localhost:8000/mcp>

## Modules

Two optional features are off by default. An admin switches them on the Server settings page (`/server-settings`), then restarts the api from its banner. An operator can instead name them in `.env` with `ENABLE_MODULES` or `DISABLE_MODULES` (comma-separated), then restart every service: a module named there is locked on the page. An unknown name, or one in both variables, stops every process from starting; the removed `claude_containers` is ignored with a warning.

The page refuses a value the api can't run with (`multi_user` off while several humans exist). If the environment later changes under a saved value, the api logs an ERROR, boots with the module the other way, and the page flags the row. Workers follow what the api runs, which it records at startup, rather than the saved values: the agents worker catches up at the start of each agent task, mounting the servers of a module switched on and hiding those of one switched off.

| Module | Adds |
|---|---|
| `multi_user` | More than one human user. Off, `tools/add_user.py` and `POST /api/users` allow one human, who gets `*` |
| `teams` | Teams, their members and projects: MCP servers, scopes, and the team and project pickers |

Everything else (search, ingest, people, notes, reports, journal, tasks, polls, agents, keys, transcript search) is always on. Modules never change who can see what. Off means 404 routes, unmounted MCP servers (their tools disappear from `tools/list` and from agent tool catalogs) and hidden scopes; data and existing scope grants stay, so turning a module back on restores it.

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

Notes can be pushed to a git repository whenever they're modified, and pulled back on an interval. An admin sets this up in **Sources → Notes**: the remote, the commit author, and for an https remote an access token (write-only, like LLM keys; `NOTES_GIT_TOKEN` in the env wins). A vault that is already a git repository keeps its history and its remote. The sync interval is on **Server settings** (`NOTES_SYNC_INTERVAL` in the env wins).

SSH identities are managed in **System operations → SSH keys**. Paste or upload an existing OpenSSH/PEM private key (Ed25519, RSA ≥2048 bits, or ECDSA), or generate an Ed25519 key. For an encrypted file, enter its passphrase once; Memory encrypts the unlocked key using `SECRETS_ENCRYPTION_KEY` for unattended use. Private keys and passphrases are never returned by the API. Public keys and fingerprints can be copied from the UI. Register the public key with your Git account or server with the access you need.

Personal keys belong to their user. Admins can also create **Instance** keys. In **Sources → Notes**, choose one of your personal or instance keys and click **Check server**. Review the hostname, port, and SHA256 fingerprints, then choose **Trust this server** to save the SSH configuration. Confirm first-connection fingerprints with your server administrator when possible. Use **Advanced: enter trusted host keys manually** to paste independently verified keys instead. Then **Save and sync**. A changed server key stops sync until you explicitly approve new trust. Host trust is bound to the configured remote. Workers read changes without a restart and remove temporary private-key files after each Git operation. SSH sync requires a selected database key; operator-mounted identities are not used.

Replacing or deleting a key affects future connections. A key selected for Notes cannot be deleted until the selection is changed or cleared.

### Fireflies transcripts

Add the account in **Sources → Transcripts** with a Fireflies API key (fireflies.ai → Settings → Developer settings). Saving it generates a webhook secret and shows it once with the account's ingest URL, `<SERVER_URL>/transcripts/ingest/<account id>`. In Fireflies' [Webhooks V2 settings](https://app.fireflies.ai/integrations/api/webhook), set that URL as the webhook URL and the secret as the signing secret, subscribe to `meeting.transcribed`, and turn on **Resend when a meeting's notes change** so corrected transcripts come through. **Webhook** on the account row shows the URL again and rotates the secret.

Fireflies only sends webhooks for meetings you organize. Meetings other people organize, and any webhook that didn't arrive, come in through a daily check of the last 14 days at 05:10 UTC. **Full rescan** walks back `TRANSCRIPTS_RESCAN_LOOKBACK_DAYS` (365). A resend (`meeting.transcribed` or `meeting.summarized` carrying `reprocessed_at`) with a changed transcript replaces the stored meeting and reruns extraction: its action items still `pending` are recreated, and ones already started, done or cancelled are kept but detached.

Behind a reverse proxy, `/transcripts/ingest/` must reach the api without a login: it authenticates with the webhook signature, not a session.

### Discord integration

Add a bot in **Sources → Discord** with its name and token (the application ID
is read from the token). Every bot reads and sends with its token over the REST
API, and a daily catch-up (02:30 UTC) refreshes
its servers and channels, re-reads the last `DISCORD_CATCH_UP_DAYS` (2) of each
collected channel to store messages that didn't arrive and apply missed edits,
and continues the history backfill. Missed deletes and reactions are not
repaired. Several people can each add the same application with their own
token; a message they can all see is stored once.

**Real-time delivery** picks how new messages arrive; switch it any time with
**Edit**, and the ingest hub picks the change up within a minute:

- **WebSocket gateway** (the default): the ingest hub keeps a gateway
  connection open for the bot.
- **Webhook**: the bot gets signed
  [Webhook Events](https://docs.discord.com/developers/events/webhook-events)
  instead. Enter the Ed25519 public key that signs them, and paste the bot's
  events URL, `<SERVER_URL>/discord/events/<bot id>`, as the webhook URL. The
  endpoint answers a signed PING with 204 and a bad signature (or a bot not in
  webhook mode) with 401. Events carry gateway payloads (`MESSAGE_CREATE`,
  `MESSAGE_UPDATE`, `MESSAGE_DELETE`, `MESSAGE_DELETE_BULK`, the four
  `MESSAGE_REACTION_*` events, `THREAD_CREATE`, `THREAD_UPDATE`); others are
  acknowledged and ignored. Servers and DMs first seen through a webhook bot
  collect everything it receives; turn collection off per server or channel as
  usual.

A signing key only proves which Memory user sent an event, so pushes are
limited to that user's own rows: edits, deletes and reactions only touch
messages their bots delivered or that sit in servers (or DMs) their bots own,
and a push never renames or claims an existing server or channel, changes a
stored Discord user's name, or links an author to a person by username. A
server first seen in a pushed event isn't listed or manageable until the
token confirms it (the gateway, or the daily REST listing), which takes it
over with default access settings. A DM is managed by the owner of the bot
that first delivered it.

Under **Advanced**, **API base URL** points the bot at any server speaking
Discord's API (default `https://discord.com/api/v10`). REST calls go there with
`Authorization: Bot <token>`, and a gateway bot asks it for the gateway
(`GET <API base URL>/gateway/bot`, which must return a `wss://` URL on the same
allowlist as the API URL) and connects there with zlib-stream compression. Bots on `discord.com` connect to Discord's gateway directly.

To get notifications in Discord, [create a bot](https://discord.com/developers/applications), then run:

```bash
python tools/discord_setup.py generate-invite --bot-token <your bot token>
```

That returns an invite URL for adding the bot to your server.

Behind a reverse proxy, `/discord/events/` must reach the api without a login:
it authenticates with the signature, not a session.

### Beeper integration

[Beeper](https://www.beeper.com) bridges WhatsApp, Signal, Telegram, Slack,
Discord and more into one Matrix account. Memory reads it through **Beeper
Server**, Beeper's headless client, and collects the messages of the networks
connected in the Beeper app. Memory never connects networks or sends messages.

> [!WARNING]
> Never publish the Beeper Server's port, nor put it on any network other
> than `beepernet`. Until an account is signed in, its setup endpoints need no
> authentication: anyone who reaches it can sign it into their own account.
> After sign-in, its token reads and sends every message of every network.

1. Start it with the stack: `docker compose --profile beeper up -d`. The
   `beeper` service builds a pinned release (closed source, unsigned, no
   licence; Beeper's terms allow personal use) and joins only `beepernet`,
   with `api`, `worker` and `ingest-hub`. It needs about 400 MB of RAM.
2. Allow it: `BEEPER_SERVER_URLS=http://beeper:23373` in `.env`. One Server
   holds one Beeper account; for more accounts run more Servers (each with its
   own data volume) and list each URL.
3. In **Sources → Beeper**, add an account on that Server, enter its email and
   the emailed code, then approve the device: compare the emoji with another
   Beeper device, or paste the account's recovery key. Memory refuses an email
   with no Beeper account; create it in the Beeper app.
4. Choose per network whether to collect, and its project and sensitivity.
   Chats inherit their network's collect setting and sensitivity and can
   override them.

Only networks connected through **Beeper Cloud** reach the Server. A network
connected "on this device" in the Beeper app stays on that device; reconnect
it with Beeper Cloud for memory to see it. Some networks (Slack, Discord,
Google Chat, LINE, Google Messages) are cloud-only.

The ingest hub keeps a WebSocket open to each active, verified account and
queues its message and chat events. The socket has no replay, so a
(re)connect (unless a reconcile ran in the last 5 minutes), and every
six hours (00:20, 06:20, 12:20 and 18:20 UTC), re-reads each collected chat
from where it stopped. A chat's first read backfills its history as far back
as the Server has it, `BEEPER_SYNC_MAX_MESSAGES_PER_RUN` messages per run; a
chat whose Server keeps finding no older history for about an hour pauses
there and shows "History incomplete"; each reconcile tries again, and the
network reconnecting or collection being turned back on resumes it. The account shows whether its
WebSocket is connected. Edits update messages in place, deletes remove them, and
reactions are stored on the message they react to. Images are stored under
`BEEPER_STORAGE_DIR`; voice-note transcriptions are stored as text. A Server
that loses its sign-in or verification, or answers as another Beeper user,
shows it on the account, and memory stops reading it until it is verified
again.

Messages are owned by the account's owner and take their project from the
chat, else its network, and likewise their sensitivity. Each is indexed
as `[WhatsApp] Family — Alice: text`, and its metadata names the network,
chat, chat type and sender. Search them with `core_search`, narrowed with the
`network` (e.g. `whatsapp`, `signal`) and `chat` filters; the
`beeper_list_chats` MCP tool lists your chats.

### Manually triggering Celery tasks

```bash
python tools/run_celery_task.py <queue> <task-name> [args]
```

Queues live in `src/memory/common/celery_app.py` (`task_routes`) and the full set is enumerated in `docker-compose.yaml` (the worker's `QUEUES` env var). At time of writing: `backfill, backup, blogs, calendar, comic, custom, discord, ebooks, email, forums, github, google, maintenance, meetings, notes, people, photos, reports, scheduler, slack, slack-sync, verification`.

## MCP client library for HTML reports

If you're embedding interactive content in stored reports, the API serves a small JS client at `/mcp-client.js` that authenticates via the user's session cookie and exposes the MCP tool surface as a JS API:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset='utf-8'>
  <title>My Report</title>
  <script src="/mcp-client.js"></script>
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

Configuration lives in `.env` (or environment variables on the host). LLM features (summaries, HyDE, query analysis, meeting notes, agents) run on each user's own Anthropic or OpenAI key, added under LLM API keys (`/keys`); admins can optionally designate their keys as shared system defaults for everyone. On the keys page, admins can choose **Use as system default** or **Clear system default**. One shared default is allowed per provider; clearing or deleting it leaves no shared default for that provider. System agents use personal defaults first, then shared defaults, while explicit source keys and **No LLM** retain precedence. Shared usage counts against the selected key’s daily cap. Custom agent runs still require personal keys. Summaries and search steps use the default Anthropic key, else the default OpenAI one. An admin can also set the Voyage key and the search `ENABLE_*` switches on the settings page (`/api/settings` API), where a value set in the env wins and shows read-only, and switch MCP servers off there. Key settings:

| Variable                          | Default              | Purpose                                                        |
| --------------------------------- | -------------------- | -------------------------------------------------------------- |
| `SERVER_URL`                      | `http://localhost:8000` | Public origin; used for OAuth redirects, CORS, cookies      |
| `ENABLE_MODULES`, `DISABLE_MODULES` | unset (all modules off) | Comma-separated modules to turn on or off, locking them on the settings page: `multi_user`, `teams` (see Modules) |
| `PASSWORD_RESET_FROM_EMAIL`       | unset (reset off)    | Address of an admin-owned, send-enabled email account that mails "Forgot password?" links; its owner can read every link |
| `DB_HOST`, `DB_PORT`              | `postgres` / `5432`  | PostgreSQL — set to `localhost` / `15432` for host-side dev   |
| `REDIS_HOST`, `REDIS_PORT`        | `redis` / `6379`     | Celery broker + cache                                          |
| `QDRANT_HOST`, `QDRANT_PORT`      | `qdrant` / `6333`    | Vector store (workers also accept `QDRANT_URL` for the full URL)|
| `VOYAGE_API_KEY`                  | unset                | All embeddings + reranking. Without it search is keyword-only and new items aren't embedded until an admin sets it on the settings page |
| `TEXT_EMBEDDING_MODEL`            | `voyage-3-large`     | Voyage text embedding model (1024d)                            |
| `MIXED_EMBEDDING_MODEL`           | `voyage-multimodal-3`| Voyage mixed text+image embedding model                        |
| `RERANK_MODEL`                    | `rerank-2.5`         | Voyage reranker used after candidate retrieval                 |
| `FILE_STORAGE_DIR`                | `/tmp/memory_files`  | Where uploaded content is stored                               |
| `S3_BACKUP_BUCKET`                | unset (backups off)  | Bucket for daily encrypted backups (files at 02:00 UTC); setting it turns them on and requires `BACKUP_ENCRYPTION_KEY` (the workers refuse to start without it). The workers also need AWS credentials |
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
| `API_SELF_RESTART`                | `false` (`1` in compose) | Lets an admin restart the api from the settings page; the container's restart policy brings it back |
| `CUSTOM_TASKS_DIR`                | unset                | Path to a directory of deployment-specific Celery tasks        |
| `LATEST_RELEASE_URL`              | unset                | URL returning the latest release tag; enables `update_available` in `/health` |
| `AGENT_DEFAULT_USER_KEY_RUN_COST_USD` | `1.00`           | Per-run cap for runs on a user's own provider key when the agent sets no `max_cost_usd` |
| `AGENT_DEFAULT_USER_KEY_DAILY_COST_USD` | `20.00`        | Daily cap on a user's own provider key when neither the key nor the agent sets a daily cap |
| `AGENT_MAX_TIMEOUT_SEC`           | `1800`               | Longest `timeout_sec` an agent spec may set                    |
| `AGENT_MAX_WAIT_SEC`              | `300`                | Longest `agent_wait` block                                     |
| `AGENT_LLM_TIMEOUT_SEC`           | `600`                | Timeout for one agent model request                            |
| `AGENT_TOOL_RESULT_MAX_CHARS`     | `50000`              | Tool results longer than this are truncated                    |
| `AGENT_CONTEXT_TRIM_AT_TOKENS`    | `100000`             | Context size at which old tool results are stubbed             |
| `AGENT_CONTEXT_TRIM_TO_TOKENS`    | `60000`              | Context size trimming aims for                                 |
| `AGENT_RUN_RETENTION_DAYS`        | `14`                 | Days a finished, unstarred run is kept when its agent sets no `retention_days` |
| `AGENT_MAX_SPAWN_DEPTH`           | `2`                  | How deep runs may start runs (`agent_run` in an agent's tools); top-level runs are depth 0 |
| `AGENT_MAX_RUNS_PER_HOUR`         | `60`                 | Most runs one agent definition may start per hour; definitions can only lower it |
| `AGENT_WORK_LEASE_DEFAULT_SEC`, `AGENT_WORK_LEASE_MIN_SEC`, `AGENT_WORK_LEASE_MAX_SEC` | `600` / `30` / `3600` | How long one `agent_work_claim` lease holds a run placed on workers, and the bounds a worker's `lease_sec` is kept within |
| `AGENT_WORK_HOLD_MAX_SEC`         | `86400`              | Longest workers may hold a run in all, from its first claim across renewals and re-claims; past it the run fails |
| `AGENT_WORK_MAX_LAPSES`           | `3`                  | Lapsed worker leases after which the run fails instead of going back for claiming |
| `AGENT_WORK_MAX_RESULT_BYTES`     | `1048576`            | Largest result a worker may submit                             |
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
- The OAuth redirect-uri allowlist always includes `http://localhost` and `http://127.0.0.1` (with any port, for the RFC 8252 native-app flow), the connector callbacks of claude.ai, claude.com, chatgpt.com and grok.com, and the server's own origin. Set `OAUTH_REDIRECT_URI_ALLOWLIST` to add other origins.
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

### Slack event ingestion and history sweeps

An app switches from polling to events once Slack delivers a message from a connected workspace (wizard step 6 waits for one). Pasting a new signing secret sends it back to polling until Slack delivers another message.
Verified installations ingest new messages through Events API; background history
sweeps default to daily. Set **Sources → Slack → Background sweep frequency** per
workspace, or use **Sync now** for an immediate sweep. Initial imports run immediately.
If any polling app for a workspace lacks verified events, normal polling continues.
The scheduler checks on `SLACK_SYNC_INTERVAL` (default one hour), so sweeps run on
the next scheduler tick after their interval expires.

The wizard's **Advanced** section takes an **Authorize URL**, **Token URL** and
**API base URL** for a Slack-compatible server (defaults `https://slack.com/oauth/v2/authorize`,
`https://slack.com/api/oauth.v2.access` and `https://slack.com/api`). With a
custom authorize URL the wizard asks for user scopes only. Several users can
register the same client ID, one app each. An events URL registered without the
wizard's `wizard_nonce` is verified by its signature alone. An app only takes
events for workspaces it was authorized into, and only its own token is used
for them (file downloads send it only to `files.slack.com` or the app's API host).

Custom Slack and Discord URLs are limited to an allowlist, since Memory sends
tokens and OAuth codes there: Slack's and Discord's own origins, plus the
comma-separated https origins in `CHAT_API_ALLOWED_ORIGINS` (for example
`https://chat-proxy.equistamp.io`). Admins may set any URL. The list is checked
when a URL is saved and again whenever it is used.

Invite the bot to channels whose messages it should receive and subscribe to the
matching message events. History sweeps cover enabled channels accessible to the
connected user. Slack allows one Events API Request URL per app: use separate
Slack apps for independent local and production installations. The dedicated
`slack-sync-worker` handles history polling while live events use the `slack` queue.
