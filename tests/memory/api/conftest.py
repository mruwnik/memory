"""Shared fixtures for API tests."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from memory.common.db.models import HumanUser, User


# Session-scoped test client to avoid lifespan state issues
_test_client = None
_test_app = None
_auth_patches = None


@pytest.fixture(scope="session")
def app_client():
    """Create a session-scoped test client with mocked authentication."""
    global _test_client, _test_app, _auth_patches
    from memory.common import settings
    from memory.api.app import app
    from memory.api.auth import get_current_user

    _test_app = app

    # Disable auth middleware for tests
    original_disable_auth = settings.DISABLE_AUTH
    settings.DISABLE_AUTH = True

    # Create a mock user that will be returned by the auth dependency
    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.email = "test@example.com"
    mock_user.scopes = ["*"]  # Admin scope for full access

    def mock_get_current_user():
        return mock_user

    # Override the FastAPI dependency
    app.dependency_overrides[get_current_user] = mock_get_current_user

    _test_client = TestClient(app, raise_server_exceptions=False)
    _test_client.__enter__()

    yield _test_client, app

    _test_client.__exit__(None, None, None)
    app.dependency_overrides.pop(get_current_user, None)
    settings.DISABLE_AUTH = original_disable_auth


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
    # Only remove the session override, not the auth override
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture
def user(db_session):
    """Create a test user matching the mock auth user."""
    existing = db_session.query(User).filter(User.id == 1).first()
    if existing:
        return existing
    test_user = HumanUser(
        id=1,
        name="Test User",
        email="test@example.com",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(test_user)
    db_session.commit()
    return test_user


@pytest.fixture
def regular_client(app_client, db_session, user):
    """Test client with non-admin (regular user) scope for access control tests."""
    from memory.api.auth import get_current_user
    from memory.common.db.connection import get_session

    test_client, app = app_client

    # Create a mock user with non-admin scope
    # Use spec=HumanUser to catch attribute access errors
    mock_user = MagicMock(spec=HumanUser)
    mock_user.id = user.id
    mock_user.email = user.email
    mock_user.scopes = ["read", "write"]  # Non-admin scopes

    def mock_get_current_user():
        return mock_user

    def get_test_session():
        try:
            yield db_session
        finally:
            pass

    # Save original auth override (the admin one from app_client)
    original_auth = app.dependency_overrides.get(get_current_user)

    # Override with non-admin user
    app.dependency_overrides[get_current_user] = mock_get_current_user
    app.dependency_overrides[get_session] = get_test_session

    yield test_client

    # Restore admin user override
    if original_auth:
        app.dependency_overrides[get_current_user] = original_auth
    # Session cleanup is done by the client fixture
