# Search Functions Test Analysis

## Overview

This document provides a comprehensive analysis of the search functions in the memory API and outlines the functional tests needed using parametrize following existing patterns in the repository.

## Current Test Coverage

### Already Tested Functions
- **embeddings.py**: `merge_range_filter`, `merge_filters` (in `test_search_embeddings.py`)
- **types.py**: `elide_content`, `SearchResult.from_source_item` (in `test_search_types.py`)

### Functions Requiring Tests

## 1. Search Module (`src/memory/api/search/search.py`)

### Functions to Test:
- `search_chunks(data, modalities, limit, filters, timeout)`
- `search_sources(chunks, previews)`
- `search(data, modalities, filters, config)`

### Recommended Test Cases:

#### `search_chunks` Function Tests:
```python
@pytest.mark.parametrize(
    "data_chunks,modalities,limit,expected_behavior",
    [
        ([mock_data_chunk("test query")], {"text"}, 10, "normal_execution"),
        ([], {"text"}, 10, "empty_data"),
        ([mock_data_chunk("query")], set(), 10, "empty_modalities"),
        ([mock_data_chunk("query")], {"text", "docs"}, 5, "multiple_modalities"),
        ([mock_data_chunk("query")], {"text"}, 100, "large_limit"),
    ],
)
```

#### `search_sources` Function Tests:
```python
@pytest.mark.parametrize(
    "chunks_by_source,previews,expected_results",
    [
        ({1: [mock_chunk1, mock_chunk2]}, False, "grouped_by_source"),
        ({1: [mock_chunk1], 2: [mock_chunk2]}, True, "multiple_sources_with_previews"),
        ({}, False, "empty_chunks"),
        ({1: []}, False, "source_with_empty_chunks"),
    ],
)
```

#### `search` Function Tests:
```python
@pytest.mark.parametrize(
    "data,modalities,filters,config,expected_workflow",
    [
        ([mock_data], {"text"}, {}, SearchConfig(), "basic_search"),
        ([mock_data], {"text"}, {"source_ids": ["id1"]}, SearchConfig(), "filtered_search"),
        ([mock_data], {"text", "docs"}, {}, SearchConfig(useScores=True), "with_scoring"),
        ([mock_data], {"text"}, {}, SearchConfig(previews=True), "with_previews"),
        ([mock_data], {"text"}, {}, SearchConfig(limit=5), "limited_results"),
    ],
)
```

## 2. BM25 Module (`src/memory/api/search/bm25.py`)

### Functions to Test:
- `search_bm25(query, modalities, limit, filters)`
- `search_bm25_chunks(data, modalities, limit, filters, timeout)`

### Recommended Test Cases:

#### `search_bm25` Function Tests:
```python
@pytest.mark.parametrize(
    "query,modalities,limit,filters,mock_db_chunks,expected_behavior",
    [
        ("python programming", {"text"}, 10, {}, [mock_chunks], "basic_query"),
        ("", {"text"}, 10, {}, [], "empty_query"),
        ("query", set(), 10, {}, [], "empty_modalities"),
        ("query", {"text"}, 10, {"source_ids": ["id1"]}, [filtered_chunks], "source_filtering"),
        ("query", {"text"}, 10, {"min_confidences": {"accuracy": 0.8}}, [filtered_chunks], "confidence_filtering"),
        ("query", {"text"}, 5, {}, [many_chunks], "limited_results"),
    ],
)
```

#### `search_bm25_chunks` Function Tests:
```python
@pytest.mark.parametrize(
    "data_chunks,expected_query_string,timeout",
    [
        ([DataChunk(data=["hello world"])], "hello world", 2),
        ([DataChunk(data=["part1", "part2"])], "part1 part2", 2),
        ([DataChunk(data=["text1"]), DataChunk(data=["text2"])], "text1 text2", 2),
        ([DataChunk(data=[123, "valid", None])], "valid", 2),
        ([], "", 2),
    ],
)
```

## 3. Scorer Module (`src/memory/api/search/scorer.py`)

### Functions to Test:
- `score_chunk(query, chunk)`
- `rank_chunks(query, chunks, min_score)`

### Recommended Test Cases:

#### `score_chunk` Function Tests:
```python
@pytest.mark.parametrize(
    "query,chunk_content,chunk_images,expected_score_range",
    [
        ("python", "python programming tutorial", [], (0.7, 1.0)),
        ("cooking", "machine learning algorithms", [], (0.0, 0.3)),
        ("", "any content", [], (0.0, 0.5)),
        ("query", "", [], (0.0, 0.2)),
        ("image query", "text with image", [mock_image], (0.3, 0.8)),
    ],
)
```

#### `rank_chunks` Function Tests:
```python
@pytest.mark.parametrize(
    "chunks_with_scores,min_score,expected_count,expected_order",
    [
        ([(0.8, "chunk1"), (0.6, "chunk2"), (0.4, "chunk3")], 0.5, 2, ["chunk1", "chunk2"]),
        ([(0.9, "a"), (0.7, "b"), (0.3, "c")], 0.0, 3, ["a", "b", "c"]),
        ([(0.2, "low"), (0.1, "lower")], 0.5, 0, []),
        ([], 0.3, 0, []),
    ],
)
```

