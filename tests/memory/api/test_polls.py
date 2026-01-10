"""Tests for polling API endpoints (public endpoints only).

Authenticated poll management is done via MCP tools and tested separately.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from memory.common.db.models import AvailabilityPoll, PollResponse, PollAvailability, User, PollStatus


@pytest.fixture(scope="module")
def app_client():
    """Create a test client with mocked authentication."""
    from memory.api import auth
    from memory.api.app import app

    with patch.object(auth, "get_token", return_value="fake-token"):
        with patch.object(auth, "get_session_user") as mock_get_user:
            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.email = "test@example.com"
            mock_get_user.return_value = mock_user

            with TestClient(app, raise_server_exceptions=False) as test_client:
                yield test_client, app


@pytest.fixture
def client(app_client, db_session):
    """Get the test client and configure DB session for each test."""
    from memory.common.db.connection import get_session

    test_client, app = app_client

    def get_test_session():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_session] = get_test_session
    yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def user(db_session):
    """Create a test user matching the mock auth user."""
    existing = db_session.query(User).filter(User.id == 1).first()
    if existing:
        return existing
    
    user = User(
        id=1,
        name="Test User",
        email="test@example.com",
        user_type="user",
        password_hash="$2b$12$fakehashfakehashfakehashfakehashfakehashfakeh",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def sample_poll(db_session, user):
    """Create a sample poll for testing."""
    poll = AvailabilityPoll(
        title="Test Poll",
        user_id=user.id,
        datetime_start=datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc),
        datetime_end=datetime(2026, 2, 5, 17, 0, tzinfo=timezone.utc),
        slot_duration_minutes=30,
        status=PollStatus.OPEN.value,
    )
    db_session.add(poll)
    db_session.commit()
    db_session.refresh(poll)
    return poll


# Public endpoint tests


def test_get_poll_for_response(client: TestClient, sample_poll):
    """Test getting poll details for responding (public, no auth)."""
    response = client.get(f"/polls/respond/{sample_poll.slug}")
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Test Poll"
    assert data["slug"] == sample_poll.slug
    assert data["status"] == "open"
    assert "datetime_start" in data
    assert "datetime_end" in data


def test_get_poll_for_response_not_found(client: TestClient):
    """Test getting non-existent poll returns 404."""
    response = client.get("/polls/respond/nonexistent")
    assert response.status_code == 404


def test_submit_response_success(client: TestClient, sample_poll):
    """Test submitting a response without authentication."""
    slot_start = datetime(2026, 2, 2, 10, 0, tzinfo=timezone.utc)
    slot_end = datetime(2026, 2, 2, 10, 30, tzinfo=timezone.utc)

    response = client.post(
        f"/polls/respond/{sample_poll.slug}",
        json={
            "respondent_name": "Alice",
            "availabilities": [
                {
                    "slot_start": slot_start.isoformat(),
                    "slot_end": slot_end.isoformat(),
                    "availability_level": 1,
                }
            ],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "created"
    assert "edit_token" in data
    assert "response_id" in data


def test_submit_response_if_needed(client: TestClient, sample_poll):
    """Test submitting a response with if_needed level."""
    slot_start = datetime(2026, 2, 2, 11, 0, tzinfo=timezone.utc)
    slot_end = datetime(2026, 2, 2, 11, 30, tzinfo=timezone.utc)

    response = client.post(
        f"/polls/respond/{sample_poll.slug}",
        json={
            "respondent_name": "Bob",
            "availabilities": [
                {
                    "slot_start": slot_start.isoformat(),
                    "slot_end": slot_end.isoformat(),
                    "availability_level": 2,  # if_needed
                }
            ],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "created"


def test_submit_response_slot_outside_range(client: TestClient, sample_poll):
    """Test that slots outside the poll's datetime range are rejected."""
    # Slot outside datetime range
    slot_start = datetime(2026, 2, 10, 10, 0, tzinfo=timezone.utc)
    slot_end = datetime(2026, 2, 10, 10, 30, tzinfo=timezone.utc)

    response = client.post(
        f"/polls/respond/{sample_poll.slug}",
        json={
            "respondent_name": "Charlie",
            "availabilities": [
                {
                    "slot_start": slot_start.isoformat(),
                    "slot_end": slot_end.isoformat(),
                    "availability_level": 1,
                }
            ],
        },
    )
    assert response.status_code == 400
    assert "outside poll time range" in response.json()["detail"]


def test_submit_response_invalid_availability_level(client: TestClient, sample_poll):
    """Test that invalid availability levels are rejected."""
    slot_start = datetime(2026, 2, 2, 10, 0, tzinfo=timezone.utc)
    slot_end = datetime(2026, 2, 2, 10, 30, tzinfo=timezone.utc)

    response = client.post(
        f"/polls/respond/{sample_poll.slug}",
        json={
            "respondent_name": "David",
            "availabilities": [
                {
                    "slot_start": slot_start.isoformat(),
                    "slot_end": slot_end.isoformat(),
                    "availability_level": 99,  # Invalid
                }
            ],
        },
    )
    assert response.status_code == 400
    assert "availability_level" in response.json()["detail"]


