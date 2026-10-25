"""
Tests for search module functions including RRF fusion, query term boosting,
title boosting, and source deduplication.
"""

import asyncio
import importlib
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.agents.provider_keys import credential
from memory.api.search import hyde
from memory.api.search.query_analysis import QueryAnalysis
from memory.api.search.types import SearchConfig
from memory.common import settings
from memory.common.db.models import Chunk
from memory.common.extract import DataChunk
from tests.conftest import make_source_item
from tests.memory.agents.fakes import reply
from memory.api.search.constants import (
    POPULARITY_BOOST,
    QUERY_TERM_BOOST,
    RECENCY_BOOST_MAX,
    RECENCY_HALF_LIFE_DAYS,
    RRF_K,
    STOPWORDS,
    SUMMARY_MATCH_BOOST,
    TITLE_MATCH_BOOST,
)
from memory.api.search.search import (
    apply_query_term_boost,
    apply_source_boosts,
    extract_query_terms,
    fuse_scores_rrf,
)

# memory.api.search re-exports the `search` function under the submodule's name.
search_module = importlib.import_module("memory.api.search.search")


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
    "query,included,excluded",
    [
        # 2-letter terms like "ai", "ml" should be INCLUDED (important acronyms)
        # 1-letter words like "a" and stopwords like "is" should be excluded
        ("AI is a new ML model", {"ai", "ml", "new", "model"}, {"is", "a"}),
    ],
)
def test_extract_query_terms_short_words(query, included, excluded):
    """Should include 2-letter words but filter 1-letter words and stopwords."""
    terms = extract_query_terms(query)
    for term in included:
        assert term in terms, f"'{term}' should be in terms"
    for term in excluded:
        assert term not in terms, f"'{term}' should not be in terms"


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


def _make_chunk(content: str | None, source_id: int = 1, score: float = 0.5) -> Chunk:
    return Chunk(content=content, source_id=source_id, relevance_score=score)


@pytest.mark.parametrize(
    "content,query_terms,expected_boost_fraction",
    [
        ("machine learning is powerful", {"machine", "learning"}, 1.0),  # Both match
        ("machine vision systems", {"machine", "learning"}, 0.5),  # One of two
        ("deep neural networks", {"machine", "learning"}, 0.0),  # No match
        ("MACHINE Learning AlGoRiThMs", {"machine", "learning", "algorithms"}, 1.0),  # Case insensitive
        (None, {"machine"}, 0.0),
    ],
)
def test_apply_query_term_boost(content, query_terms, expected_boost_fraction):
    """Query terms in the content add to the chunk's boost, not to its score."""
    chunk = _make_chunk(content)
    apply_query_term_boost([chunk], query_terms)
    assert (chunk.relevance_score, chunk.boost) == pytest.approx(
        (0.5, QUERY_TERM_BOOST * expected_boost_fraction)
    )


def test_apply_query_term_boost_empty_inputs():
    """Should handle empty query_terms or chunks."""
    chunks = [_make_chunk("machine learning")]
    apply_query_term_boost(chunks, set())
    assert chunks[0].boost == 0.0

    apply_query_term_boost([], {"machine"})  # Should not raise


def test_summary_chunks_are_boosted_above_the_items_other_chunks(db_session):
    source = make_source_item(db_session, 1)
    summary = Chunk(source_id=source.id, content="text", relevance_score=0.5, is_summary=True)
    section = Chunk(source_id=source.id, content="text", relevance_score=0.5)

    search_module._apply_boosts([summary, section], [], None)

    assert summary.boost - section.boost == pytest.approx(SUMMARY_MATCH_BOOST)


# ============================================================================
# apply_source_boosts tests (title + popularity + recency)
# ============================================================================


def _make_boost_chunk(source_id: int) -> Chunk:
    return Chunk(source_id=source_id, content="text", relevance_score=0.5)


@pytest.mark.parametrize(
    "title,query_terms,expected_boost_fraction",
    [
        ("Machine Learning Tutorial", {"machine", "learning"}, 1.0),
        ("Machine Vision Systems", {"machine", "learning"}, 0.5),
        ("Deep Neural Networks", {"machine", "learning"}, 0.0),
        ("MACHINE LEARNING Tutorial", {"machine", "learning"}, 1.0),  # Case insensitive
        (None, {"machine"}, 0.0),
    ],
)
@patch("memory.api.search.search.make_session")
def test_apply_source_boosts_title(mock_make_session, title, query_terms, expected_boost_fraction):
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

    chunks = [_make_boost_chunk(1)]
    apply_source_boosts(chunks, query_terms)

    assert chunks[0].boost == pytest.approx(TITLE_MATCH_BOOST * expected_boost_fraction)
    assert chunks[0].relevance_score == 0.5


