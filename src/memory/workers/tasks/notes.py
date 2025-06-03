import logging
import pathlib
import contextlib
import subprocess
import shlex

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import Note
from memory.common.celery_app import app, SYNC_NOTE, SYNC_NOTES, SETUP_GIT_NOTES
from memory.workers.tasks.content_processing import (
    check_content_exists,
    create_content_hash,
    create_task_result,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


def git_command(repo_root: pathlib.Path, *args: str, force: bool = False):
    if not (repo_root / ".git").exists() and not force:
        return

    # Properly escape arguments for shell execution
    escaped_args = [shlex.quote(arg) for arg in args]
    cmd = f"git -C {shlex.quote(repo_root.as_posix())} {' '.join(escaped_args)}"

    res = subprocess.run(
        cmd,
        shell=True,
        text=True,
        capture_output=True,  # Capture both stdout and stderr
    )
    if res.returncode != 0:
        logger.error(f"Git command failed: {res.returncode}")
        logger.error(f"stderr: {res.stderr}")
        if res.stdout:
            logger.error(f"stdout: {res.stdout}")
    return res


@contextlib.contextmanager
def git_tracking(repo_root: pathlib.Path, commit_message: str = "Sync note"):
    git_command(repo_root, "fetch")
    git_command(repo_root, "reset", "--hard", "origin/master")
    git_command(repo_root, "clean", "-fd")

    yield

    git_command(repo_root, "add", ".")
    git_command(repo_root, "commit", "-m", commit_message)
    git_command(repo_root, "push")


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
        with git_tracking(
            settings.NOTES_STORAGE_DIR, f"Sync note {filename}: {subject}"
        ):
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


@app.task(name=SETUP_GIT_NOTES)
@safe_task_execution
def setup_git_notes(origin: str, email: str, name: str):
    logger.info(f"Setting up git notes in {origin}")
    if (settings.NOTES_STORAGE_DIR / ".git").exists():
        logger.info("Git notes already setup")
        return {"status": "already_setup"}

    git_command(settings.NOTES_STORAGE_DIR, "init", "-b", "main", force=True)
    git_command(settings.NOTES_STORAGE_DIR, "config", "user.email", email)
    git_command(settings.NOTES_STORAGE_DIR, "config", "user.name", name)
    git_command(settings.NOTES_STORAGE_DIR, "remote", "add", "origin", origin)
    git_command(settings.NOTES_STORAGE_DIR, "add", ".")
    git_command(settings.NOTES_STORAGE_DIR, "commit", "-m", "Initial commit")
    git_command(settings.NOTES_STORAGE_DIR, "push", "-u", "origin", "main")
    return {"status": "success"}
