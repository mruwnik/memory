# Slack Push Migration — Design Doc

**Status:** Draft v2 (post-review)
**Last updated:** 2026-05-06
**Owner:** TBD

Migrate Slack ingestion from periodic polling (every 5 min) to push delivery via Slack's Events API, with multi-tenant per-Slack-app configuration stored in the database. Keep the existing polling code as a safety-net catch-up loop.

---

## 1. Context and motivation

### 1.1 Current state

- `src/memory/workers/tasks/slack.py` runs `sync_all_slack_workspaces` on a 5-minute Celery beat schedule
- Per workspace: fetches channels list → triggers per-channel sync → calls `conversations.history` with `oldest=last_message_ts` cursor → enqueues `add_slack_message` per message → polls `conversations.replies` for threads
- Auth is already user-token OAuth (`xoxp-...`), stored encrypted in `SlackUserCredentials` (one row per user per workspace)
- Slack app credentials (`SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`) live in environment variables — single shared app per Memory deployment
- `add_slack_message` task is idempotent on `(workspace_id, channel_id, message_ts)` and updates on `edited_ts`

### 1.2 Pain points

1. **Polling latency.** New messages take up to 5 minutes (worst case) to land in the index.
2. **Rate-limit pressure.** Slack's Web API tiers (3/4) cap calls; aggressive polling across many channels eats budget needed for backfill.
3. **Single Slack app per deployment.** Adding a new Slack app requires editing env vars and redeploying.
4. **Setup is opaque.** All-or-nothing config — silent failures far from the input point.

### 1.3 Non-goals

- **Not migrating Slack auth** — already on user OAuth.
- **Not adding Socket Mode.** Memory already exposes a public HTTPS endpoint.
- **Not bridging via Matrix.** ToS risk on owner accounts rules it out.
- **Not changing the message data model.** `SlackMessage`, `SlackChannel`, `SlackWorkspace` schemas stay (one minor addition — `deleted_at` — see §3.6).

### 1.4 Pre-existing bugs uncovered during review

These exist in current code, not introduced by this migration. Listed here because the migration amplifies their impact (more events → more frequent triggering) and the design must either fix them or document tolerance:

- **B-pre-1 (Critical) — `IntegrityError` handler at `slack.py:691-698` corrupts session.** The `try/except IntegrityError` wraps `process_content_item`. After `session.rollback()`, prior in-session work (channel auto-create at `:647-656`, person link at `:686-689`) is also rolled back. Worse: the duplicate path returns `already_exists` without merging fields (reactions, files) from the loser. **Must fix as a precondition** — webhook + polling overlap (§3.5) hits this constantly.
- **B-pre-2 (Critical) — Edit-before-message ordering bug.** If `message_changed` arrives before the original `message`, the insert branch runs with edited content and `edited_ts` set. When the original `message` event arrives, it short-circuits at `:613` — **the canonical pre-edit content is silently dropped, and the embedding reflects the wrong version**. Must fix: on the "already exists" branch, prefer the older `edited_ts`.
- **B-pre-3 (Medium) — Qdrant orphan points on rollback.** `process_content_item` writes to Qdrant before the SQL flush. On rollback, the Qdrant write is not reverted.

These three are **pre-migration prep work**, ~0.5 day. Doc proceeds assuming they're fixed.

---

## 2. Goals

| # | Goal | Acceptance criterion |
|---|---|---|
| G1 | Real-time message ingestion | A message in a connected workspace appears in `SlackMessage` within 5s (p95) |
| G2 | Multi-app, DB-driven config | Adding a new Slack app requires no code changes, env-var changes, or redeploy |
| G3 | Self-service onboarding | A technical user can register a Slack app and connect a workspace via UI in <15min, with each step gated by a real Slack API check |
| G4 | Polling as safety net | If webhook handler is down for hours, polling catches up missed messages |
| G5 | Multi-tenant isolation | App A's events cannot be processed using App B's signing secret; `SlackApp` rows are visible only to authorized users; secrets are visible only to the owner |

---

## 3. Architecture

### 3.1 Data model changes