@patch("memory.api.search.search.make_session")
def test_apply_source_boosts_empty_inputs(mock_make_session):
    """Should not modify chunks if query_terms or chunks is empty."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = MagicMock(return_value=None)
    mock_session.query.return_value.filter.return_value.all.return_value = []

    chunks = [_make_boost_chunk(1)]
    apply_source_boosts(chunks, set())
    assert chunks[0].boost == 0.0

    apply_source_boosts([], {"machine"})  # Should not raise


@pytest.mark.parametrize(
    "popularity,expected_boost",
    [
        (1.0, 0.0),  # Default popularity, no change
        (2.0, POPULARITY_BOOST),
        (0.5, -POPULARITY_BOOST * 0.5),  # Low popularity, small penalty
        (1.5, POPULARITY_BOOST * 0.5),
    ],
)
@patch("memory.api.search.search.make_session")
def test_apply_source_boosts_popularity(mock_make_session, popularity, expected_boost):
    """Should boost chunks based on source popularity."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_make_session.return_value.__exit__ = MagicMock(return_value=None)

    mock_source = MagicMock()
    mock_source.id = 1
    mock_source.popularity = popularity
    mock_source.inserted_at = None  # No recency boost
    mock_session.query.return_value.filter.return_value.all.return_value = [mock_source]

    chunks = [_make_boost_chunk(1)]
    apply_source_boosts(chunks, set())  # No query terms, just popularity

    assert chunks[0].boost == pytest.approx(expected_boost)


def test_apply_source_boosts_empty_chunks():
    """Should handle empty chunks list."""
    apply_source_boosts([], set())  # Should not raise


@patch("memory.api.search.search.make_session")
def test_apply_source_boosts_multiple_sources(mock_make_session):
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

    chunks = [_make_boost_chunk(1), _make_boost_chunk(2)]
    apply_source_boosts(chunks, set())

    assert [c.boost for c in chunks] == pytest.approx([POPULARITY_BOOST, 0.0])


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

    chunks = [_make_boost_chunk(1)]
    apply_source_boosts(chunks, set())

    assert chunks[0].boost == pytest.approx(RECENCY_BOOST_MAX, rel=0.01)


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

    chunks = [_make_boost_chunk(1)]
    apply_source_boosts(chunks, set())

    assert chunks[0].boost == pytest.approx(RECENCY_BOOST_MAX * 0.5, rel=0.01)


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

    chunks = [_make_boost_chunk(1)]
    apply_source_boosts(chunks, set())

    # Should get very little boost (about 0.5^4 ≈ 0.0625 of max)
    assert 0 < chunks[0].boost < RECENCY_BOOST_MAX * 0.1


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

    chunks = [_make_boost_chunk(1)]
    apply_source_boosts(chunks, set())

    assert chunks[0].boost == 0.0


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

    chunks = [_make_boost_chunk(1)]
    apply_source_boosts(chunks, set())  # Should not raise

    assert chunks[0].boost == pytest.approx(RECENCY_BOOST_MAX, rel=0.01)


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

    chunks = [_make_boost_chunk(1), _make_boost_chunk(2)]
    apply_source_boosts(chunks, set())

    assert chunks[0].boost > chunks[1].boost


# =============================================================================
# Defense-in-depth regression: search_sources final-merge access filter
# =============================================================================


