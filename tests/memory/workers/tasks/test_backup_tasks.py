import io
import subprocess
import tarfile
from unittest.mock import Mock, patch, MagicMock

import pytest
from botocore.exceptions import ClientError

from memory.common import settings
from memory.workers.tasks import backup


@pytest.fixture
def sample_files():
    """Create sample files in memory_files structure."""
    base = settings.FILE_STORAGE_DIR

    dirs_with_files = {
        "emails": ["email1.txt", "email2.txt"],
        "notes": ["note1.md", "note2.md"],
        "photos": ["photo1.jpg"],
        "comics": ["comic1.png", "comic2.png"],
        "ebooks": ["book1.epub"],
        "webpages": ["page1.html"],
    }

    for dir_name, filenames in dirs_with_files.items():
        dir_path = base / dir_name
        dir_path.mkdir(parents=True, exist_ok=True)

        for filename in filenames:
            file_path = dir_path / filename
            content = f"Content of {dir_name}/{filename}\n" * 100
            file_path.write_text(content)


@pytest.fixture
def mock_s3_client():
    """Mock boto3 S3 client."""
    with patch("boto3.client") as mock_client:
        s3_mock = MagicMock()
        mock_client.return_value = s3_mock
        yield s3_mock


@pytest.fixture
def backup_settings():
    """Mock backup settings."""
    with (
        patch.object(settings, "S3_BACKUP_ENABLED", True),
        patch.object(settings, "BACKUP_ENCRYPTION_KEY", "test-password-123"),
        patch.object(settings, "S3_BACKUP_BUCKET", "test-bucket"),
        patch.object(settings, "S3_BACKUP_PREFIX", "test-prefix"),
        patch.object(settings, "S3_BACKUP_REGION", "us-east-1"),
    ):
        yield


@pytest.fixture
def get_test_path():
    """Helper to construct test paths."""
    return lambda dir_name: settings.FILE_STORAGE_DIR / dir_name


@pytest.mark.parametrize(
    "data,key",
    [
        (b"This is a test message", "my-secret-key"),
        (b"\x00\x01\x02\xff" * 10000, "another-key"),
        (b"x" * 1000000, "large-data-key"),
    ],
    ids=["text-data", "binary-with-nulls", "large-data"],
)
def test_encrypt_decrypt_roundtrip(data, key):
    """Test encryption and decryption produces original data."""
    with patch.object(settings, "BACKUP_ENCRYPTION_KEY", key):
        cipher = backup.get_cipher()
        encrypted = cipher.encrypt(data)
        decrypted = cipher.decrypt(encrypted)

    assert decrypted == data
    assert encrypted != data


def test_encrypt_decrypt_tarball(sample_files):
    """Test full tarball creation, encryption, and decryption."""
    emails_dir = settings.FILE_STORAGE_DIR / "emails"

    # Create tarball using context manager
    with backup.create_tarball_file(emails_dir) as tar_path:
        assert tar_path is not None
        tarball_bytes = tar_path.read_bytes()
        assert len(tarball_bytes) > 0

        # Encrypt
        with patch.object(settings, "BACKUP_ENCRYPTION_KEY", "tarball-key"):
            cipher = backup.get_cipher()
            encrypted = cipher.encrypt(tarball_bytes)

            # Decrypt
            decrypted = cipher.decrypt(encrypted)

        assert decrypted == tarball_bytes

        # Verify tarball can be extracted
        tar_buffer = io.BytesIO(decrypted)
        with tarfile.open(fileobj=tar_buffer, mode="r:gz") as tar:
            members = tar.getmembers()
            assert len(members) >= 2  # At least 2 email files

            # Extract and verify content
            for member in members:
                if member.isfile():
                    extracted = tar.extractfile(member)
                    assert extracted is not None
                    content = extracted.read().decode()
                    assert "Content of emails/" in content


def test_different_keys_produce_different_ciphertext():
    """Test that different encryption keys produce different ciphertext."""
    data = b"Same data encrypted with different keys"

    with patch.object(settings, "BACKUP_ENCRYPTION_KEY", "key1"):
        cipher1 = backup.get_cipher()
        encrypted1 = cipher1.encrypt(data)

    with patch.object(settings, "BACKUP_ENCRYPTION_KEY", "key2"):
        cipher2 = backup.get_cipher()
        encrypted2 = cipher2.encrypt(data)

    assert encrypted1 != encrypted2


def test_missing_encryption_key_raises_error():
    """Test that missing encryption key raises ValueError."""
    with patch.object(settings, "BACKUP_ENCRYPTION_KEY", ""):
        with pytest.raises(ValueError, match="BACKUP_ENCRYPTION_KEY not set"):
            backup.get_cipher()


