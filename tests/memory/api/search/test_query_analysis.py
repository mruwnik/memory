"""Tests for query_analysis module."""

import json
import pytest
from unittest.mock import patch, Mock, AsyncMock

from memory.api.search.query_analysis import (
    ModalityInfo,
    QueryAnalysis,
    _build_prompt,
    _get_available_modalities,
    _get_modality_domains,
    _is_valid_sql_identifier,
    analyze_query,
)


class TestModalityInfo:
    """Tests for ModalityInfo dataclass."""

    def test_description_items_only(self):
        """Basic description with just count."""
        info = ModalityInfo(name="forum", count=1000)
        assert info.description == "1,000 items"

    def test_description_with_domains(self):
        """Description includes domains when present."""
        info = ModalityInfo(
            name="forum", count=1000, domains=["lesswrong.com", "example.com"]
        )
        assert info.description == "1,000 items from: lesswrong.com, example.com"

    def test_description_with_source_count(self):
        """Description shows sources and sections for parent entities."""
        info = ModalityInfo(name="book", count=22000, source_count=400)
        assert info.description == "400 sources (22,000 sections)"

    def test_description_with_source_count_and_domains(self):
        """Description shows sources, sections, and domains."""
        info = ModalityInfo(
            name="comic",
            count=8000,
            source_count=50,
            domains=["xkcd.com", "smbc-comics.com"],
        )
        assert (
            info.description
            == "50 sources (8,000 sections) from: xkcd.com, smbc-comics.com"
        )

    def test_description_formats_large_numbers(self):
        """Numbers are formatted with commas."""
        info = ModalityInfo(name="book", count=1234567, source_count=9876)
        assert info.description == "9,876 sources (1,234,567 sections)"


class TestQueryAnalysis:
    """Tests for QueryAnalysis dataclass."""

    def test_default_values(self):
        """QueryAnalysis has sensible defaults."""
        result = QueryAnalysis()
        assert result.modalities == set()
        assert result.sources == []
        assert result.cleaned_query == ""
        assert result.query_variants == []
        assert result.success is False

    def test_with_values(self):
        """QueryAnalysis stores provided values."""
        result = QueryAnalysis(
            modalities={"forum", "book"},
            sources=["lesswrong.com"],
            cleaned_query="test query",
            query_variants=["alternative query"],
            success=True,
        )
        assert result.modalities == {"forum", "book"}
        assert result.sources == ["lesswrong.com"]
        assert result.cleaned_query == "test query"
        assert result.query_variants == ["alternative query"]
        assert result.success is True


class TestBuildPrompt:
    """Tests for _build_prompt function."""

    def test_builds_prompt_with_modalities(self):
        """Prompt includes modality information."""
        mock_modalities = {
            "forum": ModalityInfo(
                name="forum", count=20000, domains=["lesswrong.com"]
            ),
            "book": ModalityInfo(name="book", count=22000, source_count=400),
        }

        with patch(
            "memory.api.search.query_analysis._get_available_modalities",
            return_value=mock_modalities,
        ):
            prompt = _build_prompt()

        assert "forum" in prompt
        assert "book" in prompt
        assert "20,000 items from: lesswrong.com" in prompt
        assert "400 sources (22,000 sections)" in prompt
        assert "modalities" in prompt
        assert "cleaned_query" in prompt

    def test_builds_prompt_empty_modalities(self):
        """Prompt handles no modalities gracefully."""
        with patch(
            "memory.api.search.query_analysis._get_available_modalities",
            return_value={},
        ):
            prompt = _build_prompt()

        assert "no content indexed yet" in prompt

    def test_prompt_contains_json_structure(self):
        """Prompt includes expected JSON structure."""
        with patch(
            "memory.api.search.query_analysis._get_available_modalities",
            return_value={"test": ModalityInfo(name="test", count=100)},
        ):
            prompt = _build_prompt()

        assert '"modalities": []' in prompt
        assert '"sources": []' in prompt
        assert '"cleaned_query": ""' in prompt
        assert '"query_variants": []' in prompt

    def test_prompt_contains_guidelines(self):
        """Prompt includes usage guidelines."""
        with patch(
            "memory.api.search.query_analysis._get_available_modalities",
            return_value={"forum": ModalityInfo(name="forum", count=100)},
        ):
            prompt = _build_prompt()

        assert "Remove meta-language" in prompt
        assert "Return ONLY valid JSON" in prompt
        assert "recalled_content" in prompt


