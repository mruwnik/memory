"""Tests for MCP schedule server."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from memory.api.MCP.servers.schedule import (
    schedule_message,
    list_scheduled_llm_calls,
    get_scheduled_call,
    cancel_scheduled_llm_call,
    get_current_user,
    set_auth_provider,
)


@pytest.fixture
def mock_auth_user():
    """Mock authenticated user for tests."""
    return {
        "authenticated": True,
        "user": {
            "user_id": 1,
            "discord_users": {"test_user#1234": {"id": 12345}},
        },
    }


# ====== get_current_user tests ======


def test_get_current_user_not_configured():
    """Get current user without auth provider returns error."""
    # Reset auth provider
    set_auth_provider(None)

    result = get_current_user()

    assert result["authenticated"] is False
    assert "Auth provider not configured" in result["error"]


def test_get_current_user_configured():
    """Get current user with auth provider works."""
    mock_user = {"authenticated": True, "user": {"user_id": 1}}
    set_auth_provider(lambda: mock_user)

    result = get_current_user()

    assert result == mock_user


# ====== schedule_message tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
@patch("memory.api.MCP.servers.schedule.make_session")
@patch("memory.api.MCP.servers.schedule.schedule_discord_message")
async def test_schedule_message_success(
    mock_schedule_discord, mock_make_session, mock_get_user, mock_auth_user
):
    """Schedule message succeeds with required fields."""
    mock_get_user.return_value = mock_auth_user
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_bot = MagicMock()
    mock_bot.id = 99
    mock_session.query.return_value.first.return_value = mock_bot

    mock_call = MagicMock()
    mock_call.id = "call-123"
    mock_schedule_discord.return_value = mock_call

    result = await schedule_message.fn(
        scheduled_time="2026-01-20T15:30:00Z",
        message="Test message",
    )

    assert result["success"] is True
    assert result["scheduled_call_id"] == "call-123"
    assert "2026-01-20" in result["scheduled_time"]
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
@patch("memory.api.MCP.servers.schedule.make_session")
@patch("memory.api.MCP.servers.schedule.schedule_discord_message")
async def test_schedule_message_with_model(
    mock_schedule_discord, mock_make_session, mock_get_user, mock_auth_user
):
    """Schedule message with LLM model."""
    mock_get_user.return_value = mock_auth_user
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_bot = MagicMock()
    mock_session.query.return_value.first.return_value = mock_bot

    mock_call = MagicMock()
    mock_call.id = "call-456"
    mock_schedule_discord.return_value = mock_call

    result = await schedule_message.fn(
        scheduled_time="2026-01-20T15:30:00Z",
        message="What's the weather?",
        model="anthropic/claude-3-5-sonnet-20241022",
        system_prompt="You are a weather assistant",
    )

    assert result["success"] is True
    # Verify model was passed to schedule_discord_message
    call_kwargs = mock_schedule_discord.call_args.kwargs
    assert call_kwargs["model"] == "anthropic/claude-3-5-sonnet-20241022"
    assert call_kwargs["system_prompt"] == "You are a weather assistant"


@pytest.mark.asyncio
async def test_schedule_message_empty_message_raises():
    """Schedule message without message raises ValueError."""
    with pytest.raises(ValueError, match="You must provide `message`"):
        await schedule_message.fn(
            scheduled_time="2026-01-20T15:30:00Z",
            message="",
        )


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
async def test_schedule_message_not_authenticated_raises(mock_get_user):
    """Schedule message without authentication raises ValueError."""
    mock_get_user.return_value = {"authenticated": False}

    with pytest.raises(ValueError, match="Not authenticated"):
        await schedule_message.fn(
            scheduled_time="2026-01-20T15:30:00Z",
            message="Test",
        )


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
async def test_schedule_message_no_user_id_raises(mock_get_user):
    """Schedule message without user ID raises ValueError."""
    mock_get_user.return_value = {"authenticated": True, "user": {}}

    with pytest.raises(ValueError, match="User not found"):
        await schedule_message.fn(
            scheduled_time="2026-01-20T15:30:00Z",
            message="Test",
        )


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
async def test_schedule_message_no_discord_user_or_channel_raises(mock_get_user):
    """Schedule message without Discord user or channel raises ValueError."""
    mock_get_user.return_value = {
        "authenticated": True,
        "user": {"user_id": 1, "discord_users": {}},
    }

    with pytest.raises(ValueError, match="Either discord_user or discord_channel must be provided"):
        await schedule_message.fn(
            scheduled_time="2026-01-20T15:30:00Z",
            message="Test",
        )


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
async def test_schedule_message_invalid_datetime_raises(mock_get_user, mock_auth_user):
    """Schedule message with invalid datetime raises ValueError."""
    mock_get_user.return_value = mock_auth_user

    with pytest.raises(ValueError, match="Invalid datetime format"):
        await schedule_message.fn(
            scheduled_time="not-a-datetime",
            message="Test",
        )


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
@patch("memory.api.MCP.servers.schedule.make_session")
async def test_schedule_message_no_bot_returns_error(
    mock_make_session, mock_get_user, mock_auth_user
):
    """Schedule message without bot returns error."""
    mock_get_user.return_value = mock_auth_user
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    # No bot found
    mock_session.query.return_value.first.return_value = None

    result = await schedule_message.fn(
        scheduled_time="2026-01-20T15:30:00Z",
        message="Test",
    )

    assert "error" in result
    assert result["error"] == "No bot found"


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
@patch("memory.api.MCP.servers.schedule.make_session")
@patch("memory.api.MCP.servers.schedule.schedule_discord_message")
async def test_schedule_message_with_metadata(
    mock_schedule_discord, mock_make_session, mock_get_user, mock_auth_user
):
    """Schedule message with metadata."""
    mock_get_user.return_value = mock_auth_user
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_bot = MagicMock()
    mock_session.query.return_value.first.return_value = mock_bot

    mock_call = MagicMock()
    mock_call.id = "call-789"
    mock_schedule_discord.return_value = mock_call

    metadata = {"source": "test", "priority": "high"}

    result = await schedule_message.fn(
        scheduled_time="2026-01-20T15:30:00Z",
        message="Test",
        metadata=metadata,
    )

    assert result["success"] is True
    call_kwargs = mock_schedule_discord.call_args.kwargs
    assert call_kwargs["metadata"] == metadata


# ====== list_scheduled_llm_calls tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
@patch("memory.api.MCP.servers.schedule.make_session")
async def test_list_scheduled_llm_calls_success(
    mock_make_session, mock_get_user, mock_auth_user
):
    """List scheduled LLM calls returns user's calls."""
    mock_get_user.return_value = mock_auth_user
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_call1 = MagicMock()
    mock_call1.serialize.return_value = {"id": "call-1", "status": "pending"}
    mock_call2 = MagicMock()
    mock_call2.serialize.return_value = {"id": "call-2", "status": "completed"}

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = [mock_call1, mock_call2]

    result = await list_scheduled_llm_calls.fn()

    assert result["success"] is True
    assert len(result["scheduled_calls"]) == 2
    assert result["count"] == 2


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
@patch("memory.api.MCP.servers.schedule.make_session")
async def test_list_scheduled_llm_calls_with_status_filter(
    mock_make_session, mock_get_user, mock_auth_user
):
    """List scheduled LLM calls filters by status."""
    mock_get_user.return_value = mock_auth_user
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_scheduled_llm_calls.fn(status="pending")

    # Should have two filter calls: user_id and status
    assert query_mock.filter.call_count == 2


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
@patch("memory.api.MCP.servers.schedule.make_session")
async def test_list_scheduled_llm_calls_pagination(
    mock_make_session, mock_get_user, mock_auth_user
):
    """List scheduled LLM calls supports pagination."""
    mock_get_user.return_value = mock_auth_user
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_scheduled_llm_calls.fn(limit=10, offset=20)

    query_mock.offset.assert_called_once_with(20)
    query_mock.limit.assert_called_once_with(10)


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
@patch("memory.api.MCP.servers.schedule.make_session")
async def test_list_scheduled_llm_calls_enforces_max_limit(
    mock_make_session, mock_get_user, mock_auth_user
):
    """List scheduled LLM calls enforces max limit of 200."""
    mock_get_user.return_value = mock_auth_user
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_scheduled_llm_calls.fn(limit=500)

    # Should cap at 200
    query_mock.limit.assert_called_once_with(200)


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
async def test_list_scheduled_llm_calls_not_authenticated_returns_error(mock_get_user):
    """List scheduled LLM calls without authentication returns error."""
    mock_get_user.return_value = {"authenticated": False}

    result = await list_scheduled_llm_calls.fn()

    assert "error" in result
    assert result["error"] == "Not authenticated"


