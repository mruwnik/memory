"""Tests for GitHub Sources API endpoints."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from memory.common.db.models import HumanUser
from memory.common.db.models.sources import GithubAccount, GithubRepo


@pytest.fixture
def other_user(db_session):
    """Create a second user for testing access control."""
    other = HumanUser(
        id=999,
        email="other@example.com",
        name="Other User",
        password_hash="bcrypt_hash_placeholder",
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
    since = datetime(2026, 10, 20, tzinfo=timezone.utc)
    account.repos.append(
        GithubRepo(owner="org", name="repo", sync_error="401 Bad credentials", sync_failing_since=since)
    )
    db_session.add(account)
    db_session.commit()

    response = client.get("/github/accounts")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Test GitHub Account"
    assert data[0]["auth_type"] == "pat"
    repo = data[0]["repos"][0]
    assert (repo["sync_error"], repo["sync_failing_since"]) == ("401 Bad credentials", since.isoformat())


def test_list_accounts_empty_when_no_accounts(client, db_session, user):
    """List accounts returns empty list when user has no accounts."""
    response = client.get("/github/accounts")

    assert response.status_code == 200
    assert response.json() == []


# ====== POST /github/accounts tests ======


def make_github_client_mock(login: str | None = "octocat") -> Mock:
    """Build a GithubClient mock whose credentials resolve to ``login``.

    ``login=None`` simulates a verification failure (bad credentials or
    GitHub unreachable) by raising from ``get_authenticated_login``.
    """
    mock_client = Mock()
    if login is None:
        mock_client.get_authenticated_login.side_effect = RuntimeError("bad creds")
    else:
        mock_client.get_authenticated_login.return_value = login
    return mock_client


@patch("memory.api.github_sources.GithubClient")
def test_create_account_pat_success(mock_client_class, client, db_session, user):
    """Create GitHub account with PAT authentication succeeds."""
    mock_client_class.return_value = make_github_client_mock("octocat")
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
    # Identity is resolved from the credentials, not the request body.
    assert data["verified_login"] == "octocat"

    # Verify account was created in database
    account = db_session.query(GithubAccount).filter_by(name="My GitHub PAT").first()
    assert account is not None
    assert account.user_id == user.id
    assert account.verified_login == "octocat"


@patch("memory.api.github_sources.GithubClient")
def test_create_account_app_success(mock_client_class, client, db_session, user):
    """Create GitHub account with App authentication succeeds."""
    mock_client_class.return_value = make_github_client_mock("acme-org")
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
    assert data["verified_login"] == "acme-org"


@patch("memory.api.github_sources.GithubClient")
def test_create_account_ignores_spoofed_name(mock_client_class, client, db_session, user):
    """A client-supplied name cannot masquerade as a GitHub identity (issue #84)."""
    mock_client_class.return_value = make_github_client_mock("real-attacker")
    payload = {
        "name": "trusted-maintainer",  # the spoofed identity
        "auth_type": "pat",
        "access_token": "ghp_attackers_own_token",
    }

    response = client.post("/github/accounts", json=payload)

    assert response.status_code == 200
    data = response.json()
    # name is preserved as a display label, but verified_login reflects
    # who GitHub says the token actually belongs to.
    assert data["name"] == "trusted-maintainer"
    assert data["verified_login"] == "real-attacker"


@patch("memory.api.github_sources.GithubClient")
def test_create_account_unverified_when_github_unreachable(
    mock_client_class, client, db_session, user
):
    """Verification failures leave verified_login NULL rather than trusting input."""
    mock_client_class.return_value = make_github_client_mock(None)
    payload = {
        "name": "My GitHub PAT",
        "auth_type": "pat",
        "access_token": "ghp_test123",
    }

    response = client.post("/github/accounts", json=payload)

    assert response.status_code == 200
    assert response.json()["verified_login"] is None


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


def test_get_account_not_owned(regular_client, db_session, user, other_user):
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

    response = regular_client.get(f"/github/accounts/{account.id}")

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
@patch("memory.api.github_sources.GithubClient")
def test_update_account_fields(mock_client_class, client, db_session, user, field, value):
    """Update account fields succeeds."""
    mock_client_class.return_value = make_github_client_mock("octocat")
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


@patch("memory.api.github_sources.GithubClient")
def test_update_account_reverifies_on_credential_change(
    mock_client_class, client, db_session, user
):
    """Changing credentials re-resolves the verified login from GitHub."""
    mock_client_class.return_value = make_github_client_mock("new-identity")
    account = GithubAccount(
        user_id=user.id,
        name="Test Account",
        auth_type="pat",
        access_token="old_token",
        verified_login="old-identity",
    )
    db_session.add(account)
    db_session.commit()

    response = client.patch(
        f"/github/accounts/{account.id}",
        json={"access_token": "new_token"},
    )

    assert response.status_code == 200
    db_session.refresh(account)
    assert account.verified_login == "new-identity"


@patch("memory.api.github_sources.GithubClient")
def test_update_account_name_only_keeps_verified_login(
    mock_client_class, client, db_session, user
):
    """Updating only the display name must not touch the verified identity."""
    account = GithubAccount(
        user_id=user.id,
        name="Original Name",
        auth_type="pat",
        access_token="token123",
        verified_login="octocat",
    )
    db_session.add(account)
    db_session.commit()

    response = client.patch(
        f"/github/accounts/{account.id}",
        json={"name": "Renamed"},
    )

    assert response.status_code == 200
    # A name-only update must not re-verify: no GitHub client constructed.
    mock_client_class.assert_not_called()
    db_session.refresh(account)
    assert account.name == "Renamed"
    assert account.verified_login == "octocat"


def test_update_account_not_owned(regular_client, db_session, user, other_user):
    """Update account fails when not owned by user."""
    account = GithubAccount(
        user_id=other_user.id,
        name="Other User Account",
        auth_type="pat",
        access_token="token123",
    )
    db_session.add(account)
    db_session.commit()

    response = regular_client.patch(
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


def test_delete_account_not_owned(regular_client, db_session, user, other_user):
    """Delete account fails when not owned."""
    account = GithubAccount(
        user_id=other_user.id,
        name="Other User Account",
        auth_type="pat",
        access_token="token123",
    )
    db_session.add(account)
    db_session.commit()

    response = regular_client.delete(f"/github/accounts/{account.id}")

    assert response.status_code == 404


# ====== POST /github/accounts/{account_id}/validate tests ======


@patch("memory.api.github_sources.GithubClient")
def test_validate_account_persists_verified_login(
    mock_client_class, client, db_session, user
):
    """A successful validation (re)stores the GitHub-verified login."""
    mock_client_class.return_value = make_github_client_mock("octocat")
    account = GithubAccount(
        user_id=user.id,
        name="Test Account",
        auth_type="pat",
        access_token="token123",
    )
    db_session.add(account)
    db_session.commit()
    assert account.verified_login is None

    response = client.post(f"/github/accounts/{account.id}/validate")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["user"] == "octocat"
    db_session.refresh(account)
    assert account.verified_login == "octocat"


@patch("memory.api.github_sources.GithubClient")
def test_validate_account_error_leaves_verified_login(
    mock_client_class, client, db_session, user
):
    """A failed validation reports an error and does not overwrite identity."""
    mock_client_class.return_value = make_github_client_mock(None)
    account = GithubAccount(
        user_id=user.id,
        name="Test Account",
        auth_type="pat",
        access_token="token123",
        verified_login="octocat",
    )
    db_session.add(account)
    db_session.commit()

    response = client.post(f"/github/accounts/{account.id}/validate")

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    db_session.refresh(account)
    assert account.verified_login == "octocat"


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


# ====== /github/projects access-control tests ======
#
# `GithubProject.account_id -> GithubAccount.user_id` is the ownership
# chain. Three sibling endpoints used to ignore it: list, get, delete
# all walked the table by primary key with no filter, which let any
# authenticated user read or destroy any other user's tracked GitHub
# Projects (including titles, descriptions, and READMEs that may have
# been pulled from private repos).


from memory.common.db.models import GithubProject  # noqa: E402


def _make_project(db_session, account: GithubAccount, *, title: str) -> GithubProject:
    project = GithubProject(
        account_id=account.id,
        node_id=f"node-{title}",
        number=1,
        owner_type="user",
        owner_login="someone",
        title=title,
        url=f"https://github.com/orgs/someone/projects/1#{title}",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


def test_list_projects_returns_only_callers_projects(
    regular_client, db_session, user, other_user
):
    """Non-admin GET /github/projects must omit other users' projects."""
    mine = GithubAccount(
        user_id=user.id,
        name="Mine",
        auth_type="pat",
        access_token="t1",
    )
    theirs = GithubAccount(
        user_id=other_user.id,
        name="Theirs",
        auth_type="pat",
        access_token="t2",
    )
    db_session.add_all([mine, theirs])
    db_session.commit()
    _make_project(db_session, mine, title="Mine-Project")
    _make_project(db_session, theirs, title="Theirs-Project")

    response = regular_client.get("/github/projects")

    assert response.status_code == 200
    titles = {p["title"] for p in response.json()}
    assert titles == {"Mine-Project"}


def test_list_projects_admin_sees_all_users(
    client, db_session, user, other_user
):
    """Admin (default test client) sees every user's projects with no user_id filter."""
    mine = GithubAccount(
        user_id=user.id,
        name="Mine",
        auth_type="pat",
        access_token="t1",
    )
    theirs = GithubAccount(
        user_id=other_user.id,
        name="Theirs",
        auth_type="pat",
        access_token="t2",
    )
    db_session.add_all([mine, theirs])
    db_session.commit()
    _make_project(db_session, mine, title="Mine-Project")
    _make_project(db_session, theirs, title="Theirs-Project")

    response = client.get("/github/projects")

    assert response.status_code == 200
    titles = {p["title"] for p in response.json()}
    assert titles == {"Mine-Project", "Theirs-Project"}


def test_delete_project_404_when_not_owner(
    regular_client, db_session, other_user
):
    """DELETE /github/projects/<id> for another user's project must 404 and not delete."""
    theirs = GithubAccount(
        user_id=other_user.id,
        name="Theirs",
        auth_type="pat",
        access_token="t",
    )
    db_session.add(theirs)
    db_session.commit()
    project = _make_project(db_session, theirs, title="Theirs-Project")

    response = regular_client.delete(f"/github/projects/{project.id}")

    assert response.status_code == 404
    # Project must still exist.
    db_session.expire_all()
    assert db_session.get(GithubProject, project.id) is not None


def test_delete_project_succeeds_for_owner(client, db_session, user):
    """Owner can delete their own project."""
    mine = GithubAccount(
        user_id=user.id,
        name="Mine",
        auth_type="pat",
        access_token="t",
    )
    db_session.add(mine)
    db_session.commit()
    project = _make_project(db_session, mine, title="Mine-Project")

    response = client.delete(f"/github/projects/{project.id}")

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(GithubProject, project.id) is None


# ====== Webhook installation ======


@pytest.fixture
def queued():
    with patch("memory.api.github_sources.celery_app") as app:
        app.send_task.return_value.id = "task-1"
        yield app.send_task


def hooked_account(db_session, user, secret: str | None = "s3cret") -> GithubAccount:
    account = GithubAccount(user_id=user.id, name="Hooked", auth_type="pat", access_token="t")
    account.webhook_secret = secret
    db_session.add(account)
    db_session.commit()
    return account


def sent(queued) -> list[tuple[str, list]]:
    return [(c.args[0].rsplit(".", 1)[-1], c.kwargs["args"]) for c in queued.call_args_list]


@pytest.mark.parametrize("secret, expected", [("s3cret", ["ensure_github_webhooks"]), (None, [])])
@patch("memory.api.github_sources.GithubClient")
def test_adding_a_repo_queues_its_webhook_when_the_account_has_a_secret(
    mock_client_class, client, db_session, user, queued, secret, expected
):
    mock_client_class.return_value.get_repo.return_value = {"id": 1, "owner": {"login": "o"}, "name": "r"}
    account = hooked_account(db_session, user, secret)

    client.post(f"/github/accounts/{account.id}/repos", json={"owner": "o", "name": "r"})

    assert sent(queued) == [(task, [account.id]) for task in expected]


@pytest.mark.parametrize("active, expected", [(True, ["ensure_github_webhooks"]), (False, [])])
def test_reactivating_a_repo_queues_its_webhook(client, db_session, user, queued, active, expected):
    account = hooked_account(db_session, user)
    repo = GithubRepo(account_id=account.id, owner="o", name="r", active=not active)
    db_session.add(repo)
    db_session.commit()

    client.patch(f"/github/accounts/{account.id}/repos/{repo.id}", json={"active": active})

    assert sent(queued) == [(task, [account.id]) for task in expected]


@pytest.mark.parametrize(
    "method, task", [("post", "ensure_github_webhooks"), ("delete", "remove_github_webhooks")]
)
def test_changing_the_secret_reconciles_the_hooks(client, db_session, user, queued, method, task):
    account = hooked_account(db_session, user)

    getattr(client, method)(f"/github/accounts/{account.id}/webhook-secret")

    assert sent(queued) == [(task, [account.id])]


@pytest.mark.parametrize("secret", ["s3cret", None])
def test_install_queues_with_a_secret_generating_one_if_needed(client, db_session, user, queued, secret):
    account = hooked_account(db_session, user, secret)

    response = client.post(f"/github/accounts/{account.id}/webhooks/install")

    assert response.json()["status"] == "queued"
    assert sent(queued) == [("ensure_github_webhooks", [account.id])]
    db_session.refresh(account)
    assert account.webhook_secret
    assert account.webhook_secret == (secret or account.webhook_secret)


def test_repo_responses_carry_the_webhook_health(client, db_session, user):
    account = hooked_account(db_session, user)
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    repo = GithubRepo(
        account_id=account.id, owner="o", name="r", webhook_status="installed",
        last_event_at=at, push_missing_since=at, push_last_miss_at=at,
    )
    db_session.add(repo)
    db_session.commit()

    (listed,) = client.get(f"/github/accounts/{account.id}").json()["repos"]

    fields = ("webhook_status", "last_event_at", "push_missing_since", "push_last_miss_at", "push_stale")
    assert {k: listed[k] for k in fields} == {
        "webhook_status": "installed",
        "last_event_at": at.isoformat(),
        "push_missing_since": at.isoformat(),
        "push_last_miss_at": at.isoformat(),
        "push_stale": True,
    }


@pytest.mark.parametrize(
    "status, hours_since_event, stale",
    [("installed", 1, False), ("org_hook", None, True), (None, None, False), ("missing_permission", 48, False)],
)
def test_a_hooked_repo_without_recent_events_is_flagged_stale(client, db_session, user, status, hours_since_event, stale):
    account = hooked_account(db_session, user)
    last_event = hours_since_event and datetime.now(timezone.utc) - timedelta(hours=hours_since_event)
    db_session.add(GithubRepo(account_id=account.id, owner="o", name="r", webhook_status=status, last_event_at=last_event))
    db_session.commit()

    (listed,) = client.get(f"/github/accounts/{account.id}").json()["repos"]

    assert listed["push_stale"] is stale


@pytest.mark.parametrize(
    "repo_events, stale",
    [([1], False), ([48], True), ([None], True), ([48, 1], False), ([], True)],
)
def test_an_account_without_recent_events_on_any_repo_is_flagged_stale(client, db_session, user, repo_events, stale):
    account = hooked_account(db_session, user)
    now = datetime.now(timezone.utc)
    for i, hours in enumerate(repo_events):
        last_event = hours and now - timedelta(hours=hours)
        db_session.add(GithubRepo(account_id=account.id, owner="o", name=f"r{i}", last_event_at=last_event))
    db_session.commit()

    listed = client.get(f"/github/accounts/{account.id}").json()

    assert listed["push_stale"] is stale
