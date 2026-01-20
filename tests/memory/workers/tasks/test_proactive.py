"""Tests for proactive check-in tasks."""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from memory.common.db.models import (
    DiscordBotUser,
    DiscordUser,
    DiscordChannel,
    DiscordServer,
)
from memory.workers.tasks import proactive
from memory.workers.tasks.proactive import is_cron_due


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def bot_user(db_session):
    """Create a bot user for testing."""
    bot_discord_user = DiscordUser(
        id=999999999,
        username="testbot",
    )
    db_session.add(bot_discord_user)
    db_session.flush()

    user = DiscordBotUser.create_with_api_key(
        discord_users=[bot_discord_user],
        name="testbot",
        email="bot@example.com",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def target_user(db_session):
    """Create a target Discord user for testing."""
    discord_user = DiscordUser(
        id=123456789,
        username="targetuser",
        proactive_cron="0 9 * * *",  # 9am daily
        chattiness_threshold=50,
    )
    db_session.add(discord_user)
    db_session.commit()
    return discord_user


@pytest.fixture
def target_user_no_cron(db_session):
    """Create a target Discord user without proactive cron."""
    discord_user = DiscordUser(
        id=123456790,
        username="nocronuser",
        proactive_cron=None,
    )
    db_session.add(discord_user)
    db_session.commit()
    return discord_user


@pytest.fixture
def target_server(db_session):
    """Create a target Discord server for testing."""
    server = DiscordServer(
        id=987654321,
        name="Test Server",
        proactive_cron="0 */4 * * *",  # Every 4 hours
        chattiness_threshold=30,
    )
    db_session.add(server)
    db_session.commit()
    return server


@pytest.fixture
def target_channel(db_session, target_server):
    """Create a target Discord channel for testing."""
    channel = DiscordChannel(
        id=111222333,
        name="test-channel",
        channel_type="text",
        server_id=target_server.id,
        proactive_cron="0 12 * * 1-5",  # Noon on weekdays
        chattiness_threshold=70,
    )
    db_session.add(channel)
    db_session.commit()
    return channel


# ============================================================================
# Tests for is_cron_due helper
# ============================================================================


@pytest.mark.parametrize(
    "cron_expr,now,last_run,expected",
    [
        # Cron is due when never run before and time matches
        (
            "0 9 * * *",
            datetime(2025, 12, 24, 9, 0, 30, tzinfo=timezone.utc),
            None,
            True,
        ),
        # Cron is due when last run was before the scheduled time
        (
            "0 9 * * *",
            datetime(2025, 12, 24, 9, 1, 0, tzinfo=timezone.utc),
            datetime(2025, 12, 23, 9, 0, 0, tzinfo=timezone.utc),
            True,
        ),
        # Cron is NOT due when already run this period
        (
            "0 9 * * *",
            datetime(2025, 12, 24, 9, 30, 0, tzinfo=timezone.utc),
            datetime(2025, 12, 24, 9, 5, 0, tzinfo=timezone.utc),
            False,
        ),
        # Cron is NOT due when current time is before scheduled time
        (
            "0 9 * * *",
            datetime(2025, 12, 24, 8, 0, 0, tzinfo=timezone.utc),
            None,
            False,
        ),
        # Hourly cron schedule
        (
            "0 * * * *",
            datetime(2025, 12, 24, 12, 0, 30, tzinfo=timezone.utc),
            datetime(2025, 12, 24, 11, 0, 0, tzinfo=timezone.utc),
            True,
        ),
        # Every 4 hours cron schedule
        (
            "0 */4 * * *",
            datetime(2025, 12, 24, 12, 0, 30, tzinfo=timezone.utc),
            datetime(2025, 12, 24, 8, 0, 0, tzinfo=timezone.utc),
            True,
        ),
    ],
    ids=[
        "due_never_run",
        "due_last_run_before_schedule",
        "not_due_already_run",
        "not_due_too_early",
        "due_hourly",
        "due_every_4_hours",
    ],
)
def test_is_cron_due(cron_expr, now, last_run, expected):
    """Test is_cron_due with various scenarios."""
    assert is_cron_due(cron_expr, last_run, now) is expected


def test_is_cron_due_invalid_expression():
    """Test invalid cron expression returns False."""
    now = datetime(2025, 12, 24, 9, 0, 0, tzinfo=timezone.utc)
    assert is_cron_due("invalid cron", None, now) is False


def test_is_cron_due_with_naive_last_run():
    """Test cron handles naive datetime for last_run."""
    now = datetime(2025, 12, 24, 9, 1, 0, tzinfo=timezone.utc)
    cron_expr = "0 9 * * *"
    last_run = datetime(2025, 12, 23, 9, 0, 0)  # Naive datetime
    assert is_cron_due(cron_expr, last_run, now) is True


# ============================================================================
# Tests for evaluate_proactive_checkins task
# ============================================================================


@patch("memory.workers.tasks.proactive.execute_proactive_checkin")
@patch("memory.workers.tasks.proactive.is_cron_due")
@patch("memory.workers.tasks.proactive.make_session")
def test_evaluate_proactive_checkins_dispatches_due(
    mock_make_session, mock_is_cron_due, mock_execute, db_session, target_user
):
    """Test that due check-ins are dispatched."""
    mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=False)
    mock_is_cron_due.return_value = True

    result = proactive.evaluate_proactive_checkins()

    assert result["count"] >= 1
    mock_execute.delay.assert_called()


