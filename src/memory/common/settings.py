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


FILE_STORAGE_DIR = pathlib.Path(os.getenv("FILE_STORAGE_DIR", "/tmp/memory_files"))
FILE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
CHUNK_STORAGE_DIR = pathlib.Path(
    os.getenv("CHUNK_STORAGE_DIR", FILE_STORAGE_DIR / "chunks")
)
CHUNK_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
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
EMAIL_SYNC_INTERVAL = int(os.getenv("EMAIL_SYNC_INTERVAL", 3600))
EMAIL_SYNC_INTERVAL = 60


# Embedding settings
TEXT_EMBEDDING_MODEL = os.getenv("TEXT_EMBEDDING_MODEL", "voyage-3-large")
MIXED_EMBEDDING_MODEL = os.getenv("MIXED_EMBEDDING_MODEL", "voyage-multimodal-3")