# ====== get_scheduled_call tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
@patch("memory.api.MCP.servers.schedule.make_session")
async def test_get_scheduled_call_success(
    mock_make_session, mock_get_user, mock_auth_user
):
    """Get scheduled call returns call details."""
    mock_get_user.return_value = mock_auth_user
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_call = MagicMock()
    mock_call.serialize.return_value = {
        "id": "call-123",
        "status": "pending",
        "message": "Test message",
    }

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.first.return_value = mock_call

    result = await get_scheduled_call.fn(scheduled_call_id="call-123")

    assert result["success"] is True
    assert result["scheduled_call"]["id"] == "call-123"


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
@patch("memory.api.MCP.servers.schedule.make_session")
async def test_get_scheduled_call_not_found_returns_error(
    mock_make_session, mock_get_user, mock_auth_user
):
    """Get scheduled call for non-existent ID returns error."""
    mock_get_user.return_value = mock_auth_user
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.first.return_value = None

    result = await get_scheduled_call.fn(scheduled_call_id="nonexistent")

    assert "error" in result
    assert result["error"] == "Scheduled call not found"


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
async def test_get_scheduled_call_not_authenticated_returns_error(mock_get_user):
    """Get scheduled call without authentication returns error."""
    mock_get_user.return_value = {"authenticated": False}

    result = await get_scheduled_call.fn(scheduled_call_id="call-123")

    assert "error" in result
    assert result["error"] == "Not authenticated"


