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

- The api and workers now check settings at startup and refuse to start,
  listing everything that's missing. Both require `VOYAGE_API_KEY`. Workers
  also require the key for `SUMMARIZER_MODEL`'s provider: `ANTHROPIC_API_KEY`
  by default, or `OPENAI_API_KEY` for `openai/` models. A working deployment
  already has these.

### Manual steps

None.

### Other changes for operators

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
- New `agents-worker` service (built from the API image) consumes the
  `<prefix>-agents` queue; `AGENT_WORKER_CONCURRENCY` (2) sets its concurrency; each run
  process takes about 270MB, so raise the service's memory limit with it.
  Without it, agent runs stay pending and are failed by the sweep after
  `AGENT_MAX_TIMEOUT_SEC` plus 10 minutes.
- New `agents` user scope gates the `agent_*` MCP tools and the Agents dashboard.
  Only `*` admins have it by default; grant it to anyone else who should use agents.
- Workers now refuse to start when the prefix they listen on (`QUEUE_PREFIX`,
  default `memory`) differs from `CELERY_QUEUE_PREFIX` (default `APP_NAME`), which
  tasks are routed with: such a worker would never receive any. If you set
  `APP_NAME` or either prefix, check `.env` before updating.
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

### Migrations

- `20261023_summary_requests`: creates the empty `summary_request` table. Fast.
- `20261024_agent_runs`: creates the empty `agent_definitions`, `agent_runs` and
  `agent_run_steps` tables, and adds two nullable columns to `api_keys`. Fast.

### Post-deploy commands

Embed items that never got vectors (tasks never had a collection before). Each
command picks only items with no chunks, or FAILED ones, and reingesting an item
replaces its chunks, so re-running is safe:

```bash
for t in task calendar_event github_item slack_message person_tidbit; do
  docker compose exec -T worker python - maintenance reingest-empty-source-items --item-type "$t" < tools/run_celery_task.py
done
```
