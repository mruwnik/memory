"""Tests for MCP polling server."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from memory.api.MCP.servers.polling import (
    upsert_poll,
    list_polls,
    delete_poll,
    get_poll,
    parse_datetime,
    get_current_user_id,
)


# ====== Helper function tests ======


def test_parse_datetime_valid_iso_format():
    """Parse valid ISO datetime string."""
    result = parse_datetime("2026-01-15T09:00:00Z")
    assert result == datetime(2026, 1, 15, 9, 0, 0, tzinfo=timezone.utc)


def test_parse_datetime_with_timezone():
    """Parse datetime string with explicit timezone."""
    result = parse_datetime("2026-01-15T09:00:00+00:00")
    assert result == datetime(2026, 1, 15, 9, 0, 0, tzinfo=timezone.utc)


def test_parse_datetime_none_returns_none():
    """Parse datetime with None input returns None."""
    result = parse_datetime(None)
    assert result is None


def test_parse_datetime_invalid_format_raises():
    """Parse datetime with invalid format raises ValueError."""
    with pytest.raises(ValueError, match="Invalid datetime format"):
        parse_datetime("not a datetime")


@patch("memory.api.MCP.servers.polling.get_access_token")
@patch("memory.api.MCP.servers.polling.make_session")
def test_get_current_user_id_valid_session(mock_make_session, mock_get_token):
    """Get current user ID from valid session."""
    mock_token = MagicMock()
    mock_token.token = "session-123"
    mock_get_token.return_value = mock_token

    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_user_session = MagicMock()
    mock_user_session.user.id = 42
    mock_session.get.return_value = mock_user_session

    result = get_current_user_id()

    assert result == 42


@patch("memory.api.MCP.servers.polling.get_access_token")
def test_get_current_user_id_no_token_raises(mock_get_token):
    """Get current user ID without token raises ValueError."""
    mock_get_token.return_value = None

    with pytest.raises(ValueError, match="Not authenticated - no access token"):
        get_current_user_id()


@patch("memory.api.MCP.servers.polling.get_access_token")
@patch("memory.api.MCP.servers.polling.make_session")
def test_get_current_user_id_invalid_session_raises(mock_make_session, mock_get_token):
    """Get current user ID with invalid session raises ValueError."""
    mock_token = MagicMock()
    mock_token.token = "invalid-session"
    mock_get_token.return_value = mock_token

    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session
    mock_session.get.return_value = None

    with pytest.raises(ValueError, match="Not authenticated - invalid session"):
        get_current_user_id()


# ====== upsert_poll tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
@patch("memory.api.MCP.servers.polling.poll_to_payload")
async def test_upsert_poll_create_new(mock_poll_to_payload, mock_make_session, mock_get_user_id):
    """Create new poll with required fields."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    # Setup mock poll with slug attribute
    mock_poll = MagicMock()
    mock_poll.slug = "abc123"
    mock_session.refresh = MagicMock(side_effect=lambda poll: setattr(poll, 'slug', 'abc123'))

    mock_payload = MagicMock()
    mock_payload.model_dump.return_value = {
        "id": 1,
        "slug": "abc123",
        "title": "Team Meeting",
    }
    mock_poll_to_payload.return_value = mock_payload

    result = await upsert_poll.fn(
        title="Team Meeting",
        datetime_start="2026-01-20T09:00:00Z",
        datetime_end="2026-01-20T17:00:00Z",
    )

    assert result["title"] == "Team Meeting"
    assert "/ui/polls/respond/" in result["share_url"]
    assert "/ui/polls/results/" in result["results_url"]
    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
