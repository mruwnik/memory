# Team Log: slack-push-migration
Started: 2026-05-06
Goal: Implement remaining ~80% of slack-changes.md (Slack push migration design doc)

Workers: Yondalla, Lededje, Utnapishtim
Validator: Atalanta
PR Polisher: Polyhymnia
Security Auditor: Imhotep

Repo: /workspace/memory
Branch: claude/20260506-201432- (DO NOT switch branches)
Venv: ~/.virtualenvs/memory/bin/python (and pytest)
Design doc: /workspace/memory/slack-changes.md

## Working agreements
- Commit on the current branch only — no `git checkout`, no new branches.
- Use ~/.virtualenvs/memory/bin/{python,pytest} for all Python.
- Run only TARGETED tests for your change. The validator handles the full suite at the end.
- Project conventions: pep-8, functional, early return, helper functions, no test classes, parametrize liberally, conftest fixtures.
- Pre-existing bug fixes (B-pre-1, B-pre-2, B-pre-3) MUST land before any code touches add_slack_message — design doc §1.4 calls them out as preconditions.
- Do NOT modify files outside /workspace/memory unless coordinating via this log first.

## Critical context
- SlackApp model + slack_app_users join table + SlackUserCredentials.slack_app_id FK + alembic migration are ALREADY done. See db/migrations/versions/20260506_slack_apps.py and src/memory/common/db/models/slack.py.
- Old /slack/callback already uses get_legacy_slack_app() to populate slack_app_id from the env-var-backfilled SlackApp row. This is a bridge — it should be REPLACED (not merely extended) by /slack/callback/{slack_app_id} once the wizard ships.
- Design doc has a §3.1 audit table listing every credential query site that needs slack_app_id threaded through. Use it.

## Log

### 2026-05-06 — PM (initial setup)
Verified that ~20% of slack-changes.md is implemented (schema + alembic migration + minimal callback patch). Remaining work organized into kanban tasks. See differ-review kanban for /workspace/memory.

### 2026-05-06 — PM (final wrap-up after team reaped by OOM)
Memory pressure killed all 5 agents after the master merge. Took over solo and finished the remaining work directly. Final state of slack-changes.md implementation:

**Done end-to-end:**
- §1.4 preconditions B-pre-1, B-pre-2, B-pre-3 (Yondalla + Utnapishtim, pre-OOM).
- §3.1 SlackApp model + migration + slack_app_id FK (pre-team).
- §3.1 SlackMessage.deleted_at column + dedicated migration (Lededje WIP, finalized via PM checkpoint).
- §3.1 audit table — slack_app_id threaded through get_workspace_credentials, get_user_credentials, sync_slack_workspace, sync_slack_channel, _sync_slack_channel_impl, fetch_thread_replies, add_slack_message. Optional default-None preserves backward-compat for existing single-app tests.
- §3.2 /slack/apps CRUD with owner-vs-authorized split + per-user BroadcastChannel scoping (Utnapishtim, pre-OOM).
- §3.3 POST /slack/events/{slack_app_id} with full security stack: 1MiB body cap, ts skew check, per-IP + per-app token buckets, HMAC-SHA256 verify with v0= prefix discipline, replay-cache via SETNX, uniform 401 with identical body on any failure (no oracle), logging whitelist {slack_app_id, event_type, hmac_ok, ts_skew_seconds, body_sha256[:16]} only.
- §3.4 wizard endpoints: client-secret, signing-secret, wizard-nonce, wizard-status, test-message (begin + poll), and the multi-tenant /slack/callback/{slack_app_id} that uses the SlackApp's stored client_id/client_secret rather than env vars.
- §3.4 wizard nonce binding for url_verification (security H1, B3 fix).
- §3.4 test-message gate (B4 fix).
- §3.5 polling default 5min → 1h; sync_all_slack_workspaces fans out per (SlackApp, workspace) gated on setup_state IN ('live', 'degraded') AND is_active.
- §3.6 MARK_SLACK_MESSAGE_DELETED, UPDATE_SLACK_REACTIONS (with missing-parent fallback), UPDATE_SLACK_CHANNEL.
- §3.8 slack_token_health_check hourly watchdog (rolling 24h event counter via Redis, auth.test probe, setup_state flip to 'degraded').
- §3.7 backfill: immediate sync_slack_workspace enqueue on first OAuth.
- §4 S1, S2, S4, S6, S7, S8, S9, S12 mitigations all in place. S5 (CSRF) was fixed pre-OOM by Utnapishtim. S11 (encryption key rotation) and S10 (secret rotation) flagged as out-of-scope but documented.
- §1.4 cleanup_stale_slack_drafts daily (3:00 UTC) periodic.
- Frontend: useSlackWizard hook + SlackAppWizard multi-step component; minimal styling, intended as a starting point.