**New table `SlackApp`** — represents a Slack app registered at api.slack.com. Mirrors the `DiscordBot` pattern: surrogate PK + `is_active` flag + many-to-many user authorization. Slack's `client_id` is stored as a unique non-PK column so URL routing and DB identity can evolve independently.

```python
class SlackApp(Base):
    __tablename__ = "slack_apps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    client_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    signing_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # App-level setup state ONLY. Per-user OAuth completion is implicit in
    # SlackUserCredentials row existence.
    setup_state: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    # 'draft' → 'signing_verified' → 'live' → ('degraded' on watchdog miss)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Owner = sole role with secret-read/rotation rights.
    # `authorized_users` (m:m) can run OAuth and connect workspaces, but cannot
    # see or rotate secrets.
    created_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    authorized_users: Mapped[list[User]] = relationship(
        "User", secondary=slack_app_users, back_populates="slack_apps"
    )

    # Encrypted accessors mirror DiscordBot pattern (discord.py:82-95)
    @property
    def client_secret(self) -> str | None: ...
    # ... same shape for signing_secret

    __table_args__ = (
        CheckConstraint(
            "setup_state IN ('draft','signing_verified','live','degraded')",
            name="valid_slack_app_setup_state",
        ),
    )
```

**Why surrogate PK + unique `client_id`:**
- Decouples DB identity from Slack's `client_id` format (Slack documents it loosely as `\d+\.\d+`)
- URL paths use `id`, not `client_id` — opaque to attackers and not coupled to Slack's spec
- Allows replacing a misconfigured app without rewriting all FK rows

**Squatting mitigation** (security H4): a malicious user creating a draft `SlackApp` with a victim's `client_id` only blocks the victim from registering — they cannot drive the wizard or hijack OAuth, because:
1. Wizard state is bound to `created_by_user_id`; other users cannot complete steps
2. Draft rows older than 24h with `setup_state='draft'` are auto-cleaned by a periodic task (`cleanup_stale_slack_drafts`), making `client_id` reclaimable
3. `client_id` UNIQUE constraint is checked, but a "claim conflict" 409 response tells the admin to wait or contact support

**Modified `SlackUserCredentials`** — adds FK to `SlackApp.id`:

```python
slack_app_id: Mapped[int] = mapped_column(
    BigInteger, ForeignKey("slack_apps.id", ondelete="CASCADE"), nullable=False
)
```

New uniqueness: `(slack_app_id, workspace_id, user_id)`.

**`SlackMessage` adds `deleted_at`** (nullable timestamp). Soft-delete preserves audit but hides from search via filter at the search layer.

