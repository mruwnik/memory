"""Common prediction market functionality.

This module provides shared functionality for accessing prediction market data
from Manifold, Polymarket, and Kalshi. It includes caching, API clients,
and helper functions used by the forecast MCP server.
"""

import asyncio
import json
import logging
import re
import statistics
import threading
from datetime import datetime, timedelta, timezone
from typing import Literal, NotRequired, TypedDict

import aiohttp
from cachetools import TTLCache

logger = logging.getLogger(__name__)


def question_similarity(q1: str, q2: str) -> float:
    """Calculate simple word-based similarity between two questions.

    Returns a value between 0 and 1, where 1 means identical word sets.
    """
    if not q1 or not q2:
        return 0.0

    # Normalize: lowercase, remove punctuation, split into words
    def normalize(text: str) -> set[str]:
        text = text.lower()
        text = re.sub(r"[^\w\s]", " ", text)
        words = set(text.split())
        # Remove common stop words
        stop_words = {
            "the", "a", "an", "will", "be", "is", "are", "was", "were",
            "to", "of", "in", "on", "at", "by", "for", "with", "or", "and",
            "this", "that", "it", "as", "if", "when", "than", "but", "not",
            "what", "which", "who", "how", "before", "after", "during",
        }
        return words - stop_words

    words1 = normalize(q1)
    words2 = normalize(q2)

    if not words1 or not words2:
        return 0.0

    # Jaccard similarity
    intersection = len(words1 & words2)
    union = len(words1 | words2)
    return intersection / union if union > 0 else 0.0

# --- Type definitions ---

MarketSource = Literal["manifold", "polymarket", "kalshi"]


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


# --- Caching infrastructure ---
# TTLCache is not thread-safe, so we use locks for concurrent access

# Search results cache: 5 minute TTL
_search_cache: TTLCache[str, list[dict]] = TTLCache(maxsize=500, ttl=300)
_search_cache_lock = threading.Lock()
# Market details/history cache: 10 minute TTL
_history_cache: TTLCache[str, dict] = TTLCache(maxsize=100, ttl=600)
_history_cache_lock = threading.Lock()
# Market depth cache: 1 minute TTL (more volatile)
_depth_cache: TTLCache[str, dict] = TTLCache(maxsize=100, ttl=60)
_depth_cache_lock = threading.Lock()


def cache_key(*args: str) -> str:
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


def get_cached_search(key: str) -> list[dict] | None:
    """Get a cached search result."""
    with _search_cache_lock:
        return _search_cache.get(key)


def set_cached_search(key: str, value: list[dict]) -> None:
    """Set a cached search result."""
    with _search_cache_lock:
        _search_cache[key] = value


def get_cached_history(key: str) -> dict | None:
    """Get a cached history result."""
    with _history_cache_lock:
        return _history_cache.get(key)


def set_cached_history(key: str, value: dict) -> None:
    """Set a cached history result."""
    with _history_cache_lock:
        _history_cache[key] = value


def get_cached_depth(key: str) -> dict | None:
    """Get a cached depth result."""
    with _depth_cache_lock:
        return _depth_cache.get(key)


def set_cached_depth(key: str, value: dict) -> None:
    """Set a cached depth result."""
    with _depth_cache_lock:
        _depth_cache[key] = value


# --- Helper functions ---


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
        return volume_score * 0.7 + spread_score * 0.3

    return volume_score


def parse_outcome_prices(outcome_prices: str | list) -> list:
    """Parse Polymarket outcome prices from string or list format."""
    if isinstance(outcome_prices, str):
        try:
            return json.loads(outcome_prices)
        except json.JSONDecodeError:
            return []
    return outcome_prices


# --- Manifold API ---


async def get_manifold_details(session: aiohttp.ClientSession, market_id: str):
    """Get detailed market info from Manifold."""
    async with session.get(
        f"https://api.manifold.markets/v0/market/{market_id}"
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


async def format_manifold_market(session: aiohttp.ClientSession, market: Market):
    """Format a Manifold market with additional details."""
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


async def get_manifold_history(market_id: str, days: int = 7) -> list[dict]:
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
                if resolved_at
                else None
            ),
            "was_correct": was_correct,
        })

    return results


