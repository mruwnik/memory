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

- **Modules: Claude containers, multiple users and teams are now off by
  default. Read this before updating.** Three optional modules are turned on
  on the Server settings page, or by the operator with `ENABLE_MODULES` and
  `DISABLE_MODULES` (comma-separated names, in `.env`; a change needs a
  restart), which lock the modules they name on the page:

  | Module | Turns on |
  |---|---|
  | `claude_containers` | Claude Code containers: the `/claude` routes, their MCP tools, the `claude_session` scheduled-task type, the `claude_sessions` scope, the Claude sessions and configurations pages. Needs the compose overlay below |
  | `multi_user` | More than one human user. Off, there is one human, `tools/add_user.py` creates them with `*`, and `POST /users` refuses a second human (bot users and API keys are unaffected). The api logs a warning at startup if the only human lacks `*` |
  | `teams` | Teams, their members, and projects: the `teams` and `projects` MCP servers, the four `teams`/`projects` scopes, and the team and project pickers |

  To keep what you had, add to `.env` (drop the names you don't use, and the
  `COMPOSE_FILE` line if you don't run Claude containers):

  ```
  ENABLE_MODULES=claude_containers,multi_user,teams
  COMPOSE_FILE=docker-compose.yaml:docker-compose.claude.yaml
  ```

  If `.env` already has a `COMPOSE_FILE` line (`tools/install.sh
  --non-interactive` writes
  `COMPOSE_FILE=docker-compose.yaml:deploy/aws/compose.proxy.yaml`), append
  `:docker-compose.claude.yaml` to it instead of adding a second one, e.g.
  `COMPOSE_FILE=docker-compose.yaml:deploy/aws/compose.proxy.yaml:docker-compose.claude.yaml`.
  Replacing it drops the proxy overlay silently: the api stops trusting the
  proxy's forwarded headers and rate limits key on the proxy's address.
  A `docker-compose.override.yml` is no longer read once `COMPOSE_FILE` is
  set, so list it too if you have one.

  Then `docker compose up -d`. What stops a process from starting:
  - The api refuses to start while `multi_user` is off and several human users
    exist ("N human users exist; set ENABLE_MODULES=multi_user or remove users").
  - A misspelled module name, or one listed in both `ENABLE_MODULES` and
    `DISABLE_MODULES`, stops every process, with the offending names in the message.
  - With `claude_containers` on, the api refuses to start without
    `ORCHESTRATOR_SOCKET`, which the overlay sets.

  **On the settings page.** A module the env doesn't name is a toggle under
  Modules on `/ui/server-settings` (and in the setup wizard), applied when the
  api restarts from the page's banner; the separate `/ui/modules` page and
  `GET /modules` are gone. The Instance settings page is now Server settings,
  at `/ui/server-settings`; the old `/ui/instance-settings` URL doesn't
  redirect. The page refuses `claude_containers` without `ORCHESTRATOR_SOCKET`
  and `multi_user` off while several humans exist. If the
  env changes under a saved value, the api logs `Ignoring the stored value of
  module_<name>` at ERROR, boots with the module the other way and the page
  flags the row, rather than refusing to start. The api records the modules it
  booted with (a `running_modules` row in `instance_settings`), and workers
  follow that record, not the saved values: due `claude_session` tasks wait
  until the api runs the module, and the agents worker mounts or hides a
  module's MCP servers at the start of each agent task. The api now waits for
  a healthy Postgres (`depends_on: condition: service_healthy`).
  The MCP server checklist no longer lists `teams`, `projects` or `containers`:
  they go with their module.

  **Compose overlay.** The Claude container wiring (the orchestrator socket
  mount, `group_add` with `DOCKER_GID`, the external `memory-api-<MEMORY_STACK>`
  network and its alias, and `ORCHESTRATOR_SOCKET`) left `docker-compose.yaml`
  for `docker-compose.claude.yaml`, enabled by the `COMPOSE_FILE` line above.
  Stacks that start compose with explicit `-f` flags add
  `-f docker-compose.claude.yaml` after the base file. Without the overlay, a
  fresh `docker compose up` no longer needs the `memory-api-<stack>` network;
  `tools/install.sh` and `deploy/aws/bootstrap.sh` create it only when
  `COMPOSE_FILE` lists the overlay.

  **What off means.** The routes return 404 and leave OpenAPI, the MCP servers
  are not mounted (their tools leave `tools/list`, agent catalogs and
  in-process agent runs, and an agent definition that allowlists one is
  reported as unavailable), and the scopes leave the pickers and
  `/users/scopes`. Nothing is migrated or deleted: rows, scope grants and
  `project_id`s stay, and turning a module back on restores the surfaces.
  Access control is unchanged by modules, and core features (search, ingest,
  people, notes, reports, journal, tasks, polls, agents, keys, transcript
  search) never depend on them. The enabled set is returned to logged-in users
  with the user payload (`/auth/me`), not in `/health`. Only the module flags
  unmount a server; a server disabled on the settings page is hidden and
  refused live, as before.

  **MCP tool renames.** The `claude` MCP server is split. Transcript search
  (`claude_session_search`, `claude_session_fetch`) stays core. The file
  transfer tools moved to the `claude_containers` module under new names:
  `claude_session_list` is now `containers_list_sessions`,
  `claude_session_list_dir` is `containers_list_dir`, `claude_session_pull_url`
  is `containers_pull_url` and `claude_session_push_url` is `containers_push_url`.
  Update MCP clients, skills and agent allowlists that name the old ones.

  **Leftovers when `claude_containers` is off.** Due `claude_session`
  scheduled tasks are skipped (no execution row, the next run of a cron task
  advances, a one-off task keeps its time and runs once the module is back; one
  log line) and the scheduler MCP refuses to create new ones. Executions already
  pending or running when the module goes off are not cancelled: pending ones
  fail on their next attempt, running ones wait for the stale-execution timeout
  (2 hours). Running containers are not killed, because the orchestrator is
  external. In the agents worker (which has no `ORCHESTRATOR_SOCKET`)
  `containers_list_sessions` and `containers_list_dir` fail as before.

- **Required action if you relied on the old backup defaults.** S3 backups no
  longer default to a bucket: they run only when `S3_BACKUP_BUCKET` is set, and
  `S3_BACKUP_ENABLED` is gone. An instance that had `BACKUP_ENCRYPTION_KEY` set
  and nothing else was backing up to bucket `equistamp-memory-backup` under
  prefix `Daniel`; after this update it stops backing up silently. To keep
  backing up, set `S3_BACKUP_BUCKET` and `S3_BACKUP_PREFIX` in `.env` (the old
  values, if you want your existing backups to stay in the same place) and keep
  `BACKUP_ENCRYPTION_KEY`; setting a bucket without the key makes the
  workers refuse to start. If you had
  `S3_BACKUP_ENABLED=false` to keep backups off, remove it and unset
  `S3_BACKUP_BUCKET`: the workers refuse to start while `S3_BACKUP_ENABLED` is set,
  and a bucket alone turns backups on. If you had a bucket set without
  `BACKUP_ENCRYPTION_KEY` (backups were off), either add the key or unset the
  bucket, or the workers refuse to start. To check: `grep S3_BACKUP .env` should show
  `S3_BACKUP_BUCKET`; while it is unset the `backup-all` task logs
  "S3 backup is disabled". `S3_BACKUP_PREFIX` now defaults to
  `memory` (was `Daniel`) and `tools/backup_databases.sh` and
  `tools/restore_*` also have no default bucket. New installs choose explicitly:
  `tools/install.sh` asks, and `--non-interactive` needs `S3_BACKUP_BUCKET` or
  `S3_BACKUP=off`.

- `SECRETS_ENCRYPTION_KEY` must be set to register external MCP servers (their
  tokens, client secrets and headers are encrypted with it). Registration fails
  with a clear error without it; nothing else changes.
- The api and workers now check settings at startup and refuse to start,
  listing everything that's missing. `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`
  are no longer read.

### Manual steps

- Add your own Anthropic or OpenAI key under LLM API keys before or right after
  updating, or ingest stops summarising and search drops HyDE and query
  analysis. Claude sessions of users without a default key stop being
  summarised. System agents run on Claude Haiku with an Anthropic key, else on
  `gpt-6-luna` with an OpenAI key. Summary batching (`SUMMARIZER_USE_BATCH`)
  uses the key's provider: Anthropic's Message Batches API or OpenAI's Batch
  API, both at half price. OpenAI batches upload a JSONL file to the key's
  account, deleted once its results are collected (files of batches never
  collected expire after 30 days). A key whose batch OpenAI refuses for its
  queue limit gets smaller batches until they succeed. OpenAI batches can take
  hours, so each OpenAI key has one batch in flight at a time, of up to 50,000
  requests (`SUMMARIZER_BATCH_MAX_REQUESTS` applies to Anthropic batches), and
  a batch only takes as many requests as the key's daily pool could pay for.
- **Before updating**, drain the summary queue: the migration drops every
  queued summary request, since they were queued on the server's key. Set
  `SUMMARIZER_USE_BATCH=false` on the running version and restart its workers,
  so ingest stops queueing new requests, then let in-flight batches finish
  (`collect_summary_batches` runs every `SUMMARIZER_BATCH_POLL_INTERVAL`). The
  queue is drained once
  `SELECT count(*) FROM summary_request WHERE status != 'failed'` is 0. Restore
  `SUMMARIZER_USE_BATCH` when updating. If you can't wait, list the items that
  will lose their summary with
  `SELECT DISTINCT s.id, s.type FROM summary_request r JOIN source_item s ON s.id = r.source_id WHERE r.status != 'failed'`
  and, once updated, reingest each with
  `python tools/run_celery_task.py maintenance reingest-item --item-id <id> --item-type <type>`.
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
- **Before updating, check that no user is linked to more than one Person.**
  The `20261106_unique_person_user` migration refuses to run if one is, and
  `tools/update.sh` has stopped the api by then. On the running old version:

  ```
  docker compose exec postgres psql -U kb kb -c "SELECT user_id, array_agg(id) AS person_ids, array_agg(identifier) FROM people WHERE user_id IS NOT NULL GROUP BY user_id HAVING count(*) > 1"
  ```

  For each row, either merge those people with the `people_merge` MCP tool
  (still on the old version), or keep one link and clear the others:

  ```
  docker compose exec postgres psql -U kb kb -c "UPDATE people SET user_id = NULL WHERE id IN (<person ids to unlink>)"
  ```

  If an update already failed on this migration, run the rollback commands
  `tools/update.sh` printed, clear the duplicates as above, then run
  `tools/update.sh` again.

### Other changes for operators

- Notes are a Sources tab. Admins set up the vault's git sync there (remote,
  commit author, and an access token for an https remote) and can sync it now;
  the `notes setup-git-notes` command of `tools/run_celery_task.py` is gone. A
  vault that is already a repository keeps its history and remote. New optional
  settings: `NOTES_GIT_TOKEN` (or `NOTES_GIT_TOKEN_FILE`) locks the token, and
  `NOTES_SYNC_INTERVAL` now locks the interval, which is otherwise set on the
  Server settings page and applies without a restart. It must be 60 to 86400
  seconds, or the api and workers refuse to start. Beat's `sync-notes-changes`
  entry is replaced by a minutely `sync-notes-when-due` check. `SSH_PUBLIC_KEY_FILE`
  (default `/run/secrets/ssh_public_key`) is the key the tab shows for an ssh
  remote.
