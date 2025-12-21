"""
Tests for search module functions including RRF fusion, query term boosting,
title boosting, and source deduplication.
"""

import pytest
from unittest.mock import MagicMock, patch

from datetime import datetime, timedelta, timezone

from memory.api.search.search import (
    extract_query_terms,
    apply_query_term_boost,
    deduplicate_by_source,
    apply_title_boost,
    apply_popularity_boost,
    apply_source_boosts,
    fuse_scores_rrf,
)
from memory.api.search.constants import (
    STOPWORDS,
    QUERY_TERM_BOOST,
    TITLE_MATCH_BOOST,
    POPULARITY_BOOST,
    RECENCY_BOOST_MAX,
    RECENCY_HALF_LIFE_DAYS,
    RRF_K,
)


# ============================================================================
# extract_query_terms tests
# ============================================================================


@pytest.mark.parametrize(
    "query,expected",
    [
        ("machine learning algorithms", {"machine", "learning", "algorithms"}),
        ("MACHINE Learning ALGORITHMS", {"machine", "learning", "algorithms"}),
        ("", set()),
        ("the is a an of to", set()),  # Only stopwords
    ],
)
def test_extract_query_terms_basic(query, expected):
    """Should extract meaningful terms, lowercase them, and filter stopwords."""
    assert extract_query_terms(query) == expected


@pytest.mark.parametrize(
    "query,must_include,must_exclude",
    [
        (
            "the quick brown fox jumps with the lazy dog",
            {"quick", "brown", "jumps", "lazy", "fox", "dog"},
            {"the", "with"},
        ),
        (
            "what is the best approach for neural networks",
            {"best", "approach", "neural", "networks"},
            {"what", "the", "for"},
        ),
    ],
)
def test_extract_query_terms_filtering(query, must_include, must_exclude):
    """Should filter stopwords while keeping meaningful terms."""
    terms = extract_query_terms(query)
    for term in must_include:
        assert term in terms, f"'{term}' should be in terms"
    for term in must_exclude:
        assert term not in terms, f"'{term}' should not be in terms"


@pytest.mark.parametrize(
    "query,excluded",
    [
        ("AI is a new ML model", {"ai", "is", "a", "ml"}),  # Short words filtered
    ],
)
def test_extract_query_terms_short_words(query, excluded):
    """Should filter words with 2 or fewer characters."""
    terms = extract_query_terms(query)
    for term in excluded:
        assert term not in terms


@pytest.mark.parametrize(
    "word",
    ["the", "is", "are", "was", "were", "be", "been", "have", "has", "had",
     "do", "does", "did", "to", "of", "in", "for", "on", "with", "at", "by"],
)
def test_common_stopwords_in_set(word):
    """Verify common stopwords are in the STOPWORDS set."""
    assert word in STOPWORDS


# ============================================================================
# apply_query_term_boost tests
# ============================================================================


def _make_chunk(content: str, source_id: int = 1, score: float = 0.5):
    """Create a mock chunk with given content and score."""
    chunk = MagicMock()
    chunk.content = content
    chunk.source_id = source_id
    chunk.relevance_score = score
    return chunk


@pytest.mark.parametrize(
    "content,query_terms,initial_score,expected_boost_fraction",
    [
        ("machine learning is powerful", {"machine", "learning"}, 0.5, 1.0),  # Both match
        ("machine vision systems", {"machine", "learning"}, 0.5, 0.5),  # One of two
        ("deep neural networks", {"machine", "learning"}, 0.5, 0.0),  # No match
        ("MACHINE Learning AlGoRiThMs", {"machine", "learning", "algorithms"}, 0.5, 1.0),  # Case insensitive
    ],
)
def test_apply_query_term_boost(content, query_terms, initial_score, expected_boost_fraction):
    """Should boost chunks based on query term matches."""
    chunks = [_make_chunk(content, score=initial_score)]
    apply_query_term_boost(chunks, query_terms)
    expected = initial_score + QUERY_TERM_BOOST * expected_boost_fraction
    assert chunks[0].relevance_score == pytest.approx(expected)


