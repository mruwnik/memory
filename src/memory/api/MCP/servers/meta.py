"""MCP subserver for metadata, utilities, and forecasting."""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated, Literal, NotRequired, TypedDict, get_args, get_type_hints

import aiohttp
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token

from memory.common import qdrant
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    APIKey,
    APIKeyType,
    EmailAccount,
    SourceItem,
    UserSession,
)

logger = logging.getLogger(__name__)

meta_mcp = FastMCP("memory-meta")


def _get_current_user(session: DBSession) -> dict:
    """Get the current authenticated user from the access token."""
    access_token = get_access_token()
    if not access_token:
        return {"authenticated": False}

    user_session = session.get(UserSession, access_token.token)
    if not user_session or not user_session.user:
        return {"authenticated": False, "error": "User not found"}

    user_info = user_session.user.serialize()

    # Add email accounts
    email_accounts = (
        session.query(EmailAccount)
        .filter(
            EmailAccount.user_id == user_session.user.id, EmailAccount.active.is_(True)
        )
        .all()
    )
    user_info["email_accounts"] = [
        {
            "email_address": a.email_address,
            "name": a.name,
            "account_type": a.account_type,
        }
        for a in email_accounts
    ]

    return {
        "authenticated": True,
        "token_type": "Bearer",
        "scopes": access_token.scopes,
        "client_id": access_token.client_id,
        "user": user_info,
        "public_key": user_session.user.ssh_public_key,
    }


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
async def get_current_time() -> dict:
    """Get the current time in UTC."""
    logger.info("get_current_time tool called")
    return {"current_time": datetime.now(timezone.utc).isoformat()}


@meta_mcp.tool()
async def get_user(generate_one_time_key: bool = False) -> dict:
    """Get information about the authenticated user.

    Args:
        generate_one_time_key: If True, generates a one-time API key for client operations.
                               The key will be deleted after first use.
    """
    with make_session() as session:
        result = _get_current_user(session)

        if generate_one_time_key and result.get("authenticated"):
            access_token = get_access_token()
            if access_token:
                user_session = session.get(UserSession, access_token.token)
                if user_session and user_session.user:
                    # Create a one-time API key
                    one_time_key = APIKey.create(
                        user_id=user_session.user.id,
                        key_type=APIKeyType.ONE_TIME,
                        name="MCP Client Operation",
                        is_one_time=True,
                    )
                    session.add(one_time_key)
                    session.commit()
                    result["one_time_key"] = one_time_key.key

        return result


# --- Forecasting tools ---


class BinaryProbs(TypedDict):
    prob: float


class MultiProbs(TypedDict):
    answerProbs: dict[str, float]


Probs = dict[str, BinaryProbs | MultiProbs]
OutcomeType = Literal["BINARY", "MULTIPLE_CHOICE"]
MarketSource = Literal["manifold", "polymarket", "kalshi"]


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


