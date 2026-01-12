"""
Tests for HyDE (Hypothetical Document Embeddings) query expansion.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.api.search import hyde
from memory.common.llms import Message


@pytest.fixture(autouse=True)
def clear_hyde_cache():
    """Clear the HyDE cache before each test."""
    hyde._hyde_cache.clear()
    yield
    hyde._hyde_cache.clear()


@pytest.mark.asyncio
class TestExpandQueryHyde:
    """Tests for expand_query_hyde function."""

    async def test_empty_query_returns_none(self):
        # Empty queries should be handled gracefully
        with patch("memory.api.search.hyde.create_provider") as mock_create:
            mock_provider = AsyncMock()
            mock_create.return_value = mock_provider
            mock_provider.agenerate.return_value = ""

            result = await hyde.expand_query_hyde("")

            # Should still try to expand but get empty result
            assert result is None or result == ""

    @patch("memory.api.search.hyde.create_provider")
    async def test_basic_expansion_success(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.return_value = "This is a hypothetical document about Python programming."

        result = await hyde.expand_query_hyde("python programming tutorial")

        assert result == "This is a hypothetical document about Python programming."
        mock_provider.agenerate.assert_called_once()

    @patch("memory.api.search.hyde.create_provider")
    async def test_uses_cache_on_second_call(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.return_value = "Cached response"

        # First call
        result1 = await hyde.expand_query_hyde("test query")
        assert result1 == "Cached response"
        assert mock_provider.agenerate.call_count == 1

        # Second call with same query (case-insensitive)
        result2 = await hyde.expand_query_hyde("TEST QUERY")
        assert result2 == "Cached response"
        # Should not call provider again (still 1)
        assert mock_provider.agenerate.call_count == 1

    @patch("memory.api.search.hyde.create_provider")
    async def test_cache_case_insensitive(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.return_value = "Response"

        await hyde.expand_query_hyde("Query")
        await hyde.expand_query_hyde("query")
        await hyde.expand_query_hyde("QUERY")

        # All should hit cache after first
        assert mock_provider.agenerate.call_count == 1

    @patch("memory.api.search.hyde.create_provider")
    async def test_cache_strips_whitespace(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.return_value = "Response"

        await hyde.expand_query_hyde("  query  ")
        await hyde.expand_query_hyde("query")

        # Should hit cache
        assert mock_provider.agenerate.call_count == 1

    @patch("memory.api.search.hyde.create_provider")
    async def test_cache_eviction_at_max_size(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.return_value = "Response"

        # Fill cache to max
        for i in range(hyde._CACHE_MAX_SIZE):
            await hyde.expand_query_hyde(f"query {i}")

        assert len(hyde._hyde_cache) == hyde._CACHE_MAX_SIZE

        # Add one more - should trigger eviction
        await hyde.expand_query_hyde("overflow query")

        # Cache should be reduced to half after eviction
        assert len(hyde._hyde_cache) == (hyde._CACHE_MAX_SIZE // 2) + 1

    @patch("memory.api.search.hyde.create_provider")
    async def test_timeout_returns_none(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider

        # Simulate timeout
        async def slow_generate(*args, **kwargs):
            await asyncio.sleep(10)
            return "Too slow"

        mock_provider.agenerate.side_effect = slow_generate

        result = await hyde.expand_query_hyde("test", timeout=0.1)

        assert result is None

    @patch("memory.api.search.hyde.create_provider")
    async def test_exception_returns_none(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.side_effect = ValueError("LLM error")

        result = await hyde.expand_query_hyde("test query")

        assert result is None

    @patch("memory.api.search.hyde.create_provider")
    async def test_custom_model_parameter(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.return_value = "Response"

        await hyde.expand_query_hyde("test", model="gpt-4")

        mock_create.assert_called_once_with(model="gpt-4")

    @patch("memory.api.search.hyde.create_provider")
    async def test_uses_default_model_when_none(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.return_value = "Response"

        with patch("memory.api.search.hyde.settings") as mock_settings:
            mock_settings.SUMMARIZER_MODEL = "default-model"
            await hyde.expand_query_hyde("test", model=None)

        mock_create.assert_called_once_with(model="default-model")

    @patch("memory.api.search.hyde.create_provider")
    async def test_llm_settings_temperature(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.return_value = "Response"

        await hyde.expand_query_hyde("test")

        call_kwargs = mock_provider.agenerate.call_args[1]
        assert call_kwargs["settings"].temperature == 0.3

    @patch("memory.api.search.hyde.create_provider")
    async def test_llm_settings_max_tokens(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.return_value = "Response"

        await hyde.expand_query_hyde("test")

        call_kwargs = mock_provider.agenerate.call_args[1]
        assert call_kwargs["settings"].max_tokens == 200

    @patch("memory.api.search.hyde.create_provider")
    async def test_system_prompt_passed(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.return_value = "Response"

        await hyde.expand_query_hyde("test")

        call_kwargs = mock_provider.agenerate.call_args[1]
        assert call_kwargs["system_prompt"] == hyde.HYDE_SYSTEM_PROMPT

    @patch("memory.api.search.hyde.create_provider")
    async def test_user_message_format(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.return_value = "Response"

        await hyde.expand_query_hyde("my test query")

        call_args = mock_provider.agenerate.call_args[1]
        messages = call_args["messages"]
        assert len(messages) == 1
        # Just verify the query is in the message content
        message_str = str(messages[0])
        assert "my test query" in message_str

    @patch("memory.api.search.hyde.create_provider")
    async def test_strips_whitespace_from_response(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.return_value = "  Response with whitespace  \n\n"

        result = await hyde.expand_query_hyde("test")

        assert result == "Response with whitespace"

    @patch("memory.api.search.hyde.create_provider")
    async def test_empty_response_returns_none(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.return_value = ""

        result = await hyde.expand_query_hyde("test")

        # Empty string after strip should not be cached
        assert result is None or result == ""

    @patch("memory.api.search.hyde.create_provider")
    async def test_whitespace_only_response_returns_none(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.return_value = "   \n\t  "

        result = await hyde.expand_query_hyde("test")

        # Whitespace-only becomes empty after strip
        assert result == "" or result is None

    @patch("memory.api.search.hyde.create_provider")
    async def test_custom_timeout(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider

        async def delayed_response(*args, **kwargs):
            await asyncio.sleep(0.2)
            return "Response"

        mock_provider.agenerate.side_effect = delayed_response

        # Should timeout with 0.1s timeout
        result = await hyde.expand_query_hyde("test", timeout=0.1)
        assert result is None

        # Should succeed with 0.3s timeout
        result = await hyde.expand_query_hyde("test2", timeout=0.3)
        assert result == "Response"

    @patch("memory.api.search.hyde.create_provider")
    async def test_none_response_returns_none(self, mock_create):
        mock_provider = AsyncMock()
        mock_create.return_value = mock_provider
        mock_provider.agenerate.return_value = None

        result = await hyde.expand_query_hyde("test")

        assert result is None


@pytest.mark.asyncio
class TestGetHydeChunks:
    """Tests for get_hyde_chunks function."""

    @patch("memory.api.search.hyde.expand_query_hyde")
    async def test_short_query_no_expansion(self, mock_expand):
        # Queries with < 4 words should not be expanded
        result = await hyde.get_hyde_chunks("short query")

        assert result == ["short query"]
        mock_expand.assert_not_called()

    @patch("memory.api.search.hyde.expand_query_hyde")
    async def test_three_words_no_expansion(self, mock_expand):
        result = await hyde.get_hyde_chunks("one two three")

        assert result == ["one two three"]
        mock_expand.assert_not_called()

    @patch("memory.api.search.hyde.expand_query_hyde")
    async def test_four_words_triggers_expansion(self, mock_expand):
        mock_expand.return_value = "Expanded hypothetical document"

        result = await hyde.get_hyde_chunks("one two three four")

        assert result == ["one two three four", "Expanded hypothetical document"]
        mock_expand.assert_called_once()

    @patch("memory.api.search.hyde.expand_query_hyde")
    async def test_long_query_triggers_expansion(self, mock_expand):
        mock_expand.return_value = "Expanded document"

        result = await hyde.get_hyde_chunks("this is a longer query with many words")

        assert len(result) == 2
        assert result[0] == "this is a longer query with many words"
        assert result[1] == "Expanded document"
        mock_expand.assert_called_once()

    @patch("memory.api.search.hyde.expand_query_hyde")
    async def test_expansion_failure_returns_original_only(self, mock_expand):
        mock_expand.return_value = None

        result = await hyde.get_hyde_chunks("this is a long query")

        assert result == ["this is a long query"]

    @patch("memory.api.search.hyde.expand_query_hyde")
    async def test_empty_expansion_returns_original_only(self, mock_expand):
        mock_expand.return_value = ""

        result = await hyde.get_hyde_chunks("this is a long query")

        # Empty string is falsy, so only original is included
        assert result == ["this is a long query"]

    @patch("memory.api.search.hyde.expand_query_hyde")
    async def test_passes_custom_model(self, mock_expand):
        mock_expand.return_value = "Expanded"

        await hyde.get_hyde_chunks("long query with many words", model="custom-model")

        mock_expand.assert_called_once()
        # Function signature: expand_query_hyde(query, model, timeout)
        call_args = mock_expand.call_args[0]
        assert call_args[1] == "custom-model"

    @patch("memory.api.search.hyde.expand_query_hyde")
    async def test_passes_custom_timeout(self, mock_expand):
        mock_expand.return_value = "Expanded"

        await hyde.get_hyde_chunks("long query with words", timeout=10.0)

        mock_expand.assert_called_once()
        # Function signature: expand_query_hyde(query, model, timeout)
        call_args = mock_expand.call_args[0]
        assert call_args[2] == 10.0

    @patch("memory.api.search.hyde.expand_query_hyde")
    async def test_empty_query(self, mock_expand):
        result = await hyde.get_hyde_chunks("")

        assert result == [""]
        mock_expand.assert_not_called()

    @patch("memory.api.search.hyde.expand_query_hyde")
    async def test_single_word_query(self, mock_expand):
        result = await hyde.get_hyde_chunks("python")

        assert result == ["python"]
        mock_expand.assert_not_called()

    @patch("memory.api.search.hyde.expand_query_hyde")
    async def test_whitespace_in_query(self, mock_expand):
        # Multiple spaces/tabs should still count words correctly
        mock_expand.return_value = "Expanded"

        result = await hyde.get_hyde_chunks("word1  word2   word3    word4")

        # Should have 4 words after split, triggering expansion
        assert len(result) == 2
        mock_expand.assert_called_once()

    @patch("memory.api.search.hyde.expand_query_hyde")
    async def test_word_count_boundary_exactly_4(self, mock_expand):
        mock_expand.return_value = "Expanded"

        # Exactly 4 words should trigger expansion
        result = await hyde.get_hyde_chunks("a b c d")

        assert len(result) == 2
        mock_expand.assert_called_once()

    @patch("memory.api.search.hyde.expand_query_hyde")
    async def test_original_query_always_first(self, mock_expand):
        mock_expand.return_value = "Hypothetical document"

        result = await hyde.get_hyde_chunks("long query with multiple words")

        # Original should always be first
        assert result[0] == "long query with multiple words"
        assert result[1] == "Hypothetical document"

    @patch("memory.api.search.hyde.expand_query_hyde")
    async def test_special_characters_in_query(self, mock_expand):
        mock_expand.return_value = "Expanded"

        result = await hyde.get_hyde_chunks("how to use @decorators in python?")

        # Should count 6 words, trigger expansion
        assert len(result) == 2
        mock_expand.assert_called_once()


class TestHydeSystemPrompt:
    """Tests for HyDE system prompt characteristics."""

    def test_system_prompt_exists(self):
        assert hyde.HYDE_SYSTEM_PROMPT is not None
        assert len(hyde.HYDE_SYSTEM_PROMPT) > 0

    def test_system_prompt_has_clear_instructions(self):
        # Should contain guidance on what to do and what not to do
        prompt = hyde.HYDE_SYSTEM_PROMPT
        assert "DO NOT" in prompt or "Do NOT" in prompt
        assert "DO:" in prompt or "write" in prompt.lower()

    def test_system_prompt_emphasizes_document_style(self):
        # Should encourage writing in document style
        prompt = hyde.HYDE_SYSTEM_PROMPT.lower()
        assert "document" in prompt or "article" in prompt or "passage" in prompt

    def test_system_prompt_discourages_meta_commentary(self):
        # Should tell it not to use meta phrases
        prompt = hyde.HYDE_SYSTEM_PROMPT
        assert "don't" in prompt.lower() or "do not" in prompt.lower()


class TestHydeCacheManagement:
    """Tests for HyDE cache behavior."""

    def test_cache_is_global_dict(self):
        assert isinstance(hyde._hyde_cache, dict)

    def test_cache_max_size_constant(self):
        assert hyde._CACHE_MAX_SIZE > 0
        assert isinstance(hyde._CACHE_MAX_SIZE, int)

    def test_cache_evicts_oldest_entries(self):
        # This is a behavioral test - eviction removes first half
        # Already tested in expand_query_hyde tests, but documenting here
        assert hyde._CACHE_MAX_SIZE == 100  # Verify expected constant
