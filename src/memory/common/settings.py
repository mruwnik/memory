import os
import pathlib
from dotenv import load_dotenv

load_dotenv()


DB_USER = os.getenv("DB_USER", "kb")
DB_PASSWORD = os.getenv("DB_PASSWORD", "kb")
DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "kb")

def make_db_url(user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT, db=DB_NAME):
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"

DB_URL = os.getenv("DATABASE_URL", make_db_url())


FILE_STORAGE_DIR = pathlib.Path(os.getenv("FILE_STORAGE_DIR", "/tmp/memory_files"))
FILE_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

# Maximum attachment size to store directly in the database (10MB)
MAX_INLINE_ATTACHMENT_SIZE = int(os.getenv("MAX_INLINE_ATTACHMENT_SIZE", 1 * 1024 * 1024))  