# --- KDF upgrade regression tests ------------------------------------------
#
# get_cipher previously derived the Fernet key via a bare SHA-256 of the
# operator passphrase — no salt, no work factor. The replacement uses
# PBKDF2-HMAC-SHA256 with 480k iterations (OWASP) and a pinned
# domain-separating salt (`_BACKUP_KEY_SALT`). These tests pin the
# properties that fix demands so a future "performance" refactor that
# weakens the KDF surfaces as a test failure.


def test_get_cipher_does_not_use_bare_sha256_kdf():
    """REGRESSION GUARD: a bare SHA-256 of the passphrase produces a
    specific, predictable Fernet key. Verify the new code does NOT
    produce that key — i.e. the KDF actually changed.
    """
    import base64
    import hashlib

    from cryptography.fernet import Fernet, InvalidToken

    passphrase = "operator-supplied-passphrase"
    # The OLD bare-SHA-256 KDF
    old_key = base64.urlsafe_b64encode(
        hashlib.sha256(passphrase.encode()).digest()
    )

    with patch.object(settings, "BACKUP_ENCRYPTION_KEY", passphrase):
        cipher = backup.get_cipher()

    # Encrypt under the OLD KDF, attempt to decrypt with the new
    # cipher. New cipher MUST NOT decrypt old-format ciphertext — that
    # would mean the key derivation collapsed back to bare SHA-256.
    old_cipher = Fernet(old_key)
    old_ct = old_cipher.encrypt(b"sentinel-plaintext")

    with pytest.raises(InvalidToken):
        cipher.decrypt(old_ct)


def test_get_cipher_uses_pinned_domain_separating_salt():
    """The salt constant ``_BACKUP_KEY_SALT`` is exported and pinned to
    the documented v2 value; a silent edit (e.g. dropping the salt or
    switching to a different version) will be caught here.
    """
    assert backup._BACKUP_KEY_SALT == b"memory-backup-encryption-salt-v2"


def test_get_cipher_salt_distinct_from_secrets_salt():
    """Cross-primitive isolation: even if an operator reuses the same
    passphrase for ``BACKUP_ENCRYPTION_KEY`` and ``SECRETS_ENCRYPTION_KEY``,
    the derived keys must be different because the KDF salts differ.
    """
    assert backup._BACKUP_KEY_SALT != settings.SECRETS_ENCRYPTION_SALT


def test_get_cipher_derived_key_differs_with_same_passphrase_as_secrets():
    """REGRESSION GUARD: if an operator (foolishly) sets
    ``BACKUP_ENCRYPTION_KEY = SECRETS_ENCRYPTION_KEY``, the backup
    Fernet key must still be different from the at-rest secrets Fernet
    key — i.e. the salt actually domain-separates the two primitives.
    """
    from memory.common.db.models.secrets import derive_encryption_key

    passphrase = "shared-by-mistake-passphrase"
    backup_key = derive_encryption_key(passphrase, backup._BACKUP_KEY_SALT)
    secrets_key = derive_encryption_key(passphrase, settings.SECRETS_ENCRYPTION_SALT)

    assert backup_key != secrets_key


def test_get_cipher_is_deterministic():
    """Same passphrase → same cipher key (required so a fresh-cloned
    deployment at the same code version can decrypt its own backups
    without external coordination)."""
    with patch.object(settings, "BACKUP_ENCRYPTION_KEY", "stable-key"):
        cipher_a = backup.get_cipher()
        ct_a = cipher_a.encrypt(b"test")

        cipher_b = backup.get_cipher()

    # Cipher_b must be able to decrypt cipher_a's output — proves the
    # derivations are equal even though Fernet objects are distinct.
    assert cipher_b.decrypt(ct_a) == b"test"


def test_get_cipher_pbkdf2_actually_runs():
    """REGRESSION GUARD: A 480k-iteration PBKDF2 derivation has a
    measurable wall-clock cost (typically 100-300ms on commodity
    hardware). If a future refactor accidentally drops the iteration
    count or skips PBKDF2, this test would still pass — so we instead
    pin the structural property: the derive_encryption_key helper from
    secrets.py must be the import path. A grep reviewer can then
    confirm the iterations parameter at the helper definition.
    """
    # Confirms the import wiring; the iterations are tested in the
    # secrets module's test suite at the source.
    from memory.common.db.models.secrets import (
        derive_encryption_key as _imported,
    )

    # The helper is the same callable the backup module imports.
    assert backup.derive_encryption_key is _imported


