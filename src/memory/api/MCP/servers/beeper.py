"""MCP subserver for Beeper: read-only views of the caller's Beeper chats.

Messages are searched with core_search; nothing here sends.
"""

import asyncio
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import func
from sqlalchemy.orm import contains_eager

from memory.api.MCP.access import fetch_user_by_token, get_mcp_current_user
from memory.api.MCP.visibility import READ_ONLY, require_scopes, visible_when
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import BeeperAccount, BeeperChat, BeeperNetwork
from memory.common.scopes import SCOPE_BEEPER

beeper_mcp = FastMCP("memory-beeper")

DEFAULT_FIELDS = ["id", "title", "network", "type", "effective_collect"]


async def has_beeper_accounts(user_info: dict, session: DBSession | None) -> bool:
    token = user_info.get("token")
    if not token:
        return False

    def check() -> bool:
        with make_session() as local_session:
            user = fetch_user_by_token(local_session, token)
            if user is None:
                return False
            query = local_session.query(BeeperAccount.id).filter_by(created_by_user_id=user.id)
            return query.first() is not None

    return await asyncio.to_thread(check)


def chat_fields(chat: BeeperChat) -> dict[str, Any]:
    network = chat.network
    return {
        "id": chat.id,
        "chat_id": chat.chat_id,
        "title": chat.title,
        "network": network.network,
        "network_id": network.id,
        "account_id": network.account_id,
        "type": chat.chat_type,
        "is_merged": chat.is_merged,
        "merged_into": chat.merged_into,
        "collect_messages": chat.collect_messages,
        "effective_collect": chat.should_collect,
        "project_id": chat.project_id if chat.project_id is not None else network.project_id,
        "sensitivity": chat.sensitivity or network.sensitivity,
        "last_activity_at": chat.last_activity_at.isoformat() if chat.last_activity_at else None,
        "backfill_done": chat.backfill_done,
    }


def fetch_chats(
    network: str | None,
    name: str | None,
    collected_only: bool,
    fields: list[str] | None,
    limit: int,
) -> dict[str, Any]:
    with make_session() as session:
        caller = get_mcp_current_user(session)
        if caller is None or caller.id is None:
            raise ValueError("Not authenticated")
        query = (
            session.query(BeeperChat)
            .join(BeeperNetwork, BeeperChat.network_id == BeeperNetwork.id)
            .join(BeeperAccount, BeeperNetwork.account_id == BeeperAccount.id)
            .options(contains_eager(BeeperChat.network))
            .filter(BeeperAccount.created_by_user_id == caller.id)
            .order_by(BeeperChat.last_activity_at.desc().nulls_last(), BeeperChat.id)
        )
        if network:
            query = query.filter(BeeperNetwork.network.ilike(f"%{network}%"))
        if name:
            query = query.filter(BeeperChat.title.ilike(f"%{name}%"))
        if collected_only:
            query = query.filter(
                BeeperChat.is_merged.is_(False),
                func.coalesce(BeeperChat.collect_messages, BeeperNetwork.collect_messages).is_(True),
            )
        chats = [chat_fields(chat) for chat in query.limit(limit)]
    wanted = fields or DEFAULT_FIELDS
    return {"chats": [{k: chat[k] for k in wanted if k in chat} for chat in chats], "count": len(chats)}


@beeper_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_BEEPER), has_beeper_accounts)
async def list_chats(
    network: str | None = None,
    name: str | None = None,
    collected_only: bool = False,
    fields: list[str] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """
    List the chats of your Beeper accounts, most recently active first.
    Search their messages with core_search.

    Args:
        network: Case-insensitive substring of the network name (Slack, WhatsApp, ...)
        name: Case-insensitive substring of the chat title
        collected_only: Only chats whose messages are collected
        fields: Fields to include for each chat.
                Default: ["id", "title", "network", "type", "effective_collect"]
                Available fields:
                - id: memory's chat id
                - chat_id: Beeper's chat id
                - title: Chat title
                - network: Network name
                - network_id, account_id: memory's network and Beeper account ids
                - type: single (direct) or group
                - is_merged: A merged chat, whose member chats hold the messages
                - merged_into: Beeper id of the merged chat this one belongs to
                - collect_messages: The chat's own setting (null = inherit from network)
                - effective_collect: Whether messages are collected
                - project_id: Project for access control (the network's when the chat has none)
                - sensitivity: Sensitivity level (the network's when the chat has none)
                - last_activity_at: Time of the last activity (ISO 8601)
                - backfill_done: Whether its history has been read to the start
        limit: Maximum chats to return (default 100)

    Returns:
        Dict with the chats and their count
    """
    return await asyncio.to_thread(fetch_chats, network, name, collected_only, fields, max(1, min(limit, 500)))
