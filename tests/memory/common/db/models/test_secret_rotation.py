import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import LargeBinary, text

from memory.common import settings
from memory.common.db import rekey
from memory.common.db.models import Base, Secret, SSHKey
from memory.common.db.models.secrets import (
    create_secret,
    derive_encryption_key,
    encrypted_columns,
    is_encrypted_column,
    rekey_all_secrets,
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


def test_rekey_reencrypts_secrets_and_user_keys(db_session, regular_user):
    old, new = "old-master-key", "new-master-key"
    salt = settings.SECRETS_ENCRYPTION_SALT
    old_key, new_key = derive_encryption_key(old, salt), derive_encryption_key(new, salt)
    secret = create_secret(db_session, regular_user.id, "tok", "x")
    secret.encrypted_value = Fernet(old_key).encrypt(b"s3cret")
    ssh_key = SSHKey(user_id=regular_user.id, name="SSH", public_key="public", private_key_encrypted=Fernet(old_key).encrypt(b"ssh-key"))
    db_session.add(ssh_key)
    db_session.flush()

    rotated = rekey_all_secrets(db_session, new, [old])
    db_session.expire_all()

    assert rotated == 2
    assert Fernet(new_key).decrypt(db_session.get(Secret, secret.id).encrypted_value).decode() == "s3cret"
    assert Fernet(new_key).decrypt(ssh_key.private_key_encrypted).decode() == "ssh-key"


def stored_under(db_session, user, key_secret, name="tok"):
    key = derive_encryption_key(key_secret, settings.SECRETS_ENCRYPTION_SALT)
    secret = create_secret(db_session, user.id, name, "x")
    secret.encrypted_value = Fernet(key).encrypt(b"s3cret")
    db_session.flush()
    return secret


def decrypted_with(db_session, secret, key_secret):
    db_session.expire_all()
    key = derive_encryption_key(key_secret, settings.SECRETS_ENCRYPTION_SALT)
    return Fernet(key).decrypt(db_session.get(Secret, secret.id).encrypted_value).decode()


@pytest.mark.parametrize(
    "stored_key, candidates, rekeyed",
    [
        ("old", ["old"], 1),
        ("older", ["old", "older"], 1),
        ("target", ["old"], 0),
        ("target", [], 0),
    ],
)
def test_rekey_moves_secrets_from_whichever_key_they_are_under(
    db_session, regular_user, stored_key, candidates, rekeyed
):
    secret = stored_under(db_session, regular_user, stored_key)

    assert rekey_all_secrets(db_session, "target", candidates) == rekeyed
    assert decrypted_with(db_session, secret, "target") == "s3cret"


def test_rekey_with_nothing_stored_does_nothing(db_session):
    assert rekey_all_secrets(db_session, "target", ["old"]) == 0


def test_rekey_refuses_when_no_key_decrypts(db_session, regular_user):
    secret = stored_under(db_session, regular_user, "lost")

    with pytest.raises(InvalidToken):
        rekey_all_secrets(db_session, "target", ["old"])
    assert decrypted_with(db_session, secret, "lost") == "s3cret"


def test_rekey_moves_each_value_from_its_own_key(db_session, regular_user):
    """A restore that failed part-way, or an interrupted rotation, can leave the values under different keys."""
    under_old = stored_under(db_session, regular_user, "old")
    under_target = stored_under(db_session, regular_user, "target", "tok2")

    assert rekey_all_secrets(db_session, "target", ["old"]) == 1
    assert decrypted_with(db_session, under_old, "target") == "s3cret"
    assert decrypted_with(db_session, under_target, "target") == "s3cret"


def test_rekey_follows_the_database_schema_not_the_models(db_session, regular_user):
    """A restored dump can be older than this code (no llm_provider_keys yet), and a
    rolled-back one can have a column this code no longer declares."""
    secret = stored_under(db_session, regular_user, "old")
    old_key = derive_encryption_key("old", settings.SECRETS_ENCRYPTION_SALT)
    db_session.execute(text("DROP TABLE llm_provider_keys CASCADE"))
    db_session.execute(text("ALTER TABLE secrets ADD COLUMN legacy_encrypted bytea"))
    db_session.execute(
        text("UPDATE secrets SET legacy_encrypted = :v WHERE id = :id"),
        {"v": Fernet(old_key).encrypt(b"legacy"), "id": secret.id},
    )

    assert rekey_all_secrets(db_session, "target", ["old"]) == 2
    legacy = db_session.execute(text("SELECT legacy_encrypted FROM secrets WHERE id = :id"), {"id": secret.id}).scalar()
    target_key = derive_encryption_key("target", settings.SECRETS_ENCRYPTION_SALT)
    assert Fernet(target_key).decrypt(bytes(legacy)).decode() == "legacy"


@pytest.mark.parametrize(
    "keys, code, out, stored_key",
    [("unrelated\nold", 0, "secrets_reencrypted=1\n", "target"), ("unrelated", 1, "", "old")],
)
def test_rekey_entry_point_takes_keys_from_the_environment_and_prints_only_a_count(
    db_session, regular_user, monkeypatch, capsys, keys, code, out, stored_key
):
    secret = stored_under(db_session, regular_user, "old")
    monkeypatch.setenv("ROTATE_SECRETS_TO", "target")
    monkeypatch.setenv("ROTATE_SECRETS_FROM", keys)

    assert rekey.main() == code
    assert capsys.readouterr().out == out
    assert decrypted_with(db_session, secret, stored_key) == "s3cret"