def test_search_sources_applies_final_access_filter_when_filters_provided():
    """When ``filters['access_filter']`` is provided, search_sources must
    apply it. This is the third layer of the documented three-layer
    defense-in-depth (Qdrant payload + BM25 SQL + final merge).

    Pre-fix the final-merge query had no filter: a regression in the
    upstream Qdrant/BM25 layer would leak SourceItem rows here.
    """

    # Sidestep the package-shadowing trap: the package's __init__ does
    # ``from .search import search``, which rebinds ``memory.api.search.search``
    # to the function. The submodule is still importable as a string key.
    search_module = sys.modules["memory.api.search.search"]

    # A chunk-shaped stub. search_sources only reads ``source_id`` from
    # each chunk, so a MagicMock with that attribute is sufficient.
    chunk = MagicMock()
    chunk.source_id = 42

    fake_filter_arg_seen = []
    fake_query = MagicMock()
    fake_query.filter.return_value = fake_query
    # Simulate "the access filter dropped every row"
    fake_query.all.return_value = []

    fake_db = MagicMock()
    fake_db.__enter__ = lambda self: fake_db
    fake_db.__exit__ = lambda *args: False
    fake_db.query.return_value = fake_query

    sentinel_access_filter = MagicMock(name="sentinel_access_filter")

    def fake_apply(query, access_filter):
        fake_filter_arg_seen.append(access_filter)
        # Simulate the access filter further restricting; return a query
        # whose .all() yields nothing.
        return query

    with (
        patch(
            "memory.api.search.search.make_session",
            return_value=fake_db,
        ),
        patch(
            "memory.api.search.search.apply_access_filter_to_query",
            side_effect=fake_apply,
        ) as mock_apply,
    ):
        result = asyncio.run(
            search_module.search_sources(
                chunks=[chunk],
                previews=False,
                filters={"access_filter": sentinel_access_filter},  # type: ignore
            )
        )

    assert result == []
    # The same access_filter the caller passed is what we applied.
    mock_apply.assert_called_once()
    assert fake_filter_arg_seen == [sentinel_access_filter]


def test_search_sources_fails_closed_when_filters_missing():
    """Regression for Parvati's IMPORTANT comment on PR #77: the previous
    "DEBUG-log-and-trust-upstream" fallback let a caller that forgot to
    thread filters silently bypass the final-merge access check —
    defense-in-depth that's only effective when callers remember it
    isn't defense-in-depth, it's a hint.

    Now ``search_sources`` raises ValueError when ``filters`` is None or
    has no ``access_filter`` key. Callers that legitimately want admin
    access pass ``filters={'access_filter': None}`` explicitly.
    """

    search_module = sys.modules["memory.api.search.search"]

    chunk = MagicMock()
    chunk.source_id = 42

    # filters=None: must raise.
    try:
        asyncio.run(search_module.search_sources(chunks=[chunk], previews=False))
    except ValueError as e:
        assert "access_filter" in str(e)
    else:
        raise AssertionError("search_sources(filters=None) should have raised")

    # filters={}: also missing access_filter key — must raise.
    try:
        asyncio.run(
            search_module.search_sources(
                chunks=[chunk], previews=False, filters={}
            )
        )
    except ValueError as e:
        assert "access_filter" in str(e)
    else:
        raise AssertionError("search_sources(filters={}) should have raised")


def test_search_sources_show_long_items_by_their_summary(db_session, monkeypatch):
    monkeypatch.setattr(settings, "MAX_NON_PREVIEW_LENGTH", 12)
    summarised = make_source_item(db_session, 1, content="a" * 20)
    plain = make_source_item(db_session, 2, content="b" * 20)
    hit = Chunk(source_id=summarised.id, content="a section", is_summary=False)
    other_hit = Chunk(source_id=plain.id, content="a section", is_summary=False)
    summary = Chunk(source_id=summarised.id, content="the summary", is_summary=True)
    db_session.add_all([hit, other_hit, summary])
    db_session.commit()

    results = asyncio.run(
        search_module.search_sources([hit, other_hit], filters={"access_filter": None})
    )

    assert {r.id: r.content for r in results} == {
        summarised.id: "the summary",
        plain.id: "b" * 12 + "...",
    }


def test_search_sources_empty_chunks_returns_empty():
    """Empty input must short-circuit before hitting the DB. The
    fail-closed filter check still happens (so callers must pass filters
    even with empty chunks) but the empty-list short-circuit fires
    immediately after, so we never query."""

    search_module = sys.modules["memory.api.search.search"]

    with patch("memory.api.search.search.make_session") as mock_session:
        result = asyncio.run(
            search_module.search_sources(
                chunks=[],
                previews=False,
                filters={"access_filter": None},
            )
        )

    assert result == []
    mock_session.assert_not_called()


