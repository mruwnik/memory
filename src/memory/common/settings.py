import logging
import os
import pathlib
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Application name - used for MCP server, queues, Discord channels, etc.
APP_NAME = os.getenv("APP_NAME", "memory")


def boolean_env(key: str, default: bool = False) -> bool:
    if key not in os.environ:
        return default
    return os.getenv(key, "0").lower() in ("1", "true", "yes")


def secret_env(key: str, default: str = "") -> str:
    """Read a secret from a file (KEY_FILE) or env var (KEY), with file taking priority."""
    if file_path := os.getenv(f"{key}_FILE"):
        return pathlib.Path(file_path).read_text().strip()
    return os.getenv(key, default)


# Database settings
DB_USER = os.getenv("DB_USER", "kb")
DB_PASSWORD = secret_env("POSTGRES_PASSWORD") or secret_env("DB_PASSWORD", "kb")

DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "kb")


def make_db_url(
    user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT, db=DB_NAME
):
    return f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{db}"


DB_URL = os.getenv("DATABASE_URL", make_db_url())

# Redis settings
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_DB = os.getenv("REDIS_DB", "0")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None  # Treat empty string as None
if REDIS_PASSWORD:
    REDIS_URL = f"redis://:{quote(REDIS_PASSWORD, safe='')}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
else:
    REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

# Broker settings
CELERY_QUEUE_PREFIX = os.getenv("CELERY_QUEUE_PREFIX", APP_NAME)
CELERY_BROKER_TYPE = os.getenv("CELERY_BROKER_TYPE", "redis").lower()
CELERY_BROKER_USER = os.getenv("CELERY_BROKER_USER", "")
CELERY_BROKER_PASSWORD = os.getenv("CELERY_BROKER_PASSWORD", REDIS_PASSWORD)

CELERY_BROKER_HOST = os.getenv("CELERY_BROKER_HOST", "") or f"{REDIS_HOST}:{REDIS_PORT}"
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", f"db+{DB_URL}")

# File storage settings
FILE_STORAGE_DIR = pathlib.Path(os.getenv("FILE_STORAGE_DIR", "/tmp/memory_files"))
EBOOK_STORAGE_DIR = pathlib.Path(
    os.getenv("EBOOK_STORAGE_DIR", FILE_STORAGE_DIR / "ebooks")
)
EMAIL_STORAGE_DIR = pathlib.Path(
    os.getenv("EMAIL_STORAGE_DIR", FILE_STORAGE_DIR / "emails")
)
CHUNK_STORAGE_DIR = pathlib.Path(
    os.getenv("CHUNK_STORAGE_DIR", FILE_STORAGE_DIR / "chunks")
)
COMIC_STORAGE_DIR = pathlib.Path(
    os.getenv("COMIC_STORAGE_DIR", FILE_STORAGE_DIR / "comics")
)
PHOTO_STORAGE_DIR = pathlib.Path(
    os.getenv("PHOTO_STORAGE_DIR", FILE_STORAGE_DIR / "photos")
)
WEBPAGE_STORAGE_DIR = pathlib.Path(
    os.getenv("WEBPAGE_STORAGE_DIR", FILE_STORAGE_DIR / "webpages")
)
NOTES_STORAGE_DIR = pathlib.Path(
    os.getenv("NOTES_STORAGE_DIR", FILE_STORAGE_DIR / "notes")
)
PROFILES_FOLDER = os.getenv("PROFILES_FOLDER", "profiles")
DISCORD_STORAGE_DIR = pathlib.Path(
    os.getenv("DISCORD_STORAGE_DIR", FILE_STORAGE_DIR / "discord")
)
SLACK_STORAGE_DIR = pathlib.Path(
    os.getenv("SLACK_STORAGE_DIR", FILE_STORAGE_DIR / "slack")
)
SESSIONS_STORAGE_DIR = pathlib.Path(
    os.getenv("SESSIONS_STORAGE_DIR", FILE_STORAGE_DIR / "sessions")
)
SNAPSHOT_STORAGE_DIR = pathlib.Path(
    os.getenv("SNAPSHOT_STORAGE_DIR", FILE_STORAGE_DIR / "snapshots")
)
REPORT_STORAGE_DIR = pathlib.Path(
    os.getenv("REPORT_STORAGE_DIR", FILE_STORAGE_DIR / "reports")
)
# Host path for snapshots (used by orchestrator which runs on host, not in container)
# In Docker, FILE_STORAGE_DIR is /app/memory_files but host path may differ
HOST_STORAGE_DIR = pathlib.Path(
    os.getenv("HOST_STORAGE_DIR", pathlib.Path(__file__).parent.parent.parent.parent)
)
# Directories requiring encryption during backup (contain sensitive user data).
# CHUNK_STORAGE_DIR is intentionally excluded: chunks are derived data that can be
# regenerated, and not backed up at all (see storage_dirs below).
PRIVATE_DIRS = [
    EMAIL_STORAGE_DIR,
    NOTES_STORAGE_DIR,
    PHOTO_STORAGE_DIR,
    REPORT_STORAGE_DIR,
]

