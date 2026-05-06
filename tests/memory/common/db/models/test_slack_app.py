"""Tests for the SlackApp model and its relationships."""

import pytest
from sqlalchemy.exc import IntegrityError

from memory.common.db.models import (
    HumanUser,
    SlackApp,
    SlackUserCredentials,
    SlackWorkspace,
)


def make_app(**overrides) -> SlackApp:
    defaults = dict(client_id="123.456", name="Test App")
    defaults.update(overrides)
    return SlackApp(**defaults)


def test_create_slack_app_minimal(db_session):
    app = make_app()
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
    app = make_app()
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
    app = make_app()
    app.client_secret = "secret"
    db_session.add(app)
    db_session.commit()

    app.client_secret = None
    db_session.commit()

    assert app.client_secret_encrypted is None
    assert app.client_secret is None


def test_slack_app_client_id_unique(db_session):
    db_session.add(make_app(client_id="dupe.999"))
    db_session.commit()

    db_session.add(make_app(client_id="dupe.999", name="Duplicate"))
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.parametrize(
    "state",
    ["draft", "signing_verified", "live", "degraded"],
)
def test_slack_app_setup_state_accepts_valid(db_session, state):
    app = make_app(client_id=f"valid.{state}", setup_state=state)
    db_session.add(app)
    db_session.commit()
    assert app.setup_state == state


def test_slack_app_setup_state_rejects_invalid(db_session):
    app = make_app(client_id="bad.state", setup_state="bogus")
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

    app = make_app(created_by_user_id=owner.id)
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

    app = make_app(created_by_user_id=owner.id)
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

    app = make_app(created_by_user_id=owner.id)
    db_session.add(app)
    db_session.commit()

    db_session.delete(owner)
    db_session.commit()
    db_session.refresh(app)

    assert app.created_by_user_id is None


@pytest.mark.parametrize(
    "user_id_value",
    [None, 999_999],
    ids=["unsaved_user_id_none", "real_but_unrelated_user"],
)
def test_slack_app_with_null_owner_is_not_authorized(db_session, user_id_value):
    """Regression: an app with `created_by_user_id IS NULL` (state proven
    reachable by `test_slack_app_owner_set_null_on_user_delete`) must not
    incidentally authorize a user just because their id is also None or
    happens to match no row.

    The current `is_authorized` comparison is correct only because of
    Python's `None == int` semantics; a flipped operand order would
    silently match user-with-id-None to app-with-NULL-owner. This test
    pins the contract.
    """
    other = HumanUser.create_with_password(
        email="other@example.com", name="Other", password="password123"
    )
    db_session.add(other)
    db_session.commit()

    app = make_app(created_by_user_id=None)
    db_session.add(app)
    db_session.commit()

    # Build a user-shaped probe with the parametrized id value (we don't
    # need a persisted user here — `is_authorized` only inspects user.id
    # and self.authorized_users membership).
    probe = HumanUser(email="probe@example.com", name="Probe", password_hash="x")
    probe.id = user_id_value  # type: ignore[assignment]

    assert app.is_authorized(probe) is False
    assert app.is_owner(probe) is False
    # Other real user (definitely not the owner of this NULL-owner app):
    assert app.is_authorized(other) is False
    assert app.is_owner(other) is False


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
    app = make_app()
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
    app1 = make_app(client_id="app.1", name="App 1")
    app2 = make_app(client_id="app.2", name="App 2")
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
    app = make_app()
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

    app = make_app()
    app.authorized_users.append(user)
    db_session.add(app)
    db_session.commit()
    app_id = app.id

    db_session.delete(user)
    db_session.commit()

    refreshed = db_session.get(SlackApp, app_id)
    assert refreshed is not None
    assert refreshed.authorized_users == []


# ---------------------------------------------------------------------------
# Migration regression: ON CONFLICT must strip squatter ownership
# ---------------------------------------------------------------------------


# This SQL mirrors the upgrade() logic in
# db/migrations/versions/20260506_slack_apps.py. Tested separately so we
# can exercise the conflict path without re-running the full alembic
# migration (which requires a fresh DB).
LEGACY_BACKFILL_SQL = """
INSERT INTO slack_apps (
    client_id, name, client_secret_encrypted,
    setup_state, is_active, created_by_user_id
)
VALUES (
    :client_id, :name, :secret, 'live', true, NULL
)
ON CONFLICT (client_id) DO UPDATE
    SET created_by_user_id = NULL,
        client_secret_encrypted = COALESCE(
            EXCLUDED.client_secret_encrypted,
            slack_apps.client_secret_encrypted
        ),
        name = EXCLUDED.name,
        setup_state = 'live',
        is_active = true,
        updated_at = now()
RETURNING id
"""