# ============================================================================
# _fetch_chunks_by_title — fail-closed on missing access_filter
# ============================================================================
#
# The title-match fetch is the fourth reader of SourceItem/Chunk in this
# pipeline. Before this gate, a caller that forgot to thread access_filter
# silently fell through to an unfiltered query — and because _run_searches
# wraps the embedding/BM25 calls in asyncio.gather(return_exceptions=True),
# their ValueError got swallowed into empty scores and execution still
# reached the title-match path with LLM-supplied titles driving an
# unfiltered match. These tests pin the gate.


def test_fetch_chunks_by_title_raises_on_none_filters():
    from memory.api.search.search import _fetch_chunks_by_title

    with pytest.raises(ValueError, match="`access_filter`"):
        _fetch_chunks_by_title(["some title"], {"text"}, None)


def test_fetch_chunks_by_title_raises_on_missing_key():
    from memory.api.search.search import _fetch_chunks_by_title

    with pytest.raises(ValueError, match="`access_filter`"):
        _fetch_chunks_by_title(["some title"], {"text"}, {"person_id": 5})  # type: ignore[arg-type]


def test_fetch_chunks_by_title_early_returns_skip_gate():
    """No titles / no modalities short-circuits before the gate would fire.

    The function returns ``{}`` without inspecting filters when there's
    no work to do — useful because callers in _run_searches always pass
    valid filters but may pass empty titles/modalities.
    """
    from memory.api.search.search import _fetch_chunks_by_title

    assert _fetch_chunks_by_title([], {"text"}, None) == {}
    assert _fetch_chunks_by_title(["t"], set(), None) == {}


def test_fetch_chunks_by_title_accepts_explicit_none():
    """``access_filter=None`` is the explicit superadmin opt-in."""
    from memory.api.search.search import _fetch_chunks_by_title

    with patch("memory.api.search.search.make_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.limit.return_value.all.return_value = []

        result = _fetch_chunks_by_title(
            ["some title"], {"text"}, {"access_filter": None}
        )

    assert result == {}
    mock_session.assert_called_once()


# ============================================================================
# Relative-date queries
# ============================================================================

FIXED_NOW = datetime(2026, 10, 22, 3, 30, tzinfo=timezone.utc)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW if tz is None else FIXED_NOW.astimezone(tz)


def dated_analysis(start: date | None = None, end: date | None = None) -> QueryAnalysis:
    return QueryAnalysis(cleaned_query="alerts", start_date=start, end_date=end, success=True)


async def run_dated_search(analysis: QueryAnalysis, filters: dict, ids_in_range: list[int]):
    analyze = AsyncMock(return_value=analysis)
    in_range = MagicMock(return_value=ids_in_range)
    embeddings = AsyncMock(return_value={})
    bm25 = AsyncMock(return_value={})
    with (
        patch.object(search_module, "datetime", FixedDatetime),
        patch.object(search_module, "analyze_query", analyze),
        patch.object(search_module, "search_chunks_embeddings", embeddings),
        patch.object(search_module, "search_bm25_chunks", bm25),
        patch.object(search_module, "source_ids_in_range", in_range),
        patch.object(search_module, "make_session"),
        patch.object(settings, "ENABLE_BM25_SEARCH", True),
        patch.object(settings, "ENABLE_QUERY_ANALYSIS", True),
    ):
        await search_module.search_chunks(
            [DataChunk(data=["what happened yesterday"])],
            modalities={"mail"},
            filters=filters,
            config=SearchConfig(
                useBm25=True, useHyde=False, useReranking=False, useQueryAnalysis=True
            ),
        )
    seen = {"embeddings": embeddings.call_args.args[3], "bm25": bm25.call_args.args[3]}
    return analyze, in_range, seen


@pytest.mark.asyncio
async def test_search_chunks_gives_analysis_the_search_clock():
    analyze, _, _ = await run_dated_search(dated_analysis(), {"access_filter": None}, [])

    assert analyze.call_args.kwargs["now"] == FIXED_NOW


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing_ids, ids_in_range, expected",
    [
        (None, [3, 1, 2], [1, 2, 3]),
        ([2, 3, 4], [1, 2, 3], [2, 3]),
    ],
)
async def test_search_chunks_restricts_both_arms_to_date_range(
    existing_ids, ids_in_range, expected
):
    filters = {"access_filter": None, "source_ids": existing_ids}
    _, in_range, seen = await run_dated_search(
        dated_analysis(date(2026, 10, 21), date(2026, 10, 21)), filters, ids_in_range
    )

    assert in_range.call_args.args[1:] == (
        datetime(2026, 10, 20, 12, tzinfo=timezone.utc),
        datetime(2026, 10, 22, 12, tzinfo=timezone.utc),
        None,
        {"mail"},
    )
    assert sorted(seen["embeddings"]["source_ids"]) == expected
    assert sorted(seen["bm25"]["source_ids"]) == expected
    assert filters["source_ids"] == existing_ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "analysis, ids_in_range",
    [
        # No temporal reference in the query
        (dated_analysis(), [1, 2]),
        # Nothing dated in range: searching everything beats returning nothing
        (dated_analysis(None, date(2026, 10, 21)), []),
    ],
)
async def test_search_chunks_leaves_filters_without_usable_date_range(analysis, ids_in_range):
    filters = {"access_filter": None, "source_ids": [7]}
    _, _, seen = await run_dated_search(analysis, filters, ids_in_range)

    assert seen["embeddings"]["source_ids"] == [7]
    assert seen["bm25"]["source_ids"] == [7]


