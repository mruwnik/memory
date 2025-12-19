# Memory System Investigation

## Investigation Status
- **Started:** 2025-12-19
- **Last Updated:** 2025-12-19 (Fourth Pass - Complete Verification)
- **Status:** Complete
- **Total Issues Found:** 100+ (original) + 10 new critical issues
- **Bugs Fixed/Verified:** 45+ (fixed or confirmed as non-issues)

---

## Executive Summary

This investigation identified **100+ issues** across 7 areas of the memory system. Many critical issues have been fixed:

### Fixed Issues ✅
- **Security:** Path traversal (BUG-001), CORS (BUG-014), password hashing (BUG-061), token logging (BUG-062), shell injection (BUG-064), rate limiting (BUG-030), filter validation (BUG-028), test SQL injection (BUG-050)
- **Worker reliability:** Retry config (BUG-015), silent failures (BUG-016), task time limits (BUG-035)
- **Search:** BM25 filters (BUG-003), embed status (BUG-019), SearchConfig limits (BUG-031)
- **Infrastructure:** Resource limits (BUG-040/067), Redis persistence (BUG-068), health checks (BUG-043)
- **Code quality:** SQLAlchemy deprecations (BUG-063), print statements (BUG-033/060), timezone handling (BUG-034)

### Remaining Issues
1. **Data migration:** Existing 9,370 book chunks need re-indexing to move from text to book collection (BUG-002 code fix applied)
2. **Search system:** BM25 scores discarded (BUG-026) - architectural change needed for hybrid scoring
3. **Code quality:** Bare exceptions (BUG-047/048), type safety gaps (BUG-045/046)

---

## Critical Bugs (Immediate Action Required)

### BUG-001: Path Traversal Vulnerabilities
- **Severity:** CRITICAL
- **Area:** API Security
- **Files:**
  - `src/memory/api/app.py:54-64` - `/files/{path}` endpoint
  - `src/memory/api/MCP/memory.py:355-365` - `fetch_file` tool
  - `src/memory/api/MCP/memory.py:335-352` - `note_files` tool
- **Description:** No validation that requested files are within allowed directories
- **Impact:** Arbitrary file read on server filesystem
- **Fix:** Add path resolution validation with `.resolve()` and prefix check

### BUG-002: Collection Mismatch ✅ INVESTIGATED & FIXED
- **Severity:** MEDIUM (not as critical as originally thought)
- **Area:** Data/Embedding Pipeline
- **Description:** BookSection._chunk_contents() called extract_text() without specifying modality, defaulting to "text"
- **Impact:** 9,370 book chunks stored in text collection instead of book
- **Root Cause:** `extract_text()` defaults to `modality="text"` but BookSection didn't override it
- **Fix Applied:** Added `modality="book"` to BookSection._chunk_contents() DataChunk creation
- **Note:** Original 1,338 mail items investigation was outdated - current mismatch is 24 mail->text chunks which are actually email attachments (correct behavior)
- **TODO:** Existing 9,370 book chunks need re-indexing to move from text to book collection

### BUG-003: BM25 Filters Completely Ignored
- **Severity:** CRITICAL
- **Area:** Search System
- **File:** `src/memory/api/search/bm25.py:32-43`
- **Description:** BM25 search ignores tags, dates, size filters - only applies source_ids
- **Impact:** Filter results diverge between BM25 and vector search
- **Fix:** Apply all filters consistently in BM25 search

### BUG-004: Search Score Aggregation Broken
- **Severity:** CRITICAL
- **Area:** Search System
- **File:** `src/memory/api/search/types.py:44-45`
- **Description:** Scores are summed across chunks instead of averaged
- **Impact:** Documents with more chunks always rank higher regardless of relevance
- **Fix:** Change to mean() or max-based ranking

### BUG-005: Registration Always Enabled
- **Severity:** CRITICAL
- **Area:** Configuration/Security
- **File:** `src/memory/common/settings.py:178`
- **Description:** Logic error: `REGISTER_ENABLED = boolean_env(...) or True` always evaluates to True
- **Impact:** Open registration regardless of configuration
- **Fix:** Remove `or True`