- First-run setup wizard. While no human account exists, the web UI sends
  visitors to `/ui/setup`, where the first account is created as an admin.
  Creating it needs a setup token that the api writes to its log at startup
  (`docker compose logs api | grep "setup token"`); a new token is made on each
  restart until the account exists. The wizard then walks the new admin through
  LLM keys, the Voyage key, modules, a first source, connecting an AI client,
  and their timezone. It runs
  once: an instance that already has an account never shows it.
  `POST /setup/account` returns 404 once any human account exists.
- `tools/install.sh` no longer asks about modules or writes `ENABLE_MODULES`:
  modules are switched in the setup wizard and on the Server settings page. It
  asks only whether to wire up Claude Code containers (`CLAUDE_CONTAINERS=on`
  with `--non-interactive`; default off): then it detects `DOCKER_GID`, appends
  `docker-compose.claude.yaml` to `COMPOSE_FILE` and creates the
  `memory-api-<stack>` network, and the admin turns the module on in the UI. A
  re-run keeps an existing `ENABLE_MODULES` line and an already wired overlay.
- `tools/install.sh --non-interactive` no longer requires `ADMIN_EMAIL` and
  `ADMIN_PASSWORD` (set both or neither). Without them it creates no user and
  prints the setup token for `/ui/setup`. It now runs `docker compose run --rm
  migrate` before starting the api, so creating the admin no longer meets an
  empty schema on a first install. The interactive installer's next steps say
  the same.

