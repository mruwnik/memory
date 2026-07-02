"""Tests for session transcript search indexing (index_session & friends)."""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from memory.common import settings
from memory.common.db.models import Chunk, Session, SessionSegment, SourceItem
from memory.workers.tasks import sessions as sessions_tasks
from memory.workers.tasks.maintenance import cleanup_old_claude_sessions


def make_event(event_type: str, text: str, timestamp: str) -> dict:
    return {
        "uuid": str(uuid.uuid4()),
        "type": event_type,
        "timestamp": timestamp,
        "message": {"role": event_type, "content": text},
    }


def make_events(count: int, words_per_message: int = 60) -> list[dict]:
    return [
        make_event(
            "user" if i % 2 == 0 else "assistant",
            f"message {i}: " + "word " * words_per_message,
            f"2026-07-01T12:{i:02d}:00Z",
        )
        for i in range(count)
    ]


def write_transcript(path, events, age_seconds: int = 7200):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    mtime = time.time() - age_seconds
    os.utime(path, (mtime, mtime))


def make_chunk(item: SourceItem) -> Chunk:
    return Chunk(
        id=str(uuid.uuid4()),
        source=item,
        content=item.content,
        embedding_model="test-model",
        vector=[0.1] * 1024,
        item_metadata={},
        collection_name="session",
    )


@pytest.fixture
def sessions_dir(tmp_path):
    with patch.object(settings, "SESSIONS_STORAGE_DIR", tmp_path / "sessions"):
        yield tmp_path / "sessions"


@pytest.fixture
def indexing_env(db_session, sessions_dir):
    """Route the task's DB access to the test session and stub out embedding."""

    def fake_session():
        class Ctx:
            def __enter__(self):
                return db_session

            def __exit__(self, *args):
                pass

        return Ctx()

    with (
        patch("memory.workers.tasks.sessions.make_session", fake_session),
        patch(
            "memory.common.embedding.embed_source_item",
            side_effect=lambda item: [make_chunk(item)],
        ),
        patch("memory.common.content_processing.push_chunks_to_qdrant"),
    ):
        yield db_session


@pytest.fixture
def coding_session(indexing_env, regular_user, sessions_dir):
    session = Session(
        id=uuid.uuid4(),
        user_id=regular_user.id,
        transcript_path="1/test-session.jsonl",
    )
    indexing_env.add(session)
    indexing_env.commit()
    return session


def test_index_session_creates_segments(indexing_env, coding_session, sessions_dir):
    write_transcript(
        sessions_dir / coding_session.transcript_path, make_events(12)
    )

    result = sessions_tasks.index_session(str(coding_session.id))

    assert result["status"] == "success"
    assert result["created"] > 0
    assert result["duplicates"] == 0

    segments = indexing_env.query(SessionSegment).all()
    assert len(segments) == result["created"]
    for segment in segments:
        assert segment.session_id == coding_session.id
        assert segment.creator_id == coding_session.user_id
        assert segment.project_id is None
        assert segment.project_id_inherited is False
        assert segment.modality == "session"
        assert segment.embed_status == "STORED"
        assert "message" in segment.content
        assert segment.roles == ["assistant", "user"]

    # Watermark advanced past the last message of the last segment
    assert coding_session.indexed_up_to == 12
    assert coding_session.indexed_at is not None


def test_index_session_is_idempotent(indexing_env, coding_session, sessions_dir):
    write_transcript(sessions_dir / coding_session.transcript_path, make_events(12))

    first = sessions_tasks.index_session(str(coding_session.id))

    # Reset the watermark to simulate a re-run over the same content
    coding_session.indexed_up_to = 0
    indexing_env.commit()

    second = sessions_tasks.index_session(str(coding_session.id))

    assert second["created"] == 0
    assert second["duplicates"] == first["created"]
    assert indexing_env.query(SessionSegment).count() == first["created"]


def test_index_session_holds_back_hot_tail(indexing_env, coding_session, sessions_dir):
    # Recently-modified transcript: the trailing partial segment must wait
    write_transcript(
        sessions_dir / coding_session.transcript_path,
        make_events(2, words_per_message=5),
        age_seconds=0,
    )

    result = sessions_tasks.index_session(str(coding_session.id))

    assert result["created"] == 0
    assert coding_session.indexed_up_to == 0