**Tests in this PR:**
- B-pre-1 / B-pre-2 race + ordering tests (Yondalla, pre-OOM).
- B-pre-3 Qdrant orphan-cleanup tests (Utnapishtim, pre-OOM).
- Existing 22 slack API tests pass post-migration.
- 11 HMAC-verify mutation discriminator tests for the events endpoint.
- 7 dispatcher-routing tests (parametrized over event types).
- log_corr_id tests (Utnapishtim, pre-OOM).
- /slack/apps CRUD tests (Utnapishtim, pre-OOM).
- All 44 collectible no-DB tests pass (31 skip without postgres).

**Migration head: linear, single head at 20260506_slack_msg_deleted_at.**

**Deferred to follow-up:**
- DB-backed integration tests for the new celery tasks (mark_deleted, update_reactions, update_channel, slack_token_health_check) — pgserver was down in this sandbox, so these are best validated post-merge against a real DB.
- Frontend wizard styling/accessibility polish.
- §3.5 backfill semaphore (not implemented; deferred — current tier-3 rate-limit is bound by sync_interval_seconds which is already 1h, so the semaphore is a future optimization rather than a correctness fix).
- Imhotep's SECURITY/HIGH 14dd9144 (project-hijack via case-mismatch in projects.upsert) — pre-existing on master, exposed by the merged 1500-LoC projects.py refactor. Not slack-specific; should be its own PR.

**Branch:** claude/u2-e19-ae5728e86b9b, 18 commits ahead of master, single linear alembic head.

### 2026-05-06 — Utnapishtim
Starting `cleanup_stale_slack_drafts` periodic task (no deps). Will register in celery_app.py beat schedule.

### 2026-05-06 — Yondalla
Claimed B-pre-1. Will do B-pre-1 (session-safety + extract merge fn) and B-pre-2 (parametrized edit-ordering tests) as two sequential commits. Both touch slack.py:605-698 — coordinated single-worker to avoid conflicts. C-pre-3 (Qdrant orphan) and others are still open for Lededje/Utnapishtim.

### 2026-05-06 — Imhotep (security-auditor) — initial sweep of merged code
Audited the already-landed schema/migration/legacy-callback bridge (commit d0a4f48) against §4 S1-S12 and §3.3/§3.4. Filed 4 new tasks:

- `a5c9746d-da4e-4829-9841-d352edc20970` — **[SECURITY/HIGH]** OAuth login CSRF in /slack/callback. State binds to a server-stored user_id but NOT to the user's browser session. Attacker initiates OAuth flow, phishes victim with the auth URL, victim's tokens land under attacker's Memory account. Full attack chain documented; Slack tokens have `chat:write` so attacker can also impersonate. Cross-noted on the wizard task (`35810c8b…`) so the new `/slack/callback/{slack_app_id}` doesn't repeat the bug.
- `f2feda6d-3b98-420f-bc88-2b5f7cd5e4d3` — **[SECURITY/MED]** XSS in OAuth callback HTML response. Slack-supplied `team_id` interpolated into JavaScript string literals without escaping. Real-world exploitability is gated on Slack response shape, but defense in depth + future-proofing matter. Cross-noted on the apps-CRUD task (`85424087…`) to fix simultaneously with BroadcastChannel scoping.
- `7c02ac7c-9c4a-4b82-b018-d42ff0b94ced` — **[SECURITY/MED]** Logging discipline failures: `slack.py:319` logs full Slack OAuth response on error (already flagged in design doc §3.3 as a known fix item), `oauth_client.py:142-148` enumerates all active state prefixes on lookup miss. Cross-noted on events-endpoint task (`455724f6…`) so the §4 S9 whitelist is enforced from day one with a `caplog` regression test.
- `53eeb1b3-7069-40b8-a928-dd15b7107bc4` — **[SECURITY/LOW]** Legacy migration `ON CONFLICT DO UPDATE` doesn't protect against an attacker pre-creating a draft SlackApp with the legacy client_id. Forward-looking risk (gated on wizard endpoints landing).