- One-time API keys are removed: the `meta_create_one_time_key` MCP tool, the
  `one_time` key type, the `ONE_TIME_KEY_RATE_LIMIT` and `ONE_TIME_KEY_TTL_SECONDS`
  settings (delete them from `.env`) and the `cleanup-used-one-time-keys` beat
  task. Existing one-time keys are deleted by the migration and stop working.
  Anything that minted them, such as a script calling `/claude/spawn`,
  needs a normal API key instead.
- Each user has a timezone (default `UTC`), set in User settings or through
  `PATCH /users/{id}`. Cron schedules, both scheduled tasks and agent schedules,
  run on their owner's local time, following DST; changing a timezone moves
  the user's pending cron runs.
- `VOYAGE_API_KEY` is no longer required. Without it the api and workers start,
  search is keyword-only (no reranking or HyDE), and new items are stored with
  `embed_status = FAILED` until an admin sets the key; saving it queues
  `reingest-all-empty-source-items` to embed them. `secrets/voyage_key.txt` must
  still exist (compose needs it), and may be empty: `tools/install.sh` now always
  writes it, makes the Voyage prompt skippable (also with `--non-interactive`),
  and no longer copies the key into `.env`. EC2: `voyage_api_key` is optional in
  the Terraform; leave it out to enter the key in the web UI.
