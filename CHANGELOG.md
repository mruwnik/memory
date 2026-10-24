# Changelog

Each release lists what an operator must do to update. See [UPDATING.md](UPDATING.md)
for the procedure. Use these sections in every entry, writing "None" when a
section has nothing in it:

- **Required env vars**: new settings without which the api/workers refuse to start.
- **Manual steps**: anything `tools/update.sh` doesn't do, e.g. a Postgres
  image change (major versions need a dump and restore, not just a new image).
- **Other changes for operators**: new optional settings, changed defaults,
  removed features, Qdrant changes.
- **Migrations**: new alembic revisions; flag any that are slow or lock large tables.
- **Post-deploy commands**: run after the update, in order. Each must be safe to re-run.

## Unreleased

### Required env vars

- `SECRETS_ENCRYPTION_KEY` must be set to register external MCP servers (their
  tokens, client secrets and headers are encrypted with it). Registration fails
  with a clear error without it; nothing else changes.
- The api and workers now check settings at startup and refuse to start,
  listing everything that's missing. Both require `VOYAGE_API_KEY`. Workers
  also require the key for `SUMMARIZER_MODEL`'s provider: `ANTHROPIC_API_KEY`
  by default, or `OPENAI_API_KEY` for `openai/` models. A working deployment
  already has these.

### Manual steps

- **Before updating**, check each transcript account's project and
  sensitivity (Sources → Transcripts). Once updated, newly synced meetings and,
  from the next daily sweep, existing ones take them on: every contributor on that
  project will see every meeting synced from it, and its action items, and a
  `public` account's meetings are visible to every user. Raise the sensitivity
  (`internal` for leads, `confidential` for admins), or move the account to a
  narrower project, wherever that is too wide.
- External MCP servers must be registered again (Agents → MCP servers): the
  migration drops the old `mcp_servers` and `mcp_server_assignments` tables,
  whose tokens were stored in plaintext.
- The OAuth redirect URI for external MCP servers is now
  `{SERVER_URL}/auth/mcp/callback`; `/auth/callback/discord` is gone. Update any
  OAuth app you registered by hand with the old URI.

### Other changes for operators

- Two new nightly maintenance tasks prune tables that previously grew without
  bound: `celery_taskmeta` (Celery results older than
  `TASK_RESULT_RETENTION_DAYS`, default 7) and `pending_jobs` (complete or
  failed jobs older than `PENDING_JOB_RETENTION_DAYS`, default 30; pending and
  processing jobs are never deleted). Celery's own `celery.backend_cleanup` was
  scheduled onto a queue no worker consumes, so it never ran; it is now
  disabled (`result_expires=None`). The first run on a large table deletes a
  lot, in committed batches of 5000 rows. Afterwards, run
  `VACUUM (ANALYZE) celery_taskmeta, pending_jobs;` once to make the space
  reusable. It does not shrink the files; `VACUUM FULL` does, but takes an
  exclusive lock on each table while it runs, so only use it in a quiet window.
- Ingestion summaries now go through the Anthropic Message Batches API
  (`SUMMARIZER_USE_BATCH`, default `true`). Items are searchable at once, and
  their summary chunk arrives when the batch ends, usually within an hour.
  Set `SUMMARIZER_USE_BATCH=false` to summarize inline as before. The
  `SUMMARIZER_BATCH_*` settings tune batch size, retries and the
  submit/poll intervals.
- `SUMMARIZER_MODEL` and `RANKER_MODEL` now default to
  `anthropic/claude-haiku-5-5`. Values set explicitly in `.env` are kept.
- `DISCORD_PROCESS_MESSAGES`, `DISCORD_MODEL` and `DISCORD_MAX_TOOL_CALLS` are
  removed; nothing read them. Delete them from `.env` if set.
- New optional agent-run settings: `AGENT_MAX_RUN_COST_USD` (1.00) and
  `AGENT_MAX_DAILY_COST_USD` (10.00) are hard cost ceilings; `AGENT_MAX_TIMEOUT_SEC`
  (1800), `AGENT_MAX_WAIT_SEC` (300), `AGENT_LLM_TIMEOUT_SEC` (600, per model
  request), `AGENT_TOOL_RESULT_MAX_CHARS` (50000), `AGENT_CONTEXT_TRIM_AT_TOKENS`
  (100000), `AGENT_CONTEXT_TRIM_TO_TOKENS` (60000) and `AGENT_STEP_RETENTION_DAYS`
  (90).