# Directories to backup - chunks excluded (derived data, can be regenerated)
storage_dirs = [
    EBOOK_STORAGE_DIR,
    EMAIL_STORAGE_DIR,
    COMIC_STORAGE_DIR,
    PHOTO_STORAGE_DIR,
    WEBPAGE_STORAGE_DIR,
    NOTES_STORAGE_DIR,
    DISCORD_STORAGE_DIR,
    SLACK_STORAGE_DIR,
    REPORT_STORAGE_DIR,
]

# All storage directories (including non-backed-up ones)
all_storage_dirs = storage_dirs + [CHUNK_STORAGE_DIR]

for dir in all_storage_dirs:
    dir.mkdir(parents=True, exist_ok=True)

# NOTES_STORAGE_DIR and REPORT_STORAGE_DIR must live under FILE_STORAGE_DIR:
# every Note/Report consumer (core_fetch_file, serve_file, paths.to_db_filename)
# stores filenames as FILE_STORAGE_DIR-relative. An env-var override that
# escaped FILE_STORAGE_DIR would silently break all those code paths.
for storage_dir in (NOTES_STORAGE_DIR, REPORT_STORAGE_DIR):
    if not storage_dir.resolve().is_relative_to(FILE_STORAGE_DIR.resolve()):
        raise RuntimeError(
            f"{storage_dir} must be inside FILE_STORAGE_DIR={FILE_STORAGE_DIR}"
        )

# Warn if using default /tmp storage - data will be lost on reboot
if str(FILE_STORAGE_DIR).startswith("/tmp"):
    logger.warning(
        f"FILE_STORAGE_DIR is set to '{FILE_STORAGE_DIR}' which is a temporary directory. "
        "Data stored here may be lost on system reboot. "
        "Set FILE_STORAGE_DIR environment variable to a persistent location for production use."
    )

# Maximum attachment size to store directly in the database (10MB)
MAX_INLINE_ATTACHMENT_SIZE = int(
    os.getenv("MAX_INLINE_ATTACHMENT_SIZE", 1 * 1024 * 1024)
)

# Qdrant settings
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_GRPC_PORT = int(os.getenv("QDRANT_GRPC_PORT", "6334"))
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", None)
QDRANT_PREFER_GRPC = boolean_env("QDRANT_PREFER_GRPC", False)
QDRANT_TIMEOUT = int(os.getenv("QDRANT_TIMEOUT", "60"))