- New admin settings API (`GET`/`PATCH /settings`, `POST /settings/restart`) for
  the Voyage key, the disabled MCP servers and the search `ENABLE_*` switches. A
  value set in the env still wins and is read-only there; to hand a setting to
  the UI, remove it from `.env` (or empty its secret file) and restart. Secrets
  set there are encrypted with `SECRETS_ENCRYPTION_KEY`, and `rotate-keys.sh`
  re-encrypts them. A server disabled on the page hides its tools at once, in
  the api and in agent runs.
  The compose api service now sets `API_SELF_RESTART=1`, which lets an admin
  restart it from the page for restart-bound settings (it exits, and
  `restart: unless-stopped` brings it back). The compose
  file no longer sets `ENABLE_BM25_SEARCH` (it was the default), so it can be
  changed in the UI. `/health` reports a `boot_id`.
- `DISABLED_MCP_SERVERS` is removed and ignored. Servers it listed are mounted
  again; switch them off on the Server settings page (this hides their tools and
  refuses calls, but does not unmount them). To unmount a server's code, turn
  off its module.
- The Check queue is removed: the `check_*` MCP tools, the `/check` HTTP
  endpoints, the `check` scope and the Check page. Fact-checking runs on the
  prebuilt fact-check agent instead: `agent_run(input=..., agent="fact-check")`
  then `agent_wait`, paid with the caller's own provider key (it needs the
  `agents` scope). The external `checker` Firefox extension and laptop worker
  stop working. Tokens and users still carrying `check` keep working and the
  value is ignored; asking for it in a new grant or key is rejected as an
  invalid scope. The `CHECK_*` variables can be dropped from `.env`. Leftover
  `check:*` Redis keys expire on their own within 14 days. No migration.
- The Discord collector no longer serves its channel, category and permission
  endpoints (the collector API keeps only health, DMs, channel messages,
  typing, reactions and metadata refresh). The `discord-admin` and
  `discord-admin:write` scopes are removed; tokens and users still carrying
  them keep working, the values are ignored and no longer shown in the users API.
- Teams no longer link to Discord roles or GitHub teams. `teams_upsert` drops
  `guild`, `discord_role`, `auto_sync_discord`, `github_org`,
  `github_team_slug`, `auto_sync_github` and `force_empty`; adding or removing a
  team member, and creating or updating a GitHub-backed project, no longer
  touches Discord roles, GitHub team membership or GitHub repo access. Existing
  Discord roles and GitHub teams are left as they are. MCP clients that read
  them lose the response keys `discord_sync` and `github_sync` (`teams_upsert`),
  `sync` (`teams_team_add_member`/`teams_team_remove_member`), and
  `github_team_sync` and `teams_from_github` (`projects_upsert`). The Discord
  collector's role endpoints (`/guilds/{id}/roles`, `/roles/add_member`,
  `/roles/remove_member`) are gone.
- **Narrower visibility for new repo projects.** Creating a project for a
  GitHub repo no longer adds the local teams whose GitHub team had access to
  that repo. The project gets only the teams passed in, so it can be visible
  to fewer people than before; assign teams explicitly.
- `people_upsert`: `replace_aliases` is gone. A list of aliases is always
  added, and an alias is removed by mapping it to null
  (`aliases={"@old": null}`). In `contact_info`, a null removes a key, lists
  gain entries instead of being replaced (remove one entry with
  `{"discord": {"old_handle": null}}`), and a shape change that would drop data
  (e.g. a string replacing a dict) is refused. A new scalar still replaces the
  old one: `{"email": "new@x"}` overwrites the old email.
