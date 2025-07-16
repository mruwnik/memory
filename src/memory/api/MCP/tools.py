"""
MCP tools for the epistemic sparring partner system.
"""

import logging
from datetime import datetime, timezone

from mcp.server.auth.middleware.auth_context import get_access_token
from sqlalchemy import Text
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import ARRAY

from memory.common.db.connection import make_session
from memory.common.db.models import (
    AgentObservation,
    SourceItem,
    UserSession,
)
from memory.api.MCP.base import mcp

logger = logging.getLogger(__name__)


def filter_observation_source_ids(
    tags: list[str] | None = None, observation_types: list[str] | None = None
):
    if not tags and not observation_types:
        return None

    with make_session() as session:
        items_query = session.query(AgentObservation.id)

        if tags:
            # Use PostgreSQL array overlap operator with proper array casting
            items_query = items_query.filter(
                AgentObservation.tags.op("&&")(sql_cast(tags, ARRAY(Text))),
            )
        if observation_types:
            items_query = items_query.filter(
                AgentObservation.observation_type.in_(observation_types)
            )
        source_ids = [item.id for item in items_query.all()]

    return source_ids


def filter_source_ids(
    modalities: set[str],
    tags: list[str] | None = None,
):
    if not tags:
        return None

    with make_session() as session:
        items_query = session.query(SourceItem.id)

        if tags:
            # Use PostgreSQL array overlap operator with proper array casting
            items_query = items_query.filter(
                SourceItem.tags.op("&&")(sql_cast(tags, ARRAY(Text))),
            )
        if modalities:
            items_query = items_query.filter(SourceItem.modality.in_(modalities))
        source_ids = [item.id for item in items_query.all()]

    return source_ids


@mcp.tool()
async def get_current_time() -> dict:
    """Get the current time in UTC."""
    logger.info("get_current_time tool called")
    return {"current_time": datetime.now(timezone.utc).isoformat()}


@mcp.tool()
async def get_authenticated_user() -> dict:
    """Get information about the authenticated user."""
    logger.info("🔧 get_authenticated_user tool called")
    access_token = get_access_token()
    logger.info(f"🔧 Access token from MCP context: {access_token}")

    if not access_token:
        logger.warning("❌ No access token found in MCP context!")
        return {"error": "Not authenticated"}

    logger.info(
        f"🔧 MCP context token details - scopes: {access_token.scopes}, client_id: {access_token.client_id}, token: {access_token.token[:20]}..."
    )

    # Look up the actual user from the session token
    with make_session() as session:
        user_session = (
            session.query(UserSession)
            .filter(UserSession.id == access_token.token)
            .first()
        )

        if user_session and user_session.user:
            user_info = user_session.user.serialize()
        else:
            user_info = {"error": "User not found"}

    return {
        "authenticated": True,
        "token_type": "Bearer",
        "scopes": access_token.scopes,
        "client_id": access_token.client_id,
        "user": user_info,
    }


@mcp.tool()
async def send_response(response: str) -> dict:
    """Send a response to the user."""
    logger.info(f"Sending response: {response}")
    return {"response": response}
