from typing import cast
from celery import Task

import json
import subprocess
import uuid
import pytest
import pathlib
import redis
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from memory.common.db.models import Note
from memory.common.db.models.source_item import Chunk
from memory.workers.tasks import notes
from memory.common.content_processing import create_content_hash
from memory.common import notes_git, settings
from sqlalchemy.exc import IntegrityError


def _make_mock_chunk(source_id: int) -> Chunk:
    """Create a mock chunk for testing with a unique ID."""
    return Chunk(
        id=str(uuid.uuid4()),
        content="test chunk content",
        embedding_model="test-model",
        vector=[0.1] * 1024,
        item_metadata={"source_id": source_id, "tags": ["test"]},
        collection_name="text",
    )


@pytest.fixture
def mock_make_session(db_session):
    """Mock make_session and embedding functions for note task tests."""

    @contextmanager
    def _mock_session():
        yield db_session

    with patch("memory.workers.tasks.notes.make_session", _mock_session):
        with patch(
            "memory.common.embedding.embed_source_item",
            side_effect=lambda item, *_: [_make_mock_chunk(item.id or 1)],
        ):
            with patch("memory.common.content_processing.push_to_qdrant"):
                yield db_session


@pytest.fixture
def mock_note_data():
    """Mock note data for testing."""
    return {
        "subject": "Test Note Subject",
        "content": "This is test note content with enough text to be processed and embedded.",
        "filename": "notes/test_note.md",
        "note_type": "observation",
        "confidences": {"observation_accuracy": 0.8},
        "tags": ["test", "note"],
    }


@pytest.fixture
def mock_minimal_note():
    """Mock note with minimal required data."""
    return {
        "subject": "Minimal Note",
        "content": "Minimal content",
    }


@pytest.fixture
def mock_empty_note():
    """Mock note with empty content."""
    return {
        "subject": "Empty Note",
        "content": "",
    }


def test_sync_note_success(mock_note_data, mock_make_session, qdrant):
    """Test successful note synchronization."""
    result = notes.sync_note(**mock_note_data)
    mock_make_session.commit()

    # Verify the Note was created in the database
    note = mock_make_session.query(Note).filter_by(subject="Test Note Subject").first()
    assert note is not None
    assert note.subject == "Test Note Subject"
    assert (
        note.content
        == "This is test note content with enough text to be processed and embedded."
    )
    assert note.modality == "text"
    assert note.mime_type == "text/markdown"
    assert note.note_type == "observation"
    assert note.confidence_dict == {"observation_accuracy": 0.8}
    assert note.filename is not None
    assert note.tags == ["test", "note"]

    # Verify the result - updated to match actual return format
    assert result == {
        "note_id": note.id,
        "title": "Test Note Subject",
        "status": "processed",
        "chunks_count": 1,
        "embed_status": "STORED",
        "content_length": 93,
    }


def test_sync_note_minimal_data(mock_minimal_note, mock_make_session, qdrant):
    """Test note sync with minimal required data."""
    result = notes.sync_note(**mock_minimal_note)

    note = mock_make_session.query(Note).filter_by(subject="Minimal Note").first()
    assert note is not None
    assert note.subject == "Minimal Note"
    assert note.content == "Minimal content"
    assert note.note_type is None
    assert note.confidence_dict == {}
    assert note.tags == []  # Default empty list
    # clean_filename sanitizes spaces / unsafe chars to underscores.
    assert note.filename is not None and "Minimal_Note.md" in note.filename

    # Updated to match actual return format
    assert result == {
        "note_id": note.id,
        "title": "Minimal Note",
        "status": "processed",
        "chunks_count": 1,
        "embed_status": "STORED",
        "content_length": 31,
    }


def test_sync_note_empty_content(mock_empty_note, mock_make_session, qdrant):
    """Test note sync with empty content."""
    result = notes.sync_note(**mock_empty_note)

    # Note is still created even with empty content
    note = mock_make_session.query(Note).filter_by(subject="Empty Note").first()
    assert note is not None
    assert note.subject == "Empty Note"
    assert note.content == ""

    # Updated to match actual return format
    assert result == {
        "note_id": note.id,
        "title": "Empty Note",
        "status": "processed",
        "chunks_count": 1,
        "embed_status": "STORED",
        "content_length": 14,
    }


def test_sync_note_already_exists(mock_note_data, mock_make_session):
    """Test note sync when content already exists."""
    # Create the content text the same way sync_note does
    text = Note.as_text(mock_note_data["content"], mock_note_data["subject"])
    sha256 = create_content_hash(text)

    # Add existing note with same content hash but different filename to avoid file conflicts
    existing_note = Note(
        subject="Existing Note",
        content=mock_note_data["content"],
        sha256=sha256,
        modality="text",
        tags=["existing"],
        mime_type="text/markdown",
        size=len(text.encode("utf-8")),
        embed_status="RAW",
        filename="notes/existing_note.md",
    )
    mock_make_session.add(existing_note)
    mock_make_session.commit()

    result = notes.sync_note(**mock_note_data)

    # Updated to match actual return format for already_exists case
    assert result == {
        "note_id": existing_note.id,
        "title": "Existing Note",
        "status": "already_exists",
        "chunks_count": 0,  # Existing note has no chunks
        "embed_status": "RAW",  # Existing note has RAW status
    }

    # Verify no duplicate was created
    notes_with_hash = mock_make_session.query(Note).filter_by(sha256=sha256).all()
    assert len(notes_with_hash) == 1


def test_sync_note_edit(mock_note_data, mock_make_session):
    """Test note sync when content already exists."""
    # Create the content text the same way sync_note does
    text = Note.as_text(mock_note_data["content"], mock_note_data["subject"])
    sha256 = create_content_hash(text)

    # Add existing note with same content hash but different filename to avoid file conflicts
    existing_note = Note(
        subject="Existing Note",
        content=mock_note_data["content"],
        sha256=sha256,
        modality="text",
        tags=["existing"],
        mime_type="text/markdown",
        size=len(text.encode("utf-8")),
        embed_status="RAW",
        filename="notes/test_note.md",
    )
    existing_note.update_confidences(
        {"observation_accuracy": 0.2, "predictive_value": 0.3}
    )
    mock_make_session.add(existing_note)
    mock_make_session.commit()

    result = notes.sync_note(
        **{**mock_note_data, "content": "bla bla bla", "subject": "blee"}
    )

    assert result == {
        "note_id": existing_note.id,
        "status": "processed",
        "chunks_count": 1,
        "embed_status": "STORED",
        "title": "blee",
        "content_length": 19,
    }

    # Verify no duplicate was created
    assert len(mock_make_session.query(Note).all()) == 1
    mock_make_session.refresh(existing_note)
    assert existing_note.content == "bla bla bla"  # type: ignore
    assert existing_note.confidence_dict == {
        "observation_accuracy": 0.8,
        "predictive_value": 0.3,
    }