# --- Polymarket API ---


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

            results.append({
                "source": "polymarket",
                "id": market.get("id") or event.get("id"),
                "question": event.get("title", ""),
                "url": f"https://polymarket.com/event/{event.get('slug', '')}",
                "volume": volume,
                "probability": round(prob, 3) if prob else None,
                "createdAt": created_at,
                "liquidity_score": liquidity_score,
            })
        elif not binary:
            # Multiple choice event - include all outcomes
            answers = {}
            for market in markets:
                outcome = market.get("outcome", market.get("groupItemTitle", ""))
                prices = parse_outcome_prices(market.get("outcomePrices", "[]"))
                prob = float(prices[0]) if prices else 0
                if outcome:
                    answers[outcome] = round(prob, 3)

            results.append({
                "source": "polymarket",
                "id": event.get("id"),
                "question": event.get("title", ""),
                "url": f"https://polymarket.com/event/{event.get('slug', '')}",
                "volume": volume,
                "answers": answers,
                "createdAt": created_at,
                "liquidity_score": liquidity_score,
            })

    return results


async def get_polymarket_history(market_id: str, days: int = 7) -> list[dict]:
    """Get price history for a Polymarket market.

    Uses the Polymarket CLOB API for historical prices.

    **Important limitations:**
    - The CLOB timeseries endpoint requires authentication for most markets
    - Public access is inconsistent and often returns 403 or empty data
    - For reliable history, use Manifold or Kalshi markets instead
    - This function returns an empty list when history cannot be fetched

    Args:
        market_id: The Polymarket market/condition ID.
        days: Number of days of history to fetch.

    Returns:
        List of price points with timestamp, probability, and volume.
        Returns empty list if history is unavailable (common for Polymarket).
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
                    market_id,
                    resp.status,
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


async def get_polymarket_resolved(
    term: str | None, since: datetime | None
) -> list[dict]:
    """Get resolved markets from Polymarket."""
    # Polymarket doesn't have an easy resolved markets API
    # Would need to query specific markets by ID
    return []


# --- Kalshi API ---


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


async def search_kalshi_events(
    session: aiohttp.ClientSession, term: str, min_volume: int
) -> list[dict]:
    """Search Kalshi events API for matching markets.

    Kalshi organizes political/event markets under /events, while /markets
    contains mostly sports parlays. This function searches events and returns
    the associated markets.
    """
    results: list[dict] = []
    cursor = None

    for _ in range(3):  # Max 3 pages of events
        params: dict = {"status": "open", "limit": 100}
        if cursor:
            params["cursor"] = cursor

        try:
            async with session.get(
                "https://api.elections.kalshi.com/trade-api/v2/events",
                params=params,
            ) as resp:
                if resp.status != 200:
                    break
                data = await resp.json()
        except aiohttp.ClientError:
            break

        events = data.get("events", [])
        if not events:
            break

        for event in events:
            title = event.get("title", "")
            if term.lower() not in title.lower():
                continue

            # Get markets for this event
            event_ticker = event.get("event_ticker")
            if not event_ticker:
                continue

            try:
                async with session.get(
                    f"https://api.elections.kalshi.com/trade-api/v2/events/{event_ticker}"
                ) as resp:
                    if resp.status != 200:
                        continue
                    event_data = await resp.json()
            except aiohttp.ClientError:
                continue

            # Markets are at top level, not inside "event"
            markets = event_data.get("markets", [])

            for market in markets:
                volume = market.get("volume", 0) or 0
                if volume < min_volume:
                    continue

                yes_price = market.get("last_price") or market.get("yes_bid") or 0
                probability = yes_price / 100 if yes_price else None

                yes_bid = market.get("yes_bid", 0) or 0
                yes_ask = market.get("yes_ask", 0) or 0
                spread = None
                if yes_bid > 0 and yes_ask > 0:
                    spread = (yes_ask - yes_bid) / 100

                created_at = market.get("open_time")
                liquidity_score = calculate_liquidity_score(volume, created_at, spread)

                result = {
                    "source": "kalshi",
                    "id": market.get("ticker"),
                    "question": title,  # Use event title for cleaner question
                    "url": f"https://kalshi.com/markets/{market.get('ticker', '')}",
                    "volume": volume,
                    "probability": round(probability, 3) if probability else None,
                    "createdAt": created_at,
                    "liquidity_score": round(liquidity_score, 3),
                }
                if spread is not None:
                    result["spread"] = round(spread, 3)
                results.append(result)

        cursor = data.get("cursor")
        if not cursor:
            break

    return results


async def search_kalshi_markets(
    term: str, min_volume: int = 1000, binary: bool = False  # noqa: ARG001
) -> list[dict]:
    """Search Kalshi for prediction markets matching the term.

    Uses the /events endpoint which contains political and event markets.
    The /markets endpoint is skipped as it's mostly sports betting parlays.

    Note: The `binary` parameter is accepted for API consistency but ignored since
    all Kalshi markets are binary (yes/no) by design.
    """
    async with aiohttp.ClientSession() as session:
        results = await search_kalshi_events(session, term, min_volume)
        logger.debug("Kalshi search for '%s': found %d markets", term, len(results))
        return results


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


async def get_kalshi_resolved(term: str | None, since: datetime | None) -> list[dict]:
    """Get resolved markets from Kalshi."""
    async with aiohttp.ClientSession() as session:
        all_markets: list[dict] = []
        cursor = None

        # Paginate through settled markets
        for _ in range(5):
            params: dict = {
                "status": "settled",
                "limit": 100,
            }
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
            if not cursor or not markets:
                break

    results = []
    for m in all_markets:
        title = m.get("title", "")

        # Filter by term if provided
        if term and term.lower() not in title.lower():
            continue

        # Filter by since date
        close_time = m.get("close_time")
        if close_time and since:
            try:
                closed = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                if closed < since:
                    continue
            except (ValueError, TypeError):
                pass

        # Determine resolution
        result_str = m.get("result", "")
        last_price = m.get("last_price", 0) or 0
        final_prob = last_price / 100 if last_price else None

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
            "resolution": result_str.upper() if result_str else "",
            "final_probability": round(final_prob, 3) if final_prob else None,
            "resolved_at": close_time,
            "was_correct": was_correct,
        })

    return results


# --- Combined search ---


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


async def fetch_market_info(market_id: str, source: MarketSource) -> dict | None:
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


# --- Compare and analyze ---


async def compare_forecasts_data(
    term: str, min_volume: int = 1000
) -> dict:
    """Compare forecasts across platforms and find arbitrage opportunities.

    Args:
        term: Search term to find related markets across platforms.
        min_volume: Minimum volume threshold for markets to include.

    Returns:
        Dict with markets, consensus, disagreement, and arbitrage_opportunities.
    """
    # Check cache first
    key = cache_key("compare", term, str(min_volume))
    cached = get_cached_search(key)
    if cached is not None:
        markets = cached
    else:
        markets = await search_markets(term, min_volume, binary=True)
        set_cached_search(key, markets)

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
                sum(p * v for p, v in probs_with_volume if p is not None) / total_volume,
                3,
            )

        if len(probs) > 1:
            # Standard deviation
            disagreement = round(statistics.stdev(probs), 3)

    # Find arbitrage opportunities (>5% price difference between platforms)
    # Only flag arbitrage if questions are semantically similar (>40% word overlap)
    SIMILARITY_THRESHOLD = 0.4
    arbitrage_opportunities = []
    by_source: dict[str, list[dict]] = {}
    for m in markets:
        source = m.get("source")
        if not source:
            continue
        if source not in by_source:
            by_source[source] = []
        by_source[source].append(m)

    # Early exit once we have enough opportunities (limit O(n^4) impact)
    MAX_OPPORTUNITIES = 20  # Collect up to 20, return top 10
    sources = list(by_source.keys())
    for i, s1 in enumerate(sources):
        if len(arbitrage_opportunities) >= MAX_OPPORTUNITIES:
            break
        for s2 in sources[i + 1 :]:
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
                            # Check if questions are semantically similar
                            q1 = m1.get("question", "")
                            q2 = m2.get("question", "")
                            similarity = question_similarity(q1, q2)
                            if similarity < SIMILARITY_THRESHOLD:
                                continue  # Skip if questions are too different

                            arbitrage_opportunities.append({
                                "market_1": {
                                    "source": s1,
                                    "id": m1.get("id"),
                                    "question": q1,
                                    "probability": p1,
                                },
                                "market_2": {
                                    "source": s2,
                                    "id": m2.get("id"),
                                    "question": q2,
                                    "probability": p2,
                                },
                                "difference": round(diff, 3),
                                "similarity": round(similarity, 3),
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
