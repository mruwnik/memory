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
   - **Modules**: Claude containers, multiple users and teams are off unless
     `ENABLE_MODULES` in `.env` lists them (README § Modules). Before
     updating an instance that uses them, set
     `ENABLE_MODULES=claude_containers,multi_user,teams` (only the ones you
     use). Several human users with `multi_user` off stops the api at boot, by
     design, rather than hiding users. For Claude containers also add
     `COMPOSE_FILE=docker-compose.yaml:docker-compose.claude.yaml` to `.env`
     (if `.env` already has `COMPOSE_FILE`, as `install.sh --non-interactive`
     installs do, append `:docker-compose.claude.yaml` to it rather than
     replacing it, or the proxy overlay is lost), or pass `-f docker-compose.claude.yaml` after the base file if you use
     explicit `-f` flags: the socket mount, `DOCKER_GID` group and
     `memory-api-<stack>` network moved out of `docker-compose.yaml`. MCP clients
     that call `claude_session_list`, `_list_dir`, `_pull_url` or `_push_url`
     use `containers_list_sessions`, `containers_list_dir`,
     `containers_pull_url` and `containers_push_url` now.
   - **Post-deploy commands**: you run these after step 2.
2. Find your current version:
   ```bash
   curl -s http://localhost:8000/health
   ```
   `version` is the release tag (or git sha) the images were built from.
