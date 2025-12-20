"""
Tests for the rerank module (VoyageAI cross-encoder reranking).
"""

import pytest
from unittest.mock import MagicMock, patch

from memory.api.search.rerank import rerank_chunks, DEFAULT_RERANK_MODEL


class MockRerankResult:
    """Mock for VoyageAI rerank result item."""

    def __init__(self, index: int, relevance_score: float):
        self.index = index
        self.relevance_score = relevance_score


class MockRerankResponse:
    """Mock for VoyageAI rerank response."""

    def __init__(self, results: list[MockRerankResult]):
        self.results = results


def _make_chunk(content: str = "test content", score: float = 0.5):
    """Create a mock chunk."""
    chunk = MagicMock()
    chunk.content = content
    chunk.relevance_score = score
    chunk.data = None
    return chunk


# ============================================================================
# Basic reranking tests
# ============================================================================


@pytest.mark.asyncio
@patch("memory.api.search.rerank.voyageai")
async def test_rerank_chunks_basic(mock_voyageai):
    """Should rerank chunks using VoyageAI."""
    mock_client = MagicMock()
    mock_voyageai.Client.return_value = mock_client
    mock_client.rerank.return_value = MockRerankResponse([
        MockRerankResult(index=1, relevance_score=0.9),
        MockRerankResult(index=0, relevance_score=0.7),
    ])

    chunks = [_make_chunk("first", 0.5), _make_chunk("second", 0.6)]
    result = await rerank_chunks("test query", chunks)

    assert len(result) == 2
    assert result[0].relevance_score == 0.9
    assert result[1].relevance_score == 0.7


@pytest.mark.asyncio
@patch("memory.api.search.rerank.voyageai")
async def test_rerank_chunks_reorders_correctly(mock_voyageai):
    """Should correctly reorder multiple chunks."""
    mock_client = MagicMock()
    mock_voyageai.Client.return_value = mock_client
    mock_client.rerank.return_value = MockRerankResponse([
        MockRerankResult(index=2, relevance_score=0.95),
        MockRerankResult(index=0, relevance_score=0.85),
        MockRerankResult(index=1, relevance_score=0.75),
    ])

    chunk_a = _make_chunk("chunk a", 0.5)
    chunk_b = _make_chunk("chunk b", 0.6)
    chunk_c = _make_chunk("chunk c", 0.4)

    result = await rerank_chunks("query", [chunk_a, chunk_b, chunk_c])

    assert result[0] is chunk_c
    assert result[1] is chunk_a
    assert result[2] is chunk_b


# ============================================================================
# Empty/edge case tests
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
async def test_rerank_chunks_empty_query(query):
    """Should return original chunks for empty/whitespace query."""
    chunks = [_make_chunk("test", 0.5)]
    result = await rerank_chunks(query, chunks)
    assert result == chunks


@pytest.mark.asyncio
async def test_rerank_chunks_empty_chunks():
    """Should return empty list for empty input."""
    result = await rerank_chunks("test query", [])
    assert result == []


@pytest.mark.asyncio
@patch("memory.api.search.rerank.voyageai")
async def test_rerank_chunks_all_empty_content(mock_voyageai):
    """Should return original chunks if all have empty content."""
    chunk1 = MagicMock()
    chunk1.content = ""
    chunk1.data = None
    chunk1.relevance_score = 0.5

    chunk2 = MagicMock()
    chunk2.content = None
    chunk2.data = []
    chunk2.relevance_score = 0.6

    chunks = [chunk1, chunk2]
    result = await rerank_chunks("query", chunks)

    assert result == chunks
    mock_voyageai.Client.assert_not_called()


# ============================================================================
# Model and parameter tests
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["rerank-2", "rerank-2-lite", "custom-model"])
@patch("memory.api.search.rerank.voyageai")
async def test_rerank_chunks_uses_specified_model(mock_voyageai, model):
    """Should use specified model."""
    mock_client = MagicMock()
    mock_voyageai.Client.return_value = mock_client
    mock_client.rerank.return_value = MockRerankResponse([
        MockRerankResult(index=0, relevance_score=0.8),
    ])

    await rerank_chunks("query", [_make_chunk()], model=model)

    call_kwargs = mock_client.rerank.call_args[1]
    assert call_kwargs["model"] == model


@pytest.mark.asyncio
@patch("memory.api.search.rerank.voyageai")
async def test_rerank_chunks_uses_default_model(mock_voyageai):
    """Should use default model when not specified."""
    mock_client = MagicMock()
    mock_voyageai.Client.return_value = mock_client
    mock_client.rerank.return_value = MockRerankResponse([
        MockRerankResult(index=0, relevance_score=0.8),
    ])

    await rerank_chunks("query", [_make_chunk()])

    call_kwargs = mock_client.rerank.call_args[1]
    assert call_kwargs["model"] == DEFAULT_RERANK_MODEL


