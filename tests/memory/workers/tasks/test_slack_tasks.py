"""Tests for Slack Celery tasks."""

import contextlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from unittest.mock import MagicMock, patch

import httpx
import pytest
import redis
from sqlalchemy.exc import InterfaceError, OperationalError

from memory.common.celery_app import SEND_NOTIFICATION, SYNC_SLACK_CHANNEL, SYNC_SLACK_WORKSPACE
from memory.common.db.models import Event, User
from memory.common.db.models.slack import (
    SlackApp,
    SlackChannel,
    SlackUserCredentials,
    SlackWorkspace,
)
from memory.common.db.models.source_items import SlackMessage
from memory.common.redis_lock import Lock
from memory.common.safe_http import BlockedDestination
from memory.common.slack import SlackAPIError
from memory.workers.tasks import slack


@pytest.fixture
def slack_user(db_session):
    """Create a test user for Slack workspace ownership."""
    user = User(
        name="Test User",
        email="test@example.com",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def slack_app(db_session):
    """Create a SlackApp row for tests that need credentials."""
    app = SlackApp(
        client_id="test.client.id",
        name="Test Slack App",
        setup_state="live",
    )
    db_session.add(app)
    db_session.commit()
    return app


@pytest.fixture
def slack_workspace(db_session):
    """Create a Slack workspace for testing."""
    workspace = SlackWorkspace(
        id="T12345678",
        name="Test Workspace",
        collect_messages=True,
    )
    db_session.add(workspace)
    db_session.commit()
    return workspace


@pytest.fixture
def slack_credentials(db_session, slack_app, slack_workspace, slack_user):
    """Create Slack credentials for testing."""
    credentials = SlackUserCredentials(
        slack_app_id=slack_app.id,
        workspace_id=slack_workspace.id,
        user_id=slack_user.id,
        scopes=["channels:read", "chat:write"],
        slack_user_id="U_TEST_USER",
    )
    credentials.access_token = "xoxp-test-token"
    db_session.add(credentials)
    db_session.commit()
    return credentials


@pytest.fixture
def slack_channel(db_session, slack_workspace):
    """Create a Slack channel for testing."""
    channel = SlackChannel(
        id="C12345678",
        workspace_id=slack_workspace.id,
        name="general",
        channel_type="channel",
        is_private=False,
        is_archived=False,
        collect_messages=True,
    )
    db_session.add(channel)
    db_session.commit()
    return channel


@pytest.fixture
def sample_message_data(slack_workspace, slack_channel, slack_app):
    """Sample message data for testing."""
    return {
        "workspace_id": slack_workspace.id,
        "channel_id": slack_channel.id,
        "message_ts": "1704067200.000100",
        "author_id": "U12345678",  # Just a Slack user ID
        "content": "This is a test Slack message with enough content to be processed properly.",
        "slack_app_id": slack_app.id,
        "thread_ts": None,
        "reply_count": None,
        "subtype": None,
        "edited_ts": None,
        "reactions": None,
        "files": None,
    }


@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_store_slack_message_success(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_credentials, qdrant
):
    """Test successful Slack message storage."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {"U12345678": "Test User"}

    result = slack.store_slack_message(**sample_message_data)

    assert result["status"] == "processed"
    assert "slackmessage_id" in result

    # Verify the message was created in the database
    message = (
        db_session.query(SlackMessage)
        .filter_by(message_ts=sample_message_data["message_ts"])
        .first()
    )
    assert message is not None
    assert message.content == sample_message_data["content"]
    assert message.workspace_id == sample_message_data["workspace_id"]
    assert message.channel_id == sample_message_data["channel_id"]
    assert message.author_id == sample_message_data["author_id"]
    assert message.author_name == "Test User"


@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_dm_synced_through_another_users_authorization_gets_no_creator(
    mock_build_cache,
    mock_get_creds,
    db_session,
    sample_message_data,
    slack_credentials,
    slack_app,
    slack_workspace,
    qdrant,
):
    """The app's registrar must not become the creator of DMs that another
    user's authorization collected: creators bypass project and sensitivity."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {}
    registrar = User(
        name="Registrar", email="registrar@example.com", password_hash="x"
    )
    db_session.add(registrar)
    db_session.flush()
    slack_app.created_by_user_id = registrar.id
    dm = SlackChannel(
        id="D12345678",
        workspace_id=slack_workspace.id,
        name="dm",
        channel_type="im",
        is_private=True,
        is_archived=False,
        collect_messages=True,
    )
    db_session.add(dm)
    db_session.commit()

    slack.store_slack_message(**sample_message_data | {"channel_id": dm.id})

    message = (
        db_session.query(SlackMessage)
        .filter_by(message_ts=sample_message_data["message_ts"])
        .one()
    )
    assert message.creator_id is None


@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_store_slack_message_already_exists(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_credentials, qdrant
):
    """Test adding a message that already exists."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {"U12345678": "Test User"}

    # Add the message once
    slack.store_slack_message(**sample_message_data)

    # Try to add it again
    result = slack.store_slack_message(**sample_message_data)

    assert result["status"] == "already_exists"
    assert result["message_ts"] == sample_message_data["message_ts"]

    # Verify no duplicate was created
    messages = (
        db_session.query(SlackMessage)
        .filter_by(message_ts=sample_message_data["message_ts"])
        .all()
    )
    assert len(messages) == 1


@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_store_slack_message_with_thread(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_credentials, qdrant
):
    """Test adding a Slack message that is part of a thread."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {}

    sample_message_data["thread_ts"] = "1704067100.000000"
    sample_message_data["reply_count"] = 5

    slack.store_slack_message(**sample_message_data)

    message = (
        db_session.query(SlackMessage)
        .filter_by(message_ts=sample_message_data["message_ts"])
        .first()
    )
    assert message.thread_ts == "1704067100.000000"
    assert message.reply_count == 5


@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_store_slack_message_with_reactions(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_credentials, qdrant
):
    """Test adding a Slack message with reactions."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {}

    sample_message_data["reactions"] = [
        {"name": "thumbsup", "count": 5, "users": ["U1", "U2"]},
        {"name": "heart", "count": 3, "users": ["U3"]},
    ]

    slack.store_slack_message(**sample_message_data)

    message = (
        db_session.query(SlackMessage)
        .filter_by(message_ts=sample_message_data["message_ts"])
        .first()
    )
    assert message.reactions == {"thumbsup": ["U1", "U2"], "heart": ["U3"]}


@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_store_slack_message_update_on_edit(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_credentials, qdrant
):
    """Test updating an existing message when edited."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {}

    # Add the message first
    slack.store_slack_message(**sample_message_data)

    # Update with edit
    sample_message_data["content"] = "Edited content with enough text to be meaningful."
    sample_message_data["edited_ts"] = "1704067300.000000"

    result = slack.store_slack_message(**sample_message_data)

    assert result["status"] == "updated"

    message = (
        db_session.query(SlackMessage)
        .filter_by(message_ts=sample_message_data["message_ts"])
        .first()
    )
    assert message.content == "Edited content with enough text to be meaningful."
    assert message.edited_ts == "1704067300.000000"


def ts_days_ago(days: float) -> str:
    return f"{(datetime.now(timezone.utc) - timedelta(days=days)).timestamp():.6f}"


def item_event_backfills(db_session) -> list[bool]:
    return [e.backfill for e in db_session.query(Event).filter(Event.kind == "item").order_by(Event.id)]


@pytest.mark.parametrize(
    "polled, days_ago, edited_days_ago, backfill",
    [
        (True, 0, None, False),
        (True, 1.9, None, False),
        (True, 2.1, None, True),
        (True, 400, None, True),
        (True, 400, 0, False),  # an old message edited lately is judged by its edit
        (True, 400, 5, True),
        (False, 400, None, False),  # a push is live however long it waited
    ],
)
@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_polled_messages_last_changed_long_before_they_are_stored_are_backfill(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_credentials, qdrant,
    polled, days_ago, edited_days_ago, backfill,
):
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {}
    edited_ts = None if edited_days_ago is None else ts_days_ago(edited_days_ago)

    slack.store_slack_message(
        **sample_message_data | {"message_ts": ts_days_ago(days_ago), "edited_ts": edited_ts, "polled": polled}
    )

    assert item_event_backfills(db_session) == [backfill]


@pytest.mark.parametrize(
    "polled, edited_days_ago, backfill",
    [(True, 0, False), (True, 5, True), (False, 5, False)],
)
@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_an_edit_is_backfill_only_when_a_poll_finds_it_old(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_credentials, qdrant,
    polled, edited_days_ago, backfill,
):
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {}
    old = sample_message_data | {"message_ts": ts_days_ago(10), "polled": True}
    slack.store_slack_message(**old)

    edit = {
        "content": "Edited content with enough text to be meaningful.",
        "edited_ts": ts_days_ago(edited_days_ago),
        "polled": polled,
    }
    slack.store_slack_message(**old | edit)

    assert item_event_backfills(db_session) == [True, backfill]


def test_store_slack_message_no_author_skipped(db_session, sample_message_data, qdrant):
    """Test that messages without an author are skipped."""
    sample_message_data["author_id"] = None

    result = slack.store_slack_message(**sample_message_data)

    assert result["status"] == "skipped"
    assert result["reason"] == "no_author"


@pytest.mark.transactional_db
@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_store_slack_message_unique_per_channel(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_workspace, slack_credentials, qdrant
):
    """Test that same message_ts in different channels creates separate messages."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {}

    # Add first message
    slack.store_slack_message(**sample_message_data)

    # Create another channel
    channel2 = SlackChannel(
        id="C87654321",
        workspace_id=slack_workspace.id,
        name="random",
        channel_type="channel",
        is_private=False,
        is_archived=False,
    )
    db_session.add(channel2)
    db_session.commit()

    # Add message with same ts but different channel
    sample_message_data["channel_id"] = channel2.id

    result = slack.store_slack_message(**sample_message_data)

    assert result["status"] == "processed"

    # Verify both messages exist
    messages = (
        db_session.query(SlackMessage)
        .filter_by(message_ts=sample_message_data["message_ts"])
        .all()
    )
    assert len(messages) == 2


@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_store_slack_message_with_subtype(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_credentials, qdrant
):
    """Test adding a Slack message with a subtype."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {}

    sample_message_data["subtype"] = "channel_join"

    slack.store_slack_message(**sample_message_data)

    message = (
        db_session.query(SlackMessage)
        .filter_by(message_ts=sample_message_data["message_ts"])
        .first()
    )
    assert message.message_type == "channel_join"


def test_resolve_mentions():
    """Test mention resolution in message content."""
    users_by_id = {"U12345678": "Test User"}

    content = "Hello <@U12345678>, how are you?"
    resolved = slack.resolve_mentions(content, users_by_id)

    assert "@Test User" in resolved
    assert "<@U12345678>" not in resolved


def test_resolve_mentions_unknown_user():
    """Test mention resolution with unknown user."""
    users_by_id = {}

    content = "Hello <@U_UNKNOWN>, how are you?"
    resolved = slack.resolve_mentions(content, users_by_id)

    # Unknown mentions should be preserved
    assert "<@U_UNKNOWN>" in resolved


def test_resolve_mentions_channel():
    """Test channel mention resolution."""
    users_by_id = {}

    content = "Check out <#C12345|general>"
    resolved = slack.resolve_mentions(content, users_by_id)

    assert "#general" in resolved
    assert "<#C12345|general>" not in resolved


def test_resolve_mentions_url():
    """Test URL resolution."""
    users_by_id = {}

    content = "Visit <https://example.com|Example Site>"
    resolved = slack.resolve_mentions(content, users_by_id)

    assert "Example Site" in resolved
    assert "<https://example.com|Example Site>" not in resolved


def test_sync_slack_workspace_no_credentials(db_session, slack_workspace, slack_app):
    """Test syncing workspace without credentials returns error."""
    result = slack.sync_slack_workspace(slack_workspace.id, slack_app.id)

    assert result["status"] == "error"
    assert "No valid credentials" in result["error"]


def test_sync_slack_workspace_not_found(db_session, slack_app):
    """Test syncing non-existent workspace returns error."""
    result = slack.sync_slack_workspace("T_NONEXISTENT", slack_app.id)

    assert result["status"] == "error"
    assert "Workspace not found" in result["error"]


@patch("memory.workers.tasks.slack.slack_client_for")
@patch("memory.workers.tasks.slack.sync_workspace_channels")
def test_sync_slack_workspace_success(
    mock_sync_channels,
    mock_client_class,
    db_session,
    slack_workspace,
    slack_credentials,
    slack_app,
):
    """Test successful workspace sync."""
    mock_client = MagicMock()
    mock_client.call.return_value = {"team": "Test Workspace"}
    mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_class.return_value.__exit__ = MagicMock(return_value=False)
    mock_sync_channels.return_value = 3

    result = slack.sync_slack_workspace(slack_workspace.id, slack_app.id)

    assert result["status"] == "completed"
    assert result["channels_synced"] == 3


@patch("memory.workers.tasks.slack.slack_client_for")
def test_sync_slack_workspace_token_expired(
    mock_client_class, db_session, slack_workspace, slack_credentials, slack_app
):
    """Test workspace sync with expired token."""
    mock_client = MagicMock()
    mock_client.call.side_effect = slack.SlackAPIError("token_expired")
    mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

    result = slack.sync_slack_workspace(slack_workspace.id, slack_app.id)

    assert result["status"] == "error"
    assert "token_expired" in result["error"]

    # Verify sync_error was set
    db_session.refresh(slack_workspace)
    assert "Token invalid" in slack_workspace.sync_error


@patch("memory.workers.tasks.slack.slack_client_for")
def test_sync_slack_workspace_unexpected_error(
    mock_client_class, db_session, slack_workspace, slack_credentials, slack_app
):
    """Test workspace sync with unexpected error doesn't re-raise."""
    mock_client = MagicMock()
    mock_client.call.side_effect = Exception("Unexpected error")
    mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

    result = slack.sync_slack_workspace(slack_workspace.id, slack_app.id)

    # Should return error status, not re-raise
    assert result["status"] == "error"
    assert "Unexpected error" in result["error"]

    # Verify sync_error was set
    db_session.refresh(slack_workspace)
    assert slack_workspace.sync_error is not None


@pytest.mark.parametrize(
    "channel_type,expected_type",
    [
        ("channel", "channel"),
        ("im", "dm"),
        ("mpim", "mpim"),
        ("group", "private_channel"),
    ],
)
@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_store_slack_message_creates_channel_if_missing(
    mock_build_cache, mock_get_creds,
    db_session, sample_message_data, slack_workspace, slack_credentials,
    channel_type, expected_type, qdrant
):
    """Test that store_slack_message creates channel if it doesn't exist."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {}

    # Use a channel ID that doesn't exist
    sample_message_data["channel_id"] = f"C_NEW_{channel_type}"

    result = slack.store_slack_message(**sample_message_data)

    assert result["status"] == "processed"

    # Verify channel was created
    channel = db_session.query(SlackChannel).filter_by(id=sample_message_data["channel_id"]).first()
    assert channel is not None
    assert channel.workspace_id == slack_workspace.id


@pytest.mark.transactional_db
@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_store_slack_message_race_merges_into_existing(
    mock_build_cache,
    mock_get_creds,
    db_session,
    sample_message_data,
    slack_credentials,
    slack_channel,
    qdrant,
):
    """B-pre-1 regression: an IntegrityError race must roll back only the
    message insert (not the surrounding work) and merge incoming fields
    (reactions, files) into the existing row instead of returning a bare
    `already_exists` that drops the loser's payload.
    """
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {"U12345678": "Test User"}

    # Insert the winner row normally.
    slack.store_slack_message(**sample_message_data)

    # Loser arrives with extra reactions/files and a different channel that
    # didn't exist beforehand — this exercises both the race path AND the
    # "channel auto-create must survive a rollback" guarantee.
    new_channel_id = "C_RACE_NEW"
    loser_data = dict(sample_message_data)
    loser_data["channel_id"] = new_channel_id
    loser_data["reactions"] = [{"name": "fire", "count": 1, "users": ["U_X"]}]
    loser_data["files"] = [{"id": "F_X", "mimetype": "text/plain"}]

    # Pre-insert the channel + a colliding message under the new channel id so
    # the unique (message_ts, workspace_id, channel_id) index fires.
    collision_channel = SlackChannel(
        id=new_channel_id,
        workspace_id=loser_data["workspace_id"],
        name=new_channel_id,
        channel_type="channel",
    )
    db_session.add(collision_channel)
    db_session.commit()

    winner = SlackMessage(
        slack_app_id=loser_data["slack_app_id"],
        modality="message",
        sha256=b"\x01" * 32,
        content="winner content with enough text to be embedded properly",
        message_ts=loser_data["message_ts"],
        channel_id=new_channel_id,
        workspace_id=loser_data["workspace_id"],
        author_id=loser_data["author_id"],
        message_type="message",
    )
    db_session.add(winner)
    db_session.commit()

    # Hide the winner from the first existence check to force the insert path,
    # then let the unique index produce an IntegrityError. The race-recovery
    # path should then re-fetch via a fresh `make_session` and merge.
    real_get = slack._get_existing_slack_message
    state = {"calls": 0}

    def fake_get(session, kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            return None
        return real_get(session, kwargs)

    with patch.object(slack, "_get_existing_slack_message", side_effect=fake_get):
        result = slack.store_slack_message(**loser_data)

    # Should not blow up the session — must report the merge cleanly.
    assert result["status"] in {"already_exists", "updated"}, result

    # Single row, with loser's reactions/files merged in.
    db_session.expire_all()
    rows = (
        db_session.query(SlackMessage)
        .filter_by(
            message_ts=loser_data["message_ts"],
            channel_id=new_channel_id,
            workspace_id=loser_data["workspace_id"],
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].reactions == {"fire": ["U_X"]}
    assert rows[0].files == loser_data["files"]


def test_ensure_slack_channel_commits_independently(
    db_session, slack_workspace, qdrant
):
    """B-pre-1: ensure_slack_channel commits in its own transaction so the
    channel row survives even if the caller's main transaction later rolls
    back. Previously the channel auto-create lived in the same session as the
    message insert and was lost on `session.rollback()`.
    """
    new_channel_id = "C_INDEPENDENT_COMMIT"
    assert (
        db_session.query(SlackChannel).filter_by(id=new_channel_id).first() is None
    )

    slack.ensure_slack_channel(slack_workspace.id, new_channel_id)

    # Channel is visible from a different session immediately — proving the
    # commit happened in ensure_slack_channel's own session.
    db_session.expire_all()
    channel = db_session.query(SlackChannel).filter_by(id=new_channel_id).first()
    assert channel is not None
    assert channel.workspace_id == slack_workspace.id


def test_ensure_slack_channel_idempotent(db_session, slack_workspace, slack_channel, qdrant):
    """ensure_slack_channel is safe to call when the channel already exists."""
    # Calling twice should not raise or duplicate.
    slack.ensure_slack_channel(slack_workspace.id, slack_channel.id)
    slack.ensure_slack_channel(slack_workspace.id, slack_channel.id)
    count = db_session.query(SlackChannel).filter_by(id=slack_channel.id).count()
    assert count == 1


def test_get_workspace_credentials_returns_valid(db_session, slack_workspace, slack_credentials, slack_app):
    """Test that get_workspace_credentials returns valid credentials."""
    result = slack.get_workspace_credentials(db_session, slack_workspace.id, slack_app.id)

    assert result is not None
    assert result.workspace_id == slack_workspace.id
    assert result.access_token == "xoxp-test-token"


def test_get_workspace_credentials_returns_none_when_no_creds(db_session, slack_workspace, slack_app):
    """Test that get_workspace_credentials returns None when no credentials."""
    result = slack.get_workspace_credentials(db_session, slack_workspace.id, slack_app.id)

    assert result is None


# =============================================================================
# merge_slack_message_state — pure logic; B-pre-2 ordering discriminator tests
# =============================================================================


def _stub_slack_message(content: str = "", edited_ts: str | None = None) -> SlackMessage:
    """Construct a SlackMessage in-memory only (no session attachment)."""
    msg = SlackMessage(
        modality="message",
        sha256=b"\x00" * 32,
        content=content,
        message_ts="1700000000.000000",
        channel_id="C_X",
        workspace_id="T_X",
        author_id="U_X",
        message_type="message",
        edited_ts=edited_ts,
    )
    return msg


@pytest.mark.parametrize(
    # name, existing_content, existing_edited_ts, in_content, in_edited_ts,
    # expect_changed, expect_final_content, expect_final_edited_ts
    "case",
    [
        # Case 1: incoming has no edited_ts and existing has none — idempotent
        ("idempotent_no_edits", "v1", None, "v1", None, False, "v1", None),
        # Case 1b: same identity but content differs (shouldn't happen for the
        # same message_ts; if it does, take incoming so polling-emitted later
        # corrects a half-applied earlier insert)
        ("differing_content_no_edits", "v1", None, "v2", None, True, "v2", None),
        # Case 2 (B-pre-2 fix): incoming = original, existing already has edit.
        # Overwrite content (canonical pre-edit) but PRESERVE existing.edited_ts
        # so we don't lose the marker that the message has been edited.
        ("original_arrives_after_edit", "edited_v2", "1700000010.0", "original_v1", None, True, "original_v1", "1700000010.0"),
        # Case 3: existing = original, incoming is a normal edit. Adopt new
        # edited_ts and content.
        ("edit_after_original", "v1", None, "v2", "1700000010.0", True, "v2", "1700000010.0"),
        # Case 4 (newer wins): both have edited_ts; incoming is newer.
        ("newer_edit_wins", "v1", "1700000010.0", "v2", "1700000020.0", True, "v2", "1700000020.0"),
        # Case 4b (older edit is stale): both have edited_ts; incoming older.
        ("stale_older_edit", "v2", "1700000020.0", "v_stale", "1700000010.0", False, "v2", "1700000020.0"),
        # Case 4c (equal edit): idempotent — no change
        ("duplicate_edit", "v2", "1700000020.0", "v2", "1700000020.0", False, "v2", "1700000020.0"),
    ],
    ids=lambda c: c[0],
)
def test_merge_slack_message_state_ordering(case):
    """B-pre-2 discriminator: edit-prefer-older-edited_ts semantics.

    Locks the four ordering cases enumerated in slack-changes.md §1.4 so
    common mutations (e.g., always overwriting edited_ts, dropping the
    pre-edit content branch, or flipping the older/newer comparator) are
    detected.
    """
    (
        _name,
        existing_content,
        existing_edited_ts,
        in_content,
        in_edited_ts,
        expect_changed,
        expect_final_content,
        expect_final_edited_ts,
    ) = case

    existing = _stub_slack_message(existing_content, existing_edited_ts)
    changed = slack.merge_slack_message_state(
        existing, in_content, in_edited_ts, reactions=None, files=None
    )

    assert changed is expect_changed, (
        f"changed={changed} expected {expect_changed} for case {_name}"
    )
    assert existing.content == expect_final_content
    assert existing.edited_ts == expect_final_edited_ts


def test_merge_slack_message_state_takes_incoming_reactions_when_provided():
    """Reactions overwrite when incoming is non-None (Slack sends full list)."""
    existing = _stub_slack_message("v1", None)
    existing.reactions = {"old": ["U1"]}
    new_reactions = {"fire": ["U1", "U2"]}
    slack.merge_slack_message_state(
        existing, "v1", None, reactions=new_reactions, files=None
    )
    assert existing.reactions == new_reactions


def test_merge_slack_message_state_preserves_reactions_when_incoming_none():
    """When incoming reactions is None (e.g., only edit event), keep existing."""
    existing = _stub_slack_message("v1", None)
    existing.reactions = {"fire": ["U1"]}
    slack.merge_slack_message_state(
        existing, "v2", "1700000020.0", reactions=None, files=None
    )
    assert existing.reactions == {"fire": ["U1"]}


def test_merge_slack_message_state_takes_incoming_files_when_provided():
    """Files overwrite when incoming is non-None."""
    existing = _stub_slack_message("v1", None)
    existing.files = [{"id": "F_OLD"}]
    new_files = [{"id": "F_NEW"}]
    slack.merge_slack_message_state(
        existing, "v1", None, reactions=None, files=new_files
    )
    assert existing.files == new_files


def test_merge_slack_message_state_preserves_files_when_incoming_none():
    """When incoming files is None, keep existing."""
    existing = _stub_slack_message("v1", None)
    existing.files = [{"id": "F_OLD"}]
    slack.merge_slack_message_state(
        existing, "v2", "1700000020.0", reactions=None, files=None
    )
    assert existing.files == [{"id": "F_OLD"}]


# =============================================================================
# Channel Sync Lock Tests
# =============================================================================


def test_channel_sync_lock_acquires_when_unheld():
    """Acquiring a lock when none exists yields a Lock instance."""
    channel_id = "C_TEST_LOCK_1"

    with slack.channel_sync_lock(channel_id) as lock:
        assert lock is not None
        assert slack.is_channel_sync_locked(channel_id) is True


def test_channel_sync_lock_returns_none_when_already_held():
    """Acquiring a lock when one already exists yields None."""
    channel_id = "C_TEST_LOCK_2"

    with slack.channel_sync_lock(channel_id) as outer:
        assert outer is not None
        with slack.channel_sync_lock(channel_id) as inner:
            assert inner is None


def test_channel_sync_lock_releases_on_exit():
    """Lock is released on context-manager exit."""
    channel_id = "C_TEST_LOCK_3"

    with slack.channel_sync_lock(channel_id) as lock:
        assert lock is not None
        assert slack.is_channel_sync_locked(channel_id) is True

    assert slack.is_channel_sync_locked(channel_id) is False


def test_channel_sync_lock_allows_reacquire_after_release():
    """Reacquisition works after a clean release."""
    channel_id = "C_TEST_LOCK_4"

    with slack.channel_sync_lock(channel_id) as lock:
        assert lock is not None
    with slack.channel_sync_lock(channel_id) as lock:
        assert lock is not None


def test_channel_sync_lock_release_is_ownership_checked():
    """Regression: the previous slack lock release used plain
    ``client.delete()`` with no token check. A slow worker whose lock
    had already expired and been reacquired by another worker would
    clobber the new owner. The shared distributed_lock helper uses an
    atomic Lua script to delete only when the value still equals our
    token. We simulate the reacquisition by stuffing a foreign token
    into the lock key while the outer context is still 'held' from
    Python's perspective."""
    import redis as redis_module
    from memory.common import settings

    channel_id = "C_TEST_LOCK_OWNERSHIP"
    client = redis_module.from_url(settings.REDIS_URL)
    key = slack.channel_sync_lock_key(channel_id)

    with slack.channel_sync_lock(channel_id) as lock:
        assert lock is not None
        # Simulate a different worker reacquiring after our TTL expired.
        client.set(key, "different-worker-token")

    # Foreign token must still be in place — our context-exit must not
    # have deleted it. (Real Redis returns bytes; the test fixture's
    # MockRedis returns str.)
    actual = client.get(key)
    if isinstance(actual, bytes):
        actual = actual.decode()
    assert actual == "different-worker-token"
    client.delete(key)  # cleanup


def test_is_channel_sync_locked_returns_false_when_not_locked():
    """Test is_channel_sync_locked returns False for unlocked channel."""
    channel_id = "C_NEVER_LOCKED"

    assert slack.is_channel_sync_locked(channel_id) is False


def test_sync_slack_channel_skips_when_locked(db_session, slack_channel, slack_app):
    """Test that sync_slack_channel skips if channel is already locked."""
    # Pre-acquire the lock via the new context manager. Holding it for
    # the duration of the sync_slack_channel call simulates another
    # worker being mid-sync.
    with slack.channel_sync_lock(slack_channel.id):
        result = slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert result["status"] == "skipped"
    assert result["reason"] == "sync_in_progress"


@patch("memory.workers.tasks.slack.slack_client_for")
@patch("memory.workers.tasks.slack.iter_messages")
def test_sync_slack_channel_releases_lock_on_success(
    mock_iter_messages, mock_client_class,
    db_session, slack_channel, slack_credentials, slack_app
):
    """Test that sync_slack_channel releases lock after successful sync."""
    mock_client = MagicMock()
    mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_class.return_value.__exit__ = MagicMock(return_value=False)
    mock_iter_messages.return_value = iter([])

    # Verify lock is not held before
    assert slack.is_channel_sync_locked(slack_channel.id) is False

    result = slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert result["status"] == "completed"
    # Lock should be released after
    assert slack.is_channel_sync_locked(slack_channel.id) is False


@patch("memory.workers.tasks.slack.slack_client_for")
@patch("memory.workers.tasks.slack.iter_messages")
def test_sync_slack_channel_releases_lock_on_error(
    mock_iter_messages, mock_client_class,
    db_session, slack_channel, slack_credentials, slack_app
):
    """Test that sync_slack_channel releases lock even on error."""
    mock_client = MagicMock()
    mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_class.return_value.__exit__ = MagicMock(return_value=False)
    mock_iter_messages.side_effect = slack.SlackAPIError("rate_limited")

    result = slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert result["status"] == "error"
    # Lock should still be released
    assert slack.is_channel_sync_locked(slack_channel.id) is False


@patch("memory.workers.tasks.slack.slack_client_for")
@patch("memory.workers.tasks.slack.sync_workspace_channels")
@patch("memory.workers.tasks.slack.app")
def test_sync_slack_workspace_skips_locked_channels(
    mock_app, mock_sync_channels, mock_client_class,
    db_session, slack_workspace, slack_channel, slack_credentials, slack_app
):
    """Test that sync_slack_workspace skips channels that are already locked."""
    mock_client = MagicMock()
    mock_client.call.return_value = {"team": "Test Workspace"}
    mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_class.return_value.__exit__ = MagicMock(return_value=False)
    mock_sync_channels.return_value = 1

    # Pre-lock the channel by entering the context manager and holding
    # it for the duration of sync_slack_workspace's iteration.
    with slack.channel_sync_lock(slack_channel.id):
        result = slack.sync_slack_workspace(slack_workspace.id, slack_app.id)

    assert result["status"] == "completed"
    # Channel sync task should NOT have been sent (channel was locked)
    mock_app.send_task.assert_not_called()


@patch("memory.workers.tasks.slack.slack_client_for")
@patch("memory.workers.tasks.slack.sync_workspace_channels")
@patch("memory.workers.tasks.slack.app")
def test_sync_slack_workspace_triggers_unlocked_channels(
    mock_app, mock_sync_channels, mock_client_class,
    db_session, slack_workspace, slack_channel, slack_credentials, slack_app
):
    """Test that sync_slack_workspace triggers sync for unlocked channels."""
    mock_client = MagicMock()
    mock_client.call.return_value = {"team": "Test Workspace"}
    mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_class.return_value.__exit__ = MagicMock(return_value=False)
    mock_sync_channels.return_value = 1

    # Channel is NOT locked

    result = slack.sync_slack_workspace(slack_workspace.id, slack_app.id)

    assert result["status"] == "completed"
    assert result["channels_triggered"] == 1
    # Channel sync task should have been sent
    mock_app.send_task.assert_called_once()


@pytest.mark.parametrize("setup_state", ["draft", "signing_verified", "live", "degraded"])
@patch("memory.workers.tasks.slack.app")
def test_sync_all_polls_whatever_the_event_setup_state(
    mock_app, db_session, slack_app, slack_workspace, slack_credentials, setup_state
):
    slack_app.setup_state = setup_state
    db_session.commit()

    result = slack.sync_all_slack_workspaces()

    assert result["workspaces_triggered"] == 1
    mock_app.send_task.assert_called_once_with(
        SYNC_SLACK_WORKSPACE,
        kwargs={"workspace_id": slack_workspace.id, "slack_app_id": slack_app.id},
    )


def deactivate_app(session, credentials):
    credentials.slack_app.is_active = False


def expire_token(session, credentials):
    credentials.token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)


