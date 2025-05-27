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

# Celery settings
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "kb")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "kb")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")

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

storage_dirs = [
    FILE_STORAGE_DIR,
    EBOOK_STORAGE_DIR,
    EMAIL_STORAGE_DIR,
    CHUNK_STORAGE_DIR,
    COMIC_STORAGE_DIR,
    PHOTO_STORAGE_DIR,
    WEBPAGE_STORAGE_DIR,
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
EMAIL_SYNC_INTERVAL = int(os.getenv("EMAIL_SYNC_INTERVAL", 3600))
CLEAN_COLLECTION_INTERVAL = int(os.getenv("CLEAN_COLLECTION_INTERVAL", 86400))
CHUNK_REINGEST_INTERVAL = int(os.getenv("CHUNK_REINGEST_INTERVAL", 3600))

CHUNK_REINGEST_SINCE_MINUTES = int(os.getenv("CHUNK_REINGEST_SINCE_MINUTES", 60 * 24))

# Embedding settings
TEXT_EMBEDDING_MODEL = os.getenv("TEXT_EMBEDDING_MODEL", "voyage-3-large")
MIXED_EMBEDDING_MODEL = os.getenv("MIXED_EMBEDDING_MODEL", "voyage-multimodal-3")
EMBEDDING_MAX_WORKERS = int(os.getenv("EMBEDDING_MAX_WORKERS", 50))
