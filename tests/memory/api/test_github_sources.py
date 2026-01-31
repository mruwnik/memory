"""Tests for GitHub Sources API endpoints."""

from unittest.mock import Mock, patch

import pytest

from memory.common.db.models import User
from memory.common.db.models.sources import GithubAccount, GithubRepo


@pytest.fixture
def other_user(db_session):
    """Create a second user for testing access control."""
    other = User(
        id=999,
        name="Other User",
        email="other@example.com",
    )
    db_session.add(other)
    db_session.commit()
    return other


# ====== GET /github/accounts tests ======


def test_list_accounts_returns_user_accounts(client, db_session, user):
    """List accounts returns only accounts belonging to current user."""
    # Create account for current user
    account = GithubAccount(
        user_id=user.id,
        name="Test GitHub Account",
        auth_type="pat",
        access_token="test_token",
    )
    db_session.add(account)
    db_session.commit()

    response = client.get("/github/accounts")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Test GitHub Account"
    assert data[0]["auth_type"] == "pat"


def test_list_accounts_empty_when_no_accounts(client, db_session, user):
    """List accounts returns empty list when user has no accounts."""
    response = client.get("/github/accounts")

    assert response.status_code == 200
    assert response.json() == []


# ====== POST /github/accounts tests ======


def test_create_account_pat_success(client, db_session, user):
    """Create GitHub account with PAT authentication succeeds."""
    payload = {
        "name": "My GitHub PAT",
        "auth_type": "pat",
        "access_token": "ghp_test123",
    }

    response = client.post("/github/accounts", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "My GitHub PAT"
    assert data["auth_type"] == "pat"
    assert data["has_access_token"] is True

    # Verify account was created in database
    account = db_session.query(GithubAccount).filter_by(name="My GitHub PAT").first()
    assert account is not None
    assert account.user_id == user.id


def test_create_account_app_success(client, db_session, user):
    """Create GitHub account with App authentication succeeds."""
    payload = {
        "name": "My GitHub App",
        "auth_type": "app",
        "app_id": 12345,
        "installation_id": 67890,
        "private_key": "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----",
    }

    response = client.post("/github/accounts", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "My GitHub App"
    assert data["auth_type"] == "app"
    assert data["app_id"] == 12345
    assert data["installation_id"] == 67890
    assert data["has_private_key"] is True


@pytest.mark.parametrize(
    "missing_field,payload_override",
    [
        ("access_token", {"access_token": None}),
    ],
)
def test_create_account_pat_missing_token(
    client, db_session, user, missing_field, payload_override
):
    """Create PAT account without access token fails."""
    payload = {
        "name": "Test",
        "auth_type": "pat",
        "access_token": "test_token",
    }
    payload.update(payload_override)

    response = client.post("/github/accounts", json=payload)

    # Should fail validation
    assert response.status_code in [400, 422]


# ====== GET /github/accounts/{account_id} tests ======


def test_get_account_success(client, db_session, user):
    """Get account by ID returns account details."""
    account = GithubAccount(
        user_id=user.id,
        name="Test Account",
        auth_type="pat",
        access_token="token123",
    )
    db_session.add(account)
    db_session.commit()

    response = client.get(f"/github/accounts/{account.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == account.id
    assert data["name"] == account.name


def test_get_account_not_found(client, db_session, user):
    """Get account returns 404 when account doesn't exist."""
    response = client.get("/github/accounts/999999")

    assert response.status_code == 404


def test_get_account_not_owned(client, db_session, user, other_user):
    """Get account returns 404 when account belongs to another user."""
    # Create account for a different user
    account = GithubAccount(
        user_id=other_user.id,
        name="Other User Account",
        auth_type="pat",
        access_token="token123",
    )
    db_session.add(account)
    db_session.commit()

    response = client.get(f"/github/accounts/{account.id}")

    assert response.status_code == 404


# ====== PATCH /github/accounts/{account_id} tests ======


def test_update_account_name(client, db_session, user):
    """Update account name succeeds."""
    account = GithubAccount(
        user_id=user.id,
        name="Original Name",
        auth_type="pat",
        access_token="token123",
    )
    db_session.add(account)
    db_session.commit()

    response = client.patch(
        f"/github/accounts/{account.id}",
        json={"name": "Updated Name"},
    )

    assert response.status_code == 200

    # Verify in database
    db_session.refresh(account)
    assert account.name == "Updated Name"


@pytest.mark.parametrize(
    "field,value",
    [
        ("access_token", "new_token"),
        ("active", False),
        ("app_id", 12345),
        ("installation_id", 67890),
        ("private_key", "-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----"),
    ],
)
def test_update_account_fields(client, db_session, user, field, value):
    """Update account fields succeeds."""
    account = GithubAccount(
        user_id=user.id,
        name="Test Account",
        auth_type="app",
        access_token="token123",
    )
    db_session.add(account)
    db_session.commit()

    response = client.patch(
        f"/github/accounts/{account.id}",
        json={field: value},
    )

    assert response.status_code == 200

    # Verify in database
    db_session.refresh(account)
    assert getattr(account, field) == value


def test_update_account_not_owned(client, db_session, user, other_user):
    """Update account fails when not owned by user."""
    account = GithubAccount(
        user_id=other_user.id,
        name="Other User Account",
        auth_type="pat",
        access_token="token123",
    )
    db_session.add(account)
    db_session.commit()

    response = client.patch(
        f"/github/accounts/{account.id}",
        json={"name": "New Name"},
    )

    assert response.status_code == 404


# ====== DELETE /github/accounts/{account_id} tests ======


def test_delete_account_success(client, db_session, user):
    """Delete account succeeds."""
    account = GithubAccount(
        user_id=user.id,
        name="Test Account",
        auth_type="pat",
        access_token="token123",
    )
    db_session.add(account)
    db_session.commit()
    account_id = account.id

    response = client.delete(f"/github/accounts/{account_id}")

    assert response.status_code == 200

    # Verify deleted from database
    deleted_account = db_session.query(GithubAccount).filter_by(id=account_id).first()
    assert deleted_account is None


def test_delete_account_not_found(client, db_session, user):
    """Delete account returns 404 when not found."""
    response = client.delete("/github/accounts/999999")

    assert response.status_code == 404


def test_delete_account_not_owned(client, db_session, user, other_user):
    """Delete account fails when not owned."""
    account = GithubAccount(
        user_id=other_user.id,
        name="Other User Account",
        auth_type="pat",
        access_token="token123",
    )
    db_session.add(account)
    db_session.commit()

    response = client.delete(f"/github/accounts/{account.id}")

    assert response.status_code == 404


# ====== POST /github/accounts/{account_id}/repos tests ======


@patch("memory.api.github_sources.GithubClient")
def test_create_repo_success(mock_client_class, client, db_session, user):
    """Create GitHub repo succeeds."""
    # Setup mock to return canonical repo info from GitHub
    mock_client = Mock()
    mock_client.get_repo.return_value = {
        "id": 12345,
        "owner": {"login": "octocat"},
        "name": "Hello-World",
        "full_name": "octocat/Hello-World",
    }
    mock_client_class.return_value = mock_client

    account = GithubAccount(
        user_id=user.id,
        name="Test Account",
        auth_type="pat",
        access_token="token123",
    )
    db_session.add(account)
    db_session.commit()

    payload = {
        "owner": "octocat",
        "name": "Hello-World",
        "track_issues": True,
        "track_prs": True,
    }

    response = client.post(
        f"/github/accounts/{account.id}/repos", json=payload
    )

    assert response.status_code == 200

    # Verify repo was created in database with github_id
    repo = db_session.query(GithubRepo).filter_by(name="Hello-World").first()
    assert repo is not None
    assert repo.owner == "octocat"
    assert repo.account_id == account.id
    assert repo.github_id == 12345


@patch("memory.api.github_sources.GithubClient")
def test_create_repo_with_filters(mock_client_class, client, db_session, user):
    """Create GitHub repo with filters succeeds."""
    # Setup mock to return canonical repo info from GitHub
    mock_client = Mock()
    mock_client.get_repo.return_value = {
        "id": 12345,
        "owner": {"login": "octocat"},
        "name": "Hello-World",
        "full_name": "octocat/Hello-World",
    }
    mock_client_class.return_value = mock_client

    account = GithubAccount(
        user_id=user.id,
        name="Test Account",
        auth_type="pat",
        access_token="token123",
    )
    db_session.add(account)
    db_session.commit()

    payload = {
        "owner": "octocat",
        "name": "Hello-World",
        "labels_filter": ["bug", "enhancement"],
        "state_filter": "open",
        "tags": ["important"],
    }

    response = client.post(
        f"/github/accounts/{account.id}/repos", json=payload
    )

    assert response.status_code == 200


# ====== GET /github/accounts/{account_id}/repos tests ======


def test_list_repos_returns_account_repos(client, db_session, user):
    """List repos returns all repos for the account."""
    account = GithubAccount(
        user_id=user.id,
        name="Test Account",
        auth_type="pat",
        access_token="token123",
    )
    db_session.add(account)
    db_session.commit()

    repo = GithubRepo(
        account_id=account.id,
        owner="octocat",
        name="Hello-World",
        track_issues=True,
        track_prs=True,
    )
    db_session.add(repo)
    db_session.commit()

    response = client.get(f"/github/accounts/{account.id}/repos")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["owner"] == "octocat"
    assert data[0]["name"] == "Hello-World"


def test_list_repos_empty_when_no_repos(client, db_session, user):
    """List repos returns empty list when account has no repos."""
    account = GithubAccount(
        user_id=user.id,
        name="Test Account",
        auth_type="pat",
        access_token="token123",
    )
    db_session.add(account)
    db_session.commit()

    response = client.get(f"/github/accounts/{account.id}/repos")

    assert response.status_code == 200
    assert response.json() == []