def remove_credentials(session, credentials):
    session.delete(credentials)


@pytest.mark.parametrize(
    "break_polling, reason",
    [
        (deactivate_app, "is inactive"),
        (expire_token, "has no unexpired token"),
        (remove_credentials, "no Slack credentials"),
    ],
)
@patch("memory.workers.tasks.slack.app")
def test_sync_all_warns_when_a_collecting_workspace_cannot_be_polled(
    mock_app, db_session, slack_workspace, slack_credentials, caplog, break_polling, reason
):
    break_polling(db_session, slack_credentials)
    db_session.commit()

    with caplog.at_level(logging.WARNING, logger=slack.logger.name):
        result = slack.sync_all_slack_workspaces()

    assert result["workspaces_triggered"] == 0
    mock_app.send_task.assert_not_called()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert slack_workspace.id in warnings[0]
    assert reason in warnings[0]


def collection_off(workspace):
    workspace.collect_messages = False


def synced_just_now(workspace):
    workspace.last_sync_at = datetime.now(timezone.utc)


@pytest.mark.parametrize("quiet_skip", [collection_off, synced_just_now])
@patch("memory.workers.tasks.slack.app")
def test_sync_all_skips_without_warning_when_nothing_is_wrong(
    mock_app, db_session, slack_workspace, slack_credentials, caplog, quiet_skip
):
    quiet_skip(slack_workspace)
    db_session.commit()

    with caplog.at_level(logging.WARNING, logger=slack.logger.name):
        slack.sync_all_slack_workspaces()

    mock_app.send_task.assert_not_called()
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


