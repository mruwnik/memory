"""Tests for the SQL effects of individual alembic migrations.

These tests don't actually run alembic — instead they execute the migration's
upgrade SQL directly against the test database, which already has all
migrations applied. The point is to pin the SQL semantics (idempotency,
edge-case handling) rather than to exercise alembic itself.

All tests in this module require ``--run-slow`` because they exercise the
real PostgreSQL database via the autouse ``db_session`` fixture (see
``tests/conftest.py`` for the slow-skip mechanism). Running the file under
plain ``pytest`` will report all tests as skipped — that's expected.

The migration's SQL bodies are imported directly from the migration module
so this file pins exactly the statements the migration runs; if the
migration is edited the tests automatically follow.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
from sqlalchemy import text


def _load_migration_module():
    """Load the prefix-filenames migration by file path.

    Alembic version files live outside the import path (no ``__init__.py``)
    and have filenames starting with a digit, which isn't a valid Python
    module identifier — so `importlib.import_module` won't reach them.
    Loading by file path lets the tests read the migration's SQL constants
    directly, eliminating duplication/drift risk.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[3]
    migration_path = (
        repo_root
        / "db"
        / "migrations"
        / "versions"
        / "20260508_prefix_note_report_filenames.py"
    )
    spec = importlib.util.spec_from_file_location(
        "prefix_filenames_migration", migration_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_migration = _load_migration_module()
PREFIX_NOTES_SQL = _migration._UPGRADE_NOTES_SQL
PREFIX_REPORTS_SQL = _migration._UPGRADE_REPORTS_SQL


def insert_source_item(db_session, *, type_: str, filename: str | None, sha_byte: int):
    """Minimal source_item row for filename-migration tests."""
    db_session.execute(
        text(
            """
            INSERT INTO source_item
                (type, filename, sha256, mime_type, modality, size, embed_status)
            VALUES
                (:type, :filename, :sha256, 'text/plain', 'text', 10, 'RAW')
            RETURNING id
            """
        ),
        {
            "type": type_,
            "filename": filename,
            "sha256": bytes([sha_byte]) * 32,
        },
    ).scalar()


def fetch_filename(db_session, sha_byte: int) -> str | None:
    return db_session.execute(
        text("SELECT filename FROM source_item WHERE sha256 = :sha"),
        {"sha": bytes([sha_byte]) * 32},
    ).scalar()


def test_prefix_notes_migration_prepends_legacy_filenames(db_session):
    insert_source_item(db_session, type_="note", filename="daniel/digests/foo.md", sha_byte=1)

    db_session.execute(text(PREFIX_NOTES_SQL))
    db_session.flush()

    assert fetch_filename(db_session, 1) == "notes/daniel/digests/foo.md"


def test_prefix_notes_migration_skips_already_prefixed(db_session):
    insert_source_item(db_session, type_="note", filename="notes/already.md", sha_byte=2)

    db_session.execute(text(PREFIX_NOTES_SQL))
    db_session.flush()

    # Untouched — must not double-prefix.
    assert fetch_filename(db_session, 2) == "notes/already.md"


def test_prefix_notes_migration_idempotent(db_session):
    insert_source_item(db_session, type_="note", filename="legacy/foo.md", sha_byte=3)

    # Run twice — second run should be a no-op since the LIKE clause now
    # excludes already-prefixed rows.
    db_session.execute(text(PREFIX_NOTES_SQL))
    db_session.flush()
    db_session.execute(text(PREFIX_NOTES_SQL))
    db_session.flush()

    assert fetch_filename(db_session, 3) == "notes/legacy/foo.md"


def test_prefix_reports_migration_prepends_legacy_filenames(db_session):
    insert_source_item(db_session, type_="report", filename="my_report.html", sha_byte=4)

    db_session.execute(text(PREFIX_REPORTS_SQL))
    db_session.flush()

    assert fetch_filename(db_session, 4) == "reports/my_report.html"


def test_prefix_reports_migration_skips_already_prefixed(db_session):
    insert_source_item(db_session, type_="report", filename="reports/done.html", sha_byte=5)

    db_session.execute(text(PREFIX_REPORTS_SQL))
    db_session.flush()

    assert fetch_filename(db_session, 5) == "reports/done.html"


def test_prefix_does_not_cross_types(db_session):
    # A 'note' row with no prefix should be reached by the notes migration but
    # not by the reports migration, and vice versa.
    insert_source_item(db_session, type_="note", filename="x.md", sha_byte=6)
    insert_source_item(db_session, type_="report", filename="y.html", sha_byte=7)

    db_session.execute(text(PREFIX_NOTES_SQL))
    db_session.flush()

    assert fetch_filename(db_session, 6) == "notes/x.md"
    # report row untouched by the notes migration
    assert fetch_filename(db_session, 7) == "y.html"

    db_session.execute(text(PREFIX_REPORTS_SQL))
    db_session.flush()

    assert fetch_filename(db_session, 7) == "reports/y.html"


@pytest.mark.parametrize(
    "filename",
    [
        "",  # empty string — would otherwise produce 'notes/'
        "/foo.md",  # leading slash — would otherwise produce 'notes//foo.md'
    ],
)
def test_prefix_notes_migration_skips_pathological_legacy_rows(
    db_session, filename
):
    insert_source_item(db_session, type_="note", filename=filename, sha_byte=8)

    db_session.execute(text(PREFIX_NOTES_SQL))
    db_session.flush()

    # Defensive WHERE clauses leave these rows untouched rather than
    # producing nonsense filenames.
    assert fetch_filename(db_session, 8) == filename
