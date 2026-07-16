"""S3 backup tasks for memory files."""

import logging
import os
import subprocess
import tarfile
import tempfile
import uuid
from contextlib import contextmanager, ExitStack
from pathlib import Path

import boto3
import redis
from cryptography.fernet import Fernet

from memory.common import settings
from memory.common.celery_app import app, BACKUP_PATH, BACKUP_ALL
from memory.common.db.models.secrets import derive_encryption_key
from memory.common.jobs import tracked_task

logger = logging.getLogger(__name__)

# Backup lock timeout (30 minutes max for backup to complete)
BACKUP_LOCK_TIMEOUT = 30 * 60


class BackupError(Exception):
    """Raised when a backup could not be performed (fully or partially).

    Deliberately *not* a subclass of ``RuntimeError``: the lock-contention
    path in ``backup_all_to_s3`` catches ``RuntimeError`` and downgrades it
    to a benign "skipped", and a real backup failure must not be swallowed
    that way.

    We raise rather than return a ``{"status": "failed"}`` dict because
    ``@tracked_task`` marks the PendingJob *complete* on any normal return.
    A returned failure status would therefore still be recorded as a
    successful job — which is exactly the silent-outage bug this fixes.
    Raising routes through failure metrics, task-failure notifications, and
    ``_mark_job_failed`` so every layer (logs, metrics, pending_jobs,
    notifications) reflects that the backup did not happen.
    """


def aws_credentials_available() -> bool:
    """Whether boto3 can resolve AWS credentials in the worker environment.

    ``S3_BACKUP_ENABLED`` is inferred purely from ``BACKUP_ENCRYPTION_KEY``
    being set — it says nothing about whether AWS credentials are actually
    present. Without this preflight a worker with a valid encryption key but
    no credentials uploads nothing on every run while reporting success.
    Resolving credentials up front turns that silent outage into an
    immediate, loud failure.
    """
    try:
        return boto3.Session().get_credentials() is not None
    except Exception:
        # Credential probing (env vars, shared config, instance metadata)
        # should not raise, but a broken config must fail closed.
        logger.warning("AWS credential resolution raised", exc_info=True)
        return False


def _dir_succeeded(result: dict) -> bool:
    """A per-directory result counts as a success if it uploaded or synced."""
    return bool(result.get("uploaded") or result.get("synced"))


# Reasons that mark a per-directory result as a benign skip rather than a
# failure: the directory simply had nothing to back up.
_BENIGN_SKIP_REASONS = ("directory_not_found", "empty_directory")


def _dir_failed(result: dict) -> bool:
    """A per-directory result is a failure unless it succeeded or benignly skipped.

    Fail-closed: anything that neither succeeded (``uploaded``/``synced``) nor
    carries a recognized benign ``reason`` (``directory_not_found``,
    ``empty_directory``) is a hard failure — including a malformed or empty
    result dict. This keeps the fail-loudly contract from depending on the
    exact return shapes of helpers two layers away; an unrecognized result
    can never silently drop out of both the failed and succeeded buckets.
    """
    if _dir_succeeded(result):
        return False
    return result.get("reason") not in _BENIGN_SKIP_REASONS


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


# Domain-separating salt for the small-file Fernet backup KDF.
#
# Pinned as a module-level constant so backup decryption is stable across
# code restarts and so a fresh-cloned repo at the same version can decrypt
# its own backups. Distinct from ``SECRETS_ENCRYPTION_SALT`` so backup
# ciphertext cannot be brute-forced in tandem with at-rest secrets
# ciphertext under a shared passphrase.
#
# The version suffix is the KDF format identifier. Bumping it invalidates
# all existing backups; operators must keep the matching code revision
# available to decrypt archives at the prior version.
_BACKUP_KEY_SALT = b"memory-backup-encryption-salt-v2"


