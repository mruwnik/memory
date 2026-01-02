"""MCP subserver for metadata, utilities, and forecasting."""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated, Literal, NotRequired, TypedDict, get_args, get_type_hints

import aiohttp
from fastmcp import FastMCP
from sqlalchemy import func

from memory.common import qdrant
from memory.common.db.connection import make_session
from memory.common.db.models import SourceItem
from memory.common.db.models.source_items import AgentObservation

logger = logging.getLogger(__name__)

meta_mcp = FastMCP("memory-meta")

# Auth provider will be injected at mount time
_get_current_user = None


def set_auth_provider(get_current_user_func):
    """Set the authentication provider function."""
    global _get_current_user
    _get_current_user = get_current_user_func


def get_current_user() -> dict:
    """Get the current authenticated user."""
    if _get_current_user is None:
        return {"authenticated": False, "error": "Auth provider not configured"}
    return _get_current_user()


# --- Metadata tools ---


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


@meta_mcp.tool()
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


@meta_mcp.tool()
async def get_all_tags() -> list[str]:
    """Get all unique tags used across the entire knowledge base.

    Returns sorted list of tags from both observations and content.
    """
    with make_session() as session:
        tags_query = session.query(func.unnest(SourceItem.tags)).distinct()
        return sorted({row[0] for row in tags_query if row[0] is not None})


@meta_mcp.tool(tags={"scope:observe"})
async def get_all_subjects() -> list[str]:
    """Get all unique subjects from observations about the user.

    Returns sorted list of subject identifiers used in observations.
    """
    with make_session() as session:
        return sorted(
            r.subject for r in session.query(AgentObservation.subject).distinct()
        )


@meta_mcp.tool(tags={"scope:observe"})
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


# --- Utility tools ---


@meta_mcp.tool()
async def get_current_time() -> dict:
    """Get the current time in UTC."""
    logger.info("get_current_time tool called")
    return {"current_time": datetime.now(timezone.utc).isoformat()}


@meta_mcp.tool()
async def get_authenticated_user() -> dict:
    """Get information about the authenticated user."""
    return get_current_user()


# --- Forecasting tools ---


class BinaryProbs(TypedDict):
    prob: float


class MultiProbs(TypedDict):
    answerProbs: dict[str, float]


Probs = dict[str, BinaryProbs | MultiProbs]
OutcomeType = Literal["BINARY", "MULTIPLE_CHOICE"]


class MarketAnswer(TypedDict):
    id: str
    text: str
    resolutionProbability: float


class MarketDetails(TypedDict):
    id: str
    createdTime: int
    question: str
    outcomeType: OutcomeType
    textDescription: str
    groupSlugs: list[str]
    volume: float
    isResolved: bool
    answers: list[MarketAnswer]


class Market(TypedDict):
    id: str
    url: str
    question: str
    volume: int
    createdTime: int
    outcomeType: OutcomeType
    createdAt: NotRequired[str]
    description: NotRequired[str]
    answers: NotRequired[dict[str, float]]
    probability: NotRequired[float]
    details: NotRequired[MarketDetails]


async def get_details(session: aiohttp.ClientSession, market_id: str):
    async with session.get(
        f"https://api.manifold.markets/v0/market/{market_id}"
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


async def format_market(session: aiohttp.ClientSession, market: Market):
    if market.get("outcomeType") != "BINARY":
        details = await get_details(session, market["id"])
        market["answers"] = {
            answer["text"]: round(
                answer.get("resolutionProbability") or answer.get("probability") or 0, 3
            )
            for answer in details["answers"]
        }
    if creationTime := market.get("createdTime"):
        market["createdAt"] = datetime.fromtimestamp(creationTime / 1000).isoformat()

    fields = [
        "id",
        "name",
        "url",
        "question",
        "volume",
        "createdAt",
        "details",
        "probability",
        "answers",
    ]
    return {k: v for k, v in market.items() if k in fields}


async def search_markets(term: str, min_volume: int = 1000, binary: bool = False):
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://api.manifold.markets/v0/search-markets",
            params={
                "term": term,
                "contractType": "BINARY" if binary else "ALL",
            },
        ) as resp:
            resp.raise_for_status()
            markets = await resp.json()

        return await asyncio.gather(
            *[
                format_market(session, market)
                for market in markets
                if market.get("volume", 0) >= min_volume
            ]
        )


@meta_mcp.tool()
async def get_forecasts(
    term: str, min_volume: int = 1000, binary: bool = False
) -> list[dict]:
    """Get prediction market forecasts for a given term.

    Args:
        term: The term to search for.
        min_volume: The minimum volume of the market, in units of that market, so Mana for Manifold.
        binary: Whether to only return binary markets.
    """
    return await search_markets(term, min_volume, binary)