@patch("memory.workers.tasks.proactive.execute_proactive_checkin")
@patch("memory.workers.tasks.proactive.is_cron_due")
@patch("memory.workers.tasks.proactive.make_session")
def test_evaluate_proactive_checkins_skips_not_due(
    mock_make_session, mock_is_cron_due, mock_execute, db_session, target_user
):
    """Test that not-due check-ins are not dispatched."""
    mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=False)
    mock_is_cron_due.return_value = False

    result = proactive.evaluate_proactive_checkins()

    assert result["count"] == 0
    mock_execute.delay.assert_not_called()


@patch("memory.workers.tasks.proactive.execute_proactive_checkin")
@patch("memory.workers.tasks.proactive.make_session")
def test_evaluate_proactive_checkins_skips_no_cron(
    mock_make_session, mock_execute, db_session, target_user_no_cron
):
    """Test that entities without proactive_cron are skipped."""
    mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=False)

    proactive.evaluate_proactive_checkins()

    for call in mock_execute.delay.call_args_list:
        entity_type, entity_id = call[0]
        assert entity_id != target_user_no_cron.id


@patch("memory.workers.tasks.proactive.execute_proactive_checkin")
@patch("memory.workers.tasks.proactive.is_cron_due")
@patch("memory.workers.tasks.proactive.make_session")
def test_evaluate_proactive_checkins_multiple_entity_types(
    mock_make_session,
    mock_is_cron_due,
    mock_execute,
    db_session,
    target_user,
    target_server,
    target_channel,
):
    """Test that check-ins are dispatched for users, channels, and servers."""
    mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=False)
    mock_is_cron_due.return_value = True

    result = proactive.evaluate_proactive_checkins()

    assert result["count"] == 3
    dispatched_types = {d["type"] for d in result["dispatched"]}
    assert "user" in dispatched_types
    assert "channel" in dispatched_types
    assert "server" in dispatched_types


# ============================================================================
# Tests for execute_proactive_checkin task
# ============================================================================


@patch("memory.workers.tasks.proactive.send_discord_response")
@patch("memory.workers.tasks.proactive.call_llm")
@patch("memory.workers.tasks.proactive.get_bot_for_entity")
@patch("memory.workers.tasks.proactive.make_session")
def test_execute_proactive_checkin_sends_when_above_threshold(
    mock_make_session,
    mock_get_bot,
    mock_call_llm,
    mock_send,
    db_session,
    target_user,
    bot_user,
):
    """Test check-in is sent when interest exceeds threshold."""
    mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=False)

    bot_discord_user = bot_user.discord_users[0]
    bot_discord_user.system_user = bot_user
    mock_get_bot.return_value = bot_discord_user

    mock_call_llm.side_effect = [
        "<response><number>80</number><reason>Should check in</reason></response>",
        "Hey! Just checking in - how are things going?",
    ]
    mock_send.return_value = True

    result = proactive.execute_proactive_checkin("user", target_user.id)

    assert result["status"] == "sent"
    assert result["interest"] == 80
    mock_send.assert_called_once()

    db_session.refresh(target_user)
    assert target_user.last_proactive_at is not None