### BUG-006: API Key Logged in Plain Text
- **Severity:** CRITICAL
- **Area:** Security
- **File:** `src/memory/discord/api.py:63`
- **Description:** Bot API key logged in error message
- **Impact:** Credentials exposed in logs
- **Fix:** Remove API key from log message

---

## NEW CRITICAL BUGS (2025-12-19 Second Pass)

### BUG-061: Insecure Password Hashing Using SHA-256
- **Severity:** CRITICAL 🚨
- **Area:** Authentication/Security
- **File:** `src/memory/common/db/models/users.py:23-26`
- **Description:** Password hashing uses SHA-256 instead of purpose-built password hashing algorithms
- **Code:**
  ```python
  def hash_password(password: str) -> str:
      salt = secrets.token_hex(16)
      return f"{salt}:{hashlib.sha256((salt + password).encode()).hexdigest()}"
  ```
- **Impact:**
  - SHA-256 is designed for speed, making it vulnerable to brute-force attacks
  - Attackers can test billions of password combinations per second with GPUs
  - Even with salt, passwords are at high risk of compromise
- **Fix:** Replace with bcrypt, argon2, scrypt, or PBKDF2 which are designed to be slow
- **Priority:** IMMEDIATE - All existing password hashes are insecure

### BUG-062: Full Token Logging
- **Severity:** HIGH
- **Area:** Security/Logging
- **File:** `src/memory/api/MCP/oauth_provider.py:310`
- **Description:** Full OAuth token logged in plaintext
- **Code:** `logger.info(f"Exchanged authorization code: {token}")`
- **Impact:** Tokens exposed in logs can be used to impersonate users
- **Fix:** Remove token from logs entirely or log only hash/truncated version
- **Related:** Similar issues in lines 85, 398, 429, 443, 448

### BUG-063: Deprecated SQLAlchemy .get() Usage (24+ instances)
- **Severity:** MEDIUM
- **Area:** Database/Code Quality
- **Description:** Using deprecated `session.query(Model).get(id)` pattern
- **Impact:**
  - Will break with SQLAlchemy 2.0+
  - Less efficient than modern API
- **Fix:** Replace with `session.get(Model, id)`
- **Files affected:** auth.py, oauth_provider.py, base.py, discord files, worker tasks
- **Examples:**
  - `src/memory/api/auth.py:79` - `session = db.query(UserSession).get(session_id)`
  - `src/memory/api/MCP/base.py:151` - `user_session = session.query(UserSession).get(access_token.token)`
  - 22 more instances across codebase

### BUG-064: Shell=True Command Execution
- **Severity:** MEDIUM
- **Area:** Security/Code Quality
- **File:** `src/memory/workers/tasks/notes.py:38`
- **Description:** Using `subprocess.run()` with `shell=True`
- **Code:**
  ```python
  cmd = f"git -C {shlex.quote(repo_root.as_posix())} {' '.join(escaped_args)}"
  res = subprocess.run(cmd, shell=True, ...)
  ```
- **Impact:**
  - Unnecessary shell invocation increases attack surface
  - While currently mitigated by shlex.quote(), still best practice violation
- **Fix:** Use subprocess with argument list instead of shell string
- **Note:** Arguments ARE properly escaped with shlex.quote(), reducing immediate risk

### BUG-065: Timing Attack in Password Verification
- **Severity:** MEDIUM-HIGH
- **Area:** Authentication/Security
- **File:** `src/memory/common/db/models/users.py:33`
- **Description:** Password hash comparison uses `==` operator instead of constant-time comparison
- **Code:** `return hashlib.sha256((salt + password).encode()).hexdigest() == hash_value`
- **Impact:**
  - Timing attacks could leak information about password hashes
  - Attackers can measure comparison time to infer hash similarity
  - Combined with weak SHA-256 hashing, enables faster brute-force
- **Fix:** Replace with `secrets.compare_digest(computed_hash, hash_value)`
- **Related to:** BUG-061 (both are password security issues)

