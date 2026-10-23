# Updating a deployment

A runbook for updating a single-owner deployment. Written to be followed step
by step by a person or an AI agent. Run every command from the repo checkout
the stack was started from.

## 0. Before you start

1. Read [CHANGELOG.md](CHANGELOG.md) for every release between your current
   version and the target. Note, per release:
   - **Required env vars**: add them to `.env` (or `secrets/`) *before*
     updating. The api and workers refuse to start without required settings
     and print the full list of what is missing.
   - **Manual steps**, e.g. a Postgres image change. Do these as the entry says.
   - **Slow migrations**: plan for downtime.
   - **Post-deploy commands**: you run these after step 2.
2. Find your current version:
   ```bash
   curl -s http://localhost:8000/health
   ```
   `version` is the release tag (or git sha) the images were built from.
3. Make sure tracked files have no local edits (`git status`). `tools/update.sh`
   refuses to run otherwise. Keep local config in `.env`,
   `docker-compose.override.yml` and `secrets/`, which are gitignored.
4. Check free disk. The script refuses to start with less free space in
   `backups/` than the database size plus `MIN_FREE_GB` (default 5) GiB for
   new images:
   ```bash
   df -h . && docker system df
   ```

## 1. Update

There are no release tags yet, so for now update to the tip of master:

```bash
tools/update.sh --dry-run origin/master   # print the plan; changes nothing except fetching
tools/update.sh origin/master
```

Once `v*` tags exist, `tools/update.sh` with no argument picks the newest one.
Any tag, branch or sha also works.

**AI agents:** the update takes minutes (image builds, migrations, health
polling), which is longer than most tool-call timeouts. A killed run leaves
the stack half-updated. Start it detached in its own session (`setsid` where
available; macOS has no `setsid`, so use plain `nohup`) and poll the log:

```bash
setsid nohup tools/update.sh origin/master > /dev/null 2>&1 &
tail -n 40 backups/update.log      # repeat until the last line is "UPDATE RESULT: ..."
```

The run is finished only when `backups/update.log` ends with
`UPDATE RESULT: ok (exit 0)` or `UPDATE RESULT: failed (exit N)`. Until then
it is still running; do not start another one.

The script:

1. refuses to run if disk is short or tracked files have local edits
2. fetches from `origin` and checks out the target **in detached-HEAD mode**
3. builds the images with `APP_VERSION` set to the target's `git describe`
4. stops `api`, `worker` and `ingest-hub`
5. dumps Postgres to `backups/postgres-<timestamp>-<previous-version>.dump`
6. runs the one-shot `migrate` service (`alembic upgrade head`)
7. starts the stack and polls `/health` until it is healthy, reports the new
   version, and `db_revision == expected_revision`
8. checks that `api`, `worker` and `ingest-hub` stay running without restarts
   for 15s
9. records the result in `backups/last-good` and keeps the newest
   `KEEP_BACKUPS` (default 5) dumps

Each run logs to `backups/update-<timestamp>.log`; `backups/update.log`
links to the latest.

Right after checkout, it writes `backups/update-in-progress`: the rollback
target, the target sha and, once taken, the backup path. If a run fails or is
killed (Ctrl-C/TERM), it prints the rollback commands. Re-running
`tools/update.sh` while the checkout is still at that target resumes: the
rollback target stays the version from before the first attempt, and the
pre-update backup is reused rather than replaced. If the checkout is anywhere
else, the script refuses with "stale update state": an old update was
abandoned. If `/health` is healthy at the expected revision, delete the file
and run again. The file is removed when an update succeeds; after a manual
rollback, remove it yourself (section 4).

Because the checkout is detached, `git pull` and `tools/deploy.sh` no longer
work in this checkout. Always update through `tools/update.sh`.

Settings via env: `COMPOSE` (default `docker compose`; e.g. `podman compose`),
`DOCKER` (default `docker`, or `podman` when `COMPOSE` starts with podman),
`REMOTE` (default `origin`), `BACKUP_DIR` (default `backups`),
`KEEP_BACKUPS` (default 5), `MIN_FREE_GB` (default 5), `HEALTH_TIMEOUT`
seconds (default 300).

Qdrant is not snapshotted. Vectors are derived from Postgres and files, so
they can be rebuilt with the reingest tasks. A full Qdrant snapshot is staged
in the container's `/qdrant/snapshots` tmpfs, which counts against Qdrant's
memory limit, so taking one on every update risks OOM-killing Qdrant on large
instances.

## 2. Verify

```bash
curl -s http://localhost:8000/health
```

Expect:

| Field               | Expected                                           |
| ------------------- | -------------------------------------------------- |
| `status`            | `healthy`                                          |
| `version`           | the tag (or `git describe`) you updated to         |
| `db_revision`       | equal to `expected_revision`                       |
| `update_available`  | `false`, or `null` if no release source configured |