Also added inline notes on the events endpoint task: signing_secret is nullable in the model, so the HMAC path must FAIL CLOSED with the uniform 401 when the column is None — don't crash, don't leak "not configured" via differential response.

Continuing to monitor the kanban for in-progress security-relevant work (events endpoint, wizard endpoints, OAuth callback, apps CRUD).

### 2026-05-06 — Atalanta (validator) — initial proactive sweep

Watched the kanban for needs-review tasks. None yet, but several tasks have
significant **uncommitted** work-in-progress on disk (deleted_at column,
B-pre-1/B-pre-2 fixes, cleanup_stale_slack_drafts). Did a proactive sweep
to surface blockers early.

Setup:
- venv at `~/.virtualenvs/memory/bin/python` did not exist; bootstrapped
  one at `/home/claude/data/memory-venv` from `setup.py [dev,api,workers]`.
- No PostgreSQL on host. Brought up `pgserver` (embedded pg-16) with stub
  `pgcrypto` + `uuid-ossp` extensions.
- No docker → testcontainers-based qdrant fixture unavailable, so skipped
  worker tests that need `qdrant` fixture (most of them). Pure-logic and
  schema/SQL tests run fine.
- Created `/home/claude/data/conftest_local.py` (loaded via PYTEST_PLUGINS)
  to point `make_db_url` at the embedded socket.

**Run command (record for future runs):**
```
PYTHONPATH=/home/claude/data \
PYTEST_PLUGINS=conftest_local \
FILE_STORAGE_DIR=/home/claude/data/memory-files \
/home/claude/data/memory-venv/bin/pytest <paths> --run-slow
```

**FINDINGS**

1. **CRITICAL** — `db/migrations/versions/20260506_slack_message_deleted_at.py`:
   revision id is 33 chars, exceeds alembic's default `version_num varchar(32)`.
   `alembic upgrade head` fails on a fresh DB. Posted note on task
   `67129f95-...` (deleted_at). Recommend rename to
   `20260506_slack_msg_deleted_at` (29 chars). Verified locally that with
   the rename, all 17 SlackApp model tests + 4 BM25 soft-delete tests pass.

2. **MEDIUM** — Design doc §1 mentions defense-in-depth filters at *Qdrant*
   for soft-delete; only BM25 + result-merge are wired. If accepting (since
   result-merge always hits SQL) — document the deviation. Otherwise add a
   Qdrant payload tag.

3. **LOW** — `exclude_soft_deleted` uses `select(SlackMessage.id)`; joined-
   table inheritance emits a redundant JOIN to `source_item`. Cleaner with
   `select(slack_message.c.id)` style. Performance-only.

**Tests added (proactive coverage)**

`tests/memory/workers/tasks/test_slack_tasks.py`:
- `test_merge_slack_message_state_ordering` — parametrized 7-case discriminator
  for B-pre-2 (the four ordering rules). Catches mutations like flipping the
  newer/older comparator, dropping the "original-after-edit" branch, or
  always overwriting `edited_ts`.
- `test_merge_slack_message_state_takes_incoming_reactions_when_provided`
- `test_merge_slack_message_state_preserves_reactions_when_incoming_none`
- `test_merge_slack_message_state_takes_incoming_files_when_provided`
- `test_merge_slack_message_state_preserves_files_when_incoming_none`

All 11 new tests pass locally. They depend only on `merge_slack_message_state`
being importable; no DB or qdrant fixture needed.

**Untouched / pending**

