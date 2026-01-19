"""Tests for meeting reingest (unified ingest/reingest flow)."""

import uuid
from unittest.mock import patch

import pytest

from memory.common.db.models import (
    Meeting,
    Chunk,
    PendingJob,
    JobStatus,
    JobType,
    Task,
)
from memory.workers.tasks.meetings import (
    reprocess_meeting,
    reextract_meeting,
    prepare_meeting_for_reingest,
)


@pytest.fixture
def meeting(db_session):
    """Create a sample meeting."""
    meeting = Meeting(
        sha256=b"test_meeting_hash_xxx" + bytes(11),  # 21 + 11 = 32 bytes
        title="Test Meeting",
        content="Meeting transcript content for testing extraction.",
        summary="Original summary",
        notes="Original notes",
        extraction_status="complete",
        modality="meeting",
        tags=["test"],
        size=100,
    )
    db_session.add(meeting)
    db_session.commit()
    return meeting


@pytest.fixture
def meeting_with_tasks(db_session, meeting):
    """Create a meeting with linked action item tasks."""
    tasks = [
        Task(
            task_title=f"Action item {i}",
            source_item_id=meeting.id,
            status="pending",
            priority="medium",
            sha256=f"task_hash_{i:020d}".encode()[:32],  # Exactly 32 bytes
            modality="task",
        )
        for i in range(3)
    ]
    db_session.add_all(tasks)
    db_session.commit()
    return meeting, tasks


@pytest.fixture
def meeting_with_chunks(db_session, meeting):
    """Create a meeting with chunks."""
    chunk = Chunk(
        id=str(uuid.uuid4()),
        source=meeting,
        content="Test chunk content",
        embedding_model="test-model",
        collection_name="meeting",
    )
    db_session.add(chunk)
    db_session.commit()
    return meeting, [chunk]


@pytest.fixture
def pending_job(db_session):
    """Create a pending meeting job."""
    job = PendingJob(
        job_type=JobType.MEETING.value,
        params={"test": "value"},
        status=JobStatus.PENDING.value,
    )
    db_session.add(job)
    db_session.commit()
    return job


def test_prepare_meeting_for_reingest_clears_chunks_and_detaches_tasks(
    db_session, meeting_with_tasks, qdrant
):
    """Test that prepare_meeting_for_reingest clears chunks and detaches (not deletes) tasks."""
    meeting, tasks = meeting_with_tasks
    original_task_ids = [t.id for t in tasks]

    # Add a chunk
    chunk = Chunk(
        id=str(uuid.uuid4()),
        source=meeting,
        content="Test chunk",
        embedding_model="test-model",
        collection_name="meeting",
    )
    db_session.add(chunk)
    db_session.commit()

    with patch(
        "memory.common.content_processing.qdrant.delete_points"
    ) as mock_delete:
        result = prepare_meeting_for_reingest(db_session, meeting.id)

    assert result is not None
    assert result.id == meeting.id
    mock_delete.assert_called_once()

    # Verify tasks still exist but are detached (source_item_id is None)
    remaining_tasks = (
        db_session.query(Task).filter(Task.id.in_(original_task_ids)).all()
    )
    assert len(remaining_tasks) == 3  # Tasks still exist
    for task in remaining_tasks:
        assert task.source_item_id is None  # But detached from meeting

    # Verify chunk was deleted
    assert len(meeting.chunks) == 0


def test_prepare_meeting_for_reingest_not_found(db_session):
    """Test prepare_meeting_for_reingest with non-existent meeting."""
    result = prepare_meeting_for_reingest(db_session, 99999)
    assert result is None


def test_reprocess_meeting_success(db_session, qdrant, meeting_with_chunks):
    """Test reprocessing an existing meeting."""
    meeting, chunks = meeting_with_chunks

    with (
        patch(
            "memory.common.content_processing.qdrant.delete_points"
        ) as mock_delete,
        patch(
            "memory.workers.tasks.meetings.call_extraction_llm"
        ) as mock_llm,
        patch(
            "memory.workers.tasks.meetings.process_content_item"
        ) as mock_embed,
    ):
        mock_llm.return_value = {
            "summary": "Re-extracted summary",
            "notes": "Re-extracted notes",
            "action_items": [{"description": "New task", "priority": "high"}],
        }
        mock_embed.return_value = {"status": "processed", "chunks_count": 1}

        result = reprocess_meeting(item_id=meeting.id)

    assert result["status"] == "success"
    assert result["meeting_id"] == meeting.id
    assert result["summary_length"] > 0
    assert result["tasks_created"] == 1
    mock_delete.assert_called_once()
    mock_llm.assert_called_once()
    mock_embed.assert_called_once()


