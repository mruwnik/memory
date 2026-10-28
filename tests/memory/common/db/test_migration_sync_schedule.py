"""Upgrading keeps each source's effective sync cadence from the env intervals it replaces."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def load_migration():
    path = Path(__file__).parents[4] / "db/migrations/versions/20261113_sync_schedule.py"
    spec = importlib.util.spec_from_file_location("sync_schedule_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_raises_intervals_to_the_old_beat_period(db_session, monkeypatch):
    module = load_migration()
    monkeypatch.setenv("CALENDAR_SYNC_INTERVAL", str(60 * 60))
    monkeypatch.setenv("EMAIL_SYNC_INTERVAL", str(5 * 60))
    monkeypatch.setenv("LESSWRONG_SYNC_CHECK_INTERVAL", "0")
    db = db_session.connection()
    db.execute(text("CREATE SCHEMA sync_schedule_migration_test"))
    db.execute(text("SET LOCAL search_path TO sync_schedule_migration_test"))
    for table in ("github_repos", "google_folders"):
        db.execute(text(f"CREATE TABLE {table} (id bigint PRIMARY KEY, check_interval int, last_sync_at timestamptz)"))
    db.execute(text("CREATE TABLE article_feeds (id bigint PRIMARY KEY, check_interval int, last_checked_at timestamptz)"))
    db.execute(text("CREATE TABLE calendar_accounts (id bigint PRIMARY KEY, check_interval int, sync_past_days int, last_sync_at timestamptz)"))
    db.execute(text("CREATE TABLE email_accounts (id bigint PRIMARY KEY, last_sync_at timestamptz)"))
    db.execute(text("CREATE TABLE forum_sync_configs (forum text PRIMARY KEY, active bool, last_sync_at timestamptz)"))
    db.execute(text("INSERT INTO calendar_accounts VALUES (1, 15, 30, '2026-10-01'), (2, 240, 30, NULL)"))
    db.execute(text("INSERT INTO email_accounts VALUES (1, NULL)"))
    db.execute(text("INSERT INTO forum_sync_configs VALUES ('lesswrong', true, NULL)"))

    with Operations.context(MigrationContext.configure(db)):
        module.upgrade()

    assert db.execute(
        text("SELECT check_interval, days_back, sync_queued_at::date::text FROM calendar_accounts ORDER BY id")
    ).all() == [(60, 30, "2026-10-01"), (240, 30, None)]
    assert db.execute(text("SELECT check_interval FROM email_accounts")).scalar() == 5
    assert db.execute(text("SELECT ingest_delay, active FROM forum_sync_configs")).one() == (8 * 60, False)
    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()
    db_session.rollback()
