import logging
import pathlib
import contextlib
import subprocess
from typing import cast

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import Note
from memory.common.celery_app import (
    app,
    SYNC_NOTE,
    SYNC_NOTES,
    SETUP_GIT_NOTES,
    TRACK_GIT_CHANGES,
)
from memory.common.content_processing import (
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

    # Build command as list for subprocess (safer than shell=True)
    cmd = ["git", "-C", repo_root.as_posix()] + list(args)

    res = subprocess.run(
        cmd,
        shell=False,
        text=True,
        capture_output=True,  # Capture both stdout and stderr
    )
    if res.returncode != 0:
        logger.error(f"Git command failed: {res.returncode}")
        logger.error(f"stderr: {res.stderr}")
        if res.stdout:
            logger.error(f"stdout: {res.stdout}")
    return res


def check_git_command(repo_root: pathlib.Path, *args: str, force: bool = False):
    res = git_command(repo_root, *args, force=force)
    if not res:
        raise RuntimeError(f"`{' '.join(args)}` failed")

    if res.returncode != 0:
        logger.error(f"Git command failed: {res.returncode}")
        logger.error(f"stderr: {res.stderr}")
        if res.stdout:
            logger.error(f"stdout: {res.stdout}")
        raise RuntimeError(
            f"`{' '.join(args)}` failed with return code {res.returncode}"
        )
    return res.stdout.strip()


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
    save_to_file: bool = True,
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
            existing_as_note = cast(Note, existing_note)
            logger.info(f"Note already exists: {existing_as_note.subject}")
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

        # Process the content item first (commits transaction)
        result = process_content_item(note, session)

    # Git operations MUST be outside the database transaction to avoid
    # holding the connection during slow network I/O
    if save_to_file:
        with git_tracking(
            settings.NOTES_STORAGE_DIR, f"Sync note {filename}: {subject}"
        ):
            # Re-fetch note for file operations (session is closed)
            with make_session() as session:
                note = session.get(Note, result.get("note_id"))
                if note:
                    note.save_to_file()

    return result


@app.task(name=SYNC_NOTES)
@safe_task_execution
def sync_notes(folder: str):
    path = pathlib.Path(folder)
    logger.info(f"Syncing notes from {folder}")

    new_notes = 0
    new_profiles = 0
    all_files = list(path.rglob("*.md"))

    # Import here to avoid circular imports
    from memory.common.db.models import Person
    from memory.workers.tasks.people import sync_profile_from_file

    with make_session() as session:
        for filename in all_files:
            relative_path = filename.relative_to(path).as_posix()

            # Check if this is a profile file
            if relative_path.startswith(f"{settings.PROFILES_FOLDER}/"):
                # Check if person already exists
                identifier = filename.stem
                existing = (
                    session.query(Person)
                    .filter(Person.identifier == identifier)
                    .first()
                )
                if not existing:
                    new_profiles += 1
                    sync_profile_from_file.delay(relative_path)  # type: ignore[attr-defined]
            else:
                if not check_content_exists(
                    session, Note, filename=filename.as_posix()
                ):
                    new_notes += 1
                    sync_note.delay(  # type: ignore[attr-defined]
                        subject=filename.stem,
                        content=filename.read_text(),
                        filename=relative_path,
                    )

    return {
        "notes_num": len(all_files),
        "new_notes": new_notes,
        "new_profiles": new_profiles,
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


@app.task(name=TRACK_GIT_CHANGES)
@safe_task_execution
def track_git_changes():
    """Track git changes by noting current commit, pulling new commits, and listing changed files."""
    logger.info("Tracking git changes")

    repo_root = settings.NOTES_STORAGE_DIR
    if not (repo_root / ".git").exists():
        logger.warning("Git repository not found")
        return {"status": "no_git_repo"}

    current_branch = check_git_command(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    current_commit = check_git_command(repo_root, "rev-parse", "HEAD")
    check_git_command(repo_root, "fetch", "origin")
    git_command(repo_root, "pull", "origin", current_branch)
    latest_commit = check_git_command(
        repo_root, "rev-parse", f"origin/{current_branch}"
    )

    # Check if there are any changes
    if current_commit == latest_commit:
        logger.info("No new changes")
        return {
            "status": "no_changes",
            "current_commit": current_commit,
            "latest_commit": latest_commit,
            "changed_files": [],
        }

    # Get list of changed files between current and latest commit
    diff_result = git_command(
        repo_root, "diff", "--name-only", f"{current_commit}..{latest_commit}"
    )
    if diff_result and diff_result.returncode == 0:
        changed_files = [
            filename
            for f in diff_result.stdout.strip().split("\n")
            if (filename := f.strip()) and filename.endswith(".md")
        ]
        logger.info(f"Changed files: {changed_files}")
    else:
        logger.error("Failed to get changed files")
        return {"status": "error", "error": "Failed to get changed files"}

    for filename in changed_files:
        file = settings.NOTES_STORAGE_DIR / filename
        if not file.exists():
            logger.warning(f"File not found: {filename}")
            continue

        # Check if this is a profile file
        if filename.startswith(f"{settings.PROFILES_FOLDER}/"):
            # Import here to avoid circular imports
            from memory.workers.tasks.people import sync_profile_from_file

            sync_profile_from_file.delay(filename)  # type: ignore[attr-defined]
        else:
            sync_note.delay(  # type: ignore[attr-defined]
                subject=file.stem,
                content=file.read_text(),
                filename=filename,
                save_to_file=False,
            )

    return {
        "status": "success",
        "current_commit": current_commit,
        "latest_commit": latest_commit,
        "changed_files": changed_files,
    }
