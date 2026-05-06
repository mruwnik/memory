"""Tests for the SlackApp model and its relationships."""

import pytest
from sqlalchemy.exc import IntegrityError

from memory.common.db.models import (
    HumanUser,
    SlackApp,
    SlackUserCredentials,
    SlackWorkspace,
)


def _make_app(**overrides) -> SlackApp:
    defaults = dict(client_id="123.456", name="Test App")
    defaults.update(overrides)
    return SlackApp(**defaults)


def test_create_slack_app_minimal(db_session):
    app = _make_app()
    db_session.add(app)
    db_session.commit()

    assert app.id is not None
    assert app.client_id == "123.456"
    assert app.name == "Test App"
    assert app.setup_state == "draft"
    assert app.is_active is True
    assert app.client_secret is None
    assert app.signing_secret is None


def test_slack_app_secrets_encrypted_at_rest(db_session):
    app = _make_app()
    app.client_secret = "csecret-abc"
    app.signing_secret = "sigsec-xyz"
    db_session.add(app)
    db_session.commit()

    assert app.client_secret_encrypted is not None
    assert app.signing_secret_encrypted is not None
    assert app.client_secret_encrypted != b"csecret-abc"
    assert app.signing_secret_encrypted != b"sigsec-xyz"

    assert app.client_secret == "csecret-abc"
    assert app.signing_secret == "sigsec-xyz"


def test_slack_app_secrets_clearable(db_session):
    app = _make_app()
    app.client_secret = "secret"
    db_session.add(app)
    db_session.commit()

    app.client_secret = None
    db_session.commit()

    assert app.client_secret_encrypted is None
    assert app.client_secret is None


def test_slack_app_client_id_unique(db_session):
    db_session.add(_make_app(client_id="dupe.999"))
    db_session.commit()

    db_session.add(_make_app(client_id="dupe.999", name="Duplicate"))
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.parametrize(
    "state",
    ["draft", "signing_verified", "live", "degraded"],
)
def test_slack_app_setup_state_accepts_valid(db_session, state):
    app = _make_app(client_id=f"valid.{state}", setup_state=state)
    db_session.add(app)
    db_session.commit()
    assert app.setup_state == state


def test_slack_app_setup_state_rejects_invalid(db_session):
    app = _make_app(client_id="bad.state", setup_state="bogus")
    db_session.add(app)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_slack_app_owner_is_authorized(db_session):
    owner = HumanUser.create_with_password(
        email="owner@example.com", name="Owner", password="password123"
    )
    other = HumanUser.create_with_password(
        email="other@example.com", name="Other", password="password123"
    )
    db_session.add_all([owner, other])
    db_session.commit()

    app = _make_app(created_by_user_id=owner.id)
    db_session.add(app)
    db_session.commit()

    assert app.is_owner(owner) is True
    assert app.is_owner(other) is False
    assert app.is_authorized(owner) is True
    assert app.is_authorized(other) is False


def test_slack_app_authorized_users_extends_owner(db_session):
    owner = HumanUser.create_with_password(
        email="owner@example.com", name="Owner", password="password123"
    )
    member = HumanUser.create_with_password(
        email="member@example.com", name="Member", password="password123"
    )
    db_session.add_all([owner, member])
    db_session.commit()

    app = _make_app(created_by_user_id=owner.id)
    app.authorized_users.append(member)
    db_session.add(app)
    db_session.commit()

    assert app.is_authorized(owner) is True
    assert app.is_authorized(member) is True
    assert app.is_owner(member) is False
    assert app in member.slack_apps


def test_slack_app_owner_set_null_on_user_delete(db_session):
    owner = HumanUser.create_with_password(
        email="owner@example.com", name="Owner", password="password123"
    )
    db_session.add(owner)
    db_session.commit()

    app = _make_app(created_by_user_id=owner.id)
    db_session.add(app)
    db_session.commit()

    db_session.delete(owner)
    db_session.commit()
    db_session.refresh(app)

    assert app.created_by_user_id is None


def test_slack_credential_requires_app(db_session):
    user = HumanUser.create_with_password(
        email="u@example.com", name="U", password="password123"
    )
    workspace = SlackWorkspace(id="T1", name="WS")
    db_session.add_all([user, workspace])
    db_session.commit()

    cred = SlackUserCredentials(
        workspace_id=workspace.id,
        user_id=user.id,
    )
    db_session.add(cred)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_slack_credential_uniqueness_per_app_workspace_user(db_session):
    app = _make_app()
    user = HumanUser.create_with_password(
        email="u@example.com", name="U", password="password123"
    )
    workspace = SlackWorkspace(id="T1", name="WS")
    db_session.add_all([app, user, workspace])
    db_session.commit()

    cred1 = SlackUserCredentials(
        slack_app_id=app.id,
        workspace_id=workspace.id,
        user_id=user.id,
    )
    db_session.add(cred1)
    db_session.commit()

    cred_dupe = SlackUserCredentials(
        slack_app_id=app.id,
        workspace_id=workspace.id,
        user_id=user.id,
    )
    db_session.add(cred_dupe)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_slack_credential_same_user_different_apps_allowed(db_session):
    app1 = _make_app(client_id="app.1", name="App 1")
    app2 = _make_app(client_id="app.2", name="App 2")
    user = HumanUser.create_with_password(
        email="u@example.com", name="U", password="password123"
    )
    workspace = SlackWorkspace(id="T1", name="WS")
    db_session.add_all([app1, app2, user, workspace])
    db_session.commit()

    cred1 = SlackUserCredentials(
        slack_app_id=app1.id, workspace_id=workspace.id, user_id=user.id
    )
    cred2 = SlackUserCredentials(
        slack_app_id=app2.id, workspace_id=workspace.id, user_id=user.id
    )
    db_session.add_all([cred1, cred2])
    db_session.commit()

    assert {c.slack_app_id for c in user.slack_credentials} == {app1.id, app2.id}


def test_slack_app_cascades_to_credentials(db_session):
    app = _make_app()
    user = HumanUser.create_with_password(
        email="u@example.com", name="U", password="password123"
    )
    workspace = SlackWorkspace(id="T1", name="WS")
    db_session.add_all([app, user, workspace])
    db_session.commit()

    cred = SlackUserCredentials(
        slack_app_id=app.id, workspace_id=workspace.id, user_id=user.id
    )
    db_session.add(cred)
    db_session.commit()
    cred_id = cred.id

    db_session.delete(app)
    db_session.commit()

    assert db_session.get(SlackUserCredentials, cred_id) is None


def test_slack_app_user_relationship_cascade_on_user_delete(db_session):
    user = HumanUser.create_with_password(
        email="u@example.com", name="U", password="password123"
    )
    db_session.add(user)
    db_session.commit()

    app = _make_app()
    app.authorized_users.append(user)
    db_session.add(app)
    db_session.commit()
    app_id = app.id

    db_session.delete(user)
    db_session.commit()

    refreshed = db_session.get(SlackApp, app_id)
    assert refreshed is not None
    assert refreshed.authorized_users == []
