import logging
import asyncio
from typing import Any, Callable, Final, cast, TYPE_CHECKING

import qdrant_client
from qdrant_client.http import models as qdrant_models

from memory.common import embedding, extract, qdrant
from memory.common.collections import (
    MULTIMODAL_COLLECTIONS,
    TEXT_COLLECTIONS,
)
from memory.api.search.filters import (
    FILTER_REGISTRY,
    SPECIAL_FILTER_KEYS,
    build_registry_qdrant_filters,
    reject_unknown_filter_keys,
    resolve_account_ids,
)
from memory.api.search.types import SearchFilters
from memory.common.db.connection import make_session

# Qdrant accepts every logical filter key. Registry filters become payload
# conditions; the special keys are hand-coded (access/person compound
# conditions, source_id/confidence/observation payload shapes, and created_at
# which has no Qdrant equivalent). Anything else is rejected loudly.
QDRANT_ALLOWED_FILTER_KEYS = set(FILTER_REGISTRY) | SPECIAL_FILTER_KEYS

if TYPE_CHECKING:
    from memory.common.access_control import AccessFilter

logger = logging.getLogger(__name__)


class NoAccess:
    """Sentinel type for the deny-all return of ``build_access_qdrant_filter``.

    The deny-all return must be unambiguously distinguishable from the
    "superadmin / no filter needed" return (an empty ``list``). A
    type-distinct sentinel (rather than an empty container) prevents the
    natural refactors that would silently turn deny into allow-all:

    1. ``access_conditions == []`` short-circuiting before a deny-check —
       a ``NoAccess`` instance is not equal to ``[]``.
    2. Reordering ``if access_conditions:`` before the deny-check so an
       empty deny would fall through as "no filter applied" — ``NoAccess``
       remains falsy (compat) but ``isinstance(x, NoAccess)`` still
       discriminates regardless of falsiness.
    3. Normalising return values to a single type — ``list[...] | NoAccess``
       in the signature catches at type-check time.

    Identity / pickle round-tripping is **not** load-bearing: the only
    production consumer uses ``isinstance(x, NoAccess)``. We therefore
    keep the class minimal — no ``__new__``/``__eq__``/``__hash__``/
    ``__reduce__`` machinery — and rely on type-distinctness rather than
    instance-distinctness. ``__slots__ = ()`` keeps it cheap and prevents
    accidental attribute attachment.

    The module-level :data:`NO_ACCESS` is the canonical instance returned
    from :func:`build_access_qdrant_filter`. Constructing additional
    ``NoAccess()`` instances is fine — they all pass ``isinstance(x,
    NoAccess)`` — but production code returns the module singleton.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "NO_ACCESS"

    def __bool__(self) -> bool:
        # Falsy so ``if access_conditions:`` skips the deny branch the
        # same way it does for an empty list. ``isinstance`` is the
        # authoritative discriminator; falsiness is a convenience only.
        return False


# Canonical instance returned from ``build_access_qdrant_filter``. The
# ``Final`` annotation flags accidental rebinding from elsewhere in the
# codebase to the type checker.
NO_ACCESS: Final[NoAccess] = NoAccess()


def build_access_qdrant_filter(
    access_filter: "AccessFilter | None",
) -> "list[dict[str, Any]] | NoAccess":
    """
    Build Qdrant filter conditions from an AccessFilter.

    Returns a list of "should" conditions where ANY one must match for access.
    Conditions include:
    - Person override: user's person is in the 'people' array
    - Public bypass: sensitivity is "public"
    - Project access: project_id matches a project + sensitivity is allowed

    Three meaningful return values:

    - ``[]`` (fresh empty list): superadmin / no filtering needed. Caller
      must apply no access filter to the Qdrant query.
    - :data:`NO_ACCESS` (the canonical :class:`NoAccess` instance): user
      has no access at all. Caller must short-circuit and return zero
      results. Detect with ``isinstance(x, NoAccess)`` — the canonical
      check; type-distinct from ``list``/``tuple``/empty containers so
      no accidental ``== []`` short-circuit can mask the deny.
    - non-empty list: ``should`` conditions to include in the Qdrant
      filter.

    Note: Unlike BM25, Qdrant stores *resolved* values in the payload at ingestion
    time (project_id and sensitivity already include inheritance from data sources).
    """
    if access_filter is None:
        # Superadmin - no access filtering
        return []

    should_conditions: list[dict[str, Any]] = []

    # Person override: if user's person is in the 'people' array, grant access
    if access_filter.person_id is not None:
        should_conditions.append({
            "key": "people",
            "match": {"any": [access_filter.person_id]}
        })

    # Public bypass: items with sensitivity "public" are visible to all authenticated users
    if access_filter.include_public:
        should_conditions.append({
            "key": "sensitivity",
            "match": {"value": "public"}
        })

    # Project access conditions
    for condition in access_filter.conditions:
        # Each condition: project_id must match AND sensitivity must be in allowed set
        project_condition = {
            "must": [
                {"key": "project_id", "match": {"value": condition.project_id}},
                {"key": "sensitivity", "match": {"any": list(condition.sensitivities)}},
            ]
        }
        should_conditions.append(project_condition)

    if not should_conditions:
        # No access conditions at all - return the type-distinct NO_ACCESS
        # sentinel. Consumers MUST detect this via ``isinstance(x,
        # NoAccess)`` and short-circuit; otherwise the empty list would
        # silently fall through as "no filter applied" — i.e. the user
        # would see everything.
        return NO_ACCESS

    return should_conditions


def build_person_filter(person_id: int) -> dict[str, Any]:
    """
    Build a Qdrant filter for person_id.

    Returns a filter that matches items where:
    - 'people' field is null/missing/empty (item not associated with specific people), OR
    - 'people' field contains the given person_id

    This ensures items without people associations are still returned,
    while items with people associations only return if the person is included.

    Note: We use 'is_empty' rather than 'is_null' because as_payload() returns
    an empty list [] for items without people, not null. In Qdrant, 'is_empty'
    matches null, missing, AND empty arrays, while 'is_null' only matches truly
    null/missing fields.
    """
    return {
        "should": [
            {"is_empty": {"key": "people"}},
            {"key": "people", "match": {"any": [person_id]}},
        ],
    }


async def query_chunks(
    client: qdrant_client.QdrantClient,
    upload_data: list[extract.DataChunk],
    allowed_modalities: set[str],
    embedder: Callable,
    min_score: float = 0.3,
    limit: int = 10,
    filters: dict[str, Any] | None = None,
) -> dict[str, list[qdrant_models.ScoredPoint]]:
    if not upload_data or not allowed_modalities:
        return {}

    chunks = [chunk for chunk in upload_data if chunk.data]
    if not chunks:
        logger.error(f"No chunks to embed for {allowed_modalities}")
        return {}

    vectors = embedder(chunks, input_type="query")

    # Create all search tasks to run in parallel
    search_tasks = []
    task_metadata = []  # Keep track of which collection and vector each task corresponds to

    for collection in allowed_modalities:
        for vector in vectors:
            task = asyncio.to_thread(
                qdrant.search_vectors,
                client=client,
                collection_name=collection,
                query_vector=vector,
                limit=limit,
                filter_params=filters,
            )
            search_tasks.append(task)
            task_metadata.append((collection, vector))

    # Run all searches in parallel
    if not search_tasks:
        return {}

    search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    # Group results by collection
    results_by_collection: dict[str, list[qdrant_models.ScoredPoint]] = {
        collection: [] for collection in allowed_modalities
    }

    for (collection, _), result in zip(task_metadata, search_results):
        if isinstance(result, Exception):
            logger.error(f"Search failed for collection {collection}: {result}")
            continue

        # Filter by min_score and add to collection results
        result_list = cast(list[qdrant_models.ScoredPoint], result)
        filtered_results = [r for r in result_list if r.score >= min_score]
        results_by_collection[collection].extend(filtered_results)

    return results_by_collection


def build_qdrant_special_filters(filters: "SearchFilters") -> list[dict[str, Any]]:
    """Qdrant filters for keys hand-coded outside the shared registry.

    Covers the Qdrant-specific payload shapes:
      - source_ids -> payload key "source_id" (singular) match.any
      - min_confidences -> "confidence.{type}" range
      - observation_types -> "observation_types" match.any

    access_filter and person_id are NOT handled here — they build compound
    conditions in :func:`search_chunks`.

    min/max_created_at are deliberately NOT emitted as Qdrant conditions: the
    chunk payload (``item_metadata``, built from ``SourceItem.as_payload()``)
    carries no created_at/inserted_at key, so a range on it would match zero
    points and silently drop every vector hit on a dated search. The bound is
    honored on the BM25/SQL arm (``Chunk.created_at``) instead, so created_at
    stays in ``QDRANT_ALLOWED_FILTER_KEYS`` rather than raising. Known
    limitation: because the two arms are RRF-fused, a vector hit outside the
    range can still surface via the embedding arm; closing that needs a
    created_at payload field + reindex (out of scope here).
    """
    result: list[dict[str, Any]] = []
    if source_ids := filters.get("source_ids"):
        result.append({"key": "source_id", "match": {"any": source_ids}})
    if account := filters.get("account"):
        # Qdrant has no join, so resolve the address to account id(s) and match
        # the indexed email_account_id payload key. No match -> [] -> matches
        # nothing, which is correct for an unknown account address.
        with make_session() as session:
            account_ids = resolve_account_ids(account, session)
        result.append({"key": "email_account_id", "match": {"any": account_ids}})
    if observation_types := filters.get("observation_types"):
        result.append({"key": "observation_types", "match": {"any": observation_types}})
    if min_confidences := filters.get("min_confidences"):
        result.extend(
            {"key": f"confidence.{ctype}", "range": {"gte": score}}
            for ctype, score in cast(dict, min_confidences).items()
        )
    return result


def require_access_filter(filters: "SearchFilters | None", caller: str) -> "SearchFilters":
    """Fail-closed gate on the documented three-layer access invariant.

    The codebase claims (db/CLAUDE.md, search.py docstrings) that access
    filters are applied at three layers — Qdrant payload, BM25 SQL, and
    final source merge. Pre-fix only the third layer raised on missing
    ``access_filter``; the first two silently fell through to "no filter"
    when callers forgot to thread the key. This helper makes the same
    fail-closed semantics uniform across all three.

    Pass ``filters={"access_filter": None}`` for the explicit superadmin
    case (admin builds the filter as ``None`` deliberately). A *missing*
    key is treated as a programming error — defense-in-depth that's only
    effective when callers remember to opt in is a hint, not a defense.
    """
    if filters is None or "access_filter" not in filters:
        raise ValueError(
            f"{caller} requires `filters` with an `access_filter` key. "
            "Pass `access_filter=None` for explicit superadmin/no-filter "
            "semantics. The check matches the documented three-layer "
            "access-control invariant — see search_sources for the "
            "matching final-merge fail-closed."
        )
    return filters


async def search_chunks(
    data: list[extract.DataChunk],
    modalities: set[str] | None = None,
    limit: int = 10,
    min_score: float = 0.3,
    filters: SearchFilters | None = None,
    multimodal: bool = False,
) -> dict[str, float]:
    """
    Search across knowledge base using text query and optional files.

    Parameters:
    - data: List of data to search in (e.g., text, images, files)
    - modalities: List of modalities to search in (e.g., "text", "photo", "doc")
    - limit: Maximum number of results
    - min_score: Minimum score to include in the search results
    - filters: Filters to apply to the search results — MUST contain an
      ``access_filter`` key. Pass ``access_filter=None`` for explicit
      superadmin / no-filter semantics.
    - multimodal: Whether to search in multimodal collections

    Returns:
    - Dictionary mapping chunk IDs to their similarity scores

    Raises:
        ValueError: ``filters`` is None, or ``access_filter`` is not a key
            in ``filters``. Closes the documented three-layer access-control
            invariant for the Qdrant layer (search_sources / final-merge
            already enforces this; this is the same fail-closed at the
            vector-search layer so a future caller that forgets
            ``access_filter`` cannot silently widen results).
    """
    filters = require_access_filter(filters, "search_chunks")
    reject_unknown_filter_keys(filters, allowed=QDRANT_ALLOWED_FILTER_KEYS)
    if modalities is None:
        modalities = set()

    # Registry filters (raises if an UNSUPPORTED filter like subject is passed,
    # so it fails loudly instead of leaking unfiltered results) plus the
    # Qdrant-specific special-key shapes. access_filter and person_id build
    # compound conditions below.
    search_filters = build_registry_qdrant_filters(filters)
    search_filters.extend(build_qdrant_special_filters(filters))

    # Build the complete Qdrant filter
    qdrant_filter: dict[str, Any] = {}

    if search_filters:
        qdrant_filter["must"] = search_filters

    # Add person_id filter if present
    # This matches items where 'people' is null OR contains the person_id
    #
    # Both Qdrant and BM25 implement equivalent filtering logic:
    # - Qdrant: Uses 'people' field in payload (populated via SourceItem.as_payload())
    # - BM25: Uses source_item_people junction table
    #
    # Person associations are populated during ingestion for: Meetings, Emails,
    # Slack/Discord messages, GoogleDocs, Tasks, and CalendarEvents.
    if (person_id := filters.get("person_id")) is not None:
        person_filter = build_person_filter(person_id)
        if "must" not in qdrant_filter:
            qdrant_filter["must"] = []
        qdrant_filter["must"].append(person_filter)

    # Add access control filter if present
    # Wrap in a nested Filter inside must for consistent structure
    access_filter = filters.get("access_filter")
    access_conditions = build_access_qdrant_filter(access_filter)
    # Detect "no access" via the type-distinct ``NoAccess`` sentinel.
    # The deny-all return is a separate type from ``list[...]`` so it
    # cannot be confused with ``[]`` (superadmin / no filter needed) by
    # any consumer — ``isinstance`` is the canonical discriminator and
    # ``==``-based refactors cannot silently turn deny into allow-all.
    if isinstance(access_conditions, NoAccess):
        return {}
    if access_conditions:
        if "must" not in qdrant_filter:
            qdrant_filter["must"] = []
        # Wrap as nested Filter with should. The 'should' clause requires
        # at least one condition to match by default. This ensures proper
        # AND semantics with other must conditions.
        access_nested_filter = {
            "should": access_conditions,
        }
        qdrant_filter["must"].append(access_nested_filter)

    client = qdrant.get_qdrant_client()
    results = await query_chunks(
        client,
        data,
        modalities,
        embedding.embed_text if not multimodal else embedding.embed_mixed,
        min_score=min_score,
        limit=limit,
        filters=qdrant_filter if qdrant_filter else None,
    )
    search_results = {k: results.get(k, []) for k in modalities}

    # Return chunk IDs with their scores (take max score if chunk appears multiple times)
    found_chunks: dict[str, float] = {}
    for collection_results in search_results.values():
        for r in collection_results:
            chunk_id = str(r.id)
            if chunk_id not in found_chunks or r.score > found_chunks[chunk_id]:
                found_chunks[chunk_id] = r.score
    return found_chunks


async def search_chunks_embeddings(
    data: list[extract.DataChunk],
    modalities: set[str] | None = None,
    limit: int = 10,
    filters: SearchFilters | None = None,
    timeout: int = 2,
) -> dict[str, float]:
    """
    Search chunks using embeddings across text and multimodal collections.

    ``filters`` MUST carry an ``access_filter`` key (use ``None`` for
    explicit superadmin) — see :func:`require_access_filter`.

    Returns:
    - Dictionary mapping chunk IDs to their similarity scores
    """
    filters = require_access_filter(filters, "search_chunks_embeddings")
    if modalities is None:
        modalities = set()
    # Note: Multimodal embeddings typically produce higher similarity scores,
    # so we use a higher threshold (0.4) to maintain selectivity.
    # Text embeddings produce lower scores, so we use 0.25.
    all_results = await asyncio.gather(
        asyncio.wait_for(
            search_chunks(
                data,
                modalities & TEXT_COLLECTIONS,
                limit,
                0.25,
                filters,
                False,
            ),
            timeout,
        ),
        asyncio.wait_for(
            search_chunks(
                data,
                modalities & MULTIMODAL_COLLECTIONS,
                limit,
                0.4,
                filters,
                True,
            ),
            timeout,
        ),
    )
    # Merge scores, taking max if chunk appears in both
    merged_scores: dict[str, float] = {}
    for result_dict in all_results:
        for chunk_id, score in result_dict.items():
            if chunk_id not in merged_scores or score > merged_scores[chunk_id]:
                merged_scores[chunk_id] = score
    return merged_scores
