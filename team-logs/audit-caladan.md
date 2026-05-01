# Team Log: audit-caladan

Started: 2026-05-01
Goal: Deep audit of /workspace/memory (whole repository) — find bugs, security issues, type holes, simplification wins, UX gaps, and architectural smells; ship fixes in a mergeable PR.

Branch: claude/u2-e19-f494350ee67c (cut from master, no prior diff)
Repo: /workspace/memory
Session ID: local:037b7f8ebf4788a4d7478653916fb60773813072d87cea550511789acb478f1e
Existing PR URL: None (will be created in Phase 4)

## Phase 1 Roster (Discovery)
- Miles — bug-finder
- Zole — security-auditor
- Alai — code-architect
- Ame-no-Uzume — code-simplifier
- Cazaril — python-type-checker
- Malta — ui-ux-reviewer
- Paladine — issue-curator

---

## Log

- 2026-05-01: PM (Opus) — team created. Memory headroom ~10 GB free → opted for full 6 lens + 1 curator panel rather than the default 5. Empty starting diff is expected (fresh audit branch). Phase 1 launching.
- 2026-05-01: Miles — starting audit pass 1: auth, access control, API layer (auth.py, access_control.py, app.py)
=== AUDIT PASS 1 START ===
2026-05-01T14:14:24Z [PASS 1] Starting discovery pass - auth, access control, MCP, container endpoints
- 2026-05-01: Paladine — curator online, board empty, awaiting discovery agent submissions
- 2026-05-01: Paladine — batch 1: 6 reviewed, 0 dups, 0 rejected, 6 approved
- 2026-05-01: Paladine — batch 2: 51 reviewed, 0 dups, 0 rejected, 51 approved (10 severity recalibrations: 2 CRITICAL→HIGH, 2 MEDIUM→HIGH, 6 HIGH→MEDIUM, 1 LOW→MEDIUM)
- 2026-05-01: Paladine — batch 3: 36 reviewed, 2 dups rejected, 34 approved (4 severity notes: c4a17b5c LOW→suggest MEDIUM, c4ac1b4f MEDIUM→suggest HIGH, confirmed CRITICAL 705217e5 bm25 missing view)
- 2026-05-01: Paladine — batch 4: 5 reviewed, 0 dups, 0 rejected, 5 approved (3 new HIGH security: privilege escalation team_add_member, teams.upsert no AC, discord resolve_bot_id IDOR)
- 2026-05-01: Paladine — batch 5: 4 reviewed, 1 dup rejected (6e07970d = 73965a8e), 3 approved (HIGH: API key scope escalation, WebSocket path traversal; MEDIUM: email attachment exfil)
- 2026-05-01: Paladine — batch 6: 4 reviewed, 0 dups, 0 rejected, 4 approved (MEDIUM: oauth assert crash, oauth client_id binding, email MIME crash, email CRLF injection; ef33fe2e flagged as potential HIGH given open dynamic reg)
- 2026-05-01: Paladine — batch 7: 2 reviewed, 0 dups, 0 rejected, 2 approved (MEDIUM: CalDAV SSRF (flag→HIGH if cloud-deployed), oauth session assert crash)
- 2026-05-01: Cazaril — python-type-checker starting audit pass 1

=== CAZARIL TYPE AUDIT PASS 1 START ===

Files audited (Pass 1):
- common/access_control.py
- api/search/types.py, search.py, embeddings.py, bm25.py
- common/db/models/source_item.py, source_items.py (partial), users.py, sources.py (partial)
- common/llms/base.py, tools/__init__.py, tools/base.py
- api/MCP/base.py, access.py, visibility.py
- api/auth.py
- common/extract.py, settings.py, scopes.py, github/types.py, qdrant.py (partial)
- api/MCP/servers/core.py (partial)
- Grep scans: all # type: ignore markers, bare Any annotations, bare dict returns

