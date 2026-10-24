import pytest
from cryptography.fernet import InvalidToken
from sqlalchemy import LargeBinary

from memory.common import settings
from memory.common.db.models import Base, Secret
from memory.common.db.models.secrets import (
    create_secret,
    decrypt_value_with_key,
    encrypt_value_with_key,
    derive_encryption_key,
    encrypted_columns,
    is_encrypted_column,
    rotate_all_secrets,
)

# Binary columns that are not ciphertext. Anything else of type LargeBinary must
# be named like an encrypted column so rotation picks it up.
PLAINTEXT_BINARY_COLUMNS = {
    ("agent_trigger_matches", "content_hash"),
    ("source_item", "sha256"),
    ("github_pr_data", "diff_compressed"),
}


def test_every_binary_column_is_rotated_or_declared_plaintext():
    binary = {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, LargeBinary)
    }
    rotated = {
        (table.name, column.name)
        for table, columns in encrypted_columns().items()
        for column in columns
    }

    assert binary - rotated == PLAINTEXT_BINARY_COLUMNS
    assert not any(
        is_encrypted_column(c)
        for t in Base.metadata.tables.values()
        for c in t.columns
        if (t.name, c.name) in PLAINTEXT_BINARY_COLUMNS
    )


def test_rotation_reencrypts_secrets_and_user_keys(db_session, regular_user):
    old, new = "old-master-key", "new-master-key"
    salt = settings.SECRETS_ENCRYPTION_SALT
    old_key, new_key = derive_encryption_key(old, salt), derive_encryption_key(new, salt)
    secret = create_secret(db_session, regular_user.id, "tok", "x")
    secret.encrypted_value = encrypt_value_with_key("s3cret", old_key)
    regular_user.ssh_private_key_encrypted = encrypt_value_with_key("ssh-key", old_key)
    db_session.flush()

    rotated = rotate_all_secrets(db_session, old, new)
    db_session.expire_all()

    assert rotated >= 2
    assert decrypt_value_with_key(db_session.get(Secret, secret.id).encrypted_value, new_key) == "s3cret"
    assert decrypt_value_with_key(regular_user.ssh_private_key_encrypted, new_key) == "ssh-key"


def test_rotation_with_wrong_old_key_fails(db_session, regular_user):
    create_secret(db_session, regular_user.id, "tok", "s3cret")
    db_session.flush()

    with pytest.raises(InvalidToken):
        rotate_all_secrets(db_session, "not-the-key", "new-master-key")