@pytest.mark.parametrize("event_hours_ago,age_hours,interval,expected", [
    (1, 2, 86400, 0),
    (1, 25, 86400, 1),
    (1, 13, 43200, 1),
    (1, 2, 43200, 0),
    (30, 1, 86400, 1),
    (None, 1, 86400, 1),
])
@patch("memory.workers.tasks.slack.app")
def test_event_reconciliation_cadence(
    mock_app, db_session, slack_app, slack_workspace, slack_credentials,
    event_hours_ago, age_hours, interval, expected,
):
    now = datetime.now(timezone.utc)
    slack_app.last_event_at = None if event_hours_ago is None else now - timedelta(hours=event_hours_ago)
    slack_workspace.last_sync_at = now - timedelta(hours=age_hours)
    slack_workspace.event_reconcile_interval_seconds = interval
    db_session.commit()

    result = slack.sync_all_slack_workspaces()

    assert result["workspaces_triggered"] == expected
    assert mock_app.send_task.call_count == expected


def test_mixed_apps_keep_polling(db_session, slack_app, slack_workspace):
    from memory.common.slack import workspace_sync_interval

    slack_app.last_event_at = datetime.now(timezone.utc)
    silent = SlackApp(client_id="silent.app", name="Silent", setup_state="live")
    db_session.add(silent)
    db_session.commit()
    assert workspace_sync_interval(db_session, slack_workspace, [slack_app.id, silent.id]) == slack_workspace.sync_interval_seconds