`db_revision` is the alembic revision in the database; `expected_revision` is
the head of the migrations shipped with the running code. The same info is in
the `server` field of the MCP `meta_get_user` tool.

Then check the logs for startup errors:

```bash
docker compose ps
docker compose logs --since 10m api worker ingest-hub | grep -iE "error|refusing"
```

## 3. Post-update tasks

Run every post-deploy command listed in CHANGELOG.md for each release you
crossed, oldest release first. They are written to be safe to re-run.

`tools/run_celery_task.py` queues a task for the workers. It isn't in the
worker image, so pipe it into the worker container, which has the deps and
broker settings:

```bash
docker compose exec -T worker python - maintenance reingest-empty-source-items --item-type task < tools/run_celery_task.py
```

If Redis is published to the host (see README § Local development) and the
Python deps are installed, `python tools/run_celery_task.py …` works directly.

Follow progress with `docker compose logs -f worker`.

## 4. Roll back

Use this when the new version misbehaves after a successful update, or when
`tools/update.sh` failed after migrations started. If the script failed, it
already printed these commands with the right paths filled in. Otherwise the
previous version is in the backup filename.

**Confirm with the owner before running this.** It replaces the database, and
everything written since the backup is lost.

```bash
B=backups/postgres-<timestamp>-<previous>.dump
docker compose exec -T postgres pg_restore --list < "$B" > /dev/null && \
docker compose stop api worker ingest-hub && \
docker compose exec -T postgres dropdb -U kb --force kb && \
docker compose exec -T postgres createdb -U kb kb && \
docker compose exec -T postgres pg_restore -U kb -d kb --no-owner < "$B" && \
git checkout --detach <previous> && \
APP_VERSION=<previous> docker compose up -d --build
curl -s http://localhost:8000/health    # version == <previous>, db_revision == expected_revision
```

The first line checks that the dump is readable. If it fails, stop: do not
drop the database.

Once `/health` shows the previous version at the expected revision, remove
the update state so the next `tools/update.sh` starts fresh:

```bash
rm -f backups/update-in-progress
```

If the chain stopped part-way, leave the file in place and fix the failing
step first: it records the rollback target and backup.

Restoring the dump is the supported downgrade path, not `alembic downgrade`.

After a rollback, items ingested after the backup may still have vectors in
Qdrant even though their Postgres rows are gone. The periodic
`clean-all-collections` maintenance task deletes those orphaned vectors. To
run it now, queue it as in section 3 with `maintenance clean-all-collections`.

## 5. When a migration fails

`update.sh` stops right after the failed `migrate` run, and the app services
stay stopped.

1. Read the error: `docker compose logs migrate`, or the end of
   `backups/update.log`.
2. Check what the database thinks:
   ```bash
   docker compose exec -T postgres psql -U kb -d kb -c "SELECT * FROM alembic_version"
   ```
3. `db/migrations/env.py` runs the whole upgrade in one transaction. A failure
   therefore rolls the database back to the revision it had before the
   update, even when several migrations were pending. The exception is a
   migration that explicitly commits, e.g. an `autocommit_block`. Then either:
   - **Fix the cause and re-run** `tools/update.sh <same ref>`. Fixable causes
     include disk full, a missing env var, Postgres not healthy, or a data
     problem the error names.
   - **Roll back** with the commands in section 4 (after confirming with the
     owner). If `alembic_version` still shows the pre-update revision, the
     restore is not needed. Run
     `git checkout --detach <previous> && APP_VERSION=<previous> docker compose up -d --build`,
     and once `/health` shows `<previous>`, `rm -f backups/update-in-progress`.
4. Never edit `alembic_version` by hand to "skip" a migration. The code
   expects the schema that migration creates.

## Rules for maintainers

- **Never squash or rewrite the alembic chain** in `db/migrations/versions`.
  Instances update on their own schedule and may jump many releases at once.
  `alembic upgrade head` has to be able to walk from any released revision to
  head.
- Every release gets a CHANGELOG.md entry. List new required env vars, manual
  steps (including any Postgres image change), slow migrations and post-deploy
  commands. Post-deploy commands must be safe to re-run.
- Tag releases as `vMAJOR.MINOR.PATCH`. `update.sh` picks the newest `v*` tag,
  and `update_available` compares tags numerically.

## Update notifications (optional)

Set `LATEST_RELEASE_URL` in `.env` to a URL that returns the latest release tag,
either as plain text or as JSON with a `tag_name` field, e.g.
`https://api.github.com/repos/<owner>/<repo>/releases/latest`. `/health` then
reports `latest_release` and `update_available`. The result is cached for
`LATEST_RELEASE_CHECK_INTERVAL` seconds (default 6h). A failed check reports
`null` and never fails the health check. `update_available` is `null` when the
running version is not a plain release tag, e.g. a dev build at a sha.