# Worker settings
# Intervals are in seconds
EMAIL_SYNC_INTERVAL = int(os.getenv("EMAIL_SYNC_INTERVAL", 60 * 60))
COMIC_SYNC_INTERVAL = int(os.getenv("COMIC_SYNC_INTERVAL", 60 * 60 * 24))
ARTICLE_FEED_SYNC_INTERVAL = int(os.getenv("ARTICLE_FEED_SYNC_INTERVAL", 30 * 60))
CLEAN_COLLECTION_INTERVAL = int(os.getenv("CLEAN_COLLECTION_INTERVAL", 24 * 60 * 60))
CHUNK_REINGEST_INTERVAL = int(os.getenv("CHUNK_REINGEST_INTERVAL", 60 * 60))
NOTES_SYNC_INTERVAL = int(os.getenv("NOTES_SYNC_INTERVAL", 15 * 60))
LESSWRONG_SYNC_INTERVAL = int(os.getenv("LESSWRONG_SYNC_INTERVAL", 60 * 60 * 24))
SCHEDULED_CALL_RUN_INTERVAL = int(os.getenv("SCHEDULED_CALL_RUN_INTERVAL", 60))
GITHUB_SYNC_INTERVAL = int(os.getenv("GITHUB_SYNC_INTERVAL", 60 * 60))  # 1 hour
GOOGLE_DRIVE_SYNC_INTERVAL = int(
    os.getenv("GOOGLE_DRIVE_SYNC_INTERVAL", 60 * 60)
)  # 1 hour
CALENDAR_SYNC_INTERVAL = int(os.getenv("CALENDAR_SYNC_INTERVAL", 60 * 60))  # 1 hour
TRANSCRIPTS_SYNC_INTERVAL = int(
    os.getenv("TRANSCRIPTS_SYNC_INTERVAL", 2 * 60 * 60)
)  # 2 hours
TRANSCRIPTS_RESCAN_LOOKBACK_DAYS = int(
    os.getenv("TRANSCRIPTS_RESCAN_LOOKBACK_DAYS", 365)
)  # how far back the weekly safety-net rescan looks

# Metrics collection settings
METRICS_COLLECTION_INTERVAL = int(
    os.getenv("METRICS_COLLECTION_INTERVAL", 60)
)  # 60 seconds
METRICS_CLEANUP_HOUR = int(os.getenv("METRICS_CLEANUP_HOUR", 3))  # 3 AM
METRICS_SUMMARY_REFRESH_MINUTE = int(
    os.getenv("METRICS_SUMMARY_REFRESH_MINUTE", 0)
)  # :00

CHUNK_REINGEST_SINCE_MINUTES = int(os.getenv("CHUNK_REINGEST_SINCE_MINUTES", 60 * 24))

# Embedding settings
TEXT_EMBEDDING_MODEL = os.getenv("TEXT_EMBEDDING_MODEL", "voyage-3-large")
MIXED_EMBEDDING_MODEL = os.getenv("MIXED_EMBEDDING_MODEL", "voyage-multimodal-3")

# VoyageAI max context window
EMBEDDING_MAX_TOKENS = int(os.getenv("EMBEDDING_MAX_TOKENS", 32000))
# Optimal chunk size for semantic search
DEFAULT_CHUNK_TOKENS = int(os.getenv("DEFAULT_CHUNK_TOKENS", 512))
OVERLAP_TOKENS = int(os.getenv("OVERLAP_TOKENS", 50))


# LLM settings
OPENAI_API_KEY = secret_env("OPENAI_API_KEY")
ANTHROPIC_API_KEY = secret_env("ANTHROPIC_API_KEY")
SUMMARIZER_MODEL = os.getenv("SUMMARIZER_MODEL", "anthropic/claude-haiku-4-5")
RANKER_MODEL = os.getenv("RANKER_MODEL", "anthropic/claude-3-haiku-20240307")

DEFAULT_LLM_RATE_LIMIT_WINDOW_MINUTES = int(
    os.getenv("DEFAULT_LLM_RATE_LIMIT_WINDOW_MINUTES", 30)
)
DEFAULT_LLM_RATE_LIMIT_MAX_INPUT_TOKENS = int(
    os.getenv("DEFAULT_LLM_RATE_LIMIT_MAX_INPUT_TOKENS", 1_000_000)
)
DEFAULT_LLM_RATE_LIMIT_MAX_OUTPUT_TOKENS = int(
    os.getenv("DEFAULT_LLM_RATE_LIMIT_MAX_OUTPUT_TOKENS", 1_000_000)
)
LLM_USAGE_REDIS_PREFIX = os.getenv("LLM_USAGE_REDIS_PREFIX", "llm_usage")