class TestAnalyzeQuery:
    """Tests for analyze_query async function."""

    @pytest.mark.asyncio
    async def test_returns_analysis_on_success(self):
        """Successfully parses LLM JSON response."""
        mock_response = json.dumps(
            {
                "modalities": ["forum"],
                "sources": ["lesswrong.com"],
                "cleaned_query": "rationality concepts",
                "query_variants": ["rational thinking", "epistemic rationality"],
            }
        )

        mock_provider = Mock()
        mock_provider.agenerate = AsyncMock(return_value=mock_response)

        with patch(
            "memory.api.search.query_analysis.create_provider",
            return_value=mock_provider,
        ):
            with patch(
                "memory.api.search.query_analysis._get_available_modalities",
                return_value={"forum": ModalityInfo(name="forum", count=100)},
            ):
                # Clear any cached results
                from memory.api.search import query_analysis
                query_analysis._analysis_cache = {}

                result = await analyze_query("something on lesswrong about rationality")

        assert result.success is True
        assert result.modalities == {"forum"}
        assert result.sources == ["lesswrong.com"]
        assert result.cleaned_query == "rationality concepts"
        assert "rational thinking" in result.query_variants

    @pytest.mark.asyncio
    async def test_handles_markdown_code_blocks(self):
        """Strips markdown code blocks from response."""
        mock_response = """```json
{
    "modalities": ["book"],
    "sources": [],
    "cleaned_query": "test query",
    "query_variants": []
}
```"""

        mock_provider = Mock()
        mock_provider.agenerate = AsyncMock(return_value=mock_response)

        with patch(
            "memory.api.search.query_analysis.create_provider",
            return_value=mock_provider,
        ):
            with patch(
                "memory.api.search.query_analysis._get_available_modalities",
                return_value={"book": ModalityInfo(name="book", count=100)},
            ):
                from memory.api.search import query_analysis
                query_analysis._analysis_cache = {}

                result = await analyze_query("find something in a book")

        assert result.success is True
        assert result.modalities == {"book"}

    @pytest.mark.asyncio
    async def test_handles_invalid_json(self):
        """Returns default result on invalid JSON."""
        mock_provider = Mock()
        mock_provider.agenerate = AsyncMock(return_value="not valid json {{{")

        with patch(
            "memory.api.search.query_analysis.create_provider",
            return_value=mock_provider,
        ):
            with patch(
                "memory.api.search.query_analysis._get_available_modalities",
                return_value={},
            ):
                from memory.api.search import query_analysis
                query_analysis._analysis_cache = {}

                result = await analyze_query("test query")

        assert result.success is False
        assert result.cleaned_query == "test query"

    @pytest.mark.asyncio
    async def test_handles_timeout(self):
        """Returns default result on timeout."""
        import asyncio

        async def slow_response(*args, **kwargs):
            await asyncio.sleep(10)
            return "{}"

        mock_provider = Mock()
        mock_provider.agenerate = slow_response

        with patch(
            "memory.api.search.query_analysis.create_provider",
            return_value=mock_provider,
        ):
            with patch(
                "memory.api.search.query_analysis._get_available_modalities",
                return_value={},
            ):
                from memory.api.search import query_analysis
                query_analysis._analysis_cache = {}

                result = await analyze_query("test query", timeout=0.01)

        assert result.success is False
        assert result.cleaned_query == "test query"

    @pytest.mark.asyncio
    async def test_caches_results(self):
        """Caches analysis results for repeated queries."""
        mock_response = json.dumps(
            {
                "modalities": [],
                "sources": [],
                "cleaned_query": "cached query",
                "query_variants": [],
            }
        )

        mock_provider = Mock()
        mock_provider.agenerate = AsyncMock(return_value=mock_response)

        with patch(
            "memory.api.search.query_analysis.create_provider",
            return_value=mock_provider,
        ):
            with patch(
                "memory.api.search.query_analysis._get_available_modalities",
                return_value={},
            ):
                from memory.api.search import query_analysis
                query_analysis._analysis_cache = {}

                # First call
                result1 = await analyze_query("test query for caching")
                # Second call (should use cache)
                result2 = await analyze_query("test query for caching")

        # Provider should only be called once
        assert mock_provider.agenerate.call_count == 1
        assert result1.cleaned_query == result2.cleaned_query

    @pytest.mark.asyncio
    async def test_cache_case_insensitive(self):
        """Cache key is case-insensitive."""
        mock_response = json.dumps(
            {
                "modalities": [],
                "sources": [],
                "cleaned_query": "test",
                "query_variants": [],
            }
        )

        mock_provider = Mock()
        mock_provider.agenerate = AsyncMock(return_value=mock_response)

        with patch(
            "memory.api.search.query_analysis.create_provider",
            return_value=mock_provider,
        ):
            with patch(
                "memory.api.search.query_analysis._get_available_modalities",
                return_value={},
            ):
                from memory.api.search import query_analysis
                query_analysis._analysis_cache = {}

                await analyze_query("Test Query")
                await analyze_query("test query")
                await analyze_query("TEST QUERY")

        # All variations should hit the same cache entry
        assert mock_provider.agenerate.call_count == 1

    @pytest.mark.asyncio
    async def test_handles_empty_response(self):
        """Handles empty LLM response gracefully."""
        mock_provider = Mock()
        mock_provider.agenerate = AsyncMock(return_value="")

        with patch(
            "memory.api.search.query_analysis.create_provider",
            return_value=mock_provider,
        ):
            with patch(
                "memory.api.search.query_analysis._get_available_modalities",
                return_value={},
            ):
                from memory.api.search import query_analysis
                query_analysis._analysis_cache = {}

                result = await analyze_query("test query")

        assert result.success is False
        assert result.cleaned_query == "test query"

    @pytest.mark.asyncio
    async def test_handles_none_response(self):
        """Handles None LLM response gracefully."""
        mock_provider = Mock()
        mock_provider.agenerate = AsyncMock(return_value=None)

        with patch(
            "memory.api.search.query_analysis.create_provider",
            return_value=mock_provider,
        ):
            with patch(
                "memory.api.search.query_analysis._get_available_modalities",
                return_value={},
            ):
                from memory.api.search import query_analysis
                query_analysis._analysis_cache = {}

                result = await analyze_query("test query")

        assert result.success is False


