import logging
import os
import pathlib
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Application name - used for MCP server, queues, Discord channels, etc.
APP_NAME = os.getenv("APP_NAME", "memory")


def boolean_env(key: str, default: bool = False) -> bool:
    if key not in os.environ:
        return default
    return os.getenv(key, "0").lower() in ("1", "true", "yes")


# Database settings
DB_USER = os.getenv("DB_USER", "kb")
if password_file := os.getenv("POSTGRES_PASSWORD_FILE"):
    DB_PASSWORD = pathlib.Path(password_file).read_text().strip()
else:
    DB_PASSWORD = os.getenv("DB_PASSWORD", "kb")

DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "kb")


def make_db_url(
    user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT, db=DB_NAME
):
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


DB_URL = os.getenv("DATABASE_URL", make_db_url())

# Redis settings
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_DB = os.getenv("REDIS_DB", "0")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None  # Treat empty string as None
if REDIS_PASSWORD:
    REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
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
]

# All storage directories (including non-backed-up ones)
all_storage_dirs = storage_dirs + [CHUNK_STORAGE_DIR]

for dir in all_storage_dirs:
    dir.mkdir(parents=True, exist_ok=True)

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
EMBEDDING_MAX_WORKERS = int(os.getenv("EMBEDDING_MAX_WORKERS", 50))

# VoyageAI max context window
EMBEDDING_MAX_TOKENS = int(os.getenv("EMBEDDING_MAX_TOKENS", 32000))
# Optimal chunk size for semantic search
DEFAULT_CHUNK_TOKENS = int(os.getenv("DEFAULT_CHUNK_TOKENS", 512))
OVERLAP_TOKENS = int(os.getenv("OVERLAP_TOKENS", 50))


# LLM settings
if openai_key_file := os.getenv("OPENAI_API_KEY_FILE"):
    OPENAI_API_KEY = pathlib.Path(openai_key_file).read_text().strip()
else:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

if anthropic_key_file := os.getenv("ANTHROPIC_API_KEY_FILE"):
    ANTHROPIC_API_KEY = pathlib.Path(anthropic_key_file).read_text().strip()
else:
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SUMMARIZER_MODEL = os.getenv("SUMMARIZER_MODEL", "anthropic/claude-haiku-4-5")
RANKER_MODEL = os.getenv("RANKER_MODEL", "anthropic/claude-3-haiku-20240307")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", 200000))

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
ENABLE_EMBEDDING_SEARCH = boolean_env("ENABLE_EMBEDDING_SEARCH", True)
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

# API settings
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")
INTERNAL_API_URL = os.getenv("INTERNAL_API_URL", SERVER_URL)
HTTPS = boolean_env("HTTPS", False)
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "session_id")
SESSION_COOKIE_MAX_AGE = int(os.getenv("SESSION_COOKIE_MAX_AGE", 30 * 24 * 60 * 60))
SESSION_VALID_FOR = int(os.getenv("SESSION_VALID_FOR", 30))

# API Rate limiting settings
API_RATE_LIMIT_ENABLED = boolean_env("API_RATE_LIMIT_ENABLED", True)
# Default rate limit: 100 requests per minute
API_RATE_LIMIT_DEFAULT = os.getenv("API_RATE_LIMIT_DEFAULT", "100/minute")
# Search endpoints have a lower limit to prevent abuse
API_RATE_LIMIT_SEARCH = os.getenv("API_RATE_LIMIT_SEARCH", "30/minute")
# Auth endpoints have stricter limits to prevent brute force
API_RATE_LIMIT_AUTH = os.getenv("API_RATE_LIMIT_AUTH", "10/minute")

# Claude scheduled tasks limits
MAX_SCHEDULED_TASKS_PER_USER = int(os.getenv("MAX_SCHEDULED_TASKS_PER_USER", 20))
MIN_CRON_INTERVAL_MINUTES = int(os.getenv("MIN_CRON_INTERVAL_MINUTES", 10))

REGISTER_ENABLED = boolean_env("REGISTER_ENABLED", False)
DISABLE_AUTH = boolean_env("DISABLE_AUTH", False)
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
SLACK_CLIENT_ID = os.getenv("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET = os.getenv("SLACK_CLIENT_SECRET", "")
SLACK_REDIRECT_URI = os.getenv("SLACK_REDIRECT_URI", f"{SERVER_URL}/slack/callback")
SLACK_SYNC_INTERVAL = int(os.getenv("SLACK_SYNC_INTERVAL", 300))  # seconds (5 min)


# S3 Backup settings
S3_BACKUP_BUCKET = os.getenv("S3_BACKUP_BUCKET", "equistamp-memory-backup")
S3_BACKUP_PREFIX = os.getenv("S3_BACKUP_PREFIX", "Daniel")
S3_BACKUP_REGION = os.getenv("S3_BACKUP_REGION", "eu-central-1")
BACKUP_ENCRYPTION_KEY = os.getenv("BACKUP_ENCRYPTION_KEY", "")
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

# Google Drive sync settings
GOOGLE_DRIVE_STORAGE_DIR = pathlib.Path(
    os.getenv("GOOGLE_DRIVE_STORAGE_DIR", str(FILE_STORAGE_DIR / "google_drive"))
)
GOOGLE_DRIVE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
GOOGLE_SYNC_INTERVAL = int(os.getenv("GOOGLE_SYNC_INTERVAL", 60 * 60))  # 1 hour default

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
SECRETS_ENCRYPTION_KEY = os.getenv("SECRETS_ENCRYPTION_KEY", "")

# Salt for secrets encryption key derivation
# Must remain constant for a deployment to decrypt existing secrets
# Change only when rotating ALL secrets
SECRETS_ENCRYPTION_SALT = os.getenv(
    "SECRETS_ENCRYPTION_SALT", "memory-secrets-encryption-salt-v1"
).encode()

# Memory stack identifier - used for network naming with Claude orchestrator
# Set to "prod" or "dev" to separate environments
MEMORY_STACK = os.getenv("MEMORY_STACK", "dev")


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
