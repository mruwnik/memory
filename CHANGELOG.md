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
- `SUMMARIZER_MODEL`, `RANKER_MODEL` and `DISCORD_MODEL` now default to
  `anthropic/claude-haiku-5-5`. Values set explicitly in `.env` are kept.
- The `anthropic` SDK is upgraded to 2.1.0, so the images must be rebuilt.
  `tools/update.sh` does this.
- The GitHub MCP server and the Discord management MCP tools are removed. The
  Discord server keeps `send_message`, `channel_history` and `list_channels`.
  `github` in `DISABLED_MCP_SERVERS` no longer has any effect.
- Qdrant: a new `task` collection, and a `source_id` payload index on every
  collection. Both are created automatically at startup.
- `/health` now reports `version`, `db_revision` and `expected_revision`.
  Optionally, set `LATEST_RELEASE_URL` to get `update_available`.

### Migrations

- `20261023_summary_requests`: creates the empty `summary_request` table. Fast.

### Post-deploy commands

Embed items that never got vectors (tasks never had a collection before). Each
command picks only items with no chunks, or FAILED ones, and reingesting an item
replaces its chunks, so re-running is safe:

```bash
for t in task calendar_event github_item slack_message person_tidbit; do
  docker compose exec -T worker python - maintenance reingest-empty-source-items --item-type "$t" < tools/run_celery_task.py
done
```
