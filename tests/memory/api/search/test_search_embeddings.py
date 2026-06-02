from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import copy
import pickle

from memory.api.search.embeddings import (
    build_qdrant_special_filters,
    build_person_filter,
    build_access_qdrant_filter,
    NO_ACCESS,
    NoAccess,
    require_access_filter,
    search_chunks,
    search_chunks_embeddings,
)
from memory.api.search.filters import build_registry_qdrant_filters
from memory.common.access_control import AccessFilter, AccessCondition
from memory.common.extract import DataChunk


# --- Qdrant filter translation (registry + special keys) ---
#
# The Qdrant filter dicts are now produced by the shared registry
# (``build_registry_qdrant_filters``) plus the hand-coded special-key shapes
# (``build_qdrant_special_filters``). These tests pin the translated output.


@pytest.mark.parametrize(
    "key,value,expected_payload_key",
    [
        ("min_size", 100, "size"),
        ("max_size", 1000, "size"),
        ("min_sent_at", "2024-01-01", "date"),  # payload stores email date as "date"
        ("max_sent_at", "2024-12-31", "date"),
        ("min_published", "2023-01-01", "published"),
        ("max_published", "2023-12-31", "published"),
    ],
)
def test_registry_range_filter_payload_key(key, value, expected_payload_key):
    result = build_registry_qdrant_filters({key: value})
    assert len(result) == 1
    assert result[0]["key"] == expected_payload_key
    range_key = "gte" if key.startswith("min") else "lte"
    assert result[0]["range"][range_key] == value


def test_registry_combined_range_merges():
    result = build_registry_qdrant_filters({"min_size": 100, "max_size": 1000})
    size_filters = [f for f in result if f["key"] == "size"]
    assert len(size_filters) == 1
    assert size_filters[0]["range"] == {"gte": 100, "lte": 1000}


@pytest.mark.parametrize(
    "filter_key,filter_value,payload_key",
    [
        ("tags", ["tag1", "tag2"], "tags"),
        # recipients filters the derived bare-address payload key.
        ("recipients", ["user1", "user2"], "recipient_emails"),
        ("authors", ["author1"], "authors"),
    ],
)
def test_registry_list_filters(filter_key, filter_value, payload_key):
    result = build_registry_qdrant_filters({filter_key: filter_value})
    assert result == [{"key": payload_key, "match": {"any": filter_value}}]


@pytest.mark.parametrize(
    "filter_key,filter_value,payload_key",
    [
        ("folder_path", "My Drive/EquiStamp", "folder_path"),
        # sender filters the derived bare-address payload key.
        ("sender", "user@example.com", "sender_email"),
        ("domain", "example.com", "domain"),
        ("author", "John Doe", "author"),
    ],
)
def test_registry_string_filters(filter_key, filter_value, payload_key):
    result = build_registry_qdrant_filters({filter_key: filter_value})
    assert result == [{"key": payload_key, "match": {"value": filter_value}}]


def test_special_min_confidences():
    confidences = {"observation_accuracy": 0.8, "source_reliability": 0.9}
    result = build_qdrant_special_filters({"min_confidences": confidences})
    assert result == [
        {"key": "confidence.observation_accuracy", "range": {"gte": 0.8}},
        {"key": "confidence.source_reliability", "range": {"gte": 0.9}},
    ]


def test_special_source_ids_maps_to_singular_payload_key():
    result = build_qdrant_special_filters({"source_ids": [11, 22]})
    assert result == [{"key": "source_id", "match": {"any": [11, 22]}}]


def test_special_observation_types():
    result = build_qdrant_special_filters({"observation_types": ["belief", "preference"]})
    assert result == [
        {"key": "observation_types", "match": {"any": ["belief", "preference"]}}
    ]


def test_registry_empty_min_confidences_is_special_not_registry():
    # min_confidences is a special key; the registry never emits it.
    assert build_registry_qdrant_filters({"min_confidences": {}}) == []
    assert build_qdrant_special_filters({"min_confidences": {}}) == []


def test_registry_realistic_combination():
    filters = {
        "tags": ["important", "work"],
        "min_published": "2023-01-01",
        "max_size": 1000000,
    }
    result = build_registry_qdrant_filters(filters)
    result += build_qdrant_special_filters({"min_confidences": {"observation_accuracy": 0.8}})

    tag_filter = next(f for f in result if f["key"] == "tags")
    assert tag_filter["match"]["any"] == ["important", "work"]

    published_filter = next(f for f in result if f["key"] == "published")
    assert published_filter["range"]["gte"] == "2023-01-01"

    size_filter = next(f for f in result if f["key"] == "size")
    assert size_filter["range"]["lte"] == 1000000

    confidence_filter = next(
        f for f in result if f["key"] == "confidence.observation_accuracy"
    )
    assert confidence_filter["range"]["gte"] == 0.8


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