# Search settings
ENABLE_BM25_SEARCH = boolean_env("ENABLE_BM25_SEARCH", True)
ENABLE_SEARCH_SCORING = boolean_env("ENABLE_SEARCH_SCORING", True)
ENABLE_HYDE_EXPANSION = boolean_env("ENABLE_HYDE_EXPANSION", True)
HYDE_TIMEOUT = float(os.getenv("HYDE_TIMEOUT", "3.0"))
ENABLE_QUERY_ANALYSIS = boolean_env(
    "ENABLE_QUERY_ANALYSIS", True
)  # Runs in parallel with HyDE
ENABLE_RERANKING = boolean_env("ENABLE_RERANKING", True)
RERANK_MODEL = os.getenv("RERANK_MODEL", "rerank-2-lite")
MAX_PREVIEW_LENGTH = int(os.getenv("MAX_PREVIEW_LENGTH", DEFAULT_CHUNK_TOKENS * 16))
MAX_NON_PREVIEW_LENGTH = int(os.getenv("MAX_NON_PREVIEW_LENGTH", 2000))

# OAuth settings
# Comma-separated list of URI prefixes permitted in dynamic client registration.
# Defaults to localhost only, which covers Claude Desktop and Cursor.
# Set to "*" to allow any redirect_uri (not recommended for production).
# Example: "http://localhost,https://app.example.com"
OAUTH_REDIRECT_URI_ALLOWLIST: list[str] = [
    p.strip()
    for p in os.getenv("OAUTH_REDIRECT_URI_ALLOWLIST", "http://localhost,http://127.0.0.1").split(",")
    if p.strip()
]

# API settings
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")
INTERNAL_API_URL = os.getenv("INTERNAL_API_URL", SERVER_URL)
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "session_id")
SESSION_VALID_FOR = int(os.getenv("SESSION_VALID_FOR", 30))

