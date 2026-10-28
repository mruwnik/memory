"""Existing sessions, refresh tokens and poll edit tokens keep working once only their hash is stored."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from memory.common.db.models.users import hash_token

SESSION = "6f1c3a52-9d1e-4f43-a8a5-3d2b8a0e7c11"
REFRESH = "rt_" + "ab" * 32
EDIT = "0f" * 16


def test_hash_session_tokens_leaves_no_plaintext(db_session):
    path = Path(__file__).parents[4] / "db/migrations/versions/20261117_hash_session_tokens.py"
    spec = importlib.util.spec_from_file_location("hash_session_tokens_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA hash_session_tokens_migration_test"))
    db.execute(text("SET LOCAL search_path TO hash_session_tokens_migration_test"))
    db.execute(text("CREATE TABLE user_sessions (id varchar PRIMARY KEY, api_key_id integer)"))
    db.execute(text(
        "CREATE TABLE oauth_refresh_tokens (id integer PRIMARY KEY, token varchar NOT NULL, revoked boolean NOT NULL, "
        "access_token_session_id varchar REFERENCES user_sessions(id))"
    ))
    db.execute(text("CREATE TABLE poll_responses (id integer PRIMARY KEY, edit_token varchar(32) NOT NULL)"))
    db.execute(text("CREATE INDEX idx_poll_responses_edit_token ON poll_responses (edit_token)"))
    db.execute(text("INSERT INTO user_sessions VALUES (:s, 7), ('other', NULL)"), {"s": SESSION})
    db.execute(text("INSERT INTO oauth_refresh_tokens VALUES (1, :t, false, :s), (2, 'rt_x', false, NULL)"), {"t": REFRESH, "s": SESSION})
    db.execute(text("INSERT INTO poll_responses VALUES (1, :e)"), {"e": EDIT})

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()

    assert set(db.execute(text("SELECT id, api_key_id FROM user_sessions")).all()) == {
        (hash_token(SESSION), 7), (hash_token("other"), None)
    }
    assert db.execute(text(
        "SELECT token_hash, access_token_session_id FROM oauth_refresh_tokens ORDER BY id"
    )).all() == [(hash_token(REFRESH), hash_token(SESSION)), (hash_token("rt_x"), None)]
    assert db.execute(text("SELECT edit_token_hash FROM poll_responses")).scalar_one() == hash_token(EDIT)
    columns = db.execute(text(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'hash_session_tokens_migration_test'"
    )).all()
    assert ("oauth_refresh_tokens", "token") not in columns
    assert ("poll_responses", "edit_token") not in columns
    # Ending a session unlinks its refresh token, which stays for replay detection.
    db.execute(text("DELETE FROM user_sessions WHERE id = :s"), {"s": hash_token(SESSION)})
    assert db.execute(text("SELECT access_token_session_id FROM oauth_refresh_tokens WHERE id = 1")).scalar_one() is None

    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()

    # No hash a reader of the tables has is accepted as a token afterwards.
    assert db.execute(text("SELECT count(*) FROM user_sessions")).scalar_one() == 0
    assert db.execute(text("SELECT token, revoked FROM oauth_refresh_tokens ORDER BY id")).all() == [
        (f"revoked:{hash_token(REFRESH)}", True), (f"revoked:{hash_token('rt_x')}", True)
    ]
    assert db.execute(text("SELECT edit_token FROM poll_responses")).scalar_one() not in (EDIT, hash_token(EDIT)[:32])
    db_session.rollback()
