"""MCP subserver for metadata, utilities, and forecasting."""

import asyncio
import json
import logging
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal, NotRequired, TypedDict, get_args, get_type_hints

import aiohttp
from cachetools import TTLCache
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
    WatchedMarket,
)

# --- Caching infrastructure ---
# TTLCache is not thread-safe, so we use locks for concurrent access

import threading

# Search results cache: 5 minute TTL
_search_cache: TTLCache[str, list[dict]] = TTLCache(maxsize=500, ttl=300)
_search_cache_lock = threading.Lock()
# Market details/history cache: 10 minute TTL
_history_cache: TTLCache[str, dict] = TTLCache(maxsize=100, ttl=600)
_history_cache_lock = threading.Lock()
# Market depth cache: 1 minute TTL (more volatile)
_depth_cache: TTLCache[str, dict] = TTLCache(maxsize=100, ttl=60)
_depth_cache_lock = threading.Lock()


def _cache_key(*args: str) -> str:
    """Generate a cache key from arguments."""
    return ":".join(str(a) for a in args)


def clear_all_caches() -> dict:
    """Clear all prediction market caches.

    Useful for debugging or when you know data has changed.
    """
    with _search_cache_lock:
        _search_cache.clear()
    with _history_cache_lock:
        _history_cache.clear()
    with _depth_cache_lock:
        _depth_cache.clear()
    return {"cleared": True}


logger = logging.getLogger(__name__)

meta_mcp = FastMCP("memory-meta")


def _get_user_session_from_token(session: DBSession) -> UserSession | None:
    """Get the UserSession from the current access token.

    Returns None if no token or user session found.
    """
    access_token = get_access_token()
    if not access_token:
        return None

    user_session = session.get(UserSession, access_token.token)
    if not user_session or not user_session.user:
        return None
    return user_session


def _create_one_time_key(session: DBSession, user_session: UserSession) -> str:
    """Create a one-time API key for the user.

    Returns the key string (only available at creation time).
    """
    one_time_key = APIKey.create(
        user_id=user_session.user.id,
        key_type=APIKeyType.ONE_TIME,
        name="MCP Client Operation",
    )
    session.add(one_time_key)
    session.commit()
    return one_time_key.key