**Audit list of every existing query site that needs an `slack_app_id` dimension** (architect's flag — was hidden cost in v1):

| File | Lines | Change |
|---|---|---|
| `api/slack.py:121-128` | `get_user_credentials` | Add `slack_app_id` filter param |
| `api/slack.py:338-341` | `slack_callback` | Use `slack_app_id` from URL path |
| `workers/tasks/slack.py:191-207` | `get_workspace_credentials` | Take `slack_app_id` as required arg |
| `workers/tasks/slack.py:266` | `sync_slack_workspace` | Pass `slack_app_id` through |
| `workers/tasks/slack.py:463` | `_sync_slack_channel_impl` | Look up via channel → workspace → app |
| `workers/tasks/slack.py:616` | `add_slack_message` | Receive `slack_app_id` arg from caller |

This audit is **part of the migration scope**, ~0.5 day.

### 3.2 URL routing

| Old | New |
|---|---|
| `/slack/callback` | `/slack/callback/{slack_app_id}?nonce=...` |
| _no events endpoint_ | `/slack/events/{slack_app_id}` |
| _no apps CRUD_ | `/slack/apps`, `/slack/apps/{id}`, plus per-step wizard endpoints |

The old `/slack/callback` is **removed** — single-app deployments are migrated to the new schema by a one-time data migration that creates a `SlackApp` row from current env-var values.

### 3.3 Events endpoint

```
POST /slack/events/{slack_app_id}
```

**Pre-decrypt fast-rejects** (security M3 — body must not be parsed before cheap rejects):
1. Reject body >1MB (Slack max is ~3MB but real events are small; bound the surface)
2. Reject if `X-Slack-Request-Timestamp` header missing or skew >5min — return 401 with **identical body** to signature failure (security M5)
3. Per-IP token-bucket via Redis (10 req/s burst, 2 req/s sustained per IP)
4. Per-`slack_app_id` token-bucket (50 req/s — well above Slack's normal delivery rate)

**Then** look up `SlackApp` and decrypt `signing_secret`:

5. HMAC-SHA256 verify `v0:{ts}:{body}` against decrypted secret using `hmac.compare_digest`. Verify `v0=` prefix on signature.
6. Idempotency check: `sha256(body)` in Redis with 6-min TTL. Reject duplicates with 200 (Slack's expectation for ack).

**Then** parse and dispatch:

7. `url_verification` → see §3.4 step 5b for nonce-binding logic
8. `event_callback` → branch on `event.type`:
   - `message`: enqueue `ADD_SLACK_MESSAGE` (existing handler — must include `slack_app_id` arg)
   - `message_changed` subtype: enqueue `ADD_SLACK_MESSAGE` (existing handler updates edits — see B-pre-2 fix)
   - `message_deleted` subtype: enqueue new `MARK_SLACK_MESSAGE_DELETED`
   - `reaction_added` / `reaction_removed`: enqueue `UPDATE_SLACK_REACTIONS` (see §3.6 for missing-parent handling)
   - `channel_*`: enqueue `UPDATE_SLACK_CHANNEL`
9. Return 200 within 3s. Heavy work goes to Celery.

**Logging discipline** (security H3): the handler logs only `{slack_app_id, event_type, hmac_ok, ts_skew_seconds, body_sha256}`. Bodies, headers (especially `X-Slack-Signature`), and decrypted secrets must **never** appear in logs. Existing `slack.py` OAuth logs that include full response bodies (line ~382) are a separate fix item.

### 3.4 Wizard flow

Two separate lifecycles — separated per architect's recommendation:

- **App-level (`SlackApp.setup_state`):** `draft → signing_verified → live → (degraded)`
- **Per-user OAuth (implicit):** existence of `SlackUserCredentials` row for `(app, user, workspace)`

**Wizard nonce binding** (security H1): each wizard session generates a one-shot `wizard_nonce` (32 random bytes, hex-encoded), stored in Redis with TTL 30min, scoped to `(slack_app_id, user_id)`. URLs handed to Slack include this nonce as a query param. State advancement requires both signature validity AND nonce match.

| Step | UI | Slack-side | Validation gate |
|---|---|---|---|
| 1 | "Create your Slack app" — link to api.slack.com | User creates app | None |
| 2 | "Paste your Client ID" | User reads from Basic Info page | Format check; create draft `SlackApp` row owned by current user |
| 3 | "Paste Client Secret" — show URL `{base}/slack/callback/{id}` and scope checklist for user to configure on Slack side | User configures redirect URI + scopes on Slack | None (caught by step 4) |
| 4 | "Authorize workspace" — opens OAuth window | User completes Slack OAuth screen | OAuth code exchange validates `client_id` + `client_secret` + redirect URI + scopes. Store `xoxp` token in `SlackUserCredentials`. **OAuth `state` must include `(wizard_nonce, slack_app_id, initiating_user_id)` and is validated on callback** (security M2). |
| 5a | "Paste Signing Secret" | — | Store secret encrypted; no other validation here |
| 5b | "Configure Events URL on Slack" — show `{base}/slack/events/{id}?wizard_nonce={n}` and event-subscription list | User pastes URL into Event Subscriptions, ticks events, clicks Save | Slack POSTs `url_verification` with the nonce in the URL → handler HMAC-verifies AND checks nonce matches the user's active wizard session → respond with challenge → advance to `signing_verified`. UI polls `/slack/apps/{id}/wizard-status` for state change. **Failed verification does not advance state** (security L1); user can re-paste secret and retry. Slack's retry-with-old-secret can't drive state because nonce-bound check (security B3 fix). |
| 6 | "Send a test message containing the token: `<token>`" — display unique token | User posts in any channel they're in | Within 60s, watch for a `message` event scoped to (a) this `slack_app_id`, (b) any workspace owned by the initiating user, (c) containing the token in `event.text`. On match: advance to `live`. On timeout: diagnostic checklist (`auth.test`? user has channels? events subscribed?). Token requirement prevents false-positive from chatty workspaces (B4 fix). |

Wizard is resumable from `setup_state` + presence-or-absence of credentials.

### 3.5 Polling as safety net

Existing `sync_all_slack_workspaces` is preserved with these changes:

- **Beat interval drops from 5 min to 1 hour.** Justification (architect): Slack's webhook retry window is ~3h, and our recovery time objective is ≤1h staleness on partial outage. 1h is the smallest interval that comfortably stays inside Slack's tier-3 rate limits while meeting the RTO.
- **Workspace selection joins through `SlackApp`** (bug B1 fix): only sync workspaces whose `SlackApp.setup_state IN ('live','degraded')`. Skip apps still in setup.
- **Backfill throttling** (bug B5 fix): for first-connection backfill, fan-out to channels is chunked (max 50 concurrent `SYNC_SLACK_CHANNEL` tasks per workspace, controlled by a Redis semaphore) to avoid saturating workers during wizard step 6.

### 3.6 Event handlers

| Event | Existing? | Handler | Notes |
|---|---|---|---|
| `message` (new) | ✓ | `ADD_SLACK_MESSAGE` | Must accept `slack_app_id` |
| `message_changed` | ✓ | `ADD_SLACK_MESSAGE` | Existing edit path; depends on B-pre-2 fix |
| `message_deleted` | ✗ | `MARK_SLACK_MESSAGE_DELETED` | Sets `SlackMessage.deleted_at`; search excludes |
| `reaction_added/removed` | ✗ | `UPDATE_SLACK_REACTIONS` | If parent message row missing (race), enqueue a single-message `conversations.history` lookup, then apply (B2 fix). Reuses existing `acquire_channel_sync_lock` Redis pattern (per fit reviewer) for coalescing. |
| `channel_*` | ✗ | `UPDATE_SLACK_CHANNEL` | Cheap upsert |
| `file_shared` | partial | (no new handler) | Existing `add_slack_message` handles `files` from `message` event |

### 3.7 Backfill on first connection

When `SlackUserCredentials` is first inserted (post-OAuth in step 4), enqueue an immediate `sync_slack_workspace` task. Backfill throttling per §3.5.

### 3.8 Token revocation watchdog

Bug B6 fix. New periodic task `slack_token_health_check` (runs hourly):

- For each `SlackApp` with `setup_state='live'`: count events received in last 24h via Redis counter
- If 0 events AND any of its workspaces have `collect_messages=True`: call `auth.test` on a credential
- On `token_revoked`/`invalid_auth`: flip `setup_state='degraded'`, surface error in dashboard
- Polling continues to run on `degraded` apps as best-effort

---

## 4. Security considerations

| # | Concern | Mitigation |
|---|---|---|
| S1 | Webhook authenticity | HMAC-SHA256 + 5min timestamp window + Redis idempotency cache; uniform 401 for any failure (no oracle) |
| S2 | Multi-tenant secret isolation | `client_id` UNIQUE; URL contains opaque `id`, not secrets; HMAC keyed per-app |
| S3 | Squatting | Wizard state bound to creator; 24h TTL on `draft` rows for reclamation |
| S4 | Owner-vs-authorized split | Only `created_by_user_id` reads/rotates secrets; secrets are never returned to API after entry (response shows `configured: true` flag only); `authorized_users` can install workspaces but not see or rotate secrets |
| S5 | Wizard CSRF / state hijack | `wizard_nonce` in OAuth state and Events URL; both must match active session |
| S6 | OAuth state binding | `state` = HMAC(`wizard_nonce + slack_app_id + initiating_user_id + csrf_token`); validated on callback |
| S7 | Replay protection | 5-min timestamp window AND `sha256(body)` cache with 6-min TTL — covers reactions/deletes (M1 fix) |
| S8 | DoS on events endpoint | Body cap 1MB, per-IP rate limit, per-app rate limit, all checks before signing-secret decrypt |
| S9 | Logging | Whitelist of fields allowed in logs; never log bodies, headers, or decrypted secrets |
| S10 | Secret rotation | Rotation flow drops `setup_state` back to `draft` (or `signing_verified` if only client_secret rotated), requires re-verification |
| S11 | Encryption key | Pre-existing single-key approach; document the constraint that compromise of `SECRET_ENCRYPTION_KEY` leaks all stored secrets. Key rotation is out of scope but flagged. |
| S12 | BroadcastChannel postMessage scope | Existing OAuth callback uses `BroadcastChannel` to notify other tabs; scope to a per-user channel name to prevent cross-tenant leak (security L3) |

---

## 5. Migration plan

### 5.1 Database — single Alembic migration

Per fit reviewer (precedent at `db/migrations/versions/20260201_encrypt_credentials.py`), do all schema changes in one migration:

1. Create `slack_apps` table and `slack_app_users` join table
2. Add `slack_app_id` column to `slack_user_credentials` (nullable initially)
3. Add `deleted_at` column to `slack_messages`
4. Data migration: if `SLACK_CLIENT_ID` env var is set, create one `SlackApp` row from env-var values, mark `setup_state='live'`, backfill all existing `SlackUserCredentials.slack_app_id`. Use `connection.execute` + `Session(bind=connection)` per existing pattern.
5. **Pre-flight dedup** (bug B7 fix): before adding the new uniqueness constraint, scan for any rows that would conflict and resolve (keep newest, log dropped). Required because the old constraint was `(workspace_id, user_id)` and the new one adds `slack_app_id`.
6. Make `slack_user_credentials.slack_app_id` NOT NULL via `NOT VALID` then `VALIDATE` (bug B8 fix — avoids long AccessExclusiveLock during deploy)
7. Add `(slack_app_id, workspace_id, user_id)` uniqueness constraint
8. Drop old `(workspace_id, user_id)` constraint

### 5.2 Code changes

All new code lives in existing files (no new modules):

- `src/memory/common/db/models/slack.py`: add `SlackApp`, `slack_app_users`, modify `SlackUserCredentials`, add `deleted_at` to `SlackMessage`
- `src/memory/api/slack.py`: add `/slack/apps` CRUD, wizard endpoints, `/slack/events/{id}` handler; refactor existing routes to use `slack_app_id` URL path
- `src/memory/workers/tasks/slack.py`: add `MARK_SLACK_MESSAGE_DELETED`, `UPDATE_SLACK_REACTIONS`, `UPDATE_SLACK_CHANNEL`, `slack_token_health_check`, `cleanup_stale_slack_drafts` tasks; modify all credential queries to filter on `slack_app_id`; fix B-pre-1 and B-pre-2
- `src/memory/common/celery_app.py`: register new tasks under `f"{SLACK_ROOT}.update_reactions"` etc. naming convention
- `frontend/`: wizard component (multi-step form, polling for async transitions)

### 5.3 Test plan

Tests in `tests/memory/api/test_slack.py` and `tests/memory/workers/tasks/test_slack_tasks.py`. Use existing fixtures from `tests/conftest.py` (`db_session`, `admin_user`, `regular_user`, `admin_session`, `user_session`) per project conventions. Plain test functions, no classes, parametrize liberally.

Coverage:
- Unit: HMAC verify (valid, invalid, wrong-app secret, expired ts, missing headers, replay-cache hit); wizard state transitions; per-event-type dispatch; `slack_app_id` propagation through credential queries
- Integration: full OAuth round-trip with mock Slack; `url_verification` end-to-end with nonce binding; concurrent events from two apps
- Manual: real Slack test app, full wizard top-to-bottom; webhook downtime → polling catch-up; revoked-token watchdog flips state to `degraded`

### 5.4 Rollback

Polling preserved end-to-end. To roll back: disable webhook route, revert beat schedule to 5 min. No reverse data migration needed (new tables are inert if unused).

---

## 6. Effort estimate (revised)

| Component | v1 estimate | v2 estimate (with review) |
|---|---|---|
| Pre-migration bug fixes (B-pre-1, B-pre-2, B-pre-3) | — | 0.5 day |
| DB models + migration | 0.5 day | 0.75 day (more constraints, dedup) |
| Backend API (CRUD, wizard, events handler with full security stack) | 1 day | 2 days |
| Audit + update all `SlackUserCredentials` query sites | — | 0.5 day |
| New Celery tasks (deletes, reactions, channels, watchdog, draft cleanup) | 1 day | 1 day |
| Frontend wizard | 1 day | 1.5 days (nonce flow, retry handling) |
| Tests | 0.5 day | 1 day |
| Slack app config + manual QA | 0.5 day | 0.5 day |
| **Total** | **~4-5 days** | **~7-8 days** |

(~65% confidence on revised estimate; could grow if frontend SSE/polling proves fiddly or if reaction-coalescing edge cases need more iteration.)

---

## 7. Resolved decisions (formerly open questions)

| # | Decision |
|---|---|
| 1 | **Reaction coalescing:** ship naive overwrite + reuse existing `acquire_channel_sync_lock` Redis pattern; revisit only if hot-spot observed |
| 2 | **Per-app rate limiting:** include in v1 (security M3 makes it required, not optional) |
| 3 | **`xapp-` token column:** defer; nullable column can be added later |
| 4 | **`message_deleted` retention:** soft-delete with `deleted_at`, exclude from search but retain row |
| 5 | **Multi-app same-workspace:** allow, but each `SlackApp` only sees its own events (per Slack's design); deduplication by unique constraint on messages handles double-receipt cleanly post B-pre-1 fix; reaction races resolved by per-message Redis lock |
| 6 | **Wizard accessibility:** power-user feature for v1; document with screenshots |

## 8. Remaining open questions

1. **Encryption key rotation flow** for `SECRET_ENCRYPTION_KEY` — operational, not blocking, but should be documented before this expands the secret blast radius
2. **`degraded` state UX** — do we email/notify the user, or only surface in dashboard? Probably tied to whether Memory has an existing notification channel
3. **Wizard time budget per step** — UI needs cancel/retry; do we expose an admin-only "force advance" override for support cases?

---

## 9. Alternatives considered

| Alternative | Why rejected |
|---|---|
| Socket Mode | Long-lived process outside Celery; public HTTPS already exists |
| Keep env-var config, only add events | Doesn't address goal G2 |
| One row per (app, workspace) | Slack signing secret is per-app, not per-workspace |
| OAuth `state` for wizard step | Ephemeral; wizard step needs to persist across browser sessions |
| Drop polling entirely | Bad blast radius if webhook handler has any downtime |
| `client_id` as PK | Couples DB identity to Slack format; complicates reclamation; URLs leak the discriminator (architect Q1, security H4) |
| Single `setup_state` covering app + user OAuth | Conflates two distinct lifecycles; per-user OAuth is implicit in credential row existence (architect Q2) |
| Two-step Alembic migration | Single migration matches existing precedent at `20260201_encrypt_credentials.py` (fit reviewer) |

---

## 10. Review log

- **v1** (2026-05-06): initial draft
- **v2** (2026-05-06): post-review. Incorporated findings from code-architect, security-auditor, code-reviewer, bug-finder. Major changes:
  - Surrogate PK + `client_id UNIQUE` (was `client_id` PK)
  - Split app-level `setup_state` from per-user OAuth state (implicit in credentials)
  - Added wizard nonce binding for url_verification and OAuth state
  - Added owner-vs-authorized-users role split for secret access
  - Added pre-decrypt rate limiting and body-size cap
  - Added replay-cache for non-message event types
  - Polling interval 6h → 1h with stated rationale
  - Single Alembic migration, with `NOT VALID`+`VALIDATE` to avoid lock storm
  - Added explicit audit list of all `SlackUserCredentials` query sites
  - Added pre-migration bug fixes section (B-pre-1, B-pre-2)
  - Added test-message token requirement (B4 fix) and reaction-for-missing-parent path (B2 fix)
  - Added token revocation watchdog and stale-draft cleanup tasks
  - Added `deleted_at` column to `SlackMessage`
  - Effort estimate raised from 4-5 days to 7-8 days