def test_history_routing_is_isolated_from_events():
    from memory.common import celery_app, settings

    router = celery_app.app.amqp.router
    for task in (celery_app.SYNC_ALL_SLACK_WORKSPACES, celery_app.SYNC_SLACK_WORKSPACE, celery_app.SYNC_SLACK_CHANNEL):
        assert router.route({}, task)["queue"].name == f"{settings.CELERY_QUEUE_PREFIX}-slack-sync"
    assert router.route({}, celery_app.ADD_SLACK_MESSAGE)["queue"].name == f"{settings.CELERY_QUEUE_PREFIX}-slack"


# =============================================================================
# Tokens only go to the app that issued them
# =============================================================================


@pytest.mark.parametrize("pushing_app_has_credentials, author_name", [(True, "xoxp-test-token"), (False, None)])
def test_stored_messages_use_only_the_ingesting_apps_credentials(
    db_session, sample_message_data, slack_credentials, pushing_app_has_credentials, author_name, qdrant
):
    pusher = SlackApp(client_id="pusher.app", name="Pusher", setup_state="live")
    db_session.add(pusher)
    db_session.commit()
    app_id = slack_credentials.slack_app_id if pushing_app_has_credentials else pusher.id

    with patch(
        "memory.workers.tasks.slack.get_cached_user_mapping",
        side_effect=lambda workspace_id, token, base_url: {"U12345678": token},
    ):
        slack.store_slack_message(**sample_message_data | {"slack_app_id": app_id})

    message = db_session.query(SlackMessage).filter_by(
        message_ts=sample_message_data["message_ts"]
    ).one()
    assert message.author_name == author_name


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("down"), httpx.ReadTimeout("slow"), BlockedDestination(), SlackAPIError("ratelimited")],
)
def test_a_message_is_stored_when_its_mentions_cannot_be_resolved(
    db_session, sample_message_data, slack_credentials, error, qdrant
):
    with patch("memory.workers.tasks.slack.build_user_cache", side_effect=error):
        result = slack.store_slack_message(**sample_message_data | {"content": "hi <@U1> " * 10})

    message = db_session.query(SlackMessage).filter_by(
        message_ts=sample_message_data["message_ts"]
    ).one()
    assert (result["status"], message.author_name) == ("processed", None)


@pytest.mark.parametrize(
    "url, sent",
    [
        ("https://files.slack.com/files-pri/T1-F1/a.png", True),
        ("https://chat.example.com/files/a.png", True),
        ("https://evil.example.com/a.png", False),
        ("https://files.slack.com.evil.example.com/a.png", False),
    ],
)
def test_images_are_fetched_with_the_token_only_from_slack_or_the_apps_api_host(url, sent):
    with patch.object(slack, "download_slack_file", return_value="x.png") as download:
        slack.download_message_images(
            [{"mimetype": "image/png", "url_private": url}],
            "xoxp-1",
            "1.2",
            "T1",
            api_base_url="https://chat.example.com/api",
        )

    assert download.called is sent


# =============================================================================
# Reaction events
# =============================================================================


@pytest.fixture
def stored_message(db_session, slack_workspace, slack_channel, slack_app):
    message = SlackMessage(
        slack_app_id=slack_app.id,
        modality="message",
        sha256=b"\x02" * 32,
        content="a message people react to",
        message_ts="1704067300.000100",
        channel_id=slack_channel.id,
        workspace_id=slack_workspace.id,
        author_id="U1",
        message_type="message",
        reactions={"fire": ["U1"]},
    )
    db_session.add(message)
    db_session.commit()
    return message


@pytest.mark.parametrize(
    "events, expected",
    [
        ([("add", "fire", "U2")], {"fire": ["U1", "U2"]}),
        ([("add", "fire", "U2"), ("add", "fire", "U2")], {"fire": ["U1", "U2"]}),
        ([("remove", "fire", "U1")], {}),
        ([("remove", "fire", "U1"), ("remove", "fire", "U1")], {}),
        ([("add", "tada", "U3"), ("remove", "fire", "U1")], {"tada": ["U3"]}),
    ],
)
def test_update_slack_reactions_applies_events_per_user(
    db_session, stored_message, slack_app, events, expected
):
    for action, emoji, user_id in events:
        result = slack.update_slack_reactions(
            workspace_id=stored_message.workspace_id,
            channel_id=stored_message.channel_id,
            message_ts=stored_message.message_ts,
            action=action,
            emoji=emoji,
            user_id=user_id,
            slack_app_id=slack_app.id,
        )
        assert result["status"] == "updated"

    db_session.refresh(stored_message)
    assert stored_message.reactions == expected


@pytest.mark.parametrize(
    "attempt, status, retried",
    [
        (0, "retrying", True),
        (slack.REACTION_RETRIES - 1, "retrying", True),
        (slack.REACTION_RETRIES, "not_found", False),
    ],
)
def test_update_slack_reactions_waits_for_a_message_not_stored_yet(
    db_session, slack_workspace, slack_channel, slack_app, attempt, status, retried
):
    with patch.object(slack.update_slack_reactions, "apply_async") as retry:
        result = slack.update_slack_reactions(
            workspace_id=slack_workspace.id,
            channel_id=slack_channel.id,
            message_ts="1704067999.000100",
            action="add",
            emoji="fire",
            user_id="U1",
            slack_app_id=slack_app.id,
            attempt=attempt,
        )

    assert (result["status"], retry.called) == (status, retried)


@pytest.mark.parametrize("base_url", ["https://slack.com/api", "https://chat.example.com/api"])
def test_credentials_call_their_apps_api(db_session, slack_app, slack_credentials, base_url):
    from memory.common.slack import slack_client_for

    slack_app.api_base_url = base_url
    db_session.commit()

    with patch("memory.common.chat_urls.settings.CHAT_API_ALLOWED_ORIGINS", ["https://chat.example.com"]):
        client = slack_client_for(slack_credentials)

    assert (client.base_url, client.access_token) == (base_url, "xoxp-test-token")


def custom_api_app(db_session, slack_app, owner, scopes):
    owner.scopes = scopes
    slack_app.created_by_user_id = owner.id
    slack_app.api_base_url = "https://chat.example.com/api"
    db_session.commit()


def test_credentials_refuse_an_api_url_outside_the_allowlist(db_session, slack_app, slack_credentials, slack_user):
    from memory.common.slack import SlackAPIError, slack_client_for

    custom_api_app(db_session, slack_app, slack_user, ["read"])

    with patch("memory.common.chat_urls.settings.CHAT_API_ALLOWED_ORIGINS", []), pytest.raises(SlackAPIError):
        slack_client_for(slack_credentials)


def test_credentials_use_an_api_url_an_admin_chose(db_session, slack_app, slack_credentials, slack_user):
    from memory.common.slack import slack_client_for

    custom_api_app(db_session, slack_app, slack_user, ["*"])

    with patch("memory.common.chat_urls.settings.CHAT_API_ALLOWED_ORIGINS", []):
        assert slack_client_for(slack_credentials).base_url == "https://chat.example.com/api"


