"""S3 backup tasks for memory files."""

import base64
import hashlib
import logging
import os
import subprocess
import tarfile
import tempfile
import uuid
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

    Uses a unique lock value to prevent releasing another process's lock
    if this lock expires during a long-running operation.
    """
    redis_client = redis.from_url(settings.REDIS_URL)
    lock_key = f"memory:lock:{lock_name}"
    lock_value = str(uuid.uuid4())

    # Try to acquire lock with NX (only if not exists) and expiry
    acquired = redis_client.set(lock_key, lock_value, nx=True, ex=BACKUP_LOCK_TIMEOUT)
    if not acquired:
        raise RuntimeError(f"Could not acquire backup lock '{lock_name}' - backup already in progress")

    try:
        yield
    finally:
        # Only release the lock if we still own it (atomic check-and-delete)
        # This prevents deleting another process's lock if ours expired
        release_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        redis_client.eval(release_script, 1, lock_key, lock_value)


def get_cipher() -> Fernet:
    """Create Fernet cipher from password in settings."""
    if not settings.BACKUP_ENCRYPTION_KEY:
        raise ValueError("BACKUP_ENCRYPTION_KEY not set in environment")

    # Derive key from password using SHA256
    key_bytes = hashlib.sha256(settings.BACKUP_ENCRYPTION_KEY.encode()).digest()
    key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(key)


@contextmanager
def create_tarball_file(directory: Path):
    """Create a gzipped tarball of a directory using a temp file.

    Uses a temp file instead of memory to avoid OOM on large directories.
    Yields the path to the temp file, which is cleaned up after use.
    """
    if not directory.exists():
        logger.warning(f"Directory does not exist: {directory}")
        yield None
        return

    # Create temp file for tarball
    fd, tar_path = tempfile.mkstemp(suffix=".tar.gz")
    try:
        os.close(fd)  # Close the fd, we'll open with tarfile
        with tarfile.open(tar_path, mode="w:gz") as tar:
            tar.add(directory, arcname=directory.name)
        yield Path(tar_path)
    finally:
        # Clean up temp file
        if os.path.exists(tar_path):
            os.unlink(tar_path)


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
            timeout=3600,  # 1 hour timeout for large syncs
        )
        logger.info(f"Synced {path} to {s3_uri}")
        logger.debug(f"Output: {result.stdout}")
        return {"synced": True, "directory": path, "s3_uri": s3_uri}
    except subprocess.TimeoutExpired as e:
        logger.error(f"Sync timed out for {path}: {e}")
        return {"synced": False, "directory": path, "error": "timeout"}
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to sync {path}: {e.stderr}")
        return {"synced": False, "directory": path, "error": str(e)}


def backup_encrypted_directory(path: Path) -> dict:
    """Create encrypted tarball of directory and upload to S3.

    Uses streaming via temp files to avoid loading large directories into memory.
    """
    if not path.exists():
        logger.warning(f"Directory does not exist: {path}")
        return {"uploaded": False, "reason": "directory_not_found"}

    # Create tarball using temp file (avoids OOM on large directories)
    with create_tarball_file(path) as tar_path:
        if tar_path is None:
            logger.warning(f"Empty tarball for {path}, skipping")
            return {"uploaded": False, "reason": "empty_directory"}

        tar_size = tar_path.stat().st_size
        logger.info(f"Created tarball of {path} ({tar_size} bytes)")

        # Read, encrypt, and write to another temp file
        logger.info(f"Encrypting {path}...")
        cipher = get_cipher()

        # For Fernet, we still need to load into memory for encryption
        # but at least we're not holding both tarball AND encrypted in memory
        tarball_bytes = tar_path.read_bytes()

    # Encrypt (tarball_bytes freed after this)
    encrypted_bytes = cipher.encrypt(tarball_bytes)
    del tarball_bytes  # Free memory before upload

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

            results = []
            for dir_name in settings.storage_dirs:
                # Run synchronously within lock to prevent concurrent backups
                result = backup_to_s3((settings.FILE_STORAGE_DIR / dir_name).as_posix())
                results.append({"dir": dir_name, **result})

            return {
                "status": "success",
                "directories": len(results),
                "results": results,
            }
    except RuntimeError as e:
        logger.warning(str(e))
        return {"status": "skipped", "reason": str(e)}