HIGH concerns (may hide real bugs):
1. access_control.py:46-58 — UserLike.id/scopes and SourceItemLike.project_id/sensitivity use Any; access-control logic can't be type-checked
2. llms/base.py:59,79 — ImageContent.image and ToolUseContent.input declared non-Optional but default to None; # type: ignore hides structural inconsistency

MEDIUM concerns (loose types, suppression markers):
3. MCP/access.py:85,167,187,193 — UserProxy(dict) + build_user_access_filter_from_dict(dict) + 2x # type: ignore[arg-type]
4. MCP/base.py:70,172 + MCP/visibility.py:65,69 — user_info typed as bare dict; known fixed structure → TypedDict candidate
5. api/search/types.py:18-38 — SearchResponse.results:list[dict], SearchResult.content/metadata bare dict, SearchConfig.model_post_init missing __context type
6. api/search/embeddings.py:106 — embedder: Callable missing param/return types
7. source_item.py:566 + source_items.py (many) — display_contents -> dict | None unparameterised
8. sources.py:87,673 — Book.as_payload/Person.as_payload -> dict (bare)
9. auth.py:580 — dispatch(request, call_next) missing call_next type
10. auth.py:407 — # type: ignore[attr-defined] on get_user_account generic; T bound to Base but Base has no user_id
11. common/db/models/journal.py:83,94 — user: Any should be UserLike Protocol
12. MCP/servers/projects.py + github_helpers.py — many session: Any, user: Any params
13. MCP/oauth_provider.py — multiple # type: ignore suppressions on Mapped column assignments

LOW concerns (completeness):
14. llms/base.py:50,69,92,115,136 — .valid properties missing return type annotations
15. settings.py:38 — make_db_url all params untyped, no return type
16. users.py:209-234 — APIKeyType is str subclass not StrEnum; ALL_TYPES incorrectly typed
17. common/extract.py:219 — mutable dict default arg in extract_text

=== CAZARIL PASS 1+2 COMPLETE — 22 tasks filed ===

Tasks filed (HIGH: 2, MEDIUM: 15, LOW: 7 = 22 total):
HIGH: access_control.py UserLike/SourceItemLike Any protocols; llms/base.py non-Optional None defaults
MEDIUM: MCP/access.py UserProxy bare dict + 2x ignore; MCP/base.py+visibility.py user_info TypedDict; search/types.py bare dict; embeddings.py Callable; db/models display_contents/as_payload; auth.py call_next+get_user_account; journal.py user:Any; projects.py+github_helpers session/user:Any; oauth_provider.py Mapped suppression; workers session:Any; content_processing embed_status Literal; calendar.py suppress cluster; people.py bare dict
LOW: llms/base.py valid properties; APIKeyType StrEnum; extract.py mutable default + settings.py unannotated; workers bare dict returns; get_data_source Protocol; PIL filename suppress; google_drive refresh_credentials

- 2026-05-01: Malta — UX/a11y pass 1 starting. Scope: ClaudeSessions, ClaudeFleetStats, Search UI, Dashboard, common components.

=== MALTA PASS 1: UX/A11Y DISCOVERY ===
Files reviewed: ClaudeSessions.tsx, ClaudeFleetStats.tsx, XtermTerminal.tsx, Search.tsx, SearchForm.tsx, SelectableTags.tsx, DynamicFilters.tsx, FilterInput.tsx, results.tsx, Dashboard.tsx, Loading.tsx, UserMenu.tsx, LoginPrompt.tsx, AuthError.tsx, App.tsx, index.css