def test_legacy_backfill_strips_squatter_ownership(db_session):
    """If a draft SlackApp was claimed by an attacker before the legacy
    env-var migration runs, the migration MUST strip ownership so the
    attacker doesn't end up owning the migrated production credentials.

    Regression for slack-changes.md §3.1 squatting + CWE-639.
    """
    from sqlalchemy import text

    attacker = HumanUser(
        email="attacker@example.com",
        name="Attacker",
        password_hash="x",
    )
    db_session.add(attacker)
    db_session.commit()

    squatted = SlackApp(
        client_id="prod.client.id",
        name="Squatter App",
        setup_state="draft",
        created_by_user_id=attacker.id,
    )
    squatted.client_secret = "attacker-supplied-junk"
    db_session.add(squatted)
    db_session.commit()
    squatted_id = squatted.id
    squatter_secret_blob = squatted.client_secret_encrypted

    # Simulate the migration running with a real env-var-supplied secret.
    from memory.common.db.models.secrets import encrypt_value

    real_secret_blob = encrypt_value("real-env-var-secret")

    result = db_session.execute(
        text(LEGACY_BACKFILL_SQL),
        {
            "client_id": "prod.client.id",
            "name": "Default (env-var migration)",
            "secret": real_secret_blob,
        },
    )
    resolved_id = result.scalar_one()
    db_session.commit()

    # Same row was reused (id matches; we did not create a new row).
    assert resolved_id == squatted_id

    db_session.expire_all()
    refreshed = db_session.get(SlackApp, squatted_id)
    assert refreshed is not None
    # Squatter's ownership stripped.
    assert refreshed.created_by_user_id is None
    # Squatter's secret blob replaced with the env-var truth.
    assert refreshed.client_secret_encrypted == real_secret_blob
    assert refreshed.client_secret_encrypted != squatter_secret_blob
    # Setup state forced to 'live' — env-var deployment is past verification.
    assert refreshed.setup_state == "live"
    assert refreshed.is_active is True
    # Name overwritten with migration's canonical name.
    assert refreshed.name == "Default (env-var migration)"


def test_legacy_backfill_preserves_existing_secret_when_env_unset(db_session):
    """If the env-var SLACK_CLIENT_SECRET is NOT set, the migration passes
    NULL for `secret`. The COALESCE guard must keep whatever secret the
    pre-existing row had — we don't want to null out a legitimately
    wizard-set secret just because someone deploys without the env var.
    """
    from sqlalchemy import text

    legitimate = SlackApp(
        client_id="prod.client.id",
        name="Wizard-created App",
        setup_state="live",
    )
    legitimate.client_secret = "wizard-set-secret"
    db_session.add(legitimate)
    db_session.commit()
    original_blob = legitimate.client_secret_encrypted

    # Migration without env-var secret (None passed for `secret`).
    db_session.execute(
        text(LEGACY_BACKFILL_SQL),
        {
            "client_id": "prod.client.id",
            "name": "Default (env-var migration)",
            "secret": None,
        },
    )
    db_session.commit()

    db_session.expire_all()
    refreshed = db_session.get(SlackApp, legitimate.id)
    assert refreshed is not None
    # Existing secret preserved (COALESCE kept it).
    assert refreshed.client_secret_encrypted == original_blob
    # But ownership still stripped (the squatter-protection invariant).
    assert refreshed.created_by_user_id is None


def test_legacy_backfill_creates_new_row_when_no_conflict(db_session):
    """No pre-existing row → INSERT path. Sanity check that the rewritten
    statement still creates a fresh row and returns its id."""
    from sqlalchemy import text

    result = db_session.execute(
        text(LEGACY_BACKFILL_SQL),
        {
            "client_id": "first.time.client.id",
            "name": "Default (env-var migration)",
            "secret": None,
        },
    )
    new_id = result.scalar_one()
    db_session.commit()

    fresh = db_session.get(SlackApp, new_id)
    assert fresh is not None
    assert fresh.client_id == "first.time.client.id"
    assert fresh.created_by_user_id is None
    assert fresh.setup_state == "live"
    assert fresh.is_active is True