# ====== cancel_scheduled_llm_call tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
@patch("memory.api.MCP.servers.schedule.make_session")
async def test_cancel_scheduled_llm_call_success(
    mock_make_session, mock_get_user, mock_auth_user
):
    """Cancel scheduled LLM call succeeds."""
    mock_get_user.return_value = mock_auth_user
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_call = MagicMock()
    mock_call.can_be_cancelled.return_value = True
    mock_call.status = "pending"

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.first.return_value = mock_call

    result = await cancel_scheduled_llm_call.fn(scheduled_call_id="call-123")

    assert result["success"] is True
    assert "cancelled" in result["message"]
    assert mock_call.status == "cancelled"
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
@patch("memory.api.MCP.servers.schedule.make_session")
async def test_cancel_scheduled_llm_call_not_found_returns_error(
    mock_make_session, mock_get_user, mock_auth_user
):
    """Cancel non-existent scheduled call returns error."""
    mock_get_user.return_value = mock_auth_user
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.first.return_value = None

    result = await cancel_scheduled_llm_call.fn(scheduled_call_id="nonexistent")

    assert "error" in result
    assert result["error"] == "Scheduled call not found"


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
@patch("memory.api.MCP.servers.schedule.make_session")
async def test_cancel_scheduled_llm_call_cannot_cancel_returns_error(
    mock_make_session, mock_get_user, mock_auth_user
):
    """Cancel call that cannot be cancelled returns error."""
    mock_get_user.return_value = mock_auth_user
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_call = MagicMock()
    mock_call.can_be_cancelled.return_value = False
    mock_call.status = "completed"

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.first.return_value = mock_call

    result = await cancel_scheduled_llm_call.fn(scheduled_call_id="call-123")

    assert "error" in result
    assert "Cannot cancel call" in result["error"]


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.schedule.get_current_user")
async def test_cancel_scheduled_llm_call_not_authenticated_returns_error(mock_get_user):
    """Cancel scheduled call without authentication returns error."""
    mock_get_user.return_value = {"authenticated": False}

    result = await cancel_scheduled_llm_call.fn(scheduled_call_id="call-123")

    assert "error" in result
    assert result["error"] == "Not authenticated"
