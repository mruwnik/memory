import contextlib
import json
import logging
import os
import pathlib
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, NamedTuple, cast

import redis
from sqlalchemy.exc import IntegrityError

from memory.common import instance_settings, paths, settings
from memory.common.db.connection import make_session
from memory.common.db.models import Note
from memory.common.celery_app import (
    app,
    SYNC_NOTE,
    REINDEX_NOTE,
    WRITE_NOTE_FILE,
    SETUP_GIT_NOTES,
    TRACK_GIT_CHANGES,
    SYNC_NOTES_WHEN_DUE,
)
from memory.common.content_processing import (
    check_content_exists,
    create_content_hash,
    create_task_result,
    process_content_item,
    safe_task_execution,
)
from memory.common.jobs import tracked_task
from memory.common.notes_git import (
    GIT_SYNC_STATUS_KEY,
    TOKEN_ENV,
    credential_args,
    check_branch,
    https_host,
    last_sync,
    read_vault_git,
    sync_remote,
    token_for_remote,
)
from memory.common.redis_lock import distributed_lock

logger = logging.getLogger(__name__)

# Lock timeout for git notes operations (5 minutes)
GIT_NOTES_LOCK_TIMEOUT = 5 * 60
GIT_NOTES_LOCK_POLL_SECONDS = 0.5
# Last resort, well inside the lock's TTL: the ssh and http options fail a dead remote sooner.
GIT_NETWORK_TIMEOUT = 120
SSH_TIMEOUT_OPTIONS = (
    "-o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 -o BatchMode=yes"
)
GIT_SYNC_HINTS = {
    "merge_conflict": (
        "Merging the vault's remote failed: commit or discard the vault's uncommitted"
        " edits, or merge by hand."
    ),
    "operation_in_progress": "A git operation is in progress in the vault: finish or abort it.",
    "unmerged": "The vault has unresolved conflicts: resolve and commit them.",
    "detached": "The vault is not on a branch: check one out.",
    "unreadable_repo": "The vault's git repository can't be read.",
    "no_upstream": "The vault has no remote: commits stay local.",
    "fetch": "The vault's remote can't be reached: commits stay local until it can.",
    "push": "Pushing to the vault's remote failed: commits stay local until it works.",
}
# A deferred file write retries a blocked vault for about a day: 1, 2, 4 ... 30 minutes apart.
VAULT_BUSY_RETRIES = 50
VAULT_BUSY_MAX_BACKOFF = 30 * 60
GIT_OPERATION_MARKERS = (
    "rebase-merge",
    "rebase-apply",
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
)
NOTE_FILENAME_KEY = "source_note_filename_key"
GIT_INDEXED_COMMIT_KEY = "memory:notes:git_indexed_commit"
SYNC_QUEUED_KEY = "memory:notes:sync_queued"
# Long enough for a queued sync to run and record its status, so the minutely check doesn't queue another.
SYNC_QUEUED_TTL = 10 * 60
PENDING_WRITE_KEY = "memory:notes:pending_write:{filename}"
# Outlives a deferred write's retries, so a lost worker can't block reindexing forever.
PENDING_WRITE_TTL = 2 * 24 * 60 * 60
# Lua: delete the key only if it still holds this write's sha, not a newer edit's.
CLEAR_PENDING_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class GitLockTimeout(RuntimeError):
    pass


@contextlib.contextmanager
def git_notes_lock(operation: str = "sync", wait_seconds: float = GIT_NOTES_LOCK_TIMEOUT):
    """Acquire a distributed lock for git notes operations using Redis.

    Waits up to ``wait_seconds`` for the current holder, because sync_note has
    already committed its row when it gets here. Raises GitLockTimeout once the
    wait runs out.
    """
    lock_key = f"memory:lock:git_notes:{operation}"
    client = redis.from_url(settings.REDIS_URL)
    deadline = time.monotonic() + wait_seconds
    while True:
        with distributed_lock(lock_key, GIT_NOTES_LOCK_TIMEOUT, client=client) as lock:
            if lock is not None:
                yield
                return
        if time.monotonic() >= deadline:
            raise GitLockTimeout(
                f"Could not acquire git notes lock '{operation}' - "
                "another git operation in progress"
            )
        time.sleep(GIT_NOTES_LOCK_POLL_SECONDS)


