"""Tests for the Discord MCP server.

Focuses on the snowflake-as-string contract at the MCP boundary. Discord
snowflakes routinely exceed 2^53, so any code path that emits or accepts them
as JSON integers risks silent precision loss on JS-based clients.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from memory.api.MCP.servers.discord import (
    _get_user_and_bots,
    has_discord_bots,
    send_message,
)
from memory.api.MCP.servers.teams import team_to_dict
from memory.common.db import connection as db_connection
from memory.common.db.models import (
    APIKey,
    DiscordBot,
    HumanUser,
    Team,
    UserSession,
)

from tests.conftest import mcp_auth_context

# Discord snowflakes large enough to lose precision through JS Number (>2^53).
GUILD_ID = "1379887229916155914"
CHANNEL_ID = "1502068455027900446"
ROLE_ID = "1502068455027900447"
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


# =============================================================================
# API-key auth resolves the user (not only session tokens)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("token_kind", ["session", "api_key"])
async def test_has_discord_bots_resolves_both_token_kinds(
    db_session, admin_user_with_session, discord_bot, token_kind
):
    """API-key auth puts the raw key (not a UserSession id) in the token.

    The visibility checker must resolve it via ``fetch_user_by_token`` or every
    API-key/service account is wrongly hidden from the Discord tools.
    """
    user, session = admin_user_with_session
    if token_kind == "session":
        token = session.id
    else:
        api_key = APIKey.create(user_id=user.id, key_type="mcp")
        db_session.add(api_key)
        db_session.commit()
        token = api_key.key

    assert await has_discord_bots({"token": token}, None) is True


def test_get_user_and_bots_resolves_api_key(
    db_session, admin_user_with_session, discord_bot
):
    """The tool-body resolver must also accept API keys, else the tool fails
    after visibility passes."""
    user, _ = admin_user_with_session
    api_key = APIKey.create(user_id=user.id, key_type="mcp")
    db_session.add(api_key)
    db_session.commit()

    with mcp_auth_context(api_key.key, scopes=["*"]):
        user_id, bots = _get_user_and_bots(db_session)

    assert user_id == user.id
    assert [b.id for b in bots] == [int(BOT_ID)]


# =============================================================================
# Schema-level snowflake-as-string contract
# =============================================================================


@pytest.mark.parametrize(
    "tool,kwargs",
    [
        (send_message, {"message": "x", "channel_id": int(CHANNEL_ID)}),
        (send_message, {"message": "x", "bot_id": int(BOT_ID)}),
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
@pytest.mark.parametrize("param", ["channel_id", "user_id", "bot_id"])
async def test_send_message_schema_accepts_string_snowflake(param):
    """Snowflake params must be string-or-null only, no integer branch."""
    schema = send_message.parameters  # type: ignore[attr-defined]
    types = {entry["type"] for entry in schema["properties"][param]["anyOf"]}
    assert types == {"string", "null"}


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