def test_update_slack_reactions_retries_a_lock_conflict(db_session, stored_message, slack_app):
    from sqlalchemy.exc import OperationalError

    with (
        patch.object(slack, "make_session", side_effect=OperationalError("SELECT", {}, Exception("deadlock detected"))),
        patch.object(slack.update_slack_reactions, "apply_async") as retry,
    ):
        result = slack.update_slack_reactions(
            workspace_id=stored_message.workspace_id,
            channel_id=stored_message.channel_id,
            message_ts=stored_message.message_ts,
            action="add",
            emoji="fire",
            user_id="U2",
            slack_app_id=slack_app.id,
        )

    assert (result["status"], retry.call_args.kwargs["kwargs"]["attempt"]) == ("retrying", 1)


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"is_archived": True}, ("general", True, "private_channel")),
        ({"id": "C12345678", "name": "renamed", "created": 1}, ("renamed", False, "private_channel")),
    ],
)
def test_a_channel_event_changes_only_what_it_carries(db_session, slack_channel, payload, expected):
    slack_channel.channel_type = "private_channel"
    db_session.commit()

    slack.update_slack_channel(slack_channel.workspace_id, slack_channel.id, payload)

    db_session.refresh(slack_channel)
    assert (slack_channel.name, slack_channel.is_archived, slack_channel.channel_type) == expected


# =============================================================================
# Cross-tenant isolation
# =============================================================================


@pytest.fixture
def other_workspace_channel(db_session):
    db_session.add(SlackWorkspace(id="T99999999", name="Other"))
    db_session.flush()
    channel = SlackChannel(id="C99999999", workspace_id="T99999999", name="theirs", channel_type="channel")
    db_session.add(channel)
    db_session.commit()
    return channel


def test_a_channel_event_from_another_workspace_is_ignored(db_session, slack_workspace, other_workspace_channel):
    result = slack.update_slack_channel(slack_workspace.id, other_workspace_channel.id, {"name": "hijacked"})

    db_session.refresh(other_workspace_channel)
    assert (result["status"], other_workspace_channel.name) == ("ignored", "theirs")


@patch("memory.workers.tasks.slack.get_cached_user_mapping", return_value={})
def test_a_message_for_another_workspaces_channel_is_ignored(
    _mapping, db_session, sample_message_data, other_workspace_channel, qdrant
):
    result = slack.store_slack_message(**sample_message_data | {"channel_id": other_workspace_channel.id})

    assert result["status"] == "skipped"
    assert db_session.query(SlackMessage).count() == 0


@pytest.fixture
def owners(db_session, slack_app, slack_user):
    """slack_app belongs to slack_user; a second app of theirs, and another person's app."""
    stranger = User(name="Stranger", email="stranger@example.com", password_hash="x")
    db_session.add(stranger)
    db_session.flush()
    slack_app.created_by_user_id = slack_user.id
    sibling = SlackApp(client_id="sibling", name="Sibling", created_by_user_id=slack_user.id)
    foreign = SlackApp(client_id="foreign", name="Foreign", created_by_user_id=stranger.id)
    db_session.add_all([sibling, foreign])
    db_session.commit()
    return {"own": slack_app.id, "sibling": sibling.id, "foreign": foreign.id}


@pytest.mark.parametrize("pusher, changed", [("own", True), ("sibling", True), ("foreign", False)])
def test_pushed_deletes_only_touch_the_owners_messages(db_session, stored_message, owners, pusher, changed):
    slack.mark_slack_message_deleted(
        stored_message.workspace_id, stored_message.channel_id, stored_message.message_ts,
        slack_app_id=owners[pusher],
    )

    db_session.expire_all()
    assert (db_session.query(SlackMessage).count() == 0) is changed


@pytest.mark.parametrize("pusher, expected", [("own", {"fire": ["U1", "U9"]}), ("foreign", {"fire": ["U1"]})])
def test_pushed_reactions_only_touch_the_owners_messages(db_session, stored_message, owners, pusher, expected):
    slack.update_slack_reactions(
        workspace_id=stored_message.workspace_id,
        channel_id=stored_message.channel_id,
        message_ts=stored_message.message_ts,
        action="add",
        emoji="fire",
        user_id="U9",
        slack_app_id=owners[pusher],
    )

    db_session.refresh(stored_message)
    assert stored_message.reactions == expected


@pytest.mark.parametrize("pusher, content", [("own", "edited text of the stored message"), ("foreign", "a message people react to")])
@patch("memory.workers.tasks.slack.get_cached_user_mapping", return_value={})
def test_pushed_edits_only_touch_the_owners_messages(_mapping, db_session, stored_message, owners, pusher, content, qdrant):
    slack.add_slack_message(
        workspace_id=stored_message.workspace_id,
        channel_id=stored_message.channel_id,
        message_ts=stored_message.message_ts,
        author_id="U1",
        content="edited text of the stored message",
        slack_app_id=owners[pusher],
        edited_ts="1704067400.000100",
    )

    db_session.refresh(stored_message)
    assert stored_message.content == content


def test_new_messages_record_the_app_that_ingested_them(db_session, sample_message_data, slack_credentials, qdrant):
    with patch("memory.workers.tasks.slack.get_cached_user_mapping", return_value={}):
        slack.store_slack_message(**sample_message_data)

    message = db_session.query(SlackMessage).one()
    assert message.slack_app_id == sample_message_data["slack_app_id"]


def test_images_are_not_fetched_from_an_api_url_no_longer_allowed(
    db_session, sample_message_data, slack_app, slack_credentials, slack_user, qdrant
):
    custom_api_app(db_session, slack_app, slack_user, ["read"])
    files = [{"mimetype": "image/png", "url_private": "https://chat.example.com/files/a.png"}]

    with (
        patch("memory.common.chat_urls.settings.CHAT_API_ALLOWED_ORIGINS", []),
        patch.object(slack, "download_slack_file") as download,
        patch("memory.workers.tasks.slack.get_cached_user_mapping", return_value={}),
    ):
        result = slack.store_slack_message(**sample_message_data | {"files": files})

    assert result["status"] == "processed"
    download.assert_not_called()


def test_a_channel_listing_never_touches_another_workspaces_channel(db_session, slack_workspace, other_workspace_channel):
    with patch.object(slack, "iter_channels", return_value=[{"id": other_workspace_channel.id, "name": "hijacked"}]):
        slack.sync_workspace_channels(MagicMock(), slack_workspace, db_session, {})

    db_session.refresh(other_workspace_channel)
    assert (other_workspace_channel.name, other_workspace_channel.workspace_id) == ("theirs", "T99999999")



@pytest.fixture
def unclaimed_message(db_session, stored_message):
    stored_message.slack_app_id = None
    db_session.commit()
    return stored_message


@patch("memory.workers.tasks.slack.get_cached_user_mapping", return_value={})
def test_a_poll_claims_an_unclaimed_message(_mapping, db_session, unclaimed_message, slack_app, qdrant):
    slack.store_slack_message(
        workspace_id=unclaimed_message.workspace_id,
        channel_id=unclaimed_message.channel_id,
        message_ts=unclaimed_message.message_ts,
        author_id="U1",
        content="the polled text of this stored message",
        slack_app_id=slack_app.id,
        edited_ts="1704067400.000100",
        polled=True,
    )

    db_session.refresh(unclaimed_message)
    assert (unclaimed_message.slack_app_id, unclaimed_message.content) == (
        slack_app.id, "the polled text of this stored message",
    )


def test_a_push_cannot_change_an_unclaimed_message(db_session, unclaimed_message, slack_app):
    slack.update_slack_reactions(
        workspace_id=unclaimed_message.workspace_id,
        channel_id=unclaimed_message.channel_id,
        message_ts=unclaimed_message.message_ts,
        action="add",
        emoji="fire",
        user_id="U9",
        slack_app_id=slack_app.id,
    )

    db_session.refresh(unclaimed_message)
    assert (unclaimed_message.reactions, unclaimed_message.slack_app_id) == ({"fire": ["U1"]}, None)




# =============================================================================
# Pushed new messages are read back with the pushing app's token
# =============================================================================


SHARED_CHANNEL = "C0SHARED01"
REAL_TS = "1704067500.000100"
REPLY_TS = "1704067600.000100"


class FakeSlack:
    """Slack's conversations.history/replies, answering only for channels each token can read."""

    def __init__(self, messages: dict[str, list[dict]], readable: dict[str, set[str]]):
        self.messages = messages
        self.readable = readable
        self.outage: str | Exception | httpx.Response | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        if isinstance(self.outage, Exception):
            raise self.outage
        if isinstance(self.outage, httpx.Response):
            return self.outage
        if self.outage:
            return httpx.Response(200, json={"ok": False, "error": self.outage}, headers={"Retry-After": "120"})
        token = request.headers["Authorization"].removeprefix("Bearer ")
        form = dict(httpx.QueryParams(request.content.decode()))
        channel = form["channel"]
        if channel not in self.readable.get(token, set()):
            return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
        window = [
            m for m in self.messages.get(channel, [])
            if float(form["oldest"]) <= float(m["ts"]) <= float(form["latest"])
        ]
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "conversations.history":
            found = [m for m in window if m.get("thread_ts", m["ts"]) == m["ts"]]
        else:
            thread = [m for m in self.messages.get(channel, []) if m.get("thread_ts") == form["ts"]]
            if not thread:
                return httpx.Response(200, json={"ok": False, "error": "thread_not_found"})
            found = thread[:1] + [m for m in window if m.get("thread_ts") == form["ts"] and m["ts"] != form["ts"]]
        return httpx.Response(200, json={"ok": True, "messages": found[: int(form["limit"])], "has_more": False})


@pytest.fixture
def two_tenants(db_session, slack_app, slack_credentials, slack_channel, slack_user):
    """Users B (slack_app, token xoxp-test-token) and A (their own app, token xoxp-a) in one workspace.

    B's token reads the private channel C12345678; both read SHARED_CHANNEL.
    """
    slack_app.created_by_user_id = slack_user.id
    user_a = User(name="A", email="a@example.com", password_hash="x")
    db_session.add(user_a)
    db_session.flush()
    app_a = SlackApp(client_id="a.app", name="A's app", setup_state="live", created_by_user_id=user_a.id)
    db_session.add(app_a)
    db_session.flush()
    creds_a = SlackUserCredentials(
        slack_app_id=app_a.id, workspace_id=slack_credentials.workspace_id, user_id=user_a.id,
        scopes=["channels:history"], slack_user_id="U_A",
    )
    creds_a.access_token = "xoxp-a"
    db_session.add(creds_a)
    db_session.commit()
    return {"B": slack_app.id, "A": app_a.id}