def test_reprocess_meeting_not_found(db_session):
    """Test reprocessing a non-existent meeting."""
    result = reprocess_meeting(item_id=99999)

    assert result["status"] == "error"
    assert "not found" in result["error"]


def test_reprocess_meeting_with_job_tracking(
    db_session, qdrant, meeting, pending_job
):
    """Test reprocessing with job status tracking."""
    with (
        patch("memory.common.content_processing.qdrant.delete_points"),
        patch("memory.workers.tasks.meetings.call_extraction_llm") as mock_llm,
        patch("memory.workers.tasks.meetings.process_content_item") as mock_embed,
    ):
        mock_llm.return_value = {
            "summary": "Summary",
            "notes": "Notes",
            "action_items": [],
        }
        mock_embed.return_value = {"status": "processed"}

        result = reprocess_meeting(item_id=meeting.id, job_id=pending_job.id)

    assert result["status"] == "success"

    # Check job was updated
    db_session.refresh(pending_job)
    assert pending_job.status == JobStatus.COMPLETE.value
    assert pending_job.result_id == meeting.id
    assert pending_job.result_type == "Meeting"


def test_reprocess_meeting_job_fails_on_error(
    db_session, qdrant, meeting, pending_job
):
    """Test that job is marked failed when reprocessing fails."""
    with (
        patch("memory.common.content_processing.qdrant.delete_points"),
        patch("memory.workers.tasks.meetings.call_extraction_llm") as mock_llm,
    ):
        mock_llm.side_effect = Exception("LLM API error")

        result = reprocess_meeting(item_id=meeting.id, job_id=pending_job.id)

    assert result["status"] == "error"
    assert "LLM API error" in result["error"]

    # Check job was marked as failed
    db_session.refresh(pending_job)
    assert pending_job.status == JobStatus.FAILED.value


def test_reextract_meeting_success(db_session, meeting_with_tasks):
    """Test re-extracting meeting information."""
    meeting, original_tasks = meeting_with_tasks
    original_task_ids = [t.id for t in original_tasks]

    with patch(
        "memory.workers.tasks.meetings.call_extraction_llm"
    ) as mock_llm:
        mock_llm.return_value = {
            "summary": "New extracted summary",
            "notes": "New extracted notes",
            "action_items": [
                {"description": "New task 1", "priority": "high"},
                {"description": "New task 2", "priority": "low"},
            ],
        }

        result = reextract_meeting(db_session, meeting)

    assert result["status"] == "success"
    assert result["summary_length"] > 0
    assert result["notes_length"] > 0
    assert result["tasks_created"] == 2

    # Check meeting was updated
    db_session.refresh(meeting)
    assert meeting.summary == "New extracted summary"
    assert meeting.notes == "New extracted notes"
    assert meeting.extraction_status == "complete"

    # Check original tasks were detached (still exist but not linked)
    remaining_tasks = (
        db_session.query(Task).filter(Task.id.in_(original_task_ids)).all()
    )
    assert len(remaining_tasks) == 3  # Still exist
    for task in remaining_tasks:
        assert task.source_item_id is None  # But detached

    # Check new tasks were created and linked
    new_tasks = (
        db_session.query(Task).filter(Task.source_item_id == meeting.id).all()
    )
    assert len(new_tasks) == 2


def test_reextract_meeting_llm_failure(db_session, meeting):
    """Test handling LLM failure during re-extraction."""
    with patch(
        "memory.workers.tasks.meetings.call_extraction_llm"
    ) as mock_llm:
        mock_llm.side_effect = Exception("LLM API error")

        result = reextract_meeting(db_session, meeting)

    assert result["status"] == "error"
    assert "LLM API error" in result["error"]

    # Check meeting status was updated
    db_session.refresh(meeting)
    assert meeting.extraction_status == "failed"