def get_cipher() -> Fernet:
    """Create Fernet cipher from ``BACKUP_ENCRYPTION_KEY`` via PBKDF2.

    Routes through ``derive_encryption_key`` (PBKDF2-HMAC-SHA256 with
    480k iterations, OWASP-recommended) so the work factor matches the
    rest of the secrets infrastructure. The pinned ``_BACKUP_KEY_SALT``
    is distinct from ``SECRETS_ENCRYPTION_SALT`` so a leaked backup
    ciphertext cannot be brute-forced in tandem with a leaked at-rest
    secrets ciphertext — even when an operator reuses the same
    passphrase for both.

    The ``v2`` suffix in ``_BACKUP_KEY_SALT`` is the format version.
    Operators restoring archives from before this version must use the
    matching code revision; the ciphertext format is identical, only
    the key derivation changes across version bumps.
    """
    if not settings.BACKUP_ENCRYPTION_KEY:
        raise ValueError("BACKUP_ENCRYPTION_KEY not set in environment")
    key = derive_encryption_key(settings.BACKUP_ENCRYPTION_KEY, _BACKUP_KEY_SALT)
    return Fernet(key)


# Maximum size for in-memory Fernet encryption (100MB)
# Larger files use streaming openssl encryption to avoid OOM
MAX_FERNET_SIZE = 100 * 1024 * 1024


def encrypt_file_streaming(input_path: Path, output_path: Path) -> None:
    """Encrypt a file using openssl for streaming (avoids loading into memory).

    Uses AES-256-CBC with PBKDF2 key derivation, compatible with:
        openssl enc -d -aes-256-cbc -pbkdf2 -in file.enc -out file -pass env:BACKUP_ENCRYPTION_KEY

    Security note: The encryption key is passed via environment variable rather than
    command line to avoid exposure in `ps aux`. However, processes with root access
    or same UID can still read it via /proc/<pid>/environ during subprocess execution.
    For higher security requirements, consider using openssl's -pass file: option
    with a temporary file (mode 0600) that's deleted after the subprocess starts.
    """
    if not settings.BACKUP_ENCRYPTION_KEY:
        raise ValueError("BACKUP_ENCRYPTION_KEY not set in environment")

    # Use openssl for streaming encryption - doesn't load entire file into memory
    # Pass key via environment variable (not command line) to avoid exposure in ps aux
    # Use minimal environment to avoid leaking other secrets to subprocess
    env = {
        "PATH": os.environ.get("PATH", ""),
        "BACKUP_ENCRYPTION_KEY": settings.BACKUP_ENCRYPTION_KEY,
    }
    result = subprocess.run(
        [
            "openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt",
            "-in", str(input_path),
            "-out", str(output_path),
            "-pass", "env:BACKUP_ENCRYPTION_KEY",
        ],
        capture_output=True,
        timeout=3600,  # 1 hour timeout for large files
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"openssl encryption failed: {result.stderr.decode()}")


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
        # Include stderr in the returned error so the failure surfaced to the
        # operator (via BackupError) carries the actual S3/AWS reason, not just
        # the bare non-zero-exit message.
        error = str(e)
        if e.stderr:
            error = f"{error}: {e.stderr.strip()}"
        return {"synced": False, "directory": path, "error": error}