@pytest.fixture
def fake_slack(slack_channel):
    real = {"ts": REAL_TS, "user": "U_A", "text": "what U_A actually wrote"}
    reply = {"ts": REPLY_TS, "thread_ts": REAL_TS, "user": "U_A", "text": "U_A's actual reply"}
    slack_side = FakeSlack(
        messages={SHARED_CHANNEL: [real | {"thread_ts": REAL_TS}, reply], slack_channel.id: [real]},
        readable={"xoxp-test-token": {slack_channel.id, SHARED_CHANNEL}, "xoxp-a": {SHARED_CHANNEL}},
    )
    transport = httpx.MockTransport(slack_side.handle)
    with patch("memory.common.slack.public_sync_http_client", lambda **kw: httpx.Client(transport=transport, **kw)):
        yield slack_side


def store_without_embedding(item, session, **_kwargs) -> dict[str, Any]:
    """process_content_item minus embedding: the sandbox has no Qdrant."""
    session.add(item)
    session.commit()
    return {"status": "processed", "slackmessage_id": item.id}


def push_message(app_id: int, channel_id: str, message_ts: str, **fields) -> dict:
    forged = {"author_id": "U_CEO", "content": "wire the money today", "thread_ts": None} | fields
    with (
        patch.object(slack, "process_content_item", side_effect=store_without_embedding),
        patch.object(slack, "get_cached_user_mapping", return_value={}),
    ):
        return slack.add_slack_message(
            workspace_id="T12345678", channel_id=channel_id, message_ts=message_ts, slack_app_id=app_id, **forged
        )


def stored_rows(db_session) -> list[tuple]:
    db_session.expire_all()
    rows = db_session.query(SlackMessage).order_by(SlackMessage.message_ts)
    return [(m.channel_id, m.message_ts, m.author_id, m.content, m.slack_app_id) for m in rows]


@pytest.mark.parametrize(
    "pusher, message_ts, expected",
    [
        ("A", REAL_TS, []),
        ("A", "1704067999.000100", []),
        ("B", "1704067999.000100", []),
        ("B", REAL_TS, [("C12345678", REAL_TS, "U_A", "what U_A actually wrote", "B")]),
    ],
)
def test_a_pushed_new_message_lands_only_where_the_pushing_app_reads_it(
    db_session, two_tenants, fake_slack, slack_channel, pusher, message_ts, expected
):
    push_message(two_tenants[pusher], slack_channel.id, message_ts)

    assert stored_rows(db_session) == [row[:4] + (two_tenants[row[4]],) for row in expected]


@pytest.mark.parametrize(
    "message_ts, thread_ts, expected",
    [
        (REAL_TS, None, (SHARED_CHANNEL, REAL_TS, "U_A", "what U_A actually wrote")),
        (REPLY_TS, REAL_TS, (SHARED_CHANNEL, REPLY_TS, "U_A", "U_A's actual reply")),
    ],
)
def test_a_pushed_new_message_is_stored_as_slack_has_it(
    db_session, two_tenants, fake_slack, message_ts, thread_ts, expected
):
    push_message(two_tenants["A"], SHARED_CHANNEL, message_ts, thread_ts=thread_ts)

    assert stored_rows(db_session) == [expected + (two_tenants["A"],)]


@pytest.mark.parametrize(
    "outage",
    [
        "ratelimited",
        "internal_error",
        httpx.ConnectError("connection refused"),
        httpx.Response(503, text="<html>Service Unavailable</html>"),
    ],
)
def test_a_pushed_message_slack_cannot_serve_yet_is_retried_not_dropped(db_session, two_tenants, fake_slack, outage):
    fake_slack.outage = outage

    with pytest.raises(slack.PushedMessageUnread):
        push_message(two_tenants["A"], SHARED_CHANNEL, REAL_TS)

    assert stored_rows(db_session) == []


@pytest.mark.parametrize("thread_ts", ["1704067000.000100", REPLY_TS])
def test_a_pushed_reply_under_a_thread_slack_does_not_have_is_dropped(db_session, two_tenants, fake_slack, thread_ts):
    push_message(two_tenants["A"], SHARED_CHANNEL, "1704067650.000100", thread_ts=thread_ts)

    assert stored_rows(db_session) == []


@pytest.mark.parametrize(
    "message_ts, thread_ts",
    [
        ("/var/tmp/x", None),
        ("1704067200_000100", None),
        ("1704067200.000100", "/srv/x"),
        ("", None),
        ("\u0661\u0667\u0660\u0664.\u0661", None),
    ],
)
def test_a_message_with_a_malformed_timestamp_is_not_stored(
    db_session, sample_message_data, slack_credentials, message_ts, thread_ts
):
    with patch.object(slack, "stream_download_to_path", return_value=True) as download:
        result = slack.store_slack_message(
            **sample_message_data | {"message_ts": message_ts, "thread_ts": thread_ts}
        )

    assert (result, download.call_count, db_session.query(SlackMessage).count()) == (
        {"status": "skipped", "reason": "malformed_ts"}, 0, 0,
    )


@pytest.mark.parametrize("message_ts", ["/var/tmp/x", "/1704067200.000100", "1704067200.000100/x"])
def test_slack_files_are_never_written_outside_the_storage_dir(message_ts):
    with patch.object(slack, "stream_download_to_path", return_value=True) as download:
        path = slack.download_slack_file("https://files.slack.com/a.png", {}, message_ts, "T12345678")

    assert (path, download.call_count) == (None, 0)


def history_message(ts: str, **fields) -> dict:
    return {"ts": ts, "user": "U1", "text": f"message {ts}", **fields}


def lost_connection() -> OperationalError:
    return OperationalError("COMMIT", {}, Exception("server closed the connection unexpectedly"))


def failing_store_of(ts: str, error=lost_connection):
    """process_content_item that raises ``error()`` while storing message ``ts``."""
    store = slack.process_content_item

    def process(item, session, **kwargs):
        if item.message_ts == ts:
            raise error()
        return store(item, session, **kwargs)

    return process


def stored_timestamps(db_session, channel_id: str) -> list[str]:
    rows = db_session.query(SlackMessage.message_ts).filter(SlackMessage.channel_id == channel_id)
    return sorted((ts for (ts,) in rows), key=float)


def channel_cursor(db_session, channel: SlackChannel) -> str | None:
    db_session.refresh(channel)
    return channel.last_message_ts


@pytest.fixture
def slack_history(slack_credentials):
    """A channel's history and thread replies, served from lists instead of Slack.

    ``messages`` is in Slack's order, newest first; ``replies`` maps a thread
    to its replies or to the error reading them raises. ``listed`` counts the
    messages each history listing yielded and ``pages`` the pages of ``limit``
    messages it fetched; ``threads_read`` records each thread read and the
    ``oldest`` it was read after; ``sent`` records queued tasks.
    """
    history: dict[str, Any] = {"messages": [], "replies": {}, "listed": [], "pages": [], "threads_read": [], "sent": []}

    def messages(client, channel_id, oldest=None, latest=None, inclusive=False, limit=100):
        low, high = float(oldest or 0), float(latest or "inf")
        history["listed"].append(0)
        history["pages"].append(1)
        for message in history["messages"]:
            ts = float(message["ts"])
            if (low <= ts <= high) if inclusive else (low < ts < high):
                history["pages"][-1] += history["listed"][-1] and history["listed"][-1] % limit == 0
                history["listed"][-1] += 1
                yield message

    def replies(client, channel_id, thread_ts, oldest=None):
        history["threads_read"].append((thread_ts, oldest))
        found = history["replies"].get(thread_ts, [])
        if isinstance(found, Exception):
            raise found
        return (r for r in found if float(r["ts"]) > float(oldest or 0))

    with (
        patch.object(slack, "slack_client_for") as client,
        patch.object(slack, "iter_messages", side_effect=messages),
        patch.object(slack, "iter_thread_replies", side_effect=replies),
        patch.object(slack, "build_user_cache", return_value={}),
        patch.object(slack.app, "send_task", side_effect=lambda name, kwargs, **options: history["sent"].append((name, kwargs))),
    ):
        client.return_value.__enter__.return_value = MagicMock()
        yield history


def test_a_failed_sync_resumes_from_the_last_stored_message(db_session, slack_channel, slack_app, slack_history):
    slack_history["messages"] = [history_message("3.0"), history_message("2.0"), history_message("1.0")]

    with patch.object(slack, "process_content_item", side_effect=failing_store_of("2.0")):
        with pytest.raises(OperationalError):
            slack.sync_slack_channel(slack_channel.id, slack_app.id)
    after_failure = (stored_timestamps(db_session, slack_channel.id), channel_cursor(db_session, slack_channel))
    slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert after_failure == (["1.0"], "1.0")
    assert stored_timestamps(db_session, slack_channel.id) == ["1.0", "2.0", "3.0"]
    assert channel_cursor(db_session, slack_channel) == "3.0"


def test_a_message_that_keeps_failing_is_skipped_after_three_attempts(
    db_session, slack_workspace, slack_channel, slack_app, slack_history, caplog
):
    slack_history["messages"] = [history_message("3.0"), history_message("2.0"), history_message("1.0")]

    with patch.object(slack, "process_content_item", side_effect=failing_store_of("2.0", ValueError)):
        for _ in range(slack.MESSAGE_FAILURE_LIMIT):
            with pytest.raises(ValueError):
                slack.sync_slack_channel(slack_channel.id, slack_app.id)
        with caplog.at_level(logging.ERROR, logger=slack.logger.name):
            caplog.clear()
            slack.sync_slack_channel(slack_channel.id, slack_app.id)

    db_session.refresh(slack_workspace)
    assert stored_timestamps(db_session, slack_channel.id) == ["1.0", "3.0"]
    assert channel_cursor(db_session, slack_channel) == "3.0"
    assert [r.getMessage() for r in caplog.records] == [
        f"Slack message 2.0 in channel {slack_channel.id} failed 3 times; skipped"
    ]
    assert slack_workspace.sync_error == f"Slack message 2.0 in channel {slack_channel.id} failed 3 times; skipped"


