# pyright: reportFunctionMemberAccess=false
"""Beeper messages say which network and chat they came from, and filter by them."""

import pytest

from memory.api.MCP.servers.core import count_items, list_items
from memory.api.search.bm25 import run_bm25_query
from memory.api.search.embeddings import search_chunks
from memory.api.search.types import SearchFilters
from memory.beeper import ingest
from memory.common import beeper, extract
from memory.common.db.models import BeeperChat, BeeperNetwork, Chunk
from memory.common.db.models.source_items import BeeperMessage
from tests.conftest import mcp_auth_context

TEXT = "are we still on for the family dinner on saturday"


def add_chat(db_session, account, network: str, title: str, chat_type: str = "group") -> BeeperChat:
    net = BeeperNetwork(
        account=account, network_account_id=f"{network.lower()}.1", network=network,
        display_name=f"{network} - +44 7700 900000", collect_messages=True,
    )
    chat = BeeperChat(network=net, chat_id=f"!{title}:{network}", title=title, chat_type=chat_type)
    db_session.add_all([net, chat])
    db_session.commit()
    return chat


def store(db_session, chat: BeeperChat, message_id: str, sender: str = "Alice", text: str = TEXT) -> BeeperMessage:
    message = beeper.Message.model_validate({
        "id": message_id, "chatID": chat.chat_id, "accountID": chat.network.network_account_id,
        "senderID": f"@{sender.lower()}:beeper.local", "senderName": sender,
        "timestamp": "2026-11-05T10:00:00.000Z", "sortKey": message_id, "text": text,
    })
    ingest.store_message(db_session, chat, message, [])
    return db_session.query(BeeperMessage).filter_by(chat_id=chat.id, message_id=message_id).one()


@pytest.fixture
def chats(db_session, beeper_account, qdrant):
    beeper_account.name = "Personal"
    db_session.commit()
    family = add_chat(db_session, beeper_account, "WhatsApp", "Family")
    signal = add_chat(db_session, beeper_account, "Signal", "Bob", chat_type="single")
    return {
        "whatsapp": store(db_session, family, "w1"),
        "whatsapp_bob": store(db_session, family, "w2", sender="Bob"),
        "signal": store(db_session, signal, "s1", sender="Bob"),
    }


def test_a_message_names_its_network_chat_and_sender(chats):
    message = chats["whatsapp"]

    assert message.title == f"[WhatsApp] Family — Alice: {TEXT}"
    assert {k: v for k, v in message.as_payload().items() if k in BEEPER_KEYS} == {
        "network": "whatsapp",
        "network_name": "WhatsApp",
        "network_account": "WhatsApp - +44 7700 900000",
        "account_name": "Personal",
        "chat_title": "Family",
        "chat_type": "group",
        "sender_name": "Alice",
        "beeper_chat_id": message.chat_id,
    }
    assert {chunk.content for chunk in message.chunks} == {f"[WhatsApp] Family — Alice: {TEXT}"}


BEEPER_KEYS = {
    "network", "network_name", "network_account", "account_name", "chat_title", "chat_type", "sender_name",
    "beeper_chat_id",
}


def source_ids(db_session, chunk_ids) -> set[int]:
    return {c.source_id for c in db_session.query(Chunk).filter(Chunk.id.in_(list(chunk_ids)))}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters, expected",
    [
        ({"network": "whatsapp"}, {"whatsapp", "whatsapp_bob"}),
        ({"network": "Signal"}, {"signal"}),
        ({"chat": "family"}, {"whatsapp", "whatsapp_bob"}),
        ({"network": "signal", "chat": "Family"}, set()),
        ({"network": "telegram"}, set()),
    ],
)
async def test_search_filters_by_network_and_chat(db_session, chats, filters, expected):
    wanted = {chats[name].id for name in expected}
    query = extract.extract_text(TEXT, skip_summary=True)

    admin_filters = SearchFilters(**filters, access_filter=None)

    vector_hits = await search_chunks(query, {"message"}, limit=20, min_score=0, filters=admin_filters)
    bm25_hits = run_bm25_query("family dinner saturday", {"message"}, 20, admin_filters)

    assert source_ids(db_session, vector_hits) == wanted
    assert source_ids(db_session, bm25_hits) == wanted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"filters": {"network": "whatsapp"}}, {"whatsapp", "whatsapp_bob"}),
        ({"channel": "Family"}, {"whatsapp", "whatsapp_bob"}),
        ({"author": "bob"}, {"whatsapp_bob", "signal"}),
        ({"filters": {"network": "signal"}, "author": "Bob"}, {"signal"}),
    ],
)
async def test_list_and_count_filter_by_network_chat_and_sender(db_session, chats, admin_session, kwargs, expected):
    with mcp_auth_context(admin_session.token):
        listed = await list_items.fn(**kwargs)
        counted = await count_items.fn(**kwargs)

    assert {item["id"] for item in listed["items"]} == {chats[name].id for name in expected}
    assert counted["total"] == len(expected)