# CORS allow-list for development hosts. The Vite dev server lives on
# http://localhost:5173 by default; trusting it from production lets
# any locally-running attacker JS (other npm projects, malicious VS Code
# previews, DNS-rebound sites) make credentialed cross-origin requests
# and read the response. Gate dev origins on this flag so production
# defaults closed; set ALLOW_LOCALHOST_CORS=true in dev .env files.
ALLOW_LOCALHOST_CORS = boolean_env("ALLOW_LOCALHOST_CORS", False)
LOCALHOST_CORS_ORIGINS = [
    p.strip()
    for p in os.getenv(
        "LOCALHOST_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if p.strip()
]

# API Rate limiting settings
API_RATE_LIMIT_ENABLED = boolean_env("API_RATE_LIMIT_ENABLED", True)
# Default rate limit: 100 requests per minute
API_RATE_LIMIT_DEFAULT = os.getenv("API_RATE_LIMIT_DEFAULT", "100/minute")
# Auth endpoints have stricter limits to prevent brute force.
# Used by /oauth/login and /users/me/change-password — see
# `memory.common.rate_limit`.
API_RATE_LIMIT_AUTH = os.getenv("API_RATE_LIMIT_AUTH", "10/minute")
# Comma-separated set of immediate-hop IPs whose ``X-Forwarded-For`` the
# rate limiter should trust. Anything else falls back to the direct
# connection IP — preventing a remote attacker from rotating XFF to mint
# fresh per-request buckets and bypass the limiter. Default is loopback
# only (safe everywhere); set this to your reverse-proxy / load-balancer
# IPs in production. Use ``*`` to trust every immediate hop (matches the
# ``--forwarded-allow-ips=*`` default in docker/api/Dockerfile, but
# disables the spoofing protection).
RATE_LIMIT_TRUSTED_PROXIES = os.getenv(
    "RATE_LIMIT_TRUSTED_PROXIES", "127.0.0.1,::1"
)

# Claude scheduled tasks limits
MAX_SCHEDULED_TASKS_PER_USER = int(os.getenv("MAX_SCHEDULED_TASKS_PER_USER", 20))
MIN_CRON_INTERVAL_MINUTES = int(os.getenv("MIN_CRON_INTERVAL_MINUTES", 10))

# Maximum allowed body size for `/claude/transfer/push` (tar uploads to a
# Claude container). The endpoint buffers the body in API memory before
# proxying to the orchestrator, so an unbounded read is a single-request
# OOM vector. Default 256 MB; raise if you need to push larger artifacts.
MAX_TRANSFER_PUSH_BYTES = int(
    os.getenv("MAX_TRANSFER_PUSH_BYTES", 256 * 1024 * 1024)
)

# Maximum allowed body size for `/telemetry/ingest` and the OTLP
# fan-out routes (`/v1/metrics`, `/v1/logs`, `/v1/traces`). The handler
# buffers the body in API memory and runs `parse_otlp_json` against it
# synchronously, so an unbounded read is the same OOM vector as
# transfer/push. Default 5 MiB — well above any sane batch from an
# in-process OTLP exporter.
MAX_TELEMETRY_PAYLOAD_BYTES = int(
    os.getenv("MAX_TELEMETRY_PAYLOAD_BYTES", 5 * 1024 * 1024)
)

# Per-request size caps for the authenticated content-upload endpoints
# (``/books/upload``, ``/photos/upload``, ``/reports/upload``). All three
# previously called ``await file.read()`` with no upper bound, which
# meant any authenticated user could buffer multi-GB uploads in API
# RAM (OOM kill the API container) or fill ``FILE_STORAGE_DIR`` (which
# Postgres + Qdrant + every other service shares). Defaults match what
# real users actually upload: 100 MiB ebooks, 50 MiB photos, 25 MiB
# reports. Raise via env var if a deployment needs larger artifacts.
MAX_BOOK_UPLOAD_BYTES = int(
    os.getenv("MAX_BOOK_UPLOAD_BYTES", 100 * 1024 * 1024)
)
MAX_PHOTO_UPLOAD_BYTES = int(
    os.getenv("MAX_PHOTO_UPLOAD_BYTES", 50 * 1024 * 1024)
)
MAX_REPORT_UPLOAD_BYTES = int(
    os.getenv("MAX_REPORT_UPLOAD_BYTES", 25 * 1024 * 1024)
)

DISABLE_AUTH = boolean_env("DISABLE_AUTH", False)
# Paired confirmation flag for DISABLE_AUTH. The single-flag toggle is an
# anti-pattern for kill-switches of this magnitude (a stray env-var leak
# from .env.dev into prod silently turns the entire knowledge base into an
# anonymous read/write API). When DISABLE_AUTH=true and any "this looks
# like prod" signal is set, startup refuses unless this confirmation
# flag is also set to the literal "yes-i-am-sure" value.
DISABLE_AUTH_CONFIRM = os.getenv("I_KNOW_THIS_DISABLES_AUTH", "")


def _is_loopback_url(url: str) -> bool:
    """Return True if ``url`` clearly points at the local machine.

    We deliberately accept only the exact loopback hostnames; anything
    else (including IPv6 link-local, .local mDNS, private RFC1918, etc.)
    is treated as non-loopback so the safety check fails closed.

    ``0.0.0.0`` is intentionally **not** in the loopback set even though
    "the box itself" is a common interpretation. ``0.0.0.0`` is the
    wildcard bind address (``INADDR_ANY``) — semantically "listen on
    every interface" — so a ``SERVER_URL=http://0.0.0.0:8000`` is a
    statement of intent to reach the API from elsewhere on the network,
    not a loopback declaration. The platform's resolution of dialing
    ``0.0.0.0`` is also OS-dependent (loopback on Linux, the public IP
    on Windows / some routed setups) so silently treating it as
    loopback would be a false-negative for the safety check.
    """
    if not url:
        return True
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def validate_disable_auth_safety() -> None:
    """Refuse to start when DISABLE_AUTH=true and prod-like signals are set.

    Called eagerly at FastAPI app startup so a misconfigured deployment
    crash-loops at boot rather than serving every endpoint anonymously.
    Operators who knowingly want anonymous access in a non-loopback
    environment must set ``I_KNOW_THIS_DISABLES_AUTH=yes-i-am-sure``.
    """
    if not DISABLE_AUTH:
        return

    prod_signals: list[str] = []
    if not _is_loopback_url(SERVER_URL):
        prod_signals.append(f"SERVER_URL={SERVER_URL!r} is not loopback")
    if S3_BACKUP_ENABLED:
        prod_signals.append("S3_BACKUP_ENABLED=true")
    non_loopback_redirects = [
        p for p in OAUTH_REDIRECT_URI_ALLOWLIST if p != "*" and not _is_loopback_url(p)
    ]
    if non_loopback_redirects:
        prod_signals.append(
            f"OAUTH_REDIRECT_URI_ALLOWLIST contains non-loopback entries: {non_loopback_redirects}"
        )
    if "*" in OAUTH_REDIRECT_URI_ALLOWLIST:
        prod_signals.append("OAUTH_REDIRECT_URI_ALLOWLIST contains wildcard '*'")

    if not prod_signals:
        return

    if DISABLE_AUTH_CONFIRM == "yes-i-am-sure":
        logger.warning(
            "DISABLE_AUTH=true with production signals %s, but "
            "I_KNOW_THIS_DISABLES_AUTH=yes-i-am-sure is set. Proceeding.",
            prod_signals,
        )
        return

    raise RuntimeError(
        "DISABLE_AUTH=true is set alongside production signals: "
        + "; ".join(prod_signals)
        + ". Refusing to start to avoid serving the API anonymously. "
        "If this is genuinely a development environment, switch "
        "SERVER_URL to localhost / disable S3 backup / restrict the "
        "OAuth redirect allowlist to loopback. To override anyway, "
        "set I_KNOW_THIS_DISABLES_AUTH=yes-i-am-sure."
    )
STATIC_DIR = pathlib.Path(
    os.getenv(
        "STATIC_DIR",
        pathlib.Path(__file__).parent.parent.parent.parent / "frontend" / "dist",
    )
)

# Discord notification settings
DISCORD_BOT_ID = int(os.getenv("DISCORD_BOT_ID", "0"))
DISCORD_ERROR_CHANNEL = os.getenv("DISCORD_ERROR_CHANNEL", f"{APP_NAME}-errors")
DISCORD_ACTIVITY_CHANNEL = os.getenv("DISCORD_ACTIVITY_CHANNEL", f"{APP_NAME}-activity")
DISCORD_DISCOVERY_CHANNEL = os.getenv(
    "DISCORD_DISCOVERY_CHANNEL", f"{APP_NAME}-discoveries"
)
DISCORD_CHAT_CHANNEL = os.getenv("DISCORD_CHAT_CHANNEL", f"{APP_NAME}-chat")


# Enable Discord notifications if bot token is set
DISCORD_NOTIFICATIONS_ENABLED = boolean_env("DISCORD_NOTIFICATIONS_ENABLED", True)
DISCORD_PROCESS_MESSAGES = boolean_env("DISCORD_PROCESS_MESSAGES", True)
DISCORD_MODEL = os.getenv("DISCORD_MODEL", "anthropic/claude-haiku-4-5")
DISCORD_MAX_TOOL_CALLS = int(os.getenv("DISCORD_MAX_TOOL_CALLS", 10))


# Discord collector settings
DISCORD_COLLECTOR_ENABLED = boolean_env("DISCORD_COLLECTOR_ENABLED", True)
DISCORD_COLLECT_DMS = boolean_env("DISCORD_COLLECT_DMS", True)
DISCORD_COLLECT_BOTS = boolean_env("DISCORD_COLLECT_BOTS", True)
DISCORD_COLLECTOR_PORT = int(os.getenv("DISCORD_COLLECTOR_PORT", 8003))
DISCORD_COLLECTOR_SERVER_URL = os.getenv("DISCORD_COLLECTOR_SERVER_URL", "0.0.0.0")
DISCORD_CONTEXT_WINDOW = int(os.getenv("DISCORD_CONTEXT_WINDOW", 10))

# Slack integration settings
# Polling interval is the safety net behind the push events endpoint
# (slack-changes.md §3.5). Slack's webhook retry window is ~3h and we want
# ≤1h staleness on partial outages, so 1h is the smallest interval that
# stays comfortably inside Slack's tier-3 rate limits while meeting RTO.
SLACK_SYNC_INTERVAL = int(os.getenv("SLACK_SYNC_INTERVAL", 3600))  # seconds (1 hour)


# S3 Backup settings
S3_BACKUP_BUCKET = os.getenv("S3_BACKUP_BUCKET", "equistamp-memory-backup")
S3_BACKUP_PREFIX = os.getenv("S3_BACKUP_PREFIX", "Daniel")
S3_BACKUP_REGION = os.getenv("S3_BACKUP_REGION", "eu-central-1")
BACKUP_ENCRYPTION_KEY = secret_env("BACKUP_ENCRYPTION_KEY")
S3_BACKUP_ENABLED = boolean_env("S3_BACKUP_ENABLED", bool(BACKUP_ENCRYPTION_KEY))
S3_BACKUP_INTERVAL = int(
    os.getenv("S3_BACKUP_INTERVAL", 60 * 60 * 24)
)  # Daily by default

# Google OAuth settings
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI", f"{SERVER_URL}/auth/callback/google"
)
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# Google Drive sync interval is configured at GOOGLE_DRIVE_SYNC_INTERVAL
# (see "Source-syncing intervals" near the top of the file). The
# previously-defined GOOGLE_SYNC_INTERVAL and GOOGLE_DRIVE_STORAGE_DIR
# were never read from anywhere; deleted.