def test_a_message_whose_store_was_killed_three_times_is_skipped(db_session, slack_channel, slack_app, slack_history):
    slack_history["messages"] = [history_message("2.0"), history_message("1.0")]
    for _ in range(slack.MESSAGE_FAILURE_LIMIT):
        slack.get_redis_client().incr(slack.message_failures_key(slack_channel.id, "2.0"))

    slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert stored_timestamps(db_session, slack_channel.id) == ["1.0"]
    assert channel_cursor(db_session, slack_channel) == "2.0"


@pytest.mark.parametrize(
    "error",
    [
        lost_connection,
        lambda: InterfaceError("SELECT", {}, Exception("connection already closed")),
        lambda: redis.ConnectionError("Connection refused"),
    ],
    ids=["postgres", "postgres-interface", "redis"],
)
def test_losing_a_connection_never_skips_a_message(db_session, slack_channel, slack_app, slack_history, error):
    slack_history["messages"] = [history_message("2.0"), history_message("1.0")]

    with patch.object(slack, "process_content_item", side_effect=failing_store_of("2.0", error)):
        for _ in range(slack.MESSAGE_FAILURE_LIMIT + 1):
            with pytest.raises(type(error())):
                slack.sync_slack_channel(slack_channel.id, slack_app.id)
    slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert stored_timestamps(db_session, slack_channel.id) == ["1.0", "2.0"]


@pytest.mark.parametrize(
    "skipped",
    [
        history_message("2.0", subtype="channel_join"),
        history_message("2.0", subtype="channel_leave"),
        {"ts": "2.0", "subtype": "bot_message", "bot_id": "B1", "text": "deploy finished"},
        {"ts": "2.0", "text": "a message without an author"},
    ],
)
def test_the_cursor_passes_messages_skipped_on_purpose(db_session, slack_channel, slack_app, slack_history, skipped):
    slack_history["messages"] = [skipped, history_message("1.0")]

    slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert stored_timestamps(db_session, slack_channel.id) == ["1.0"]
    assert channel_cursor(db_session, slack_channel) == "2.0"


def test_the_cursor_stays_before_a_thread_whose_replies_could_not_be_read(
    db_session, slack_channel, slack_app, slack_history
):
    slack_history["messages"] = [history_message("1.0", thread_ts="1.0", reply_count=1)]
    slack_history["replies"] = {"1.0": slack.SlackAPIError("ratelimited")}

    result = slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert (result["status"], channel_cursor(db_session, slack_channel)) == ("error", None)


def test_the_cursor_passes_a_thread_that_is_gone(db_session, slack_channel, slack_app, slack_history):
    slack_history["messages"] = [history_message("2.0"), history_message("1.0", thread_ts="1.0", reply_count=1)]
    slack_history["replies"] = {"1.0": slack.SlackAPIError("thread_not_found")}

    slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert stored_timestamps(db_session, slack_channel.id) == ["1.0", "2.0"]
    assert channel_cursor(db_session, slack_channel) == "2.0"


def test_thread_replies_are_stored_with_their_parent(db_session, slack_channel, slack_app, slack_history):
    slack_history["messages"] = [history_message("2.0"), history_message("1.0", thread_ts="1.0", reply_count=2)]
    slack_history["replies"] = {"1.0": [history_message("1.5"), history_message("3.0")]}

    slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert stored_timestamps(db_session, slack_channel.id) == ["1.0", "1.5", "2.0", "3.0"]
    assert channel_cursor(db_session, slack_channel) == "2.0"


@pytest.mark.parametrize(
    "parent_age_days, newest_reply_age_days, read_again",
    [(3, 1 / 24, True), (3, 2.5, False), (20, 1 / 24, False)],
    ids=["new-reply", "no-new-reply", "parent-older-than-lookback"],
)
def test_a_later_sync_reads_replies_posted_under_a_thread_it_already_passed(
    db_session, slack_channel, slack_app, slack_history, parent_age_days, newest_reply_age_days, read_again
):
    now = time.time()
    parent, first, newest = (f"{now - days * 86400:.6f}" for days in (parent_age_days, 2.5, newest_reply_age_days))
    slack_history["messages"] = [history_message(parent, thread_ts=parent, reply_count=1, latest_reply=first)]
    slack_history["replies"] = {parent: [history_message(first)]}
    slack.sync_slack_channel(slack_channel.id, slack_app.id)
    slack_history["threads_read"].clear()

    slack_history["messages"] = [history_message(parent, thread_ts=parent, reply_count=2, latest_reply=newest)]
    slack_history["replies"] = {parent: [history_message(ts) for ts in sorted({first, newest}, key=float)]}
    with patch.object(slack.settings, "SLACK_THREAD_LOOKBACK_DAYS", 14):
        slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert len(stored_timestamps(db_session, slack_channel.id)) == 2 + read_again
    assert slack_history["threads_read"] == [(parent, first)] * read_again


def test_a_sync_out_of_time_continues_in_a_new_task(db_session, slack_channel, slack_app, slack_history):
    slack_history["messages"] = [history_message("2.0"), history_message("1.0")]

    with patch.object(slack, "CHANNEL_SYNC_BUDGET_SECONDS", 0):
        result = slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert (result["status"], stored_timestamps(db_session, slack_channel.id)) == ("continuing", ["1.0"])
    assert channel_cursor(db_session, slack_channel) == "1.0"
    assert slack_history["sent"] == [(SYNC_SLACK_CHANNEL, {"channel_id": slack_channel.id, "slack_app_id": slack_app.id})]


def test_a_turn_lists_history_a_page_at_a_time(db_session, slack_channel, slack_app, slack_history):
    hour = 3600
    slack_history["messages"] = [history_message(f"{n * hour}.000000") for n in range(500, 0, -1)]

    with patch.object(slack, "HISTORY_PAGE_SIZE", 5), patch.object(slack, "CHANNEL_SYNC_BUDGET_SECONDS", 0):
        result = slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert (result["status"], stored_timestamps(db_session, slack_channel.id)) == ("continuing", [f"{hour}.000000"])
    assert (max(slack_history["listed"]), max(slack_history["pages"])) == (6, 1)


def test_messages_on_a_window_edge_are_read_once(db_session, slack_channel, slack_app, slack_history):
    day = 86400
    slack_channel.last_message_ts = f"{day:.6f}"
    db_session.commit()
    slack_history["messages"] = [history_message(f"{k * day:.6f}") for k in range(20, 0, -1)]

    with patch.object(slack, "HISTORY_PAGE_SIZE", 5):
        result = slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert (result["status"], result["messages_read"]) == ("completed", 19)
    assert stored_timestamps(db_session, slack_channel.id) == [f"{k * day:.6f}" for k in range(2, 21)]


def test_a_second_too_full_for_a_page_is_read_whole(db_session, slack_channel, slack_app, slack_history):
    slack_channel.last_message_ts = "1000.000000"
    db_session.commit()
    timestamps = [f"1000.{n}50000" for n in range(10)]
    slack_history["messages"] = [history_message(ts) for ts in reversed(timestamps)]

    with patch.object(slack, "HISTORY_PAGE_SIZE", 5):
        result = slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert (result["status"], result["messages_read"]) == ("completed", 10)
    assert stored_timestamps(db_session, slack_channel.id) == timestamps


def test_an_empty_channel_without_a_cursor_is_listed_once(db_session, slack_channel, slack_app, slack_history):
    result = slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert (result["status"], len(slack_history["listed"])) == ("completed", 1)


def test_a_window_cut_short_by_now_is_halved_from_where_it_ends(db_session, slack_channel, slack_app, slack_history):
    start = int(time.time()) - 100
    slack_channel.last_message_ts = f"{start:.6f}"
    db_session.commit()
    slack_history["messages"] = [history_message(f"{start + s:.6f}") for s in range(95, 0, -10)]

    with patch.object(slack, "HISTORY_PAGE_SIZE", 5), patch.object(slack.settings, "SLACK_THREAD_LOOKBACK_DAYS", 0):
        result = slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert (result["status"], result["messages_read"]) == ("completed", 10)
    assert len(slack_history["listed"]) <= 4


def test_a_sparse_history_is_listed_in_growing_windows(db_session, slack_channel, slack_app, slack_history):
    start = int(time.time()) - 30 * 86400
    slack_channel.last_message_ts = f"{start:.6f}"
    db_session.commit()
    slack_history["messages"] = [history_message(f"{start + h * 3600:.6f}") for h in range(30 * 24, 0, -6)]

    result = slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert (result["status"], result["messages_read"]) == ("completed", 120)
    assert len(slack_history["listed"]) <= 10


def test_a_sync_that_lost_its_lock_leaves_the_cursor(db_session, slack_channel, slack_app, slack_history):
    slack_history["messages"] = [history_message("2.0"), history_message("1.0")]

    with patch.object(Lock, "extend", return_value=False):
        result = slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert (result["status"], stored_timestamps(db_session, slack_channel.id)) == ("error", [])
    assert channel_cursor(db_session, slack_channel) is None


def test_a_sync_whose_lock_expires_in_the_first_thread_stops(db_session, slack_channel, slack_app, slack_history):
    def replies_while_the_lock_expires():
        yield history_message("1.1")
        slack.get_redis_client().delete(slack.channel_sync_lock_key(slack_channel.id))
        yield history_message("1.2")

    slack_history["messages"] = [history_message("2.0"), history_message("1.0", thread_ts="1.0", reply_count=2)]
    slack_history["replies"] = {"1.0": replies_while_the_lock_expires()}

    result = slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert (result["status"], stored_timestamps(db_session, slack_channel.id)) == ("error", ["1.0", "1.1"])
    assert channel_cursor(db_session, slack_channel) is None


def test_a_sync_through_an_api_url_off_the_allowlist_reports_it(
    db_session, slack_channel, slack_app, slack_credentials, slack_user
):
    custom_api_app(db_session, slack_app, slack_user, ["read"])

    with patch("memory.common.chat_urls.settings.CHAT_API_ALLOWED_ORIGINS", []):
        result = slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert result == {"status": "error", "error": "Slack API error: api_base_url_not_allowed"}