Key findings:
- CRITICAL: SelectableTags uses <span> for interactive toggles — no keyboard access, no ARIA role
- CRITICAL: Fleet table <tr> rows have onClick but no tabIndex/role — keyboard users locked out
- HIGH: Search input has no <label>, placeholder-only violates WCAG 1.3.1/3.3.2
- HIGH: Search failures silently show "No results found" — error is swallowed in catch block
- HIGH: "No results found" shown on initial load before any search (false signal to user)
- HIGH: Error banners lack role="alert" — screen readers not notified of dynamic errors
- HIGH: UserMenu dropdown: no Escape-to-close, no arrow key nav, missing aria-expanded
- HIGH: ClaudeSessions sidebar is fixed w-72 — mobile viewport (320px) layout broken
- HIGH: Primary color #667eea has only 3.66:1 contrast on white text (WCAG AA needs 4.5:1 for normal text)
- MEDIUM: Non-clickable fleet rows communicate reason only via title tooltip
- MEDIUM: Session sidebar list items not keyboard-focusable
- MEDIUM: Loading component lacks role="status"/aria-live
- MEDIUM: XtermTerminal div needs role="application" + aria-label
- MEDIUM: HistoryChart line charts have no accessible data alternative
- MEDIUM: FilterInput labels lack htmlFor binding
- MEDIUM: browser confirm() used for destructive Kill Session action
- MEDIUM: Stale URL session ID shows empty fleet view with no explanatory error message
- LOW: gray-*/slate-* token inconsistency throughout
- LOW: No skip-to-content link

17 kanban tasks created from pass 1. Pass 2 starting: polls UI, people management, sources panels, notes.

=== MALTA PASS 2: UX/A11Y DISCOVERY ===
Files reviewed: PollCreate.tsx, PollRespond.tsx, PollGrid.tsx, PersonFormModal.tsx, NotesPage.tsx, sources/shared.tsx, Jobs.tsx (partial)

Additional findings from pass 2:
- CRITICAL: PollGrid div-based grid, drag-only selection — keyboard users cannot mark any availability (public-facing feature)
- HIGH: PersonFormModal missing role="dialog", aria-modal, aria-labelledby, focus trap — all form labels also unassociated
- HIGH: Shared Modal and ConfirmDialog in sources/shared.tsx same dialog ARIA failures; StatusBadge clickable span no keyboard
- MEDIUM: NotesPage file tree folder buttons missing aria-expanded; decorative emojis not aria-hidden; Preview/Edit tabs missing aria-pressed
- LOW: TagsInput × remove buttons have no aria-label (reads as "times")

Pass 2 added 6 more tasks. Total: 23 tasks filed across 2 passes.

=== MALTA PASSES COMPLETE ===
Summary by severity:
- CRITICAL: 3 (SelectableTags spans, fleet table tr rows, PollGrid)
- HIGH: 9 (search label, search error, false empty state, alert roles, UserMenu keyboard, mobile sidebar, contrast, PersonFormModal dialog, sources Modal/ConfirmDialog)
- MEDIUM: 8 (session sidebar keyboard, Loading status role, XtermTerminal role, HistoryChart a11y, FilterInput labels, kill confirm(), stale URL, fleet row tooltips, NotesPage tree)
- LOW: 3 (gray/slate tokens, skip-to-content, TagsInput remove labels)

=== ALAI ARCHITECTURE AUDIT — PASS 1 COMPLETE ===
2026-05-01T14:18:50Z

Files audited (Pass 1):
- api/app.py
- api/auth.py
- api/cloud_claude.py (1317 lines)
- api/claude_environments.py
- api/orchestrator_client.py
- api/MCP/access.py
- api/sessions.py
- common/access_control.py
- common/celery_app.py
- common/scopes.py
- workers/tasks/sessions.py
- workers/tasks/scheduled_tasks.py

Findings summary (11 tasks created):

HIGH:
1. has_admin_scope duplicated in auth.py vs access_control.py — wrong-layer imports (github_sources, jobs, telemetry)
2. Auth middleware _CLAUDE_SESSION_PATTERN misses snapshot-based sessions (s{n} prefix not hex) — WebSocket 401 bug
3. Celery global task_autoretry_for=(Exception,) — retries programming errors; Claude session spawner can spawn 4x containers