def run_git(
    repo_root: pathlib.Path, *args: str, network: bool = False
) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "-C", repo_root.as_posix(), "-c", "core.quotePath=false"]
    env, timeout = None, None
    if network:
        # No redirects: a remote could bounce the request to an internal address.
        cmd += ["-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=30", "-c", "http.followRedirects=false"]
        ssh = os.environ.get("GIT_SSH_COMMAND", "ssh")
        env = {**os.environ, "GIT_SSH_COMMAND": f"{ssh} {SSH_TIMEOUT_OPTIONS}"}
        timeout = GIT_NETWORK_TIMEOUT
        remote_url = read_vault_git(repo_root).remote_url
        host = https_host(remote_url)
        if host and (token := token_for_remote(remote_url)):
            cmd += credential_args(host)
            env[TOKEN_ENV] = token
    cmd += args
    try:
        return subprocess.run(cmd, text=True, capture_output=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="timed out")


class Upstream(NamedTuple):
    remote: str
    branch: str
    configured: bool

    @property
    def tracking_ref(self) -> str:
        return f"refs/remotes/{self.remote}/{self.branch}"


def find_upstream(repo_root: pathlib.Path) -> Upstream | None:
    """The current branch's upstream, else the same-named branch on origin (or on the only remote)."""
    branch = run_git(repo_root, "symbolic-ref", "--short", "HEAD").stdout.strip()
    if not branch:
        return None
    configured = run_git(
        repo_root,
        "for-each-ref",
        "--format=%(upstream:remotename) %(upstream:remoteref)",
        f"refs/heads/{branch}",
    ).stdout.split()
    if len(configured) == 2:
        remote, ref = configured
        return Upstream(remote, ref.removeprefix("refs/heads/"), configured=True)
    if remote := sync_remote(None, tuple(run_git(repo_root, "remote").stdout.split())):
        return Upstream(remote, branch, configured=False)
    return None


def head_commit(repo_root: pathlib.Path) -> str:
    return run_git(repo_root, "rev-parse", "--verify", "--quiet", "HEAD").stdout.strip()


def count_commits(repo_root: pathlib.Path, revisions: str) -> int:
    res = run_git(repo_root, "rev-list", "--count", revisions)
    return int(res.stdout) if res.returncode == 0 else 0


def git_step_failed(step: str, res: subprocess.CompletedProcess[str]) -> bool:
    if res.returncode == 0:
        return False
    # Git's output can carry note paths, subjects and the remote URL.
    logger.warning("Notes git %s failed with exit %s", step, res.returncode)
    return True


def repo_blocked(repo_root: pathlib.Path) -> str | None:
    """Why Memory mustn't touch the vault now: the user is mid-operation, or HEAD is off a branch.

    A write there could overwrite the user's resolution, be wiped by an abort,
    or later be committed as the user's own edit.
    """
    git_dir = run_git(repo_root, "rev-parse", "--absolute-git-dir")
    if git_dir.returncode != 0:
        return "unreadable_repo"
    if any((pathlib.Path(git_dir.stdout.strip()) / m).exists() for m in GIT_OPERATION_MARKERS):
        return "operation_in_progress"
    if run_git(repo_root, "ls-files", "--unmerged").stdout.strip():
        return "unmerged"
    if run_git(repo_root, "symbolic-ref", "--quiet", "HEAD").returncode != 0:
        return "detached"
    return None


def pull_upstream(repo_root: pathlib.Path, upstream: Upstream) -> str | None:
    """Merge the remote's commits in, keeping local ones. Returns the failed step, if any.

    git merge refuses up front when it would touch a file with uncommitted
    edits, so those edits are never stashed or overwritten. A conflicted merge
    is aborted, which leaves the user's unrelated edits in place.
    """
    fetch = run_git(repo_root, "fetch", "--quiet", upstream.remote, network=True)
    if git_step_failed("fetch", fetch):
        return "fetch"
    tracking = upstream.tracking_ref
    if run_git(repo_root, "rev-parse", "--verify", "--quiet", tracking).returncode != 0:
        return None
    if count_commits(repo_root, f"HEAD..{tracking}") == 0:
        return None
    merge = run_git(repo_root, "merge", "--no-edit", "--quiet", tracking)
    if not git_step_failed("merge", merge):
        return None
    if run_git(repo_root, "rev-parse", "--verify", "--quiet", "MERGE_HEAD").returncode == 0:
        run_git(repo_root, "merge", "--abort")
    return "merge_conflict"