@pytest.mark.parametrize(
    "note_type,confidence,tags",
    [
        ("observation", 0.9, ["high-confidence", "important"]),
        ("reflection", 0.6, ["personal", "thoughts"]),
        (None, 0.5, []),
        ("meeting", 1.0, ["work", "notes", "2024"]),
    ],
)
def test_sync_note_parameters(note_type, confidence, tags, mock_make_session, qdrant):
    """Test note sync with various parameter combinations."""
    result = notes.sync_note(
        subject=f"Test Note {note_type}",
        content="Test content for parameter testing",
        note_type=note_type,
        confidences={"observation_accuracy": confidence},
        tags=tags,
    )

    note = mock_make_session.query(Note).filter_by(subject=f"Test Note {note_type}").first()
    assert note is not None
    assert note.note_type == note_type
    assert note.confidence_dict == {"observation_accuracy": confidence}
    assert note.tags == tags

    # Updated to match actual return format
    text = f"# Test Note {note_type}\n\nTest content for parameter testing"
    assert result == {
        "note_id": note.id,
        "title": f"Test Note {note_type}",
        "status": "processed",
        "chunks_count": 1,
        "embed_status": "STORED",
        "content_length": len(text.encode("utf-8")),
    }


def test_sync_note_content_hash_consistency(mock_make_session):
    """Test that content hash is calculated consistently."""
    note_data = {
        "subject": "Hash Test",
        "content": "Consistent content for hashing",
        "tags": ["hash-test"],
    }

    # Sync the same note twice
    result1 = notes.sync_note(**note_data)
    result2 = notes.sync_note(**note_data)

    # First should succeed, second should detect existing
    assert result1["status"] == "processed"
    assert result2["status"] == "already_exists"
    assert result1["note_id"] == result2["note_id"]

    # Verify only one note exists in database
    notes_in_db = mock_make_session.query(Note).filter_by(subject="Hash Test").all()
    assert len(notes_in_db) == 1


def test_note_as_text_method():
    """Test the Note.as_text static method used in sync_note."""
    content = "This is the note content"
    subject = "Note Subject"

    text = Note.as_text(content, subject)

    # The method should combine subject and content appropriately
    assert subject in text
    assert content in text


def test_sync_note_with_long_content(mock_make_session, qdrant):
    """Test sync_note with longer content to ensure proper chunking."""
    long_content = "This is a longer note content. " * 100  # Make it substantial
    result = notes.sync_note(
        subject="Long Note",
        content=long_content,
        tags=["long", "test"],
    )

    note = mock_make_session.query(Note).filter_by(subject="Long Note").first()
    assert note is not None
    assert note.content == long_content
    assert result["status"] == "processed"
    chunks_count = result["chunks_count"]
    assert isinstance(chunks_count, int)
    assert chunks_count > 0


def test_sync_note_unicode_content(mock_make_session, qdrant):
    """Test sync_note with unicode content."""
    unicode_content = "This note contains unicode: 你好世界 🌍 математика"
    result = notes.sync_note(
        subject="Unicode Note",
        content=unicode_content,
    )

    note = mock_make_session.query(Note).filter_by(subject="Unicode Note").first()
    assert note is not None
    assert note.content == unicode_content
    assert result["status"] == "processed"


# ----- Path-traversal hardening for sync_note + Note.save_to_file -----
#
# `notes.upsert` (the MCP tool) already validates filenames, but
# `track_git_changes` re-fans-out into `sync_note` with
# paths pulled from the filesystem / git remote. A `.md` file named
# `../../etc/cron.d/poc.md` would otherwise be written outside
# NOTES_STORAGE_DIR — arbitrary host file write at worker process privs.


@pytest.mark.parametrize(
    "bad_filename",
    [
        "../../etc/cron.d/poc",
        "../escape.md",
        "notes/../escape.md",
        "/../../etc/passwd",  # leading slash is stripped, then escapes
        "escape.md",  # FILE_STORAGE_DIR-relative but outside notes/
        "emails/escape.md",  # inside FILE_STORAGE_DIR but not under notes/
    ],
)
def test_sync_note_rejects_traversal_filename(bad_filename, tmp_path, caplog):
    """A path-traversal filename must be rejected before any DB or FS write."""
    from memory.common import settings

    # Anchor the resolution to a writable temp dir so the comparison is
    # deterministic regardless of host config.
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(exist_ok=True)

    with (
        patch.object(settings, "FILE_STORAGE_DIR", tmp_path),
        patch.object(settings, "NOTES_STORAGE_DIR", notes_dir),
    ):
        # No mock_make_session here on purpose — the validation must
        # raise before we ever try to open a DB session.
        with pytest.raises(ValueError):
            notes.sync_note(
                subject="poc",
                content="x",
                filename=bad_filename,
            )

    # Nothing should have been written.
    assert list(notes_dir.iterdir()) == []
    # Error records reach the appliance operator; filenames carry note titles.
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(m.startswith("Refusing to sync note") for m in errors)
    assert not any(bad_filename.lstrip("/") in m for m in errors)


def test_note_save_to_file_rejects_traversal_filename(tmp_path):
    """Defense in depth: Note.save_to_file refuses to write outside the dir."""
    from memory.common import settings

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(exist_ok=True)
    sentinel = tmp_path / "should-not-exist.md"

    note = Note(
        modality="text",
        mime_type="text/markdown",
        subject="poc",
        content="malicious",
        filename="notes/../should-not-exist.md",  # escapes notes_dir
    )

    with (
        patch.object(settings, "FILE_STORAGE_DIR", tmp_path),
        patch.object(settings, "NOTES_STORAGE_DIR", notes_dir),
    ):
        with pytest.raises(ValueError):
            note.save_to_file()

    assert not sentinel.exists()
    # The notes dir itself must be untouched.
    assert list(notes_dir.iterdir()) == []


def test_note_save_to_file_accepts_valid_relative_path(tmp_path):
    """Sanity: a normal relative path still works after the validator."""
    from memory.common import settings

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(exist_ok=True)

    note = Note(
        modality="text",
        mime_type="text/markdown",
        subject="ok",
        content="hello",
        filename="notes/subdir/ok.md",
    )

    with (
        patch.object(settings, "FILE_STORAGE_DIR", tmp_path),
        patch.object(settings, "NOTES_STORAGE_DIR", notes_dir),
    ):
        note.save_to_file()

    assert (notes_dir / "subdir" / "ok.md").read_text() == "hello"


@pytest.mark.parametrize(
    "subject",
    [
        "",  # empty string — clean_filename returns ""
        "!!!",  # all non-alphanumeric — clean_filename strips to ""
        "***---",
    ],
)
def test_note_save_to_file_empty_subject_uses_untitled_fallback(subject, tmp_path):
    """Empty/all-special subject must not produce ``notes/.md``."""
    from memory.common import settings

    notes_dir = tmp_path / "notes"
    notes_dir.mkdir(exist_ok=True)

    note = Note(
        modality="text",
        mime_type="text/markdown",
        subject=subject,
        content="content",
        filename=None,  # triggers the fallback branch
    )

    with (
        patch.object(settings, "FILE_STORAGE_DIR", tmp_path),
        patch.object(settings, "NOTES_STORAGE_DIR", notes_dir),
    ):
        note.save_to_file()

    assert note.filename == "notes/untitled.md"
    assert (notes_dir / "untitled.md").read_text() == "content"