@pytest.mark.asyncio
@pytest.mark.parametrize("top_k", [1, 5, 10])
@patch("memory.api.search.rerank.voyageai")
async def test_rerank_chunks_respects_top_k(mock_voyageai, top_k):
    """Should pass top_k to VoyageAI."""
    mock_client = MagicMock()
    mock_voyageai.Client.return_value = mock_client
    mock_client.rerank.return_value = MockRerankResponse([
        MockRerankResult(index=0, relevance_score=0.8),
    ])

    chunks = [_make_chunk() for _ in range(10)]
    await rerank_chunks("query", chunks, top_k=top_k)

    call_kwargs = mock_client.rerank.call_args[1]
    assert call_kwargs["top_k"] == top_k


# ============================================================================
# Content handling tests
# ============================================================================


@pytest.mark.asyncio
@patch("memory.api.search.rerank.voyageai")
async def test_rerank_chunks_skips_none_content(mock_voyageai):
    """Should skip chunks with None content."""
    mock_client = MagicMock()
    mock_voyageai.Client.return_value = mock_client
    mock_client.rerank.return_value = MockRerankResponse([
        MockRerankResult(index=0, relevance_score=0.8),
    ])

    chunk_with = _make_chunk("has content", 0.5)
    chunk_without = _make_chunk(None, 0.5)
    chunk_without.content = None

    await rerank_chunks("query", [chunk_with, chunk_without])

    call_kwargs = mock_client.rerank.call_args[1]
    assert len(call_kwargs["documents"]) == 1
    assert call_kwargs["documents"][0] == "has content"


@pytest.mark.asyncio
@patch("memory.api.search.rerank.voyageai")
async def test_rerank_chunks_truncates_long_content(mock_voyageai):
    """Should truncate content to 8000 characters."""
    mock_client = MagicMock()
    mock_voyageai.Client.return_value = mock_client
    mock_client.rerank.return_value = MockRerankResponse([
        MockRerankResult(index=0, relevance_score=0.8),
    ])

    long_content = "x" * 10000
    await rerank_chunks("query", [_make_chunk(long_content)])

    call_kwargs = mock_client.rerank.call_args[1]
    assert len(call_kwargs["documents"][0]) == 8000


@pytest.mark.asyncio
@patch("memory.api.search.rerank.voyageai")
async def test_rerank_chunks_uses_data_fallback(mock_voyageai):
    """Should fall back to data attribute if content is empty."""
    mock_client = MagicMock()
    mock_voyageai.Client.return_value = mock_client
    mock_client.rerank.return_value = MockRerankResponse([
        MockRerankResult(index=0, relevance_score=0.8),
    ])

    chunk = MagicMock()
    chunk.content = ""
    chunk.data = ["text from data", 123, "more text"]
    chunk.relevance_score = 0.5

    await rerank_chunks("query", [chunk])

    call_kwargs = mock_client.rerank.call_args[1]
    assert "text from data" in call_kwargs["documents"][0]
    assert "more text" in call_kwargs["documents"][0]


# ============================================================================
# Error handling tests
# ============================================================================


@pytest.mark.asyncio
@patch("memory.api.search.rerank.voyageai")
async def test_rerank_chunks_handles_api_error(mock_voyageai):
    """Should return original chunks on API error."""
    mock_client = MagicMock()
    mock_voyageai.Client.return_value = mock_client
    mock_client.rerank.side_effect = Exception("API error")

    chunks = [_make_chunk("test", 0.5)]
    result = await rerank_chunks("query", chunks)

    assert result == chunks


@pytest.mark.asyncio
@patch("memory.api.search.rerank.voyageai")
async def test_rerank_chunks_handles_missing_index(mock_voyageai):
    """Should handle missing indices gracefully."""
    mock_client = MagicMock()
    mock_voyageai.Client.return_value = mock_client
    mock_client.rerank.return_value = MockRerankResponse([
        MockRerankResult(index=0, relevance_score=0.8),
        MockRerankResult(index=99, relevance_score=0.7),  # Invalid index
    ])

    result = await rerank_chunks("query", [_make_chunk()])
    assert len(result) == 1


# ============================================================================
# Object preservation tests
# ============================================================================


@pytest.mark.asyncio
@patch("memory.api.search.rerank.voyageai")
async def test_rerank_chunks_preserves_objects(mock_voyageai):
    """Should return the same chunk objects, not copies."""
    mock_client = MagicMock()
    mock_voyageai.Client.return_value = mock_client
    mock_client.rerank.return_value = MockRerankResponse([
        MockRerankResult(index=0, relevance_score=0.8),
    ])

    chunk = _make_chunk("test", 0.5)
    result = await rerank_chunks("query", [chunk])

    assert result[0] is chunk


@pytest.mark.asyncio
@patch("memory.api.search.rerank.voyageai")
async def test_rerank_chunks_updates_scores(mock_voyageai):
    """Should update chunk relevance_score from reranker."""
    mock_client = MagicMock()
    mock_voyageai.Client.return_value = mock_client
    mock_client.rerank.return_value = MockRerankResponse([
        MockRerankResult(index=0, relevance_score=0.95),
    ])

    chunk = _make_chunk("test", 0.5)
    result = await rerank_chunks("query", [chunk])

    assert result[0].relevance_score == 0.95
