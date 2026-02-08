import logging
import pathlib
import contextlib
import subprocess
import uuid
from typing import cast

import redis

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

# Lock timeout for git notes operations (5 minutes)
GIT_NOTES_LOCK_TIMEOUT = 5 * 60


@contextlib.contextmanager
def git_notes_lock(operation: str = "sync"):
    """Acquire a distributed lock for git notes operations using Redis.

    Prevents concurrent git operations which could cause data loss due to
    git reset --hard and git clean -fd commands.

    Uses atomic check-and-delete to ensure we only release our own lock,
    not one acquired by another process if ours expired.
    """
    redis_client = redis.from_url(settings.REDIS_URL)
    lock_key = f"memory:lock:git_notes:{operation}"
    lock_value = str(uuid.uuid4())

    # Try to acquire lock with NX (only if not exists) and expiry
    acquired = redis_client.set(lock_key, lock_value, nx=True, ex=GIT_NOTES_LOCK_TIMEOUT)
    if not acquired:
        raise RuntimeError(f"Could not acquire git notes lock '{operation}' - another git operation in progress")

    try:
        yield
    finally:
        # Atomically release only if we still own the lock (prevents releasing another process's lock)
        release_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        redis_client.eval(release_script, 1, lock_key, lock_value)


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
    """Context manager for git operations with distributed locking.

    Acquires a lock before performing destructive git operations to prevent
    concurrent tasks from losing uncommitted changes.

    If uncommitted changes exist, they are stashed before reset and restored
    after the operation completes (merged with new changes).
    """
    with git_notes_lock("tracking"):
        git_command(repo_root, "fetch")

        # Check for uncommitted changes before destructive reset
        status_result = git_command(repo_root, "status", "--porcelain")
        status_output = status_result.stdout if status_result else ""
        has_uncommitted = bool(status_output and status_output.strip())

        if has_uncommitted:
            logger.warning(f"Found uncommitted changes, stashing: {status_output[:200]}")
            git_command(repo_root, "stash", "push", "-m", "auto-stash before sync")

        try:
            git_command(repo_root, "reset", "--hard", "origin/master")
            git_command(repo_root, "clean", "-fd")

            yield

            git_command(repo_root, "add", ".")
            git_command(repo_root, "commit", "-m", commit_message)
            git_command(repo_root, "push")
        finally:
            # Restore stashed changes if we had any
            if has_uncommitted:
                result = git_command(repo_root, "stash", "pop")
                if result and result.returncode != 0:
                    # Stash pop failed (likely merge conflict) - log prominently
                    # Changes remain in stash list for manual recovery
                    logger.error(
                        f"Failed to restore stashed changes (exit {result.returncode}): "
                        f"{result.stderr or result.stdout}"
                    )
                    logger.error(
                        "WARNING: Stashed changes remain in stash list. "
                        "Run 'git stash list' and 'git stash pop' manually to recover."
                    )
                else:
                    logger.info("Restored stashed changes")


@app.task(name=SYNC_NOTE)
@safe_task_execution
def sync_note(
    subject: str,
    content: str,
    filename: str | None = None,
    note_type: str | None = None,
    confidences: dict[str, float] | None = None,
    tags: list[str] | None = None,
    save_to_file: bool = True,
):
    confidences = confidences or {}
    tags = tags or []
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
                modality="text",
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
    note_id = result.get("note_id") or result.get("id")
    if save_to_file and note_id:
        with git_tracking(
            settings.NOTES_STORAGE_DIR, f"Sync note {filename}: {subject}"
        ):
            # Re-fetch note for file operations (session is closed)
            with make_session() as session:
                note = session.get(Note, note_id)
                if note:
                    note.save_to_file()
                else:
                    logger.error(f"Note {note_id} not found after commit - skipping file save")
    elif save_to_file:
        logger.error(f"No note_id in result, cannot save to file: {result}")

    return result


@app.task(name=SYNC_NOTES)
@safe_task_execution
def sync_notes(folder: str):
    path = pathlib.Path(folder)
    logger.info(f"Syncing notes from {folder}")

    new_notes = 0
    all_files = list(path.rglob("*.md"))

    with make_session() as session:
        for filename in all_files:
            relative_path = filename.relative_to(path).as_posix()

            # Skip profile files (handled separately)
            if relative_path.startswith(f"{settings.PROFILES_FOLDER}/"):
                continue

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

        # Skip profile files (handled separately)
        if filename.startswith(f"{settings.PROFILES_FOLDER}/"):
            continue

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
