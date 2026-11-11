"""Only items that keep a file on disk serve it, and only from their own storage dir."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from memory.common import settings
from memory.common.access_control import get_accessible_source_item_by_filename
from memory.common.db.models import MiscDoc, Note, Photo, Report, User
from memory.common.db.models.source_items import GoogleDoc
from memory.common.db.models.sources import GoogleAccount, GoogleFolder
from memory.workers.tasks.google_drive import _create_google_doc, _update_existing_doc


def drive_file_data(file_id: str, title: str) -> dict:
    return {
        "file_id": file_id, "title": title, "original_mime_type": "text/plain", "folder_path": "Mine",
        "owner": "attacker@gmail.com", "last_modified_by": None, "modified_at": None, "created_at": None,
        "content": f"anything {file_id}", "content_hash": f"h-{file_id}", "size": 10, "word_count": 2,
        "mime_type": "text/plain", "shared_with": [],
    }


def make_folder(db_session, user) -> GoogleFolder:
    account = GoogleAccount(
        user_id=user.id, name="Attacker", email="attacker@gmail.com", access_token="t", refresh_token="r",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1), scopes=["drive"], active=True,
    )
    db_session.add(account)
    db_session.flush()
    folder = GoogleFolder(account_id=account.id, folder_id="f1", folder_name="Mine")
    db_session.add(folder)
    db_session.flush()
    return folder


def write_file(relative: str, body: str = "secret") -> None:
    path = settings.FILE_STORAGE_DIR / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def test_drive_doc_titled_as_a_path_unlocks_nothing(member_client, db_session, user):
    victim = User(id=999, name="Victim", email="victim@example.com", password_hash="x")
    db_session.add(victim)
    db_session.flush()
    write_file("notes/private.md", "the victim's private note")
    db_session.add(Note(
        modality="note", sha256=b"v" * 32, content="the victim's private note", subject="private",
        filename="notes/private.md", creator_id=victim.id,
    ))
    write_file("notes/.git/config", '[remote "origin"]\n\turl = https://bob:hunter2@git.example.com/v.git\n')
    folder = make_folder(db_session, user)
    for n, title in enumerate(("notes/private.md", "notes/.git/config")):
        doc = _create_google_doc(folder, drive_file_data(f"drive-{n}", title))  # type: ignore[arg-type]
        db_session.add(doc)
        db_session.flush()
        doc.apply_inherited_access_control()
    db_session.commit()

    note = member_client.get("/files/notes/private.md")
    config = member_client.get("/files/notes/.git/config")

    assert (note.status_code, config.status_code) == (403, 404), (note.text, config.text)


def test_drive_docs_keep_no_filename(db_session, user):
    folder = make_folder(db_session, user)
    doc = _create_google_doc(folder, drive_file_data("drive-1", "notes/a.md"))  # type: ignore[arg-type]
    db_session.add(doc)
    db_session.flush()

    renamed = drive_file_data("drive-1", "notes/b.md") | {"content_hash": "changed"}
    _update_existing_doc(db_session, doc, folder, renamed)  # type: ignore[arg-type]

    assert (doc.filename, doc.title) == (None, "notes/b.md")


@pytest.mark.parametrize(
    "model, filename, extra",
    [
        (MiscDoc, "notes/other.md", {}),
        (Photo, "reports/other.html", {}),
        (Report, "misc/other.pdf", {"report_format": "pdf"}),
        (Note, "photos/other.jpg", {}),
    ],
)
def test_item_serves_no_file_outside_its_storage_dir(client, db_session, user, model, filename, extra):
    write_file(filename)
    db_session.add(model(modality="doc", sha256=b"x" * 32, filename=filename, creator_id=user.id, **extra))
    db_session.commit()

    response = client.get(f"/files/{filename}")

    assert response.status_code == 404, response.text


def test_hidden_segment_is_never_served(client, db_session, user):
    write_file("notes/.git/config")
    note = Note(modality="note", sha256=b"h" * 32, filename="notes/ok.md", creator_id=user.id)
    db_session.add(note)
    db_session.flush()
    # The model refuses such a filename; a legacy row could still hold one.
    db_session.execute(
        text("UPDATE source_item SET filename = 'notes/.git/config' WHERE id = :id"), {"id": note.id}
    )
    db_session.commit()

    response = client.get("/files/notes/.git/config")

    assert response.status_code == 404, response.text


def test_type_gate_applies_to_admins(db_session, admin_user):
    write_file("notes/private.md")
    doc = _create_google_doc(make_folder(db_session, admin_user), drive_file_data("d", "x"))  # type: ignore[arg-type]
    db_session.add(doc)
    db_session.flush()
    # A row written before Drive docs stopped keeping a filename.
    db_session.execute(
        text("UPDATE source_item SET filename = 'notes/private.md' WHERE id = :id"), {"id": doc.id}
    )
    db_session.commit()

    with pytest.raises(FileNotFoundError):
        get_accessible_source_item_by_filename(db_session, admin_user, "notes/private.md")


@pytest.mark.parametrize(
    "model, filename",
    [
        (GoogleDoc, "notes/private.md"),
        (Note, "/etc/passwd"),
        (Note, "notes/../reports/x.html"),
        (Note, "notes/.git/config"),
        (MiscDoc, ".env"),
    ],
)
def test_model_refuses_unsafe_filename(model, filename):
    with pytest.raises(ValueError):
        model(filename=filename)


@pytest.mark.parametrize(
    "model, filename",
    [(Note, "notes/ok.md"), (MiscDoc, "misc/abc_1_doc.pdf"), (Photo, "photos/p.jpg"), (GoogleDoc, None)],
)
def test_model_accepts_stored_filename(model, filename):
    assert model(filename=filename).filename == filename