### BUG-066: No Unique Index on OAuthState.state
- **Severity:** LOW-MEDIUM
- **Area:** Database/Performance
- **Description:** OAuth state parameter lacks unique constraint at database level
- **Impact:**
  - Could allow duplicate state values
  - Performance degradation on lookups
  - Potential OAuth confusion attacks
- **Evidence:** Migration `20251103_154126_mcp_servers.py:53` has unique constraint on `mcp_servers.state` but `oauth_states` table may lack it
- **Fix:** Add unique index to oauth_states.state column

### BUG-067: Incomplete Resource Limits in Docker Compose
- **Severity:** LOW
- **Area:** Infrastructure
- **Description:** Only one service has resource limits configured
- **File:** `docker-compose.yaml:195`
- **Current:** Only `ingest-hub` has limits: `cpus: 0.5, memory: 512m`
- **Missing:** postgres, redis, qdrant, api, workers have no limits
- **Impact:** Services could consume all host resources causing OOM or CPU starvation
- **Fix:** Add resource limits to all services

### BUG-068: Redis Persistence Disabled
- **Severity:** LOW-MEDIUM
- **Area:** Infrastructure/Data Integrity
- **File:** `docker-compose.yaml:108`
- **Description:** Redis configured with persistence disabled
- **Code:** `redis-server --save "" --appendonly "no"`
- **Impact:**
  - All Redis data (LLM rate limits, usage tracking) lost on restart
  - LLM usage tracking state resets
  - Could allow rate limit bypass after restart
- **Fix:** Enable AOF or RDB persistence unless purely ephemeral cache is intended
- **Note:** May be intentional design decision - verify requirements

---

## FIXED BUGS (Confirmed in Recent Commits)

Based on git history analysis, the following bugs have been FIXED:

### ✅ BUG-001: Path Traversal Vulnerabilities - FIXED
- **File:** `src/memory/api/app.py:48-70`
- **Fix:** Added `validate_path_within_directory()` function
- **Implementation:** Properly validates paths using `.resolve()` and prefix checking

### ✅ BUG-004: Search Score Aggregation - FIXED
- **Commit:** 21dedbe "Fix search score aggregation to use mean instead of sum"
- **Fix:** Changed from sum to mean aggregation

### ✅ BUG-005: Registration Always Enabled - FIXED
- **Commit:** 116d036 "Fix REGISTER_ENABLED always evaluating to True (BUG-005)"
- **File:** `src/memory/common/settings.py:178`
- **Fix:** Removed `or True` from logic

### ✅ BUG-007: Wrong Object Appended in break_chunk() - FIXED
- **Commit:** 28bc10d "Fix break_chunk() appending wrong object (BUG-007)"
- **Fix:** Corrected to append individual item instead of entire chunk object

### ✅ BUG-014: CORS Misconfiguration - FIXED
- **File:** `src/memory/api/app.py:41`
- **Fix:** Changed from `allow_origins=["*"]` to `allow_origins=[settings.SERVER_URL]`

### ✅ Mass Bug Fix
- **Commit:** 52274f8 "Fix 19 bugs from investigation"
- **Note:** 19 additional bugs were fixed in bulk - review commit for details

### ✅ BUG-010: MCP Servers Relationship - ALREADY FIXED
- **File:** `src/memory/common/db/models/discord.py:30-47`
- **Status:** Implemented as @property using dynamic query
- **Implementation:** Uses object_session() to query MCPServerAssignment

### ✅ BUG-011: User ID Type Mismatch - ALREADY FIXED
- **Files:** `users.py:56`, `scheduled_calls.py:24`
- **Status:** Both use Integer type (not BigInteger)
- **Verification:** User.id and ScheduledLLMCall.user_id are both Integer

### ✅ BUG-061 to BUG-068: Security & Infrastructure Fixes - FIXED
- **Commit:** 1c43f1a "Fix 7 critical security and code quality bugs"
- **Fixed:** Password hashing, token logging, shell=True, SQLAlchemy deprecations, Docker limits, Redis persistence

### ✅ BUG-003: BM25 Filters - ALREADY FIXED
- **File:** `src/memory/api/search/bm25.py:32-62`
- **Status:** All filters now applied (size, confidence, observation_types, source_ids)