3. Make sure tracked files have no local edits (`git status`). `tools/update.sh`
   refuses to run otherwise. Keep local config in `.env`,
   `docker-compose.override.yml` and `secrets/`, which are gitignored.
   `.env` pins `COMPOSE_PROJECT_NAME` (the script adds it from the existing
   stack when missing, or stops if it can't tell which one that is); changing
   it moves compose to new, empty volumes. On podman, or where the script
   can't reach the docker socket, set `COMPOSE_PROJECT_NAME` in `.env` by hand.
4. Check free disk. The script refuses to start with less free space in
   `backups/` than the database size plus `MIN_FREE_GB` (default 5) GiB for
   new images:
   ```bash
   df -h . && docker system df
   ```

## 1. Update

Update to the latest release, or specify a release tag:

```bash
tools/update.sh --dry-run v0.3.0   # print the plan; changes nothing except fetching
tools/update.sh v0.3.0
```

`tools/update.sh` with no argument picks the newest release tag.
Any tag, branch or sha also works.

To follow only releases you trust, pass an `allowed_signers` file (one
`* namespaces="git" <ssh public key>` line per release signer) and a minimum age:

```bash
tools/update.sh --require-signed /etc/memory/allowed_signers --min-age-hours 48
```

`update.sh` then takes the newest `v*` tag with a valid SSH signature from one of
those keys that is at least 48 hours old. Age is the later of the tag date and
when this host first saw the tag, recorded in `$STATE_DIR/seen-tags` (default
`/var/lib/memory`), so a signer can't backdate a tag past the delay. Only
annotated, SSH-signed tags whose embedded name matches are accepted, and
automatic selection considers only `vMAJOR.MINOR.PATCH` tags. An explicit `REF`
must be a `v*` tag and pass the same checks, and must not be older than the
checkout unless you also pass `--allow-downgrade`. A tag never seen before counts as
first seen now; `--dry-run` does not record sightings. The selection lives in `tools/select_release.sh`,
which prints the chosen tag or exits nonzero.

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
4. runs the new worker image's settings check, so missing required settings or
   mismatched queue prefixes stop the update while the old stack still runs
5. stops `api`, `worker`, `agents-worker`, `slack-sync-worker` and `ingest-hub`
6. dumps Postgres to `backups/postgres-<timestamp>-<previous-version>.dump`
7. runs the one-shot `migrate` service (`alembic upgrade head`)
8. starts the stack and polls `/health` until it is healthy, reports the new
   version, and `db_revision == expected_revision`
9. checks that `api`, `worker`, `agents-worker`, `slack-sync-worker` and `ingest-hub` stay running
   without restarts for 15s
10. records the result in `backups/last-good` and keeps the newest
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
docker compose logs --since 10m api worker agents-worker slack-sync-worker ingest-hub | grep -iE "error|refusing"
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
docker compose stop api worker agents-worker slack-sync-worker ingest-hub && \
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

### Repair scripts

`tools/ops/` holds the repair actions, run as root on the box (or by an operator
through fixed SSM documents). Output is numbers and states only, except for
`logs.sh` (see *Trust limits* in [deploy/aws/README.md](deploy/aws/README.md)).

| Script | Does |
|---|---|
| `status.sh` | Health, containers, disk, memory, last update and backup results, pending reboot |
| `logs.sh [LINES]` | The last ERROR records and traceback shapes of api and worker, scrubbed of quoted values, URLs, emails and credentials |
| `restart.sh [service]` | Restart one service or all |
| `update-to.sh TAG` | `update.sh` with signature and age checks (`ALLOWED_SIGNERS`, `MIN_AGE_HOURS`), refusing a tag older than the checkout; prints step and result lines only |
| `rollback.sh --yes` | The commands above, from the update state or `last-good`; refuses unless the record is for the current checkout, and skips the tag it leaves |
| `restore-latest-backup.sh --yes` | Restore the newest complete S3 set (Qdrant, then Postgres, then files); first keeps a fresh `pre-restore-*.dump` and a `pre-restore-qdrant-*.tar.gz` |
| `rotate-keys.sh --yes [--only=NAME,...]` | Rotate the keys Memory generates (below); first keeps a fresh `pre-rotate-*.dump` |
| `disk-cleanup.sh` | Prune old images, build cache and dumps beyond `KEEP_BACKUPS` |
| `reboot.sh --yes` | `shutdown -r +1` |

All take `--dry-run`. `update.sh` and every script here but `status.sh` and
`logs.sh` refuse with "busy" while another one, or a nightly unit, holds
`/run/lock/memory-nightly.lock`. On-box state is in `$STATE_DIR` (default
`/var/lib/memory`): `update-failed` (written by `tools/update.sh`),
`restore-failed` and `restore.log` (owner-only details of a failed restore or
rollback), `rotate-keys` and `rotate-keys.log` (an unfinished or failed
rotation, and its owner-only details), `skip-tags`, and `last-backup` (the
nightly backup timer writes `ok` or `failed` as its first word). `status.sh`
reports the markers.

### Rotating keys

`tools/ops/rotate-keys.sh --yes` replaces every key Memory generated at install:

| Key | Kept in | Effect of rotating |
|---|---|---|
| `SECRETS_ENCRYPTION_KEY` | `secrets/secrets_encryption_key.txt` | Every stored secret is re-encrypted in one transaction. Transfer links, password reset links and OAuth sign-ins in flight stop working |
| `POSTGRES_PASSWORD` | `secrets/postgres_password.txt` | `ALTER ROLE` first, over the container's socket |
| `REDIS_PASSWORD` | `.env` | Redis is recreated with the new `--requirepass` |
| `BACKUP_ENCRYPTION_KEY` | `.env`, the file `BACKUP_ENCRYPTION_KEY_FILE` names, or `ops.env` on EC2 | New backups use the new key |
| `TRANSFER_TOKEN_SECRET` | `.env`, only if set there | In-flight transfer and reset links stop working. Unset, it is derived from `SECRETS_ENCRYPTION_KEY` and changes with it |

`--only=REDIS_PASSWORD,POSTGRES_PASSWORD` limits it to those keys. Provider keys
(Voyage, OpenAI, Anthropic) come from their providers: rotate them there and
re-run `tools/install.sh`, or edit `secrets/` and `.env`.

The script refuses unless `$BACKUP_DIR` has the database's size plus
`MIN_FREE_GB` (default 5) free. It takes a fresh `pre-rotate-*.dump` (keeping
the newest `KEEP_BACKUPS`) and stops the app services. It writes the new values
to the files above, mode 600, and with `MEMORY_SSM_PREFIX` set (EC2) to the SSM
parameters as well. It then changes the Postgres password, re-encrypts the
secrets, recreates every container and waits for `/health`. The app is down
for those few minutes. New values are generated on the box and never printed
or logged.

If any step fails, it puts the old values back everywhere and restarts. The
exception is a change the database has already taken whose undo fails too: the
new `SECRETS_ENCRYPTION_KEY` or `POSTGRES_PASSWORD` then stays in place, because
the database is on it. The same goes for a change that reported failure but
may have committed, when its undo fails: the old value goes back in the files,
and the database may still be on the new one. The output and `status.sh`
(`rotate_keys=failed kept_new=... unsettled=...`) name both kinds. Fix the
cause in `rotate-keys.log`, then run `rotate-keys.sh --yes --only=<those names>`. A run that was killed shows as
`rotate_keys=unfinished`; running it again is safe.

Backups need the `BACKUP_ENCRYPTION_KEY` they were made with, and their
databases hold secrets under the `SECRETS_ENCRYPTION_KEY` of the time. Each
rotation keeps the old and the new value of those two in
`secrets/previous/<name>/` and, on EC2, in SSM under `<prefix>/previous/<name>/`.
It does so before anything depends on the new value. Kept values are never
deleted automatically: the box can't tell which backups (in the bucket,
noncurrent versions included, in `backups/`, or copied elsewhere) still need
one, and they cost almost nothing. To drop them yourself, take the timestamp of
the last rotation before your oldest backup (entries are named
`<timestamp>-<pid>-<0|1>`) and, while `status.sh` says `rotate_keys=none`, run:

