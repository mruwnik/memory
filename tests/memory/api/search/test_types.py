"""Tests for search result types."""

import pytest
from datetime import datetime, timezone
from typing import cast
from unittest.mock import Mock

from memory.api.search.constants import SUMMARY_MATCH_BOOST
from memory.api.search.types import SearchResult, elide_content
from memory.common import settings
from memory.common.db.models import Chunk, SourceItem


class TestElideContent:
    """Tests for content elision."""

    def test_elide_short_content(self):
        """Short content should not be elided."""
        content = "short text"
        assert elide_content(content, 100) == "short text"

    def test_elide_long_content(self):
        """Long content should be truncated with ellipsis."""
        content = "a" * 150
        result = elide_content(content, 100)
        assert result == "a" * 100 + "..."
        assert len(result) == 103

    def test_elide_empty_content(self):
        """Empty content should return empty."""
        assert elide_content("", 100) == ""

    def test_elide_exact_length(self):
        """Content at exact max length should not be elided."""
        content = "a" * 100
        assert elide_content(content, 100) == content


class TestSearchResult:
    """Tests for SearchResult.from_source_item."""

    def _make_chunk(self, relevance_score: float, content: str = "chunk") -> Mock:
        """Create a mock chunk with given relevance score."""
        chunk = Mock()
        chunk.relevance_score = relevance_score
        chunk.content = content
        chunk.is_summary = False
        return chunk

    def _make_source_item(self, **kwargs) -> Mock:
        """Create a mock source item."""
        defaults = {
            "id": 1,
            "size": 1000,
            "mime_type": "text/plain",
            "content": "test content",
            "filename": "test.txt",
            "tags": ["tag1", "tag2"],
            "display_contents": {"key": "value"},
            "inserted_at": datetime.now(timezone.utc),
        }
        defaults.update(kwargs)

        source = Mock()
        for key, value in defaults.items():
            setattr(source, key, value)
        return source

    def test_search_score_single_chunk(self):
        """Single chunk should use its relevance score directly."""
        source = self._make_source_item()
        chunks = [self._make_chunk(0.9)]

        result = SearchResult.from_source_item(source, chunks)
        assert result.search_score == 0.9

    def test_search_score_multiple_chunks_uses_max(self):
        """Multiple chunks should use max of relevance scores.

        Using max finds documents with at least one highly relevant section,
        which is better for 'half-remembered' searches where users recall one detail.
        """
        source = self._make_source_item()
        chunks = [
            self._make_chunk(0.9),
            self._make_chunk(0.7),
            self._make_chunk(0.8),
        ]

        result = SearchResult.from_source_item(source, chunks)
        # Max of 0.9, 0.7, 0.8 = 0.9
        assert result.search_score == pytest.approx(0.9)

    def test_search_score_empty_chunks(self):
        """Empty chunk list should result in None or 0 score."""
        source = self._make_source_item()
        chunks = []

        result = SearchResult.from_source_item(source, chunks)
        # With no chunks, score should be 0 or None
        assert result.search_score == 0 or result.search_score is None

    def test_search_score_not_biased_by_chunk_count(self):
        """Documents with more chunks should not rank higher by default."""
        source = self._make_source_item()

        # Document A: 2 chunks with average score 0.7
        chunks_a = [self._make_chunk(0.7), self._make_chunk(0.7)]
        result_a = SearchResult.from_source_item(source, chunks_a)

        # Document B: 10 chunks with average score 0.6
        chunks_b = [self._make_chunk(0.6) for _ in range(10)]
        result_b = SearchResult.from_source_item(source, chunks_b)

        # A should rank higher than B (0.7 > 0.6)
        assert result_a.search_score is not None
        assert result_b.search_score is not None
        assert result_a.search_score > result_b.search_score

    def test_basic_result_fields(self):
        """Test that basic fields are populated correctly."""
        source = self._make_source_item(
            id=42,
            size=5000,
            mime_type="application/pdf",
            filename="doc.pdf",
            tags=["important"],
        )
        chunks = [self._make_chunk(0.5, "chunk content")]

        result = SearchResult.from_source_item(source, chunks)

        assert result.id == 42
        assert result.size == 5000
        assert result.mime_type == "application/pdf"
        assert result.filename == "doc.pdf"
        assert result.tags == ["important"]
        assert len(result.chunks) == 1

    def test_chunk_content_elided(self):
        """Chunk content should be elided if too long."""
        source = self._make_source_item()
        long_content = "x" * 5000
        chunks = [self._make_chunk(0.5, long_content)]

        result = SearchResult.from_source_item(source, chunks)

        # Chunk content should be truncated
        assert len(result.chunks[0]) < len(long_content)
        assert result.chunks[0].endswith("...")


def make_source(content: str = "test content") -> SourceItem:
    return SourceItem(
        id=1, sha256=b"x", content=content, modality="text", mime_type="text/plain", size=1, tags=[]
    )


def make_chunk(score: float, is_summary: bool = False) -> Chunk:
    return Chunk(content="chunk", relevance_score=score, is_summary=is_summary)


@pytest.mark.parametrize("summary", ["the summary", None])
def test_search_result_carries_the_items_summary(summary):
    result = SearchResult.from_source_item(make_source(), [make_chunk(0.5)], summary=summary)
    assert result.summary == summary


@pytest.mark.parametrize(
    "content, previews, summary, expected",
    [
        ("a" * 20, False, "s", "a" * 20),  # content that fits wins
        ("a" * 50, False, "s", "s"),
        ("a" * 50, True, "s", "a" * 50),  # fits the longer preview limit
        ("a" * 500, True, "s", "s"),
        ("a" * 50, False, None, "a" * 20 + "..."),  # no summary: cut content
        ("a" * 50, False, "b" * 30, "b" * 20 + "..."),  # summaries obey the limit too
    ],
)
def test_long_content_is_previewed_by_its_summary(
    monkeypatch, content, previews, summary, expected
):
    monkeypatch.setattr(settings, "MAX_PREVIEW_LENGTH", 100)
    monkeypatch.setattr(settings, "MAX_NON_PREVIEW_LENGTH", 20)
    result = SearchResult.from_source_item(
        make_source(content), [make_chunk(0.5)], previews, summary=summary
    )
    assert result.content == expected


@pytest.mark.parametrize("score", [0.5, 0.02, -0.5])
def test_a_summary_match_scores_a_little_above_an_equal_content_match(score):
    content_hit = SearchResult.from_source_item(make_source(), [make_chunk(score)])
    summary_hit = SearchResult.from_source_item(make_source(), [make_chunk(score, True)])
    assert content_hit.search_score == score
    assert summary_hit.search_score == pytest.approx(score + abs(score) * SUMMARY_MATCH_BOOST)


def test_a_summary_match_does_not_beat_a_clearly_better_content_match():
    content_hit = SearchResult.from_source_item(make_source(), [make_chunk(0.6)])
    summary_hit = SearchResult.from_source_item(make_source(), [make_chunk(0.5, True)])
    assert cast(float, content_hit.search_score) > cast(float, summary_hit.search_score)