def test_person_id_not_emitted_by_filter_builders():
    """person_id is a special key: neither the registry nor the special-key
    builder emits a payload condition for it (search_chunks builds the
    compound person filter separately)."""
    assert build_registry_qdrant_filters({"person_id": 42}) == []
    assert build_qdrant_special_filters({"person_id": 42}) == []


# --- Access Control Filter Tests ---


def test_build_access_qdrant_filter_superadmin():
    """Test that superadmin (None filter) returns empty list (no filtering)."""
    result = build_access_qdrant_filter(None)
    assert result == []


def test_build_access_qdrant_filter_no_access():
    """Test that empty access filter with no public bypass returns the
    NO_ACCESS sentinel (not a magic project_id == -1 dict)."""
    access_filter = AccessFilter(conditions=[], include_public=False)
    result = build_access_qdrant_filter(access_filter)

    # Identity-comparable sentinel: callers MUST detect this via
    # `is NO_ACCESS` to distinguish from `[]` (superadmin / no filter).
    assert result is NO_ACCESS


def test_build_access_qdrant_filter_no_access_is_distinct_from_superadmin():
    """The NO_ACCESS sentinel must NOT be equal-by-identity to the
    superadmin empty-list return; otherwise consumers cannot tell the
    'deny all' case from 'no filter needed' and would silently grant
    full access."""
    superadmin_result = build_access_qdrant_filter(None)
    no_access_result = build_access_qdrant_filter(
        AccessFilter(conditions=[], include_public=False)
    )

    # The deny-all sentinel is a typed singleton instance, distinguishable
    # from the empty-list superadmin return at multiple levels.
    assert superadmin_result == []
    assert no_access_result is NO_ACCESS
    assert isinstance(no_access_result, NoAccess)
    assert superadmin_result is not NO_ACCESS
    assert not isinstance(superadmin_result, NoAccess)


# ---------------------------------------------------------------------------
# Discriminator tests for the NoAccess sentinel.
#
# These tests assert each of the regression vectors that broke the previous
# empty-tuple sentinel design is now blocked. Each test corresponds to a
# specific natural-looking refactor that would silently turn "deny" into
# "allow all" if the sentinel were just an empty container.
# ---------------------------------------------------------------------------


def test_no_access_sentinel_not_equal_to_empty_list():
    """REGRESSION GUARD: a static analyzer / contributor swapping
    ``access_conditions is NO_ACCESS`` for ``access_conditions == NO_ACCESS``
    must NOT silently match `[]` (the superadmin/no-filter return).
    """
    # The deny-all sentinel must NOT compare equal to ``[]``: otherwise an
    # ``access_conditions == []`` comparison anywhere in the consumer chain
    # would short-circuit superadmin queries instead.
    assert NO_ACCESS != []
    assert [] != NO_ACCESS
    assert NO_ACCESS != ()
    assert () != NO_ACCESS
    assert NO_ACCESS != {}


def test_no_access_sentinel_equal_to_itself():
    """The sentinel compares equal to itself (default object equality
    on the same instance). The module-level ``NO_ACCESS`` is the
    canonical instance returned from production code; constructing
    additional ``NoAccess()`` instances is fine but they're separate
    objects — type-distinctness, not instance-distinctness, is the
    load-bearing property."""
    assert NO_ACCESS == NO_ACCESS
    assert NO_ACCESS is NO_ACCESS


def test_no_access_sentinel_falsy():
    """The sentinel is falsy so ``if access_conditions:`` continues to
    skip the deny branch the same way it did with the empty-tuple
    sentinel. The ``isinstance`` check (or ``is NO_ACCESS``) is the
    authoritative discriminator — falsiness is a compatibility property,
    not a security signal."""
    assert not bool(NO_ACCESS)
    assert not NO_ACCESS


def test_no_access_sentinel_survives_copy_as_isinstance():
    """REGRESSION GUARD: ``copy.copy``/``deepcopy`` of NO_ACCESS may
    produce a different instance (we no longer enforce singleton via
    ``__reduce__``), but the copy must still ``isinstance(_, NoAccess)``.
    The canonical check is type-distinctness, not identity."""
    assert isinstance(copy.copy(NO_ACCESS), NoAccess)
    assert isinstance(copy.deepcopy(NO_ACCESS), NoAccess)