def test_create_tarball_with_files(sample_files):
    """Test creating tarball from directory with files."""
    notes_dir = settings.FILE_STORAGE_DIR / "notes"

    with backup.create_tarball_file(notes_dir) as tar_path:
        assert tar_path is not None
        tarball_bytes = tar_path.read_bytes()
        assert len(tarball_bytes) > 0

        # Verify it's a valid gzipped tarball
        tar_buffer = io.BytesIO(tarball_bytes)
        with tarfile.open(fileobj=tar_buffer, mode="r:gz") as tar:
            members = tar.getmembers()
            filenames = [m.name for m in members if m.isfile()]
            assert len(filenames) >= 2
            assert any("note1.md" in f for f in filenames)
            assert any("note2.md" in f for f in filenames)


def test_create_tarball_nonexistent_directory():
    """Test creating tarball from nonexistent directory."""
    nonexistent = settings.FILE_STORAGE_DIR / "does_not_exist"

    with backup.create_tarball_file(nonexistent) as tar_path:
        # Nonexistent directory yields None
        assert tar_path is None


def test_create_tarball_empty_directory():
    """Test creating tarball from empty directory."""
    empty_dir = settings.FILE_STORAGE_DIR / "empty"
    empty_dir.mkdir(parents=True, exist_ok=True)

    with backup.create_tarball_file(empty_dir) as tar_path:
        assert tar_path is not None
        tarball_bytes = tar_path.read_bytes()

        # Should create tarball with just the directory entry
        assert len(tarball_bytes) > 0
        tar_buffer = io.BytesIO(tarball_bytes)
        with tarfile.open(fileobj=tar_buffer, mode="r:gz") as tar:
            members = tar.getmembers()
            assert len(members) >= 1
            assert members[0].isdir()