# Orphan verification settings
VERIFICATION_BATCH_SIZE = int(os.getenv("VERIFICATION_BATCH_SIZE", 100))
VERIFICATION_INTERVAL_HOURS = int(os.getenv("VERIFICATION_INTERVAL_HOURS", 24))
MAX_VERIFICATION_FAILURES = int(os.getenv("MAX_VERIFICATION_FAILURES", 3))
VERIFICATION_SYNC_INTERVAL = int(
    os.getenv("VERIFICATION_SYNC_INTERVAL", 60 * 60 * 6)
)  # 6 hours

# Session retention settings
SESSION_RETENTION_DAYS = int(os.getenv("SESSION_RETENTION_DAYS", 30))

# SSH key encryption secret for encrypting private keys at rest
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
# This should be unique per deployment - if the same secret is used across
# deployments, encrypted keys will be portable between them.
SECRETS_ENCRYPTION_KEY = secret_env("SECRETS_ENCRYPTION_KEY")

# Salt for secrets encryption key derivation
# Must remain constant for a deployment to decrypt existing secrets
# Change only when rotating ALL secrets
SECRETS_ENCRYPTION_SALT = os.getenv(
    "SECRETS_ENCRYPTION_SALT", "memory-secrets-encryption-salt-v1"
).encode()