- New `agents-worker` service (its own image, `docker/agents/Dockerfile`, with
  the api and worker dependencies) consumes the `<prefix>-agents` queue;
  `AGENT_WORKER_CONCURRENCY` (2) sets its concurrency; each run process takes
  about 270MB, so raise the service's memory limit with it.
  Without it, agent runs stay pending and are failed by the sweep after
  `AGENT_MAX_TIMEOUT_SEC` plus 10 minutes.
- New `agents` user scope gates the `agent_*` MCP tools and the Agents dashboard.
  Only `*` admins have it by default; grant it to anyone else who should use agents.
- Users can add their own Anthropic/OpenAI API keys for agent runs (Agents → API
  keys). Runs fall back to the server's `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` only
  with the new `llm_server_key` scope (`*` admins have it); the migration grants it
  to users and API keys that already hold `agents`. Server-key runs keep the
  `AGENT_MAX_*` ceilings; runs on a user's key skip them and are capped per run by
  the agent's `max_cost_usd` or the new `AGENT_DEFAULT_USER_KEY_RUN_COST_USD` (1.00),
  and per key per day by the key's or the agent's daily cap, or else the new
  `AGENT_DEFAULT_USER_KEY_DAILY_COST_USD` (20.00).
  Keys are encrypted with `SECRETS_ENCRYPTION_KEY`.
- Workers now refuse to start when the prefix they listen on (`QUEUE_PREFIX`,
  default `memory`) differs from `CELERY_QUEUE_PREFIX` (default `APP_NAME`,
  itself default `memory`), which tasks are routed with: such a worker would
  never receive any. Before updating, run
  `grep -E '^(APP_NAME|QUEUE_PREFIX|CELERY_QUEUE_PREFIX)=' .env`. If it prints
  nothing, or `APP_NAME=memory` alone, there is nothing to do. Otherwise
  `QUEUE_PREFIX` (or `memory` if unset) must equal `CELERY_QUEUE_PREFIX` (or
  `APP_NAME` if unset); if they differ, set both to the same value in `.env`.
  `tools/update.sh` runs this check on the new image before stopping anything,
  so a mismatch fails the update while the old stack keeps running.
- The `anthropic` SDK is upgraded to 2.1.0, so the images must be rebuilt.
  `tools/update.sh` does this.
- The GitHub MCP server and the Discord management MCP tools are removed. The
  Discord server keeps `send_message`, `channel_history` and `list_channels`.
  `github` in `DISABLED_MCP_SERVERS` no longer has any effect.
- Qdrant: a new `task` collection, and a `source_id` payload index on every
  collection. Both are created automatically at startup.
- `/health` now reports `version`, `db_revision` and `expected_revision`.
  Optionally, set `LATEST_RELEASE_URL` to get `update_available`.
- HyDE query expansion is now off by default (`ENABLE_HYDE_EXPANSION=false`):
  it added about 0.5 s per search without improving results. Set it to `true`
  to keep it.
- Search has new timeouts: `RERANK_TIMEOUT` (default 4 s per 100 candidates;
  a slower rerank falls back to fused order), `QUERY_EMBEDDING_TIMEOUT`
  (default 5 s) and `QDRANT_SEARCH_TIMEOUT` (default 5 s; a collection that
  misses it is left out of that search). Ingestion keeps `QDRANT_TIMEOUT`.
- Dependency security update: Python packages (pillow, cryptography,
  python-multipart, mcp/fastmcp, awscli, markdownify and others) and the
  frontend lockfile are bumped, and the Qdrant image moves to `v1.14.1`.
  The api image's frontend build stage moves from Node 18 to Node 22.
  The images must be rebuilt. `tools/update.sh` does this.
- Agents can now run unattended: on a schedule (`agent_schedule`, stored as a
  scheduled task of type `agent_run`, counting toward `MAX_SCHEDULED_TASKS_PER_USER`)
  and on ingest triggers (`agent_trigger_upsert`). Both start runs on the
  `agents-worker`, so it must be running. New optional settings:
  `AGENT_MAX_RUNS_PER_HOUR` (60, per definition; a definition's
  `max_runs_per_hour` can only lower it) and `AGENT_MAX_TRIGGERS_PER_USER`
  (20 enabled triggers). A trigger that hits a rate or cost cap disables itself
  and notifies its owner. New beat task `sweep-trigger-buffers` (every minute,
  `maintenance` queue) fires batched triggers. Once any trigger exists, every
  stored item queues a `MATCH_INGEST_TRIGGERS` task on `maintenance`. The daily
  `purge-agent-run-steps` task now also deletes trigger match claims older than
  30 days.
