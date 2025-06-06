import os
import pathlib
from dotenv import load_dotenv

load_dotenv()


def boolean_env(key: str, default: bool = False) -> bool:
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


# Broker settings
CELERY_QUEUE_PREFIX = os.getenv("CELERY_QUEUE_PREFIX", "memory")
CELERY_BROKER_TYPE = os.getenv("CELERY_BROKER_TYPE", "amqp").lower()  # amqp or sqs
CELERY_BROKER_USER = os.getenv("CELERY_BROKER_USER", "kb")
CELERY_BROKER_PASSWORD = os.getenv("CELERY_BROKER_PASSWORD", "kb")

CELERY_BROKER_HOST = os.getenv("CELERY_BROKER_HOST", "")
if not CELERY_BROKER_HOST and CELERY_BROKER_TYPE == "amqp":
    RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
    RABBITMQ_PORT = os.getenv("RABBITMQ_PORT", "5672")
    CELERY_BROKER_HOST = f"{RABBITMQ_HOST}:{RABBITMQ_PORT}//"

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

storage_dirs = [
    FILE_STORAGE_DIR,
    EBOOK_STORAGE_DIR,
    EMAIL_STORAGE_DIR,
    CHUNK_STORAGE_DIR,
    COMIC_STORAGE_DIR,
    PHOTO_STORAGE_DIR,
    WEBPAGE_STORAGE_DIR,
    NOTES_STORAGE_DIR,
]
for dir in storage_dirs:
    dir.mkdir(parents=True, exist_ok=True)

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
SUMMARIZER_MODEL = os.getenv("SUMMARIZER_MODEL", "anthropic/claude-3-haiku-20240307")

# API settings
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000")
HTTPS = boolean_env("HTTPS", False)
SESSION_HEADER_NAME = os.getenv("SESSION_HEADER_NAME", "X-Session-ID")
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "session_id")
SESSION_COOKIE_MAX_AGE = int(os.getenv("SESSION_COOKIE_MAX_AGE", 30 * 24 * 60 * 60))
SESSION_VALID_FOR = int(os.getenv("SESSION_VALID_FOR", 30))

REGISTER_ENABLED = boolean_env("REGISTER_ENABLED", False) or True
DISABLE_AUTH = boolean_env("DISABLE_AUTH", False)

# Discord notification settings
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_ERROR_CHANNEL = os.getenv("DISCORD_ERROR_CHANNEL", "memory-errors")
DISCORD_ACTIVITY_CHANNEL = os.getenv("DISCORD_ACTIVITY_CHANNEL", "memory-activity")
DISCORD_DISCOVERY_CHANNEL = os.getenv("DISCORD_DISCOVERY_CHANNEL", "memory-discoveries")
DISCORD_CHAT_CHANNEL = os.getenv("DISCORD_CHAT_CHANNEL", "memory-chat")


# Enable Discord notifications if bot token is set
DISCORD_NOTIFICATIONS_ENABLED = (
    boolean_env("DISCORD_NOTIFICATIONS_ENABLED", True) and DISCORD_BOT_TOKEN
)
