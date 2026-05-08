"""Tests for Slack Celery tasks."""

from unittest.mock import MagicMock, patch

import pytest

from memory.common.db.models import User
from memory.common.db.models.slack import (
    SlackApp,
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
    user = User(
        id=1,
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


@pytest.mark.transactional_db
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


@patch("memory.workers.tasks.slack.SlackClient")
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


@patch("memory.workers.tasks.slack.SlackClient")
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


@patch("memory.workers.tasks.slack.SlackClient")
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


@pytest.mark.transactional_db
@patch("memory.workers.tasks.slack.get_workspace_credentials")
@patch("memory.workers.tasks.slack.build_user_cache")
def test_add_slack_message_race_merges_into_existing(
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
    slack.add_slack_message(**sample_message_data)

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
        result = slack.add_slack_message(**loser_data)

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
    assert rows[0].reactions == loser_data["reactions"]
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
    existing.reactions = [{"name": "old", "count": 1}]
    new_reactions = [{"name": "fire", "count": 3}]
    slack.merge_slack_message_state(
        existing, "v1", None, reactions=new_reactions, files=None
    )
    assert existing.reactions == new_reactions


def test_merge_slack_message_state_preserves_reactions_when_incoming_none():
    """When incoming reactions is None (e.g., only edit event), keep existing."""
    existing = _stub_slack_message("v1", None)
    existing.reactions = [{"name": "fire", "count": 3}]
    slack.merge_slack_message_state(
        existing, "v2", "1700000020.0", reactions=None, files=None
    )
    assert existing.reactions == [{"name": "fire", "count": 3}]


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


@patch("memory.workers.tasks.slack.SlackClient")
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


@patch("memory.workers.tasks.slack.SlackClient")
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


@patch("memory.workers.tasks.slack.SlackClient")
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


@patch("memory.workers.tasks.slack.SlackClient")
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


