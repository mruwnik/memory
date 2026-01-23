"""Tests for Slack Celery tasks."""

from unittest.mock import MagicMock, patch

import pytest

from memory.common.db.models import User
from memory.common.db.models.slack import (
    SlackChannel,
    SlackUser,
    SlackWorkspace,
)
from memory.common.db.models.source_items import SlackMessage
from memory.workers.tasks import slack


@pytest.fixture
def slack_user(db_session):
    """Create a test user for Slack workspace ownership."""
    existing = db_session.query(User).filter(User.id == 1).first()
    if existing:
        return existing
    user = User(id=1, name="Test User", email="test@example.com")
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def slack_workspace(db_session, slack_user):
    """Create a Slack workspace for testing."""
    workspace = SlackWorkspace(
        id="T12345678",
        name="Test Workspace",
        user_id=slack_user.id,
        collect_messages=True,
    )
    workspace.access_token = "xoxp-test-token"
    db_session.add(workspace)
    db_session.commit()
    return workspace


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
def slack_author(db_session, slack_workspace):
    """Create a Slack user for testing."""
    user = SlackUser(
        id="U12345678",
        workspace_id=slack_workspace.id,
        username="testuser",
        display_name="Test User",
        real_name="Test User",
        is_bot=False,
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def sample_message_data(slack_workspace, slack_channel, slack_author):
    """Sample message data for testing."""
    return {
        "workspace_id": slack_workspace.id,
        "channel_id": slack_channel.id,
        "message_ts": "1704067200.000100",
        "author_id": slack_author.id,
        "content": "This is a test Slack message with enough content to be processed properly.",
        "thread_ts": None,
        "reply_count": None,
        "subtype": None,
        "edited_ts": None,
        "reactions": None,
        "files": None,
    }


def test_add_slack_message_success(db_session, sample_message_data, qdrant):
    """Test successful Slack message addition."""
    result = slack.add_slack_message(**sample_message_data)

    assert result["status"] == "created"
    assert "message_ts" in result

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


def test_add_slack_message_already_exists(db_session, sample_message_data, qdrant):
    """Test adding a message that already exists."""
    # Add the message once
    slack.add_slack_message(**sample_message_data)

    # Try to add it again
    result = slack.add_slack_message(**sample_message_data)

    assert result["status"] == "already_exists"
    assert result["message_ts"] == sample_message_data["message_ts"]

    # Verify no duplicate was created
    messages = (
        db_session.query(SlackMessage)
        .filter_by(message_ts=sample_message_data["message_ts"])
        .all()
    )
    assert len(messages) == 1


def test_add_slack_message_with_thread(db_session, sample_message_data, qdrant):
    """Test adding a Slack message that is part of a thread."""
    sample_message_data["thread_ts"] = "1704067100.000000"
    sample_message_data["reply_count"] = 5

    slack.add_slack_message(**sample_message_data)

    message = (
        db_session.query(SlackMessage)
        .filter_by(message_ts=sample_message_data["message_ts"])
        .first()
    )
    assert message.thread_ts == "1704067100.000000"
    assert message.reply_count == 5


def test_add_slack_message_with_reactions(db_session, sample_message_data, qdrant):
    """Test adding a Slack message with reactions."""
    sample_message_data["reactions"] = [
        {"name": "thumbsup", "count": 5, "users": ["U1", "U2"]},
        {"name": "heart", "count": 3, "users": ["U3"]},
    ]

    slack.add_slack_message(**sample_message_data)

    message = (
        db_session.query(SlackMessage)
        .filter_by(message_ts=sample_message_data["message_ts"])
        .first()
    )
    assert message.reactions is not None
    assert len(message.reactions) == 2
    assert message.reactions[0]["name"] == "thumbsup"


def test_add_slack_message_update_on_edit(db_session, sample_message_data, qdrant):
    """Test updating an existing message when edited."""
    # Add the message first
    slack.add_slack_message(**sample_message_data)

    # Update with edit
    sample_message_data["content"] = "Edited content with enough text to be meaningful."
    sample_message_data["edited_ts"] = "1704067300.000000"

    result = slack.add_slack_message(**sample_message_data)

    assert result["status"] == "updated"

    message = (
        db_session.query(SlackMessage)
        .filter_by(message_ts=sample_message_data["message_ts"])
        .first()
    )
    assert message.content == "Edited content with enough text to be meaningful."
    assert message.edited_ts == "1704067300.000000"


def test_add_slack_message_no_author_skipped(db_session, sample_message_data, qdrant):
    """Test that messages without an author are skipped."""
    sample_message_data["author_id"] = None

    result = slack.add_slack_message(**sample_message_data)

    assert result["status"] == "skipped"
    assert result["reason"] == "no_author"


def test_add_slack_message_unique_per_channel(db_session, sample_message_data, slack_workspace, qdrant):
    """Test that same message_ts in different channels creates separate messages."""
    # Add first message
    slack.add_slack_message(**sample_message_data)

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

    result = slack.add_slack_message(**sample_message_data)

    assert result["status"] == "created"

    # Verify both messages exist
    messages = (
        db_session.query(SlackMessage)
        .filter_by(message_ts=sample_message_data["message_ts"])
        .all()
    )
    assert len(messages) == 2


def test_add_slack_message_with_subtype(db_session, sample_message_data, qdrant):
    """Test adding a Slack message with a subtype."""
    sample_message_data["subtype"] = "channel_join"

    slack.add_slack_message(**sample_message_data)

    message = (
        db_session.query(SlackMessage)
        .filter_by(message_ts=sample_message_data["message_ts"])
        .first()
    )
    assert message.message_type == "channel_join"


def test_resolve_mentions(db_session, slack_workspace, slack_author):
    """Test mention resolution in message content."""
    # Add workspace users to be resolved
    users_by_id = {slack_author.id: slack_author}

    content = f"Hello <@{slack_author.id}>, how are you?"
    resolved = slack.resolve_mentions(content, users_by_id)

    assert f"@{slack_author.display_name}" in resolved
    assert f"<@{slack_author.id}>" not in resolved


def test_resolve_mentions_unknown_user(db_session, slack_workspace):
    """Test mention resolution with unknown user."""
    users_by_id = {}

    content = "Hello <@U_UNKNOWN>, how are you?"
    resolved = slack.resolve_mentions(content, users_by_id)

    # Unknown mentions should be preserved
    assert "<@U_UNKNOWN>" in resolved


@patch("memory.workers.tasks.slack.get_slack_client")
def test_sync_slack_workspace_no_token(mock_client, db_session, slack_workspace):
    """Test syncing workspace without access token returns error."""
    slack_workspace.access_token = None
    db_session.commit()

    result = slack.sync_slack_workspace(slack_workspace.id)

    assert result["status"] == "error"
    assert "No access token" in result["error"]


def test_sync_slack_workspace_not_found(db_session):
    """Test syncing non-existent workspace returns error."""
    result = slack.sync_slack_workspace("T_NONEXISTENT")

    assert result["status"] == "error"
    assert "Workspace not found" in result["error"]


@patch("memory.workers.tasks.slack.get_slack_client")
@patch("memory.workers.tasks.slack.slack_api_call")
@patch("memory.workers.tasks.slack.sync_workspace_users")
@patch("memory.workers.tasks.slack.sync_workspace_channels")
def test_sync_slack_workspace_success(
    mock_sync_channels,
    mock_sync_users,
    mock_api_call,
    mock_client,
    db_session,
    slack_workspace,
):
    """Test successful workspace sync."""
    mock_client.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_client.return_value.__exit__ = MagicMock(return_value=False)
    mock_api_call.return_value = {"team": "Test Workspace"}
    mock_sync_users.return_value = 5
    mock_sync_channels.return_value = 3

    result = slack.sync_slack_workspace(slack_workspace.id)

    assert result["status"] == "completed"
    assert result["users_synced"] == 5
    assert result["channels_synced"] == 3


@patch("memory.workers.tasks.slack.get_slack_client")
@patch("memory.workers.tasks.slack.slack_api_call")
def test_sync_slack_workspace_token_expired(
    mock_api_call, mock_client, db_session, slack_workspace
):
    """Test workspace sync with expired token."""
    mock_client.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_client.return_value.__exit__ = MagicMock(return_value=False)
    mock_api_call.side_effect = slack.SlackAPIError("token_expired")

    result = slack.sync_slack_workspace(slack_workspace.id)

    assert result["status"] == "error"
    assert "token_expired" in result["error"]

    # Verify sync_error was set
    db_session.refresh(slack_workspace)
    assert "Token invalid" in slack_workspace.sync_error


@patch("memory.workers.tasks.slack.get_slack_client")
@patch("memory.workers.tasks.slack.slack_api_call")
def test_sync_slack_workspace_unexpected_error(
    mock_api_call, mock_client, db_session, slack_workspace
):
    """Test workspace sync with unexpected error doesn't re-raise."""
    mock_client.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_client.return_value.__exit__ = MagicMock(return_value=False)
    mock_api_call.side_effect = Exception("Unexpected error")

    result = slack.sync_slack_workspace(slack_workspace.id)

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
        ("group", "group_dm"),
    ],
)
def test_add_slack_message_creates_channel_if_missing(
    db_session, sample_message_data, slack_workspace, slack_author, channel_type, expected_type, qdrant
):
    """Test that add_slack_message creates channel if it doesn't exist."""
    # Use a channel ID that doesn't exist
    sample_message_data["channel_id"] = f"C_NEW_{channel_type}"

    result = slack.add_slack_message(**sample_message_data)

    assert result["status"] == "created"

    # Verify channel was created
    channel = db_session.query(SlackChannel).filter_by(id=sample_message_data["channel_id"]).first()
    assert channel is not None
    assert channel.workspace_id == slack_workspace.id