- `core_list_items` and `core_count_items` take `since`, `until` (when the
  content happened), `channel`, `author` and `repo`.
- Agents can call tools on external MCP servers that each user registers and
  authorizes (OAuth with dynamic or manual client registration, static headers,
  or none; streamable HTTP or SSE). New `agent_server_*` MCP tools and dashboard
  pages under `/ui/agents/servers`. stdio servers aren't supported.
- External MCP servers may be on private and LAN addresses (RFC 1918, ULA,
  CGNAT): **every user of the instance can reach your LAN through them**. Always
  blocked: loopback, link-local and cloud metadata, the default docker/podman
  address pools (172.16.0.0/12, 10.88.0.0/16, 10.89.0.0/16), this instance's own
  host on every port, the compose services, and, in the api and workers images
  (`MCP_BLOCK_CONTAINER_NETWORKS=1`), every network the container is attached
  to. Block more with `MCP_BLOCKED_NETWORKS` (comma-separated CIDRs). If the host
  publishes ports on 0.0.0.0, add its own LAN address, e.g.
  `MCP_BLOCKED_NETWORKS=192.168.1.10/32`; on a host attached to several networks,
  add each one it shouldn't reach.
- New optional settings: `MCP_MAX_SERVERS_PER_USER` (20), `MCP_MAX_TOOLS_PER_SERVER`
  (200), `MCP_MAX_TOOL_DESCRIPTION_CHARS` (4000), `MCP_MAX_TOOL_SCHEMA_BYTES` (16384),
  `MCP_MAX_RESPONSE_BYTES` (10 MiB), `MCP_CALL_TIMEOUT_SEC` (120),
  `MCP_CONNECT_TIMEOUT_SEC` (30), `MCP_TOKEN_REFRESH_SKEW_SEC` (300),
  `MCP_OAUTH_STATE_TTL_SEC` (600), `MCP_SERVER_CHANGES_PER_HOUR` (30),
  `MCP_BLOCKED_NETWORKS` (empty) and `MCP_BLOCK_CONTAINER_NETWORKS` (set to 1 by
  the images; if the route table can't be read, external servers can't be dialed).
- Agent tool names are now limited to 64 characters (the strictest provider
  limit), so an external tool whose `mcp_<server id>_<name>` is longer is listed
  as unavailable.
- The unused MCP connector plumbing in the LLM providers (`mcp_servers=`) is removed.
- **Access widens**: synced items now record the owner of the account they came
  from as their creator, and a creator can read their items whatever their
  project and sensitivity, in SQL, BM25 and vector search alike. Owners are the
  email, GitHub, Google Drive, calendar and transcript account's user, and the
  article feed's user. Slack and Discord messages get none. Email attachments
  and meeting action items take their parent's creator, so a transcript
  account's owner can also update their meetings' action-item tasks. Uploads
  through `/books/upload`, `/photos/upload` and `POST /meetings` record the
  uploader. Updating a report keeps its first creator. Moving an account to a
  project its owner can't access no longer hides its content from them; remove
  the account instead.
- Feed blog posts now also inherit their article feed's project and
  sensitivity, not just its owner.
- **Meetings become visible to their project**: meetings now inherit project
  and sensitivity from their transcript account (default `basic`), and their
  action-item tasks and email attachments from their parent. Synced meetings
  used to be visible only to superadmins and their attendees; now every
  contributor on the account's project sees them. See Manual steps.
- Items now resolve their inherited project, sensitivity and creator when they
  are stored, so they are searchable by their project and fire their owner's
  ingest triggers at once rather than after the next access-control sweep.
- Qdrant: chunk payloads gain `creator_id`. Existing points get it from the
  post-deploy command below; until then owners find their items outside their
  projects through BM25 only.
- Qdrant: `project_id`, `sensitivity` and `creator_id` payload indexes on every
  collection, since every non-admin vector search filters on them. Workers
  create them at startup; the first start after the update waits while they
  are built, which is slower on a large instance.