def test_apply_query_term_boost_empty_inputs():
    """Should handle empty query_terms or chunks."""
    chunks = [_make_chunk("machine learning", score=0.5)]
    apply_query_term_boost(chunks, set())
    assert chunks[0].relevance_score == 0.5

    apply_query_term_boost([], {"machine"})  # Should not raise


def test_apply_query_term_boost_none_values():
    """Should handle None content and relevance_score."""
    chunk_none_content = MagicMock()
    chunk_none_content.content = None
    chunk_none_content.relevance_score = 0.5
    apply_query_term_boost([chunk_none_content], {"machine"})
    assert chunk_none_content.relevance_score == 0.5

    chunk_none_score = MagicMock()
    chunk_none_score.content = "machine learning"
    chunk_none_score.relevance_score = None
    apply_query_term_boost([chunk_none_score], {"machine", "learning"})
    assert chunk_none_score.relevance_score == pytest.approx(QUERY_TERM_BOOST)


def test_apply_query_term_boost_multiple_chunks():
    """Should boost each chunk independently."""
    chunks = [
        _make_chunk("machine learning", score=0.5),
        _make_chunk("deep networks", score=0.6),
        _make_chunk("machine vision", score=0.4),
    ]
    query_terms = {"machine", "learning"}
    apply_query_term_boost(chunks, query_terms)

    assert chunks[0].relevance_score == pytest.approx(0.5 + QUERY_TERM_BOOST)
    assert chunks[1].relevance_score == 0.6  # No match
    assert chunks[2].relevance_score == pytest.approx(0.4 + QUERY_TERM_BOOST * 0.5)


# ============================================================================
# deduplicate_by_source tests
# ============================================================================


def _make_source_chunk(source_id: int, score: float):
    """Create a mock chunk with given source_id and score."""
    chunk = MagicMock()
    chunk.source_id = source_id
    chunk.relevance_score = score
    return chunk


@pytest.mark.parametrize(
    "chunks_data,expected_count,expected_scores",
    [
        # Multiple chunks per source - keep highest
        ([(1, 0.5), (1, 0.8), (1, 0.3), (2, 0.6)], 2, {1: 0.8, 2: 0.6}),
        # Single chunk per source - keep all
        ([(1, 0.5), (2, 0.6), (3, 0.7)], 3, {1: 0.5, 2: 0.6, 3: 0.7}),
        # Empty list
        ([], 0, {}),
    ],
)
def test_deduplicate_by_source(chunks_data, expected_count, expected_scores):
    """Should keep only highest scoring chunk per source."""
    chunks = [_make_source_chunk(sid, score) for sid, score in chunks_data]
    result = deduplicate_by_source(chunks)

    assert len(result) == expected_count
    for chunk in result:
        assert chunk.relevance_score == expected_scores[chunk.source_id]


def test_deduplicate_by_source_preserves_objects():
    """Should return the actual chunk objects, not copies."""
    chunk1 = _make_source_chunk(1, 0.5)
    chunk2 = _make_source_chunk(1, 0.8)
    result = deduplicate_by_source([chunk1, chunk2])
    assert result[0] is chunk2


def test_deduplicate_by_source_none_scores():
    """Should handle None relevance_score as 0."""
    chunk1 = _make_source_chunk(1, None)
    chunk2 = _make_source_chunk(1, 0.5)
    result = deduplicate_by_source([chunk1, chunk2])
    assert result[0].relevance_score == 0.5


# ============================================================================
# apply_title_boost tests
# ============================================================================


def _make_title_chunk(source_id: int, score: float = 0.5):
    """Create a mock chunk for title boost tests."""
    chunk = MagicMock()
    chunk.source_id = source_id
    chunk.relevance_score = score
    return chunk


