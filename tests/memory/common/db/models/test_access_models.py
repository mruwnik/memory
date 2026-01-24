"""Tests for access control database models."""

import pytest
from memory.common.db.models.access import AccessLog, log_access
from memory.common.db.models.users import HumanUser


@pytest.fixture
def test_user(db_session):
    """Create a test user."""
    user = HumanUser.create_with_password(
        email="test@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.commit()
    return user


# --- AccessLog Tests ---


def test_create_access_log(db_session, test_user):
    """Test creating an access log entry."""
    log = AccessLog(
        user_id=test_user.id,
        action="search",
        query="test query",
        result_count=10,
    )
    db_session.add(log)
    db_session.commit()

    assert log.id is not None
    assert log.user_id == test_user.id
    assert log.action == "search"
    assert log.query == "test query"
    assert log.result_count == 10
    assert log.timestamp is not None


def test_access_log_helper(db_session, test_user):
    """Test log_access helper function."""
    log = log_access(
        db_session,
        user_id=test_user.id,
        action="view_item",
        item_id=123,
    )
    db_session.commit()

    assert log.user_id == test_user.id
    assert log.action == "view_item"
    assert log.item_id == 123
    assert log.query is None
    assert log.result_count is None


def test_access_log_as_payload(db_session, test_user):
    """Test log serialization."""
    log = AccessLog(
        user_id=test_user.id,
        action="search",
        query="test",
        result_count=5,
    )
    db_session.add(log)
    db_session.commit()

    payload = log.as_payload()
    assert payload["id"] == log.id
    assert payload["user_id"] == test_user.id
    assert payload["action"] == "search"
    assert payload["query"] == "test"
    assert payload["result_count"] == 5
    assert payload["timestamp"] is not None