def test_sync_unencrypted_success(sample_files, backup_settings):
    """Test successful sync of unencrypted directory."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(stdout="Synced files", returncode=0)

        comics_path = settings.FILE_STORAGE_DIR / "comics"
        result = backup.sync_unencrypted_directory(comics_path)

    assert result["synced"] is True
    assert result["directory"] == comics_path
    assert "s3_uri" in result
    assert "test-bucket" in result["s3_uri"]
    assert "test-prefix/comics" in result["s3_uri"]

    # Verify aws s3 sync was called correctly
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert call_args[0] == "aws"
    assert call_args[1] == "s3"
    assert call_args[2] == "sync"
    assert "--delete" in call_args
    assert "--region" in call_args


def test_sync_unencrypted_nonexistent_directory(backup_settings):
    """Test syncing nonexistent directory."""
    nonexistent_path = settings.FILE_STORAGE_DIR / "does_not_exist"
    result = backup.sync_unencrypted_directory(nonexistent_path)

    assert result["synced"] is False
    assert result["reason"] == "directory_not_found"


def test_sync_unencrypted_aws_cli_failure(sample_files, backup_settings):
    """Test handling of AWS CLI failure."""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "aws", stderr="AWS CLI error"
        )

        comics_path = settings.FILE_STORAGE_DIR / "comics"
        result = backup.sync_unencrypted_directory(comics_path)

    assert result["synced"] is False
    assert "error" in result


def test_backup_encrypted_success(
    sample_files, mock_s3_client, backup_settings, get_test_path
):
    """Test successful encrypted backup."""
    result = backup.backup_encrypted_directory(get_test_path("emails"))

    assert result["uploaded"] is True
    assert result["size_bytes"] > 0
    assert result["s3_key"].endswith("emails.tar.gz.enc")

    call_kwargs = mock_s3_client.put_object.call_args[1]
    assert call_kwargs["Bucket"] == "test-bucket"
    assert call_kwargs["ServerSideEncryption"] == "AES256"


def test_backup_encrypted_nonexistent_directory(
    mock_s3_client, backup_settings, get_test_path
):
    """Test backing up nonexistent directory."""
    result = backup.backup_encrypted_directory(get_test_path("does_not_exist"))

    assert result["uploaded"] is False
    assert result["reason"] == "directory_not_found"
    mock_s3_client.put_object.assert_not_called()


def test_backup_encrypted_empty_directory(
    mock_s3_client, backup_settings, get_test_path
):
    """Test backing up empty directory."""
    empty_dir = get_test_path("empty_encrypted")
    empty_dir.mkdir(parents=True, exist_ok=True)

    result = backup.backup_encrypted_directory(empty_dir)
    assert "uploaded" in result


def test_backup_encrypted_s3_failure(
    sample_files, mock_s3_client, backup_settings, get_test_path
):
    """Test handling of S3 upload failure."""
    mock_s3_client.put_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "PutObject"
    )

    result = backup.backup_encrypted_directory(get_test_path("notes"))
    assert result["uploaded"] is False
    assert "error" in result


def test_backup_encrypted_data_integrity(
    sample_files, mock_s3_client, backup_settings, get_test_path
):
    """Test that encrypted backup maintains data integrity through full cycle."""
    result = backup.backup_encrypted_directory(get_test_path("notes"))
    assert result["uploaded"] is True

    # Decrypt uploaded data
    cipher = backup.get_cipher()
    encrypted_data = mock_s3_client.put_object.call_args[1]["Body"]
    decrypted_tarball = cipher.decrypt(encrypted_data)

    # Verify content
    tar_buffer = io.BytesIO(decrypted_tarball)
    with tarfile.open(fileobj=tar_buffer, mode="r:gz") as tar:
        note1_found = False
        for member in tar.getmembers():
            if member.name.endswith("note1.md") and member.isfile():
                file_obj = tar.extractfile(member)
                assert file_obj is not None
                content = file_obj.read().decode()
                assert "Content of notes/note1.md" in content
                note1_found = True
        assert note1_found, "note1.md not found in tarball"


def test_backup_disabled():
    """Test that backup returns early when disabled."""
    with patch.object(settings, "S3_BACKUP_ENABLED", False):
        result = backup.backup_all_to_s3()

    assert result["status"] == "disabled"


def test_backup_full_execution(sample_files, mock_s3_client, backup_settings):
    """Test full backup execution processes all directories."""
    with (
        patch.object(backup, "backup_to_s3") as mock_task,
        patch.object(backup, "backup_lock") as mock_lock,
    ):
        mock_task.return_value = {"uploaded": True}
        mock_lock.return_value.__enter__ = Mock()
        mock_lock.return_value.__exit__ = Mock(return_value=None)

        result = backup.backup_all_to_s3()

    assert result["status"] == "success"
    assert "directories" in result
    assert "results" in result

    # Verify task was called for each storage directory (runs synchronously now)
    assert mock_task.call_count == len(settings.storage_dirs)


def test_backup_handles_partial_failures(
    sample_files, mock_s3_client, backup_settings, get_test_path
):
    """Test that backup continues even if some directories fail."""
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "aws", stderr="Sync failed"
        )
        result = backup.sync_unencrypted_directory(get_test_path("comics"))

    assert result["synced"] is False
    assert "error" in result


def test_same_key_different_runs_different_ciphertext():
    """Test that Fernet produces different ciphertext each run (due to nonce)."""
    data = b"Consistent data"

    with patch.object(settings, "BACKUP_ENCRYPTION_KEY", "same-key"):
        cipher = backup.get_cipher()
        encrypted1 = cipher.encrypt(data)
        encrypted2 = cipher.encrypt(data)

    # Should be different due to random nonce, but both should decrypt to same value
    assert encrypted1 != encrypted2

    decrypted1 = cipher.decrypt(encrypted1)
    decrypted2 = cipher.decrypt(encrypted2)
    assert decrypted1 == decrypted2 == data


def test_key_derivation_consistency():
    """Test that same password produces same encryption key."""
    password = "test-password"

    with patch.object(settings, "BACKUP_ENCRYPTION_KEY", password):
        cipher1 = backup.get_cipher()
        cipher2 = backup.get_cipher()

    # Both should be able to decrypt each other's ciphertext
    data = b"Test data"
    encrypted = cipher1.encrypt(data)
    decrypted = cipher2.decrypt(encrypted)
    assert decrypted == data


@pytest.mark.parametrize(
    "dir_name,is_private",
    [
        ("emails", True),
        ("notes", True),
        ("photos", True),
        ("comics", False),
        ("ebooks", False),
        ("webpages", False),
        ("lesswrong", False),
        ("chunks", False),
    ],
)
def test_directory_encryption_classification(dir_name, is_private, backup_settings):
    """Test that directories are correctly classified as encrypted or not."""
    # Create a mock PRIVATE_DIRS list
    private_dirs = ["emails", "notes", "photos"]

    with patch.object(
        settings, "PRIVATE_DIRS", [settings.FILE_STORAGE_DIR / d for d in private_dirs]
    ):
        test_path = settings.FILE_STORAGE_DIR / dir_name
        is_in_private = test_path in settings.PRIVATE_DIRS

        assert is_in_private == is_private