@pytest.mark.parametrize(
    "title,query_terms,initial_score,expected_boost_fraction",
    [
        ("Machine Learning Tutorial", {"machine", "learning"}, 0.5, 1.0),
        ("Machine Vision Systems", {"machine", "learning"}, 0.5, 0.5),
        ("Deep Neural Networks", {"machine", "learning"}, 0.5, 0.0),
        ("MACHINE LEARNING Tutorial", {"machine", "learning"}, 0.5, 1.0),  # Case insensitive
    ],
)
@patch("memory.api.search.search.make_session")
def test_apply_title_boost(mock_make_session, title, query_terms, initial_score, expected_boost_fraction):
    """Should boost chunks when title matches query terms."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = MagicMock(return_value=None)

    mock_source = MagicMock()
    mock_source.id = 1
    mock_source.title = title
    mock_source.popularity = 1.0  # Default popularity, no boost
    mock_source.inserted_at = None  # No recency boost
    mock_session.query.return_value.filter.return_value.all.return_value = [mock_source]

    chunks = [_make_title_chunk(1, initial_score)]
    apply_title_boost(chunks, query_terms)

    expected = initial_score + TITLE_MATCH_BOOST * expected_boost_fraction
    assert chunks[0].relevance_score == pytest.approx(expected)


def test_apply_title_boost_empty_inputs():
    """Should not modify chunks if query_terms or chunks is empty."""
    chunks = [_make_title_chunk(1, 0.5)]
    apply_title_boost(chunks, set())
    assert chunks[0].relevance_score == 0.5

    apply_title_boost([], {"machine"})  # Should not raise


@patch("memory.api.search.search.make_session")
def test_apply_title_boost_none_title(mock_make_session):
    """Should handle sources with None or missing title."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = MagicMock(return_value=None)

    # Source with None title
    mock_source = MagicMock()
    mock_source.id = 1
    mock_source.title = None
    mock_source.popularity = 1.0  # Default popularity, no boost
    mock_source.inserted_at = None  # No recency boost
    mock_session.query.return_value.filter.return_value.all.return_value = [mock_source]

    chunks = [_make_title_chunk(1, 0.5)]
    apply_title_boost(chunks, {"machine"})
    assert chunks[0].relevance_score == 0.5


# ============================================================================
# apply_popularity_boost tests
# ============================================================================


def _make_pop_chunk(source_id: int, score: float = 0.5):
    """Create a mock chunk for popularity boost tests."""
    chunk = MagicMock()
    chunk.source_id = source_id
    chunk.relevance_score = score
    return chunk


@pytest.mark.parametrize(
    "popularity,initial_score,expected_multiplier",
    [
        (1.0, 0.5, 1.0),  # Default popularity, no change
        (2.0, 0.5, 1.0 + POPULARITY_BOOST),  # High popularity
        (0.5, 0.5, 1.0 - POPULARITY_BOOST * 0.5),  # Low popularity
        (1.5, 1.0, 1.0 + POPULARITY_BOOST * 0.5),  # Moderate popularity
    ],
)
@patch("memory.api.search.search.make_session")
def test_apply_popularity_boost(mock_make_session, popularity, initial_score, expected_multiplier):
    """Should boost chunks based on source popularity."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = MagicMock(return_value=None)

    mock_source = MagicMock()
    mock_source.id = 1
    mock_source.popularity = popularity
    mock_source.inserted_at = None  # No recency boost
    mock_session.query.return_value.filter.return_value.all.return_value = [mock_source]

    chunks = [_make_pop_chunk(1, initial_score)]
    apply_popularity_boost(chunks)

    expected = initial_score * expected_multiplier
    assert chunks[0].relevance_score == pytest.approx(expected)


def test_apply_popularity_boost_empty_chunks():
    """Should handle empty chunks list."""
    apply_popularity_boost([])  # Should not raise


@patch("memory.api.search.search.make_session")
def test_apply_popularity_boost_multiple_sources(mock_make_session):
    """Should apply different boosts per source."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = MagicMock(return_value=None)

    source1 = MagicMock()
    source1.id = 1
    source1.popularity = 2.0  # High karma
    source1.inserted_at = None  # No recency boost
    source2 = MagicMock()
    source2.id = 2
    source2.popularity = 1.0  # Default
    source2.inserted_at = None  # No recency boost
    mock_session.query.return_value.filter.return_value.all.return_value = [source1, source2]

    chunks = [_make_pop_chunk(1, 0.5), _make_pop_chunk(2, 0.5)]
    apply_popularity_boost(chunks)

    # Source 1 should be boosted
    assert chunks[0].relevance_score == pytest.approx(0.5 * (1.0 + POPULARITY_BOOST))
    # Source 2 should be unchanged (popularity = 1.0)
    assert chunks[1].relevance_score == 0.5