def add_existing_note(
    session, filename: str, creator_id: int | None = None, content: str = "original"
) -> Note:
    note = Note(
        subject="Existing",
        content=content,
        sha256=create_content_hash(content),
        modality="text",
        mime_type="text/markdown",
        size=len(content),
        embed_status="RAW",
        filename=filename,
        creator_id=creator_id,
    )
    session.add(note)
    session.commit()
    return note


def test_sync_note_create_only_refuses_existing_filename(mock_note_data, mock_make_session):
    existing = add_existing_note(mock_make_session, mock_note_data["filename"])

    result = notes.sync_note(**mock_note_data, create_only=True)

    assert result == {"status": "already_exists", "note_id": existing.id}
    mock_make_session.refresh(existing)
    assert existing.content == "original"  # type: ignore


@pytest.mark.parametrize(
    "writer, admin, expected",
    [
        ("regular_user", False, "rewritten"),
        ("admin_user", True, "rewritten"),
        ("admin_user", False, "original"),
    ],
)
def test_sync_note_only_lets_the_owner_or_an_admin_overwrite(
    writer, admin, expected, request, mock_note_data, mock_make_session, regular_user, qdrant
):
    existing = add_existing_note(mock_make_session, mock_note_data["filename"], regular_user.id)

    notes.sync_note(
        **{**mock_note_data, "content": "rewritten"},
        creator_id=request.getfixturevalue(writer).id,
        admin=admin,
    )

    mock_make_session.refresh(existing)
    assert existing.content == expected  # type: ignore
    assert existing.creator_id == regular_user.id  # type: ignore


@pytest.mark.parametrize("create_only", [False, True])
def test_duplicate_note_does_not_expose_or_rewrite_another_users_note(
    mock_note_data, mock_make_session, regular_user, admin_user, create_only
):
    existing = add_existing_note(mock_make_session, mock_note_data["filename"], regular_user.id)
    existing.subject = mock_note_data["subject"]
    existing.content = mock_note_data["content"]
    existing.sha256 = create_content_hash(Note.as_text(mock_note_data["content"], mock_note_data["subject"]))
    mock_make_session.commit()

    with patch.object(notes, "write_note_file_or_defer") as write:
        result = notes.sync_note(
            **mock_note_data, creator_id=admin_user.id, admin=False, create_only=create_only
        )

    assert result == {"status": "forbidden"}
    write.assert_not_called()
    mock_make_session.refresh(existing)
    assert existing.creator_id == regular_user.id


def test_notes_cannot_share_a_filename(mock_note_data, db_session):
    add_existing_note(db_session, mock_note_data["filename"])

    with pytest.raises(IntegrityError, match="source_note_filename_key"):
        add_existing_note(db_session, mock_note_data["filename"], content="other")


def test_sync_note_retries_against_a_concurrently_created_note(
    mock_note_data, mock_make_session, regular_user, admin_user, qdrant
):
    real_process = notes.process_content_item
    competitor: list[Note] = []

    def lose_the_race(note, session):
        # Another worker commits the same filename between our lookup and our insert.
        session.rollback()
        competitor.append(add_existing_note(session, mock_note_data["filename"], admin_user.id))
        return real_process(note, session)

    calls = iter([lose_the_race, real_process])
    with patch.object(notes, "process_content_item", side_effect=lambda n, s: next(calls)(n, s)):
        result = notes.sync_note(**mock_note_data, creator_id=regular_user.id)

    assert result == {"status": "forbidden"}
    rows = mock_make_session.query(Note).filter(Note.filename == mock_note_data["filename"]).all()
    assert [(r.id, r.content) for r in rows] == [(competitor[0].id, "original")]


def test_sync_note_gives_up_on_a_second_filename_race(mock_note_data, mock_make_session):
    orig = Mock(diag=Mock(constraint_name=notes.NOTE_FILENAME_KEY))
    race = IntegrityError("INSERT", {}, orig)

    with patch.object(notes, "process_content_item", side_effect=race) as process:
        with pytest.raises(IntegrityError):
            notes.sync_note(**mock_note_data)

    assert process.call_count == 2


@pytest.mark.parametrize(
    "filename", ["notes/sub/../canon.md", "notes//canon.md", "/notes/canon", "notes/./canon.md"]
)
def test_sync_note_stores_the_canonical_filename(filename, mock_note_data, mock_make_session, qdrant):
    notes.sync_note(**{**mock_note_data, "filename": filename})

    assert [n.filename for n in mock_make_session.query(Note)] == ["notes/canon.md"]


def test_git_notes_lock_waits_for_current_holder():
    with ExitStack() as holder:
        holder.enter_context(notes.distributed_lock("memory:lock:git_notes:tracking", 60))
        with patch.object(notes.time, "sleep", side_effect=lambda _: holder.close()) as sleep:
            with notes.git_notes_lock("tracking"):
                pass

    sleep.assert_called_once()


def test_git_notes_lock_gives_up_after_wait():
    with notes.git_notes_lock("tracking"):
        with pytest.raises(RuntimeError, match="Could not acquire git notes lock"):
            with notes.git_notes_lock("tracking", wait_seconds=0):
                pass


# ----- Git sync against real repos: a bare "remote", the vault, and another clone -----