# Memory stack identifier - used for network naming with Claude orchestrator
# Set to "prod" or "dev" to separate environments
MEMORY_STACK = os.getenv("MEMORY_STACK", "dev")

# HMAC secret for short-lived signed URLs used by the cloud-claude session
# file transfer endpoints.
#
# Two operator-facing modes:
#
# 1. ``TRANSFER_TOKEN_SECRET`` env var explicitly set → used as-is.
#    Operators who want independently-rotatable secrets configure this
#    directly. Rotation only invalidates in-flight transfer URLs (~60s)
#    instead of forcing a full secrets-encryption-key migration.
#
# 2. ``TRANSFER_TOKEN_SECRET`` empty AND ``SECRETS_ENCRYPTION_KEY`` set →
#    *derive* a domain-separated key from ``SECRETS_ENCRYPTION_KEY`` via
#    HKDF-SHA256 (RFC 5869). The derived key is mathematically distinct
#    from the input key (HKDF is a one-way pseudo-random function with a
#    domain-separating ``info`` string), so HMAC-SHA256 tags computed on
#    transfer URLs do NOT leak any information about the at-rest AES-GCM
#    key that protects user secrets in the DB.
#
#    This gives operators the "single secret to configure" UX the previous
#    bare-``or`` fallback intended, without the crypto-isolation cost of
#    sharing key material across two distinct primitives:
#
#    | Use                      | Algorithm                | Boundary |
#    |--------------------------|--------------------------|----------|
#    | ``SECRETS_ENCRYPTION_KEY`` (raw)     | AES-GCM (Fernet) for at-rest secrets    | DB |
#    | ``TRANSFER_TOKEN_SECRET`` (HKDF-derived) | HMAC-SHA256 of presigned URL | URL |
#
#    Rotating ``SECRETS_ENCRYPTION_KEY`` still rotates the derived
#    transfer secret implicitly — that's expected; the input key changing
#    means every output key changes. But a leak of one no longer trivially
#    discloses the other.
#
# 3. Both empty → ``TRANSFER_TOKEN_SECRET`` stays None and the transfer
#    code path raises a clear error at first mint/verify
#    (``transfer_tokens._require_secret``). Operators who never use cloud-
#    claude transfers don't need to configure either secret.
#
# Domain-separating ``info`` includes ``v1`` so that rotating the
# derivation scheme (e.g. switching to a different hash) cleanly
# invalidates all existing tokens by bumping the version tag.
_TRANSFER_TOKEN_SECRET_HKDF_INFO = b"memory:transfer-token-secret:v1"