# ============================================================================
# fuse_scores_rrf tests
# ============================================================================


@pytest.mark.parametrize(
    "embedding_scores,bm25_scores,expected_key,expected_score",
    [
        # Both sources have same ranking
        ({"a": 0.9, "b": 0.7}, {"a": 0.8, "b": 0.6}, "a", 2 / (RRF_K + 1)),
        # Item only in embeddings
        ({"a": 0.9, "b": 0.7}, {"a": 0.8}, "b", 1 / (RRF_K + 2)),
        # Item only in BM25
        ({"a": 0.9}, {"a": 0.8, "b": 0.7}, "b", 1 / (RRF_K + 2)),
        # Single item in both
        ({"a": 0.9}, {"a": 0.8}, "a", 2 / (RRF_K + 1)),
    ],
)
def test_fuse_scores_rrf_basic(embedding_scores, bm25_scores, expected_key, expected_score):
    """Should compute RRF scores correctly."""
    result = fuse_scores_rrf(embedding_scores, bm25_scores)
    assert result[expected_key] == pytest.approx(expected_score)


def test_fuse_scores_rrf_different_rankings():
    """Should handle items ranked differently in each source."""
    embedding_scores = {"a": 0.9, "b": 0.5}  # a=1, b=2
    bm25_scores = {"a": 0.3, "b": 0.8}  # b=1, a=2

    result = fuse_scores_rrf(embedding_scores, bm25_scores)

    # Both should have same RRF score (1/61 + 1/62)
    expected = 1 / (RRF_K + 1) + 1 / (RRF_K + 2)
    assert result["a"] == pytest.approx(expected)
    assert result["b"] == pytest.approx(expected)


@pytest.mark.parametrize(
    "embedding_scores,bm25_scores,expected_len",
    [
        ({}, {}, 0),
        ({}, {"a": 0.8, "b": 0.6}, 2),
        ({"a": 0.9, "b": 0.7}, {}, 2),
    ],
)
def test_fuse_scores_rrf_empty_inputs(embedding_scores, bm25_scores, expected_len):
    """Should handle empty inputs gracefully."""
    result = fuse_scores_rrf(embedding_scores, bm25_scores)
    assert len(result) == expected_len


def test_fuse_scores_rrf_many_items():
    """Should handle many items correctly."""
    embedding_scores = {str(i): 1.0 - i * 0.01 for i in range(100)}
    bm25_scores = {str(i): 1.0 - i * 0.01 for i in range(100)}

    result = fuse_scores_rrf(embedding_scores, bm25_scores)

    assert len(result) == 100
    assert result["0"] > result["99"]  # First should have highest score


def test_fuse_scores_rrf_only_ranks_matter():
    """RRF should only care about ranks, not score magnitudes."""
    # Same ranking, different score scales
    result1 = fuse_scores_rrf(
        {"a": 0.99, "b": 0.98, "c": 0.97},
        {"a": 100, "b": 50, "c": 1},
    )
    result2 = fuse_scores_rrf(
        {"a": 0.5, "b": 0.4, "c": 0.3},
        {"a": 0.9, "b": 0.8, "c": 0.7},
    )

    # RRF scores should be identical since rankings are the same
    assert result1["a"] == pytest.approx(result2["a"])
    assert result1["b"] == pytest.approx(result2["b"])
    assert result1["c"] == pytest.approx(result2["c"])


# ============================================================================
# apply_source_boosts recency tests
# ============================================================================


def _make_recency_chunk(source_id: int, score: float = 0.5):
    """Create a mock chunk for recency boost tests."""
    chunk = MagicMock()
    chunk.source_id = source_id
    chunk.relevance_score = score
    return chunk