def git(repo: pathlib.Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return res.stdout.strip()


def configure_identity(repo: pathlib.Path):
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")


def commit_file(repo: pathlib.Path, name: str, text: str):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", f"add {name}")


def make_vault(tmp_path: pathlib.Path, branch: str) -> tuple[pathlib.Path, pathlib.Path]:
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", branch, str(remote))
    vault = settings.NOTES_STORAGE_DIR
    git(vault, "init", "-q", "-b", branch)
    configure_identity(vault)
    git(vault, "remote", "add", "origin", str(remote))
    commit_file(vault, "existing.md", "existing")
    git(vault, "push", "-q", "-u", "origin", branch)
    mark_indexed(vault)
    return vault, remote


def mark_indexed(vault: pathlib.Path):
    redis.from_url(settings.REDIS_URL).set(
        notes.GIT_INDEXED_COMMIT_KEY, git(vault, "rev-parse", "HEAD")
    )


def push_from_other_clone(tmp_path: pathlib.Path, remote: pathlib.Path, name: str, text: str):
    other = tmp_path / "other"
    if not other.exists():
        git(tmp_path, "clone", "-q", str(remote), str(other))
        configure_identity(other)
    git(other, "pull", "-q")
    commit_file(other, name, text)
    git(other, "push", "-q")


def save_note(vault: pathlib.Path, name: str, text: str):
    with notes.git_tracking(vault, f"Sync note {name}") as sync:
        sync.track(vault / name)
        (vault / name).write_text(text)


def remote_files(remote: pathlib.Path, branch: str) -> set[str]:
    return set(git(remote, "ls-tree", "--name-only", branch).splitlines())


def head_files(vault: pathlib.Path) -> set[str]:
    return set(git(vault, "ls-tree", "--name-only", "HEAD").splitlines())


def last_status() -> dict:
    status = redis.from_url(settings.REDIS_URL).get(notes.GIT_SYNC_STATUS_KEY)
    assert isinstance(status, (str, bytes))
    return json.loads(status)


@pytest.fixture(autouse=True)
def queued_reindexes():
    with patch.object(notes.reindex_note, "delay") as delay:
        yield delay


@pytest.fixture
def reindexed(queued_reindexes):
    """Filenames git sync queued for reindexing."""
    return lambda: [c.args[0] for c in queued_reindexes.call_args_list]


def run_queued_reindexes(queued_reindexes) -> list[str]:
    """Run the queued reindexes, as a worker would once the git lock is free."""
    return [notes.reindex_note(c.args[0])["status"] for c in queued_reindexes.call_args_list]


@pytest.mark.parametrize("branch", ["main", "master"])
def test_git_tracking_pushes_to_the_current_branch(tmp_path, branch):
    vault, remote = make_vault(tmp_path, branch)

    save_note(vault, "new.md", "new")

    assert remote_files(remote, branch) == {"existing.md", "new.md"}
    assert git(vault, "status", "--porcelain") == ""
    assert last_status()["ok"] is True


@pytest.mark.parametrize("branch", ["main", "master"])
def test_git_tracking_merges_local_commits_with_remote_edits(tmp_path, branch, reindexed):
    vault, remote = make_vault(tmp_path, branch)
    commit_file(vault, "local-only.md", "unpushed")
    push_from_other_clone(tmp_path, remote, "elsewhere.md", "pushed elsewhere")

    save_note(vault, "new.md", "new")

    assert remote_files(remote, branch) == {
        "existing.md",
        "local-only.md",
        "elsewhere.md",
        "new.md",
    }
    assert git(vault, "rev-parse", "HEAD") == git(remote, "rev-parse", branch)
    assert last_status()["ok"] is True


def test_git_tracking_reindexes_notes_the_pull_changed(tmp_path, reindexed):
    vault, remote = make_vault(tmp_path, "master")
    push_from_other_clone(tmp_path, remote, "elsewhere.md", "pushed elsewhere")
    push_from_other_clone(tmp_path, remote, "café.md", "non-ascii name")
    push_from_other_clone(tmp_path, remote, "ours.md", "remote version")

    save_note(vault, "ours.md", "memory version")

    assert sorted(reindexed()) == ["notes/café.md", "notes/elsewhere.md"]
    assert (vault / "ours.md").read_text() == "memory version"


def test_git_tracking_keeps_local_commits_when_remote_is_unreachable(tmp_path, caplog):
    vault, remote = make_vault(tmp_path, "master")
    commit_file(vault, "local-only.md", "unpushed")
    git(vault, "remote", "set-url", "origin", str(tmp_path / "gone.git"))

    with caplog.at_level("WARNING", logger="memory.workers.tasks.notes"):
        save_note(vault, "new.md", "new")
        save_note(vault, "newer.md", "newer")

    assert head_files(vault) == {"existing.md", "local-only.md", "new.md", "newer.md"}
    assert git(vault, "status", "--porcelain") == ""
    assert remote_files(remote, "master") == {"existing.md"}
    status = last_status()
    assert (status["ok"], status["failed_step"]) == (False, "fetch")
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warnings
    assert not any(str(tmp_path) in m or ".md" in m for m in warnings)


def test_git_tracking_pushes_kept_commits_once_remote_is_back(tmp_path, reindexed):
    vault, remote = make_vault(tmp_path, "master")
    git(vault, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    save_note(vault, "offline.md", "offline")
    git(vault, "remote", "set-url", "origin", str(remote))
    push_from_other_clone(tmp_path, remote, "elsewhere.md", "pushed elsewhere")

    save_note(vault, "online.md", "online")

    assert remote_files(remote, "master") == {
        "existing.md",
        "offline.md",
        "elsewhere.md",
        "online.md",
    }
    assert last_status()["ok"] is True


@pytest.mark.parametrize("remote_edits", [[], [("elsewhere.md", "pushed elsewhere")]])
def test_git_tracking_leaves_outside_edits_alone(tmp_path, remote_edits, reindexed):
    vault, remote = make_vault(tmp_path, "master")
    commit_file(vault, "edited.md", "original")
    commit_file(vault, "deleted.md", "original")
    git(vault, "push", "-q")
    for name, text in remote_edits:
        push_from_other_clone(tmp_path, remote, name, text)
    (vault / "edited.md").write_text("edited outside Memory")
    (vault / "deleted.md").unlink()
    (vault / "untracked.md").write_text("draft")
    before = git(vault, "status", "--porcelain")

    save_note(vault, "new.md", "new")

    assert git(vault, "status", "--porcelain") == before
    assert (vault / "edited.md").read_text() == "edited outside Memory"
    assert git(vault, "stash", "list") == ""
    assert remote_files(remote, "master") == {
        "existing.md",
        "edited.md",
        "deleted.md",
        "new.md",
        *(name for name, _ in remote_edits),
    }
    assert last_status()["ok"] is True


def test_git_tracking_keeps_local_commits_when_merge_conflicts(tmp_path):
    vault, remote = make_vault(tmp_path, "master")
    commit_file(vault, "existing.md", "local version")
    local_head = git(vault, "rev-parse", "HEAD")
    push_from_other_clone(tmp_path, remote, "existing.md", "remote version")

    save_note(vault, "new.md", "new")

    assert git(vault, "merge-base", "--is-ancestor", local_head, "HEAD") == ""
    assert (vault / "existing.md").read_text() == "local version"
    assert git(vault, "status", "--porcelain") == ""
    assert last_status()["failed_step"] == "merge_conflict"
    assert last_status()["hint"]


def test_git_tracking_never_conflicts_an_uncommitted_edit(tmp_path):
    """Remote and user both changed a file the user hasn't committed: leave it, and say so."""
    vault, remote = make_vault(tmp_path, "master")
    commit_file(vault, "local-only.md", "unpushed")
    push_from_other_clone(tmp_path, remote, "existing.md", "remote version")
    (vault / "existing.md").write_text("user's uncommitted version")

    save_note(vault, "first.md", "first")
    save_note(vault, "second.md", "second")

    assert (vault / "existing.md").read_text() == "user's uncommitted version"
    assert git(vault, "status", "--porcelain") == "M existing.md"
    assert git(vault, "ls-files", "--unmerged") == ""
    assert git(vault, "stash", "list") == ""
    assert {"local-only.md", "first.md", "second.md"} <= head_files(vault)
    assert last_status()["failed_step"] == "merge_conflict"
    assert last_status()["hint"]


def test_git_tracking_commits_users_edit_before_overwriting_it(tmp_path):
    vault, remote = make_vault(tmp_path, "master")
    (vault / "existing.md").write_text("existing\nuser's uncommitted paragraph")

    save_note(vault, "existing.md", "memory version")

    assert (vault / "existing.md").read_text() == "memory version"
    assert git(remote, "show", "master~1:existing.md") == "existing\nuser's uncommitted paragraph"
    assert git(remote, "show", "master:existing.md") == "memory version"
    assert git(vault, "status", "--porcelain") == ""


def detach_head(vault: pathlib.Path, remote: pathlib.Path, tmp_path: pathlib.Path):
    git(vault, "checkout", "-q", "--detach")


def stop_mid_rebase(vault: pathlib.Path, remote: pathlib.Path, tmp_path: pathlib.Path):
    commit_file(vault, "existing.md", "local version")
    push_from_other_clone(tmp_path, remote, "existing.md", "remote version")
    git(vault, "fetch", "-q")
    subprocess.run(["git", "-C", str(vault), "rebase", "origin/master"], capture_output=True)


def stop_mid_merge(vault: pathlib.Path, remote: pathlib.Path, tmp_path: pathlib.Path):
    stop_mid_rebase(vault, remote, tmp_path)
    git(vault, "rebase", "--abort")
    subprocess.run(["git", "-C", str(vault), "merge", "origin/master"], capture_output=True)


@pytest.mark.parametrize(
    "block, step",
    [
        (detach_head, "detached"),
        (stop_mid_rebase, "operation_in_progress"),
        (stop_mid_merge, "operation_in_progress"),
    ],
)
def test_git_tracking_never_writes_into_a_blocked_vault(tmp_path, block, step):
    vault, remote = make_vault(tmp_path, "master")
    block(vault, remote, tmp_path)
    (vault / "existing.md").write_text("user's resolution")
    head = git(vault, "rev-parse", "HEAD")
    before = git(vault, "status", "--porcelain")

    with pytest.raises(notes.VaultBusy):
        save_note(vault, "existing.md", "memory version")

    assert (vault / "existing.md").read_text() == "user's resolution"
    assert git(vault, "status", "--porcelain") == before
    assert git(vault, "rev-parse", "HEAD") == head
    assert remote_files(remote, "master") == {"existing.md"}
    assert last_status()["failed_step"] == step


@pytest.fixture
def deferred_writes():
    """Arguments of the file writes sync_note deferred while the vault was busy."""
    with patch.object(notes.write_note_file_when_free, "apply_async") as apply_async:
        yield lambda: [c.args[0] for c in apply_async.call_args_list]


def note_args(content: str, filename: str | None = "notes/n.md", subject: str = "n") -> dict:
    return {"subject": subject, "content": content, "filename": filename}


def test_sync_note_defers_the_file_write_while_vault_is_busy(
    mock_make_session, qdrant, tmp_path, deferred_writes
):
    vault, remote = make_vault(tmp_path, "master")
    notes.sync_note(**note_args("v1"))
    git(vault, "checkout", "-q", "--detach")

    notes.sync_note(**note_args("v2"))

    row = mock_make_session.query(Note).filter(Note.filename == "notes/n.md").one()
    assert row.content == "v2"
    assert (vault / "n.md").read_text() == "v1"
    assert last_status()["failed_step"] == "detached"
    [write] = deferred_writes()

    git(vault, "checkout", "-q", "master")
    assert notes.write_note_file_when_free(*write)["status"] == "written"

    assert (vault / "n.md").read_text() == "v2"
    assert git(remote, "show", "master:n.md") == "v2"


def test_deferred_write_keeps_waiting_while_vault_is_busy(
    mock_make_session, qdrant, tmp_path, deferred_writes
):
    vault, _ = make_vault(tmp_path, "master")
    git(vault, "checkout", "-q", "--detach")
    notes.sync_note(**note_args("v1"))
    [write] = deferred_writes()

    with pytest.raises(notes.VaultBusy):
        notes.write_note_file_when_free(*write)

    assert not (vault / "n.md").exists()


def test_deferred_write_never_replays_older_content(
    mock_make_session, qdrant, tmp_path, deferred_writes
):
    vault, _ = make_vault(tmp_path, "master")
    notes.sync_note(**note_args("v1"))
    git(vault, "checkout", "-q", "--detach")
    notes.sync_note(**note_args("v2"))
    git(vault, "checkout", "-q", "master")
    notes.sync_note(**note_args("v3"))
    [stale_write] = deferred_writes()

    # Celery may deliver it any number of times; every delivery must be a no-op.
    results = [notes.write_note_file_when_free(*stale_write) for _ in range(2)]

    assert [r["status"] for r in results] == ["superseded", "superseded"]
    assert (vault / "n.md").read_text() == "v3"
    row = mock_make_session.query(Note).filter(Note.filename == "notes/n.md").one()
    assert row.content == "v3"


def test_sync_note_gives_filenameless_notes_their_own_rows(
    mock_make_session, qdrant, tmp_path, deferred_writes
):
    vault, _ = make_vault(tmp_path, "master")
    git(vault, "checkout", "-q", "--detach")

    notes.sync_note(**note_args("ALPHA BODY", filename=None, subject="alpha"))
    notes.sync_note(**note_args("BETA BODY", filename=None, subject="beta"))

    rows = {(n.filename, n.content) for n in mock_make_session.query(Note).all()}
    assert rows == {("notes/alpha.md", "ALPHA BODY"), ("notes/beta.md", "BETA BODY")}


def test_sync_note_dedupe_hit_keeps_a_users_committed_edit(mock_make_session, qdrant, tmp_path):
    vault, _ = make_vault(tmp_path, "master")
    notes.sync_note(**note_args("v1"))
    commit_file(vault, "n.md", "user edit committed in the vault")

    notes.sync_note(**note_args("v1"))

    assert (vault / "n.md").read_text() == "user edit committed in the vault"


def test_sync_note_failures_still_notify(tmp_path):
    with patch("memory.common.content_processing.notify_task_failure") as notify:
        with pytest.raises(ValueError):
            notes.sync_note(**note_args("x", filename="../escape.md"))

    notify.assert_called_once()


def test_git_tracking_reindexes_notes_committed_in_the_vault(tmp_path, reindexed):
    vault, _ = make_vault(tmp_path, "master")
    save_note(vault, "first.md", "first")
    commit_file(vault, "by-hand.md", "committed in the vault")

    save_note(vault, "second.md", "second")

    assert reindexed() == ["notes/by-hand.md"]


def test_git_tracking_unstages_its_paths_when_commit_fails(tmp_path):
    vault, _ = make_vault(tmp_path, "master")
    hook = vault / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)

    save_note(vault, "new.md", "new")

    assert git(vault, "status", "--porcelain") == "?? new.md"
    assert last_status()["failed_step"] == "commit"


def test_git_tracking_skips_pulled_files_it_cannot_reindex(tmp_path, reindexed):
    vault, remote = make_vault(tmp_path, "master")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(remote), str(other))
    configure_identity(other)
    (other / "escape.md").symlink_to("/etc/hosts")
    (other / "binary.md").write_bytes(b"\xff\xfe not utf-8")
    (other / "good.md").write_text("good")
    git(other, "add", ".")
    git(other, "commit", "-q", "-m", "mixed")
    git(other, "push", "-q")

    save_note(vault, "new.md", "new")
    with patch.object(notes, "upsert_note") as upsert:
        statuses = [notes.reindex_note(f)["status"] for f in ["notes/binary.md", "notes/escape.md"]]
        notes.reindex_note("notes/good.md")

    assert sorted(reindexed()) == ["notes/binary.md", "notes/escape.md", "notes/good.md"]
    assert statuses == ["unreadable", "unreadable"]
    upsert.assert_called_once_with(
        subject="good", content="good", filename="notes/good.md", save_to_file=False
    )
    assert "new.md" in remote_files(remote, "master")
    assert last_status()["ok"] is True