def _derive_transfer_token_secret(master_key: str) -> str:
    """HKDF-SHA256-derive a 32-byte (256-bit) HMAC key from ``master_key``.

    Returns hex (the existing ``transfer_tokens._sign`` calls
    ``secret.encode("utf-8")`` so any printable string works; hex keeps
    the value greppable in process listings if it ever leaks). The
    derivation is deterministic — same input → same output — which is
    required so all API instances in a deployment compute the same
    transfer secret without coordination.

    Imports happen at module load (``settings.py`` is imported eagerly
    by the API), so any cryptography-library bug surfaces at startup
    rather than first-token mint.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    derived_bytes = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=SECRETS_ENCRYPTION_SALT,
        info=_TRANSFER_TOKEN_SECRET_HKDF_INFO,
    ).derive(master_key.encode("utf-8"))
    return derived_bytes.hex()


_explicit_transfer_secret = secret_env("TRANSFER_TOKEN_SECRET")
if _explicit_transfer_secret:
    TRANSFER_TOKEN_SECRET: str | None = _explicit_transfer_secret
elif SECRETS_ENCRYPTION_KEY:
    TRANSFER_TOKEN_SECRET = _derive_transfer_token_secret(SECRETS_ENCRYPTION_KEY)
else:
    TRANSFER_TOKEN_SECRET = None

# Default lifetime (seconds) for cloud-claude file transfer URLs.
TRANSFER_TOKEN_TTL_SECONDS = int(os.getenv("TRANSFER_TOKEN_TTL_SECONDS", 60))

# Base URL the API uses to proxy to the Claude session orchestrator. The host
# part is consumed by ``httpx.AsyncHTTPTransport(uds=ORCHESTRATOR_SOCKET)``
# (the actual transport is the Unix socket; the URL hostname is just an
# unused placeholder), but configurable so non-prod stacks or tests can
# override the prefix without patching call sites.
ORCHESTRATOR_BASE_URL = os.getenv(
    "ORCHESTRATOR_BASE_URL", "http://orchestrator"
).rstrip("/")


def parse_csv_set(key: str, default: frozenset[str] = frozenset()) -> frozenset[str]:
    """Parse comma-separated env var into a frozenset."""
    value = os.getenv(key, "")
    if not value.strip():
        return default
    return frozenset(s.strip().lower() for s in value.split(",") if s.strip())


# MCP server configuration
# Comma-separated list of server names to disable (e.g., "slack,forecast")
# Valid names: books, core, discord, email, forecast, github, meta, organizer, people, polling, schedule, slack
DISABLED_MCP_SERVERS: frozenset[str] = parse_csv_set("DISABLED_MCP_SERVERS")

# Custom tasks directory for deployment-specific periodic tasks
CUSTOM_TASKS_DIR: str | None = os.getenv("CUSTOM_TASKS_DIR") or None
