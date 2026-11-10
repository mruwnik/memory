"""Tests for prediction market utilities."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.common.markets import (
    format_manifold_market,
    get_manifold_resolved,
    question_similarity,
    search_polymarket_markets,
)


@pytest.mark.parametrize(
    "q1,q2,expected_min,expected_max",
    [
        # Identical questions
        ("Will Trump win 2024?", "Will Trump win 2024?", 1.0, 1.0),
        # Very similar questions
        (
            "Will Trump win the 2024 presidential election?",
            "Trump wins 2024 presidential election",
            0.5,
            1.0,
        ),
        # Somewhat similar (same topic, different framing)
        # Note: "100k" and "100000" don't match as words, so similarity is lower
        (
            "Bitcoin reaches $100k by 2025",
            "Will Bitcoin price exceed $100,000 in 2025?",
            0.2,
            0.5,
        ),
        # Completely different questions
        (
            "Will Ukraine ceasefire happen by 2025?",
            "XRP reaches new all time high in 2024",
            0.0,
            0.2,
        ),
        # Empty strings
        ("", "", 0.0, 0.0),
        ("Some question", "", 0.0, 0.0),
        ("", "Some question", 0.0, 0.0),
        # Only stop words
        ("the a an is are", "will be to of in", 0.0, 0.0),
    ],
)
def test_question_similarity(q1, q2, expected_min, expected_max):
    """question_similarity returns expected range for various question pairs."""
    similarity = question_similarity(q1, q2)
    assert expected_min <= similarity <= expected_max, (
        f"Expected similarity between {expected_min} and {expected_max}, "
        f"got {similarity} for '{q1}' vs '{q2}'"
    )


def test_question_similarity_symmetric():
    """question_similarity is symmetric."""
    q1 = "Will AGI be developed before 2030?"
    q2 = "AGI development timeline: before 2030"
    assert question_similarity(q1, q2) == question_similarity(q2, q1)


def test_question_similarity_case_insensitive():
    """question_similarity is case insensitive."""
    q1 = "TRUMP WINS 2024"
    q2 = "trump wins 2024"
    assert question_similarity(q1, q2) == 1.0


def http_session(payload) -> MagicMock:
    """An aiohttp.ClientSession stand-in whose every GET returns `payload`."""
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value=payload)
    get_cm = MagicMock()
    get_cm.__aenter__ = AsyncMock(return_value=response)
    get_cm.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.get = MagicMock(return_value=get_cm)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize("prices, expected", [("[0, 1]", 0.0), ("[0.25, 0.75]", 0.25)])
async def test_search_polymarket_keeps_zero_probability(prices, expected):
    event = {
        "id": "e1",
        "title": "Q",
        "slug": "q",
        "volume": 50000,
        "markets": [{"id": "m1", "outcomePrices": prices}],
    }
    session = http_session({"events": [event]})

    with patch("memory.common.markets.aiohttp.ClientSession", return_value=session):
        result = await search_polymarket_markets("q", min_volume=1000)

    assert result[0]["probability"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resolution_probability, expected_final, expected_correct",
    [(0, 0.0, True), (0.9, 0.9, False), (None, 0.3, True)],
)
async def test_manifold_resolved_uses_zero_resolution_probability(
    resolution_probability, expected_final, expected_correct
):
    market = {
        "id": "m1",
        "question": "Q",
        "outcomeType": "BINARY",
        "resolution": "NO",
        "resolutionProbability": resolution_probability,
        "probability": 0.3,
    }
    session = http_session([market])

    with patch("memory.common.markets.aiohttp.ClientSession", return_value=session):
        result = await get_manifold_resolved(None, None)

    assert (result[0]["final_probability"], result[0]["was_correct"]) == (
        expected_final,
        expected_correct,
    )


@pytest.mark.asyncio
async def test_format_manifold_market_keeps_zero_resolution_probability():
    answers = [
        {"text": "A", "resolutionProbability": 0, "probability": 0.4},
        {"text": "B", "resolutionProbability": 1, "probability": 0.6},
    ]
    session = http_session({"answers": answers})
    market = {"id": "m1", "outcomeType": "MULTIPLE_CHOICE", "question": "Q"}

    result = await format_manifold_market(session, cast(Any, market))

    assert result["answers"] == {"A": 0.0, "B": 1}
