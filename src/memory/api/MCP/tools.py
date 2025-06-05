"""
MCP tools for the epistemic sparring partner system.
"""

import logging
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP
from sqlalchemy import Text
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import ARRAY

from memory.common.db.connection import make_session
from memory.common.db.models import AgentObservation, SourceItem

logger = logging.getLogger(__name__)

# Create MCP server instance
mcp = FastMCP("memory", stateless_http=True)


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
    return {"current_time": datetime.now(timezone.utc).isoformat()}
