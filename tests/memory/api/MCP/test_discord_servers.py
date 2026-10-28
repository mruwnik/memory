"""Tests for the Discord MCP server.

Focuses on the snowflake-as-string contract at the MCP boundary. Discord
snowflakes routinely exceed 2^53, so any code path that emits or accepts them
as JSON integers risks silent precision loss on JS-based clients.
"""
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from memory.api.MCP.servers.discord import (
    _get_user_and_bots,
    channel_history,
    has_discord_bots,
    list_channels,
    send_message,
)
from memory.common.db import connection as db_connection
from memory.common.db.models import (
    APIKey,
    DiscordBot,
    DiscordChannel,
    DiscordServer,
    HumanUser,
    UserSession,
)

from tests.conftest import mcp_auth_context

# Discord snowflakes large enough to lose precision through JS Number (>2^53).
CHANNEL_ID = "1502068455027900446"
BOT_ID = "1379887229916155914"


def get_fn(tool):
    """Extract underlying function from FunctionTool if wrapped."""
    return getattr(tool, "fn", tool)


@pytest.fixture(autouse=True)
def reset_db_cache():
    db_connection._engine = None
    db_connection._session_factory = None
    yield
    db_connection._engine = None
    db_connection._session_factory = None


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
        token="discord-test-session",
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
        token = session.token
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
# Channel visibility: only servers the caller's own bots are in
# =============================================================================


@pytest.fixture
def two_owners(db_session, regular_user, user_session):
    """Two users, each owning a bot in its own server with two channels."""
    other = HumanUser(
        name="Other",
        email="other-discord@example.com",
        password_hash="bcrypt_hash_placeholder",
        scopes=["discord"],
    )
    db_session.add(other)
    db_session.flush()
    for owner, bot_id, server_id, prefix in [
        (regular_user, 101, 1001, "mine"),
        (other, 102, 1002, "theirs"),
    ]:
        bot = DiscordBot(id=bot_id, name=f"{prefix} bot", is_active=True)
        owner.discord_bots.append(bot)
        db_session.add(bot)
        db_session.flush()
        db_session.add(DiscordServer(id=server_id, name=f"{prefix} server", bot_id=bot_id))
        db_session.flush()
        db_session.add_all(
            [
                DiscordChannel(id=server_id * 10 + 1, server_id=server_id, name=f"{prefix}-general", channel_type="text"),
                DiscordChannel(id=server_id * 10 + 2, server_id=server_id, name=f"{prefix}-Random", channel_type="text"),
            ]
        )
    db_session.add(DiscordChannel(id=9999, name="some dm", channel_type="dm"))
    db_session.commit()
    return user_session


def channel_names(result):
    return sorted(ch["name"] for ch in result["channels"])


@pytest.mark.asyncio
async def test_list_channels_hides_other_users_servers(two_owners):
    with mcp_auth_context(two_owners.token):
        result = await get_fn(list_channels)(include_dms=True)

    assert channel_names(result) == ["mine-Random", "mine-general"]


@pytest.mark.asyncio
async def test_list_channels_admin_sees_all(two_owners, admin_user_with_session):
    _, session = admin_user_with_session
    with mcp_auth_context(session.token):
        result = await get_fn(list_channels)(include_dms=True)

    assert channel_names(result) == [
        "mine-Random", "mine-general", "some dm", "theirs-Random", "theirs-general",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("server", ["theirs server", "1002", "no such server", "424242"])
async def test_list_channels_server_filter_does_not_reveal_other_servers(two_owners, server):
    with mcp_auth_context(two_owners.token):
        result = await get_fn(list_channels)(server=server)

    assert result == {"channels": [], "count": 0, "error": f"Server '{server}' not found"}


@pytest.mark.asyncio
@pytest.mark.parametrize("server", ["mine server", "1001"])
async def test_list_channels_server_filter_own_server(two_owners, server):
    with mcp_auth_context(two_owners.token):
        result = await get_fn(list_channels)(server=server)

    assert channel_names(result) == ["mine-Random", "mine-general"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,expected",
    [("random", ["mine-Random"]), ("GEN", ["mine-general"]), ("theirs", [])],
)
async def test_list_channels_name_filter(two_owners, name, expected):
    with mcp_auth_context(two_owners.token):
        result = await get_fn(list_channels)(name=name)

    assert channel_names(result) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields,expected",
    [
        (None, {"id", "name", "type", "server_id"}),
        (["name", "sensitivity"], {"name", "sensitivity"}),
    ],
)
async def test_list_channels_fields(two_owners, fields, expected):
    with mcp_auth_context(two_owners.token):
        result = await get_fn(list_channels)(fields=fields)

    assert {frozenset(ch) for ch in result["channels"]} == {frozenset(expected)}


@pytest.mark.asyncio
async def test_list_channels_rejects_unknown_fields(two_owners):
    with mcp_auth_context(two_owners.token):
        with pytest.raises(ValueError, match="Invalid fields"):
            await get_fn(list_channels)(fields=["id", "bogus"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [{"channel_id": "10021"}, {"channel_name": "theirs-general"}, {"channel_id": "9999"}],
)
async def test_channel_history_rejects_inaccessible_channel(two_owners, kwargs):
    with mcp_auth_context(two_owners.token):
        with pytest.raises(ValueError, match="not found or not accessible"):
            await get_fn(channel_history)(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"channel_id": "10011"}, {"channel_name": "mine-general"}])
async def test_channel_history_own_channel(two_owners, kwargs):
    with mcp_auth_context(two_owners.token):
        result = await get_fn(channel_history)(**kwargs)

    assert result["channel"] == {"id": "10011", "name": "mine-general"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [{"channel_id": "10021"}, {"channel_name": "theirs-general"}, {"channel_id": "424242"}],
)
async def test_send_message_rejects_inaccessible_channel(two_owners, kwargs):
    with mcp_auth_context(two_owners.token):
        with pytest.raises(ValueError, match="not found or not accessible"):
            await get_fn(send_message)(message="hi", **kwargs)