ENHANCEMENT_SETTINGS = [
    "ENABLE_BM25_SEARCH", "ENABLE_HYDE_EXPANSION", "ENABLE_RERANKING", "ENABLE_QUERY_ANALYSIS",
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled, requested, expected",
    [
        (True, None, True),
        (True, True, True),
        (True, False, False),
        (False, None, False),
        (False, True, False),
        (False, False, False),
    ],
)
async def test_search_chunks_settings_cap_enhancements(monkeypatch, enabled, requested, expected):
    for setting in ENHANCEMENT_SETTINGS:
        monkeypatch.setattr(settings, setting, enabled)
    llm = AsyncMock(return_value=(None, None))
    searches = AsyncMock(return_value=({}, {}))
    rerank = AsyncMock(return_value=[])
    with (
        patch.object(search_module, "_run_llm_analysis", llm),
        patch.object(search_module, "restrict_to_date_range", lambda filters, *_: filters),
        patch.object(search_module, "_run_searches", searches),
        patch.object(search_module, "_fetch_chunks", MagicMock(return_value=[])),
        patch.object(search_module, "_apply_reranking", rerank),
    ):
        await search_module.search_chunks(
            [DataChunk(data=["query"])],
            filters={"access_filter": None},
            config=SearchConfig(
                useBm25=requested, useHyde=requested,
                useReranking=requested, useQueryAnalysis=requested,
            ),
        )

    use_query_analysis, use_hyde = llm.call_args.args[2:4]
    resolved = (use_query_analysis, use_hyde, searches.call_args.args[5], rerank.call_args.args[3])
    assert resolved == (expected,) * 4


@pytest.mark.asyncio
async def test_search_chunks_without_a_voyage_key_searches_keywords_only(monkeypatch):
    monkeypatch.setattr(settings, "VOYAGE_API_KEY", "")
    for setting in ENHANCEMENT_SETTINGS:
        monkeypatch.setattr(settings, setting, True)
    llm = AsyncMock(return_value=(None, None))
    searches = AsyncMock(return_value=({}, {}))
    rerank = AsyncMock(return_value=[])
    with (
        patch.object(search_module, "_run_llm_analysis", llm),
        patch.object(search_module, "restrict_to_date_range", lambda filters, *_: filters),
        patch.object(search_module, "_run_searches", searches),
        patch.object(search_module, "_fetch_chunks", MagicMock(return_value=[])),
        patch.object(search_module, "_apply_reranking", rerank),
    ):
        await search_module.search_chunks(
            [DataChunk(data=["query"])],
            filters={"access_filter": None},
            config=SearchConfig(useBm25=False),
        )

    use_hyde = llm.call_args.args[3]
    use_bm25, use_embeddings = searches.call_args.args[5], searches.call_args.kwargs["use_embeddings"]
    assert (use_bm25, use_embeddings, use_hyde, rerank.call_args.args[3]) == (True, False, False, False)


@pytest.mark.asyncio
async def test_apply_reranking_keeps_fused_order_when_rerank_stalls():
    async def stall(*args, **kwargs):
        await asyncio.sleep(60)

    chunks = [MagicMock(relevance_score=0.2, boost=0.0), MagicMock(relevance_score=0.1, boost=0.0)]
    with patch.object(search_module, "rerank_chunks", stall), \
         patch.object(settings, "RERANK_TIMEOUT", 0.01):
        result = await asyncio.wait_for(
            search_module._apply_reranking(chunks, "query", 10, True), 5
        )

    assert result == chunks