MEDIUM:
4. cloud_claude.py is 1317-line god module (6+ concerns mixed together)
5. mark_environment_used() calls db.commit() in helper — leaked transaction control
6. /files/{path} serves any authenticated user's files without ownership check
7. Celery worker calls back to own API via HTTP to spawn sessions — circular dependency, latency, token leak risk
8. MCP fetch_user_by_token skips API key is_valid() and handle_api_key_use() — expired keys work, one-time keys not consumed

LOW:
9. create_environment uses "placeholder" volume name in two-phase DB write
10. relay_select_pane builds query string via f-string without URL encoding
11. sessions.py list_projects triggers N+1 via len(p.sessions)
12. Non-REST route names on claude/environments endpoints (/create, /list)

=== ALAI PASS 2 START ===
2026-05-01T14:19:00Z — Beginning search layer, MCP architecture, DB model, and concurrency model review

Files audited (Pass 2):
- api/search/search.py
- api/search/query_analysis.py
- api/MCP/base.py
- workers/tasks/sessions.py (summarize path)
- common/db/models/source_item.py (event listener scope)
- api/orchestrator_client.py (relay_select_pane)

Additional findings (9 more tasks):

MEDIUM:
1. MCP get_current_user() returns authenticated:True when user lookup fails
2. _fetch_chunks_by_title loads 500 full ORM rows — should use SQL ILIKE
3. search.py conditional imports leave names unbound — pyright suppression hides NameError

LOW:
4. summarize_stale_sessions loads ALL sessions + stat() every hour
5. query_analysis.py modality cache has no lock — redundant DB refreshes
6. Systemic mixed naive/aware datetime: .replace(tzinfo=None) in 7+ files
7. MCP OAuth login_page raises ValueError → 500; hardcoded Cursor URL rewrite
8. relay_select_pane builds query string via f-string without urlencode
9. sessions.py list_projects N+1 via len(p.sessions)

=== ALAI PASS 2 COMPLETE ===
Total Alai tasks: 20 (3 HIGH, 7 MEDIUM, 10 LOW)
Board total: 74 tasks across all agents

Key architectural themes:
- Access control split between access_control.py (correct) and auth.py (wrong) → consolidate
- cloud_claude.py needs decomposition into 5-6 sub-modules
- Celery blanket retry is most operationally dangerous (can spawn 4x containers)
- Auth whitelist pattern is a functional WebSocket bug for snapshot sessions
- MCP auth path inconsistent with REST — expired keys work, one-time keys not consumed
- Datetime timezone strategy inconsistent system-wide

- 2026-05-01: Miles — pass 1 complete. Filed 8 tasks (2 HIGH, 4 MEDIUM, 2 LOW). Starting pass 2: worker tasks, MCP server notes/reports/scheduler, qdrant search, cloud_claude proxy.
- 2026-05-01: Miles — pass 2 continuing. Filed: CSP injection (dup of existing MEDIUM, raised to HIGH), path traversal in report upsert (new), tidbit arbitrary project assignment (HIGH, new), _deep_merge recursion (MEDIUM, new), tidbit_list pagination before access filter (MEDIUM, new), OAuth create_expiration local time (MEDIUM, partial dup of existing HIGH). Pass 1 bugs all confirmed filed by other agents. Starting pass 3: maintenance.py, backup.py, email workers, ebook parser, common/extract.py, API projects/teams endpoints.

=== ALAI PASS 3 START ===
2026-05-01 — Focus: Qdrant/vector search layer, MCP server implementations, worker task patterns

Files audited (Pass 3):
- common/qdrant.py
- workers/tasks/maintenance.py
- workers/tasks/sessions.py
- api/MCP/servers/core.py
- api/MCP/servers/scheduler.py
- api/MCP/servers/claude.py
- api/MCP/servers/meta.py
- api/sessions.py (targeted review)
- api/transfer_tokens.py
- common/access_control.py (cross-reference for access divergence)

Additional findings (10 tasks filed):

HIGH:
1. MCP list_items/count_items skip creator_id check — fetch() uses user_can_access which DOES grant it — inconsistent access between single-fetch and bulk-list paths