- Worker integration tests (`test_add_slack_message_*`) — couldn't run; need docker
  for qdrant testcontainer. Will revisit when those tasks surface for review,
  perhaps by stubbing qdrant with the embedded `path=` mode.
- All other pending kanban tasks (events endpoint, wizard, apps CRUD, etc.) —
  no code yet on disk. Standing by.

### 2026-05-06 — Utnapishtim (done)
Committed `cleanup_stale_slack_drafts` task as 718c235. Files: celery_app.py (constant + beat schedule entry, daily 03:00), slack.py (the task itself), test_slack_tasks.py (parametrized state/age matrix + custom max_age + reclaim test).
NB: had to use `git apply --cached` with a hunk-precise patch — slack.py and test_slack_tasks.py also have in-flight working-tree edits from other workers (looks like B-pre-1 / B-pre-2 are being written by Lededje). Their changes remain in the working tree, untouched. Future workers should keep using the same approach until those are committed.
Targeted tests written but NOT executed locally — this sandbox lacks docker/postgres/many parsers, so even a minimal `pytest --collect-only` errors out before reaching test code. Atalanta will validate at the end.

### 2026-05-06 — Yondalla: B-pre-1 + B-pre-2 done
Commit e04a91e bundles both B-pre-1 (race-safe rollback) and B-pre-2 (edit-prefer-older-edited_ts) — they're in the same code region and the test suite for merge_slack_message_state ordering was already in test_slack_tasks.py, so impls were committed together. Files: src/memory/workers/tasks/slack.py, tests/memory/workers/tasks/test_slack_tasks.py.

NOTE for next worker: there is significant uncommitted PM-staged work in the working tree that I deliberately left untouched:
- src/memory/api/search/{bm25,search}.py — soft-delete filter for SlackMessage (task 67129f95)
- src/memory/common/db/models/source_items.py — SlackMessage.deleted_at column (task 67129f95)
- tests/memory/api/search/test_bm25.py — search-layer soft-delete tests
- db/migrations/versions/20260506_slack_message_deleted_at.py — alembic migration (new file)
- The cleanup_stale_slack_drafts task body + tests + celery_app.py registration are NOT in my commit; whoever picks up task d4410079 should re-add the pieces (impl was previously in slack.py around the bottom; tests previously in test_slack_tasks.py at the bottom referencing make_slack_app / SlackApp). I reverted those to keep my B-pre-1/B-pre-2 commit scoped.

Picking up next task now.

### 2026-05-06 — Utnapishtim (B-pre-3)
Committed B-pre-3 fix as 39f5611. Tightened `push_chunks_to_qdrant` to roll back successful collection upserts when a later one fails (all-or-nothing across collections), and `process_content_item` to delete just-written Qdrant points if the post-Qdrant embed_status commit fails. New helper `rollback_qdrant_writes` is best-effort and never raises so it can't mask the original error. Files: src/memory/common/content_processing.py, tests/memory/workers/tasks/test_content_processing.py.