def test_git_tracking_without_upstream_commits_locally(tmp_path):
    vault = settings.NOTES_STORAGE_DIR
    git(vault, "init", "-q", "-b", "main")
    configure_identity(vault)
    commit_file(vault, "existing.md", "existing")

    save_note(vault, "new.md", "new")

    assert head_files(vault) == {"existing.md", "new.md"}
    assert last_status()["failed_step"] == "no_upstream"


def test_git_tracking_without_repo_just_writes():
    save_note(settings.NOTES_STORAGE_DIR, "new.md", "new")

    assert (settings.NOTES_STORAGE_DIR / "new.md").read_text() == "new"


def test_setup_git_notes_pushes_and_later_syncs_follow(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    (settings.NOTES_STORAGE_DIR / "existing.md").write_text("existing")

    notes.setup_git_notes(str(remote), "test@example.com", "Test")
    save_note(settings.NOTES_STORAGE_DIR, "new.md", "new")

    assert remote_files(remote, "main") == {"existing.md", "new.md"}


@pytest.mark.parametrize("remote_name", ["origin", "upstream"])
def test_setup_git_notes_never_rewrites_an_existing_vault(tmp_path, remote_name):
    vault, remote = make_vault(tmp_path, "master")
    if remote_name != "origin":
        git(vault, "remote", "rename", "origin", remote_name)
    history = git(vault, "rev-parse", "HEAD")
    (vault / "draft.md").write_text("draft")

    result = notes.setup_git_notes(str(tmp_path / "elsewhere.git"), "new@example.com", "New Name")

    assert result["status"] == "updated"
    assert git(vault, "remote") == remote_name
    assert git(vault, "remote", "get-url", remote_name) == str(remote)
    assert git(vault, "rev-parse", "HEAD") == history
    assert git(vault, "status", "--porcelain") == "?? draft.md"
    assert (git(vault, "config", "user.name"), git(vault, "config", "user.email")) == (
        "New Name",
        "new@example.com",
    )


def test_setup_git_notes_retried_after_a_failed_first_run_commits_the_notes(tmp_path):
    vault = settings.NOTES_STORAGE_DIR
    (vault / "existing.md").write_text("existing")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    real_run_git = notes.run_git

    def remote_add_fails(repo_root, *args, **kwargs):
        if args[:2] == ("remote", "add"):
            return subprocess.CompletedProcess(args, 1, "", "")
        return real_run_git(repo_root, *args, **kwargs)

    with patch.object(notes, "run_git", remote_add_fails), pytest.raises(RuntimeError):
        notes.setup_git_notes(str(remote), "test@example.com", "Test")
    notes.setup_git_notes(str(remote), "test@example.com", "Test")

    assert remote_files(remote, "main") == {"existing.md"}
    assert last_status()["ok"] is True


def test_setup_git_notes_takes_an_author_name_that_looks_like_an_option(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))

    notes.setup_git_notes(str(remote), "test@example.com", "--unset")

    assert git(settings.NOTES_STORAGE_DIR, "config", "user.name") == "--unset"