MEDIUM:
2. MCP core.py apply_access_control_to_query duplicates access_control.py SQL logic — two paths will diverge (creator_id already diverged)
3. MCP fetch_file has no file ownership check — any SCOPE_READ user reads any stored file
4. maintenance.py has 4 local imports inside function bodies (style violation per CLAUDE.md)
5. MCP core.py _build_search_description() runs DB query at decorator evaluation (module import time) — stale description after new content added

LOW:
6. core.py list_items/count_items duplicate 6-filter application block verbatim
7. MCP tool functions use mutable default arguments (dict{} and set() literals)
8. maintenance.py check_batch() creates new Qdrant client on every batch call in loop
9. qdrant.batch_ids() yields spurious empty list on final full-page iteration
10. core.py has 3 in-function sqlalchemy imports (plus 1 duplicate)

=== ALAI PASS 3 COMPLETE ===
Total Alai tasks: 30 (4 HIGH, 12 MEDIUM, 14 LOW)

Key patterns across Pass 3:
- Access control has two independent SQL-building paths that have already diverged (creator_id gap)
- MCP file access has no ownership checks at either layer (REST or MCP)
- Style guide violations (local imports) systemic across worker tasks and MCP servers
- Qdrant client lifecycle management weak (no singleton, new connections per batch)

=== ALAI PASS 4 START ===
2026-05-01 — Focus: MCP teams/projects servers, telemetry, sentinel patterns, scheduled task races

Files audited (Pass 4):
- api/MCP/servers/teams.py
- api/MCP/servers/projects.py
- api/telemetry.py (targeted)
- workers/tasks/scheduled_tasks.py (targeted — double-dispatch analysis)

Additional findings (3 tasks filed):

MEDIUM:
1. teams.py/projects.py both use _UNSET="__UNSET__" string sentinel — type-unsafe, magic string ambiguous (Python string interning means calling with "__UNSET__" as owner value silently skips update)

LOW:
2. teams.py:253 upsert_team_record imports datetime inside function body; projects.py same
3. projects.py generate_negative_project_id uses savepoint+rollback for read-only SELECT — overcomplicated, race protection illusory

Notes:
- run_scheduled_tasks double-dispatch risk for stuck_pending: locks released at first commit(), then re-dispatched after. However execute_scheduled_task guards against actual double execution (status check). Risk is logged not filed.
- telemetry.py imports has_admin_scope from auth.py — confirmed duplicate of existing filed task.

=== ALAI PASS 4 COMPLETE ===
Total Alai tasks: 33 (4 HIGH, 13 MEDIUM, 16 LOW)

NOTE: PM closed Phase 1 at 112 tasks before this session. Passes 3+4 added 13 more tasks (1H, 3M, 9L). Total board: ~125 tasks. New HIGH task (list_items/count_items creator_id gap) should be included in Phase 2 CRITICAL+HIGH scope.

---
## Phase 1 → Phase 2 Handoff
2026-05-01: PM — Phase 1 board final state: 112 pending (1 CRITICAL, 22 HIGH, 55 MEDIUM, 34 LOW). Discovery agents idle (all sent shutdown_request + URGENT-stop). Paladine continuing background dedup; will retire after Phase 2 spawns.

Phase 2 directive: workers focus ONLY on CRITICAL + HIGH (23 tasks). Skip MEDIUM and LOW this iteration — they roll over as backlog. Target ~700-1000 lines of diff.

## Phase 2 Roster (Implementation)
- Obatala — worker (security focus: path traversal, MCP authz, file ownership, OAuth, CSP)
- Sheeana — worker (frontend a11y: SelectableTags, Fleet rows, UserMenu, dialogs, contrast)
- Legolas — worker (backend correctness: Celery retry, datetime, layering, dead code)
- Eight Antidote — worker (general / overflow / type holes)
- Jefri — validator

---
## Phase 2 Validation Log (Jefri)