```bash
cutoff=20260101T000000Z   # the last rotation before your oldest backup
for f in secrets/previous/*/*; do [[ ${f##*/} < $cutoff ]] && rm -- "$f"; done
```

On EC2 run it in `/opt/memory` as the app user, and delete the SSM copies with
your own credentials (the instance can't):

```bash
aws ssm get-parameters-by-path --path /<name>/previous --recursive --query 'Parameters[].Name' --output text \
  | tr '\t' '\n' | awk -F/ -v cutoff=20260101T000000Z '$NF < cutoff' | xargs -n 10 aws ssm delete-parameters --names
```

Without EC2 the kept values exist only on this machine, and the new ones are
never printed: after rotating, copy `secrets/` (with `previous/`) and `.env` to
wherever you keep them for disaster recovery, or neither old nor new backups
can be decrypted once the machine is lost.
`restore-latest-backup.sh` tries the current key, then the kept ones. A restore
or rollback on an install that has rotated moves the restored secrets to the
current key, from whichever kept key each one is under. `rollback.sh` does that
with the release it rolls back to, unless that release predates rotation.

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
- Cut a release with `tools/release.sh [--push] vX.Y.Z`. It needs a clean
  checkout of master, a `## vX.Y.Z` section in CHANGELOG.md (rename
  `Unreleased`) and passing tests, then creates an SSH-signed annotated tag
  (`SIGNING_KEY` or git's `user.signingkey`). It pushes master and the tag only
  with `--push`. The default test command is the venv's
  `pytest --run-slow -n 3 --dist loadfile`; local master must equal
  `$REMOTE/master`.

## Update notifications (optional)

Set `LATEST_RELEASE_URL` in `.env` to a URL that returns the latest release tag,
either as plain text or as JSON with a `tag_name` field, e.g.
`https://api.github.com/repos/<owner>/<repo>/releases/latest`. `/health` then
reports `latest_release` and `update_available`. The result is cached for
`LATEST_RELEASE_CHECK_INTERVAL` seconds (default 6h). A failed check reports
`null` and never fails the health check. `update_available` is `null` when the
running version is not a plain release tag, e.g. a dev build at a sha.
