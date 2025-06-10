import logging
from collections import defaultdict
from typing import Annotated, TypedDict, get_args, get_type_hints

from memory.common import qdrant
from sqlalchemy import func

from memory.api.MCP.tools import mcp
from memory.common.db.connection import make_session
from memory.common.db.models import SourceItem
from memory.common.db.models.source_items import AgentObservation

logger = logging.getLogger(__name__)


class SchemaArg(TypedDict):
    type: str | None
    description: str | None


class CollectionMetadata(TypedDict):
    schema: dict[str, SchemaArg]
    size: int


def from_annotation(annotation: Annotated) -> SchemaArg | None:
    try:
        type_, description = get_args(annotation)
        type_str = str(type_)
        if type_str.startswith("typing."):
            type_str = type_str[7:]
        elif len((parts := type_str.split("'"))) > 1:
            type_str = parts[1]
        return SchemaArg(type=type_str, description=description)
    except IndexError:
        logger.error(f"Error from annotation: {annotation}")
        return None


def get_schema(klass: type[SourceItem]) -> dict[str, SchemaArg]:
    if not hasattr(klass, "as_payload"):
        return {}

    if not (payload_type := get_type_hints(klass.as_payload).get("return")):
        return {}

    return {
        name: schema
        for name, arg in payload_type.__annotations__.items()
        if (schema := from_annotation(arg))
    }


@mcp.tool()
async def get_metadata_schemas() -> dict[str, CollectionMetadata]:
    """Get the metadata schema for each collection used in the knowledge base.

    These schemas can be used to filter the knowledge base.

    Returns: A mapping of collection names to their metadata schemas with field types and descriptions.

    Example:
    ```
    {
        "mail": {"subject": {"type": "str", "description": "The subject of the email."}},
        "chat": {"subject": {"type": "str", "description": "The subject of the chat message."}}
    }
    """
    client = qdrant.get_qdrant_client()
    sizes = qdrant.get_collection_sizes(client)
    schemas = defaultdict(dict)
    for klass in SourceItem.__subclasses__():
        for collection in klass.get_collections():
            schemas[collection].update(get_schema(klass))

    return {
        collection: CollectionMetadata(schema=schema, size=size)
        for collection, schema in schemas.items()
        if (size := sizes.get(collection))
    }


@mcp.tool()
async def get_all_tags() -> list[str]:
    """Get all unique tags used across the entire knowledge base.

    Returns sorted list of tags from both observations and content.
    """
    with make_session() as session:
        tags_query = session.query(func.unnest(SourceItem.tags)).distinct()
        return sorted({row[0] for row in tags_query if row[0] is not None})


@mcp.tool()
async def get_all_subjects() -> list[str]:
    """Get all unique subjects from observations about the user.

    Returns sorted list of subject identifiers used in observations.
    """
    with make_session() as session:
        return sorted(
            r.subject for r in session.query(AgentObservation.subject).distinct()
        )


@mcp.tool()
async def get_all_observation_types() -> list[str]:
    """Get all observation types that have been used.

    Standard types are belief, preference, behavior, contradiction, general, but there can be more.
    """
    with make_session() as session:
        return sorted(
            {
                r.observation_type
                for r in session.query(AgentObservation.observation_type).distinct()
                if r.observation_type is not None
            }
        )