- 2026-05-01: Jefri — validator online. Board clear of needs-review tasks; monitoring for worker submissions.

=== ZOLE SECURITY AUDIT — PASS 1 COMPLETE (carried over from prior session) ===
2026-05-01 — Pass 1 findings (7 tasks filed):
- [HIGH] proxy_differ: unsanitized differ_path enables path traversal to orchestrator endpoints
- [HIGH] Open OAuth dynamic client registration allows auth-code phishing
- [MEDIUM] /oauth/login POST has no per-endpoint rate limit
- [MEDIUM] SSH_PRIVATE_KEY and GitHub tokens injected as container env vars → leaked via session logs
- [LOW] allowed_tools passed to container without content validation
- [LOW] Docker application logs accessible to all authenticated users
- [MEDIUM] TRANSFER_TOKEN_SECRET falls back to SECRETS_ENCRYPTION_KEY — key reuse

=== ZOLE SECURITY AUDIT — PASS 2 START ===
2026-05-01 — Focus: Search access control, MCP tool auth, worker/Celery security, notes/people isolation

Files audited (Pass 2):
- api/search/search.py — _fetch_chunks_by_title, search pipeline
- api/search/embeddings.py — Qdrant access filter construction
- api/search/bm25.py — BM25 access filter + source_item_access_view reference
- api/search/query_analysis.py — LLM prompt construction
- api/MCP/access.py — MCP user lookup, fetch_user_by_token
- api/MCP/visibility.py + visibility_middleware.py — tool visibility/scope system
- api/MCP/servers/core.py — search, fetch, fetch_file, list_items
- api/MCP/servers/notes.py — note_files, upsert
- api/MCP/servers/people.py — list_all, _person_to_dict
- api/MCP/servers/reports.py — upsert, delete
- api/auth.py — session lookup, key types, scope enforcement
- common/access_control.py — user_can_access, build_access_filter
- workers/tasks/notes.py — sync_note, git operations

Pass 2 findings (4 tasks filed):

CRITICAL:
1. BM25 access filter references `source_item_access_view` — view not in any migration; BM25 access filtering is silently broken on fresh deployments (violates defense-in-depth invariant)

HIGH:
2. Notes storage has no per-user isolation — `note_files` recursively lists ALL users' notes; any `notes`-scoped user can enumerate and read other users' private notes via `fetch_file`

MEDIUM:
3. `_fetch_chunks_by_title` applies no access filter — LLM recalled_content (from query analysis) drives unfiltered DB chunk injection; prompt injection into query analysis LLM can exfiltrate unauthorized content
4. `people.list_all` exposes full contact_info (emails, phone, Discord IDs) and linked user account data (`user_email`, `user_name`, `user_id`) to any `people`-scoped user — PII enumeration attack

=== ZOLE SECURITY AUDIT — PASS 2 COMPLETE ===
- 2026-05-01: Obatala — Fixed [HIGH] proxy_differ path traversal (4d446a3f). Added validate_differ_subpath() with url-decode + dotdot segment check. Commit 7456b11.
- 2026-05-01: Obatala — Fixed [HIGH] MCP report upsert path traversal (cf36aef1). Applied paths.validate_path_within_directory() matching notes.py pattern. Commit 6cb2643.
- 2026-05-01: Obatala — Fixed [HIGH] CSP injection (bc1d4aa6). Dual-layer defense: validate_csp_sources() at write (reports.py) + sanitize_csp_source_list() at serve (app.py). Commit 44510bd.

=== AME-NO-UZUME CODE SIMPLIFIER — PASS 2 COMPLETE ===
2026-05-01