def test_a_sole_remote_not_named_origin_is_the_vaults_upstream(tmp_path):
    vault, remote = make_vault(tmp_path, "main")
    git(vault, "remote", "rename", "origin", "backup")
    git(vault, "branch", "--unset-upstream")

    result = notes.setup_git_notes(None, "test@example.com", "Test")
    save_note(vault, "new.md", "new")

    assert result["status"] == "updated"
    assert last_status()["ok"] is True
    assert "new.md" in remote_files(remote, "main")


def test_setup_git_notes_adds_a_remote_to_a_vault_without_one(tmp_path):
    vault = settings.NOTES_STORAGE_DIR
    git(vault, "init", "-q", "-b", "main")
    configure_identity(vault)
    commit_file(vault, "kept.md", "kept")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))

    notes.setup_git_notes(str(remote), "test@example.com", "Test")

    assert remote_files(remote, "main") == {"kept.md"}
    assert git(vault, "log", "--format=%s") == "add kept.md"
    assert last_status()["ok"] is True


def test_a_push_with_the_token_set_never_stores_it(tmp_path, monkeypatch):
    vault, remote = make_vault(tmp_path, "main")
    https_url = "https://git.example.com/me/notes.git"
    # The vault shows an https remote, which git rewrites to the local bare repo.
    git(vault, "remote", "set-url", "origin", https_url)
    git(vault, "config", f"url.{remote}.insteadOf", https_url)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(settings, "NOTES_GIT_TOKEN", "ghp_secret1234")

    save_note(vault, "new.md", "new")

    assert "new.md" in remote_files(remote, "main")
    assert "ghp_secret1234" not in (vault / ".git" / "config").read_text()
    assert not (tmp_path / "home" / ".git-credentials").exists()
    assert not (tmp_path / "home" / ".config" / "git" / "credentials").exists()


def test_track_git_changes_without_repo():
    assert notes.track_git_changes() == {"status": "no_git_repo"}


def test_track_git_changes_pulls_remote_notes(tmp_path, reindexed):
    vault, remote = make_vault(tmp_path, "main")
    commit_file(vault, "local-only.md", "unpushed")
    (vault / "draft.md").write_text("draft")
    push_from_other_clone(tmp_path, remote, "elsewhere.md", "pushed elsewhere")

    result = notes.track_git_changes()

    assert result["status"] == "success"
    assert result["changed_files"] == ["elsewhere.md", "local-only.md"]
    assert reindexed() == ["notes/elsewhere.md", "notes/local-only.md"]
    assert "local-only.md" in remote_files(remote, "main")
    assert (vault / "draft.md").read_text() == "draft"


def test_track_git_changes_reports_unreachable_remote(tmp_path, reindexed):
    vault, _ = make_vault(tmp_path, "master")
    git(vault, "remote", "set-url", "origin", str(tmp_path / "gone.git"))

    result = notes.track_git_changes()

    assert (result["status"], result["failed_step"]) == ("sync_failed", "fetch")
    assert last_status()["failed_step"] == "fetch"
    assert reindexed() == []