@pytest.mark.parametrize(
    "identifier,expected",
    [
        # Valid identifiers
        ("blog_post", True),
        ("MailMessage", True),
        ("_private", True),
        ("a", True),
        ("table123", True),
        ("UPPERCASE", True),
        ("mixed_Case_123", True),
        # Invalid identifiers
        ("", False),  # Empty
        ("123start", False),  # Starts with number
        ("has-hyphen", False),  # Contains hyphen
        ("has space", False),  # Contains space
        ("has.dot", False),  # Contains dot
        ("semi;colon", False),  # SQL injection attempt
        ("quote'mark", False),  # SQL injection attempt
        ('quote"mark', False),  # SQL injection attempt
        ("drop table--", False),  # SQL injection attempt
        ("x" * 129, False),  # Too long (>128 chars)
    ],
)
def test_is_valid_sql_identifier(identifier, expected):
    """Validates SQL identifiers correctly."""
    assert _is_valid_sql_identifier(identifier) == expected


def test_is_valid_sql_identifier_max_length_boundary():
    """Tests boundary condition for max length."""
    assert _is_valid_sql_identifier("x" * 128) is True
    assert _is_valid_sql_identifier("x" * 129) is False


class TestGetAvailableModalities:
    """Tests for _get_available_modalities function."""

    def test_uses_cache_when_fresh(self):
        """Returns cached data when cache is fresh."""
        import time
        from memory.api.search import query_analysis

        # Set up cache
        query_analysis._modality_cache = {
            "test": ModalityInfo(name="test", count=100)
        }
        query_analysis._cache_timestamp = time.time()

        with patch(
            "memory.api.search.query_analysis._refresh_modality_cache"
        ) as mock_refresh:
            result = _get_available_modalities()

        mock_refresh.assert_not_called()
        assert "test" in result

    def test_refreshes_cache_when_stale(self):
        """Refreshes cache when TTL has expired."""
        import time
        from memory.api.search import query_analysis

        # Set up stale cache
        query_analysis._modality_cache = {}
        query_analysis._cache_timestamp = time.time() - 7200  # 2 hours ago

        with patch(
            "memory.api.search.query_analysis._refresh_modality_cache"
        ) as mock_refresh:
            _get_available_modalities()

        mock_refresh.assert_called_once()

    def test_refreshes_cache_when_empty(self):
        """Refreshes cache when cache is empty."""
        import time
        from memory.api.search import query_analysis

        query_analysis._modality_cache = {}
        query_analysis._cache_timestamp = time.time()

        with patch(
            "memory.api.search.query_analysis._refresh_modality_cache"
        ) as mock_refresh:
            _get_available_modalities()

        mock_refresh.assert_called_once()


