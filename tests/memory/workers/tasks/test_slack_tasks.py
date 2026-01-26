"""Tests for Slack Celery tasks."""

from unittest.mock import MagicMock, patch

import pytest

from memory.common.db.models import User
from memory.common.db.models.slack import (
    SlackChannel,
    SlackUserCredentials,
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
def slack_credentials(db_session, slack_workspace, slack_user):
    """Create Slack credentials for testing."""
    credentials = SlackUserCredentials(
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
def sample_message_data(slack_workspace, slack_channel):
    """Sample message data for testing."""
    return {
        "workspace_id": slack_workspace.id,
        "channel_id": slack_channel.id,
        "message_ts": "1704067200.000100",
        "author_id": "U12345678",  # Just a Slack user ID
        "content": "This is a test Slack message with enough content to be processed properly.",
        "thread_ts": None,
        "reply_count": None,
        "subtype": None,
        "edited_ts": None,
        "reactions": None,
        "files": None,
    }


@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_add_slack_message_success(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_credentials, qdrant
):
    """Test successful Slack message addition."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {"U12345678": "Test User"}

    result = slack.add_slack_message(**sample_message_data)

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
def test_add_slack_message_already_exists(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_credentials, qdrant
):
    """Test adding a message that already exists."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {"U12345678": "Test User"}

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


@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_add_slack_message_with_thread(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_credentials, qdrant
):
    """Test adding a Slack message that is part of a thread."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {}

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


@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_add_slack_message_with_reactions(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_credentials, qdrant
):
    """Test adding a Slack message with reactions."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {}

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


@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_add_slack_message_update_on_edit(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_credentials, qdrant
):
    """Test updating an existing message when edited."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {}

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


@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_add_slack_message_unique_per_channel(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_workspace, slack_credentials, qdrant
):
    """Test that same message_ts in different channels creates separate messages."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {}

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
def test_add_slack_message_with_subtype(
    mock_build_cache, mock_get_creds, db_session, sample_message_data, slack_credentials, qdrant
):
    """Test adding a Slack message with a subtype."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {}

    sample_message_data["subtype"] = "channel_join"

    slack.add_slack_message(**sample_message_data)

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


def test_sync_slack_workspace_no_credentials(db_session, slack_workspace):
    """Test syncing workspace without credentials returns error."""
    result = slack.sync_slack_workspace(slack_workspace.id)

    assert result["status"] == "error"
    assert "No valid credentials" in result["error"]


def test_sync_slack_workspace_not_found(db_session):
    """Test syncing non-existent workspace returns error."""
    result = slack.sync_slack_workspace("T_NONEXISTENT")

    assert result["status"] == "error"
    assert "Workspace not found" in result["error"]


@patch("memory.workers.tasks.slack.SlackClient")
@patch("memory.workers.tasks.slack.sync_workspace_channels")
def test_sync_slack_workspace_success(
    mock_sync_channels,
    mock_client_class,
    db_session,
    slack_workspace,
    slack_credentials,
):
    """Test successful workspace sync."""
    mock_client = MagicMock()
    mock_client.call.return_value = {"team": "Test Workspace"}
    mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_class.return_value.__exit__ = MagicMock(return_value=False)
    mock_sync_channels.return_value = 3

    result = slack.sync_slack_workspace(slack_workspace.id)

    assert result["status"] == "completed"
    assert result["channels_synced"] == 3


@patch("memory.workers.tasks.slack.SlackClient")
def test_sync_slack_workspace_token_expired(
    mock_client_class, db_session, slack_workspace, slack_credentials
):
    """Test workspace sync with expired token."""
    mock_client = MagicMock()
    mock_client.call.side_effect = slack.SlackAPIError("token_expired")
    mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

    result = slack.sync_slack_workspace(slack_workspace.id)

    assert result["status"] == "error"
    assert "token_expired" in result["error"]

    # Verify sync_error was set
    db_session.refresh(slack_workspace)
    assert "Token invalid" in slack_workspace.sync_error


@patch("memory.workers.tasks.slack.SlackClient")
def test_sync_slack_workspace_unexpected_error(
    mock_client_class, db_session, slack_workspace, slack_credentials
):
    """Test workspace sync with unexpected error doesn't re-raise."""
    mock_client = MagicMock()
    mock_client.call.side_effect = Exception("Unexpected error")
    mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

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
        ("group", "private_channel"),
    ],
)
@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_add_slack_message_creates_channel_if_missing(
    mock_build_cache, mock_get_creds,
    db_session, sample_message_data, slack_workspace, slack_credentials,
    channel_type, expected_type, qdrant
):
    """Test that add_slack_message creates channel if it doesn't exist."""
    mock_get_creds.return_value = slack_credentials
    mock_build_cache.return_value = {}

    # Use a channel ID that doesn't exist
    sample_message_data["channel_id"] = f"C_NEW_{channel_type}"

    result = slack.add_slack_message(**sample_message_data)

    assert result["status"] == "processed"

    # Verify channel was created
    channel = db_session.query(SlackChannel).filter_by(id=sample_message_data["channel_id"]).first()
    assert channel is not None
    assert channel.workspace_id == slack_workspace.id


def test_get_workspace_credentials_returns_valid(db_session, slack_workspace, slack_credentials):
    """Test that get_workspace_credentials returns valid credentials."""
    result = slack.get_workspace_credentials(db_session, slack_workspace.id)

    assert result is not None
    assert result.workspace_id == slack_workspace.id
    assert result.access_token == "xoxp-test-token"


def test_get_workspace_credentials_returns_none_when_no_creds(db_session, slack_workspace):
    """Test that get_workspace_credentials returns None when no credentials."""
    result = slack.get_workspace_credentials(db_session, slack_workspace.id)

    assert result is None