### ✅ BUG-008: Oversized Chunks - ALREADY FIXED
- **File:** `src/memory/common/chunker.py`
- **Status:** `yield_spans()` guarantees all spans are under max_tokens

### ✅ BUG-009: Race Condition - ALREADY FIXED
- **File:** `src/memory/workers/tasks/scheduled_calls.py:164`
- **Status:** Uses `.with_for_update(skip_locked=True)` for atomic claim

### ✅ BUG-013: Embedding Error Handling - ALREADY FIXED
- **File:** `src/memory/common/embedding.py:78-92`
- **Status:** Has try-except with retry logic and exponential backoff

---

## High Severity Bugs (Most Now Fixed)

### ✅ BUG-007: Wrong Object Appended in break_chunk() - FIXED
- **File:** `src/memory/common/embedding.py:57`
- **Status:** Fixed in commit 28bc10d

### ✅ BUG-008: Oversized Chunks Exceed Token Limits - FIXED
- **Status:** yield_spans() now guarantees token limits

### ✅ BUG-009: Scheduled Call Race Condition - FIXED
- **Status:** Fixed with FOR UPDATE SKIP LOCKED

### ✅ BUG-010: Missing MCP Servers Relationship - FIXED
- **File:** `src/memory/common/db/models/discord.py:30-47`
- **Status:** Implemented as @property using dynamic query

### BUG-011: User ID Type Mismatch
- **Files:** `users.py:47`, `scheduled_calls.py:23`
- **Description:** `ScheduledLLMCall.user_id` is BigInteger but `User.id` is Integer
- **Impact:** Foreign key constraint violations

### BUG-012: Inverted Min Score Thresholds
- **File:** `src/memory/api/search/embeddings.py:186-207`
- **Description:** Multimodal uses 0.25, text uses 0.4 - should be reversed
- **Impact:** Multimodal results artificially boosted

### BUG-013: No Error Handling in Embedding Pipeline
- **File:** `src/memory/common/embedding.py`
- **Description:** No try-except blocks around Voyage AI API calls
- **Impact:** Entire content processing fails on API error

### BUG-014: Unrestricted CORS Configuration ✅ FIXED
- **File:** `src/memory/api/app.py:36-42`
- **Description:** ~~`allow_origins=["*"]` with `allow_credentials=True`~~ Now uses `settings.SERVER_URL`
- **Impact:** ~~CSRF attacks enabled~~ Fixed
- **Status:** ✅ Already fixed - CORS now uses specific origin from settings

### BUG-015: Missing Retry Configuration ✅ FIXED
- **Files:** All task files
- **Description:** ~~No `autoretry_for`, `max_retries` on any Celery tasks~~ Global config in celery_app.py
- **Impact:** ~~Transient failures lost without retry~~ Fixed
- **Status:** ✅ Already fixed - celery_app.py has global retry config (autoretry, max_retries=3, backoff, jitter)

### BUG-016: Silent Task Failures ✅ FIXED
- **File:** `src/memory/workers/tasks/content_processing.py:258-296`
- **Description:** ~~`safe_task_execution` catches all exceptions, returns as dict~~ Now re-raises exceptions
- **Impact:** ~~Failed tasks can't be retried by Celery~~ Fixed
- **Status:** ✅ Already fixed - exceptions are now re-raised after logging to allow Celery retries

---

## Medium Severity Bugs

### Data Layer
- BUG-017: ✅ Missing `collection_name` index - FIXED (Index exists at source_item.py:168)
- BUG-018: N/A AgentObservation dead code - intentional TODO comments for future embedding types
- BUG-019: ✅ Embed status never set to STORED after push - FIXED (properly sets STORED at lines 169, 245)
- BUG-020: ✅ Missing server_id index on DiscordMessage - FIXED (Index exists at source_items.py:428-432)

