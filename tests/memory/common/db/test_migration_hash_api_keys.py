"""Existing API keys keep authenticating once only their hash is stored."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from memory.common.db.models.users import api_key_preview, hash_api_key

# key -> the preview it must get: only a known type prefix survives, never custom text.
PREVIEWS = {
    "internal_" + "ab" * 32: "internal_...abab",
    "user_" + "0f" * 32: "user_...0f0f",
    "bot_" + "12" * 32: "bot_...1212",
    "mcp_a_b_c1234": "mcp_...1234",
    "abc_": "...abc_",
    "my_secret_pass_1234": "...1234",
    "nounderscore5678": "...5678",
}
KEYS = list(PREVIEWS)


def test_hash_api_keys_leaves_no_plaintext_key(db_session):
    path = Path(__file__).parents[4] / "db/migrations/versions/20261115_hash_api_keys.py"
    spec = importlib.util.spec_from_file_location("hash_api_keys_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA hash_api_keys_migration_test"))
    db.execute(text("SET LOCAL search_path TO hash_api_keys_migration_test"))
    db.execute(text(
        "CREATE TABLE users (id integer PRIMARY KEY, password_hash varchar, api_key varchar UNIQUE, "
        "CONSTRAINT user_has_auth_method CHECK (password_hash IS NOT NULL OR api_key IS NOT NULL))"
    ))
    db.execute(text("INSERT INTO users VALUES (1, NULL, :key)"), {"key": KEYS[1]})
    db.execute(text(
        "CREATE TABLE api_keys (id integer PRIMARY KEY, key varchar NOT NULL UNIQUE, "
        "revoked boolean NOT NULL DEFAULT false)"
    ))
    for i, key in enumerate(KEYS):
        db.execute(text("INSERT INTO api_keys (id, key) VALUES (:id, :key)"), {"id": i, "key": key})

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()

    assert db.execute(text("SELECT key_hash, key_preview FROM api_keys ORDER BY id")).all() == [
        (hash_api_key(key), PREVIEWS[key]) for key in KEYS
    ]
    assert [api_key_preview(key) for key in KEYS] == list(PREVIEWS.values())
    columns = db.execute(text(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'hash_api_keys_migration_test'"
    )).all()
    assert ("api_keys", "key") not in columns
    assert ("users", "api_key") not in columns
    db.execute(text("INSERT INTO users (id) VALUES (2)"))  # a bot: no password, keys live in api_keys

    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()

    # A downgrade must not turn a stored hash into a working key.
    assert db.execute(text("SELECT revoked, key FROM api_keys ORDER BY id")).all() == [
        (True, f"revoked:{hash_api_key(key)}") for key in KEYS
    ]
    db_session.rollback()
