"""Upgrading drops sender and raw-header tags from email attachments."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261119_email_sender_tags.py"
    spec = importlib.util.spec_from_file_location("email_sender_tags_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_drops_sender_tags_from_attachments_only(db_session):
    module = load_migration()
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA email_sender_tags_migration_test"))
    db.execute(text("SET LOCAL search_path TO email_sender_tags_migration_test"))
    db.execute(text("CREATE TABLE source_item (id bigint PRIMARY KEY, tags text[] NOT NULL)"))
    db.execute(text("CREATE TABLE mail_message (id bigint PRIMARY KEY, sender text)"))
    db.execute(text("CREATE TABLE email_attachment (id bigint PRIMARY KEY, mail_message_id bigint)"))
    db.execute(
        text(
            "INSERT INTO source_item (id, tags) VALUES "
            "(1, ARRAY['work', 'a@x.com']), "
            """(2, ARRAY['email', '"mBank" <kontakt@mbank.pl>', 'bank']), """
            "(3, ARRAY['email', 'a@x.com', '=?UTF-8?Q?Wal=C4=99cik?= <k@far.ai>', 'z']), "
            "(4, ARRAY['email', 'b@x.com']), "
            "(6, ARRAY['email', 'Jo <j@x.com>', 'keep'])"
        )
    )
    db.execute(
        text(
            "INSERT INTO mail_message (id, sender) VALUES "
            """(1, 'a@x.com'), (5, '"mBank" <kontakt@mbank.pl>'), (7, NULL)"""
        )
    )
    db.execute(text("INSERT INTO email_attachment VALUES (2, 5), (3, 1), (4, 1), (6, 7)"))

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()
    tags = db.execute(text("SELECT id, tags FROM source_item ORDER BY id")).all()
    db_session.rollback()

    assert tags == [
        (1, ["work", "a@x.com"]),
        (2, ["email", "bank"]),
        (3, ["email", "z"]),
        (4, ["email", "b@x.com"]),
        (6, ["email", "keep"]),
    ]