@patch("memory.workers.tasks.proactive.call_llm")
@patch("memory.workers.tasks.proactive.get_bot_for_entity")
@patch("memory.workers.tasks.proactive.make_session")
def test_execute_proactive_checkin_skips_below_threshold(
    mock_make_session,
    mock_get_bot,
    mock_call_llm,
    db_session,
    target_user,
    bot_user,
):
    """Test check-in is skipped when interest is below threshold."""
    mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=False)

    bot_discord_user = bot_user.discord_users[0]
    bot_discord_user.system_user = bot_user
    mock_get_bot.return_value = bot_discord_user

    mock_call_llm.return_value = (
        "<response><number>30</number><reason>Not much to say</reason></response>"
    )

    result = proactive.execute_proactive_checkin("user", target_user.id)

    assert result["status"] == "below_threshold"
    assert result["interest"] == 30
    assert result["threshold"] == 50


@pytest.mark.parametrize(
    "llm_response,expected_status",
    [
        (None, "no_eval_response"),
        ("I'm not sure what to say.", "no_score_in_response"),
    ],
    ids=["no_response", "malformed_response"],
)
@patch("memory.workers.tasks.proactive.call_llm")
@patch("memory.workers.tasks.proactive.get_bot_for_entity")
@patch("memory.workers.tasks.proactive.make_session")
def test_execute_proactive_checkin_handles_bad_llm_response(
    mock_make_session,
    mock_get_bot,
    mock_call_llm,
    llm_response,
    expected_status,
    db_session,
    target_user,
    bot_user,
):
    """Test handling of missing or malformed LLM responses."""
    mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=False)

    bot_discord_user = bot_user.discord_users[0]
    bot_discord_user.system_user = bot_user
    mock_get_bot.return_value = bot_discord_user
    mock_call_llm.return_value = llm_response

    result = proactive.execute_proactive_checkin("user", target_user.id)

    assert result["status"] == expected_status


@patch("memory.workers.tasks.proactive.make_session")
def test_execute_proactive_checkin_nonexistent_entity(mock_make_session, db_session):
    """Test handling when entity doesn't exist."""
    mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=False)

    result = proactive.execute_proactive_checkin("user", 999999)

    assert "error" in result
    assert "not found" in result["error"]


@patch("memory.workers.tasks.proactive.get_bot_for_entity")
@patch("memory.workers.tasks.proactive.make_session")
def test_execute_proactive_checkin_no_bot_user(
    mock_make_session, mock_get_bot, db_session, target_user
):
    """Test handling when no bot user is found."""
    mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=False)
    mock_get_bot.return_value = None

    result = proactive.execute_proactive_checkin("user", target_user.id)

    assert "error" in result
    assert "No bot user" in result["error"]


@patch("memory.workers.tasks.proactive.send_discord_response")
@patch("memory.workers.tasks.proactive.call_llm")
@patch("memory.workers.tasks.proactive.get_bot_for_entity")
@patch("memory.workers.tasks.proactive.make_session")
def test_execute_proactive_checkin_uses_proactive_prompt(
    mock_make_session,
    mock_get_bot,
    mock_call_llm,
    mock_send,
    db_session,
    bot_user,
):
    """Test that proactive_prompt is included in the evaluation."""
    mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=False)

    user_with_prompt = DiscordUser(
        id=555666777,
        username="promptuser",
        proactive_cron="0 9 * * *",
        proactive_prompt="Focus on their coding projects",
        chattiness_threshold=50,
    )
    db_session.add(user_with_prompt)
    db_session.commit()

    bot_discord_user = bot_user.discord_users[0]
    bot_discord_user.system_user = bot_user
    mock_get_bot.return_value = bot_discord_user

    mock_call_llm.side_effect = [
        "<response><number>80</number><reason>Check on projects</reason></response>",
        "How are your coding projects coming along?",
    ]
    mock_send.return_value = True

    result = proactive.execute_proactive_checkin("user", user_with_prompt.id)

    assert result["status"] == "sent"
    call_args = mock_call_llm.call_args_list[0]
    messages_arg = call_args.kwargs.get("messages") or call_args[1].get("messages")
    assert any("Focus on their coding projects" in str(m) for m in messages_arg)