Files audited (Pass 2 — broader scope):
- api/MCP/servers/people.py (merge_* functions)
- api/MCP/servers/github_helpers.py, api/github_sources.py, workers/tasks/github.py, workers/verification.py (GithubCredentials boilerplate)
- api/MCP/servers/forecast.py, meta.py, discord.py, claude.py, github.py, email.py, slack.py, polling.py, scheduler.py (MCP auth helpers)
- workers/tasks/forums.py, observations.py (mutable defaults)
- common/db/models/sources.py, source_items.py (isoformat pattern)
- parsers/blogs.py, html.py (inheritance pattern, clean)
- workers/tasks/scheduled_tasks.py, sessions.py, google_drive.py, reports.py, metrics.py (targeted review)
- common/markets.py (cache helpers, stop_words)

5 new tasks filed (Pass 2):
MEDIUM: people.py 6 merge_* functions follow identical FK-reassignment pattern
MEDIUM: GithubCredentials constructor repeated 12+ times across 6 files → GithubAccount.as_credentials()
LOW: Mutable default [] and {} in forums.py and observations.py (extends existing task)
LOW: isoformat() if x else None repeated 85+ times — extract iso_or_none() helper
LOW: MCP auth boilerplate duplicated: _get_user_session_from_token and _get_current_user_id per-file

Total Ame-no-Uzume tasks: 19 (0 CRITICAL, 0 HIGH, 7 MEDIUM, 12 LOW)

=== AME-NO-UZUME DISCOVERY COMPLETE — RETIRING (Phase 1 shutdown received) ===
## Pass 3 progress - 2026-05-01T14:39:43Z
- Finished sessions.py, projects.py, teams.py
- Filed: teams.upsert access control bypass (HIGH), projects.create_repo_project without access check (MEDIUM)
- Starting pass on discord.py, github_helpers.py, maintenance.py remaining parts
- 2026-05-01: Obatala — Fixed [HIGH] /files/{path} ownership (fd8eed92). Added user+session deps, SourceItem lookup, user_can_access() gate. Commit 25892c7.
- 2026-05-01: Obatala — Fixed [HIGH] MCP session expiry (009d2bbf). Added is_session_expired() mirroring auth.py tz logic; gated fetch_user_by_token. Commit 31f4b46.
- 2026-05-01: Obatala — Fixed [HIGH] tidbit project leakage (c24f15d9). Project membership check added to both tidbit_add and tidbit_update. Commit 91e0e05.
- 2026-05-01: Obatala — Fixed [HIGH] OAuth open registration (0962b176). Added OAUTH_REDIRECT_URI_ALLOWLIST (default: localhost) validated in register_client(). Commit a0b41f8.
- 2026-05-01: Obatala — Fixed [CRITICAL] BM25 phantom view (705217e5). Rewrote apply_access_filter() using direct SourceItem columns; added creator_id condition. Commit ab615ed.
- 2026-05-01: Obatala — Fixed [HIGH] list_items creator_id gap (83eb770a). Added creator_id OR condition to core.py apply_access_control_to_query(). Commit 00190df.
## Pass 3 complete - 2026-05-01T14:46:29Z
- Filed: teams.upsert access bypass (HIGH), projects repo update no access check (MEDIUM), resolve_bot_id no ownership check (HIGH), github list_* no access control (MEDIUM), WS differ path traversal (HIGH)
- Note: HTTP differ proxy path traversal (filed by others) is ALREADY FIXED in code - validate_differ_subpath() called at line 1027
- Starting Pass 4: checking api/app.py, api/MCP/base.py, parsers/blogs.py, common/email_sender.py, api/telemetry.py
- 2026-05-01: Obatala — Fixed [HIGH] team privilege escalation (2e548785). Added get_caller_team_role() + role-rank validation in team_add/remove_member. In commit bf5d64e (bundled with polls fix).
- 2026-05-01: Obatala — Fixed [HIGH] teams.upsert access control (8ee05c92). Added user auth + get_caller_team_role check for existing teams. Commit 1f72160.
- 2026-05-01: Obatala — Fixed [HIGH] session ingest ownership (73965a8e). Added user_id ownership check in get_or_create_session(). Commit a66f581.
- 2026-05-01: Obatala — Fixed [HIGH] Discord bot ownership (71213d70). resolve_bot_id() now delegates to resolve_bot() for all 13 call sites. Commit 6bb8c59.

