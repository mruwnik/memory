import hashlib

from memory.common import settings
from memory.common.db.models import MiscDoc
from memory.workers.tasks.misc import sync_misc_doc


def test_sync_misc_doc_ingests_text(db_session):
    body = b"generic misc ingestion body, long enough to chunk.\n" * 3
    path = settings.MISC_STORAGE_DIR / "task_doc.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)

    result = sync_misc_doc(
        file_path=str(path),
        mime_type="text/plain",
        tags=["unit"],
        doc_metadata={"k": "v"},
        creator_id=None,
        project_id=None,
    )

    assert result["status"] in {"processed", "already_exists", "skipped"}
    doc = (
        db_session.query(MiscDoc)
        .filter(MiscDoc.sha256 == hashlib.sha256(body).digest())
        .one()
    )
    assert doc.doc_metadata == {"k": "v"}
    assert doc.mime_type == "text/plain"
    assert doc.tags == ["unit"]


def test_sync_misc_doc_is_idempotent(db_session):
    body = b"dedupe me " * 20
    path = settings.MISC_STORAGE_DIR / "dupe.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    kwargs = dict(
        file_path=str(path),
        mime_type="text/plain",
        tags=[],
        doc_metadata={},
        creator_id=None,
        project_id=None,
    )
    sync_misc_doc(**kwargs)
    sync_misc_doc(**kwargs)
    assert (
        db_session.query(MiscDoc)
        .filter(MiscDoc.sha256 == hashlib.sha256(body).digest())
        .count()
        == 1
    )
