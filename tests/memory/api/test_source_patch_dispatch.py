"""A data-source PATCH that changes access control dispatches reconciliation once.

The ORM listener in ``models/access_control_events.py`` is the only
dispatcher: it bumps ``config_version`` by one and sends one
``update_source_access_control`` task per changed source.
"""

import pytest

from memory.common.celery_app import UPDATE_SOURCE_ACCESS_CONTROL
from memory.common.db.models import (
    CalendarAccount,
    DiscordBot,
    DiscordChannel,
    DiscordServer,
    EmailAccount,
    GoogleAccount,
    GoogleFolder,
    SlackApp,
    SlackChannel,
    SlackUserCredentials,
    SlackWorkspace,
    TranscriptAccount,
)


def make_discord_server(user):
    bot = DiscordBot(id=11111, application_id=11111, name="Bot")
    bot.created_by_user_id = user.id
    return DiscordServer(id=44444, name="Guild", bot=bot, verified=True)


def make_discord_channel(user):
    server = make_discord_server(user)
    return DiscordChannel(id=33333, name="general", channel_type="text", server=server)


def make_slack_workspace(user):
    workspace = SlackWorkspace(id="T12345678", name="Workspace")
    app = SlackApp(client_id="test.client.id", name="App", setup_state="live")
    credentials = SlackUserCredentials(
        slack_app=app,
        workspace=workspace,
        user_id=user.id,
        scopes=["channels:read"],
        slack_user_id="U_TEST_USER",
    )
    credentials.access_token = "xoxp-test-token"
    return workspace


def make_slack_channel(user):
    return SlackChannel(
        id="C12345678",
        workspace=make_slack_workspace(user),
        name="general",
        channel_type="channel",
        is_private=False,
        is_archived=False,
    )


def make_email_account(user):
    return EmailAccount(
        user_id=user.id,
        name="Mail",
        email_address="test@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        username="user",
        password="pass",
    )


def make_calendar_account(user):
    return CalendarAccount(
        name="Cal",
        calendar_type="caldav",
        caldav_url="https://cal.example.com",
        caldav_username="user",
        caldav_password="pass",
    )


def make_transcript_account(user):
    account = TranscriptAccount(user_id=user.id, name="Notes", provider="fireflies", tags=[])
    account.api_key = "ff_test_key"
    return account


def make_google_folder(user):
    account = GoogleAccount(user_id=user.id, name="Drive", email="drive@example.com")
    return GoogleFolder(account=account, folder_id="f", folder_name="F")


@pytest.mark.parametrize(
    "make_source, url",
    [
        (make_discord_server, "/discord/servers/{id}"),
        (make_discord_channel, "/discord/channels/{id}"),
        (make_slack_workspace, "/slack/workspaces/{id}"),
        (make_slack_channel, "/slack/channels/{id}"),
        (make_email_account, "/email-accounts/{id}"),
        (make_calendar_account, "/calendar-accounts/{id}"),
        (make_transcript_account, "/transcript-accounts/{id}"),
        (
            make_google_folder,
            "/google-drive/accounts/{account_id}/folders/{id}",
        ),
    ],
)
def test_sensitivity_patch_dispatches_once(
    client, db_session, user, no_celery_dispatch, make_source, url
):
    source = make_source(user)
    source.sensitivity = "basic"
    db_session.add(source)
    db_session.commit()
    version_before = source.config_version or 0
    no_celery_dispatch.reset_mock()

    response = client.patch(
        url.format(id=source.id, account_id=getattr(source, "account_id", None)),
        json={"sensitivity": "confidential"},
    )

    assert response.status_code == 200
    db_session.refresh(source)
    assert source.config_version == version_before + 1
    assert [call.args[0] for call in no_celery_dispatch.call_args_list] == [
        UPDATE_SOURCE_ACCESS_CONTROL
    ]
    assert no_celery_dispatch.call_args.kwargs["args"] == [
        source.data_source_type,
        source.id,
        source.config_version,
    ]
