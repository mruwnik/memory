from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.api.search.embeddings import (
    merge_range_filter,
    merge_filters,
    build_person_filter,
    build_access_qdrant_filter,
    search_chunks,
)
from memory.common.access_control import AccessFilter, AccessCondition
from memory.common.extract import DataChunk


def test_merge_range_filter_new_filter():
    """Test adding new range filters"""
    filters = []
    result = merge_range_filter(filters, "min_size", 100)
    assert result == [{"key": "size", "range": {"gte": 100}}]

    filters = []
    result = merge_range_filter(filters, "max_size", 1000)
    assert result == [{"key": "size", "range": {"lte": 1000}}]


def test_merge_range_filter_existing_field():
    """Test adding to existing field"""
    filters = [{"key": "size", "range": {"lte": 1000}}]
    result = merge_range_filter(filters, "min_size", 100)
    assert result == [{"key": "size", "range": {"lte": 1000, "gte": 100}}]


def test_merge_range_filter_override_existing():
    """Test overriding existing values"""
    filters = [{"key": "size", "range": {"gte": 100}}]
    result = merge_range_filter(filters, "min_size", 200)
    assert result == [{"key": "size", "range": {"gte": 200}}]


def test_merge_range_filter_with_other_filters():
    """Test adding range filter alongside other filters"""
    filters = [{"key": "tags", "match": {"any": ["tag1"]}}]
    result = merge_range_filter(filters, "min_size", 100)

    expected = [
        {"key": "tags", "match": {"any": ["tag1"]}},
        {"key": "size", "range": {"gte": 100}},
    ]
    assert result == expected


@pytest.mark.parametrize(
    "key,expected_direction,expected_field",
    [
        ("min_sent_at", "min", "sent_at"),
        ("max_sent_at", "max", "sent_at"),
        ("min_published", "min", "published"),
        ("max_published", "max", "published"),
        ("min_size", "min", "size"),
        ("max_size", "max", "size"),
    ],
)
def test_merge_range_filter_key_parsing(key, expected_direction, expected_field):
    """Test that field names are correctly extracted from keys"""
    filters = []
    result = merge_range_filter(filters, key, 100)

    assert len(result) == 1
    assert result[0]["key"] == expected_field
    range_key = "gte" if expected_direction == "min" else "lte"
    assert result[0]["range"][range_key] == 100


@pytest.mark.parametrize(
    "filter_key,filter_value",
    [
        ("tags", ["tag1", "tag2"]),
        ("recipients", ["user1", "user2"]),
        ("observation_types", ["belief", "preference"]),
        ("authors", ["author1"]),
    ],
)
def test_merge_filters_list_filters(filter_key, filter_value):
    """Test list filters that use match any"""
    filters = []
    result = merge_filters(filters, filter_key, filter_value)
    expected = [{"key": filter_key, "match": {"any": filter_value}}]
    assert result == expected


def test_merge_filters_min_confidences():
    """Test min_confidences filter creates multiple range conditions"""
    filters = []
    confidences = {"observation_accuracy": 0.8, "source_reliability": 0.9}
    result = merge_filters(filters, "min_confidences", confidences)

    expected = [
        {"key": "confidence.observation_accuracy", "range": {"gte": 0.8}},
        {"key": "confidence.source_reliability", "range": {"gte": 0.9}},
    ]
    assert result == expected


def test_merge_filters_source_ids():
    """Test source_ids filter maps to source_id field in payload"""
    filters = []
    result = merge_filters(filters, "source_ids", ["id1", "id2"])
    expected = [{"key": "source_id", "match": {"any": ["id1", "id2"]}}]
    assert result == expected


