"""
Tests for PostgreSQL full-text search (BM25) functionality.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from memory.api.search import bm25
from memory.api.search.bm25 import apply_access_filter
from memory.api.search.types import SearchFilters
from memory.common import extract
from memory.common.access_control import AccessFilter


class TestBuildTsquery:
    """Tests for build_tsquery function."""

    def test_simple_query(self):
        result = bm25.build_tsquery("hello world")
        assert result == "hello:* & world:*"

    def test_empty_query(self):
        result = bm25.build_tsquery("")
        assert result == ""

    def test_whitespace_only(self):
        result = bm25.build_tsquery("   \n\t   ")
        assert result == ""

    def test_stopwords_filtered(self):
        # All stopwords should be filtered out
        result = bm25.build_tsquery("the and or but in on at")
        assert result == ""

    def test_mixed_stopwords_and_terms(self):
        result = bm25.build_tsquery("the quick brown fox")
        # "the" is a stopword, should be filtered
        assert result == "quick:* & brown:* & fox:*"

    def test_special_characters_removed(self):
        # Special tsquery characters should be removed
        result = bm25.build_tsquery("test&query|special!chars:*<>-\"'()")
        assert result == "test:* & query:* & special:* & chars:*"

    def test_short_words_filtered(self):
        # Words < 2 chars should be filtered
        result = bm25.build_tsquery("a i go to be ok")
        # "a", "i", "to", "be" are stopwords; "go", "ok" remain
        assert result == "go:* & ok:*"

    def test_case_insensitive(self):
        result = bm25.build_tsquery("Hello WORLD Test")
        assert result == "hello:* & world:* & test:*"

    def test_prefix_wildcard_added(self):
        result = bm25.build_tsquery("program")
        assert result == "program:*"
        # Should match "program", "programs", "programming", etc.

    def test_single_character_words_ignored(self):
        result = bm25.build_tsquery("x y z test")
        assert result == "test:*"

    def test_unicode_characters(self):
        result = bm25.build_tsquery("café naïve résumé")
        assert result == "café:* & naïve:* & résumé:*"

    def test_numbers_included(self):
        result = bm25.build_tsquery("python3 v2.0 test123")
        # Dots are removed, making "v2.0" become "v2.0" as one word
        assert result == "python3:* & v2.0:* & test123:*"

    def test_email_like_query(self):
        result = bm25.build_tsquery("user@example.com")
        # Special chars (@ and .) are replaced with spaces by regex
        # but the word boundary detection keeps it as one term
        assert "user@example.com:*" in result or ("user:*" in result and "example:*" in result)

    def test_programming_terms(self):
        result = bm25.build_tsquery("async/await for loops")
        # "for" is stopword, "/" is replaced but doesn't split the word
        assert result == "async/await:* & loops:*"

    def test_hyphenated_words(self):
        result = bm25.build_tsquery("full-text search")
        assert result == "full:* & text:* & search:*"


@pytest.mark.asyncio
class TestSearchBm25:
    """Tests for search_bm25 async function."""

    async def test_empty_query_returns_empty(self):
        result = await bm25.search_bm25("", {"text"})
        assert result == {}

    async def test_stopwords_only_returns_empty(self):
        result = await bm25.search_bm25("the and or", {"text"})
        assert result == {}

    @patch("memory.api.search.bm25.make_session")
    async def test_basic_search_success(self, mock_make_session):
        # Setup mock database session
        mock_db = MagicMock()
        mock_make_session.return_value.__enter__.return_value = mock_db

        # Mock query results
        mock_item1 = MagicMock()
        mock_item1.id = "chunk1"
        mock_item1.rank = 0.8

        mock_item2 = MagicMock()
        mock_item2.id = "chunk2"
        mock_item2.rank = 0.4

        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            mock_item1,
            mock_item2,
        ]

        result = await bm25.search_bm25("test query", {"text"}, limit=10)

        # Should normalize scores to 0-1 range
        # max=0.8, min=0.4, range=0.4
        # chunk1: (0.8-0.4)/0.4 = 1.0
        # chunk2: (0.4-0.4)/0.4 = 0.0
        assert result == {"chunk1": 1.0, "chunk2": 0.0}

    @patch("memory.api.search.bm25.make_session")
    async def test_equal_scores_returns_half(self, mock_make_session):
        # When all scores are equal, should return 0.5 for all
        mock_db = MagicMock()
        mock_make_session.return_value.__enter__.return_value = mock_db

        mock_item1 = MagicMock()
        mock_item1.id = "chunk1"
        mock_item1.rank = 0.5

        mock_item2 = MagicMock()
        mock_item2.id = "chunk2"
        mock_item2.rank = 0.5

        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            mock_item1,
            mock_item2,
        ]

        result = await bm25.search_bm25("test", {"text"})

        assert result == {"chunk1": 0.5, "chunk2": 0.5}

    @patch("memory.api.search.bm25.make_session")
    async def test_no_results_returns_empty(self, mock_make_session):
        mock_db = MagicMock()
        mock_make_session.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = (
            []
        )

        result = await bm25.search_bm25("nonexistent term", {"text"})
        assert result == {}

    @patch("memory.api.search.bm25.make_session")
    async def test_filters_source_ids(self, mock_make_session):
        mock_db = MagicMock()
        mock_make_session.return_value.__enter__.return_value = mock_db
        mock_query = mock_db.query.return_value

        # Make the filter chain return empty results
        mock_query.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = (
            []
        )

        filters = SearchFilters(source_ids=[1, 2, 3])
        await bm25.search_bm25("test", {"text"}, filters=filters)

        # Verify filter was called with source_ids
        # (We can't easily verify the exact filter args with SQLAlchemy expressions,
        # but we can ensure the flow executed)
        assert mock_query.filter.called

    @patch("memory.api.search.bm25.make_session")
    async def test_filters_observation_types(self, mock_make_session):
        mock_db = MagicMock()
        mock_make_session.return_value.__enter__.return_value = mock_db
        mock_query = mock_db.query.return_value
        mock_query.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = (
            []
        )

        filters = SearchFilters(observation_types=["preference", "fact"])
        await bm25.search_bm25("test", {"text"}, filters=filters)

        assert mock_query.filter.called

    @patch("memory.api.search.bm25.make_session")
    async def test_limit_parameter(self, mock_make_session):
        mock_db = MagicMock()
        mock_make_session.return_value.__enter__.return_value = mock_db
        mock_query = mock_db.query.return_value
        mock_limit = mock_query.filter.return_value.order_by.return_value.limit
        mock_limit.return_value.all.return_value = []

        await bm25.search_bm25("test", {"text"}, limit=20)

        # Verify limit was called with 20
        mock_limit.assert_called_once_with(20)

    @patch("memory.api.search.bm25.make_session")
    async def test_zero_rank_items_excluded(self, mock_make_session):
        # Items with rank 0 should be excluded from results
        mock_db = MagicMock()
        mock_make_session.return_value.__enter__.return_value = mock_db

        mock_item1 = MagicMock()
        mock_item1.id = "chunk1"
        mock_item1.rank = 0.5

        mock_item2 = MagicMock()
        mock_item2.id = "chunk2"
        mock_item2.rank = 0  # Zero rank, should be excluded

        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            mock_item1,
            mock_item2,
        ]

        result = await bm25.search_bm25("test", {"text"})

        # Only chunk1 should be in results
        assert "chunk1" in result
        assert "chunk2" not in result

    @patch("memory.api.search.bm25.make_session")
    async def test_modalities_filter(self, mock_make_session):
        mock_db = MagicMock()
        mock_make_session.return_value.__enter__.return_value = mock_db
        mock_query = mock_db.query.return_value
        mock_query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = (
            []
        )

        await bm25.search_bm25("test", {"email", "blog", "note"})

        # Verify filter was called (modalities passed to filter)
        assert mock_query.filter.called


@pytest.mark.asyncio
class TestSearchBm25Chunks:
    """Tests for search_bm25_chunks async function."""

    async def test_empty_data_returns_empty(self):
        result = await bm25.search_bm25_chunks([], {"text"})
        assert result == {}

    async def test_data_chunks_with_no_strings_returns_empty(self):
        # DataChunks with only non-string content (using cast to test edge case handling)
        from typing import cast, Any
        chunks = [
            extract.DataChunk(data=cast(Any, [123, 456])),
            extract.DataChunk(data=cast(Any, [{"key": "value"}])),
        ]
        result = await bm25.search_bm25_chunks(chunks, {"text"})
        assert result == {}

    @patch("memory.api.search.bm25.search_bm25")
    async def test_single_query_chunk(self, mock_search_bm25):
        mock_search_bm25.return_value = {"chunk1": 0.8, "chunk2": 0.4}

        chunks = [extract.DataChunk(data=["test query"])]
        result = await bm25.search_bm25_chunks(chunks, {"text"}, limit=10)

        assert result == {"chunk1": 0.8, "chunk2": 0.4}
        mock_search_bm25.assert_called_once()

    @patch("memory.api.search.bm25.search_bm25")
    async def test_multiple_query_chunks_merged(self, mock_search_bm25):
        # First query returns chunk1=0.8, chunk2=0.4
        # Second query returns chunk2=0.9, chunk3=0.5
        # Result should take max for each chunk
        mock_search_bm25.side_effect = [
            {"chunk1": 0.8, "chunk2": 0.4},
            {"chunk2": 0.9, "chunk3": 0.5},
        ]

        chunks = [
            extract.DataChunk(data=["first query"]),
            extract.DataChunk(data=["second query"]),
        ]
        result = await bm25.search_bm25_chunks(chunks, {"text"})

        # Should take max score for each chunk
        assert result == {"chunk1": 0.8, "chunk2": 0.9, "chunk3": 0.5}
        assert mock_search_bm25.call_count == 2

    @patch("memory.api.search.bm25.search_bm25")
    async def test_timeout_handling(self, mock_search_bm25):
        # Simulate timeout
        async def slow_search(*args, **kwargs):
            await asyncio.sleep(2)
            return {}

        mock_search_bm25.side_effect = slow_search

        chunks = [extract.DataChunk(data=["test"])]
        result = await bm25.search_bm25_chunks(chunks, {"text"}, timeout=0.1)

        # Should return empty dict on timeout
        assert result == {}

    @patch("memory.api.search.bm25.search_bm25")
    async def test_exception_handling(self, mock_search_bm25):
        # First query succeeds, second raises exception
        mock_search_bm25.side_effect = [
            {"chunk1": 0.8},
            ValueError("Database error"),
        ]

        chunks = [
            extract.DataChunk(data=["first"]),
            extract.DataChunk(data=["second"]),
        ]
        result = await bm25.search_bm25_chunks(chunks, {"text"})

        # Should still return results from successful query
        assert result == {"chunk1": 0.8}

    @patch("memory.api.search.bm25.search_bm25")
    async def test_mixed_content_chunks(self, mock_search_bm25):
        # DataChunks with mixed string and non-string content (using cast to test edge case handling)
        from typing import cast, Any
        mock_search_bm25.return_value = {"chunk1": 0.5}

        chunks = [
            extract.DataChunk(data=cast(Any, ["text content", 123, {"key": "value"}, "more text"]))
        ]
        await bm25.search_bm25_chunks(chunks, {"text"})

        # Should extract only string content
        mock_search_bm25.assert_called_once()
        call_args = mock_search_bm25.call_args
        query = call_args[0][0]
        assert query == "text content more text"

    @patch("memory.api.search.bm25.search_bm25")
    async def test_whitespace_normalization(self, mock_search_bm25):
        mock_search_bm25.return_value = {}

        chunks = [
            extract.DataChunk(data=["  lots   of    whitespace  "])
        ]
        await bm25.search_bm25_chunks(chunks, {"text"})

        call_args = mock_search_bm25.call_args
        query = call_args[0][0]
        # The implementation joins with spaces but doesn't normalize internal whitespace
        # It preserves the original spacing
        assert "lots" in query
        assert "whitespace" in query

    @patch("memory.api.search.bm25.search_bm25")
    async def test_filters_passed_through(self, mock_search_bm25):
        mock_search_bm25.return_value = {}

        filters = SearchFilters(source_ids=[1, 2, 3])
        chunks = [extract.DataChunk(data=["test"])]
        await bm25.search_bm25_chunks(chunks, {"text"}, filters=filters)

        # Verify filters were passed to search_bm25
        # Function signature is: search_bm25(query, modalities, limit, filters)
        call_args = mock_search_bm25.call_args
        assert call_args[0][3] == filters  # 4th positional arg

    @patch("memory.api.search.bm25.search_bm25")
    async def test_limit_passed_through(self, mock_search_bm25):
        mock_search_bm25.return_value = {}

        chunks = [extract.DataChunk(data=["test"])]
        await bm25.search_bm25_chunks(chunks, {"text"}, limit=50)

        call_args = mock_search_bm25.call_args
        # Function signature is: search_bm25(query, modalities, limit, filters)
        assert call_args[0][2] == 50  # 3rd positional arg

    @patch("memory.api.search.bm25.search_bm25")
    async def test_modalities_passed_through(self, mock_search_bm25):
        mock_search_bm25.return_value = {}

        chunks = [extract.DataChunk(data=["test"])]
        modalities = {"email", "blog", "note"}
        await bm25.search_bm25_chunks(chunks, modalities)

        call_args = mock_search_bm25.call_args
        assert call_args[0][1] == modalities


def test_apply_access_filter_superadmin_returns_unchanged_query():
    """Test that None filter (superadmin) returns query unchanged."""
    mock_query = MagicMock()
    result = apply_access_filter(mock_query, None)
    assert result is mock_query
    assert not mock_query.filter.called


def test_apply_access_filter_empty_matches_nothing():
    """Test that empty access filter returns impossible condition."""
    mock_query = MagicMock()
    access_filter = AccessFilter(conditions=[])
    apply_access_filter(mock_query, access_filter)
    mock_query.filter.assert_called_once()


@pytest.mark.asyncio
class TestSearchBm25PersonFilter:
    """Tests for person_id filter in BM25 search."""

    @patch("memory.api.search.bm25.make_session")
    async def test_person_id_triggers_source_join(self, mock_make_session):
        """Test that person_id filter triggers join with SourceItem."""
        mock_db = MagicMock()
        mock_make_session.return_value.__enter__.return_value = mock_db
        mock_query = mock_db.query.return_value
        mock_filter = mock_query.filter.return_value
        mock_join = mock_filter.join.return_value
        mock_join.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        filters = SearchFilters(person_id=42)
        await bm25.search_bm25("test", {"text"}, filters=filters)

        # Verify join was called (person_id requires source join)
        assert mock_filter.join.called

    @patch("memory.api.search.bm25.make_session")
    async def test_person_id_filter_applied(self, mock_make_session):
        """Test that person_id filter is properly applied to query."""
        mock_db = MagicMock()
        mock_make_session.return_value.__enter__.return_value = mock_db
        mock_query = mock_db.query.return_value

        # Setup the chain for source join + person filter
        mock_base_filter = mock_query.filter.return_value
        mock_join = mock_base_filter.join.return_value
        mock_person_filter = mock_join.filter.return_value
        mock_person_filter.order_by.return_value.limit.return_value.all.return_value = []

        filters = SearchFilters(person_id=42)
        await bm25.search_bm25("test", {"text"}, filters=filters)

        # The person_id filter adds a filter call after the join
        # This tests that the flow includes the filter call
        assert mock_join.filter.called


# ============================================================================
# Integration tests for apply_access_filter
# These require db_session (--run-slow) as they test real database queries
# ============================================================================


@pytest.fixture
def github_account(db_session):
    """Create a GitHub account for testing."""
    from memory.common.db.models.sources import GithubAccount

    account = GithubAccount(
        user_id=1,
        name="Test Account",
        auth_type="pat",
        access_token="test_token",
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def github_repo(db_session, github_account):
    """Create a GitHub repo for testing."""
    from memory.common.db.models.sources import GithubRepo

    repo = GithubRepo(
        account_id=github_account.id,
        github_id=12345,
        owner="testowner",
        name="testrepo",
    )
    db_session.add(repo)
    db_session.commit()
    return repo


@pytest.fixture
def project(db_session, github_repo):
    """Create a GitHub milestone (project) for testing."""
    from memory.common.db.models.sources import GithubMilestone

    milestone = GithubMilestone(
        repo_id=github_repo.id,
        github_id=100,
        number=1,
        title="Test Project",
        state="open",
    )
    db_session.add(milestone)
    db_session.commit()
    return milestone


@pytest.fixture
def second_project(db_session, github_repo):
    """Create a second GitHub milestone (project) for testing."""
    from memory.common.db.models.sources import GithubMilestone

    milestone = GithubMilestone(
        repo_id=github_repo.id,
        github_id=101,
        number=2,
        title="Second Project",
        state="open",
    )
    db_session.add(milestone)
    db_session.commit()
    return milestone


@pytest.fixture
def source_items_with_access(db_session, project, second_project):
    """Create source items with different project_id and sensitivity values."""
    from memory.common.db.models import Note

    items = [
        # Project 1 items
        Note(content="Project 1 basic item", project_id=project.id, sensitivity="basic"),
        Note(content="Project 1 internal item", project_id=project.id, sensitivity="internal"),
        Note(content="Project 1 confidential item", project_id=project.id, sensitivity="confidential"),
        # Project 2 items
        Note(content="Project 2 basic item", project_id=second_project.id, sensitivity="basic"),
        Note(content="Project 2 internal item", project_id=second_project.id, sensitivity="internal"),
        # Item with no project (superadmin only)
        Note(content="Unclassified item", project_id=None, sensitivity="basic"),
    ]
    for item in items:
        db_session.add(item)
    db_session.commit()
    return items


@pytest.mark.parametrize(
    "role,expected_count",
    [
        # Contributor sees only basic (1 item)
        ("contributor", 1),
        # Manager sees basic + internal (2 items)
        ("manager", 2),
        # Admin sees all sensitivities (3 items)
        ("admin", 3),
    ],
)
def test_apply_access_filter_single_project_sensitivity(
    db_session, project, source_items_with_access, role, expected_count
):
    """Test that single project filter respects sensitivity levels based on role."""
    from memory.common.access_control import AccessCondition, AccessFilter, get_allowed_sensitivities
    from memory.common.db.models import SourceItem

    # Ensure fixture ran (creates items in db)
    _ = source_items_with_access

    # Build filter for project with given role
    sensitivities = get_allowed_sensitivities(role)
    assert sensitivities is not None, f"Invalid role: {role}"
    access_filter = AccessFilter(
        conditions=[AccessCondition(project_id=project.id, sensitivities=sensitivities)]
    )

    # Query all source items with access filter
    query = db_session.query(SourceItem)
    filtered_query = apply_access_filter(query, access_filter)
    results = filtered_query.all()

    assert len(results) == expected_count
    # All results should be from the correct project
    for item in results:
        assert item.project_id == project.id
        assert item.sensitivity in sensitivities


def test_apply_access_filter_multiple_projects(
    db_session, project, second_project, source_items_with_access
):
    """Test that multiple project conditions are OR'd together correctly."""
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.common.db.models import SourceItem

    # Ensure fixture ran
    _ = source_items_with_access

    # User is contributor on project 1, manager on project 2
    access_filter = AccessFilter(
        conditions=[
            AccessCondition(project_id=project.id, sensitivities=frozenset({"basic"})),
            AccessCondition(project_id=second_project.id, sensitivities=frozenset({"basic", "internal"})),
        ]
    )

    query = db_session.query(SourceItem)
    filtered_query = apply_access_filter(query, access_filter)
    results = filtered_query.all()

    # Should get: 1 from project 1 (basic only) + 2 from project 2 (basic + internal)
    assert len(results) == 3

    # Verify the right items were returned
    project1_items = [i for i in results if i.project_id == project.id]
    project2_items = [i for i in results if i.project_id == second_project.id]

    assert len(project1_items) == 1
    assert project1_items[0].sensitivity == "basic"

    assert len(project2_items) == 2
    assert {i.sensitivity for i in project2_items} == {"basic", "internal"}