async def test_upsert_poll_create_missing_title_raises(mock_get_user_id):
    """Create poll without title raises ValueError."""
    mock_get_user_id.return_value = 1

    with pytest.raises(ValueError, match="title, datetime_start, and datetime_end required"):
        await upsert_poll.fn(
            datetime_start="2026-01-20T09:00:00Z",
            datetime_end="2026-01-20T17:00:00Z",
        )


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
async def test_upsert_poll_update_existing(mock_make_session, mock_get_user_id):
    """Update existing poll."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_poll = MagicMock()
    mock_poll.user_id = 1
    mock_poll.title = "Old Title"
    # Set datetime attributes to avoid comparison errors
    mock_poll.datetime_start = datetime(2026, 1, 20, 9, 0, 0, tzinfo=timezone.utc)
    mock_poll.datetime_end = datetime(2026, 1, 20, 17, 0, 0, tzinfo=timezone.utc)
    mock_poll.slot_duration_minutes = 30
    mock_poll.slug = "abc123"
    mock_session.get.return_value = mock_poll

    with patch("memory.api.MCP.servers.polling.poll_to_payload") as mock_payload:
        mock_payload.return_value.model_dump.return_value = {"id": 1, "slug": "abc123"}
        await upsert_poll.fn(poll_id=1, title="Updated Title")

    assert mock_poll.title == "Updated Title"
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
async def test_upsert_poll_update_not_owned_raises(mock_make_session, mock_get_user_id):
    """Update poll not owned by user raises ValueError."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_poll = MagicMock()
    mock_poll.user_id = 999  # Different user
    mock_session.get.return_value = mock_poll

    with pytest.raises(ValueError, match="Poll 1 not found"):
        await upsert_poll.fn(poll_id=1, title="Hacked Title")


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
async def test_upsert_poll_invalid_datetime_range_raises(mock_make_session, mock_get_user_id):
    """Create poll with start after end raises ValueError."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    with pytest.raises(ValueError, match="datetime_start must be before datetime_end"):
        await upsert_poll.fn(
            title="Bad Poll",
            datetime_start="2026-01-20T17:00:00Z",
            datetime_end="2026-01-20T09:00:00Z",
        )


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
async def test_upsert_poll_duration_too_short_raises(mock_make_session, mock_get_user_id):
    """Create poll with duration shorter than slot raises ValueError."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    with pytest.raises(ValueError, match="Poll duration .* must be at least one slot duration"):
        await upsert_poll.fn(
            title="Too Short",
            datetime_start="2026-01-20T09:00:00Z",
            datetime_end="2026-01-20T09:20:00Z",  # 20 minutes
            slot_duration=30,  # Need at least 30 minutes
        )


@pytest.mark.parametrize(
    "status,expected_status",
    [
        ("open", "open"),
        ("closed", "closed"),
        ("cancelled", "cancelled"),
    ],
)
@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
@patch("memory.api.MCP.servers.polling.poll_to_payload")
async def test_upsert_poll_status_changes(
    mock_poll_to_payload, mock_make_session, mock_get_user_id, status, expected_status
):
    """Update poll status."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_poll = MagicMock()
    mock_poll.user_id = 1
    # Set datetime attributes to avoid comparison errors
    mock_poll.datetime_start = datetime(2026, 1, 20, 9, 0, 0, tzinfo=timezone.utc)
    mock_poll.datetime_end = datetime(2026, 1, 20, 17, 0, 0, tzinfo=timezone.utc)
    mock_poll.slot_duration_minutes = 30
    mock_poll.slug = "abc123"
    mock_session.get.return_value = mock_poll

    mock_poll_to_payload.return_value.model_dump.return_value = {"id": 1, "slug": "abc123"}

    await upsert_poll.fn(poll_id=1, status=status)

    assert mock_poll.status == expected_status


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
@patch("memory.api.MCP.servers.polling.poll_to_payload")
async def test_upsert_poll_finalize_sets_time(mock_poll_to_payload, mock_make_session, mock_get_user_id):
    """Finalize poll sets finalized_at and finalized_time."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_poll = MagicMock()
    mock_poll.user_id = 1
    # Set datetime attributes to avoid comparison errors
    mock_poll.datetime_start = datetime(2026, 1, 20, 9, 0, 0, tzinfo=timezone.utc)
    mock_poll.datetime_end = datetime(2026, 1, 20, 17, 0, 0, tzinfo=timezone.utc)
    mock_poll.slot_duration_minutes = 30
    mock_poll.slug = "abc123"
    mock_session.get.return_value = mock_poll

    mock_poll_to_payload.return_value.model_dump.return_value = {"id": 1, "slug": "abc123"}

    await upsert_poll.fn(
        poll_id=1,
        status="finalized",
        finalized_time="2026-01-20T14:00:00Z"
    )

    assert mock_poll.status == "finalized"
    assert mock_poll.finalized_at is not None
    assert mock_poll.finalized_time is not None


# ====== list_polls tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
@patch("memory.api.MCP.servers.polling.poll_to_payload")
async def test_list_polls_returns_user_polls(mock_poll_to_payload, mock_make_session, mock_get_user_id):
    """List polls returns only user's polls."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_poll1 = MagicMock()
    mock_poll2 = MagicMock()

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = [mock_poll1, mock_poll2]

    mock_poll_to_payload.side_effect = [
        MagicMock(model_dump=lambda: {"id": 1, "title": "Poll 1"}),
        MagicMock(model_dump=lambda: {"id": 2, "title": "Poll 2"}),
    ]

    result = await list_polls.fn()

    assert len(result) == 2
    assert result[0]["title"] == "Poll 1"
    assert result[1]["title"] == "Poll 2"


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
async def test_list_polls_filters_by_status(mock_make_session, mock_get_user_id):
    """List polls filters by status."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_polls.fn(status="open")

    # Should have two filter calls: user_id and status
    assert query_mock.filter.call_count == 2


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
async def test_list_polls_pagination(mock_make_session, mock_get_user_id):
    """List polls supports pagination."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_polls.fn(limit=10, offset=20)

    query_mock.offset.assert_called_once_with(20)
    query_mock.limit.assert_called_once_with(10)


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
async def test_list_polls_enforces_max_limit(mock_make_session, mock_get_user_id):
    """List polls enforces max limit of 200."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_polls.fn(limit=500)

    # Should cap at 200
    query_mock.limit.assert_called_once_with(200)