@pytest.mark.parametrize(
    "filter_key,filter_value",
    [
        ("folder_path", "My Drive/EquiStamp"),
        ("sender", "user@example.com"),
        ("domain", "example.com"),
        ("author", "John Doe"),
    ],
)
def test_merge_filters_string_filters(filter_key, filter_value):
    """Test string filters create exact match conditions"""
    filters = []
    result = merge_filters(filters, filter_key, filter_value)
    expected = [{"key": filter_key, "match": {"value": filter_value}}]
    assert result == expected


def test_merge_filters_range_delegation():
    """Test range filters are properly delegated to merge_range_filter"""
    filters = []
    result = merge_filters(filters, "min_size", 100)

    assert len(result) == 1
    assert "range" in result[0]
    assert result[0]["range"]["gte"] == 100


def test_merge_filters_combined_range():
    """Test that min/max range pairs merge into single filter"""
    filters = []
    filters = merge_filters(filters, "min_size", 100)
    filters = merge_filters(filters, "max_size", 1000)

    size_filters = [f for f in filters if f["key"] == "size"]
    assert len(size_filters) == 1
    assert size_filters[0]["range"]["gte"] == 100
    assert size_filters[0]["range"]["lte"] == 1000


def test_merge_filters_preserves_existing():
    """Test that existing filters are preserved when adding new ones"""
    existing_filters = [{"key": "existing", "match": "value"}]
    result = merge_filters(existing_filters, "tags", ["new_tag"])

    assert len(result) == 2
    assert {"key": "existing", "match": "value"} in result
    assert {"key": "tags", "match": {"any": ["new_tag"]}} in result


def test_merge_filters_realistic_combination():
    """Test a realistic filter combination for knowledge base search"""
    filters = []

    # Add typical knowledge base filters
    filters = merge_filters(filters, "tags", ["important", "work"])
    filters = merge_filters(filters, "min_published", "2023-01-01")
    filters = merge_filters(filters, "max_size", 1000000)  # 1MB max
    filters = merge_filters(filters, "min_confidences", {"observation_accuracy": 0.8})

    assert len(filters) == 4

    # Check each filter type
    tag_filter = next(f for f in filters if f["key"] == "tags")
    assert tag_filter["match"]["any"] == ["important", "work"]

    published_filter = next(f for f in filters if f["key"] == "published")
    assert published_filter["range"]["gte"] == "2023-01-01"

    size_filter = next(f for f in filters if f["key"] == "size")
    assert size_filter["range"]["lte"] == 1000000

    confidence_filter = next(
        f for f in filters if f["key"] == "confidence.observation_accuracy"
    )
    assert confidence_filter["range"]["gte"] == 0.8


def test_merge_filters_unknown_key():
    """Test that unknown filter keys are logged and ignored for security"""
    filters = []
    result = merge_filters(filters, "unknown_field", "unknown_value")
    # Unknown keys should be ignored to prevent filter injection
    assert result == []


def test_merge_filters_empty_min_confidences():
    """Test min_confidences with empty dict does nothing"""
    filters = []
    result = merge_filters(filters, "min_confidences", {})
    assert result == []


# --- Person Filter Tests ---


def test_build_person_filter_structure():
    """Test that build_person_filter creates correct Qdrant filter structure"""
    result = build_person_filter(42)

    # Should create a "should" filter with two conditions
    # Note: No explicit min_should needed - Qdrant's 'should' requires at least one match by default
    assert "should" in result
    assert "min_should" not in result  # Not needed for "at least one" semantics

    should_conditions = result["should"]
    assert len(should_conditions) == 2


def test_build_person_filter_is_empty_condition():
    """Test that person filter includes is_empty condition for null/missing/empty people field"""
    result = build_person_filter(42)

    # First condition: people field is null, missing, or empty array
    # Using is_empty (not is_null) because as_payload() returns [] for items without people
    is_empty_condition = result["should"][0]
    assert is_empty_condition == {"is_empty": {"key": "people"}}


def test_build_person_filter_match_condition():
    """Test that person filter includes match condition for person_id"""
    result = build_person_filter(42)

    # Second condition: people field contains person_id
    match_condition = result["should"][1]
    assert match_condition == {"key": "people", "match": {"any": [42]}}


