"""Tests for the data-source access-control dispatch listeners."""

import pytest

from memory.common.celery_app import UPDATE_SOURCE_ACCESS_CONTROL
from memory.common.db.models import DiscordChannel, DiscordServer, EmailAccount, Project


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


@pytest.mark.transactional_db
def test_ac_change_advances_updated_at(db_session, test_user, no_celery_dispatch):
    """A config change advances updated_at even on a source model without
    onupdate=func.now() (EmailAccount). The frequent reconciliation sweep
    filters on updated_at, so it must move when project_id/sensitivity does."""
    project = Project(title="Updated-At Project", state="open")
    db_session.add(project)
    db_session.commit()

    account = make_account(db_session, test_user)
    updated_before = account.updated_at

    account.project_id = project.id
    db_session.commit()

    assert account.updated_at > updated_before


@pytest.mark.transactional_db
def test_moving_a_discord_channel_to_another_server_dispatches(db_session, no_celery_dispatch):
    """A channel's content inherits from its server, so a new server re-resolves it."""
    db_session.add_all([DiscordServer(id=1, name="one"), DiscordServer(id=2, name="two")])
    db_session.flush()
    channel = DiscordChannel(id=3, server_id=1, name="general", channel_type="text")
    db_session.add(channel)
    db_session.commit()
    no_celery_dispatch.reset_mock()

    channel.server_id = 2
    db_session.commit()

    assert no_celery_dispatch.call_args.kwargs["args"] == ["discord_channel", 3, channel.config_version]