@pytest.mark.parametrize(
    "files, calls",
    [
        (None, {"history": False, "users": False}),
        ([{"mimetype": "image/png", "url_private": "https://files.slack.com/a.png"}], {"history": False, "users": False, "images": False}),
    ],
)
def test_slack_is_never_called_inside_a_db_transaction(db_session, slack_channel, slack_app, slack_history, files, calls):
    """Postgres kills a connection left idle in a transaction for a minute, and
    Slack calls (history pages, rate-limit waits, user lists, downloads) can take longer."""
    sessions = []
    make_session = slack.make_session

    @contextlib.contextmanager
    def tracked_session():
        with make_session() as session:
            sessions.append(session)
            yield session
            sessions.remove(session)

    in_transaction: dict[str, bool] = {}

    def recording(call, respond):
        def side_effect(*args, **kwargs):
            in_transaction[call] = any(s.in_transaction() for s in sessions)
            return respond(*args, **kwargs)
        return side_effect

    slack_history["messages"] = [history_message("1.0", files=files)]
    with (
        patch.object(slack, "make_session", tracked_session),
        patch.object(slack, "iter_messages", side_effect=recording("history", cast(MagicMock, slack.iter_messages).side_effect)),
        patch.object(slack, "build_user_cache", side_effect=recording("users", lambda *a: {})),
        patch.object(slack, "download_message_images", side_effect=recording("images", lambda *a, **kw: [])),
    ):
        slack.sync_slack_channel(slack_channel.id, slack_app.id)

    assert in_transaction == calls


UNCLAIMED_TS = "1704067300.000100"


@pytest.mark.parametrize(
    "messages, replies",
    [
        ([history_message(UNCLAIMED_TS)], {}),
        ([history_message("1.0", thread_ts="1.0", reply_count=1)], {"1.0": [history_message(UNCLAIMED_TS)]}),
    ],
)
def test_a_sync_claims_messages_no_app_has_claimed(
    db_session, unclaimed_message, slack_channel, slack_app, slack_history, messages, replies
):
    slack_history["messages"] = messages
    slack_history["replies"] = replies

    slack.sync_slack_channel(slack_channel.id, slack_app.id)

    db_session.refresh(unclaimed_message)
    assert (unclaimed_message.message_ts, unclaimed_message.slack_app_id) == (UNCLAIMED_TS, slack_app.id)


@pytest.fixture
def stored_history(db_session, slack_workspace, slack_channel, slack_app):
    """``slack_channel`` holds top-level messages of Oct 1 and 5 and an Oct 20 reply to the first."""
    for day, thread in [("2026-10-01", "2026-10-01"), ("2026-10-05", None), ("2026-10-20", "2026-10-01")]:
        db_session.add(
            SlackMessage(
                modality="message",
                sha256=day.encode() + bytes(22),
                content=f"message of {day}",
                message_ts=slack.slack_ts(day),
                thread_ts=thread and slack.slack_ts(thread),
                channel_id=slack_channel.id,
                workspace_id=slack_workspace.id,
                author_id="U1",
                message_type="message",
                slack_app_id=slack_app.id,
            )
        )
    db_session.commit()
    return slack_channel


@pytest.mark.parametrize(
    "cursor, since, rewound_to",
    [
        ("2026-11-05", None, "2026-10-05"),
        ("2026-10-25", None, "2026-10-05"),
        ("2026-11-05", "2026-10-03", "2026-10-03"),
        ("2026-11-05", "2026-10-10", "2026-10-05"),
    ],
)
def test_rewinding_moves_a_cursor_back_to_the_newest_stored_message(
    db_session, stored_history, slack_credentials, slack_app, cursor, since, rewound_to
):
    stored_history.last_message_ts = slack.slack_ts(cursor)
    db_session.commit()

    with patch.object(slack.app, "send_task") as send:
        slack.rewind_slack_cursors(since=since)

    assert channel_cursor(db_session, stored_history) == slack.slack_ts(rewound_to)
    assert [c.kwargs["kwargs"] for c in send.call_args_list] == [
        {"channel_id": stored_history.id, "slack_app_id": slack_app.id}
    ]


@pytest.mark.parametrize("cursor, since", [("2026-10-05", None), ("2026-10-02", "2026-10-10")])
def test_rewinding_leaves_a_cursor_at_or_before_its_target(
    db_session, stored_history, slack_credentials, slack_app, cursor, since
):
    stored_history.last_message_ts = slack.slack_ts(cursor)
    db_session.commit()

    with patch.object(slack.app, "send_task") as send:
        result = slack.rewind_slack_cursors(since=since)

    assert (result["channels"], send.call_count) == ([], 0)
    assert channel_cursor(db_session, stored_history) == slack.slack_ts(cursor)


@pytest.mark.parametrize("since, rewound_to", [(None, None), ("2026-05-01", slack.slack_ts("2026-05-01"))])
def test_rewinding_a_channel_with_nothing_stored_refetches_its_history_from_since(
    db_session, slack_channel, slack_credentials, slack_app, since, rewound_to
):
    slack_channel.last_message_ts = slack.slack_ts("2026-05-16")
    db_session.commit()

    with patch.object(slack.app, "send_task"):
        result = slack.rewind_slack_cursors(since=since)

    assert channel_cursor(db_session, slack_channel) == rewound_to
    assert result["channels"] == [
        {"channel_id": slack_channel.id, "cursor": slack.slack_ts("2026-05-16"), "rewound_to": rewound_to, "queued": True}
    ]


@pytest.mark.parametrize("collect_messages, is_archived", [(False, False), (True, True)])
def test_rewinding_leaves_channels_that_are_not_collected(
    db_session, slack_channel, slack_credentials, slack_app, collect_messages, is_archived
):
    slack_channel.last_message_ts = slack.slack_ts("2026-05-16")
    slack_channel.collect_messages = collect_messages
    slack_channel.is_archived = is_archived
    db_session.commit()

    with patch.object(slack.app, "send_task") as send:
        result = slack.rewind_slack_cursors()

    assert (result["channels"], send.call_count) == ([], 0)
    assert channel_cursor(db_session, slack_channel) == slack.slack_ts("2026-05-16")


@pytest.mark.parametrize("dry_run", [False, True])
def test_rewinding_reports_a_channel_whose_sync_is_running(
    db_session, stored_history, slack_credentials, slack_app, dry_run
):
    stored_history.last_message_ts = slack.slack_ts("2026-11-05")
    db_session.commit()

    with slack.channel_sync_lock(stored_history.id), patch.object(slack.app, "send_task") as send:
        result = slack.rewind_slack_cursors(dry_run=dry_run)

    assert (result["channels"], result["busy"], send.call_count) == ([], [stored_history.id], 0)
    assert channel_cursor(db_session, stored_history) == slack.slack_ts("2026-11-05")


def test_a_dry_run_rewinds_nothing(db_session, stored_history, slack_credentials, slack_app):
    stored_history.last_message_ts = slack.slack_ts("2026-11-05")
    db_session.commit()

    with patch.object(slack.app, "send_task") as send:
        result = slack.rewind_slack_cursors(dry_run=True)

    assert result["channels"] == [
        {
            "channel_id": stored_history.id,
            "cursor": slack.slack_ts("2026-11-05"),
            "rewound_to": slack.slack_ts("2026-10-05"),
            "queued": True,
        }
    ]
    assert (channel_cursor(db_session, stored_history), send.call_count) == (slack.slack_ts("2026-11-05"), 0)


# --- Polls that store what the Events API should have delivered ---


def minutes_ago(minutes: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


def sent(minutes: int) -> str:
    return f"{minutes_ago(minutes).timestamp():.6f}"


@pytest.fixture
def pushing_app(db_session, slack_app, slack_channel, slack_user):
    slack_app.created_by_user_id = slack_user.id
    slack_app.last_event_at = minutes_ago(1)
    slack_channel.push_since = minutes_ago(120)
    db_session.commit()
    return slack_app


def poll(app, channel, ts: str) -> dict:
    message = history_message(ts, text="A message long enough to store and embed.")
    return slack.store_history_message(message, channel.workspace_id, channel.id, app.id)


def push(app, channel, ts: str) -> dict:
    return slack.store_slack_message(channel.workspace_id, channel.id, ts, "U1", "Pushed, long enough to store.", app.id)


def notifications(no_celery_dispatch) -> list:
    return [c for c in no_celery_dispatch.call_args_list if c.args and c.args[0] == SEND_NOTIFICATION]


def test_poll_storing_an_unpushed_message_marks_the_app_and_notifies_once(
    db_session, pushing_app, slack_channel, qdrant, no_celery_dispatch
):
    poll(pushing_app, slack_channel, sent(30))
    db_session.refresh(pushing_app)
    db_session.refresh(slack_channel)
    first_miss = pushing_app.push_last_miss_at
    assert pushing_app.push_missing_since == first_miss is not None
    assert slack_channel.push_since is None  # push no longer counts as covering it
    [notification] = notifications(no_celery_dispatch)
    assert "Test Slack App" in notification.kwargs["args"][2]
    assert "long enough" not in notification.kwargs["args"][2]

    # Events from other channels end the outage; this channel can't start another.
    pushing_app.push_missing_since = None
    db_session.commit()
    poll(pushing_app, slack_channel, sent(20))
    db_session.refresh(pushing_app)
    assert (pushing_app.push_missing_since, pushing_app.push_last_miss_at) == (None, first_miss)
    assert len(notifications(no_celery_dispatch)) == 1


def no_push_to_channel(app, channel):
    channel.push_since = None


def no_events_for_app(app, channel):
    app.last_event_at = None


def push_as_set_up(app, channel):
    pass


@pytest.mark.parametrize(
    "minutes, setup",
    [
        (5, push_as_set_up),  # its event may still be on its way
        (150, push_as_set_up),  # sent before push reached the channel
        (30, no_push_to_channel),  # the app's bot isn't in it, so push never covered it
        (30, no_events_for_app),
    ],
)
def test_polled_messages_push_cannot_have_missed_leave_the_app_unmarked(
    db_session, pushing_app, slack_channel, qdrant, no_celery_dispatch, minutes, setup
):
    setup(pushing_app, slack_channel)
    db_session.commit()

    poll(pushing_app, slack_channel, sent(minutes))

    db_session.refresh(pushing_app)
    assert pushing_app.push_missing_since is None
    assert notifications(no_celery_dispatch) == []


def test_poll_of_a_pushed_message_leaves_the_app_unmarked(db_session, pushing_app, slack_channel, qdrant):
    ts = sent(30)
    push(pushing_app, slack_channel, ts)

    poll(pushing_app, slack_channel, ts)

    db_session.refresh(pushing_app)
    assert pushing_app.push_missing_since is None


def test_the_first_pushed_message_dates_push_in_its_channel(db_session, slack_app, slack_channel, qdrant):
    push(slack_app, slack_channel, sent(3))
    db_session.refresh(slack_channel)
    first = slack_channel.push_since
    push(slack_app, slack_channel, sent(1))

    db_session.refresh(slack_channel)
    assert first is not None
    assert slack_channel.push_since == first