@pytest.mark.parametrize("person_id", [1, 100, 999999])
def test_build_person_filter_different_ids(person_id):
    """Test person filter with different person IDs"""
    result = build_person_filter(person_id)
    match_condition = result["should"][1]
    assert match_condition["match"]["any"] == [person_id]


def test_merge_filters_ignores_person_id():
    """Test that merge_filters ignores person_id key (handled separately)"""
    filters = []
    result = merge_filters(filters, "person_id", 42)
    # person_id is handled separately in search_chunks, so merge_filters should ignore it
    # (it falls through to the unknown key case)
    assert result == []


# --- Access Control Filter Tests ---


def test_build_access_qdrant_filter_superadmin():
    """Test that superadmin (None filter) returns empty list (no filtering)."""
    result = build_access_qdrant_filter(None)
    assert result == []


def test_build_access_qdrant_filter_no_access():
    """Test that empty access filter returns impossible condition."""
    access_filter = AccessFilter(conditions=[])
    result = build_access_qdrant_filter(access_filter)

    # Should return a condition that matches nothing
    assert len(result) == 1
    assert result[0]["key"] == "project_id"
    assert result[0]["match"]["value"] == -1


def test_build_access_qdrant_filter_single_project():
    """Test access filter with single project membership."""
    condition = AccessCondition(
        project_id=1,
        sensitivities=frozenset({"basic", "internal"}),
    )
    access_filter = AccessFilter(conditions=[condition])
    result = build_access_qdrant_filter(access_filter)

    assert len(result) == 1
    # Should have must conditions for project_id and sensitivity
    assert "must" in result[0]
    must_conditions = result[0]["must"]
    assert len(must_conditions) == 2

    # Check project_id condition
    project_condition = next(c for c in must_conditions if c["key"] == "project_id")
    assert project_condition["match"]["value"] == 1

    # Check sensitivity condition
    sensitivity_condition = next(c for c in must_conditions if c["key"] == "sensitivity")
    assert set(sensitivity_condition["match"]["any"]) == {"basic", "internal"}


def test_build_access_qdrant_filter_multiple_projects():
    """Test access filter with multiple project memberships."""
    conditions = [
        AccessCondition(project_id=1, sensitivities=frozenset({"basic"})),
        AccessCondition(project_id=2, sensitivities=frozenset({"basic", "internal", "confidential"})),
    ]
    access_filter = AccessFilter(conditions=conditions)
    result = build_access_qdrant_filter(access_filter)

    # Should have two "should" conditions (one per project)
    assert len(result) == 2

    # Find conditions by project_id
    proj1_cond = next(r for r in result if any(
        c.get("key") == "project_id" and c["match"]["value"] == 1
        for c in r["must"]
    ))
    proj2_cond = next(r for r in result if any(
        c.get("key") == "project_id" and c["match"]["value"] == 2
        for c in r["must"]
    ))

    # Project 1: only basic
    sens1 = next(c for c in proj1_cond["must"] if c["key"] == "sensitivity")
    assert sens1["match"]["any"] == ["basic"]

    # Project 2: all levels
    sens2 = next(c for c in proj2_cond["must"] if c["key"] == "sensitivity")
    assert set(sens2["match"]["any"]) == {"basic", "internal", "confidential"}


# --- Integration Tests for search_chunks with filter parameters ---


@pytest.fixture
def mock_qdrant_client():
    """Create a mock Qdrant client."""
    return MagicMock()