def test_submit_response_to_closed_poll(client: TestClient, db_session, user):
    """Test that responses cannot be submitted to closed polls."""
    poll = AvailabilityPoll(
        title="Closed Poll",
        user_id=user.id,
        datetime_start=datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc),
        datetime_end=datetime(2026, 2, 5, 17, 0, tzinfo=timezone.utc),
        status=PollStatus.CLOSED.value,
    )
    db_session.add(poll)
    db_session.commit()
    db_session.refresh(poll)

    slot_start = datetime(2026, 2, 2, 10, 0, tzinfo=timezone.utc)
    slot_end = datetime(2026, 2, 2, 10, 30, tzinfo=timezone.utc)

    response = client.post(
        f"/polls/respond/{poll.slug}",
        json={
            "respondent_name": "Eve",
            "availabilities": [
                {
                    "slot_start": slot_start.isoformat(),
                    "slot_end": slot_end.isoformat(),
                    "availability_level": 1,
                }
            ],
        },
    )
    assert response.status_code == 400
    assert "closed" in response.json()["detail"].lower()


def test_update_response_with_token(client: TestClient, sample_poll, db_session):
    """Test updating a response using edit token header."""
    # Create response
    response_obj = PollResponse(
        poll_id=sample_poll.id,
        respondent_name="Alice",
    )
    db_session.add(response_obj)
    db_session.commit()
    db_session.refresh(response_obj)

    slot_start = datetime(2026, 2, 2, 10, 0, tzinfo=timezone.utc)
    slot_end = datetime(2026, 2, 2, 10, 30, tzinfo=timezone.utc)

    response = client.put(
        f"/polls/respond/{sample_poll.slug}/{response_obj.id}",
        headers={"X-Edit-Token": response_obj.edit_token},
        json={
            "respondent_name": "Alice Updated",
            "availabilities": [
                {
                    "slot_start": slot_start.isoformat(),
                    "slot_end": slot_end.isoformat(),
                    "availability_level": 2,
                }
            ],
        },
    )
    assert response.status_code == 200


def test_update_response_invalid_token(client: TestClient, sample_poll, db_session):
    """Test that updating with invalid token fails."""
    response_obj = PollResponse(
        poll_id=sample_poll.id,
        respondent_name="Alice",
    )
    db_session.add(response_obj)
    db_session.commit()
    db_session.refresh(response_obj)

    slot_start = datetime(2026, 2, 2, 10, 0, tzinfo=timezone.utc)
    slot_end = datetime(2026, 2, 2, 10, 30, tzinfo=timezone.utc)

    response = client.put(
        f"/polls/respond/{sample_poll.slug}/{response_obj.id}",
        headers={"X-Edit-Token": "wrong-token"},
        json={
            "respondent_name": "Alice Updated",
            "availabilities": [
                {
                    "slot_start": slot_start.isoformat(),
                    "slot_end": slot_end.isoformat(),
                    "availability_level": 1,
                }
            ],
        },
    )
    assert response.status_code == 403