def test_sync_note_rewrites_missing_file_on_dedupe_hit(mock_note_data, mock_make_session, qdrant):
    notes.sync_note(**mock_note_data)
    path = settings.FILE_STORAGE_DIR / mock_note_data["filename"]
    path.unlink()

    result = notes.sync_note(**mock_note_data)

    assert result["status"] == "already_exists"
    assert path.read_text() == mock_note_data["content"]


def note_row(db_session, filename: str = "notes/n.md") -> str:
    db_session.expire_all()
    return db_session.query(Note).filter(Note.filename == filename).one().content


def test_reindex_never_overwrites_an_edit_awaiting_its_file_write(
    mock_make_session, qdrant, tmp_path, deferred_writes, queued_reindexes
):
    vault, _ = make_vault(tmp_path, "master")
    notes.sync_note(**note_args("v1"))
    commit_file(vault, "n.md", "older hand commit")
    git(vault, "checkout", "-q", "--detach")
    notes.sync_note(**note_args("memory v2"))
    git(vault, "checkout", "-q", "master")

    notes.track_git_changes()
    assert run_queued_reindexes(queued_reindexes) == ["write_pending"]
    [write] = deferred_writes()
    notes.write_note_file_when_free(*write)

    assert note_row(mock_make_session) == "memory v2"
    assert (vault / "n.md").read_text() == "memory v2"


def test_reindex_skips_a_note_whose_write_never_happened(
    mock_make_session, qdrant, tmp_path, queued_reindexes
):
    vault, _ = make_vault(tmp_path, "master")
    notes.sync_note(**note_args("v1"))
    old = git(vault, "rev-parse", "HEAD")
    notes.sync_note(**note_args("v2"))
    with patch.object(notes, "write_note_file_or_defer"):
        notes.sync_note(**note_args("v3"))
    redis.from_url(settings.REDIS_URL).set(notes.GIT_INDEXED_COMMIT_KEY, old)

    notes.track_git_changes()

    assert run_queued_reindexes(queued_reindexes) == ["write_pending"]
    assert note_row(mock_make_session) == "v3"


def test_reindex_indexes_the_file_once_its_write_is_done(
    mock_make_session, qdrant, tmp_path, queued_reindexes
):
    vault, _ = make_vault(tmp_path, "master")
    notes.sync_note(**note_args("v1"))
    commit_file(vault, "n.md", "hand edit")

    notes.track_git_changes()

    assert run_queued_reindexes(queued_reindexes) == ["processed"]
    assert note_row(mock_make_session) == "hand edit"


def test_first_sync_reindexes_every_tracked_note(
    mock_make_session, qdrant, tmp_path, reindexed
):
    vault, _ = make_vault(tmp_path, "master")
    commit_file(vault, "n.md", "hand commit")
    redis.from_url(settings.REDIS_URL).delete(notes.GIT_INDEXED_COMMIT_KEY)

    first = notes.track_git_changes()
    second = notes.track_git_changes()

    assert sorted(first["changed_files"]) == ["existing.md", "n.md"]
    assert second["changed_files"] == []
    assert sorted(reindexed()) == ["notes/existing.md", "notes/n.md"]


def test_sync_note_defers_when_the_git_lock_is_taken(
    mock_make_session, qdrant, tmp_path, deferred_writes
):
    make_vault(tmp_path, "master")
    with patch.object(notes, "write_note_file", side_effect=notes.GitLockTimeout("busy")):
        notes.sync_note(**note_args("v1"))

    assert len(deferred_writes()) == 1


def test_deferred_write_clears_its_mark_when_it_gives_up(
    mock_make_session, qdrant, tmp_path, deferred_writes
):
    vault, _ = make_vault(tmp_path, "master")
    git(vault, "checkout", "-q", "--detach")
    notes.sync_note(**note_args("v1"))
    [write] = deferred_writes()
    assert notes.write_pending("notes/n.md")

    cast(Task, notes.write_note_file_when_free).apply(write, retries=notes.VAULT_BUSY_RETRIES)

    assert not notes.write_pending("notes/n.md")


def test_sync_note_clears_its_mark_when_saving_the_row_fails(mock_make_session, tmp_path):
    make_vault(tmp_path, "master")
    with patch.object(notes, "process_content_item", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError):
            notes.sync_note(**note_args("v1"))

    assert not notes.write_pending("notes/n.md")


def test_reindex_keeps_the_rows_subject(mock_make_session, qdrant, tmp_path, queued_reindexes):
    vault, _ = make_vault(tmp_path, "master")
    notes.sync_note(subject="Meeting with Bob, 3 Oct", content="body", filename="notes/n.md")
    redis.from_url(settings.REDIS_URL).delete(notes.GIT_INDEXED_COMMIT_KEY)

    notes.track_git_changes()
    unchanged = run_queued_reindexes(queued_reindexes)
    queued_reindexes.reset_mock()
    commit_file(vault, "n.md", "hand-edited body")
    notes.track_git_changes()
    edited = run_queued_reindexes(queued_reindexes)

    assert sorted(unchanged) == ["processed", "unchanged"]  # existing.md is new to the DB
    assert edited == ["processed"]
    mock_make_session.expire_all()
    row = mock_make_session.query(Note).filter(Note.filename == "notes/n.md").one()
    assert (row.subject, row.content) == ("Meeting with Bob, 3 Oct", "hand-edited body")


def test_reindex_runs_the_untracked_upsert(mock_make_session, qdrant, tmp_path):
    vault, _ = make_vault(tmp_path, "master")
    commit_file(vault, "n.md", "hand commit")

    with patch.object(notes, "sync_note") as tracked:
        result = notes.reindex_note("notes/n.md")

    assert result["status"] == "processed"
    tracked.assert_not_called()


def test_reindex_retries_when_the_git_lock_is_taken(tmp_path):
    with patch.object(notes, "git_notes_lock", side_effect=notes.GitLockTimeout("busy")) as lock:
        cast(Task, notes.reindex_note).apply(("notes/n.md",))

    # Eager mode runs each retry at once.
    assert lock.call_count == notes.VAULT_BUSY_RETRIES + 1


def test_reindex_never_saves_over_an_edit_marked_during_it(mock_make_session, qdrant, tmp_path):
    make_vault(tmp_path, "master")
    notes.sync_note(**note_args("v1"))
    notes.mark_pending_write("notes/n.md", create_content_hash("newer"))

    result = notes.upsert_note(subject="n", content="hand", filename="notes/n.md", save_to_file=False)

    assert result["status"] == "write_pending"
    assert note_row(mock_make_session) == "v1"


def test_superseded_write_leaves_the_newer_edits_mark(
    mock_make_session, qdrant, tmp_path, deferred_writes
):
    vault, _ = make_vault(tmp_path, "master")
    git(vault, "checkout", "-q", "--detach")
    notes.sync_note(**note_args("A"))
    notes.sync_note(**note_args("B"))
    git(vault, "checkout", "-q", "master")
    write_a, write_b = deferred_writes()

    assert notes.write_note_file_when_free(*write_a)["status"] == "superseded"
    assert notes.write_pending("notes/n.md")
    assert notes.write_note_file_when_free(*write_b)["status"] == "written"
    assert not notes.write_pending("notes/n.md")
    assert (vault / "n.md").read_text() == "B"


NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "last_at, interval, due",
    [
        (None, 900, True),
        (NOW - timedelta(seconds=899), 900, False),
        (NOW - timedelta(seconds=900), 900, True),
        (NOW - timedelta(seconds=120), 60, True),
        (NOW - timedelta(hours=2), 24 * 60 * 60, False),
    ],
)
def test_a_sync_is_due_once_the_interval_has_passed_since_the_last(last_at, interval, due):
    assert notes.sync_due(last_at, interval, NOW) is due


@pytest.fixture
def queued_syncs():
    with patch.object(notes.track_git_changes, "delay") as delay:
        yield delay


def test_the_due_check_skips_a_vault_without_git(queued_syncs):
    notes.sync_notes_when_due()

    queued_syncs.assert_not_called()


def test_the_due_check_queues_one_sync_until_that_sync_records_its_status(tmp_path, queued_syncs, monkeypatch):
    make_vault(tmp_path, "main")
    monkeypatch.setattr(settings, "NOTES_SYNC_INTERVAL", 900)
    redis.from_url(settings.REDIS_URL).delete(notes_git.GIT_SYNC_STATUS_KEY)

    notes.sync_notes_when_due()
    notes.sync_notes_when_due()
    assert queued_syncs.call_count == 1

    notes.record_git_sync(None)
    notes.sync_notes_when_due()
    assert queued_syncs.call_count == 1


def test_confirmed_setup_changes_destination_without_losing_history(tmp_path):
    vault, old_remote = make_vault(tmp_path, "master")
    history = git(vault, "rev-parse", "HEAD")
    new_remote = tmp_path / "new.git"
    git(tmp_path, "init", "-q", "--bare", str(new_remote))
    (vault / "draft.md").write_text("draft")
    result = notes.setup_git_notes(str(new_remote), "new@example.com", "New", branch="notes", change_destination=True)
    assert result["status"] == "updated"
    assert git(vault, "rev-parse", "HEAD") == history
    assert git(vault, "symbolic-ref", "--short", "HEAD") == "notes"
    assert git(vault, "remote", "get-url", "origin") == str(new_remote)
    assert git(new_remote, "rev-parse", "refs/heads/notes") == history
    assert (vault / "draft.md").read_text() == "draft"
    assert git(old_remote, "rev-parse", "refs/heads/master") == history


def test_changed_destination_never_overwrites_unrelated_remote_history(tmp_path):
    vault, _ = make_vault(tmp_path, "main")
    original = git(vault, "rev-parse", "HEAD")
    other = tmp_path / "other-vault"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    configure_identity(other)
    commit_file(other, "unrelated.md", "other repository")
    remote = tmp_path / "unrelated.git"
    git(tmp_path, "clone", "-q", "--bare", str(other), str(remote))
    remote_head = git(remote, "rev-parse", "HEAD")
    result = notes.setup_git_notes(str(remote), "me@example.com", "Me", change_destination=True)
    assert result["status"] == "sync_failed"
    assert git(vault, "rev-parse", "HEAD") == original
    assert git(remote, "rev-parse", "HEAD") == remote_head


@pytest.mark.parametrize("branch", [None, "master"])
@pytest.mark.parametrize("local_notes", [False, True])
def test_initial_setup_adopts_remote_history(tmp_path, branch, local_notes):
    remote = tmp_path / "seed.git"
    seed = tmp_path / "seed"
    git(tmp_path, "init", "-q", "--bare", "-b", "master", str(remote))
    git(tmp_path, "clone", "-q", str(remote), str(seed))
    configure_identity(seed)
    commit_file(seed, "remote.md", "remote notes")
    git(seed, "push", "-q", "origin", "master")
    original = git(seed, "rev-parse", "HEAD")
    vault = settings.NOTES_STORAGE_DIR
    if local_notes:
        (vault / "local.md").write_text("local notes")
    result = notes.setup_git_notes(str(remote), "test@example.com", "Test", branch=branch, change_destination=bool(branch))
    assert result["status"] == "created"
    assert git(vault, "symbolic-ref", "--short", "HEAD") == "master"
    assert git(vault, "rev-list", "--max-parents=0", "HEAD") == original
    assert (vault / "remote.md").read_text() == "remote notes"
    assert remote_files(remote, "master") == ({"remote.md", "local.md"} if local_notes else {"remote.md"})
    assert last_status()["ok"] is True


@pytest.mark.parametrize("ignored", [False, True])
def test_initial_setup_preserves_colliding_local_file_and_retries(tmp_path, ignored):
    remote = tmp_path / "seed.git"
    seed = tmp_path / "seed"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(tmp_path, "clone", "-q", str(remote), str(seed))
    configure_identity(seed)
    commit_file(seed, "note.md", "remote version")
    git(seed, "push", "-q", "origin", "main")
    vault = settings.NOTES_STORAGE_DIR
    (vault / "note.md").write_text("local version")
    if ignored:
        (vault / ".gitignore").write_text("note.md\n")
    result = notes.setup_git_notes(str(remote), "test@example.com", "Test")
    assert result["failed_step"] == "initial_checkout"
    assert (vault / "note.md").read_text() == "local version"
    assert not notes.head_commit(vault)
    (vault / "note.md").rename(vault / "local.md")
    assert notes.setup_git_notes(str(remote), "test@example.com", "Test")["status"] == "updated"
    assert (vault / "note.md").read_text() == "remote version"
    assert (vault / "local.md").read_text() == "local version"


def test_initial_fetch_failure_does_not_create_independent_history(tmp_path):
    vault = settings.NOTES_STORAGE_DIR
    (vault / "local.md").write_text("local version")
    remote = tmp_path / "unavailable.git"
    result = notes.setup_git_notes(str(remote), "test@example.com", "Test")
    assert result == {"status": "sync_failed", "failed_step": "fetch"}
    assert not notes.head_commit(vault)
    assert (vault / "local.md").read_text() == "local version"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    assert notes.setup_git_notes(str(remote), "test@example.com", "Test")["status"] == "updated"
    assert remote_files(remote, "main") == {"local.md"}


@pytest.mark.parametrize("stderr,reason", [
    ("remote: Write access to repository not granted.", "push_permission"),
    ("remote: protected branch hook declined", "push_policy"),
    ("! [rejected] main -> main (non-fast-forward)", "push_changed"),
    ("fatal: unable to contact server", "push"),
])
def test_push_failure_has_safe_actionable_hint(tmp_path, stderr, reason):
    vault, _ = make_vault(tmp_path, "main")
    commit_file(vault, "local.md", "local")
    original = notes.run_git
    def fail_push(root, *args, **kwargs):
        if args[0] == "push":
            return subprocess.CompletedProcess(args, 1, "", stderr)
        return original(root, *args, **kwargs)
    with patch.object(notes, "run_git", fail_push):
        assert notes.push_upstream(vault, notes.find_upstream(vault)) == reason
    assert reason in notes.GIT_SYNC_HINTS
