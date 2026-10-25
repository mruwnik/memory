import hashlib

import pytest

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


def sync_upload(uploader, scopes=None):
    """Upload the same bytes to the uploader's own path, as the ingest endpoint does."""
    creator_id = uploader and uploader.id
    path = settings.MISC_STORAGE_DIR / f"dupe_{creator_id}.txt"
    path.write_bytes(b"dedupe me " * 20)
    return sync_misc_doc(
        file_path=str(path),
        mime_type="text/plain",
        tags=[],
        doc_metadata={},
        creator_id=creator_id,
        project_id=None,
        creator_scopes=scopes,
    )


@pytest.mark.parametrize(
    "first, second, second_scopes, deduped",
    [
        ("regular", "regular", ["read"], True),
        ("regular", None, None, True),  # an operator may see everything
        ("admin", "regular", ["read"], False),
        ("regular", "admin", ["*"], True),
        ("regular", "admin", ["read"], False),  # an admin's narrowed key
    ],
)
def test_sync_misc_doc_dedups_only_onto_a_doc_the_uploader_can_read(
    db_session, admin_user, regular_user, first, second, second_scopes, deduped
):
    users = {"admin": admin_user, "regular": regular_user, None: None}
    original = sync_upload(users[first])
    again = sync_upload(users[second], second_scopes)

    assert (again["status"] == "already_exists") == deduped
    assert (again["miscdoc_id"] == original["miscdoc_id"]) == deduped
    assert db_session.query(MiscDoc).count() == (1 if deduped else 2)


def test_sync_misc_doc_second_uploaders_copy_dedups_onto_itself(
    db_session, admin_user, regular_user
):
    sync_upload(admin_user)
    own = sync_upload(regular_user)
    again = sync_upload(regular_user)

    assert again["status"] == "already_exists"
    assert again["miscdoc_id"] == own["miscdoc_id"]
    assert db_session.query(MiscDoc).count() == 2


def test_sync_misc_doc_refuses_to_bring_back_the_uploaders_hidden_doc(db_session, regular_user):
    original = sync_upload(regular_user)
    db_session.get(MiscDoc, original["miscdoc_id"]).sensitivity = "hidden"
    db_session.commit()

    with pytest.raises(ValueError, match="hidden"):
        sync_upload(regular_user)

    assert db_session.query(MiscDoc).count() == 1
