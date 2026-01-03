import pytest
from memory.api.search.embeddings import merge_range_filter, merge_filters


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
