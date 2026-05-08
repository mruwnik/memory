"""Tests for the Discord MCP server.

Focuses on the snowflake-as-string contract at the MCP boundary and the
``upsert_channel -> set_perms`` round-trip that motivated the eager
``ensure_channel_record`` insert. Discord snowflakes routinely exceed 2^53,
so any code path that emits or accepts them as JSON integers risks silent
precision loss on JS-based clients.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from memory.api.MCP.servers.discord import (
    create_role,
    ensure_channel_record,
    perms,
    set_perms,
    upsert_channel,
)
from memory.api.MCP.servers.teams import team_to_dict
from memory.common.db import connection as db_connection
from memory.common.db.models import (
    DiscordBot,
    DiscordChannel,
    DiscordServer,
    HumanUser,
    Team,
    UserSession,
)

from tests.conftest import mcp_auth_context

# Discord snowflakes large enough to lose precision through JS Number (>2^53).
GUILD_ID = "1379887229916155914"
CHANNEL_ID_CREATED = "1502068455027900446"
ROLE_ID = "1502068455027900447"
USER_TARGET_ID = "697845811165528147"
BOT_ID = "1379887229916155914"


def get_fn(tool):
    """Extract underlying function from FunctionTool if wrapped."""
    return getattr(tool, "fn", tool)


@pytest.fixture(autouse=True)
def reset_db_cache():
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None
    db_connection._scoped_session = None


@pytest.fixture
def admin_user_with_session(db_session):
    """Admin user + active session, returned as (user, session)."""
    user = HumanUser(
        name="Admin",
        email="admin-discord@example.com",
        password_hash="bcrypt_hash_placeholder",
        scopes=["*"],
    )
    db_session.add(user)
    db_session.flush()

    session = UserSession(
        id="discord-test-session",
        user_id=user.id,
        expires_at=datetime.now() + timedelta(days=1),
    )
    db_session.add(session)
    db_session.commit()
    return user, session


@pytest.fixture
def discord_bot(db_session, admin_user_with_session):
    user, _ = admin_user_with_session
    bot = DiscordBot(id=int(BOT_ID), name="Test Bot", is_active=True)
    db_session.add(bot)
    user.discord_bots.append(bot)
    db_session.commit()
    return bot


@pytest.fixture
def discord_server(db_session, discord_bot):
    server = DiscordServer(id=int(GUILD_ID), name="Test Guild", bot_id=int(BOT_ID))
    db_session.add(server)
    db_session.commit()
    return server


# =============================================================================
# Schema-level snowflake-as-string contract
# =============================================================================


@pytest.mark.parametrize(
    "tool,kwargs",
    [
        (create_role, {"name": "x", "guild": int(GUILD_ID)}),
        (create_role, {"name": "x", "bot_id": int(BOT_ID)}),
    ],
)
@pytest.mark.asyncio
async def test_int_snowflake_rejected_at_mcp_boundary(tool, kwargs):
    """Pydantic must reject integer values for snowflake-typed params.

    Without this guard, JS-based MCP clients silently corrupt 19-digit ids
    via float64 round-tripping. The schema must declare ``string`` only.
    """
    with pytest.raises(ValidationError):
        await tool.run(kwargs)


@pytest.mark.asyncio
async def test_create_role_schema_accepts_string_snowflake():
    """The string form must round-trip cleanly through schema validation."""
    schema = create_role.parameters
    guild_schema = schema["properties"]["guild"]
    bot_id_schema = schema["properties"]["bot_id"]

    # Both should be string-or-null only, no integer branch.
    guild_types = {entry["type"] for entry in guild_schema["anyOf"]}
    bot_id_types = {entry["type"] for entry in bot_id_schema["anyOf"]}
    assert guild_types == {"string", "null"}
    assert bot_id_types == {"string", "null"}


# =============================================================================
# team_to_dict snowflake stringification
# =============================================================================


def test_team_to_dict_stringifies_discord_snowflakes(db_session):
    """``discord_role_id`` and ``discord_guild_id`` must come out as strings.

    Storing them in BigInteger DB columns is fine — the precision loss
    happens at the JSON boundary. ``team_to_dict`` is the response builder
    for every team-MCP tool, so this is the single most important place
    for the contract.
    """
    role_id = int(ROLE_ID)
    guild_id = int(GUILD_ID)
    team = Team(
        name="Snowflake Team",
        slug="snowflake-team",
        discord_role_id=role_id,
        discord_guild_id=guild_id,
    )
    db_session.add(team)
    db_session.commit()

    result = team_to_dict(team)

    assert result["discord_role_id"] == ROLE_ID
    assert result["discord_guild_id"] == GUILD_ID
    assert isinstance(result["discord_role_id"], str)
    assert isinstance(result["discord_guild_id"], str)


def test_team_to_dict_handles_null_discord_fields(db_session):
    team = Team(name="Plain Team", slug="plain-team")
    db_session.add(team)
    db_session.commit()

    result = team_to_dict(team)

    assert result["discord_role_id"] is None
    assert result["discord_guild_id"] is None


# =============================================================================
# ensure_channel_record helper
# =============================================================================


def test_ensure_channel_record_inserts_when_missing(db_session, discord_server):
    """Fresh channel ids should be inserted with the supplied metadata."""
    channel_id = int(CHANNEL_ID_CREATED)

    record = ensure_channel_record(
        db_session,
        channel_id=channel_id,
        name="hawk-linuxarena-sync",
        server_id=int(GUILD_ID),
        category_id=None,
    )
    db_session.commit()

    assert record.id == channel_id
    assert record.name == "hawk-linuxarena-sync"
    assert record.channel_type == "text"

    # Re-query confirms persistence.
    fresh = db_session.get(DiscordChannel, channel_id)
    assert fresh is not None
    assert fresh.name == "hawk-linuxarena-sync"


def test_ensure_channel_record_updates_name_and_category(db_session, discord_server):
    """A re-run with a new name / category should overwrite the old values."""
    channel_id = int(CHANNEL_ID_CREATED)

    db_session.add(
        DiscordChannel(
            id=channel_id,
            server_id=int(GUILD_ID),
            category_id=None,
            name="old-name",
            channel_type="text",
        )
    )
    db_session.commit()

    new_category = int(ROLE_ID)  # any unique snowflake-shaped int
    ensure_channel_record(
        db_session,
        channel_id=channel_id,
        name="new-name",
        server_id=int(GUILD_ID),
        category_id=new_category,
    )
    db_session.commit()

    fresh = db_session.get(DiscordChannel, channel_id)
    assert fresh.name == "new-name"
    assert fresh.category_id == new_category


def test_ensure_channel_record_preserves_existing_category_when_none(
    db_session, discord_server
):
    """``category_id=None`` means "no change", not "clear" — matches MCP semantics."""
    channel_id = int(CHANNEL_ID_CREATED)
    original_category = int(ROLE_ID)

    db_session.add(
        DiscordChannel(
            id=channel_id,
            server_id=int(GUILD_ID),
            category_id=original_category,
            name="x",
            channel_type="text",
        )
    )
    db_session.commit()

    ensure_channel_record(
        db_session,
        channel_id=channel_id,
        name="x",
        server_id=int(GUILD_ID),
        category_id=None,
    )
    db_session.commit()

    fresh = db_session.get(DiscordChannel, channel_id)
    assert fresh.category_id == original_category


# =============================================================================
# upsert_channel -> set_perms round trip (the bug Fix 2 was written to close)
# =============================================================================


@pytest.mark.asyncio
async def test_upsert_channel_then_set_perms_resolves_by_canonical_name(
    db_session, admin_user_with_session, discord_bot, discord_server
):
    """Regression test for the Fix 2 bug.

    Before the eager ``ensure_channel_record`` insert, calling
    ``set_perms(channel="<canonical-name>")`` immediately after
    ``upsert_channel`` returned "channel not found": the local
    ``discord_channels`` row only existed once a message had landed in the
    channel. This test exercises the round-trip with both call sites
    mocked at the bot-API boundary, asserting that ``set_perms`` finds the
    channel via name lookup.

    Also catches the user-name-vs-canonical-name regression: Discord
    normalizes channel names (lowercase / hyphenated). If the stored row
    used the user-supplied ``name``, the human-readable canonical form
    would miss.
    """
    _, session = admin_user_with_session
    user_supplied_name = "Hawk LinuxArena Sync"
    canonical_name = "hawk-linuxarena-sync"

    upsert_response = {
        "success": True,
        "action": "created",
        "channel": {"id": CHANNEL_ID_CREATED, "name": canonical_name},
    }
    set_perms_response = {"success": True}

    with (
        mcp_auth_context(session.id),
        patch(
            "memory.common.discord.upsert_channel", return_value=upsert_response
        ),
        patch(
            "memory.common.discord.set_channel_permission",
            return_value=set_perms_response,
        ),
    ):
        # ``topic`` forces the Discord-API path (without it, upsert_channel
        # treats the call as a local-only update of project_id/sensitivity).
        upsert_result = await get_fn(upsert_channel)(
            name=user_supplied_name,
            guild=GUILD_ID,
            topic="test topic",
        )
        assert upsert_result["success"] is True

        # Second tool call, fresh session — must resolve the channel via the
        # canonical name even though the caller never persisted anything
        # itself.
        set_result = await get_fn(set_perms)(
            channel=canonical_name,
            role=ROLE_ID,
            allow=["view_channel"],
        )

    assert set_result["success"] is True

    # Sanity-check the persisted row used Discord's canonical name, not the
    # user-supplied one.
    db_session.expire_all()
    record = db_session.get(DiscordChannel, int(CHANNEL_ID_CREATED))
    assert record is not None
    assert record.name == canonical_name


@pytest.mark.asyncio
async def test_upsert_channel_then_perms_returns_string_target_ids(
    db_session, admin_user_with_session, discord_bot, discord_server
):
    """``perms`` must return ``target_id`` as a string.

    The bot-side ``get_channel_permissions`` route now stringifies; this
    test checks the contract holds end-to-end through the MCP layer.
    """
    _, session = admin_user_with_session
    canonical_name = "perms-test-channel"

    upsert_response = {
        "success": True,
        "action": "created",
        "channel": {"id": CHANNEL_ID_CREATED, "name": canonical_name},
    }
    perms_response = {
        "channel": canonical_name,
        "overwrites": [
            {
                "target_type": "role",
                "target_id": USER_TARGET_ID,  # bot API now returns str
                "target_name": "some-role",
                "allow": ["view_channel"],
                "deny": [],
            }
        ],
    }

    with (
        mcp_auth_context(session.id),
        patch(
            "memory.common.discord.upsert_channel", return_value=upsert_response
        ),
        patch(
            "memory.common.discord.get_channel_permissions",
            return_value=perms_response,
        ),
    ):
        await get_fn(upsert_channel)(
            name=canonical_name, guild=GUILD_ID, topic="t"
        )
        result = await get_fn(perms)(channel_name=canonical_name)

    overwrite = result["overwrites"][0]
    assert isinstance(overwrite["target_id"], str)
    assert overwrite["target_id"] == USER_TARGET_ID