def test_no_access_sentinel_survives_pickle_as_isinstance():
    """REGRESSION GUARD: pickling round-trips to a NoAccess instance
    (different object, same type). If a future caller depends on the
    rehydrated value being literally ``NO_ACCESS`` (identity) — and
    such a caller does not exist today — restore ``__reduce__`` and
    pin identity here too."""
    rehydrated = pickle.loads(pickle.dumps(NO_ACCESS))
    assert isinstance(rehydrated, NoAccess)


def test_no_access_sentinel_isinstance_canonical():
    """The canonical detection method is ``isinstance(x, NoAccess)`` — it
    works regardless of singleton-ness and is what the consumer uses."""
    assert isinstance(NO_ACCESS, NoAccess)
    assert not isinstance([], NoAccess)
    assert not isinstance((), NoAccess)
    assert not isinstance({}, NoAccess)
    assert not isinstance(None, NoAccess)
    assert not isinstance([{"key": "x"}], NoAccess)


def test_no_access_sentinel_not_iterable_as_conditions():
    """If a consumer accidentally tries to iterate ``access_conditions``
    as if it were a list of Qdrant 'should' clauses, the deny-all
    sentinel must produce a clear error (not silently iterate as empty,
    which would yield "no filter applied")."""
    # The sentinel is intentionally NOT iterable: any code path that
    # forgets the ``isinstance`` discriminator and tries to splat
    # access_conditions into a Qdrant filter will raise a TypeError
    # rather than silently produce an unfiltered query.
    import pytest as _pytest

    with _pytest.raises(TypeError):
        list(NO_ACCESS)  # type: ignore[call-overload]


def test_no_access_sentinel_repr_is_descriptive():
    """The repr is the canonical name so debugging output ("got
    NO_ACCESS instead of a list") is unambiguous."""
    assert repr(NO_ACCESS) == "NO_ACCESS"


def test_build_access_qdrant_filter_no_access_with_public():
    """Test that empty access filter with public bypass allows public items."""
    access_filter = AccessFilter(conditions=[], include_public=True)
    result = build_access_qdrant_filter(access_filter)

    # Should allow public items only — narrow the union return type
    # before indexing.
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["key"] == "sensitivity"
    assert result[0]["match"]["value"] == "public"


def test_build_access_qdrant_filter_single_project():
    """Test access filter with single project membership (no public bypass)."""
    condition = AccessCondition(
        project_id=1,
        sensitivities=frozenset({"basic", "internal"}),
    )
    access_filter = AccessFilter(conditions=[condition], include_public=False)
    result = build_access_qdrant_filter(access_filter)

    assert isinstance(result, list)
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


def test_build_access_qdrant_filter_single_project_with_public():
    """Test access filter with single project membership and public bypass."""
    condition = AccessCondition(
        project_id=1,
        sensitivities=frozenset({"basic", "internal"}),
    )
    access_filter = AccessFilter(conditions=[condition], include_public=True)
    result = build_access_qdrant_filter(access_filter)

    # Should have 2 conditions: public bypass + project access
    assert isinstance(result, list)
    assert len(result) == 2

    # Find public bypass condition
    public_condition = next(r for r in result if r.get("key") == "sensitivity")
    assert public_condition["match"]["value"] == "public"

    # Find project access condition
    project_result = next(r for r in result if "must" in r)
    must_conditions = project_result["must"]
    assert len(must_conditions) == 2

    project_condition = next(c for c in must_conditions if c["key"] == "project_id")
    assert project_condition["match"]["value"] == 1

    sensitivity_condition = next(c for c in must_conditions if c["key"] == "sensitivity")
    assert set(sensitivity_condition["match"]["any"]) == {"basic", "internal"}


def test_build_access_qdrant_filter_multiple_projects():
    """Test access filter with multiple project memberships (no public bypass)."""
    conditions = [
        AccessCondition(project_id=1, sensitivities=frozenset({"basic"})),
        AccessCondition(project_id=2, sensitivities=frozenset({"basic", "internal", "confidential"})),
    ]
    access_filter = AccessFilter(conditions=conditions, include_public=False)
    result = build_access_qdrant_filter(access_filter)

    # Should have two "should" conditions (one per project)
    assert isinstance(result, list)
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
        await search_chunks(
            data,
            modalities={"text"},
            filters={"person_id": 42, "access_filter": None},
        )

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
        # Disable public bypass for simpler test assertions
        access_filter = AccessFilter(conditions=[condition], include_public=False)

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
        # Find the project condition in the nested should (skip non-must entries like public bypass)
        project_must = next(
            (c["must"] for c in access_nested["should"] if "must" in c),
            None
        )
        assert project_must is not None
        assert any(c.get("key") == "project_id" and c["match"]["value"] == 1 for c in project_must)


