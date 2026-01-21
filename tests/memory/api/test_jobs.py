"""Tests for jobs API endpoints."""

import pytest
from fastapi.testclient import TestClient

from memory.common.db.models import PendingJob, JobStatus, JobType, User


# Note: app_client, client, and user fixtures are defined in conftest.py


@pytest.fixture
def job_for_user(db_session, user):
    """Create a job owned by the test user."""
    job = PendingJob(
        job_type=JobType.MEETING.value,
        external_id="user-job-ext-123",
        params={"title": "Test Meeting"},
        status=JobStatus.PENDING.value,
        user_id=user.id,
    )
    db_session.add(job)
    db_session.commit()
    return job


@pytest.fixture
def other_user(db_session):
    """Create a different user for testing cross-user access."""
    existing = db_session.query(User).filter(User.id == 99999).first()
    if existing:
        return existing
    other = User(
        id=99999,
        name="Other User",
        email="other@example.com",
    )
    db_session.add(other)
    db_session.commit()
    return other


@pytest.fixture
def job_for_other_user(db_session, other_user):
    """Create a job owned by a different user."""
    job = PendingJob(
        job_type=JobType.MEETING.value,
        external_id="other-user-job",
        params={"title": "Other User Meeting"},
        status=JobStatus.PENDING.value,
        user_id=other_user.id,
    )
    db_session.add(job)
    db_session.commit()
    return job


@pytest.fixture
def failed_job(db_session, user):
    """Create a failed job for retry testing."""
    job = PendingJob(
        job_type=JobType.MEETING.value,
        external_id="failed-job-ext",
        params={"title": "Failed Meeting"},
        status=JobStatus.FAILED.value,
        error_message="Processing failed",
        user_id=user.id,
    )
    db_session.add(job)
    db_session.commit()
    return job