@patch("memory.api.search.search.make_session")
def test_recency_boost_new_content(mock_make_session):
    """Brand new content should get full recency boost."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = MagicMock(return_value=None)

    now = datetime.now(timezone.utc)
    mock_source = MagicMock()
    mock_source.id = 1
    mock_source.title = None
    mock_source.popularity = 1.0
    mock_source.inserted_at = now  # Just inserted
    mock_session.query.return_value.filter.return_value.all.return_value = [mock_source]

    chunks = [_make_recency_chunk(1, 0.5)]
    apply_source_boosts(chunks, set())

    # Should get nearly full recency boost
    expected = 0.5 + RECENCY_BOOST_MAX
    assert chunks[0].relevance_score == pytest.approx(expected, rel=0.01)


@patch("memory.api.search.search.make_session")
def test_recency_boost_half_life_decay(mock_make_session):
    """Content at half-life age should get half the boost."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = MagicMock(return_value=None)

    now = datetime.now(timezone.utc)
    mock_source = MagicMock()
    mock_source.id = 1
    mock_source.title = None
    mock_source.popularity = 1.0
    mock_source.inserted_at = now - timedelta(days=RECENCY_HALF_LIFE_DAYS)
    mock_session.query.return_value.filter.return_value.all.return_value = [mock_source]

    chunks = [_make_recency_chunk(1, 0.5)]
    apply_source_boosts(chunks, set())

    # Should get half the recency boost
    expected = 0.5 + RECENCY_BOOST_MAX * 0.5
    assert chunks[0].relevance_score == pytest.approx(expected, rel=0.01)


@patch("memory.api.search.search.make_session")
def test_recency_boost_old_content(mock_make_session):
    """Very old content should get minimal recency boost."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = MagicMock(return_value=None)

    now = datetime.now(timezone.utc)
    mock_source = MagicMock()
    mock_source.id = 1
    mock_source.title = None
    mock_source.popularity = 1.0
    mock_source.inserted_at = now - timedelta(days=365)  # 1 year old
    mock_session.query.return_value.filter.return_value.all.return_value = [mock_source]

    chunks = [_make_recency_chunk(1, 0.5)]
    apply_source_boosts(chunks, set())

    # Should get very little boost (about 0.5^4 ≈ 0.0625 of max)
    assert chunks[0].relevance_score > 0.5
    assert chunks[0].relevance_score < 0.5 + RECENCY_BOOST_MAX * 0.1


@patch("memory.api.search.search.make_session")
def test_recency_boost_none_timestamp(mock_make_session):
    """Should handle None inserted_at gracefully."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = MagicMock(return_value=None)

    mock_source = MagicMock()
    mock_source.id = 1
    mock_source.title = None
    mock_source.popularity = 1.0
    mock_source.inserted_at = None
    mock_session.query.return_value.filter.return_value.all.return_value = [mock_source]

    chunks = [_make_recency_chunk(1, 0.5)]
    apply_source_boosts(chunks, set())

    # No recency boost applied
    assert chunks[0].relevance_score == 0.5


@patch("memory.api.search.search.make_session")
def test_recency_boost_timezone_naive(mock_make_session):
    """Should handle timezone-naive timestamps."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = MagicMock(return_value=None)

    # Timezone-naive timestamp
    naive_dt = datetime.now().replace(tzinfo=None)
    mock_source = MagicMock()
    mock_source.id = 1
    mock_source.title = None
    mock_source.popularity = 1.0
    mock_source.inserted_at = naive_dt
    mock_session.query.return_value.filter.return_value.all.return_value = [mock_source]

    chunks = [_make_recency_chunk(1, 0.5)]
    apply_source_boosts(chunks, set())  # Should not raise

    # Should get nearly full boost since it's very recent
    assert chunks[0].relevance_score > 0.5


@patch("memory.api.search.search.make_session")
def test_recency_boost_ordering(mock_make_session):
    """Newer content should rank higher than older content."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = MagicMock(return_value=None)

    now = datetime.now(timezone.utc)
    source_new = MagicMock()
    source_new.id = 1
    source_new.title = None
    source_new.popularity = 1.0
    source_new.inserted_at = now - timedelta(days=1)

    source_old = MagicMock()
    source_old.id = 2
    source_old.title = None
    source_old.popularity = 1.0
    source_old.inserted_at = now - timedelta(days=180)

    mock_session.query.return_value.filter.return_value.all.return_value = [source_new, source_old]

    chunks = [_make_recency_chunk(1, 0.5), _make_recency_chunk(2, 0.5)]
    apply_source_boosts(chunks, set())

    # Newer content should have higher score
    assert chunks[0].relevance_score > chunks[1].relevance_score
