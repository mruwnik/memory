import importlib.util
from pathlib import Path

from sqlalchemy import text

from memory.common.db.models import Note
from tests.conftest import unique_sha256

MIGRATION = Path(__file__).parents[4] / "db/migrations/versions/20261105_unique_note_filename.py"


def migration():
    spec = importlib.util.spec_from_file_location("unique_note_filename", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def add_note(db, filename: str, sha256: bytes) -> Note:
    note = Note(
        subject="s",
        content="c",
        sha256=sha256,
        modality="text",
        mime_type="text/markdown",
        size=1,
        filename=filename,
    )
    db.add(note)
    db.flush()
    return note


def test_duplicate_note_filenames_keep_only_the_newest_and_free_their_hashes(db_session):
    # Recreate pre-migration data; the rollback restores the index.
    db_session.execute(text("DROP INDEX source_note_filename_key"))
    old_sha, new_sha, other_sha = unique_sha256("old"), unique_sha256("new"), unique_sha256("other")
    old = add_note(db_session, "notes/dup.md", old_sha)
    new = add_note(db_session, "notes/dup.md", new_sha)
    other = add_note(db_session, "notes/other.md", other_sha)

    migration().retire_duplicate_notes(db_session.connection())
    db_session.expire_all()

    rows = {n.id: (n.filename, n.sha256) for n in db_session.query(Note)}
    db_session.rollback()
    assert rows[new.id] == ("notes/dup.md", new_sha)
    assert rows[other.id] == ("notes/other.md", other_sha)
    assert rows[old.id][0] is None
    # A later write of the old content must not dedupe onto the retired row.
    assert rows[old.id][1] != old_sha
