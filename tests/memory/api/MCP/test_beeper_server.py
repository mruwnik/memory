import pytest

from memory.api.MCP.servers.beeper import has_beeper_accounts, list_chats
from memory.common.db.models import BeeperAccount, BeeperChat, BeeperNetwork
from tests.conftest import mcp_auth_context


def add_chats(db_session, user, url: str, titles: list[str], network: str = "Slack") -> BeeperNetwork:
    account = BeeperAccount(created_by_user_id=user.id, server_url=url)
    net = BeeperNetwork(account=account, network_account_id=f"{network}.1", network=network, collect_messages=True)
    db_session.add_all([account, net, *(BeeperChat(network=net, chat_id=f"!{t}:{url}", title=t) for t in titles)])
    db_session.commit()
    return net


@pytest.fixture
def two_owners(db_session, regular_user, user_session, admin_user):
    mine = add_chats(db_session, regular_user, "http://mine:1", ["general", "random"])
    add_chats(db_session, admin_user, "http://theirs:1", ["secret plans"], network="WhatsApp")
    return mine


def titles(result: dict) -> list[str]:
    return sorted(chat["title"] for chat in result["chats"])


@pytest.mark.asyncio
async def test_list_chats_shows_only_the_callers_chats(two_owners, user_session):
    with mcp_auth_context(user_session.token):
        result = await list_chats.fn()

    assert titles(result) == ["general", "random"]
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_admins_see_only_their_own_chats(two_owners, admin_session):
    with mcp_auth_context(admin_session.token):
        result = await list_chats.fn()

    assert titles(result) == ["secret plans"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters, expected",
    [
        ({"name": "GEN"}, ["general"]),
        ({"network": "slack"}, ["general", "random"]),
        ({"network": "whatsapp"}, []),
        ({"name": "secret"}, []),
    ],
)
async def test_list_chats_filters(two_owners, user_session, filters, expected):
    with mcp_auth_context(user_session.token):
        result = await list_chats.fn(**filters)

    assert titles(result) == expected


@pytest.mark.asyncio
async def test_list_chats_collected_only_follows_overrides(db_session, two_owners, user_session):
    db_session.query(BeeperChat).filter_by(title="random").one().collect_messages = False
    db_session.commit()

    with mcp_auth_context(user_session.token):
        result = await list_chats.fn(collected_only=True, fields=["title", "collect_messages", "effective_collect"])

    assert result["chats"] == [{"title": "general", "collect_messages": None, "effective_collect": True}]


@pytest.mark.asyncio
async def test_the_tool_shows_to_users_with_beeper_accounts(db_session, regular_user, user_session):
    add_chats(db_session, regular_user, "http://mine:1", [])

    assert await has_beeper_accounts({"token": user_session.token}, None) is True


@pytest.mark.asyncio
async def test_the_tool_is_hidden_from_users_without_beeper_accounts(db_session, user_session, admin_user):
    add_chats(db_session, admin_user, "http://theirs:1", [])

    assert await has_beeper_accounts({"token": user_session.token}, None) is False