def backup_encrypted_directory(path: Path) -> dict:
    """Create encrypted tarball of directory and upload to S3.

    Uses streaming encryption for large files to avoid OOM.
    Small files (<100MB) use Fernet, large files use openssl streaming.
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

        logger.info(f"Encrypting {path}...")

        s3_client = boto3.client("s3", region_name=settings.S3_BACKUP_REGION)

        # Use streaming encryption for large files to avoid OOM
        if tar_size > MAX_FERNET_SIZE:
            logger.info(f"Using streaming encryption for large file ({tar_size} bytes)")
            # Create temp file for encrypted output
            enc_fd, enc_path = tempfile.mkstemp(suffix=".enc")
            os.close(enc_fd)
            try:
                encrypt_file_streaming(tar_path, Path(enc_path))
                enc_size = os.path.getsize(enc_path)

                # Use upload_file for streaming upload (doesn't load into memory)
                s3_key = f"{settings.S3_BACKUP_PREFIX}/{path.name}.tar.gz.ssl"
                logger.info(
                    f"Uploading encrypted {path} to s3://{settings.S3_BACKUP_BUCKET}/{s3_key}"
                )
                s3_client.upload_file(
                    enc_path,
                    settings.S3_BACKUP_BUCKET,
                    s3_key,
                    ExtraArgs={"ServerSideEncryption": "AES256"},
                )
                return {
                    "uploaded": True,
                    "directory": path,
                    "size_bytes": enc_size,
                    "s3_key": s3_key,
                    "encryption": "openssl",
                }
            except Exception as e:
                logger.error(f"Failed to upload {path}: {e}")
                return {"uploaded": False, "directory": path, "error": str(e)}
            finally:
                if os.path.exists(enc_path):
                    os.unlink(enc_path)
        else:
            # Small files (<MAX_FERNET_SIZE): use Fernet with the v2 KDF
            # (PBKDF2-HMAC-SHA256 + 480k iterations + pinned salt — see
            # ``get_cipher`` docstring for the salt-versioning contract
            # and operator migration notes).
            cipher = get_cipher()
            tarball_bytes = tar_path.read_bytes()
            encrypted_bytes = cipher.encrypt(tarball_bytes)
            del tarball_bytes

    # Upload small files using put_object (already in memory)
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
            "encryption": "fernet",
        }
    except Exception as e:
        logger.error(f"Failed to upload {path}: {e}")
        return {"uploaded": False, "directory": path, "error": str(e)}


@app.task(name=BACKUP_PATH)
@tracked_task
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
@tracked_task
def backup_all_to_s3():
    """Main backup task that syncs unencrypted dirs and uploads encrypted dirs.

    Uses a distributed lock to prevent concurrent backup operations.
    """
    if not settings.S3_BACKUP_ENABLED:
        logger.info("S3 backup is disabled")
        return {"status": "disabled"}

    # Preflight: fail loudly if credentials can't be resolved rather than
    # letting every directory fail one by one with "Unable to locate
    # credentials" while the task still reports success.
    if not aws_credentials_available():
        raise BackupError(
            "S3 backup is enabled (BACKUP_ENCRYPTION_KEY is set) but AWS "
            "credentials could not be resolved. Set AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY (or attach an instance role) in the worker "
            "environment."
        )

    # Acquire the lock in its own try/except so that ONLY lock contention is
    # downgraded to a benign "skipped". The backup work must run outside this
    # handler: a RuntimeError raised by real backup work has to propagate (fail
    # the task), not be misclassified as "skipped" — that would reintroduce the
    # silent-outage bug this task guards against.
    try:
        stack = ExitStack()
        stack.enter_context(backup_lock())
    except RuntimeError as e:
        # backup_lock() raises RuntimeError only when another backup already
        # holds the lock — a benign skip, not a failure.
        logger.warning(str(e))
        return {"status": "skipped", "reason": str(e)}

    # `with stack:` forwards the real exc-info to backup_lock()'s __exit__ if
    # backup work raises, honoring the context-manager protocol, while keeping
    # the lock-acquisition try/except above scoped to lock contention only.
    with stack:
        logger.info("Starting S3 backup...")

        results = []
        for dir_name in settings.storage_dirs:
            # Run synchronously within lock to prevent concurrent backups
            result = backup_to_s3((settings.FILE_STORAGE_DIR / dir_name).as_posix())
            results.append({"dir": dir_name, **result})

    failed = [r for r in results if _dir_failed(r)]
    succeeded = [r for r in results if _dir_succeeded(r)]
    skipped = [r for r in results if not _dir_failed(r) and not _dir_succeeded(r)]

    if failed:
        summary = ", ".join(
            f"{r.get('dir')}: {r.get('error') or 'unrecognized result'}"
            for r in failed
        )
        status = "failed" if not succeeded else "partial"
        logger.error(
            "S3 backup %s — %d failed, %d succeeded, %d skipped of %d: %s",
            status,
            len(failed),
            len(succeeded),
            len(skipped),
            len(results),
            summary,
        )
        raise BackupError(
            f"S3 backup {status}: {len(failed)}/{len(results)} directories "
            f"failed: {summary}"
        )

    logger.info("S3 backup succeeded for all %d directories", len(results))
    return {
        "status": "success",
        "directories": len(results),
        "results": results,
    }
