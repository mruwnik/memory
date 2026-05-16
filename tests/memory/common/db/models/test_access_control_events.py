"""Tests for the data-source access-control dispatch listeners."""

import pytest

from memory.common.celery_app import UPDATE_SOURCE_ACCESS_CONTROL
from memory.common.db.models import EmailAccount, Project


def make_account(db_session, test_user, **overrides):
    """Create and commit an EmailAccount for dispatch-listener tests."""
    account = EmailAccount(
        name="AC Events Account",
        email_address="ac-events@example.com",
        imap_server="imap.example.com",
        imap_port=993,
        username="ac-events@example.com",
        password="pw",
        use_ssl=True,
        folders=["INBOX"],
        tags=[],
        active=True,
        user_id=test_user.id,
        **overrides,
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.mark.transactional_db
def test_project_id_change_bumps_version_and_dispatches(
    db_session, test_user, no_celery_dispatch
):
    """Changing a data source's project_id bumps config_version and
    dispatches update_source_access_control once, after commit."""
    project = Project(title="AC Events Project", state="open")
    db_session.add(project)
    db_session.commit()

    account = make_account(db_session, test_user)
    version_before = account.config_version
    no_celery_dispatch.reset_mock()  # ignore dispatches from setup commits

    account.project_id = project.id
    db_session.commit()

    assert account.config_version == version_before + 1

    no_celery_dispatch.assert_called_once()
    call = no_celery_dispatch.call_args
    assert call.args[0] == UPDATE_SOURCE_ACCESS_CONTROL
    assert call.kwargs["args"] == [
        "email_account",
        account.id,
        account.config_version,
    ]


@pytest.mark.transactional_db
def test_sensitivity_change_dispatches(db_session, test_user, no_celery_dispatch):
    """A sensitivity change also triggers reconciliation dispatch."""
    account = make_account(db_session, test_user, sensitivity="basic")
    version_before = account.config_version
    no_celery_dispatch.reset_mock()

    account.sensitivity = "confidential"
    db_session.commit()

    assert account.config_version == version_before + 1
    no_celery_dispatch.assert_called_once()
    assert no_celery_dispatch.call_args.kwargs["args"][0] == "email_account"


@pytest.mark.transactional_db
def test_non_ac_field_change_does_not_dispatch(
    db_session, test_user, no_celery_dispatch
):
    """Editing a non-access-control field leaves config_version alone and
    dispatches nothing."""
    account = make_account(db_session, test_user)
    version_before = account.config_version
    no_celery_dispatch.reset_mock()

    account.name = "Renamed Account"
    db_session.commit()

    assert account.config_version == version_before
    no_celery_dispatch.assert_not_called()


@pytest.mark.transactional_db
def test_source_creation_does_not_dispatch(
    db_session, test_user, no_celery_dispatch
):
    """Creating a source dispatches nothing — a brand-new source has no
    content items to reconcile (only session.dirty is considered)."""
    no_celery_dispatch.reset_mock()
    make_account(db_session, test_user)
    no_celery_dispatch.assert_not_called()


@pytest.mark.transactional_db
def test_noop_ac_field_assignment_does_not_dispatch(
    db_session, test_user, no_celery_dispatch
):
    """Re-assigning project_id / sensitivity to their current values is a
    no-op: no config_version bump, no dispatch. Guards against every config
    PATCH (the endpoints assign these fields unconditionally) triggering a
    full reconciliation."""
    project = Project(title="No-op Project", state="open")
    db_session.add(project)
    db_session.commit()

    account = make_account(
        db_session, test_user, project_id=project.id, sensitivity="internal"
    )
    version_before = account.config_version
    no_celery_dispatch.reset_mock()

    # Equal-value re-assignment — the shape the update endpoints produce.
    account.project_id = project.id
    account.sensitivity = "internal"
    account.name = "Renamed In The Same Save"
    db_session.commit()

    assert account.config_version == version_before
    no_celery_dispatch.assert_not_called()