# Tests for _get_modality_domains (SQL injection protection)


def test_get_modality_domains_skips_invalid_table_names():
    """Invalid table names are skipped to prevent SQL injection."""
    mock_db = Mock()

    # Return a mix of valid and malicious table names
    with patch(
        "memory.api.search.query_analysis._get_tables_with_url_column",
        return_value=["blog_post", "drop table--", "mail_message", "'; DROP TABLE;--"],
    ):
        # Mock db.execute to return empty results
        mock_db.execute.return_value = iter([])

        _get_modality_domains(mock_db)

    # Verify that only valid tables were included in the query
    call_args = mock_db.execute.call_args
    if call_args:
        query_text = str(call_args[0][0])
        assert "blog_post" in query_text
        assert "mail_message" in query_text
        assert "drop table" not in query_text
        assert "DROP TABLE" not in query_text


def test_get_modality_domains_empty_tables():
    """Returns empty dict when no tables have URL columns."""
    mock_db = Mock()

    with patch(
        "memory.api.search.query_analysis._get_tables_with_url_column",
        return_value=[],
    ):
        result = _get_modality_domains(mock_db)

    assert result == {}
    mock_db.execute.assert_not_called()


def test_get_modality_domains_all_invalid_tables():
    """Returns empty dict when all table names are invalid."""
    mock_db = Mock()

    with patch(
        "memory.api.search.query_analysis._get_tables_with_url_column",
        return_value=["'; DROP TABLE;--", "123invalid", "has-hyphen"],
    ):
        result = _get_modality_domains(mock_db)

    assert result == {}
    mock_db.execute.assert_not_called()


def test_get_modality_domains_handles_db_error():
    """Handles database errors gracefully."""
    from sqlalchemy.exc import SQLAlchemyError

    mock_db = Mock()
    mock_db.execute.side_effect = SQLAlchemyError("Database error")

    with patch(
        "memory.api.search.query_analysis._get_tables_with_url_column",
        return_value=["valid_table"],
    ):
        result = _get_modality_domains(mock_db)

    assert result == {}


def test_get_modality_domains_extracts_domains():
    """Correctly extracts domains from URLs."""
    mock_db = Mock()

    # Mock database rows: (modality, url)
    mock_rows = [
        ("blog", "https://lesswrong.com/posts/123"),
        ("blog", "https://lesswrong.com/posts/456"),
        ("blog", "https://example.com/article"),
        ("forum", "https://forum.example.org/thread/1"),
    ]
    mock_db.execute.return_value = iter(mock_rows)

    with patch(
        "memory.api.search.query_analysis._get_tables_with_url_column",
        return_value=["blog_post"],
    ):
        result = _get_modality_domains(mock_db)

    # Domains should be deduplicated and sorted by frequency
    assert "blog" in result
    assert "lesswrong.com" in result["blog"]
    assert "example.com" in result["blog"]
    assert "forum" in result
    assert "forum.example.org" in result["forum"]