# ====== delete_poll tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
async def test_delete_poll_success(mock_make_session, mock_get_user_id):
    """Delete poll succeeds for owned poll."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_poll = MagicMock()
    mock_poll.user_id = 1
    mock_poll.title = "My Poll"
    mock_session.get.return_value = mock_poll

    result = await delete_poll.fn(poll_id=1)

    assert result["deleted"] is True
    assert result["poll_id"] == 1
    assert result["title"] == "My Poll"
    mock_session.delete.assert_called_once_with(mock_poll)
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
async def test_delete_poll_not_owned_raises(mock_make_session, mock_get_user_id):
    """Delete poll not owned raises ValueError."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_poll = MagicMock()
    mock_poll.user_id = 999  # Different user
    mock_session.get.return_value = mock_poll

    with pytest.raises(ValueError, match="Poll 1 not found"):
        await delete_poll.fn(poll_id=1)


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
async def test_delete_poll_not_found_raises(mock_make_session, mock_get_user_id):
    """Delete non-existent poll raises ValueError."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session
    mock_session.get.return_value = None

    with pytest.raises(ValueError, match="Poll 999 not found"):
        await delete_poll.fn(poll_id=999)


# ====== get_poll tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
@patch("memory.api.MCP.servers.polling.poll_to_payload")
@patch("memory.api.MCP.servers.polling.aggregate_availability")
async def test_get_poll_by_id(
    mock_aggregate, mock_poll_to_payload, mock_make_session, mock_get_user_id
):
    """Get poll by ID returns poll with aggregated results."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_poll = MagicMock()
    mock_poll.user_id = 1
    mock_poll.response_count = 3
    mock_session.get.return_value = mock_poll

    mock_poll_to_payload.return_value.model_dump.return_value = {"id": 1, "title": "Test Poll"}

    mock_slot1 = MagicMock()
    mock_slot1.available_count = 3
    mock_slot1.model_dump.return_value = {"time": "09:00", "available": 3}
    mock_slot2 = MagicMock()
    mock_slot2.available_count = 1
    mock_slot2.model_dump.return_value = {"time": "10:00", "available": 1}
    mock_aggregate.return_value = [mock_slot1, mock_slot2]

    result = await get_poll.fn(poll_id=1)

    assert result["poll"]["title"] == "Test Poll"
    assert result["response_count"] == 3
    assert len(result["aggregated"]) == 2
    assert len(result["best_slots"]) == 1
    assert result["best_slots"][0]["available"] == 3


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
@patch("memory.api.MCP.servers.polling.poll_to_payload")
@patch("memory.api.MCP.servers.polling.aggregate_availability")
async def test_get_poll_by_slug(
    mock_aggregate, mock_poll_to_payload, mock_make_session, mock_get_user_id
):
    """Get poll by slug returns poll (public access)."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_poll = MagicMock()
    mock_poll.response_count = 0

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.first.return_value = mock_poll

    mock_poll_to_payload.return_value.model_dump.return_value = {"slug": "abc123"}
    mock_aggregate.return_value = []

    result = await get_poll.fn(slug="abc123")

    assert result["poll"]["slug"] == "abc123"
    assert result["best_slots"] == []


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
async def test_get_poll_no_identifier_raises(mock_get_user_id):
    """Get poll without ID or slug raises ValueError."""
    mock_get_user_id.return_value = 1

    with pytest.raises(ValueError, match="Must provide either poll_id or slug"):
        await get_poll.fn()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
async def test_get_poll_both_identifiers_raises(mock_get_user_id):
    """Get poll with both ID and slug raises ValueError."""
    mock_get_user_id.return_value = 1

    with pytest.raises(ValueError, match="Cannot provide both poll_id and slug"):
        await get_poll.fn(poll_id=1, slug="abc123")


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
async def test_get_poll_by_id_not_owned_raises(mock_make_session, mock_get_user_id):
    """Get poll by ID not owned raises ValueError."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_poll = MagicMock()
    mock_poll.user_id = 999  # Different user
    mock_session.get.return_value = mock_poll

    with pytest.raises(ValueError, match="Poll 1 not found"):
        await get_poll.fn(poll_id=1)


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.polling.get_current_user_id")
@patch("memory.api.MCP.servers.polling.make_session")
async def test_get_poll_by_slug_not_found_raises(mock_make_session, mock_get_user_id):
    """Get poll by non-existent slug raises ValueError."""
    mock_get_user_id.return_value = 1
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.first.return_value = None

    with pytest.raises(ValueError, match="Poll with slug 'nonexistent' not found"):
        await get_poll.fn(slug="nonexistent")