def test_get_response_by_token(client: TestClient, sample_poll, db_session):
    """Test fetching a response by edit token for editing."""
    # Create response with availability
    response_obj = PollResponse(
        poll_id=sample_poll.id,
        respondent_name="Alice",
    )
    db_session.add(response_obj)
    db_session.flush()

    slot_start = datetime(2026, 2, 2, 10, 0, tzinfo=timezone.utc)
    slot_end = datetime(2026, 2, 2, 10, 30, tzinfo=timezone.utc)

    avail = PollAvailability(
        response_id=response_obj.id,
        slot_start=slot_start,
        slot_end=slot_end,
        availability_level=1,
    )
    db_session.add(avail)
    db_session.commit()
    db_session.refresh(response_obj)

    response = client.get(
        f"/polls/respond/{sample_poll.slug}/response",
        headers={"X-Edit-Token": response_obj.edit_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["response_id"] == response_obj.id
    assert data["respondent_name"] == "Alice"
    assert len(data["availabilities"]) == 1


def test_get_response_by_token_not_found(client: TestClient, sample_poll):
    """Test that invalid edit token returns 404."""
    response = client.get(
        f"/polls/respond/{sample_poll.slug}/response",
        headers={"X-Edit-Token": "nonexistent-token"},
    )
    assert response.status_code == 404


def test_get_poll_results_public(client: TestClient, sample_poll, db_session):
    """Test getting aggregated results publicly."""
    # Add some responses
    slot_start = datetime(2026, 2, 2, 10, 0, tzinfo=timezone.utc)
    slot_end = datetime(2026, 2, 2, 10, 30, tzinfo=timezone.utc)

    response1 = PollResponse(poll_id=sample_poll.id, respondent_name="Alice")
    response2 = PollResponse(poll_id=sample_poll.id, respondent_name="Bob")
    db_session.add_all([response1, response2])
    db_session.flush()

    avail1 = PollAvailability(
        response_id=response1.id,
        slot_start=slot_start,
        slot_end=slot_end,
        availability_level=1,
    )
    avail2 = PollAvailability(
        response_id=response2.id,
        slot_start=slot_start,
        slot_end=slot_end,
        availability_level=1,
    )
    db_session.add_all([avail1, avail2])
    db_session.commit()

    response = client.get(f"/polls/respond/{sample_poll.slug}/results")
    assert response.status_code == 200
    data = response.json()
    assert data["response_count"] == 2
    assert "aggregated" in data
    assert "best_slots" in data
    assert len(data["aggregated"]) >= 1
    
    # Check aggregation correctness
    slot_agg = next(
        (s for s in data["aggregated"] if s["slot_start"] == slot_start.isoformat().replace("+00:00", "Z")),
        None
    )
    assert slot_agg is not None, f"Expected to find slot starting at {slot_start.isoformat()}"
    assert slot_agg["available_count"] == 2


def test_get_poll_results_empty(client: TestClient, sample_poll):
    """Test getting results for poll with no responses."""
    response = client.get(f"/polls/respond/{sample_poll.slug}/results")
    assert response.status_code == 200
    data = response.json()
    assert data["response_count"] == 0
    assert data["aggregated"] == []
    assert data["best_slots"] == []


# Model tests


def test_poll_slug_is_unique(db_session, user):
    """Test that poll slugs are unique."""
    poll1 = AvailabilityPoll(
        title="Poll 1",
        user_id=user.id,
        datetime_start=datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc),
        datetime_end=datetime(2026, 2, 5, 17, 0, tzinfo=timezone.utc),
    )
    poll2 = AvailabilityPoll(
        title="Poll 2",
        user_id=user.id,
        datetime_start=datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc),
        datetime_end=datetime(2026, 3, 5, 17, 0, tzinfo=timezone.utc),
    )
    db_session.add_all([poll1, poll2])
    db_session.commit()

    assert poll1.slug != poll2.slug
    assert len(poll1.slug) == 12  # Updated slug length
    assert len(poll2.slug) == 12


def test_poll_is_open_property(db_session, user):
    """Test the is_open property correctly reflects poll state."""
    # Open poll
    open_poll = AvailabilityPoll(
        title="Open",
        user_id=user.id,
        datetime_start=datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc),
        datetime_end=datetime(2026, 2, 5, 17, 0, tzinfo=timezone.utc),
        status=PollStatus.OPEN.value,
    )
    assert open_poll.is_open is True

    # Closed poll
    closed_poll = AvailabilityPoll(
        title="Closed",
        user_id=user.id,
        datetime_start=datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc),
        datetime_end=datetime(2026, 2, 5, 17, 0, tzinfo=timezone.utc),
        status=PollStatus.CLOSED.value,
    )
    assert closed_poll.is_open is False

    # Poll with past closes_at
    expired_poll = AvailabilityPoll(
        title="Expired",
        user_id=user.id,
        datetime_start=datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc),
        datetime_end=datetime(2026, 2, 5, 17, 0, tzinfo=timezone.utc),
        status=PollStatus.OPEN.value,
        closes_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    assert expired_poll.is_open is False


def test_poll_response_edit_token_generated(db_session, sample_poll):
    """Test that edit tokens are automatically generated."""
    response = PollResponse(
        poll_id=sample_poll.id,
        respondent_name="Alice",
    )
    db_session.add(response)
    db_session.commit()
    
    assert response.edit_token is not None
    assert len(response.edit_token) == 32  # 16 bytes hex = 32 chars


def test_poll_availability_cascade_delete(db_session, sample_poll):
    """Test that deleting a response cascades to availabilities."""
    response = PollResponse(
        poll_id=sample_poll.id,
        respondent_name="Alice",
    )
    db_session.add(response)
    db_session.flush()
    
    avail = PollAvailability(
        response_id=response.id,
        slot_start=datetime(2026, 2, 2, 10, 0, tzinfo=timezone.utc),
        slot_end=datetime(2026, 2, 2, 10, 30, tzinfo=timezone.utc),
        availability_level=1,
    )
    db_session.add(avail)
    db_session.commit()
    
    avail_id = avail.id
    db_session.delete(response)
    db_session.commit()
    
    # Availability should be deleted
    assert db_session.get(PollAvailability, avail_id) is None
