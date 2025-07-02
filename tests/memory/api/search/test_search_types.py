import pytest
from datetime import datetime
from unittest.mock import Mock

from memory.api.search.types import elide_content, SearchResult
from memory.common.db.models import Chunk, SourceItem


@pytest.mark.parametrize(
    "content,max_length,expected",
    [
        ("short text", 100, "short text"),
        ("this is a very long piece of text that exceeds the limit", 20, "this is a very long ..."),
        ("", 100, ""),
        ("exactly twenty chars", 20, "exactly twenty chars"),
        ("exactly twenty one c", 20, "exactly twenty one c"),
        ("exactly twenty one chars", 20, "exactly twenty one c..."),
        (None, 100, None),
    ],
)
def test_elide_content(content, max_length, expected):
    """Test content elision with various input lengths"""
    result = elide_content(content, max_length)
    assert result == expected


def test_elide_content_default_max_length():
    """Test elide_content with default max_length parameter"""
    long_content = "a" * 150  # 150 characters
    result = elide_content(long_content)
    assert len(result) == 103  # 100 + "..."
    assert result.endswith("...")


def test_search_result_from_source_item_basic():
    """Test basic SearchResult creation from SourceItem and chunks"""
    # Create mock source item
    source = Mock(spec=SourceItem)
    source.id = 123
    source.size = 1024
    source.mime_type = "text/plain"
    source.content = "This is the main content of the source"
    source.filename = "test.txt"
    source.tags = ["tag1", "tag2"]
    source.inserted_at = datetime(2024, 1, 1, 12, 0, 0)
    source.display_contents = {"extra": "metadata", "content": "should be removed"}

    # Create mock chunks
    chunk1 = Mock(spec=Chunk)
    chunk1.content = "First chunk content"
    chunk1.relevance_score = 0.8

    chunk2 = Mock(spec=Chunk)
    chunk2.content = "Second chunk content"
    chunk2.relevance_score = 0.6

    chunks = [chunk1, chunk2]

    result = SearchResult.from_source_item(source, chunks)

    assert result.id == 123
    assert result.size == 1024
    assert result.mime_type == "text/plain"
    assert result.filename == "test.txt"
    assert result.tags == ["tag1", "tag2"]
    assert result.created_at == datetime(2024, 1, 1, 12, 0, 0)
    assert result.search_score == 1.4  # 0.8 + 0.6
    assert len(result.chunks) == 2
    assert "content" not in result.metadata
    assert result.metadata["extra"] == "metadata"


def test_search_result_from_source_item_with_previews():
    """Test SearchResult creation with previews enabled"""
    source = Mock(spec=SourceItem)
    source.id = 456
    source.size = 2048
    source.mime_type = "application/pdf"
    source.content = "a" * 1000  # Long content to test preview length
    source.filename = "document.pdf"
    source.tags = []
    source.inserted_at = None
    source.display_contents = {}

    chunk = Mock(spec=Chunk)
    chunk.content = "b" * 200  # Long chunk content
    chunk.relevance_score = 0.9

    result = SearchResult.from_source_item(source, [chunk], previews=True)

    assert result.id == 456
    assert result.created_at is None
    assert result.tags == []
    assert result.search_score == 0.9
    # Content should be limited by MAX_PREVIEW_LENGTH setting


def test_search_result_from_source_item_without_previews():
    """Test SearchResult creation with previews disabled (default)"""
    source = Mock(spec=SourceItem)
    source.id = 789
    source.size = None
    source.mime_type = None
    source.content = "a" * 1000  # Long content
    source.filename = None
    source.tags = None
    source.inserted_at = datetime(2024, 6, 15)
    source.display_contents = None

    chunk = Mock(spec=Chunk)
    chunk.content = "Short chunk"
    chunk.relevance_score = 0.5

    result = SearchResult.from_source_item(source, [chunk])

    assert result.id == 789
    assert result.size is None
    assert result.mime_type is None
    assert result.filename is None
    assert result.tags is None
    assert result.metadata is None
    assert result.search_score == 0.5
    # Content should be limited by MAX_NON_PREVIEW_LENGTH setting


@pytest.mark.parametrize(
    "relevance_scores,expected_total",
    [
        ([0.8, 0.6, 0.4], 1.8),
        ([1.0], 1.0),
        ([0.0, 0.0, 0.0], 0.0),
        ([], 0.0),
        ([0.33, 0.33, 0.34], 1.0),
    ],
)
def test_search_result_score_calculation(relevance_scores, expected_total):
    """Test that search scores are correctly calculated from chunk relevance scores"""
    source = Mock(spec=SourceItem)
    source.id = 1
    source.size = 100
    source.mime_type = "text/plain"
    source.content = "content"
    source.filename = "test.txt"
    source.tags = []
    source.inserted_at = datetime.now()
    source.display_contents = {}

    chunks = []
    for score in relevance_scores:
        chunk = Mock(spec=Chunk)
        chunk.content = f"chunk with score {score}"
        chunk.relevance_score = score
        chunks.append(chunk)

    result = SearchResult.from_source_item(source, chunks)
    assert result.search_score == expected_total


def test_search_result_chunk_content_elision():
    """Test that chunk content is properly elided in SearchResult"""
    source = Mock(spec=SourceItem)
    source.id = 1
    source.size = 100
    source.mime_type = "text/plain"
    source.content = "content"
    source.filename = "test.txt"
    source.tags = []
    source.inserted_at = datetime.now()
    source.display_contents = {}

    # Create chunk with very long content
    chunk = Mock(spec=Chunk)
    chunk.content = "a" * 1000  # Very long content
    chunk.relevance_score = 0.5

    result = SearchResult.from_source_item(source, [chunk])

    # Chunk content should be elided to DEFAULT_CHUNK_TOKENS * 4 characters
    # Since DEFAULT_CHUNK_TOKENS is typically small, content should be elided
    assert len(result.chunks[0]) < 1000
    assert result.chunks[0].endswith("...")