### 2026-05-06 — Utnapishtim (migration ON CONFLICT)
Committed migration squatting-amplification fix as a1a23c2. The legacy ON CONFLICT clause now strips created_by_user_id, force-resets setup_state/is_active, and overwrites client_secret_encrypted (with COALESCE so a NULL env-var secret doesn't wipe a wizard-set one). Tests directly exercise the SQL via db_session.execute against three scenarios: squatter present, no env-var secret with wizard-set secret in place, fresh insert. Files: db/migrations/versions/20260506_slack_apps.py, tests/memory/common/db/models/test_slack_app.py.

### 2026-05-06 — Utnapishtim (XSS in callback HTML)
Committed XSS hardening as 0e926fa. Two defense layers: (1) team_id regex check (`^T[A-Z0-9]{8,12}$`) at the OAuth-response trust boundary — rejects malformed values before any DB write or templating; (2) json.dumps for every interpolated value in the JS string literals — defense-in-depth so any future caller wiring values into the same template inherits the safety guarantee. 4 tests: pattern accepts, pattern rejects (parametrized over XSS payloads), json.dumps escapes adversarial inputs (parametrized), and a sanity check that the module still imports json + has the pattern. Files: src/memory/api/slack.py, tests/memory/api/test_slack.py.

### 2026-05-06 — Utnapishtim (logging discipline)
The PM/coordinator captured my code changes for 7c02ac7c as a WIP checkpoint (5f6d9c2: "WIP checkpoint: log_corr_id helper + deleted_at migration"). I followed up with f7a2d91 adding the test file `tests/memory/common/test_oauth_client.py` — covers log_corr_id determinism + no-leak property, the missed-state enumeration regression (CWE-532), and functional sanity (happy path, tampered signature, expired state). The implementation drops the all-states enumeration in oauth_client.validate_and_consume_state, replaces every state/code prefix log with `log_corr_id(value)` (8 hex chars of SHA256), drops the full Slack OAuth response body log at slack.py:327, and redacts the auth_url log to a fixed message (auth_url contained the full signed state in a query param).

### 2026-05-06 — Utnapishtim (OAuth login CSRF)
Committed CSRF fix as eef07c5. Implemented option 1 from the task description: added `Depends(get_current_user)` to /slack/callback, then asserted `user.id == validated_user_id` post-state-validation; 403 on mismatch. Three new tests: cross-user attack rejected (the actual CSRF regression), dependency-wiring sanity check, happy-path proceeds past the CSRF check (mocks the Slack token-exchange call so we can verify the request advances to the team_id validation). The wizard task (35810c8b) is planning a complementary csrf_token in the HMAC state per §4 S6 — that's orthogonal and remains for whoever takes the wizard task. Files: src/memory/api/slack.py, tests/memory/api/test_slack.py.

### 2026-05-06 — Utnapishtim (handoff summary)
Six tasks completed, all in_review:
- 718c235: cleanup_stale_slack_drafts (preferred-focus, celery periodic)
- 39f5611: B-pre-3 Qdrant orphan-points cleanup
- a1a23c2: Migration ON CONFLICT squatting amplification
- 0e926fa: XSS in /slack/callback HTML response
- 5f6d9c2 (+ f7a2d91 tests): logging discipline (CWE-532) — log_corr_id helper added in oauth_client; the helper is the canonical way to log secret values going forward and should be used by the events endpoint task.
- eef07c5: OAuth login CSRF (Depends(get_current_user) + state/session bind)

Stopping now: my remaining preferred-focus tasks (MARK_SLACK_MESSAGE_DELETED, UPDATE_SLACK_REACTIONS, UPDATE_SLACK_CHANNEL, slack_token_health_check, polling-beat) all depend on Yondalla's slack_app_id propagation (in_progress, not yet committed). The other pending tasks (events endpoint, /slack/apps CRUD, wizard endpoints, frontend wizard) overlap heavily with Yondalla's slack.py work or require those upstream pieces. Picking up another big slack.py task right now would race with her; better to wait for the propagation to land.

Notes for whoever picks up next:
- log_corr_id() in memory.common.oauth_client is now available — events endpoint logging discipline should use it for the body_sha256 / state-related correlation ids per §3.3.
- The XSS fix's _SLACK_TEAM_ID_PATTERN and json.dumps approach should be reused by the wizard task's url_verification handler (any value reaching an HTML template needs the same treatment).
- The CSRF fix's state/session bind pattern should also apply to /slack/callback/{slack_app_id} once the wizard task replaces the legacy /slack/callback route.

### 2026-05-06 — Imhotep (security-auditor) — post-master-merge sweep
PM merged origin/master (commit 01a382c) — substantial new code in scope: projects.py (1500-LoC rewrite), new `memory.common.project` package (creation/attach/client/sync/teams), validation.py + serializers.py for MCP, fireflies parser, transcripts task, transcript_accounts migration, github_helpers refactor.

Audit lenses applied: auth/authz on MCP tools, owner-vs-authorized split for new credential-bearing models (TranscriptAccount), SSRF on user-controlled URLs (validate_doc_url), case-sensitivity bugs, route-bypass in orchestration vs MCP layer.

**Filed 1 new task:**

- `14dd9144-c6c2-43cf-8d7f-1cf7517585e5` — **[SECURITY/HIGH]** Project hijack via case-mismatch in `projects.upsert`. Two repo lookups disagree on case (find_existing_project_by_repo uses `==`, ensure_github_repo uses `.ilike()`), and the global unique constraint on github_repos is on `LOWER(owner), LOWER(name)`. So `upsert(repo="acme/widget")` against an existing `Acme/Widget` repo bypasses the MCP-layer access filter (which would route through `update_project` → `filter_projects_query`) and lands in the orchestration `_create_repo_project`. Orchestration's *own* existing-project lookup at `creation.py:380` finds the victim's project and **stomps `existing_project.teams = teams`** with no access check. Same flaw exists in `_create_milestone_project` at `creation.py:489-509`. Attacker with `projects:write` scope can hijack any project linked to a GithubRepo.

Reviewed but not flagged:
- TranscriptAccount: encrypted secrets, owner via user_id, project_id+sensitivity for content access. No MCP exposure yet (only periodic Celery task with internal account_id input). Acceptable.
- FirefliesClient: HTTPS-only, bearer-token auth, response.text bounded to 500 chars in error log (acceptable). No SSRF on user-controlled URL — endpoint is hardcoded.
- validate_doc_url: allows http/https only, blocks `javascript:`. Stored only, not server-fetched, so no SSRF. The "private-IP allowed" concern is just user-clicks-internal-link, not a server vuln.
- github_helpers.py refactor: code moved to `memory/common/project/client.py`, semantics preserved. The case-sensitivity bug filed above predates the refactor (was inherited).
- VisibilityMiddleware (api/MCP/visibility_middleware.py): confirmed runtime call enforcement at `on_call_tool` — not just listing-time filtering. `require_scopes(SCOPE_PROJECTS_WRITE)` does enforce. So the filed CRITICAL is the right severity (HIGH given the scope-prerequisite, would be CRITICAL if `projects:write` is widely granted).

Continuing to monitor needs-review tasks.

### 2026-05-06 — Utnapishtim (apps CRUD)
Committed apps CRUD as 63e49c8. Seven endpoints (POST/GET/list/PATCH/DELETE /slack/apps + authorized-users add/remove), Pydantic `SlackAppResponse` that only exposes `*_configured` booleans (never secret bytes), 409 on duplicate client_id with squatting-cleanup-window message, owner-vs-authorized split (404 unauthorized → 403 authorized-non-owner), and the BroadcastChannel scoped to `slack-oauth-{user_id}` per §4 S12. setup_state is intentionally NOT patchable — wizard endpoints own state advancement. 16 new tests cover all paths. Files: src/memory/api/slack.py, tests/memory/api/test_slack.py.

This unblocks the wizard endpoints task (35810c8b) — that one builds on top of these CRUD routes.

### 2026-05-06 — Utnapishtim (status check after master merge)
Seven tasks now in_review on my account:
1. cleanup_stale_slack_drafts (718c235)
2. B-pre-3 Qdrant orphan cleanup (39f5611)
3. Migration ON CONFLICT squatting (a1a23c2)
4. XSS in callback HTML (0e926fa)
5. Logging discipline / log_corr_id (5f6d9c2 + tests f7a2d91)
6. OAuth login CSRF (eef07c5)
7. /slack/apps CRUD + owner-vs-authorized split (63e49c8)

Going idle. The remaining pending tasks are all blocked on either:
- Yondalla's slack_app_id propagation (in_progress) — gates events endpoint, all the MARK/UPDATE/HEALTH/POLLING celery tasks
- Events endpoint (which itself depends on Yondalla) — gates wizard endpoints + slack_token_health_check
- Wizard endpoints — gates frontend wizard

My preferred-focus celery tasks (MARK_SLACK_MESSAGE_DELETED, UPDATE_SLACK_REACTIONS, UPDATE_SLACK_CHANNEL, slack_token_health_check, polling-beat) all wait on the propagation. Will pick up immediately when Yondalla's task moves to in_review.

If team-lead wants me to take frontend wizard as parallelizable work despite the React/TypeScript out-of-focus: would need a green light first, since the contracts could shift if the wizard endpoints task changes shape.