async def get_manifold_details(session: aiohttp.ClientSession, market_id: str):
    async with session.get(
        f"https://api.manifold.markets/v0/market/{market_id}"
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


async def format_manifold_market(session: aiohttp.ClientSession, market: Market):
    if market.get("outcomeType") != "BINARY":
        details = await get_manifold_details(session, market["id"])
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


async def search_manifold_markets(
    term: str, min_volume: int = 1000, binary: bool = False
) -> list[dict]:
    """Search Manifold Markets for prediction markets matching the term."""
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

        results = await asyncio.gather(
            *[
                format_manifold_market(session, market)
                for market in markets
                if market.get("volume", 0) >= min_volume
            ],
            return_exceptions=True,
        )
        # Filter out any failed market fetches
        return [{"source": "manifold", **r} for r in results if isinstance(r, dict)]


def parse_outcome_prices(outcome_prices: str | list) -> list:
    """Parse Polymarket outcome prices from string or list format."""
    if isinstance(outcome_prices, str):
        try:
            return json.loads(outcome_prices)
        except json.JSONDecodeError:
            return []
    return outcome_prices


async def search_polymarket_markets(
    term: str, min_volume: int = 1000, binary: bool = False
) -> list[dict]:
    """Search Polymarket for prediction markets matching the term.

    Uses the Gamma API public search endpoint.
    """
    async with aiohttp.ClientSession() as session:
        params: dict = {
            "q": term,
            "limit_per_type": 50,
            "events_status": "active",
        }
        async with session.get(
            "https://gamma-api.polymarket.com/public-search",
            params=params,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        events = data.get("events", [])
        results = []
        for event in events:
            volume = float(event.get("volume", 0) or 0)
            if volume < min_volume:
                continue

            # Events can have multiple markets (outcomes)
            markets = event.get("markets", [])

            # For binary events (single market with yes/no)
            if len(markets) == 1:
                market = markets[0]
                prices = parse_outcome_prices(market.get("outcomePrices", "[]"))
                prob = float(prices[0]) if prices else None

                results.append(
                    {
                        "source": "polymarket",
                        "id": market.get("id") or event.get("id"),
                        "question": event.get("title", ""),
                        "url": f"https://polymarket.com/event/{event.get('slug', '')}",
                        "volume": volume,
                        "probability": round(prob, 3) if prob else None,
                        "createdAt": event.get("startDate"),
                    }
                )
            elif not binary:
                # Multiple choice event - include all outcomes
                answers = {}
                for market in markets:
                    outcome = market.get("outcome", market.get("groupItemTitle", ""))
                    prices = parse_outcome_prices(market.get("outcomePrices", "[]"))
                    prob = float(prices[0]) if prices else 0
                    if outcome:
                        answers[outcome] = round(prob, 3)

                results.append(
                    {
                        "source": "polymarket",
                        "id": event.get("id"),
                        "question": event.get("title", ""),
                        "url": f"https://polymarket.com/event/{event.get('slug', '')}",
                        "volume": volume,
                        "answers": answers,
                        "createdAt": event.get("startDate"),
                    }
                )

        return results


def filter_kalshi_market(market: dict, term: str, min_volume: int) -> dict | None:
    """Filter and format a Kalshi market if it matches the search criteria.

    Args:
        market: Raw market data from Kalshi API.
        term: Search term (case-insensitive).
        min_volume: Minimum volume threshold.

    Returns:
        Formatted market dict if it matches criteria, None otherwise.
    """
    title = market.get("title", "")
    subtitle = market.get("subtitle", "")
    event_title = market.get("event_title", "")

    # Check if term matches title, subtitle, or event title
    searchable = f"{title} {subtitle} {event_title}".lower()
    if term.lower() not in searchable:
        return None

    volume = market.get("volume", 0) or 0
    if volume < min_volume:
        return None

    # Kalshi markets are binary (yes/no)
    # Prefer last_price (actual trade price) over yes_bid (current bid)
    # for more accurate probability in illiquid markets
    yes_price = market.get("last_price") or market.get("yes_bid") or 0
    # Kalshi prices are in cents (0-100)
    probability = yes_price / 100 if yes_price else None

    return {
        "source": "kalshi",
        "id": market.get("ticker"),
        "question": title,
        "url": f"https://kalshi.com/markets/{market.get('ticker', '')}",
        "volume": volume,
        "probability": round(probability, 3) if probability else None,
        "createdAt": market.get("open_time"),
    }


async def search_kalshi_markets(
    term: str, min_volume: int = 1000, binary: bool = False  # noqa: ARG001
) -> list[dict]:
    """Search Kalshi for prediction markets matching the term.

    Kalshi doesn't have a text search API, so we fetch open markets and filter locally.
    Note: The `binary` parameter is accepted for API consistency but ignored since
    all Kalshi markets are binary (yes/no) by design.
    """
    async with aiohttp.ClientSession() as session:
        all_markets: list[dict] = []
        cursor = None

        # Paginate through markets (up to a reasonable limit)
        for _ in range(5):  # Max 5 pages = 500 markets
            params: dict = {
                "status": "open",
                "limit": 100,
            }
            if cursor:
                params["cursor"] = cursor

            async with session.get(
                "https://api.elections.kalshi.com/trade-api/v2/markets",
                params=params,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

            markets = data.get("markets", [])
            all_markets.extend(markets)

            cursor = data.get("cursor")
            if not cursor or not markets:
                break

        # Filter markets using the helper function
        results = [
            result
            for market in all_markets
            if (result := filter_kalshi_market(market, term, min_volume)) is not None
        ]

        return results


async def search_markets(
    term: str,
    min_volume: int = 1000,
    binary: bool = False,
    sources: list[MarketSource] | None = None,
) -> list[dict]:
    """Search multiple prediction market sources.

    Args:
        term: Search query.
        min_volume: Minimum market volume to include.
        binary: Whether to only return binary (yes/no) markets.
        sources: List of sources to search. Defaults to all sources.

    Returns:
        Combined list of markets from all requested sources.
    """
    if sources is None:
        sources = ["manifold", "polymarket", "kalshi"]

    search_funcs = {
        "manifold": search_manifold_markets,
        "polymarket": search_polymarket_markets,
        "kalshi": search_kalshi_markets,
    }

    # Filter out invalid sources with a warning
    valid_sources = []
    for source in sources:
        if source in search_funcs:
            valid_sources.append(source)
        else:
            logger.warning("Unknown prediction market source: %s", source)

    if not valid_sources:
        return []

    tasks = [search_funcs[source](term, min_volume, binary) for source in valid_sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Flatten results, filtering out errors
    all_markets = []
    for source, result in zip(valid_sources, results):
        if isinstance(result, list):
            all_markets.extend(result)
        elif isinstance(result, Exception):
            logger.warning("Error fetching markets from %s: %s", source, result)

    return all_markets


@meta_mcp.tool()
async def get_forecasts(
    term: str,
    min_volume: int = 1000,
    binary: bool = False,
    sources: list[MarketSource] | None = None,
) -> list[dict]:
    """Get prediction market forecasts for a given term from multiple prediction markets.

    Args:
        term: The term to search for.
        min_volume: The minimum volume of the market, in units of that market (Mana for Manifold, USD for others).
        binary: Whether to only return binary markets.
        sources: List of prediction market sources to search. Options: "manifold", "polymarket", "kalshi".
                 Defaults to all three if not specified.
    """
    return await search_markets(term, min_volume, binary, sources)
