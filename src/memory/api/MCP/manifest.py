import asyncio
import aiohttp
from datetime import datetime

from typing import TypedDict, NotRequired, Literal
from memory.api.MCP.tools import mcp


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


@mcp.tool()
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