@pytest.mark.asyncio
async def test_search_chunks_passes_person_filter_to_query():
    """Test that search_chunks correctly passes person_id filter to query_chunks."""
    with patch("memory.api.search.embeddings.qdrant") as mock_qdrant, \
         patch("memory.api.search.embeddings.query_chunks", new_callable=AsyncMock) as mock_query:
        mock_qdrant.get_qdrant_client.return_value = "mock_client"
        mock_query.return_value = {}

        data = [DataChunk(data=["test query"])]
        await search_chunks(data, modalities={"text"}, filters={"person_id": 42})

        # Verify query_chunks was called
        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args[1]

        # Check that filters contain the person filter structure
        filters = call_kwargs.get("filters")
        assert filters is not None
        assert "must" in filters

        # Find the person filter in the must conditions
        # Uses is_empty (not is_null) because as_payload() returns [] for items without people
        person_filter = next(
            (f for f in filters["must"] if "should" in f and any(
                "is_empty" in c for c in f["should"]
            )),
            None
        )
        assert person_filter is not None
        assert person_filter["should"][1]["match"]["any"] == [42]


@pytest.mark.asyncio
async def test_search_chunks_passes_access_filter_to_query():
    """Test that search_chunks correctly passes access_filter to query_chunks."""
    with patch("memory.api.search.embeddings.qdrant") as mock_qdrant, \
         patch("memory.api.search.embeddings.query_chunks", new_callable=AsyncMock) as mock_query:
        mock_qdrant.get_qdrant_client.return_value = "mock_client"
        mock_query.return_value = {}

        condition = AccessCondition(project_id=1, sensitivities=frozenset({"basic"}))
        access_filter = AccessFilter(conditions=[condition])

        data = [DataChunk(data=["test query"])]
        await search_chunks(data, modalities={"text"}, filters={"access_filter": access_filter})

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args[1]

        filters = call_kwargs.get("filters")
        assert filters is not None
        assert "must" in filters

        # Find the access filter (nested should with project conditions)
        access_nested = next(
            (f for f in filters["must"] if "should" in f and any(
                "must" in c for c in f["should"]
            )),
            None
        )
        assert access_nested is not None
        # Verify the project_id condition is in the nested should
        project_cond = access_nested["should"][0]["must"]
        assert any(c.get("key") == "project_id" and c["match"]["value"] == 1 for c in project_cond)


@pytest.mark.asyncio
async def test_search_chunks_no_access_filter_returns_early():
    """Test that empty access filter (no project access) returns empty without querying."""
    with patch("memory.api.search.embeddings.qdrant") as mock_qdrant, \
         patch("memory.api.search.embeddings.query_chunks", new_callable=AsyncMock) as mock_query:
        mock_qdrant.get_qdrant_client.return_value = "mock_client"
        mock_query.return_value = {}

        # Empty conditions = no project access
        access_filter = AccessFilter(conditions=[])

        data = [DataChunk(data=["test query"])]
        result = await search_chunks(data, modalities={"text"}, filters={"access_filter": access_filter})

        # Should return empty dict immediately without calling Qdrant
        assert result == {}
        mock_query.assert_not_called()


@pytest.mark.asyncio
async def test_search_chunks_superadmin_no_access_filter():
    """Test that None access filter (superadmin) adds no access filtering."""
    with patch("memory.api.search.embeddings.qdrant") as mock_qdrant, \
         patch("memory.api.search.embeddings.query_chunks", new_callable=AsyncMock) as mock_query:
        mock_qdrant.get_qdrant_client.return_value = "mock_client"
        mock_query.return_value = {}

        data = [DataChunk(data=["test query"])]
        # Explicitly pass None for access_filter (superadmin)
        await search_chunks(data, modalities={"text"}, filters={"access_filter": None})

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args[1]

        # Filters should be None or empty (no access filtering for superadmin)
        filters = call_kwargs.get("filters")
        # No must conditions should be present for access control
        if filters:
            # If there are must conditions, none should be access-related
            must_conditions = filters.get("must", [])
            access_conditions = [
                f for f in must_conditions
                if f.get("key") == "project_id" or (
                    "should" in f and any("must" in c for c in f["should"])
                )
            ]
            assert access_conditions == []
