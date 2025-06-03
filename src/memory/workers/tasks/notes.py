import logging
import pathlib

from memory.common.db.connection import make_session
from memory.common.db.models import Note
from memory.common.celery_app import app, SYNC_NOTE, SYNC_NOTES
from memory.workers.tasks.content_processing import (
    check_content_exists,
    create_content_hash,
    create_task_result,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


@app.task(name=SYNC_NOTE)
@safe_task_execution
def sync_note(
    subject: str,
    content: str,
    filename: str | None = None,
    note_type: str | None = None,
    confidences: dict[str, float] = {},
    tags: list[str] = [],
):
    logger.info(f"Syncing note {subject}")
    text = Note.as_text(content, subject)
    sha256 = create_content_hash(text)

    if filename:
        filename = filename.lstrip("/")
        if not filename.endswith(".md"):
            filename = f"{filename}.md"

    with make_session() as session:
        existing_note = check_content_exists(session, Note, sha256=sha256)
        if existing_note:
            logger.info(f"Note already exists: {existing_note.subject}")
            return create_task_result(existing_note, "already_exists")

        note = session.query(Note).filter(Note.filename == filename).one_or_none()

        if not note:
            note = Note(
                modality="note",
                mime_type="text/markdown",
            )
        else:
            logger.info("Editing preexisting note")
        note.content = content  # type: ignore
        note.subject = subject  # type: ignore
        note.filename = filename  # type: ignore
        note.embed_status = "RAW"  # type: ignore
        note.size = len(text.encode("utf-8"))  # type: ignore
        note.sha256 = sha256  # type: ignore

        if note_type:
            note.note_type = note_type  # type: ignore
        if tags:
            note.tags = tags  # type: ignore

        note.update_confidences(confidences)
        note.save_to_file()
        return process_content_item(note, session)


@app.task(name=SYNC_NOTES)
@safe_task_execution
def sync_notes(folder: str):
    path = pathlib.Path(folder)
    logger.info(f"Syncing notes from {folder}")

    new_notes = 0
    all_files = list(path.rglob("*.md"))
    with make_session() as session:
        for filename in all_files:
            if not check_content_exists(session, Note, filename=filename.as_posix()):
                new_notes += 1
                sync_note.delay(
                    subject=filename.stem,
                    content=filename.read_text(),
                    filename=filename.as_posix(),
                )

    return {
        "notes_num": len(all_files),
        "new_notes": new_notes,
    }
