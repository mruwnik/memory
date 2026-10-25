"""Exercise both directions using an isolated pre-migration schema."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def test_existing_ciphertext_and_default_survive_upgrade(db_session):
    path = Path(__file__).parents[4] / "db/migrations/versions/20261108_ssh_keys.py"
    spec = importlib.util.spec_from_file_location("ssh_keys_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA ssh_migration_test"))
    db.execute(text("SET LOCAL search_path TO ssh_migration_test"))
    db.execute(
        text(
            "CREATE TABLE users (id integer PRIMARY KEY, ssh_public_key text, ssh_private_key_encrypted bytea)"
        )
    )
    db.execute(
        text(
            "INSERT INTO users VALUES (1, 'ssh-ed25519 YWJj', :cipher), (2, NULL, NULL)"
        ),
        {"cipher": b"unchanged-ciphertext"},
    )
    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()
    row = (
        db.execute(
            text(
                "SELECT k.* FROM users u JOIN ssh_keys k ON k.id=u.default_ssh_key_id WHERE u.id=1"
            )
        )
        .mappings()
        .one()
    )
    assert bytes(row["private_key_encrypted"]) == b"unchanged-ciphertext"
    assert row["user_id"] == 1
    assert row["public_key"] == "ssh-ed25519 YWJj"
    assert row["fingerprint"].startswith("SHA256:")
    assert (
        db.execute(text("SELECT default_ssh_key_id FROM users WHERE id=2")).scalar()
        is None
    )
    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()
    assert (
        bytes(
            db.execute(
                text("SELECT ssh_private_key_encrypted FROM users WHERE id=1")
            ).scalar()
        )
        == b"unchanged-ciphertext"
    )
    db_session.rollback()