### Content Processing
- BUG-021: ✅ No chunk validation after break_chunk - FIXED (yield_spans guarantees max_tokens)
- BUG-022: Low priority - extract_ebook creates single chunk, BUT sync_book task properly creates BookSection chunks
- BUG-023: SHA256-only deduplication misses semantic duplicates (`source_item.py:51-91`)
- BUG-024: Email hash inconsistency with markdown conversion (`email.py:171-185`)
- BUG-025: Acceptable - 4 chars/token is common approximation (accurate tokenization requires model-specific tokenizers)

### Search System
- BUG-026: BM25 scores calculated then discarded (`bm25.py:66-70`)
- BUG-027: N/A LLM score fallback - actually reasonable (0.0 means chunk not prioritized when scoring fails)
- BUG-028: ✅ Missing filter validation - FIXED (unknown filter keys now logged and ignored instead of passed through)
- BUG-029: N/A Hardcoded min_score thresholds - intentional (0.25 text, 0.4 multimodal due to different score distributions)

### API Layer
- BUG-030: ✅ Missing rate limiting - FIXED (added slowapi middleware with configurable limits: 100/min default, 30/min search, 10/min auth)
- BUG-031: ✅ No SearchConfig limits - FIXED (enforces 1-1000 limit, 1-300 timeout in model_post_init)
- BUG-032: N/A CSRF protection - already mitigated (uses OAuth Bearer tokens not cookie-based auth, CORS restricts to specific origins)
- BUG-033: ✅ Debug print statements in production - FIXED (no print statements found in src/memory)
- BUG-034: ✅ Timezone handling issues - FIXED (now uses timezone-aware UTC comparison)

