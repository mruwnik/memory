"""Tests for scorer module."""

import pytest
from unittest.mock import MagicMock, patch

from memory.api.search.scorer import score_chunk, rank_chunks


@pytest.fixture
def mock_chunk():
    """Create a mock chunk for testing."""
    chunk = MagicMock()
    chunk.data = ["Some text content"]
    chunk.relevance_score = None
    return chunk


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "llm_response,expected_score",
    [
        (None, 0.0),  # Null response
        ("", 0.0),  # Empty response
        ("<score>0.75</score>", 0.75),  # Valid score
        ("Some response without score tag", 0.0),  # Missing score tag
        ("<score>not a number</score>", 0.0),  # Invalid score value
    ],
)
async def test_score_chunk_llm_responses(mock_chunk, llm_response, expected_score):
    """Tests score_chunk handling of various LLM responses."""
    with patch("memory.api.search.scorer.llms.summarize", return_value=llm_response):
        result = await score_chunk("test query", mock_chunk)

    assert result.relevance_score == expected_score


@pytest.mark.asyncio
async def test_score_chunk_handles_chunk_data_error(mock_chunk):
    """Returns chunk unchanged when data access fails."""
    type(mock_chunk).data = property(
        lambda self: (_ for _ in ()).throw(Exception("Error"))
    )

    result = await score_chunk("test query", mock_chunk)

    assert result is mock_chunk


@pytest.mark.asyncio
async def test_score_chunk_handles_llm_exception(mock_chunk):
    """Returns chunk unchanged when LLM raises exception."""
    with patch(
        "memory.api.search.scorer.llms.summarize",
        side_effect=Exception("LLM error"),
    ):
        result = await score_chunk("test query", mock_chunk)

    assert result is mock_chunk


@pytest.mark.asyncio
async def test_rank_chunks_sorts_by_relevance_score():
    """Ranks chunks by relevance score in descending order."""
    chunks = [MagicMock(data=["text"]) for _ in range(3)]
    scores = [0.3, 0.9, 0.5]

    async def mock_score_chunk(query, chunk):
        idx = chunks.index(chunk)
        chunk.relevance_score = scores[idx]
        return chunk

    with patch("memory.api.search.scorer.score_chunk", side_effect=mock_score_chunk):
        result = await rank_chunks("query", chunks)

    assert result[0].relevance_score == 0.9
    assert result[1].relevance_score == 0.5
    assert result[2].relevance_score == 0.3


@pytest.mark.asyncio
async def test_rank_chunks_filters_by_min_score():
    """Filters out chunks below min_score threshold."""
    chunks = [MagicMock(data=["text"]) for _ in range(3)]
    scores = [0.2, 0.5, 0.8]

    async def mock_score_chunk(query, chunk):
        idx = chunks.index(chunk)
        chunk.relevance_score = scores[idx]
        return chunk

    with patch("memory.api.search.scorer.score_chunk", side_effect=mock_score_chunk):
        result = await rank_chunks("query", chunks, min_score=0.4)

    assert len(result) == 2
    assert all(c.relevance_score >= 0.4 for c in result)