def test_get_job_by_id(client: TestClient, job_for_user):
    """Test getting a job by ID."""
    response = client.get(f"/jobs/{job_for_user.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == job_for_user.id
    assert data["job_type"] == "meeting"
    assert data["status"] == "pending"
    assert data["external_id"] == "user-job-ext-123"


def test_get_job_not_found(client: TestClient):
    """Test getting a non-existent job."""
    response = client.get("/jobs/99999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"


def test_get_job_other_user_returns_404(client: TestClient, job_for_other_user):
    """Test that users cannot see other users' jobs."""
    response = client.get(f"/jobs/{job_for_other_user.id}")

    # Should return 404 (not 403) to avoid leaking job existence
    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"


def test_get_job_by_external_id(client: TestClient, job_for_user):
    """Test getting a job by external ID."""
    response = client.get(f"/jobs/external/{job_for_user.external_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == job_for_user.id
    assert data["external_id"] == job_for_user.external_id


def test_get_job_by_external_id_with_type_filter(client: TestClient, db_session, user):
    """Test getting a job by external ID with job type filter."""
    # Create jobs with same external_id but different types
    job1 = PendingJob(
        job_type=JobType.MEETING.value,
        external_id="shared-ext-id",
        params={},
        status=JobStatus.PENDING.value,
        user_id=user.id,
    )
    job2 = PendingJob(
        job_type=JobType.REPROCESS.value,
        external_id="shared-ext-id",
        params={},
        status=JobStatus.COMPLETE.value,
        user_id=user.id,
    )
    db_session.add_all([job1, job2])
    db_session.commit()

    # Filter by job_type=meeting
    response = client.get("/jobs/external/shared-ext-id?job_type=meeting")

    assert response.status_code == 200
    data = response.json()
    assert data["job_type"] == "meeting"


def test_get_job_by_external_id_not_found(client: TestClient):
    """Test getting a job by non-existent external ID."""
    response = client.get("/jobs/external/nonexistent-ext-id")

    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"


def test_get_job_by_external_id_other_user(client: TestClient, job_for_other_user):
    """Test that external_id lookup respects user ownership."""
    response = client.get(f"/jobs/external/{job_for_other_user.external_id}")

    assert response.status_code == 404


def test_list_jobs(client: TestClient, db_session, user):
    """Test listing jobs for current user."""
    # Create multiple jobs
    for i in range(3):
        job = PendingJob(
            job_type=JobType.MEETING.value,
            external_id=f"list-test-{i}",
            params={},
            status=JobStatus.PENDING.value,
            user_id=user.id,
        )
        db_session.add(job)
    db_session.commit()

    response = client.get("/jobs")

    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 3


def test_list_jobs_filter_by_status(client: TestClient, db_session, user):
    """Test filtering jobs by status."""
    # Create jobs with different statuses
    pending = PendingJob(
        job_type=JobType.MEETING.value,
        params={},
        status=JobStatus.PENDING.value,
        user_id=user.id,
    )
    complete = PendingJob(
        job_type=JobType.MEETING.value,
        params={},
        status=JobStatus.COMPLETE.value,
        user_id=user.id,
    )
    db_session.add_all([pending, complete])
    db_session.commit()

    response = client.get("/jobs?status=pending")

    assert response.status_code == 200
    data = response.json()
    assert all(job["status"] == "pending" for job in data)


def test_list_jobs_filter_by_type(client: TestClient, db_session, user):
    """Test filtering jobs by job type."""
    meeting = PendingJob(
        job_type=JobType.MEETING.value,
        params={},
        status=JobStatus.PENDING.value,
        user_id=user.id,
    )
    reprocess = PendingJob(
        job_type=JobType.REPROCESS.value,
        params={},
        status=JobStatus.PENDING.value,
        user_id=user.id,
    )
    db_session.add_all([meeting, reprocess])
    db_session.commit()

    response = client.get("/jobs?job_type=meeting")

    assert response.status_code == 200
    data = response.json()
    assert all(job["job_type"] == "meeting" for job in data)


def test_list_jobs_pagination(client: TestClient, db_session, user):
    """Test job listing pagination."""
    # Create 5 jobs
    for i in range(5):
        job = PendingJob(
            job_type=JobType.MEETING.value,
            external_id=f"page-test-{i}",
            params={},
            status=JobStatus.PENDING.value,
            user_id=user.id,
        )
        db_session.add(job)
    db_session.commit()

    # Get first page
    response1 = client.get("/jobs?limit=2&offset=0")
    assert response1.status_code == 200
    page1 = response1.json()
    assert len(page1) == 2

    # Get second page
    response2 = client.get("/jobs?limit=2&offset=2")
    assert response2.status_code == 200
    page2 = response2.json()
    assert len(page2) == 2

    # Pages should have different jobs
    page1_ids = {job["id"] for job in page1}
    page2_ids = {job["id"] for job in page2}
    assert page1_ids.isdisjoint(page2_ids)


def test_list_jobs_excludes_other_users(client: TestClient, job_for_other_user, user):
    """Test that list_jobs only returns current user's jobs."""
    response = client.get("/jobs")

    assert response.status_code == 200
    data = response.json()
    # Should not contain the other user's job
    job_ids = [job["id"] for job in data]
    assert job_for_other_user.id not in job_ids


def test_retry_job_not_found(client: TestClient):
    """Test retrying a non-existent job."""
    response = client.post("/jobs/99999/retry")

    assert response.status_code == 404


def test_retry_job_wrong_status(client: TestClient, job_for_user):
    """Test retrying a job that isn't failed."""
    # job_for_user has status=pending
    response = client.post(f"/jobs/{job_for_user.id}/retry")

    assert response.status_code == 400
    assert "Only failed jobs can be retried" in response.json()["detail"]


def test_retry_job_success(client: TestClient, db_session, user):
    """Test successful retry of a failed job resets and reuses the same job."""
    from unittest.mock import patch
    from memory.common.jobs import celery_app

    # Create a failed job with _task_name
    failed_job = PendingJob(
        job_type=JobType.MEETING.value,
        params={
            "title": "Test Meeting",
            "_task_name": "memory.workers.tasks.meetings.process_meeting",
        },
        status=JobStatus.FAILED.value,
        error_message="Previous failure",
        user_id=user.id,
    )
    db_session.add(failed_job)
    db_session.commit()
    original_id = failed_job.id

    with patch.object(
        celery_app, "send_task", return_value=type("Task", (), {"id": "celery-retry"})()
    ):
        response = client.post(f"/jobs/{failed_job.id}/retry")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == original_id  # Same job reused
    assert data["status"] == "pending"
    assert data["job_type"] == "meeting"
    assert data["error_message"] is None  # Error cleared


def test_retry_job_missing_task_name(client: TestClient, db_session, user):
    """Test retry fails for jobs without _task_name (old jobs)."""
    old_job = PendingJob(
        job_type=JobType.MEETING.value,
        params={"title": "Old job"},  # No _task_name
        status=JobStatus.FAILED.value,
        user_id=user.id,
    )
    db_session.add(old_job)
    db_session.commit()

    response = client.post(f"/jobs/{old_job.id}/retry")

    assert response.status_code == 400
    assert "missing _task_name" in response.json()["detail"]


def test_retry_job_other_user(client: TestClient, db_session, other_user):
    """Test that users cannot retry other users' jobs."""
    # Create a failed job for another user
    other_job = PendingJob(
        job_type=JobType.MEETING.value,
        params={},
        status=JobStatus.FAILED.value,
        user_id=other_user.id,
    )
    db_session.add(other_job)
    db_session.commit()

    response = client.post(f"/jobs/{other_job.id}/retry")

    assert response.status_code == 404


def test_route_ordering_external_before_job_id(client: TestClient, job_for_user):
    """Test that /external/{external_id} route works and isn't captured by /{job_id}."""
    # This tests the route ordering fix - "external" should not be parsed as an integer
    response = client.get("/jobs/external/user-job-ext-123")

    # Should succeed (not fail with "invalid integer")
    assert response.status_code == 200
    assert response.json()["external_id"] == "user-job-ext-123"


# ---------- Admin user tests ----------


@pytest.fixture
def admin_user(db_session):
    """Create an admin user with * scope."""
    existing = db_session.query(User).filter(User.id == 2).first()
    if existing:
        existing.scopes = ["*"]
        db_session.commit()
        return existing
    admin = User(
        id=2,
        name="Admin User",
        email="admin@example.com",
        scopes=["*"],
    )
    db_session.add(admin)
    db_session.commit()
    return admin


@pytest.fixture
def admin_client(app_client, db_session, admin_user):
    """Get a test client authenticated as admin user."""
    from unittest.mock import MagicMock, patch
    from memory.api import auth
    from memory.common.db.connection import get_session

    test_client, app = app_client

    # Create mock admin user
    mock_admin = MagicMock()
    mock_admin.id = admin_user.id
    mock_admin.email = admin_user.email
    mock_admin.scopes = ["*"]

    def get_test_session():
        try:
            yield db_session
        finally:
            pass

    # Patch auth to return admin user
    with patch.object(auth, "get_session_user", return_value=mock_admin):
        app.dependency_overrides[get_session] = get_test_session
        yield test_client
        app.dependency_overrides.clear()


def test_admin_can_see_other_user_job(admin_client: TestClient, job_for_other_user):
    """Test that admin can view another user's job."""
    response = admin_client.get(f"/jobs/{job_for_other_user.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == job_for_other_user.id


def test_admin_list_all_users_jobs(admin_client: TestClient, db_session, user, other_user):
    """Test that admin with no user_id filter sees all jobs."""
    # Create jobs for both users
    job1 = PendingJob(
        job_type=JobType.MEETING.value,
        params={},
        status=JobStatus.PENDING.value,
        user_id=user.id,
        external_id="admin-test-user1",
    )
    job2 = PendingJob(
        job_type=JobType.MEETING.value,
        params={},
        status=JobStatus.PENDING.value,
        user_id=other_user.id,
        external_id="admin-test-other",
    )
    db_session.add_all([job1, job2])
    db_session.commit()

    response = admin_client.get("/jobs")

    assert response.status_code == 200
    data = response.json()
    job_ids = [j["id"] for j in data]
    # Admin should see both jobs
    assert job1.id in job_ids
    assert job2.id in job_ids


def test_admin_filter_by_user_id(admin_client: TestClient, db_session, user, other_user):
    """Test that admin can filter jobs by user_id."""
    # Create jobs for both users
    job1 = PendingJob(
        job_type=JobType.MEETING.value,
        params={},
        status=JobStatus.PENDING.value,
        user_id=user.id,
        external_id="filter-test-user1",
    )
    job2 = PendingJob(
        job_type=JobType.MEETING.value,
        params={},
        status=JobStatus.PENDING.value,
        user_id=other_user.id,
        external_id="filter-test-other",
    )
    db_session.add_all([job1, job2])
    db_session.commit()

    # Filter by specific user
    response = admin_client.get(f"/jobs?user_id={other_user.id}")

    assert response.status_code == 200
    data = response.json()
    # Should only see other_user's jobs when filtering
    job_ids = [j["id"] for j in data]
    assert job2.id in job_ids
    # job1 should not appear (belongs to user, not other_user)
    filtered_job = [j for j in data if j["external_id"] == "filter-test-user1"]
    assert len(filtered_job) == 0


def test_admin_filter_nonexistent_user(admin_client: TestClient):
    """Test that admin filtering by non-existent user returns 404."""
    response = admin_client.get("/jobs?user_id=999999")

    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"


def test_non_admin_cannot_filter_by_user_id(client: TestClient, job_for_other_user):
    """Test that non-admin users cannot use user_id filter to see other users' jobs."""
    # Regular client (non-admin) trying to filter by other user's ID
    response = client.get(f"/jobs?user_id={job_for_other_user.user_id}")

    assert response.status_code == 200
    data = response.json()
    # Should not see other user's job even with user_id filter
    job_ids = [j["id"] for j in data]
    assert job_for_other_user.id not in job_ids