def test_apply_access_filter_excludes_null_project_id(
    db_session, project, source_items_with_access
):
    """Test that items with NULL project_id are excluded for non-superadmins."""
    from memory.common.access_control import AccessCondition, AccessFilter
    from memory.common.db.models import SourceItem

    # Ensure fixture ran
    _ = source_items_with_access

    # Even with admin role on a project, NULL project_id items should be excluded
    access_filter = AccessFilter(
        conditions=[
            AccessCondition(
                project_id=project.id,
                sensitivities=frozenset({"basic", "internal", "confidential"}),
            )
        ]
    )

    query = db_session.query(SourceItem)
    filtered_query = apply_access_filter(query, access_filter)
    results = filtered_query.all()

    # All results should have a project_id (no NULLs)
    for item in results:
        assert item.project_id is not None


def test_apply_access_filter_superadmin_sees_all(db_session, source_items_with_access):
    """Test that superadmin (None filter) sees all items including NULL project_id."""
    from memory.common.db.models import SourceItem

    # Ensure fixture ran
    _ = source_items_with_access

    query = db_session.query(SourceItem)
    filtered_query = apply_access_filter(query, None)  # None = superadmin
    results = filtered_query.all()

    # Superadmin sees all 6 items including the one with NULL project_id
    assert len(results) == 6

    # Verify NULL project_id item is included
    null_project_items = [i for i in results if i.project_id is None]
    assert len(null_project_items) == 1