@patch("memory.workers.tasks.proactive.send_discord_response")
@patch("memory.workers.tasks.proactive.call_llm")
@patch("memory.workers.tasks.proactive.get_bot_for_entity")
@patch("memory.workers.tasks.proactive.make_session")
def test_execute_proactive_checkin_channel(
    mock_make_session,
    mock_get_bot,
    mock_call_llm,
    mock_send,
    db_session,
    target_channel,
    bot_user,
):
    """Test check-in to a channel."""
    mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=False)

    bot_discord_user = bot_user.discord_users[0]
    bot_discord_user.system_user = bot_user
    mock_get_bot.return_value = bot_discord_user

    mock_call_llm.side_effect = [
        "<response><number>50</number><reason>Check channel</reason></response>",
        "Good morning everyone!",
    ]
    mock_send.return_value = True

    result = proactive.execute_proactive_checkin("channel", target_channel.id)

    assert result["status"] == "sent"
    assert result["entity_type"] == "channel"

    send_call = mock_send.call_args
    assert send_call.kwargs.get("channel_id") == target_channel.id


@patch("memory.workers.tasks.proactive.send_discord_response")
@patch("memory.workers.tasks.proactive.call_llm")
@patch("memory.workers.tasks.proactive.get_bot_for_entity")
@patch("memory.workers.tasks.proactive.make_session")
def test_execute_proactive_checkin_updates_last_proactive_at(
    mock_make_session,
    mock_get_bot,
    mock_call_llm,
    mock_send,
    db_session,
    target_user,
    bot_user,
):
    """Test that last_proactive_at is updated after successful check-in."""
    mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=False)

    bot_discord_user = bot_user.discord_users[0]
    bot_discord_user.system_user = bot_user
    mock_get_bot.return_value = bot_discord_user

    mock_call_llm.side_effect = [
        "<response><number>80</number><reason>Check in</reason></response>",
        "Hey there!",
    ]
    mock_send.return_value = True

    before_time = datetime.now(timezone.utc)
    proactive.execute_proactive_checkin("user", target_user.id)
    after_time = datetime.now(timezone.utc)

    db_session.refresh(target_user)
    assert target_user.last_proactive_at is not None
    assert before_time <= target_user.last_proactive_at <= after_time


@patch("memory.workers.tasks.proactive.call_llm")
@patch("memory.workers.tasks.proactive.get_bot_for_entity")
@patch("memory.workers.tasks.proactive.make_session")
def test_execute_proactive_checkin_updates_last_proactive_at_on_skip(
    mock_make_session,
    mock_get_bot,
    mock_call_llm,
    db_session,
    target_user,
    bot_user,
):
    """Test that last_proactive_at is updated even when check-in is skipped."""
    mock_make_session.return_value.__enter__ = Mock(return_value=db_session)
    mock_make_session.return_value.__exit__ = Mock(return_value=False)

    bot_discord_user = bot_user.discord_users[0]
    bot_discord_user.system_user = bot_user
    mock_get_bot.return_value = bot_discord_user

    mock_call_llm.return_value = (
        "<response><number>10</number><reason>Nothing to say</reason></response>"
    )

    proactive.execute_proactive_checkin("user", target_user.id)

    db_session.refresh(target_user)
    assert target_user.last_proactive_at is not None
