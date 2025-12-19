"""S3 backup tasks for memory files."""

import base64
import hashlib
import io
import logging
import subprocess
import tarfile
from contextlib import contextmanager
from pathlib import Path

import boto3
import redis
from cryptography.fernet import Fernet

from memory.common import settings
from memory.common.celery_app import app, BACKUP_PATH, BACKUP_ALL

logger = logging.getLogger(__name__)

# Backup lock timeout (30 minutes max for backup to complete)
BACKUP_LOCK_TIMEOUT = 30 * 60


@contextmanager
def backup_lock(lock_name: str = "backup_all"):
    """Acquire a distributed lock for backup operations using Redis.

    Prevents concurrent backup operations which could cause resource
    contention and inconsistent state.
    """
    redis_client = redis.from_url(settings.REDIS_URL)
    lock_key = f"memory:lock:{lock_name}"

    # Try to acquire lock with NX (only if not exists) and expiry
    acquired = redis_client.set(lock_key, "1", nx=True, ex=BACKUP_LOCK_TIMEOUT)
    if not acquired:
        raise RuntimeError(f"Could not acquire backup lock '{lock_name}' - backup already in progress")

    try:
        yield
    finally:
        # Release the lock
        redis_client.delete(lock_key)


def get_cipher() -> Fernet:
    """Create Fernet cipher from password in settings."""
    if not settings.BACKUP_ENCRYPTION_KEY:
        raise ValueError("BACKUP_ENCRYPTION_KEY not set in environment")

    # Derive key from password using SHA256
    key_bytes = hashlib.sha256(settings.BACKUP_ENCRYPTION_KEY.encode()).digest()
    key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(key)


def create_tarball(directory: Path) -> bytes:
    """Create a gzipped tarball of a directory in memory."""
    if not directory.exists():
        logger.warning(f"Directory does not exist: {directory}")
        return b""

    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        tar.add(directory, arcname=directory.name)

    tar_buffer.seek(0)
    return tar_buffer.read()


def sync_unencrypted_directory(path: Path) -> dict:
    """Sync an unencrypted directory to S3 using aws s3 sync."""
    if not path.exists():
        logger.warning(f"Directory does not exist: {path}")
        return {"synced": False, "reason": "directory_not_found"}

    s3_uri = f"s3://{settings.S3_BACKUP_BUCKET}/{settings.S3_BACKUP_PREFIX}/{path.name}"

    cmd = [
        "aws",
        "s3",
        "sync",
        str(path),
        s3_uri,
        "--delete",
        "--region",
        settings.S3_BACKUP_REGION,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        logger.info(f"Synced {path} to {s3_uri}")
        logger.debug(f"Output: {result.stdout}")
        return {"synced": True, "directory": path, "s3_uri": s3_uri}
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to sync {path}: {e.stderr}")
        return {"synced": False, "directory": path, "error": str(e)}


def backup_encrypted_directory(path: Path) -> dict:
    """Create encrypted tarball of directory and upload to S3."""
    if not path.exists():
        logger.warning(f"Directory does not exist: {path}")
        return {"uploaded": False, "reason": "directory_not_found"}

    # Create tarball
    logger.info(f"Creating tarball of {path}...")
    tarball_bytes = create_tarball(path)

    if not tarball_bytes:
        logger.warning(f"Empty tarball for {path}, skipping")
        return {"uploaded": False, "reason": "empty_directory"}

    # Encrypt
    logger.info(f"Encrypting {path} ({len(tarball_bytes)} bytes)...")
    cipher = get_cipher()
    encrypted_bytes = cipher.encrypt(tarball_bytes)

    # Upload to S3
    s3_client = boto3.client("s3", region_name=settings.S3_BACKUP_REGION)
    s3_key = f"{settings.S3_BACKUP_PREFIX}/{path.name}.tar.gz.enc"

    try:
        logger.info(
            f"Uploading encrypted {path} to s3://{settings.S3_BACKUP_BUCKET}/{s3_key}"
        )
        s3_client.put_object(
            Bucket=settings.S3_BACKUP_BUCKET,
            Key=s3_key,
            Body=encrypted_bytes,
            ServerSideEncryption="AES256",
        )
        return {
            "uploaded": True,
            "directory": path,
            "size_bytes": len(encrypted_bytes),
            "s3_key": s3_key,
        }
    except Exception as e:
        logger.error(f"Failed to upload {path}: {e}")
        return {"uploaded": False, "directory": path, "error": str(e)}


@app.task(name=BACKUP_PATH)
def backup_to_s3(path: Path | str):
    """Backup a specific directory to S3."""
    path = Path(path)

    if not path.exists():
        logger.warning(f"Directory does not exist: {path}")
        return {"uploaded": False, "reason": "directory_not_found"}

    if path in settings.PRIVATE_DIRS:
        return backup_encrypted_directory(path)
    return sync_unencrypted_directory(path)


@app.task(name=BACKUP_ALL)
def backup_all_to_s3():
    """Main backup task that syncs unencrypted dirs and uploads encrypted dirs.

    Uses a distributed lock to prevent concurrent backup operations.
    """
    if not settings.S3_BACKUP_ENABLED:
        logger.info("S3 backup is disabled")
        return {"status": "disabled"}

    try:
        with backup_lock():
            logger.info("Starting S3 backup...")

            for dir_name in settings.storage_dirs:
                backup_to_s3.delay((settings.FILE_STORAGE_DIR / dir_name).as_posix())

            return {
                "status": "success",
                "message": f"Started backup for {len(settings.storage_dirs)} directories",
            }
    except RuntimeError as e:
        logger.warning(str(e))
        return {"status": "skipped", "reason": str(e)}