## 4. Embeddings Module Additional Functions

### Functions Still Needing Tests:
- `query_chunks(client, upload_data, allowed_modalities, embedder, min_score, limit, filters)`
- `search_chunks(data, modalities, limit, min_score, filters, multimodal)`
- `search_chunks_embeddings(data, modalities, limit, filters, timeout)`

### Recommended Test Cases:

#### `query_chunks` Function Tests:
```python
@pytest.mark.parametrize(
    "upload_data,allowed_modalities,min_score,limit,mock_results",
    [
        ([mock_data_chunk], {"text"}, 0.3, 10, mock_qdrant_results),
        ([], {"text"}, 0.3, 10, {}),
        ([mock_data_chunk], set(), 0.3, 10, {}),
        ([mock_data_chunk], {"text", "docs"}, 0.5, 5, mock_multimodal_results),
    ],
)
```

#### `search_chunks` Function Tests:
```python
@pytest.mark.parametrize(
    "data,modalities,limit,min_score,filters,multimodal",
    [
        ([mock_data], {"text"}, 10, 0.3, {}, False),
        ([mock_data], {"text", "docs"}, 10, 0.25, {}, True),
        ([mock_data], {"text"}, 5, 0.4, {"tags": ["important"]}, False),
        ([], {"text"}, 10, 0.3, {}, False),
    ],
)
```

## 5. Test Implementation Strategy

### Pattern Following Existing Tests
All tests should follow the existing pattern seen in `test_search_embeddings.py`:
- Use `@pytest.mark.parametrize` for comprehensive parameter testing
- Create descriptive test names that explain the test scenario
- Use meaningful assertions that verify expected behavior
- Include edge cases (empty inputs, invalid data, etc.)
- Mock external dependencies (database, LLM calls, vector search)

### Mock Strategy
- **Database Operations**: Mock `make_session`, query chains
- **Vector Search**: Mock Qdrant client and search results
- **LLM Calls**: Mock `llms.call` and `asyncio.to_thread`
- **External Libraries**: Mock BM25, stemming, tokenization

### Test Isolation
Each test should:
- Test one function at a time
- Mock all external dependencies
- Use parametrize to test multiple scenarios
- Verify both success and error cases
- Test async functionality properly

## 6. Environment Setup Challenges

### Dependencies Required
The full test environment requires numerous dependencies:
- `anthropic` - AI/LLM client
- `openai` - OpenAI client  
- `voyageai` - Voyage AI client
- `Pillow` - Image processing
- `beautifulsoup4` - HTML parsing
- `PyMuPDF` - PDF processing
- `pypandoc` - Document conversion
- `bm25s` - BM25 search
- `Stemmer` - Text stemming
- `qdrant-client` - Vector database client
- `psycopg2-binary` - PostgreSQL adapter (requires system dependencies)

### Import Chain Issues
The current module structure creates import dependencies through `__init__.py` that pull in the entire dependency chain, making it difficult to test individual functions in isolation.

### Recommended Solutions
1. **Refactor imports**: Consider making `__init__.py` imports conditional or lazy
2. **Test isolation**: Import functions directly rather than through module packages
3. **Dependency injection**: Make external dependencies injectable for easier testing
4. **Mock at module level**: Mock entire modules rather than individual functions

## 7. Test Files Created

### Completed Test Files:
- `tests/memory/api/search/test_search_types.py` - Tests for types module (existing)
- `tests/memory/api/search/test_search_embeddings.py` - Tests for embeddings filtering (existing)

### Test Files Attempted:
- `tests/memory/api/search/test_search_bm25.py` - BM25 search function tests (created but not runnable due to import issues)
- `tests/memory/api/search/test_search_scorer.py` - Scorer function tests (created but not runnable due to import issues)

### Recommended Next Steps:
1. **Environment Setup**: Resolve dependency installation and import chain issues
2. **Module Refactoring**: Consider restructuring imports to enable isolated testing
3. **Gradual Implementation**: Start with simpler functions and work up to more complex ones
4. **CI/CD Integration**: Ensure tests can run in automated environments

## 8. Summary

The search module contains 12+ functions that need comprehensive testing. I've analyzed each function and provided detailed test case recommendations using the parametrize pattern. The main challenge is the complex dependency chain that prevents isolated function testing. Once the environment issues are resolved, the tests can be implemented following the patterns I've outlined.

Key functions prioritized for testing:
1. **High Priority**: `search`, `search_chunks`, `search_sources` (main entry points)
2. **Medium Priority**: `score_chunk`, `rank_chunks` (scoring functionality)  
3. **Lower Priority**: `search_bm25`, `search_bm25_chunks` (alternative search method)
4. **Additional**: Remaining embeddings functions not yet tested

All tests should follow the existing repository patterns with parametrize decorators, comprehensive parameter coverage, proper mocking, and meaningful assertions.