- **Security**: `/files/` and `/reports/` render only passive types inline (PDF,
  plain text, raster images, audio, video); HTML, SVG, XML, JS and unknown
  types download instead. Stored HTML (e.g. an email attachment) used to render
  on memory's origin with the viewer's session. HTML reports opened through
  `/reports/` are unchanged.
- New `claude_sessions` user scope gates every Claude container route (`/claude/*`:
  spawn, list, kill, attach, logs, stats, differ, terminal, environments,
  snapshots, including `tools/claude_snapshot.py` uploads) and the
  `claude_session_list`/`_list_dir`/`_pull_url`/`_push_url` MCP tools. The
  migration grants it to users who own a Claude config snapshot or environment
  (nobody else could spawn); `*` admins pass without it. Grant it to anyone else
  who should use Claude sessions. An API key with its own scope list now needs
  `claude_sessions` in that list, and REST routes gated on a scope now check an
  API key's own scopes rather than its owner's (inline admin checks still see
  the owner's `*`, a known gap).
- Scheduling a `claude_session` task needs `claude_sessions`. Each run re-checks
  that the user, and the API key that set it up, still hold it; if not, the run
  fails, the schedule is disabled and its owner is notified.
- A scheduled `claude_session` execution now stays `running` until its
  container exits (`completed`; `failed` if the container is `dead`, or was
  removed before its exit was seen), and the next run of that schedule waits
  for it. The api checks this every `CLAUDE_MAINTENANCE_INTERVAL_SEC` (300),
  then removes exited and dead containers through the orchestrator's
  `/cleanup`, so files can no longer be pulled from an exited container after
  that. Running containers are never touched. An execution still running after
  `CLAUDE_SESSION_TIMEOUT_HOURS` (24) is marked `failed`, by the api or the
  scheduler worker; its container keeps running. With
  `CLAUDE_MAINTENANCE_INTERVAL_SEC=0`, or an api without the orchestrator
  socket (logged at startup), nothing settles executions early, so each run of
  a repeating schedule waits up to that timeout.
- Check-queue callbacks now go through the same dial-time-checked client as
  external MCP servers. `CHECK_ALLOW_PRIVATE_CALLBACKS=true` still opens private
  and LAN ranges, but loopback is now always refused, so a localhost callback
  receiver must listen on a LAN address instead.

### Migrations

- `20261023_summary_requests`: creates the empty `summary_request` table. Fast.
- `20261024_agent_runs`: creates the empty `agent_definitions`, `agent_runs` and
  `agent_run_steps` tables, and adds two nullable columns to `api_keys`. Fast.
- `20261025_agent_triggers`: creates the empty `agent_triggers` and
  `agent_trigger_matches` tables. Fast.
- `20261026_external_mcp_servers`: drops `mcp_server_assignments` and
  `mcp_servers` (their rows are lost; see Manual steps) and creates the empty
  `external_mcp_servers` table. Fast.
- `20261027_llm_provider_keys`: creates the empty `llm_provider_keys` table, adds
  `key_source` (default `server`) and `provider_key_id` to `agent_runs`, and grants
  `llm_server_key` to users and API keys holding `agents`. Fast.
- `20261028_source_item_creators`: **slow on large instances**. Sets
  `source_item.creator_id` from the source owners above on every item that has
  none, in batches of 50,000 ids, committing after each batch. It can run while
  the app is up, and re-running it after a failure continues where it stopped.
  This is the access widening described above. Downgrading leaves the creators.
- `20261028_claude_sessions_scope`: grants `claude_sessions` to users who own a
  Claude config snapshot or environment. Fast.

### Post-deploy commands

Embed items that never got vectors (tasks never had a collection before). Each
command picks only items with no chunks, or FAILED ones, and reingesting an item
replaces its chunks, so re-running is safe:

```bash
for t in task calendar_event github_item slack_message person_tidbit; do
  docker compose exec -T worker python - maintenance reingest-empty-source-items --item-type "$t" < tools/run_celery_task.py
done
```

Give existing meetings,
their action items and email attachments their inherited project and
sensitivity, and write `creator_id` into every Qdrant payload. The daily sweep
at 05:30 resolves the SQL rows on its own, but only this command writes
`creator_id` for items whose rows didn't change. It can take hours on a large
instance and is safe to re-run:

```bash
docker compose exec -T worker python - maintenance reconcile-access-control --rewrite-payloads < tools/run_celery_task.py
```