def push_upstream(repo_root: pathlib.Path, upstream: Upstream) -> str | None:
    tracking = upstream.tracking_ref
    has_remote_branch = (
        run_git(repo_root, "rev-parse", "--verify", "--quiet", tracking).returncode == 0
    )
    if has_remote_branch and count_commits(repo_root, f"{tracking}..HEAD") == 0:
        return None
    set_upstream = [] if upstream.configured else ["--set-upstream"]
    push = run_git(
        repo_root,
        "push",
        "--quiet",
        *set_upstream,
        upstream.remote,
        f"HEAD:refs/heads/{upstream.branch}",
        network=True,
    )
    return "push" if git_step_failed("push", push) else None


def repo_path(repo_root: pathlib.Path, path: pathlib.Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def commit_files(repo_root: pathlib.Path, files: list[pathlib.Path], message: str) -> str | None:
    """Commit just ``files``: anything else staged or edited in the vault stays as it is."""
    paths = [repo_path(repo_root, f) for f in files]
    if not paths:
        return None
    if git_step_failed("add", run_git(repo_root, "add", "--", *paths)):
        failed = "add"
    elif run_git(repo_root, "diff", "--cached", "--quiet", "--", *paths).returncode == 0:
        return None
    else:
        commit = run_git(repo_root, "commit", "--quiet", "-m", message, "--only", "--", *paths)
        failed = "commit" if git_step_failed("commit", commit) else None
    if failed:
        # Paths left staged would ride along in the user's next commit.
        run_git(repo_root, "reset", "--quiet", "--", *paths)
    return failed


def changed_notes(repo_root: pathlib.Path, before: str, after: str) -> list[str]:
    if not before or before == after:
        return []
    diff = run_git(repo_root, "diff", "--name-only", "-z", f"{before}..{after}")
    if git_step_failed("diff", diff):
        return []
    return [f for f in diff.stdout.split("\0") if f.endswith(".md")]


def reindex_notes(repo_root: pathlib.Path, filenames: list[str]):
    # Not via to_db_filename(path): that resolves symlinks, which reindex_note must refuse.
    prefix = paths.to_db_filename(repo_root)
    for filename in filenames:
        reindex_note.delay(f"{prefix}/{filename}")  # type: ignore[attr-defined]


def mark_pending_write(filename: str, sha256: bytes):
    key = PENDING_WRITE_KEY.format(filename=filename)
    redis.from_url(settings.REDIS_URL).set(key, sha256.hex(), ex=PENDING_WRITE_TTL)


def clear_pending_write(filename: str, sha256: bytes):
    key = PENDING_WRITE_KEY.format(filename=filename)
    redis.from_url(settings.REDIS_URL).eval(CLEAR_PENDING_SCRIPT, 1, key, sha256.hex())  # type: ignore[arg-type]


def write_pending(filename: str) -> bool:
    key = PENDING_WRITE_KEY.format(filename=filename)
    return bool(redis.from_url(settings.REDIS_URL).exists(key))


def read_vault_note(path: pathlib.Path) -> str | None:
    """The note's text, unless it isn't a regular file inside the vault or can't be decoded."""
    # A pulled symlink could point anywhere on the worker; never follow it.
    if path.is_symlink() or not path.resolve().is_relative_to(settings.NOTES_STORAGE_DIR.resolve()):
        return None
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return None


def record_git_sync(failed_step: str | None):
    status = {
        "ok": failed_step is None,
        "failed_step": failed_step,
        "hint": GIT_SYNC_HINTS.get(failed_step or ""),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    redis.from_url(settings.REDIS_URL).set(GIT_SYNC_STATUS_KEY, json.dumps(status))


class VaultBusy(RuntimeError):
    def __init__(self, step: str):
        super().__init__(f"Notes vault is not writable right now: {step}")
        self.step = step


def indexed_commit(repo_root: pathlib.Path) -> str | None:
    """The vault commit whose notes are all indexed, if it still exists."""
    commit = redis.from_url(settings.REDIS_URL).get(GIT_INDEXED_COMMIT_KEY)
    if not commit:
        return None
    commit = commit.decode() if isinstance(commit, bytes) else str(commit)
    exists = run_git(repo_root, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0
    return commit if exists else None


@dataclass
class VaultSync:
    repo_root: pathlib.Path
    is_repo: bool
    before: str = ""
    failures: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    written: list[pathlib.Path] = field(default_factory=list)

    def track(self, path: pathlib.Path):
        """Call before writing ``path``: an uncommitted edit there is committed first, so it stays in history."""
        self.written.append(path)
        rel = repo_path(self.repo_root, path)
        if not self.is_repo or not run_git(self.repo_root, "status", "--porcelain", "--", rel).stdout:
            return
        # Commits the worktree version; a different staged version of the file is not kept.
        if step := commit_files(
            self.repo_root, [path], f"Keep local edits to {rel} before Memory update"
        ):
            self.failures.append(step)

    @property
    def failed_step(self) -> str | None:
        return self.failures[0] if self.failures else None


@contextlib.contextmanager
def git_tracking(repo_root: pathlib.Path, commit_message: str = "Sync note") -> Iterator[VaultSync]:
    """Pull the remote, then commit and push the files the caller ``track``s.

    Edits made to the vault outside Memory are never stashed or reset, and are
    only committed when Memory is about to overwrite that file. When the remote
    can't be reached the commit stays local and goes out with a later sync.
    Notes changed since the last indexed commit (pulled, or committed in the
    vault by hand) are queued for reindexing; all of them when no commit has
    been indexed yet. Raises VaultBusy, before
    anything is written, while the vault is mid-operation or off a branch.
    """
    with git_notes_lock("tracking"):
        if not (repo_root / ".git").exists():
            yield VaultSync(repo_root, is_repo=False)
            return

        if blocked := repo_blocked(repo_root):
            record_git_sync(blocked)
            raise VaultBusy(blocked)
        sync = VaultSync(repo_root, is_repo=True, before=head_commit(repo_root))
        upstream = find_upstream(repo_root)
        if not upstream:
            sync.failures.append("no_upstream")
        elif step := pull_upstream(repo_root, upstream):
            sync.failures.append(step)
        if base := indexed_commit(repo_root):
            sync.changed = changed_notes(repo_root, base, head_commit(repo_root))
        else:
            sync.changed = [f.relative_to(repo_root).as_posix() for f in repo_root.rglob("*.md")]

        try:
            yield sync
        finally:
            ours = {repo_path(repo_root, p) for p in sync.written}
            reindex_notes(repo_root, [f for f in sync.changed if f not in ours])

        if step := commit_files(repo_root, sync.written, commit_message):
            sync.failures.append(step)
        if upstream and not sync.failures and (step := push_upstream(repo_root, upstream)):
            sync.failures.append(step)
        record_git_sync(sync.failed_step)
        redis.from_url(settings.REDIS_URL).set(GIT_INDEXED_COMMIT_KEY, head_commit(repo_root))


def violates(error: IntegrityError, constraint: str) -> bool:
    diag = getattr(error.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == constraint


def note_file_missing(note: Note) -> bool:
    return bool(note.filename) and not (settings.FILE_STORAGE_DIR / str(note.filename)).exists()


def find_duplicate(
    sha256: bytes, save_to_file: bool, creator_id: int | None = None, admin: bool = False
) -> tuple[dict, int | None] | None:
    """The result for a note already stored with this content, and its id if its file is missing."""
    with make_session() as session:
        existing = check_content_exists(session, Note, sha256=sha256)
        if not existing:
            return None
        note = cast(Note, existing)
        if creator_id is not None and not admin and note.creator_id != creator_id:
            return {"status": "forbidden"}, None
        logger.info(f"Note already exists: {note.subject}")
        missing = save_to_file and note_file_missing(note)
        return create_task_result(note, "already_exists"), (note.id if missing else None)


def write_note_file(note_id: int, sha256: bytes, commit_message: str) -> bool:
    """Write the note's file, unless the note has changed since ``sha256``. Returns whether it did."""
    with git_tracking(settings.NOTES_STORAGE_DIR, commit_message) as sync:
        # Re-fetch note for file operations (session is closed)
        with make_session() as session:
            note = session.get(Note, note_id)
            if not note:
                logger.error(f"Note {note_id} not found after commit - skipping file save")
                return False
            if note.sha256 != sha256:
                # The mark belongs to the newer edit's write, which clears it.
                logger.info("Note %s changed since this write was queued; skipping it", note_id)
                return False
            sync.track(note.file_path())
            note.save_to_file()
            clear_pending_write(str(note.filename), sha256)
            return True


def vault_busy_backoff(retries: int) -> int:
    return min(60 * 2**retries, VAULT_BUSY_MAX_BACKOFF)


def write_note_file_or_defer(note_id: int, filename: str, sha256: bytes, commit_message: str):
    try:
        write_note_file(note_id, sha256, commit_message)
    except (VaultBusy, GitLockTimeout):
        logger.warning("Notes vault busy: deferring the file write of note %s", note_id)
        write_note_file_when_free.apply_async(  # type: ignore[attr-defined]
            (note_id, filename, sha256.hex(), commit_message), countdown=vault_busy_backoff(0)
        )
    except Exception:
        clear_pending_write(filename, sha256)
        raise


@app.task(name=WRITE_NOTE_FILE, bind=True, max_retries=VAULT_BUSY_RETRIES)
@safe_task_execution
def write_note_file_when_free(
    self, note_id: int, filename: str, sha256_hex: str, commit_message: str
):
    """Write a note's file once the vault is free; a newer edit of the note supersedes it."""
    sha256 = bytes.fromhex(sha256_hex)
    try:
        written = write_note_file(note_id, sha256, commit_message)
    except (VaultBusy, GitLockTimeout) as e:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=vault_busy_backoff(self.request.retries + 1))
        clear_pending_write(filename, sha256)
        raise
    except Exception:
        clear_pending_write(filename, sha256)
        raise
    return {"status": "written" if written else "superseded", "note_id": note_id}


@app.task(name=REINDEX_NOTE, bind=True, max_retries=VAULT_BUSY_RETRIES)
@safe_task_execution
def reindex_note(self, filename: str):
    """Index a vault file's current text, unless Memory's own newer edit of it awaits writing.

    Reads the file under the git lock, so it sees Memory's write or the pending mark.
    """
    try:
        with git_notes_lock("tracking"):
            return reindex_note_file(filename)
    except GitLockTimeout as e:
        # The indexed commit has moved past this file: dropping it would skip it for good.
        raise self.retry(exc=e, countdown=vault_busy_backoff(self.request.retries))


def reindex_note_file(filename: str) -> dict:
    if write_pending(filename):
        return {"status": "write_pending"}
    path = settings.FILE_STORAGE_DIR / filename
    content = read_vault_note(path)
    if content is None:
        logger.warning("Skipped reindexing a note that isn't a readable vault file")
        return {"status": "unreadable"}
    with make_session() as session:
        row = session.query(Note).filter(Note.filename == filename).one_or_none()
        if row and row.content == content:
            return {"status": "unchanged", "note_id": row.id}
        # The file holds only the body; the subject lives in the row.
        subject = str(row.subject) if row and row.subject else path.stem
    return upsert_note(subject=subject, content=content, filename=filename, save_to_file=False)


@app.task(name=SYNC_NOTE)
@tracked_task
def sync_note(subject: str, content: str, **options: Any):
    return upsert_note(subject, content, **options)


def upsert_note(
    subject: str,
    content: str,
    filename: str | None = None,
    note_type: str | None = None,
    confidences: dict[str, float] | None = None,
    tags: list[str] | None = None,
    save_to_file: bool = True,
    creator_id: int | None = None,
    admin: bool = False,
    create_only: bool = False,
    retried: bool = False,
):
    """Save a note. ``creator_id`` is the writing user, who may only overwrite their own notes
    unless ``admin``; None means the vault itself (sync and reindex), which may overwrite any.
    """
    confidences = confidences or {}
    tags = tags or []
    logger.info(f"Syncing note {subject}")
    text = Note.as_text(content, subject)
    sha256 = create_content_hash(text)

    # Set before the row is saved: two rows without a filename would match each other.
    # Defense in depth: track_git_changes passes paths from the
    # vault and its remote, which we don't trust.
    try:
        filename = Note.db_filename(filename, subject)
    except ValueError:
        logger.error(
            "Refusing to sync note %s of creator %s: unsafe filename",
            sha256.hex()[:8],
            creator_id,
        )
        raise

    if duplicate := find_duplicate(sha256, save_to_file, creator_id, admin):
        result, rewrite_id = duplicate
        if rewrite_id:
            # The row was committed but its file write never happened.
            logger.warning("Rewriting missing file of note %s", rewrite_id)
            write_note_file_or_defer(
                rewrite_id, filename, sha256, f"Rewrite file of note {rewrite_id}"
            )
        return result

    with make_session() as session:
        # The row lock orders this save against a concurrent reindex's write_pending check.
        note = (
            session.query(Note).filter(Note.filename == filename).with_for_update().one_or_none()
        )

        if note and creator_id is not None and not admin and note.creator_id != creator_id:
            logger.warning("Refusing user %s's write over note %s they don't own", creator_id, note.id)
            return {"status": "forbidden"}

        if note and create_only:
            # Closes the race between the MCP tool's check and this task running.
            logger.warning("Refusing to overwrite existing note %s", filename)
            return {"status": "already_exists", "note_id": note.id}

        created = note is None
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

        # Only set creator_id on new notes; preserve existing ownership on updates
        if creator_id is not None and note.creator_id is None:
            note.creator_id = creator_id  # type: ignore

        if note_type:
            note.note_type = note_type  # type: ignore
        if tags:
            note.tags = tags  # type: ignore

        note.update_confidences(confidences)

        if save_to_file:
            # Before the commit, so a reindex can't index the old file over this edit.
            mark_pending_write(filename, sha256)
        elif write_pending(filename):
            # A Memory edit was saved while this reindex read the file.
            session.rollback()
            return {"status": "write_pending"}
        # Process the content item first (commits transaction)
        try:
            result = process_content_item(note, session)
        except IntegrityError as e:
            if save_to_file:
                clear_pending_write(filename, sha256)
            if retried or not (created and violates(e, NOTE_FILENAME_KEY)):
                raise
            session.rollback()
            result = None
        except Exception:
            if save_to_file:
                clear_pending_write(filename, sha256)
            raise

    if result is None:
        # A concurrent save created this filename first: go again, against its row.
        return upsert_note(
            subject,
            content,
            filename=filename,
            note_type=note_type,
            confidences=confidences,
            tags=tags,
            save_to_file=save_to_file,
            creator_id=creator_id,
            admin=admin,
            create_only=create_only,
            retried=True,
        )

    # Git operations MUST be outside the database transaction to avoid
    # holding the connection during slow network I/O
    note_id = result.get("note_id") or result.get("id")
    if save_to_file and note_id:
        write_note_file_or_defer(note_id, filename, sha256, f"Sync note {filename}: {subject}")
    elif save_to_file:
        logger.error(f"No note_id in result, cannot save to file: status={result.get('status')}")

    return result


def setup_step(repo_root: pathlib.Path, step: str, *args: str):
    if git_step_failed(step, run_git(repo_root, *args)):
        raise RuntimeError(f"Notes git setup failed at {step}")


@app.task(name=SETUP_GIT_NOTES)
@tracked_task
def setup_git_notes(origin: str | None, email: str, name: str, branch: str | None = None, change_destination: bool = False):
    """Make the vault a repo committing as this author, with ``origin`` as its remote, then sync it.

    Existing history is preserved. Explicitly confirmed destination changes
    rename the current branch and update its upstream without force-pushing.
    """
    repo_root = settings.NOTES_STORAGE_DIR
    if branch is not None:
        branch = check_branch(branch)
    with git_notes_lock("tracking"):
        created = not (repo_root / ".git").exists()
        if created:
            repo_root.mkdir(parents=True, exist_ok=True)
            setup_step(repo_root, "init", "init", "--quiet", "-b", "main")
        if change_destination and (blocked := repo_blocked(repo_root)):
            raise RuntimeError(f"Notes git setup blocked: {blocked}")
        if branch and change_destination:
            setup_step(repo_root, "branch", "branch", "-m", branch)
        if change_destination and origin:
            vault = read_vault_git(repo_root)
            remote = vault.remote_name or "origin"
            if remote in vault.remotes:
                # Remove stale tracking refs and push URLs belonging to the old destination.
                setup_step(repo_root, "remote", "remote", "remove", remote)
            setup_step(repo_root, "remote", "remote", "add", remote, "--", origin)
        setup_step(repo_root, "config", "config", "--", "user.email", email)
        setup_step(repo_root, "config", "config", "--", "user.name", name)
        if origin and not run_git(repo_root, "remote").stdout.strip():
            setup_step(repo_root, "remote", "remote", "add", "origin", "--", origin)
        if change_destination:
            vault = read_vault_git(repo_root)
            remote = vault.remote_name or ("origin" if "origin" in vault.remotes else None)
            if remote and vault.branch:
                setup_step(repo_root, "upstream", "config", f"branch.{vault.branch}.remote", remote)
                setup_step(repo_root, "upstream", "config", f"branch.{vault.branch}.merge", f"refs/heads/{vault.branch}")
        # Not `created`: a first run that failed after init leaves a repo with no commit.
        if not head_commit(repo_root):
            setup_step(repo_root, "add", "add", "--all")
            setup_step(repo_root, "commit", "commit", "--quiet", "--allow-empty", "-m", "Initial commit")

    try:
        with git_tracking(repo_root) as sync:
            pass
        if sync.failed_step:
            return {"status": "sync_failed", "failed_step": sync.failed_step}
    except VaultBusy as e:
        return {"status": "sync_failed", "failed_step": e.step}
    return {"status": "created" if created else "updated"}


@app.task(name=TRACK_GIT_CHANGES)
@tracked_task
def track_git_changes():
    """Track git changes by noting current commit, pulling new commits, and listing changed files."""
    logger.info("Tracking git changes")

    repo_root = settings.NOTES_STORAGE_DIR
    if not (repo_root / ".git").exists():
        logger.warning("Git repository not found")
        return {"status": "no_git_repo"}

    try:
        with git_tracking(repo_root) as sync:
            pass
    except VaultBusy as e:
        return {"status": "sync_failed", "failed_step": e.step}
    latest_commit = head_commit(repo_root)

    if sync.failed_step:
        status = "sync_failed"
    elif sync.before != latest_commit:
        status = "success"
    else:
        status = "no_changes"
    return {
        "status": status,
        "failed_step": sync.failed_step,
        "current_commit": sync.before,
        "latest_commit": latest_commit,
        "changed_files": sync.changed,
    }


def sync_due(last_at: datetime | None, interval: int, now: datetime) -> bool:
    return last_at is None or (now - last_at).total_seconds() >= interval


@app.task(name=SYNC_NOTES_WHEN_DUE)
def sync_notes_when_due():
    """Queue a vault sync once the interval (a server setting) has passed since the last one."""
    if not (settings.NOTES_STORAGE_DIR / ".git").exists():
        return {"status": "no_git_repo"}
    interval = instance_settings.value(instance_settings.NOTES_SYNC_INTERVAL)
    status = last_sync()
    last_at = datetime.fromisoformat(status["at"]) if status else None
    if not sync_due(last_at, interval, datetime.now(timezone.utc)):
        return {"status": "not_due"}
    client = redis.from_url(settings.REDIS_URL)
    if not client.set(SYNC_QUEUED_KEY, "1", nx=True, ex=min(interval, SYNC_QUEUED_TTL)):
        return {"status": "already_queued"}
    track_git_changes.delay()  # type: ignore[attr-defined]
    return {"status": "queued"}