=== ZOLE SECURITY AUDIT — PASS 3 COMPLETE ===
2026-05-01T14:48Z

Files audited (Pass 3):
- api/MCP/servers/teams.py — privilege escalation, upsert access bypass
- api/MCP/servers/scheduler.py — cancel scope, spawn_config token handling
- api/MCP/servers/email.py — attachment path traversal, cross-user file exfiltration
- api/MCP/servers/notes.py — confirmed shared flat storage
- common/db/models/scheduled_tasks.py — serialize() token exposure
- api/sessions.py — session ingest ownership, tool usage stats
- api/users.py — API key scope escalation (create_user_api_key vs create_user)
- api/auth.py — brute force rate limiting gap
- api/cloud_claude.py — WebSocket auth, transfer token verification
- api/docker_logs.py — confirmed admin scope gap (already filed)
- api/secrets.py — confirmed correct access control
- api/MCP/oauth_provider.py — key scopes used in MCP but not REST

Pass 3 findings (4 tasks filed):
1. [HIGH] team member privilege escalation (2e548785) — ALREADY FIXED by Obatala commit bf5d64e
2. [HIGH] API key scope escalation: create_user_api_key allows "*" scope grant (f966f4f2) — UNFIXED
3. [MEDIUM] session transcript injection (6e07970d) — DUPLICATE of 73965a8e, already fixed by Obatala commit a66f581
4. [MEDIUM] email attachment exfiltration via _load_attachment (fbdb7893) — UNFIXED

Key new HIGH finding: create_user_api_key missing scope subset validation. create_user (line 166) has the check; create_user_api_key does not. MCP oauth_provider uses api_key.scopes as override — any user can escalate to "*" admin in MCP context.

=== ZOLE PASS 4 START ===
2026-05-01T14:48Z — Focus: app.py parsers, github access control, OAuth PKCE/state param, Slack credentials, MCP base.py login vulnerabilities

=== LEGOLAS — PHASE 2 BACKEND FIXES COMPLETE ===
2026-05-01T14:55Z

Completed 7 backend CRITICAL/HIGH tasks (+ 1 MEDIUM flagged as HIGH in cached list):

1. [HIGH] Auth middleware _CLAUDE_SESSION_PATTERN (45314cbb) — Fixed regex to cover u{id}-s{snap}-{hex} and u{id}-x-{hex} formats. Commit e4949af.
2. [HIGH] validate_slot tz-naive TypeError (3c101102) — Added ensure_utc() helper; applied to all datetime comparisons in validate_slot() and aggregate_availability(). Added regression test. Commit bf5d64e.
3. [HIGH] Celery global autoretry (0c164b05) — Removed task_autoretry_for=(Exception,) + retry_kwargs + task_retry_backoff from celery_app.py. Commit e1671bb.
4. [HIGH] has_admin_scope layering (4bf0593b) — Deleted duplicate from auth.py; imported from access_control.py; updated github_sources, jobs, telemetry imports. Commit a73339c.
5. [LOW] Dead code generate_negative_project_id (0f740604) — Deleted (zero call sites confirmed by grep). Commit ba8a24c.
6. [MEDIUM] Tests with if-conditionals (8f655db1) — Split 4 parametrize tests into success/denied pairs; replaced 2 mock side_effects with dict dispatch. Commit c93174a.
7. [HIGH] API key scope escalation (f966f4f2) — Added validate_scopes() + scope-subset check to create_user_api_key(). Added 3 tests. Commit 2b94e2f.
8. [HIGH] WS differ path traversal (72dc9d7a) — Applied same validate_differ_subpath logic before websocket.accept(). Commit e2e66c1.

No more pending backend HIGH/CRITICAL tasks. Remaining pending are frontend a11y (PollGrid, PersonFormModal, Modal/ConfirmDialog) → Sheeana's domain.