def test_searcher_keys_are_the_searchers_usable_defaults(db_session, regular_user, llm_key):
    assert set(search_module.searcher_keys(regular_user.id).values()) == {credential(llm_key)}


def test_anonymous_searches_get_no_keys():
    assert set(search_module.searcher_keys(None).values()) == {None}


def test_keyless_searchers_get_no_keys(db_session, regular_user):
    assert set(search_module.searcher_keys(regular_user.id).values()) == {None}


@pytest.mark.asyncio
@pytest.mark.parametrize("searcher_fixture, hyde_requests", [("llm_key", 1), ("regular_user", 0)])
async def test_search_writes_a_hyde_passage_only_for_a_keyed_searcher(
    db_session, regular_user, scripted_llm, monkeypatch, request, searcher_fixture, hyde_requests
):
    request.getfixturevalue(searcher_fixture)
    monkeypatch.setattr(settings, "ENABLE_HYDE_EXPANSION", True)
    monkeypatch.setattr(hyde, "_hyde_cache", {})
    monkeypatch.setattr(search_module, "search_chunks_embeddings", AsyncMock(return_value={}))
    scripted_llm.script = [reply("a passage")]

    await search_module.search(
        [DataChunk(data=["that essay about tabooing your words"])],
        filters={"access_filter": None},
        config=SearchConfig(useBm25=False, useHyde=True, useReranking=False, useQueryAnalysis=False),
        searcher_id=regular_user.id,
    )

    assert len(scripted_llm.requests) == hyde_requests


# ============================================================================
# Boosts apply to the final scores, whichever scoring ran last
# ============================================================================

LONG_AGO = datetime.now(timezone.utc) - timedelta(days=720)


def fake_voyage(scores: dict[str, float]) -> MagicMock:
    """A Voyage client whose reranker scores each document by its text, honouring top_k."""

    def rerank(query, documents, model, top_k):
        ranked = sorted(
            (SimpleNamespace(index=i, relevance_score=scores[d]) for i, d in enumerate(documents)),
            key=lambda r: r.relevance_score,
            reverse=True,
        )
        return SimpleNamespace(results=ranked[:top_k])

    voyage = MagicMock()
    voyage.Client.return_value.rerank.side_effect = rerank
    return voyage


@pytest.mark.asyncio
@pytest.mark.parametrize("use_reranking", [True, False])
@pytest.mark.parametrize(
    "overrides, winner",
    [
        ({}, "plain"),
        ({"filename": "kettle descale guide"}, "boosted"),
        ({"inserted_at": datetime.now(timezone.utc)}, "boosted"),
    ],
)
async def test_boosts_reorder_near_ties_in_the_final_scores(
    db_session, monkeypatch, use_reranking, overrides, winner
):
    monkeypatch.setattr(settings, "VOYAGE_API_KEY", "test")
    monkeypatch.setattr(settings, "ENABLE_RERANKING", True)
    plain = make_source_item(db_session, 1, filename="notes", inserted_at=LONG_AGO)
    boosted = make_source_item(
        db_session, 2, **{"filename": "notes", "inserted_at": LONG_AGO, **overrides}
    )
    leader = Chunk(source_id=plain.id, content="descale a kettle: notes")
    runner_up = Chunk(source_id=boosted.id, content="descale a kettle: guide")
    db_session.add_all([leader, runner_up])
    db_session.commit()
    fused = {str(leader.id): 0.0164, str(runner_up.id): 0.0161}
    rerank_scores = {"descale a kettle: notes": 0.60, "descale a kettle: guide": 0.59}

    with (
        patch.object(search_module, "_run_searches", AsyncMock(return_value=(fused, {}))),
        patch("memory.api.search.rerank.voyageai", fake_voyage(rerank_scores)),
    ):
        results = await search_module.search(
            [DataChunk(data=["descale kettle"])],
            filters={"access_filter": None},
            config=SearchConfig(
                limit=1, useBm25=False, useHyde=False,
                useReranking=use_reranking, useQueryAnalysis=False,
            ),
        )

    assert [r.id for r in results] == [{"plain": plain.id, "boosted": boosted.id}[winner]]