- `people_merge` keeps both people's contact info: values that differ become
  a list (primary's first) instead of the primary's value winning. Email,
  GitHub and Slack lookups match any value in such a list; notifications reach
  every merged Slack/GitHub account, and single-value uses (email target,
  Discord suggestion) take the primary's. The people edit form only sends
  contact fields that changed.
- `tools/ops/rotate-keys.sh --yes [--only=NAME,...]` rotates the keys Memory
  generates: `SECRETS_ENCRYPTION_KEY` (re-encrypting the stored secrets),
  `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `BACKUP_ENCRYPTION_KEY` and an
  explicitly set `TRANSFER_TOKEN_SECRET`. It keeps a fresh dump, stops the app,
  writes the new values (and on EC2 the SSM parameters), recreates the
  containers and puts the old values back if anything fails. It prints names and
  counts only. The backup and secrets keys backups need stay in
  `secrets/previous/` (and SSM `<prefix>/previous/`) indefinitely; UPDATING.md
  has the command to delete the ones older than your oldest backup. On Docker
  installs, copy `secrets/` and `.env` off the machine after rotating.
  `restore-latest-backup.sh` and `rollback.sh` use them: a backup from before a
  rotation restores, and its stored secrets move to the current key. On EC2 it
  is the operator document `rotate-keys` in the `recover` role. See
  [UPDATING.md](UPDATING.md#rotating-keys).
- `JWT_SECRET` is gone: nothing read it. `install.sh` no longer writes
  `secrets/jwt_secret.txt`, which you may delete, and on EC2 the next apply
  deletes the `jwt_secret` parameter.
- **EC2:** the re-apply adds the `rotate-keys` document, lets the instance write
  its generated secrets and create `previous/*` (`ssm:PutParameter` there,
  `kms:Encrypt`). Terraform no longer resets those SSM values
  on apply (`ignore_changes`); `voyage_api_key` moves to its own resource,
  without a replacement. If SSM's `backup_encryption_key` differs from the one
  the box backs up with, the box keeps its own, and the nightly update stops
  after rendering the rest of the config.
- The Claude "New session" form saves GitHub tokens on the server (encrypted,
  per user, only the last 4 characters are ever shown) instead of in the
  browser's localStorage, and clears the old localStorage copy on load. Users
  re-enter their tokens once. Every session a user spawns gets their saved clone
  token unless the spawn request names its own. The saved write token goes only to
  sessions spawned from the form, or whose request or schedule sets
  `use_saved_write_token`. Any API key with the `claude_sessions` scope can
  therefore obtain the saved tokens by spawning a session and reading its env.
- Users can reset a forgotten password by emailed link ("Forgot password?" on the
  login page). It is off until you set `PASSWORD_RESET_FROM_EMAIL` to the address
  of an existing send-enabled email account (Sources → Email) owned by an admin,
  which sends the links. Whoever owns that account can read every reset link. It
  also needs `SECRETS_ENCRYPTION_KEY` (or `TRANSFER_TOKEN_SECRET`). A reset ends
  all the user's sessions; API keys are kept. Email sync never ingests reset mail.
- `tools/install.sh --non-interactive` installs from environment variables
  without prompting and creates the first user (see the README). Both modes now
  also generate `secrets/secrets_encryption_key.txt` when absent (compose mounts
  it), and tighten `.env` and `secrets/` to owner-only permissions. `tools/add_user.py`
  reads the password from `ADD_USER_PASSWORD` and accepts `--if-missing`.
- `tools/update.sh` accepts `--require-signed <allowed_signers>` and
  `--min-age-hours N` to install only signed, sufficiently old release tags
  (age counts from when the host first saw the tag; state in `$STATE_DIR`).
  Without them it behaves as before. New `tools/release.sh` cuts signed tags.
- New repair scripts in `tools/ops/` (status, logs, restart, update-to, rollback,
  restore-latest-backup, disk-cleanup, reboot); see UPDATING.md. `tools/update.sh`
  now sources `tools/ops/lib.sh`, writes `$STATE_DIR/update-failed` when an update
  fails (default `STATE_DIR=/var/lib/memory`), and `last-good` records the
  previous version. `rollback.sh` adds the tag it leaves to `$STATE_DIR/skip-tags`,
  which the automatic release selection passes over.
  Automatic selection never returns a tag at or behind the checkout (exit 3,
  "up to date"), and `update.sh` does nothing when the target is already checked out.
- API log records now carry a level (`[time: LEVEL/logger] message`, as in the
  workers), and app warnings and errors in the API go through it instead of
  printing bare.
- `tools/restore_databases.sh` is removed; use `tools/ops/restore-latest-backup.sh`
  (set `BACKUP_SOURCE` to a local directory laid out like the bucket). Its Qdrant
  restore never worked: Qdrant has no API to restore full snapshots. The new one
  tars the Qdrant volume to `backups/pre-restore-qdrant-*.tar.gz`, then starts
  Qdrant on the snapshot (`--storage-snapshot`), and puts the tarball back if that fails.
- `tools/backup_databases.sh` with `MAX_BACKUPS=0` keeps every backup, for a
  bucket whose lifecycle rule expires them.
- Automatic release selection counts an installed, signed tag as up to date
  whatever its age, so the night after an install that skipped the delay
  doesn't fail.
- `tools/ops/restore-latest-backup.sh` hands `BACKUP_ENCRYPTION_KEY` to the
  worker container that decrypts file backups, so the key no longer has to be
  in `.env`.
- `tools/install.sh` creates the external `memory-api-<MEMORY_STACK>` Docker
  network if it is missing; before, `docker compose up` failed on a new host
  until it was created by hand. The first user it creates now has full access.
  `tools/add_user.py --admin` grants the `*` scope; without it users get `read`.
- `RATE_LIMIT_TRUSTED_PROXIES` accepts CIDRs, like `FORWARDED_ALLOW_IPS`
  already did (the rate limiter matched exact IPs only).
- `tools/install.sh --non-interactive` now adds `deploy/aws/compose.proxy.yaml`
  through `COMPOSE_FILE` in `.env`. It pins the `kbnet` subnet and gateway
  (`KBNET_SUBNET`, `KBNET_GATEWAY`; default `10.213.47.0/24`, `.1`) and makes
  kbnet the api's gateway network, and both trust settings become loopback
  plus that gateway. Docker hands a host proxy's loopback connections to the
  api from that gateway, so before this the api saw every client as the
  gateway: one rate-limit bucket for everyone, and `X-Forwarded-Proto`
  ignored. The override needs Docker Engine 28 or later, and each stack on a
  host its own `KBNET_SUBNET`. The base compose file and other installs are
  unchanged.
- New EC2 first-boot script `deploy/aws/bootstrap.sh` and nightly host jobs in
  `deploy/aws/host/` (update, backup, reboot window); see deploy/aws/README.md.
- Transcript accounts accept pushed transcripts at
  `POST /transcripts/ingest/{account_id}`, authenticated by a per-account
  secret generated in the UI (Sources → Transcripts → Push). A new `generic`
  provider is push-only and needs no API key; Fireflies accounts can point
  Fireflies webhooks at the same URL. The path bypasses the auth middleware,
  so a reverse proxy that allow-lists public paths must allow
  `/transcripts/ingest/`. The old free-text webhook secret field is gone and
  the migration clears any secret stored through it, so owners must generate
  a new one in the UI before pushing. Polling is unchanged.
- MCP tools gated on "has any items" (organizer task and calendar tools,
  deadlines, books, observations) are still hidden from tool listings while
  their table is empty, but calling one no longer fails with "Access denied";
  it returns empty results. The Calendar and Tasks pages work again on an
  instance with no events or tasks yet.
- Slack polling no longer waits for a Slack app's push-event setup. Any
  workspace with collection on and an unexpired OAuth token on an active app is
  polled, so workspaces whose app setup was never finished (stuck in `draft` or
  `signing_verified`) start polling again after this deploys, and the first
  sync backfills everything since they stopped. A collecting workspace that
  still can't be polled logs a warning naming it and the reason, and "Sync Now"
  returns that reason instead of silently doing nothing. The Sources page shows
  "Polling only" for such apps, with a button that resumes the setup wizard.
- Two new nightly maintenance tasks prune tables that previously grew without
  bound: `celery_taskmeta` (Celery results older than
  `TASK_RESULT_RETENTION_DAYS`, default 7) and `pending_jobs` (complete or
  failed jobs older than `PENDING_JOB_RETENTION_DAYS`, default 30; pending and
  processing jobs are never deleted). Before pruning, it marks jobs still
  `processing` more than twice the Celery task time limit (2h by
  default) after they started as `failed` ("Worker lost"): their worker
  was killed (time limit, OOM, deploy) before it could record the outcome.
  Celery's own `celery.backend_cleanup` was scheduled onto a queue no worker
  consumes, so it never ran; it is now disabled (`result_expires=None`). The
  first run on a large table deletes a lot, in committed batches of 5000 rows.
  Afterwards, run `VACUUM (ANALYZE) celery_taskmeta, pending_jobs;` once to
  make the space reusable. It does not shrink the files; `VACUUM FULL` does,
  but takes an exclusive lock on each table while it runs, so only use it in a
  quiet window.
- Ingestion commits each new item (as `RAW`) before calling the embedding API,
  so a slow Voyage call no longer outlasts Postgres' 60s idle-in-transaction
  timeout, which killed the connection and rolled the item back. An item whose
  embedding fails is now kept as `FAILED` rather than re-fetched by the next
  sync. The daily `process_raw_items` retries `FAILED` items inserted in the
  last 48 hours (so about twice each), and skips `RAW` items updated in the
  last hour, which may still be embedding. Items that failed before this
  release, or re-embeds of items older than 48 hours, are not retried
  automatically; re-embed those with the `reingest_all_empty_source_items`
  maintenance task.
- Ingest summaries and tags run only on a provider key: a source's explicit key
  (Sources → each source → LLM key), else the default key of whoever brought the
  item in. Items without one are embedded without a summary. Spend counts
  against the key's daily cap. Anyone who can see a source may switch it to one
  of their own keys (they pay from then on), to default, or off.
- Meeting extraction (summary, notes, action items) runs on the meeting's key
  like other ingest LLM work; meetings without one are stored with extraction
  `skipped`. Its prompt, system prompt and model can no longer be overridden
  per call.
- Batch summaries are sent as one batch per paying key, at half the key's
  synchronous price against its daily cap. A key that runs out of daily cap
  or that its provider rejects keeps its requests queued until it can pay; a
  batch the provider refuses (a 4xx other than auth or rate limit) uses up one of
  the requests' `SUMMARIZER_BATCH_MAX_ATTEMPTS`, and the reason is logged.
- Ingestion summaries now go through the Anthropic Message Batches API
  (`SUMMARIZER_USE_BATCH`, default `true`). Items are searchable at once, and
  their summary chunk arrives when the batch ends, usually within an hour.
  Set `SUMMARIZER_USE_BATCH=false` to summarize inline as before. The
  `SUMMARIZER_BATCH_*` settings tune batch size, retries and the
  submit/poll intervals.
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `SUMMARIZER_MODEL`, `RANKER_MODEL`,
  `SESSION_SUMMARIZER_MODEL` and `MEETING_MODEL` are removed: every LLM call
  runs a system agent (Agents → System agents) on a user's own key. Users need
  a default Anthropic or OpenAI key (LLM API keys, `/ui/keys`) for summaries,
  search's LLM steps, meeting notes and session summaries (see Manual steps);
  Claude sessions of users without one stop being summarised. Each provider's
  keys also pay for agents on its models (OpenAI: `openai/gpt-6-astra`,
  `openai/gpt-6.1-sol`, `openai/gpt-6-luna`). The `openai_key` /
  `anthropic_key` docker secrets can be deleted.
- `DISCORD_PROCESS_MESSAGES`, `DISCORD_MODEL` and `DISCORD_MAX_TOOL_CALLS` are
  removed; nothing read them. Delete them from `.env` if set.
- `DEFAULT_LLM_RATE_LIMIT_WINDOW_MINUTES`, `DEFAULT_LLM_RATE_LIMIT_MAX_INPUT_TOKENS`,
  `DEFAULT_LLM_RATE_LIMIT_MAX_OUTPUT_TOKENS` and `LLM_USAGE_REDIS_PREFIX` are
  removed; nothing enforced them. Each key's daily cap limits spend instead.
  The `llm_usage*` Redis keys can be deleted.
- New optional agent-run settings: `AGENT_MAX_TIMEOUT_SEC`
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
- Users can add their own Anthropic/OpenAI API keys for agent runs (LLM API
  keys). Runs are capped per run by the agent's `max_cost_usd` or the new
  `AGENT_DEFAULT_USER_KEY_RUN_COST_USD` (1.00), and per key per day by the key's
  or the agent's daily cap, or else the new `AGENT_DEFAULT_USER_KEY_DAILY_COST_USD`
  (20.00).
  Keys are encrypted with `SECRETS_ENCRYPTION_KEY`.
- Agent runs no longer fall back to the server's `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`:
  each run pays with its owner's key. The `llm_server_key` scope and the
  `AGENT_MAX_RUN_COST_USD` / `AGENT_MAX_DAILY_COST_USD` settings are removed (they only
  bounded server-key spend). Let the `agents` queue drain before upgrading: runs
  queued on the server key fail after the migration.
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
- The LLM API keys page moved to `/ui/keys` and is open to every user; key names are
  now unique per user (tools accept a key's name or id).
- HyDE, query analysis and result scoring run on the searcher's default key for
  their provider and are skipped for users without one; search still works.
  `RANKER_MODEL` is removed; delete it from `.env` if set.
- Search results and `core_fetch` return each item's LLM summary as `summary`.
  A long item's search `content` is its summary instead of its first
  characters, and a match on the summary ranks slightly above an equal match on
  the text. Only summaries made after this update are marked as summaries
  (`chunk.is_summary`): older items show and rank as before until they are
  reingested.

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
- `20261029_telemetry_api_key`: adds a nullable `api_key_id` to
  `telemetry_events` with a foreign key and a partial index. The index is built
  concurrently, so telemetry writes keep flowing, but the build reads the whole
  table and can take a while on large instances. Events stored before it have
  no key.
- `20261030_sync_failing_since`: adds nullable `sync_failing_since` to every
  synced source table, and `sync_error` to `google_folders`, `github_repos` and
  `article_feeds`. Fast. Errors already stored show without a start time until
  the next sync.
- `20261031_transcript_push_only`: makes `transcript_accounts.api_key_encrypted`
  nullable and clears every stored `webhook_secret_encrypted`. Fast.
  Downgrading deletes push-only (`generic`) accounts.
- `20261101_claude_github_tokens`: creates the empty `claude_github_tokens`
  table. Fast.
- `20261102_drop_team_sync`: drops the Discord role and GitHub team columns
  from `teams` (`discord_role_id`, `discord_guild_id`, `auto_sync_discord`,
  `github_team_id`, `github_team_slug`, `github_org`, `auto_sync_github`). Fast.
  The links are lost; downgrading restores the columns empty.
- `20261029_user_keys_only`: pins agents set to the server's key to their owner's default
  key where one exists (the others lose the pin and are refused until their owner adds a
  key), removes the `llm_server_key` scope, drops `agent_runs.key_source`. Fast.
- `20261029_key_names_per_user`: renames same-named keys of one user (`name (provider)`)
  and makes names unique per user. Fast.
- `20261029_llm_spend`: new table for per-key LLM spend outside agent runs. Fast.
- `20261029_source_llm_keys`: adds `llm_key_id`/`llm_disabled` to every source table and
  `discord_bots.created_by_user_id` (backfilled with each bot's lowest-id authorized user).
  Fast.
- `20261029_summary_request_keys`: deletes all `summary_request` rows (see Manual steps)
  and adds `provider_key_id`. Fast.
- `20261103_drop_one_time_keys`: deletes the `api_keys` rows of type `one_time`. Fast.
  Downgrading does not restore them.
- `20261103_user_timezone`: adds `users.timezone` (IANA name, default `UTC`). Fast.
- `20261103_instance_settings`: creates the empty `instance_settings` table. Fast.
- `20261103_summary_batch_pacing`: adds the nullable
  `llm_provider_keys.summary_batch_limit` and `summary_request.submitted_at`. Fast.
- `20261104_chunk_is_summary`: adds `chunk.is_summary` (default false). Fast: a
  constant default doesn't rewrite the table.
- `20261105_unique_note_filename`: one note per filename. Where several notes
  share a filename, all but the newest lose their filename and get a new content
  hash. Their content stays in the database and stays searchable, and the new
  hash stops them from swallowing a later save of the same text. Downgrading
  can't restore the cleared filenames. Then builds a partial unique index on
  `source_item.filename` for notes, concurrently, so writes keep flowing, but the
  build reads the whole `source_item` table and can take a while on large
  instances. If the index build fails, re-run the migration.
- `20261106_unique_person_user`: makes `people.user_id` unique (a user is linked
  to at most one Person). Fast. **It refuses to run if a user is already linked
  to several people** and lists them; see Manual steps for the check to run
  before updating and how to recover.

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
