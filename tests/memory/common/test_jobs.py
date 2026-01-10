"""Tests for job tracking utilities."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from memory.common.db.models import PendingJob, JobStatus, JobType
from memory.common import jobs as job_utils


@pytest.fixture
def sample_job(db_session):
    """Create a sample pending job."""
    job = PendingJob(
        job_type=JobType.MEETING.value,
        external_id="test-external-123",
        params={"test_param": "value"},
        status=JobStatus.PENDING.value,
    )
    db_session.add(job)
    db_session.commit()
    return job


def test_create_job(db_session):
    """Test creating a new job."""
    job = job_utils.create_job(
        db_session,
        job_type=JobType.MEETING,
        params={"transcript_size": 1000},
        external_id="ext-123",
        user_id=None,
    )
    db_session.commit()

    assert job.id is not None
    assert job.job_type == "meeting"
    assert job.external_id == "ext-123"
    assert job.params == {"transcript_size": 1000}
    assert job.status == JobStatus.PENDING.value
    assert job.attempts == 0


def test_create_job_with_string_type(db_session):
    """Test creating a job with string job type."""
    job = job_utils.create_job(
        db_session,
        job_type="custom_type",
        params={},
    )
    db_session.commit()

    assert job.job_type == "custom_type"


def test_get_job(db_session, sample_job):
    """Test retrieving a job by ID."""
    retrieved = job_utils.get_job(db_session, sample_job.id)

    assert retrieved is not None
    assert retrieved.id == sample_job.id
    assert retrieved.external_id == sample_job.external_id


def test_get_job_not_found(db_session):
    """Test retrieving a non-existent job."""
    retrieved = job_utils.get_job(db_session, 99999)

    assert retrieved is None


def test_get_job_by_external_id(db_session, sample_job):
    """Test retrieving a job by external ID."""
    retrieved = job_utils.get_job_by_external_id(
        db_session, sample_job.external_id
    )

    assert retrieved is not None
    assert retrieved.id == sample_job.id


def test_get_job_by_external_id_with_type_filter(db_session, sample_job):
    """Test retrieving a job by external ID with job type filter."""
    # Matching job type
    retrieved = job_utils.get_job_by_external_id(
        db_session, sample_job.external_id, job_type=JobType.MEETING.value
    )
    assert retrieved is not None

    # Non-matching job type
    retrieved = job_utils.get_job_by_external_id(
        db_session, sample_job.external_id, job_type=JobType.REPROCESS.value
    )
    assert retrieved is None


def test_get_job_by_external_id_not_found(db_session):
    """Test retrieving a non-existent external ID."""
    retrieved = job_utils.get_job_by_external_id(db_session, "nonexistent")

    assert retrieved is None


def test_start_job(db_session, sample_job):
    """Test marking a job as processing."""
    job = job_utils.start_job(db_session, sample_job.id)
    db_session.commit()

    assert job.status == JobStatus.PROCESSING.value
    assert job.attempts == 1


def test_start_job_increments_attempts(db_session, sample_job):
    """Test that start_job increments attempts each time."""
    job_utils.start_job(db_session, sample_job.id)
    db_session.commit()

    job_utils.start_job(db_session, sample_job.id)
    db_session.commit()

    db_session.refresh(sample_job)
    assert sample_job.attempts == 2


def test_start_job_not_found(db_session):
    """Test starting a non-existent job."""
    job = job_utils.start_job(db_session, 99999)

    assert job is None


def test_complete_job(db_session, sample_job):
    """Test marking a job as complete."""
    job = job_utils.complete_job(
        db_session,
        sample_job.id,
        result_id=42,
        result_type="Meeting",
    )
    db_session.commit()

    assert job.status == JobStatus.COMPLETE.value
    assert job.result_id == 42
    assert job.result_type == "Meeting"
    assert job.completed_at is not None


def test_complete_job_without_result(db_session, sample_job):
    """Test marking a job as complete without result linking."""
    job = job_utils.complete_job(db_session, sample_job.id)
    db_session.commit()

    assert job.status == JobStatus.COMPLETE.value
    assert job.result_id is None
    assert job.result_type is None


def test_complete_job_not_found(db_session):
    """Test completing a non-existent job."""
    job = job_utils.complete_job(db_session, 99999)

    assert job is None


def test_fail_job(db_session, sample_job):
    """Test marking a job as failed."""
    error_msg = "Something went wrong"
    job = job_utils.fail_job(db_session, sample_job.id, error_msg)
    db_session.commit()

    assert job.status == JobStatus.FAILED.value
    assert job.error_message == error_msg
    assert job.completed_at is not None


def test_fail_job_not_found(db_session):
    """Test failing a non-existent job."""
    job = job_utils.fail_job(db_session, 99999, "error")

    assert job is None


def test_update_job_celery_task_id(db_session, sample_job):
    """Test updating job with Celery task ID."""
    celery_id = "abc-123-def"
    job_utils.update_job_celery_task_id(db_session, sample_job, celery_id)
    db_session.commit()

    db_session.refresh(sample_job)
    assert sample_job.celery_task_id == celery_id


def test_list_jobs_no_filter(db_session):
    """Test listing all jobs."""
    # Create multiple jobs
    for i in range(3):
        job = PendingJob(
            job_type=JobType.MEETING.value,
            external_id=f"ext-{i}",
            params={},
            status=JobStatus.PENDING.value,
        )
        db_session.add(job)
    db_session.commit()

    jobs = job_utils.list_jobs(db_session)

    assert len(jobs) == 3


def test_list_jobs_filter_by_status(db_session):
    """Test listing jobs filtered by status."""
    # Create jobs with different statuses
    pending_job = PendingJob(
        job_type=JobType.MEETING.value,
        params={},
        status=JobStatus.PENDING.value,
    )
    complete_job = PendingJob(
        job_type=JobType.MEETING.value,
        params={},
        status=JobStatus.COMPLETE.value,
    )
    db_session.add_all([pending_job, complete_job])
    db_session.commit()

    pending_jobs = job_utils.list_jobs(db_session, status=JobStatus.PENDING)

    assert len(pending_jobs) == 1
    assert pending_jobs[0].status == JobStatus.PENDING.value


def test_list_jobs_filter_by_job_type(db_session):
    """Test listing jobs filtered by job type."""
    meeting_job = PendingJob(
        job_type=JobType.MEETING.value,
        params={},
        status=JobStatus.PENDING.value,
    )
    reprocess_job = PendingJob(
        job_type=JobType.REPROCESS.value,
        params={},
        status=JobStatus.PENDING.value,
    )
    db_session.add_all([meeting_job, reprocess_job])
    db_session.commit()

    meeting_jobs = job_utils.list_jobs(db_session, job_type=JobType.MEETING)

    assert len(meeting_jobs) == 1
    assert meeting_jobs[0].job_type == JobType.MEETING.value


def test_list_jobs_pagination(db_session):
    """Test listing jobs with limit and offset."""
    for i in range(5):
        job = PendingJob(
            job_type=JobType.MEETING.value,
            external_id=f"ext-{i}",
            params={},
            status=JobStatus.PENDING.value,
        )
        db_session.add(job)
    db_session.commit()

    # Get first page
    page1 = job_utils.list_jobs(db_session, limit=2, offset=0)
    assert len(page1) == 2

    # Get second page
    page2 = job_utils.list_jobs(db_session, limit=2, offset=2)
    assert len(page2) == 2

    # Ensure different jobs
    page1_ids = {j.id for j in page1}
    page2_ids = {j.id for j in page2}
    assert page1_ids.isdisjoint(page2_ids)


def test_pending_job_model_mark_methods(db_session):
    """Test the convenience methods on PendingJob model."""
    job = PendingJob(
        job_type=JobType.MEETING.value,
        params={},
        status=JobStatus.PENDING.value,
    )
    db_session.add(job)
    db_session.commit()

    # Test mark_processing
    job.mark_processing()
    db_session.commit()
    assert job.status == JobStatus.PROCESSING.value
    assert job.attempts == 1

    # Test mark_complete
    job.mark_complete(result_id=1, result_type="Test")
    db_session.commit()
    assert job.status == JobStatus.COMPLETE.value
    assert job.result_id == 1
    assert job.completed_at is not None

    # Test mark_failed (create new job)
    job2 = PendingJob(
        job_type=JobType.MEETING.value,
        params={},
        status=JobStatus.PROCESSING.value,
    )
    db_session.add(job2)
    db_session.commit()

    job2.mark_failed("Test error")
    db_session.commit()
    assert job2.status == JobStatus.FAILED.value
    assert job2.error_message == "Test error"
    assert job2.completed_at is not None