### Worker Tasks
- BUG-035: ✅ No task time limits - FIXED (celery_app.py has task_time_limit=3600, task_soft_time_limit=3000)
- BUG-036: Acceptable - IntegrityError caught and returns error (retrying duplicates wouldn't help)
- BUG-037: ✅ Timezone bug in scheduled calls - FIXED (properly converts to UTC and strips tzinfo for DB comparison)
- BUG-038: N/A Beat schedule - standard practice is single beat process; use celery-redbeat for distributed
- BUG-039: ✅ Email sync fails entire account on single folder error - FIXED (process_folder has own try-except, continues to next folder)

### Infrastructure
- BUG-040: ✅ Missing resource limits for postgres, redis, qdrant, api - FIXED in BUG-067
- BUG-041: N/A Backup encryption silently disabled - actually reasonable (S3_BACKUP_ENABLED=False when no key)
- BUG-042: Restore scripts don't validate database integrity (`restore_databases.sh:79`)
- BUG-043: ✅ Health check doesn't check dependencies - FIXED (now checks database and Qdrant connections)
- BUG-044: ✅ Uvicorn proxy headers - FIXED (FORWARDED_ALLOW_IPS now configurable via env var, with secure deployment guidance)

### Code Quality
- BUG-045: 183 unsafe cast() operations (various files)
- BUG-046: 21 type:ignore comments (various files)
- BUG-047: 32 bare except Exception blocks (various files)
- BUG-048: 13 exception swallowing with pass (various files)
- BUG-049: N/A OAuth callback already has CSRF protection (state parameter validated against database, generated with secrets.token_urlsafe)
- BUG-050: ✅ SQL injection in test database handling - FIXED (added identifier validation for database names)

---

## Low Severity Bugs

- BUG-051: Duplicate chunks (16 identical "Claude plays Pokemon" chunks)
- BUG-052: Garbage content in text collection
- BUG-053: No vector freshness index (`source_item.py:157`)
- BUG-054: N/A OAuthToken missing Base inheritance - intentional mixin design (used by OAuthState and OAuthRefreshToken)
- BUG-055: ✅ collection_model returns "unknown" - FIXED (now returns None instead of placeholder)
- BUG-056: ✅ Unused "appuser" in Dockerfile - FIXED (removed unused user creation)
- BUG-057: ✅ Build dependencies not cleaned up - FIXED (added apt-get purge after pip install in Dockerfile)
- BUG-058: N/A Typos in log messages - no log messages found at referenced location
- BUG-059: MockRedis overly simplistic (`tests/conftest.py:24-46`)
- BUG-060: ✅ Print statement in ebook.py:192 - FIXED (changed to logger.debug)

---

## Improvement Suggestions

### High Priority
1. **Implement proper retry logic** for all Celery tasks with exponential backoff
2. **Add comprehensive health checks** that validate all service dependencies
3. **Fix score aggregation** to use mean/max instead of sum
4. **Add rate limiting** to prevent DoS attacks
5. **Implement proper CSRF protection** for OAuth flows
6. **Add resource limits** to all Docker services
7. **Implement centralized logging** with ELK or Grafana Loki

### Medium Priority
1. **Re-chunk oversized content** - add validation to enforce size limits
2. **Add chunk deduplication** based on content hash within same source
3. **Preserve BM25 scores** for hybrid search weighting
4. **Add task progress tracking** for long-running operations
5. **Implement distributed beat lock** for multi-worker deployments
6. **Add backup verification tests** - periodically test restore
7. **Replace cast() with type guards** throughout codebase

### Lower Priority
1. **Add Prometheus metrics** for observability
2. **Implement structured JSON logging** with correlation IDs
3. **Add graceful shutdown handling** to workers
4. **Document configuration requirements** more thoroughly
5. **Add integration tests** for critical workflows
6. **Remove dead code** and TODO comments in production

---

## Feature Ideas

### Search Enhancements
1. **Hybrid score weighting** - configurable balance between BM25 and vector
2. **Query expansion** - automatic synonym/related term expansion
3. **Faceted search** - filter by date ranges, sources, tags with counts
4. **Search result highlighting** - show matched terms in context
5. **Saved searches** - store and re-run common queries

### Content Management
1. **Content quality scoring** - automatic assessment of chunk quality
2. **Duplicate detection UI** - show and merge semantic duplicates
3. **Re-indexing queue** - prioritize content for re-embedding
4. **Content archiving** - move old content to cold storage
5. **Bulk operations** - tag, delete, re-process multiple items

### Email Management
1. **Email filtering rules** - configurable rules to filter/categorize emails (e.g., skip marketing spam but keep order confirmations, shipping notifications, appointment reminders)
2. **Email source classification** - auto-detect email types (transactional, marketing, personal, receipts)
3. **Smart email retention** - keep "useful" emails (orders, bookings, confirmations) while filtering noise

### User Experience
1. **Search analytics** - track what users search for
2. **Relevance feedback** - let users rate results to improve ranking
3. **Personal knowledge graph** - visualize connections between content
4. **Smart summaries** - auto-generate summaries of search results
5. **Email digest** - scheduled summary of new content

### Infrastructure
1. **Auto-scaling workers** - scale based on queue depth
2. **Multi-tenant support** - isolate data by user/org
3. **Backup scheduling UI** - configure backup frequency
4. **Monitoring dashboard** - Grafana-style metrics visualization
5. **Audit logging** - track all data access and modifications

---

## Investigation Log

### 2025-12-19 - Complete Investigation

**Data Layer (10 issues)**
- Missing relationships (mcp_servers)
- Type mismatches (User.id)
- Missing indexes (collection_name, server_id)
- Dead code (AgentObservation)

**Content Processing (12 issues)**
- Critical: break_chunk bug appends wrong object
- Critical: Oversized chunks exceed limits
- Deduplication only on SHA256
- Ebook creates single massive chunk

**Search System (14 issues)**
- Critical: BM25 ignores filters
- Critical: Score aggregation broken (sum vs mean)
- Inverted min_score thresholds
- BM25 scores discarded

**API Layer (12 issues)**
- Critical: Path traversal vulnerabilities (3 endpoints)
- CORS misconfiguration
- Missing rate limiting
- Debug print statements

**Worker Tasks (20 issues)**
- No retry configuration
- Silent task failures
- Race condition in scheduled calls
- No task timeouts

**Infrastructure (12 issues)**
- Missing resource limits
- Backup encryption issues
- Health check incomplete
- No centralized logging

**Code Quality (20+ issues)**
- 183 unsafe casts
- 32 bare exception blocks
- Registration always enabled bug
- API key logging

---

## Database Statistics

```
Sources by Modality:
  forum: 981
  mail: 665
  text: 165
  comic: 115
  doc: 102
  book: 78
  observation: 26
  note: 3
  photo: 2
  blog: 1

Chunks by Collection:
  forum: 8786
  text: 1843
  mail: 1418
  doc: 312
  book: 156
  semantic: 84
  comic: 49
  temporal: 26
  blog: 7
  photo: 2

Vectors in Qdrant:
  forum: 8778
  mail: 2756 (1338 orphaned!)
  text: 505 (1338 missing!)
  doc: 312
  book: 156
  semantic: 84
  comic: 49
  temporal: 26
  blog: 7
  photo: 2

Embed Status:
  STORED: 2056
  FAILED: 81
  RAW: 1
```

---

## Updated Priority List (After Second Pass)

### CRITICAL - Fix Immediately
1. ✅ **FIXED:** Path traversal vulnerabilities (BUG-001)
2. ✅ **FIXED:** Registration always enabled (BUG-005)
3. ✅ **FIXED:** Search score aggregation (BUG-004)
4. ✅ **FIXED:** CORS misconfiguration (BUG-014)
5. ✅ **FIXED:** Wrong object in break_chunk (BUG-007)
6. 🚨 **NEW:** Replace SHA-256 password hashing with bcrypt/argon2 (BUG-061)
7. 🔴 **OPEN:** Fix collection mismatch for 1,338 items (BUG-002)
8. 🔴 **OPEN:** Fix BM25 filter application (BUG-003)
9. 🔴 **OPEN:** Remove API key from logs (BUG-006)

### HIGH Priority
10. 🚨 **NEW:** Stop logging full OAuth tokens (BUG-062)
11. 🚨 **NEW:** Fix timing attack in password verification (BUG-065)
12. 🔴 **OPEN:** Add retry logic to all Celery tasks (BUG-015, BUG-016)
13. 🔴 **OPEN:** Fix scheduled call race condition (BUG-009)
14. 🔴 **OPEN:** Fix oversized chunks exceeding token limits (BUG-008)

### MEDIUM Priority
15. 🚨 **NEW:** Update 24+ deprecated SQLAlchemy .get() calls (BUG-063)
16. 🚨 **NEW:** Remove shell=True from subprocess calls (BUG-064)
17. 🔴 **OPEN:** Add resource limits to Docker services (BUG-040, BUG-067)
18. 🔴 **OPEN:** Missing MCP servers relationship (BUG-010)
19. 🔴 **OPEN:** User ID type mismatch (BUG-011)

### Summary Statistics
- **Total Bugs Found:** 118 (100+ original + 8 new in second pass)
- **Bugs Fixed:** 25+ (confirmed in recent commits)
- **Critical Bugs Open:** 4
- **High Priority Open:** 5
- **Medium/Low Open:** 80+

---

## Investigation Notes

### What Was Checked (Second Pass - 2025-12-19)
✅ Security vulnerabilities (SQL injection, command injection, XSS)
✅ Authentication implementation (password hashing, session management)
✅ Logging practices (credential exposure)
✅ Database patterns (deprecated APIs, missing indexes)
✅ Docker configuration (resource limits, persistence)
✅ OAuth implementation (state management, token handling)
✅ Code quality (exception handling, type safety)
✅ Recent commits and fixes

### Good Security Practices Observed
- ✅ Path traversal protection properly implemented (fixed)
- ✅ CORS properly configured with specific origins (fixed)
- ✅ Secrets loaded from files, not environment variables
- ✅ Services run as non-root users where possible
- ✅ Read-only filesystems for workers
- ✅ Security capabilities dropped in containers
- ✅ Healthchecks configured for critical services
- ✅ Git command arguments properly escaped with shlex.quote()
- ✅ Search result limits enforced (max 1000)
- ✅ Timeout limits enforced (max 300s)
- ✅ Rate limiting infrastructure exists for LLM usage

### Areas Still Needing Attention
- 🔴 Password hashing needs complete overhaul
- 🔴 Logging practices need audit for credential exposure
- 🔴 Database API modernization for SQLAlchemy 2.0
- 🔴 Resource limits need to be added to all services
- 🔴 Redis persistence configuration needs review
