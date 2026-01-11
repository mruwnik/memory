"""Shared fixtures for API tests."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from memory.common.db.models import User


# Session-scoped test client to avoid lifespan state issues
_test_client = None
_test_app = None
_auth_patches = None


@pytest.fixture(scope="session")
def app_client():
    """Create a session-scoped test client with mocked authentication."""
    global _test_client, _test_app, _auth_patches
    from memory.api import auth
    from memory.api.app import app

    _test_app = app

    token_patch = patch.object(auth, "get_token", return_value="fake-token")
    user_patch = patch.object(auth, "get_session_user")

    token_patch.start()
    mock_get_user = user_patch.start()

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.email = "test@example.com"
    mock_get_user.return_value = mock_user

    _test_client = TestClient(app, raise_server_exceptions=False)
    _test_client.__enter__()

    yield _test_client, app

    _test_client.__exit__(None, None, None)
    token_patch.stop()
    user_patch.stop()


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
    test_user = User(
        id=1,
        name="Test User",
        email="test@example.com",
    )
    db_session.add(test_user)
    db_session.commit()
    return test_user