def test_index_session_incremental(indexing_env, coding_session, sessions_dir):
    transcript = sessions_dir / coding_session.transcript_path
    events = make_events(12)
    write_transcript(transcript, events)

    first = sessions_tasks.index_session(str(coding_session.id))

    write_transcript(transcript, events + make_events(24)[12:])
    second = sessions_tasks.index_session(str(coding_session.id))

    assert second["created"] > 0
    assert second["duplicates"] == 0
    assert (
        indexing_env.query(SessionSegment).count()
        == first["created"] + second["created"]
    )
    assert coding_session.indexed_up_to == 24


def test_index_session_missing_transcript(indexing_env, coding_session):
    result = sessions_tasks.index_session(str(coding_session.id))
    assert result["status"] == "skipped"


def test_index_session_invalid_uuid(indexing_env):
    result = sessions_tasks.index_session("not-a-uuid")
    assert result["status"] == "error"


def test_index_stale_sessions_queues_only_stale(
    indexing_env, regular_user, sessions_dir
):
    stale = Session(
        id=uuid.uuid4(), user_id=regular_user.id, transcript_path="1/stale.jsonl"
    )
    fresh = Session(
        id=uuid.uuid4(),
        user_id=regular_user.id,
        transcript_path="1/fresh.jsonl",
        indexed_at=datetime.now(timezone.utc),
    )
    indexing_env.add_all([stale, fresh])
    indexing_env.commit()

    write_transcript(sessions_dir / "1/stale.jsonl", make_events(2))
    write_transcript(sessions_dir / "1/fresh.jsonl", make_events(2))

    with patch.object(sessions_tasks.index_session, "delay") as mock_delay:
        result = sessions_tasks.index_stale_sessions()

    assert result["queued"] == 1
    mock_delay.assert_called_once_with(str(stale.id))


def test_index_stale_sessions_requeues_pending_tail(
    indexing_env, regular_user, sessions_dir
):
    # Indexed while the transcript was still hot: the tail is pending even
    # though the file hasn't changed since.
    session = Session(
        id=uuid.uuid4(),
        user_id=regular_user.id,
        transcript_path="1/hot.jsonl",
        indexed_at=datetime.now(timezone.utc),
    )
    indexing_env.add(session)
    indexing_env.commit()
    write_transcript(sessions_dir / "1/hot.jsonl", make_events(2), age_seconds=60)

    with patch.object(sessions_tasks.index_session, "delay") as mock_delay:
        result = sessions_tasks.index_stale_sessions()

    assert result["queued"] == 1
    mock_delay.assert_called_once_with(str(session.id))


def test_cleanup_old_sessions_removes_segments(
    indexing_env, coding_session, sessions_dir
):
    write_transcript(sessions_dir / coding_session.transcript_path, make_events(12))
    sessions_tasks.index_session(str(coding_session.id))
    assert indexing_env.query(SessionSegment).count() > 0

    with patch("memory.workers.tasks.maintenance.make_session", lambda: _ctx(indexing_env)):
        cleanup_old_claude_sessions(max_age_days=0)

    assert indexing_env.query(SessionSegment).count() == 0
    assert indexing_env.query(SourceItem).count() == 0
    assert indexing_env.query(Session).count() == 0


class _ctx:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *args):
        pass


def test_index_session_halts_watermark_on_embedding_failure(
    indexing_env, coding_session, sessions_dir
):
    write_transcript(sessions_dir / coding_session.transcript_path, make_events(12))

    calls = {"count": 0}

    def flaky_embed(item):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise IOError("voyage is down")
        return [make_chunk(item)]

    with patch("memory.common.embedding.embed_source_item", side_effect=flaky_embed):
        result = sessions_tasks.index_session(str(coding_session.id))

    assert result["status"] == "partial"
    assert result["created"] == 1
    # The failed row was dropped and the watermark halted before it,
    # so the slice stays retryable; indexed_at stays unset so the
    # stale-session sweep requeues this session.
    segments = indexing_env.query(SessionSegment).all()
    assert len(segments) == 1
    assert coding_session.indexed_up_to == segments[0].end_index + 1
    assert coding_session.indexed_at is None

    # Once embedding recovers, the remaining slice is indexed.
    recovery = sessions_tasks.index_session(str(coding_session.id))

    assert recovery["status"] == "success"
    assert recovery["created"] >= 1
    assert coding_session.indexed_up_to == 12
    assert coding_session.indexed_at is not None
