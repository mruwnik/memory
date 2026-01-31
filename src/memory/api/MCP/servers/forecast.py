"""MCP subserver for prediction market forecasting tools.

This server provides tools for searching, analyzing, and tracking prediction
markets across Manifold, Polymarket, and Kalshi.

Requires the "forecast" scope.
"""

import asyncio
from datetime import datetime, timezone
from typing import Literal

import aiohttp
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token

from memory.common.db.connection import make_session
from memory.common.db.models import UserSession, WatchedMarket
from memory.common.markets import (
    MarketSource,
    cache_key,
    clear_all_caches,
    compare_forecasts_data,
    fetch_market_info,
    get_cached_depth,
    get_cached_history,
    get_kalshi_history,
    get_kalshi_resolved,
    get_manifold_history,
    get_manifold_resolved,
    get_polymarket_history,
    get_polymarket_resolved,
    search_markets,
    set_cached_depth,
    set_cached_history,
    get_cached_search,
    set_cached_search,
)
from memory.common.scopes import has_scope

# Short name to stay under 20 char limit for full server name
forecast_mcp = FastMCP("memory-forecast")


def _check_forecast_scope() -> bool:
    """Check if the current user has the forecast scope."""
    access_token = get_access_token()
    if not access_token:
        return False
    return has_scope(access_token.scopes, "forecast")


def _get_user_session_from_token(session) -> "UserSession | None":
    """Get the UserSession from the current access token."""
    access_token = get_access_token()
    if not access_token:
        return None
    user_session = session.get(UserSession, access_token.token)
    if not user_session or not user_session.user:
        return None
    return user_session


# --- Search Tools ---


@forecast_mcp.tool()
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
    if not _check_forecast_scope():
        return [{"error": "Missing 'forecast' scope"}]

    # Check cache first
    key = cache_key("search", term, str(min_volume), str(binary), str(sources))
    cached = get_cached_search(key)
    if cached is not None:
        return cached

    results = await search_markets(term, min_volume, binary, sources)
    set_cached_search(key, results)
    return results


@forecast_mcp.tool()
async def clear_cache() -> dict:
    """Clear all cached forecast data.

    Useful for debugging or when you need fresh data immediately.
    Clears search results, market history, and order book depth caches.

    Returns:
        Dict confirming caches were cleared.
    """
    if not _check_forecast_scope():
        return {"error": "Missing 'forecast' scope"}

    return clear_all_caches()


# --- History Tools ---


@forecast_mcp.tool()
async def history(
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

    Source-specific notes:
        - **Manifold**: Reliable history via bet aggregation. Good for all periods.
        - **Kalshi**: Reliable history via candlestick API. Good for all periods.
        - **Polymarket**: History often unavailable (requires auth). Returns empty
          history in most cases. Use Manifold/Kalshi for historical analysis.
    """
    if not _check_forecast_scope():
        return {"error": "Missing 'forecast' scope"}

    key = cache_key("history", market_id, source, period)
    cached = get_cached_history(key)
    if cached is not None:
        return cached

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

    set_cached_history(key, result)
    return result


# --- Depth Tools ---


@forecast_mcp.tool()
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
    if not _check_forecast_scope():
        return {"error": "Missing 'forecast' scope"}

    if source != "kalshi":
        return {
            "error": f"Order book not available for {source}. Only Kalshi is supported.",
            "market_id": market_id,
            "source": source,
        }

    key = cache_key("depth", market_id, source)
    cached = get_cached_depth(key)
    if cached is not None:
        return cached

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

    orderbook = data.get("orderbook") or {}

    # Parse yes and no sides (handle None or missing keys gracefully)
    yes_levels = orderbook.get("yes") or []
    no_levels = orderbook.get("no") or []

    yes_bids = [
        {"price": float(level[0]) / 100, "quantity": int(level[1])}
        for level in yes_levels
        if level and len(level) >= 2
    ]
    no_bids = [
        {"price": float(level[0]) / 100, "quantity": int(level[1])}
        for level in no_levels
        if level and len(level) >= 2
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

    set_cached_depth(key, result)
    return result


# --- Analysis Tools ---


@forecast_mcp.tool()
async def compare_forecasts(
    term: str,
    min_volume: int = 1000,
) -> dict:
    """Compare forecasts across platforms for the same topic.

    Searches all platforms for markets matching the term and calculates:
    - Volume-weighted consensus probability
    - Disagreement (standard deviation of probabilities)
    - Arbitrage opportunities (>5% price differences between platforms)

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
    if not _check_forecast_scope():
        return {"error": "Missing 'forecast' scope"}

    return await compare_forecasts_data(term, min_volume)


@forecast_mcp.tool()
async def resolved(
    term: str | None = None,
    since: str | None = None,
    sources: list[MarketSource] | None = None,
) -> list[dict]:
    """Get resolved markets for calibration analysis.

    Useful for comparing market predictions to actual outcomes.

    Args:
        term: Optional search term to filter markets.
        since: Optional ISO date string to filter markets resolved after this date.
        sources: List of sources to query. Defaults to all sources.

    Returns:
        List of resolved markets with: market_id, source, question, resolution,
        final_probability, resolved_at, was_correct (for binary markets).
    """
    if not _check_forecast_scope():
        return [{"error": "Missing 'forecast' scope"}]

    if sources is None:
        sources = ["manifold", "kalshi"]  # Polymarket doesn't have easy resolved API

    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    resolve_funcs = {
        "manifold": get_manifold_resolved,
        "kalshi": get_kalshi_resolved,
        "polymarket": get_polymarket_resolved,
    }

    tasks = [
        resolve_funcs[source](term, since_dt)
        for source in sources
        if source in resolve_funcs
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_resolved = []
    for result in results:
        if isinstance(result, list):
            all_resolved.extend(result)

    return all_resolved


# --- Watchlist Tools ---


@forecast_mcp.tool()
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
        Dict with status and market info.
    """
    if not _check_forecast_scope():
        return {"error": "Missing 'forecast' scope"}

    # Get user info first
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


@forecast_mcp.tool()
async def get_watchlist() -> list[dict]:
    """Get all markets you are watching with current prices and changes.

    Returns:
        List of watched markets with: market_id, source, question, url,
        price_when_added, current_price, price_change, added_at.
    """
    if not _check_forecast_scope():
        return [{"error": "Missing 'forecast' scope"}]

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
                session.query(WatchedMarket).filter(WatchedMarket.id == wm_id).update({
                    WatchedMarket.last_price: new_price,
                    WatchedMarket.last_updated: now,
                })
            session.commit()

    return results


@forecast_mcp.tool()
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
    if not _check_forecast_scope():
        return {"error": "Missing 'forecast' scope"}

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