@pytest.mark.asyncio
async def test_search_chunks_no_access_filter_returns_early():
    """Test that empty access filter with no public bypass returns empty without querying."""
    with patch("memory.api.search.embeddings.qdrant") as mock_qdrant, \
         patch("memory.api.search.embeddings.query_chunks", new_callable=AsyncMock) as mock_query:
        mock_qdrant.get_qdrant_client.return_value = "mock_client"
        mock_query.return_value = {}

        # Empty conditions + no public bypass = no access at all
        access_filter = AccessFilter(conditions=[], include_public=False)

        data = [DataChunk(data=["test query"])]
        result = await search_chunks(data, modalities={"text"}, filters={"access_filter": access_filter})

        # Should return empty dict immediately without calling Qdrant
        assert result == {}
        mock_query.assert_not_called()


@pytest.mark.asyncio
async def test_search_chunks_no_project_access_but_public():
    """Test that empty access filter with public bypass queries for public items."""
    with patch("memory.api.search.embeddings.qdrant") as mock_qdrant, \
         patch("memory.api.search.embeddings.query_chunks", new_callable=AsyncMock) as mock_query:
        mock_qdrant.get_qdrant_client.return_value = "mock_client"
        mock_query.return_value = {}

        # Empty conditions but include_public=True = can still search public items
        access_filter = AccessFilter(conditions=[], include_public=True)

        data = [DataChunk(data=["test query"])]
        await search_chunks(data, modalities={"text"}, filters={"access_filter": access_filter})

        # Should still query Qdrant (for public items)
        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args[1]
        filters = call_kwargs.get("filters")
        assert filters is not None
        # Should have a filter for public sensitivity
        public_filter = next(
            (f for f in filters.get("must", [])
             if "should" in f and any(
                 c.get("key") == "sensitivity" and c.get("match", {}).get("value") == "public"
                 for c in f["should"]
             )),
            None
        )
        assert public_filter is not None


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


# --- require_access_filter — three-layer fail-closed regression ---
#
# The codebase documents (AGENTS.md § Access Control, search.py docstrings) that access
# filters are applied at three layers — Qdrant payload, BM25 SQL, and
# final source merge. Pre-fix only the third layer raised on missing
# ``access_filter``; the first two silently fell through to "no filter".
# These tests pin the new uniform fail-closed behaviour at the chunk
# layer so a future caller cannot regress to the old fail-open shape.


def test_require_access_filter_raises_on_none():
    with pytest.raises(ValueError, match="requires `filters`"):
        require_access_filter(None, "test")


def test_require_access_filter_raises_on_missing_key():
    """Missing ``access_filter`` key (NOT ``access_filter=None``)."""
    with pytest.raises(ValueError, match="`access_filter`"):
        require_access_filter({"person_id": 5}, "test")  # type: ignore[arg-type]


def test_require_access_filter_accepts_explicit_none():
    """``access_filter=None`` is the explicit superadmin opt-in."""
    out = require_access_filter({"access_filter": None}, "test")
    assert out == {"access_filter": None}


def test_require_access_filter_accepts_real_filter():
    af = AccessFilter(conditions=[])
    out = require_access_filter({"access_filter": af}, "test")
    assert out.get("access_filter") is af


@pytest.mark.asyncio
async def test_search_chunks_raises_on_none_filters():
    """Top-level search_chunks must fail-closed on None filters."""
    with pytest.raises(ValueError, match="`access_filter`"):
        await search_chunks([DataChunk(data=["q"])], modalities={"text"})


@pytest.mark.asyncio
async def test_search_chunks_raises_on_missing_access_filter_key():
    """Filters dict without access_filter key must fail-closed."""
    with pytest.raises(ValueError, match="`access_filter`"):
        await search_chunks(
            [DataChunk(data=["q"])],
            modalities={"text"},
            filters={"person_id": 5},  # type: ignore[typeddict-item]
        )


@pytest.mark.asyncio
async def test_search_chunks_embeddings_raises_on_missing_access_filter():
    """Outer entry-point search_chunks_embeddings also fail-closes."""
    with pytest.raises(ValueError, match="`access_filter`"):
        await search_chunks_embeddings(
            [DataChunk(data=["q"])],
            modalities={"text"},
            filters={"min_size": 100},  # type: ignore[typeddict-item]
        )