def _get_current_user(session: DBSession) -> dict:
    """Get the current authenticated user from the access token."""
    user_session = _get_user_session_from_token(session)
    if not user_session:
        access_token = get_access_token()
        if not access_token:
            return {"authenticated": False}
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

    access_token = get_access_token()
    return {
        "authenticated": True,
        "token_type": "Bearer",
        "scopes": access_token.scopes if access_token else [],
        "client_id": access_token.client_id if access_token else None,
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
            if user_session := _get_user_session_from_token(session):
                result["one_time_key"] = _create_one_time_key(session, user_session)

        return result


# --- Forecasting tools ---


class BinaryProbs(TypedDict):
    prob: float


class MultiProbs(TypedDict):
    answerProbs: dict[str, float]


Probs = dict[str, BinaryProbs | MultiProbs]
OutcomeType = Literal["BINARY", "MULTIPLE_CHOICE"]
MarketSource = Literal["manifold", "polymarket", "kalshi"]


def calculate_liquidity_score(
    volume: float, created_at: str | int | None, spread: float | None = None
) -> float:
    """Calculate a normalized liquidity score (0-1) for a market.

    Higher scores indicate more trustworthy prices.
    Factors:
    - Volume per day since market opened (capped at reasonable threshold)
    - Spread (bid-ask spread, if available)
    """
    if volume <= 0:
        return 0.0

    # Calculate days since market opened
    days_open = 1.0  # default to 1 day if unknown
    if created_at:
        try:
            if isinstance(created_at, int):
                # Unix timestamp in milliseconds
                created_time = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)
            else:
                # ISO string
                created_time = datetime.fromisoformat(
                    created_at.replace("Z", "+00:00")
                )
            days_open = max(1.0, (datetime.now(timezone.utc) - created_time).days)
        except (ValueError, TypeError, OSError):
            pass

    # Volume per day, normalized
    # $10k/day is considered very liquid (score ~1.0)
    volume_per_day = volume / days_open
    volume_score = min(1.0, volume_per_day / 10000)

    # If we have spread info, factor it in (lower spread = better)
    if spread is not None:
        # 0.01 spread (1%) is excellent, 0.20 (20%) is poor
        spread_score = max(0.0, 1.0 - spread / 0.20)
        return (volume_score * 0.7 + spread_score * 0.3)

    return volume_score


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

    # Calculate liquidity score
    volume = market.get("volume", 0) or 0
    created_at = market.get("createdTime")
    liquidity_score = calculate_liquidity_score(volume, created_at)

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
    result = {k: v for k, v in market.items() if k in fields}
    result["liquidity_score"] = round(liquidity_score, 3)
    return result


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

            # Calculate liquidity score
            created_at = event.get("startDate")
            liquidity_score = round(calculate_liquidity_score(volume, created_at), 3)

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
                        "createdAt": created_at,
                        "liquidity_score": liquidity_score,
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
                        "createdAt": created_at,
                        "liquidity_score": liquidity_score,
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

    # Calculate spread from yes_bid and yes_ask (or no_bid and no_ask)
    yes_bid = market.get("yes_bid", 0) or 0
    yes_ask = market.get("yes_ask", 0) or 0
    spread = None
    if yes_bid > 0 and yes_ask > 0:
        spread = (yes_ask - yes_bid) / 100  # Convert cents to fraction

    # Calculate liquidity score
    created_at = market.get("open_time")
    liquidity_score = calculate_liquidity_score(volume, created_at, spread)

    result = {
        "source": "kalshi",
        "id": market.get("ticker"),
        "question": title,
        "url": f"https://kalshi.com/markets/{market.get('ticker', '')}",
        "volume": volume,
        "probability": round(probability, 3) if probability else None,
        "createdAt": created_at,
        "liquidity_score": round(liquidity_score, 3),
    }

    # Include spread for Kalshi (since they have order book data)
    if spread is not None:
        result["spread"] = round(spread, 3)

    return result


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

    Returns:
        List of markets with: id, question, url, volume, probability, createdAt, liquidity_score.
        Kalshi markets also include spread (bid-ask spread).
    """
    # Check cache first
    cache_key = _cache_key("search", term, str(min_volume), str(binary), str(sources))
    with _search_cache_lock:
        if cache_key in _search_cache:
            return _search_cache[cache_key]

    results = await search_markets(term, min_volume, binary, sources)
    with _search_cache_lock:
        _search_cache[cache_key] = results
    return results


@meta_mcp.tool()
async def clear_forecast_cache() -> dict:
    """Clear all cached forecast data.

    Useful for debugging or when you need fresh data immediately.
    Clears search results, market history, and order book depth caches.

    Returns:
        Dict confirming caches were cleared.
    """
    return clear_all_caches()


# --- Market History ---


async def get_manifold_history(
    market_id: str, days: int = 7
) -> list[dict]:
    """Get price history for a Manifold market by aggregating bets."""
    async with aiohttp.ClientSession() as session:
        # Verify market exists
        async with session.get(
            f"https://api.manifold.markets/v0/market/{market_id}"
        ) as resp:
            if resp.status != 200:
                return []
            await resp.json()  # Consume response to verify market exists

        # Get bets for this market
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_ts = int(cutoff.timestamp() * 1000)

        async with session.get(
            "https://api.manifold.markets/v0/bets",
            params={
                "contractId": market_id,
                "afterTime": cutoff_ts,
                "limit": 1000,
            },
        ) as resp:
            if resp.status != 200:
                return []
            bets = await resp.json()

        # Aggregate bets by day
        daily_probs: dict[str, list[float]] = {}
        daily_volumes: dict[str, float] = {}

        for bet in bets:
            ts = bet.get("createdTime", 0)
            day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            prob_after = bet.get("probAfter", 0)
            amount = abs(bet.get("amount", 0))

            if day not in daily_probs:
                daily_probs[day] = []
                daily_volumes[day] = 0

            daily_probs[day].append(prob_after)
            daily_volumes[day] += amount

        # Build history list
        history = []
        for day in sorted(daily_probs.keys()):
            probs = daily_probs[day]
            avg_prob = sum(probs) / len(probs) if probs else 0
            history.append({
                "timestamp": f"{day}T00:00:00Z",
                "probability": round(avg_prob, 3),
                "volume": round(daily_volumes[day], 2),
            })

        return history


async def get_kalshi_history(
    ticker: str, period: Literal["1d", "7d", "30d", "all"] = "7d"
) -> list[dict]:
    """Get price history for a Kalshi market using candlesticks API."""
    period_map = {
        "1d": ("minute", 60 * 24),  # 1 day of minute candles
        "7d": ("hour", 24 * 7),  # 7 days of hourly candles
        "30d": ("day", 30),  # 30 days of daily candles
        "all": ("day", 365),  # up to a year
    }
    resolution, limit = period_map.get(period, ("hour", 168))

    async with aiohttp.ClientSession() as session:
        # Verify market exists
        async with session.get(
            f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}"
        ) as resp:
            if resp.status != 200:
                return []
            await resp.json()  # Consume response to verify market exists

        # Get candlesticks
        async with session.get(
            f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}/candlesticks",
            params={
                "period_interval": resolution,
                "limit": limit,
            },
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()

        candlesticks = data.get("candlesticks", [])
        history = []

        for candle in candlesticks:
            # Kalshi prices are in cents (0-100)
            close_price = candle.get("close", 0) or candle.get("yes_price", 0)
            prob = close_price / 100 if close_price else None
            vol = candle.get("volume", 0)

            history.append({
                "timestamp": candle.get("end_period_ts") or candle.get("ts"),
                "probability": round(prob, 3) if prob else None,
                "volume": vol,
            })

        return history


async def get_polymarket_history(
    market_id: str, days: int = 7
) -> list[dict]:
    """Get price history for a Polymarket market.

    Uses the Polymarket CLOB API for historical prices.

    Note: This endpoint may not be publicly available or may require
    authentication. If history cannot be fetched, returns an empty list.
    Polymarket price history is less reliable than Manifold or Kalshi.
    """
    async with aiohttp.ClientSession() as session:
        # Try to get history from Polymarket's CLOB timeseries endpoint
        # Note: This endpoint may require different parameters
        async with session.get(
            "https://clob.polymarket.com/prices-history",
            params={
                "market": market_id,
                "interval": "1d" if days > 7 else "1h",
                "fidelity": min(days, 30),
            },
        ) as resp:
            if resp.status != 200:
                # Fallback: return empty history
                logger.warning(
                    "Could not fetch Polymarket history for %s: %s",
                    market_id, resp.status
                )
                return []
            data = await resp.json()

        history = []
        for point in data.get("history", []):
            history.append({
                "timestamp": point.get("t"),
                "probability": round(float(point.get("p", 0)), 3),
                "volume": point.get("v", 0),
            })

        return history


@meta_mcp.tool()
async def get_market_history(
    market_id: str,
    source: MarketSource,
    period: Literal["1d", "7d", "30d", "all"] = "7d",
) -> dict:
    """Get price history for a specific prediction market.

    Args:
        market_id: The market identifier (ticker for Kalshi, contract ID for others).
        source: The prediction market source ("manifold", "polymarket", or "kalshi").
        period: Time period for history ("1d", "7d", "30d", or "all").

    Returns:
        Dict with market_id, source, history (list of timestamp/probability/volume),
        current price, and price changes (24h, 7d).

    Note:
        Polymarket history may not be available (returns empty history if
        the endpoint is inaccessible). Manifold and Kalshi are more reliable.
    """
    cache_key = _cache_key("history", market_id, source, period)
    with _history_cache_lock:
        if cache_key in _history_cache:
            return _history_cache[cache_key]

    days_map = {"1d": 1, "7d": 7, "30d": 30, "all": 365}
    days = days_map.get(period, 7)

    history: list[dict] = []
    question = ""

    if source == "manifold":
        history = await get_manifold_history(market_id, days)
        # Get current question
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.manifold.markets/v0/market/{market_id}"
            ) as resp:
                if resp.status == 200:
                    market = await resp.json()
                    question = market.get("question", "")
    elif source == "kalshi":
        history = await get_kalshi_history(market_id, period)
        # Get current question
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.elections.kalshi.com/trade-api/v2/markets/{market_id}"
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    question = data.get("market", {}).get("title", "")
    elif source == "polymarket":
        history = await get_polymarket_history(market_id, days)

    # Calculate current and changes
    current = None
    change_24h = None
    change_7d = None

    if history:
        current = history[-1].get("probability") if history else None

        # Find prices from 24h and 7d ago
        now = datetime.now(timezone.utc)
        for point in reversed(history):
            ts = point.get("timestamp")
            if not ts:
                continue
            try:
                if isinstance(ts, str):
                    pt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                else:
                    pt = datetime.fromtimestamp(ts, tz=timezone.utc)

                age = (now - pt).total_seconds() / 3600  # hours
                if change_24h is None and age >= 24 and current is not None:
                    old_prob = point.get("probability")
                    if old_prob is not None:
                        change_24h = round(current - old_prob, 3)
                if change_7d is None and age >= 168 and current is not None:  # 7*24
                    old_prob = point.get("probability")
                    if old_prob is not None:
                        change_7d = round(current - old_prob, 3)
            except (ValueError, TypeError, OSError):
                continue

    result = {
        "market_id": market_id,
        "source": source,
        "question": question,
        "history": history,
        "current": current,
        "change_24h": change_24h,
        "change_7d": change_7d,
    }

    with _history_cache_lock:
        _history_cache[cache_key] = result
    return result


# --- Market Depth (Order Book) ---


@meta_mcp.tool()
async def get_market_depth(
    market_id: str,
    source: MarketSource = "kalshi",
) -> dict:
    """Get order book depth for a prediction market.

    Currently only Kalshi is supported (has public order book API).
    Manifold uses an AMM (no order book). Polymarket's CLOB requires auth.

    Args:
        market_id: The market ticker (e.g., "TRUMP-WIN-2024").
        source: The prediction market source. Only "kalshi" is currently supported.

    Returns:
        Dict with order book data: yes_bids, no_bids, spread, midpoint, depth_at_1pct.
    """
    if source != "kalshi":
        return {
            "error": f"Order book not available for {source}. Only Kalshi is supported.",
            "market_id": market_id,
            "source": source,
        }

    cache_key = _cache_key("depth", market_id, source)
    with _depth_cache_lock:
        if cache_key in _depth_cache:
            return _depth_cache[cache_key]

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://api.elections.kalshi.com/trade-api/v2/markets/{market_id}/orderbook"
        ) as resp:
            if resp.status != 200:
                return {
                    "error": f"Could not fetch order book: HTTP {resp.status}",
                    "market_id": market_id,
                    "source": source,
                }
            data = await resp.json()

        orderbook = data.get("orderbook", {})

        # Parse yes and no sides
        yes_bids = [
            {"price": float(level[0]) / 100, "quantity": int(level[1])}
            for level in orderbook.get("yes", [])
        ]
        no_bids = [
            {"price": float(level[0]) / 100, "quantity": int(level[1])}
            for level in orderbook.get("no", [])
        ]

        # Calculate spread and midpoint
        spread = None
        midpoint = None
        if yes_bids and no_bids:
            best_yes_bid = yes_bids[0]["price"]
            best_no_bid = no_bids[0]["price"]
            # Yes bid + No bid should equal ~$1 in an efficient market
            # Spread is the gap
            implied_yes_ask = 1 - best_no_bid
            spread = round(implied_yes_ask - best_yes_bid, 3)
            midpoint = round((best_yes_bid + implied_yes_ask) / 2, 3)

        # Calculate depth within 1% of midpoint
        depth_at_1pct = 0
        if midpoint is not None:
            lower = midpoint - 0.01
            upper = midpoint + 0.01
            for bid in yes_bids:
                if lower <= bid["price"] <= upper:
                    depth_at_1pct += bid["quantity"]
            for bid in no_bids:
                implied_yes = 1 - bid["price"]
                if lower <= implied_yes <= upper:
                    depth_at_1pct += bid["quantity"]

        result = {
            "market_id": market_id,
            "source": source,
            "yes_bids": yes_bids[:10],  # Top 10 levels
            "no_bids": no_bids[:10],
            "spread": spread,
            "midpoint": midpoint,
            "depth_at_1pct": depth_at_1pct,
        }

        with _depth_cache_lock:
            _depth_cache[cache_key] = result
        return result


# --- Compare Forecasts ---


@meta_mcp.tool()
async def compare_forecasts(
    term: str,
    min_volume: int = 1000,
) -> dict:
    """Compare forecasts across prediction market platforms for the same topic.

    This tool helps identify when platforms disagree on probabilities,
    which can be informative about uncertainty or market inefficiency.

    Args:
        term: Search term to find related markets across platforms.
        min_volume: Minimum volume threshold for markets to include.

    Returns:
        Dict with:
        - term: The search term
        - markets: List of all matching markets grouped by similarity
        - consensus: Volume-weighted average probability (for binary markets)
        - disagreement: Standard deviation of probabilities
        - arbitrage_opportunities: Markets with significant price gaps
    """
    # Check cache first
    cache_key = _cache_key("compare", term, str(min_volume))
    with _search_cache_lock:
        if cache_key in _search_cache:
            markets = _search_cache[cache_key]
        else:
            markets = None
    if markets is None:
        markets = await search_markets(term, min_volume, binary=True)
        with _search_cache_lock:
            _search_cache[cache_key] = markets

    if not markets:
        return {
            "term": term,
            "markets": [],
            "consensus": None,
            "disagreement": None,
            "arbitrage_opportunities": [],
        }

    # Extract probabilities and volumes for consensus calculation
    probs_with_volume = [
        (m.get("probability"), m.get("volume", 0))
        for m in markets
        if m.get("probability") is not None
    ]

    consensus = None
    disagreement = None

    if probs_with_volume:
        probs: list[float] = [p for p, _ in probs_with_volume if p is not None]
        volumes = [v for _, v in probs_with_volume]
        total_volume = sum(volumes)

        if total_volume > 0:
            # Volume-weighted average
            consensus = round(
                sum(p * v for p, v in probs_with_volume if p is not None) / total_volume, 3
            )

        if len(probs) > 1:
            # Standard deviation
            disagreement = round(statistics.stdev(probs), 3)

    # Find arbitrage opportunities (>5% price difference between platforms)
    arbitrage_opportunities = []
    by_source = {}
    for m in markets:
        source = m.get("source")
        if source not in by_source:
            by_source[source] = []
        by_source[source].append(m)

    # Early exit once we have enough opportunities (limit O(n^4) impact)
    MAX_OPPORTUNITIES = 20  # Collect up to 20, return top 10
    sources = list(by_source.keys())
    for i, s1 in enumerate(sources):
        if len(arbitrage_opportunities) >= MAX_OPPORTUNITIES:
            break
        for s2 in sources[i + 1:]:
            if len(arbitrage_opportunities) >= MAX_OPPORTUNITIES:
                break
            for m1 in by_source[s1]:
                if len(arbitrage_opportunities) >= MAX_OPPORTUNITIES:
                    break
                for m2 in by_source[s2]:
                    if len(arbitrage_opportunities) >= MAX_OPPORTUNITIES:
                        break
                    p1 = m1.get("probability")
                    p2 = m2.get("probability")
                    if p1 is not None and p2 is not None:
                        diff = abs(p1 - p2)
                        if diff >= 0.05:  # 5% difference threshold
                            arbitrage_opportunities.append({
                                "market_1": {
                                    "source": s1,
                                    "id": m1.get("id"),
                                    "question": m1.get("question"),
                                    "probability": p1,
                                },
                                "market_2": {
                                    "source": s2,
                                    "id": m2.get("id"),
                                    "question": m2.get("question"),
                                    "probability": p2,
                                },
                                "difference": round(diff, 3),
                            })

    # Sort by largest difference
    arbitrage_opportunities.sort(key=lambda x: x["difference"], reverse=True)

    return {
        "term": term,
        "markets": markets,
        "consensus": consensus,
        "disagreement": disagreement,
        "arbitrage_opportunities": arbitrage_opportunities[:10],  # Top 10
    }


# --- Resolved Markets ---


async def get_manifold_resolved(
    term: str | None, since: datetime | None
) -> list[dict]:
    """Get resolved markets from Manifold."""
    async with aiohttp.ClientSession() as session:
        params: dict = {"limit": 100, "filter": "resolved"}
        if term:
            params["term"] = term

        async with session.get(
            "https://api.manifold.markets/v0/search-markets",
            params=params,
        ) as resp:
            if resp.status != 200:
                return []
            markets = await resp.json()

        results = []
        for m in markets:
            resolved_at = m.get("resolutionTime")
            if resolved_at and since:
                resolved_time = datetime.fromtimestamp(resolved_at / 1000, tz=timezone.utc)
                if resolved_time < since:
                    continue

            resolution = m.get("resolution", "")
            final_prob = m.get("resolutionProbability") or m.get("probability")

            was_correct = None
            if m.get("outcomeType") == "BINARY" and final_prob is not None:
                if resolution == "YES":
                    was_correct = final_prob >= 0.5
                elif resolution == "NO":
                    was_correct = final_prob < 0.5

            results.append({
                "market_id": m.get("id"),
                "source": "manifold",
                "question": m.get("question", ""),
                "resolution": resolution,
                "final_probability": round(final_prob, 3) if final_prob else None,
                "resolved_at": (
                    datetime.fromtimestamp(resolved_at / 1000, tz=timezone.utc).isoformat()
                    if resolved_at else None
                ),
                "was_correct": was_correct,
            })

        return results


async def get_kalshi_resolved(
    term: str | None, since: datetime | None
) -> list[dict]:
    """Get resolved markets from Kalshi."""
    async with aiohttp.ClientSession() as session:
        all_markets: list[dict] = []
        cursor = None

        for _ in range(3):  # Limit pagination
            params: dict = {"status": "closed", "limit": 100}
            if cursor:
                params["cursor"] = cursor

            async with session.get(
                "https://api.elections.kalshi.com/trade-api/v2/markets",
                params=params,
            ) as resp:
                if resp.status != 200:
                    break
                data = await resp.json()

            markets = data.get("markets", [])
            all_markets.extend(markets)
            cursor = data.get("cursor")
            if not cursor:
                break

        results = []
        for m in all_markets:
            title = m.get("title", "")

            # Filter by term if provided
            if term and term.lower() not in title.lower():
                continue

            close_time = m.get("close_time")
            if close_time and since:
                try:
                    closed = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                    if closed < since:
                        continue
                except ValueError:
                    pass

            result_str = m.get("result", "")
            final_price = m.get("last_price", 0)
            final_prob = final_price / 100 if final_price else None

            was_correct = None
            if final_prob is not None:
                if result_str == "yes":
                    was_correct = final_prob >= 0.5
                elif result_str == "no":
                    was_correct = final_prob < 0.5

            results.append({
                "market_id": m.get("ticker"),
                "source": "kalshi",
                "question": title,
                "resolution": result_str.upper() if result_str else None,
                "final_probability": round(final_prob, 3) if final_prob else None,
                "resolved_at": close_time,
                "was_correct": was_correct,
            })

        return results


@meta_mcp.tool()
async def get_resolved_markets(
    term: str | None = None,
    since: str | None = None,
    sources: list[MarketSource] | None = None,
) -> list[dict]:
    """Get resolved prediction markets for calibration analysis.

    Use this to compare what markets predicted vs. what actually happened.

    Args:
        term: Optional search term to filter markets.
        since: Optional ISO date string to only include markets resolved after this date.
        sources: List of sources to query. Defaults to ["manifold", "kalshi"].

    Returns:
        List of resolved markets with: market_id, source, question, resolution,
        final_probability (last price before resolution), resolved_at, was_correct.
    """
    if sources is None:
        sources = ["manifold", "kalshi"]  # Polymarket resolved markets harder to query

    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            since_dt = None

    tasks = []
    for source in sources:
        if source == "manifold":
            tasks.append(get_manifold_resolved(term, since_dt))
        elif source == "kalshi":
            tasks.append(get_kalshi_resolved(term, since_dt))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_markets = []
    for result in results:
        if isinstance(result, list):
            all_markets.extend(result)
        elif isinstance(result, Exception):
            logger.warning("Error fetching resolved markets: %s", result)

    # Sort by resolved_at descending
    all_markets.sort(
        key=lambda x: x.get("resolved_at") or "",
        reverse=True,
    )

    return all_markets


# --- Watchlist Tools ---


async def fetch_market_info(
    market_id: str, source: MarketSource
) -> dict | None:
    """Fetch current market info and price."""
    async with aiohttp.ClientSession() as session:
        if source == "manifold":
            async with session.get(
                f"https://api.manifold.markets/v0/market/{market_id}"
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return {
                    "question": data.get("question"),
                    "url": data.get("url"),
                    "probability": data.get("probability"),
                }
        elif source == "kalshi":
            async with session.get(
                f"https://api.elections.kalshi.com/trade-api/v2/markets/{market_id}"
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                market = data.get("market", {})
                price = market.get("last_price", 0)
                return {
                    "question": market.get("title"),
                    "url": f"https://kalshi.com/markets/{market_id}",
                    "probability": price / 100 if price else None,
                }
        else:  # polymarket
            # For Polymarket, try to get from Gamma API
            async with session.get(
                f"https://gamma-api.polymarket.com/markets/{market_id}"
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                prices = parse_outcome_prices(data.get("outcomePrices", "[]"))
                return {
                    "question": data.get("question"),
                    "url": f"https://polymarket.com/event/{data.get('slug', '')}",
                    "probability": float(prices[0]) if prices else None,
                }


@meta_mcp.tool()
async def watch_market(
    market_id: str,
    source: MarketSource,
    alert_threshold: float | None = None,
) -> dict:
    """Add a prediction market to your watchlist for tracking over time.

    Args:
        market_id: The market identifier (ticker for Kalshi, contract ID for others).
        source: The prediction market source ("manifold", "polymarket", or "kalshi").
        alert_threshold: Optional price threshold to track (for future alerting).

    Returns:
        Dict with watched market info including current price.
    """
    # First check auth and existing watch outside of async context
    with make_session() as session:
        user_session = _get_user_session_from_token(session)
        if not user_session or not user_session.user:
            return {"error": "Not authenticated"}

        user_id = user_session.user.id

        # Check if already watching
        existing = (
            session.query(WatchedMarket)
            .filter(
                WatchedMarket.user_id == user_id,
                WatchedMarket.market_id == market_id,
                WatchedMarket.source == source,
            )
            .first()
        )

        if existing:
            return {
                "status": "already_watching",
                "market_id": market_id,
                "source": source,
                "added_at": existing.added_at.isoformat(),
            }

    # Fetch market info outside DB session (async HTTP call)
    market_info = await fetch_market_info(market_id, source)
    if not market_info:
        return {
            "error": f"Could not fetch market info for {source}:{market_id}",
            "market_id": market_id,
            "source": source,
        }

    # Create watched market entry in new session
    with make_session() as session:
        now = datetime.now(timezone.utc)
        watched = WatchedMarket(
            user_id=user_id,
            market_id=market_id,
            source=source,
            question=market_info.get("question"),
            url=market_info.get("url"),
            price_when_added=market_info.get("probability"),
            last_price=market_info.get("probability"),
            last_updated=now,
            alert_threshold=alert_threshold,
        )
        session.add(watched)
        session.commit()
        added_at = watched.added_at.isoformat()

    return {
        "status": "watching",
        "market_id": market_id,
        "source": source,
        "question": market_info.get("question"),
        "url": market_info.get("url"),
        "current_price": market_info.get("probability"),
        "alert_threshold": alert_threshold,
        "added_at": added_at,
    }


@meta_mcp.tool()
async def get_watchlist() -> list[dict]:
    """Get all markets you are watching with current prices and changes.

    Returns:
        List of watched markets with: market_id, source, question, url,
        price_when_added, current_price, price_change, added_at.
    """
    # Get watched markets from DB first
    with make_session() as session:
        user_session = _get_user_session_from_token(session)
        if not user_session or not user_session.user:
            return []

        user_id = user_session.user.id

        watched_markets = (
            session.query(WatchedMarket)
            .filter(WatchedMarket.user_id == user_id)
            .order_by(WatchedMarket.added_at.desc())
            .all()
        )

        if not watched_markets:
            return []

        # Extract data we need before closing session
        market_data = [
            {
                "id": wm.id,
                "market_id": wm.market_id,
                "source": wm.source,
                "question": wm.question,
                "url": wm.url,
                "price_when_added": wm.price_when_added,
                "alert_threshold": wm.alert_threshold,
                "added_at": wm.added_at.isoformat(),
            }
            for wm in watched_markets
        ]

    # Fetch current prices concurrently using asyncio.gather
    fetch_tasks = [
        fetch_market_info(m["market_id"], m["source"])  # type: ignore
        for m in market_data
    ]
    market_infos = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    # Build results and collect price updates
    results = []
    price_updates: list[tuple[int, float]] = []  # (id, new_price)

    for wm_data, market_info in zip(market_data, market_infos):
        current_price = None
        if isinstance(market_info, dict):
            current_price = market_info.get("probability")
            if current_price is not None:
                price_updates.append((wm_data["id"], current_price))

        price_change = None
        if current_price is not None and wm_data["price_when_added"] is not None:
            price_change = round(current_price - wm_data["price_when_added"], 3)

        results.append({
            "market_id": wm_data["market_id"],
            "source": wm_data["source"],
            "question": wm_data["question"],
            "url": wm_data["url"],
            "price_when_added": wm_data["price_when_added"],
            "current_price": current_price,
            "price_change": price_change,
            "alert_threshold": wm_data["alert_threshold"],
            "added_at": wm_data["added_at"],
        })

    # Update prices in DB if we have updates
    if price_updates:
        with make_session() as session:
            now = datetime.now(timezone.utc)
            for wm_id, new_price in price_updates:
                session.query(WatchedMarket).filter(
                    WatchedMarket.id == wm_id
                ).update({
                    WatchedMarket.last_price: new_price,
                    WatchedMarket.last_updated: now,
                })
            session.commit()

    return results


@meta_mcp.tool()
async def unwatch_market(
    market_id: str,
    source: MarketSource,
) -> dict:
    """Remove a market from your watchlist.

    Args:
        market_id: The market identifier.
        source: The prediction market source.

    Returns:
        Dict with status of the removal.
    """
    with make_session() as session:
        user_session = _get_user_session_from_token(session)
        if not user_session or not user_session.user:
            return {"error": "Not authenticated"}

        user_id = user_session.user.id

        watched = (
            session.query(WatchedMarket)
            .filter(
                WatchedMarket.user_id == user_id,
                WatchedMarket.market_id == market_id,
                WatchedMarket.source == source,
            )
            .first()
        )

        if not watched:
            return {
                "status": "not_found",
                "market_id": market_id,
                "source": source,
            }

        session.delete(watched)
        session.commit()

        return {
            "status": "removed",
            "market_id": market_id,
            "source": source,
        }
