"""Single source of truth for translating logical search filters.

The same logical filters (``MCPSearchFilters``) need to be turned into three
different backend queries:

1. list/count SQL over ``SourceItem`` (``apply_item_filters`` in core.py)
2. BM25 SQL over ``Chunk`` joined to ``SourceItem`` (``search_bm25`` in bm25.py)
3. Qdrant payload filters (``search_chunks`` in embeddings.py)

Historically each backend hand-maintained its own mapping, and they drifted:
a filter implemented in one arm but skipped in another silently produced
confidently-wrong results (e.g. ``recipients`` honored by Qdrant but ignored
by BM25, so non-matching mail leaked into the RRF-merged output).

This module declares each content-metadata filter once as a :class:`FilterSpec`
and exposes thin translators that fold the registry onto a SQLAlchemy query or
into Qdrant filter dicts. A completeness invariant (asserted at import and
covered by a pure-Python test) forces every new ``MCPSearchFilters`` key to be
wired here or explicitly marked special.

Filters that are genuinely backend-specific and hand-coded (access control,
person association, source-id prefiltering, confidence scores, observation
collection routing, and the created_at divergence described below) live in
:data:`SPECIAL_FILTER_KEYS` rather than the registry.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import cast as sql_cast
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy import Text
from sqlalchemy.orm import Session

from memory.common.db.models import (
    BlogPost,
    EmailAccount,
    GoogleDoc,
    MailMessage,
    SourceItem,
)
from memory.api.search.types import MCPSearchFilters, SearchFilters


class SqlOp(Enum):
    """How a filter value constrains a SQL column."""

    EQ = "eq"
    GTE = "gte"
    LTE = "lte"
    IN = "in"
    ARRAY_ANY = "array_any"  # Postgres ``&&`` overlap on an ARRAY column
    ILIKE_SUBSTR = "ilike_substr"  # case-insensitive substring match
    # case-insensitive substring against any element of an ARRAY column, via
    # array_to_string; matches if any of the (list-valued) filter values is a
    # substring of any element.
    ARRAY_ILIKE_SUBSTR = "array_ilike_substr"


class QdrantOp(Enum):
    """How a filter value constrains a Qdrant payload field."""

    MATCH_VALUE = "match_value"  # exact scalar match
    MATCH_ANY = "match_any"  # list membership (match.any)
    RANGE_GTE = "range_gte"
    RANGE_LTE = "range_lte"


# Sentinel meaning "this filter has no faithful Qdrant equivalent". A Qdrant
# translator that is handed such a filter must raise rather than silently drop
# it or emit a filter with different semantics.
class QdrantUnsupported:
    """No faithful Qdrant translation exists for this filter.

    ``subject`` is matched in SQL with a case-insensitive substring (ILIKE).
    Qdrant only offers exact ``match.value`` (the payload has no full-text
    index on ``subject``), so a Qdrant ``subject`` filter would silently
    change the semantics from substring to exact. Rather than lie, a Qdrant
    caller that passes ``subject`` gets a loud ``ValueError``.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "QDRANT_UNSUPPORTED"


QDRANT_UNSUPPORTED = QdrantUnsupported()


@dataclass(frozen=True)
class FilterSpec:
    """Declarative description of one logical filter across all backends.

    Attributes:
        key: The ``MCPSearchFilters`` TypedDict key.
        model: Target ORM model. ``None`` means the column lives on
            ``SourceItem`` itself; otherwise the model is a joined-table
            subclass joined on ``model.id == SourceItem.id``.
        column: Attribute name of the column on ``model`` (or ``SourceItem``).
        sql_op: How the value constrains that column in SQL.
        qdrant_key: Payload key in Qdrant, or ``None`` when unsupported.
        qdrant_op: Qdrant operator, or :data:`QDRANT_UNSUPPORTED`.
        array_cast: When ``True`` the bound value is cast to ``ARRAY(Text)``
            before the SQL comparison (matches the ARRAY(Text) columns).
        lowercase_value: When ``True`` the filter value is lowercased before the
            Qdrant exact match. The SQL arm is case-insensitive already (ILIKE);
            this keeps the case-sensitive Qdrant arm in agreement when the
            payload key was stored lowercased (e.g. the derived email keys).
    """

    key: str
    model: type | None
    column: str
    sql_op: SqlOp
    qdrant_key: str | None
    qdrant_op: QdrantOp | QdrantUnsupported
    array_cast: bool = False
    lowercase_value: bool = False

    def target_model(self) -> type:
        return self.model or SourceItem

    def column_attr(self):
        return getattr(self.target_model(), self.column)


# Content-metadata filters. Each min/max pair is two specs on the same column.
#
# Qdrant payload keys are whatever ``as_payload()`` emits per subclass
# (merged into ``Chunk.item_metadata`` at ingestion). Notably:
#   - MailMessage emits the sent date under "date" (NOT "sent_at"), and
#     emits "folder" (NOT "folder_path").
#   - "subject" exists in the mail payload but only supports exact match in
#     Qdrant, so it is marked QDRANT_UNSUPPORTED (see QdrantUnsupported).
#
# WARNING: only "tags", "people" and the keys in qdrant.EXTRA_PAYLOAD_INDEXES
# ("sender_email", "recipient_emails", "folder", "email_account_id") have a
# Qdrant payload index (see qdrant.ensure_payload_indexes / create_payload_index).
# Filtering an unindexed payload key may silently match nothing in Qdrant, so any
# spec routed to Qdrant must target an indexed key (add it to
# EXTRA_PAYLOAD_INDEXES) or be QDRANT_UNSUPPORTED.
_SPECS: tuple[FilterSpec, ...] = (
    FilterSpec("tags", None, "tags", SqlOp.ARRAY_ANY, "tags", QdrantOp.MATCH_ANY, array_cast=True),
    FilterSpec("min_size", None, "size", SqlOp.GTE, "size", QdrantOp.RANGE_GTE),
    FilterSpec("max_size", None, "size", SqlOp.LTE, "size", QdrantOp.RANGE_LTE),
    # Mail.
    #
    # The stored ``sender``/``recipients`` columns hold raw header strings:
    # MIME-encoded display names and inconsistent ``Name <addr>`` vs bare-addr
    # forms, so the same mailbox is smeared across variants. SQL matches them
    # with a case-insensitive substring (the bare address is always plaintext
    # in the header, even when the display name is MIME-encoded), which finds
    # a mailbox across every stored variant including legacy rows.
    #
    # Qdrant has no substring operator, so it instead filters the *derived*
    # payload keys ``sender_email``/``recipient_emails`` (bare, lowercased
    # addresses parsed in MailMessage.as_payload(), KEYWORD-indexed) with exact
    # match — correct because the derived values are normalized. ``lowercase_value``
    # lowercases the query so the case-sensitive Qdrant match agrees with the
    # case-insensitive SQL ILIKE arm. The two arms use different operators on
    # purpose; substring is a strict superset of the exact matches, so the RRF
    # merge never leaks a non-matching mail (a full-address query agrees on both;
    # a partial-substring query matches only on the SQL/BM25 arm).
    FilterSpec(
        "sender", MailMessage, "sender", SqlOp.ILIKE_SUBSTR,
        "sender_email", QdrantOp.MATCH_VALUE, lowercase_value=True,
    ),
    FilterSpec(
        "recipients", MailMessage, "recipients", SqlOp.ARRAY_ILIKE_SUBSTR,
        "recipient_emails", QdrantOp.MATCH_ANY, lowercase_value=True,
    ),
    FilterSpec("subject", MailMessage, "subject", SqlOp.ILIKE_SUBSTR, None, QDRANT_UNSUPPORTED),
    # Mail folder is a discrete IMAP identifier ("INBOX", "[Gmail]/Sent Mail"),
    # so exact match on both backends. Distinct from GoogleDoc.folder_path below.
    FilterSpec("folder", MailMessage, "folder", SqlOp.EQ, "folder", QdrantOp.MATCH_VALUE),
    FilterSpec("min_sent_at", MailMessage, "sent_at", SqlOp.GTE, "date", QdrantOp.RANGE_GTE),
    FilterSpec("max_sent_at", MailMessage, "sent_at", SqlOp.LTE, "date", QdrantOp.RANGE_LTE),
    # Google Docs
    FilterSpec(
        "folder_path", GoogleDoc, "folder_path", SqlOp.ILIKE_SUBSTR,
        "folder_path", QdrantOp.MATCH_VALUE,
    ),
    # Blog
    FilterSpec("domain", BlogPost, "domain", SqlOp.EQ, "domain", QdrantOp.MATCH_VALUE),
    FilterSpec("author", BlogPost, "author", SqlOp.EQ, "author", QdrantOp.MATCH_VALUE),
    FilterSpec("authors", BlogPost, "author", SqlOp.IN, "authors", QdrantOp.MATCH_ANY),
    FilterSpec("min_published", BlogPost, "published", SqlOp.GTE, "published", QdrantOp.RANGE_GTE),
    FilterSpec("max_published", BlogPost, "published", SqlOp.LTE, "published", QdrantOp.RANGE_LTE),
)

FILTER_REGISTRY: dict[str, FilterSpec] = {spec.key: spec for spec in _SPECS}


# Keys deliberately NOT in the registry because they are backend-specific and
# hand-coded in each consumer.
#
# created_at is special because the two SQL backends scope it to *different*
# columns on purpose:
#   - list/count filter SourceItem.inserted_at (when the item entered the KB)
#   - bm25 filters Chunk.created_at (when the chunk was created)
#   - Qdrant has no per-chunk created_at payload key at all.
# These are not interchangeable (a chunk can be re-derived after the item was
# first inserted), so they are intentionally NOT unified into one registry
# spec. If a future change makes them provably identical, fold them in.
# ``account`` is special because the user supplies an email *address* but the
# filter constrains ``MailMessage.email_account_id`` (an FK). The address must be
# resolved to account id(s) first, which the registry's value->column mapping
# can't express. SQL resolves it inline via a subquery; Qdrant resolves to ids
# in Python (it has no join), so the two arms are hand-coded per backend.
SPECIAL_FILTER_KEYS: frozenset[str] = frozenset(
    {
        "access_filter",
        "person_id",
        "account",
        "source_ids",
        "min_confidences",
        "observation_types",
        "min_created_at",
        "max_created_at",
    }
)


def account_match_sql(value: str):
    """SQL clause selecting SourceItem rows that are mail ingested by an account
    whose address equals ``value`` (case-insensitive).

    A nested subquery on ``SourceItem.id`` keeps this join-free, so it composes
    with the registry's mail-subclass join without any double-join coordination,
    and works identically over the list/count query (on SourceItem) and the BM25
    query (Chunk joined to SourceItem). An address matching no account yields an
    empty inner set -> matches nothing, which is correct.
    """
    account_ids = select(EmailAccount.id).where(
        func.lower(EmailAccount.email_address) == value.lower()
    )
    mail_ids = select(MailMessage.id).where(
        MailMessage.email_account_id.in_(account_ids)
    )
    return SourceItem.id.in_(mail_ids)


def resolve_account_ids(value: str, session: Session) -> list[int]:
    """Account ids whose address equals ``value`` (case-insensitive).

    The Qdrant arm has no subquery/join, so it filters the indexed
    ``email_account_id`` payload key against this resolved id list.
    """
    return list(
        session.scalars(
            select(EmailAccount.id).where(
                func.lower(EmailAccount.email_address) == value.lower()
            )
        )
    )


def mcp_filter_keys() -> set[str]:
    return set(MCPSearchFilters.__annotations__)


def search_only_keys() -> set[str]:
    """SearchFilters keys NOT in MCPSearchFilters (internal-only).

    ``SearchFilters`` is a ``TypedDict`` subclass; in Python 3.12 its
    ``__annotations__`` carries both its own and the inherited keys.
    Subtracting the MCP keys yields exactly the internal additions
    (``source_ids``/``access_filter``).
    """
    return set(SearchFilters.__annotations__) - mcp_filter_keys()


def check_registry_completeness() -> None:
    """Raise if the registry has drifted from the filter TypedDicts.

    Every MCP filter key must be either in :data:`FILTER_REGISTRY` or in
    :data:`SPECIAL_FILTER_KEYS` (and not both). Every ``SearchFilters``-only
    key (``source_ids``/``access_filter``) must be special. This is the
    anti-drift guarantee: adding a TypedDict key without wiring it everywhere
    fails here.
    """
    mcp_keys = mcp_filter_keys()
    search_only = search_only_keys()
    all_keys = mcp_keys | search_only
    registry_keys = set(FILTER_REGISTRY)
    overlap = registry_keys & SPECIAL_FILTER_KEYS
    if overlap:
        raise ValueError(f"keys both in registry and special: {sorted(overlap)}")

    # The covered universe is every SearchFilters key (MCP keys plus the
    # internal-only ``source_ids``/``access_filter``). A registry/special key
    # not present anywhere in SearchFilters signals a typo or removed field.
    covered = registry_keys | SPECIAL_FILTER_KEYS
    missing = mcp_keys - covered
    extra = covered - all_keys
    if missing:
        raise ValueError(
            f"MCPSearchFilters keys not wired in registry or SPECIAL_FILTER_KEYS: "
            f"{sorted(missing)}"
        )
    if extra:
        raise ValueError(
            f"registry/special keys not present in SearchFilters: {sorted(extra)}"
        )

    not_special = search_only - SPECIAL_FILTER_KEYS
    if not_special:
        raise ValueError(
            f"SearchFilters-only keys must be in SPECIAL_FILTER_KEYS: "
            f"{sorted(not_special)}"
        )


# Fail fast at import if anyone edits MCPSearchFilters without wiring it.
check_registry_completeness()


def escape_like(s: str) -> str:
    """Escape ILIKE metacharacters to prevent pattern injection."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def apply_spec_sql(query, spec: FilterSpec, value: Any, *, joined: set[type]):
    """Fold one registry spec onto a SQLAlchemy query.

    ``joined`` tracks subclass models already joined to ``SourceItem`` so each
    is joined at most once (joins are 1:1 on ``id``, so they never multiply
    rows). Returns the updated query.
    """
    model = spec.model
    if model is not None and model not in joined:
        # Join the raw subclass table (not the polymorphic entity) so the
        # ON clause binds to the outer SourceItem row. Joining the entity
        # re-aliases source_item and yields a cartesian product.
        table = model.__table__
        query = query.join(table, table.c.id == SourceItem.id)
        joined.add(model)

    column = spec.column_attr()

    if spec.sql_op is SqlOp.EQ:
        return query.filter(column == value)
    if spec.sql_op is SqlOp.GTE:
        return query.filter(column >= value)
    if spec.sql_op is SqlOp.LTE:
        return query.filter(column <= value)
    if spec.sql_op is SqlOp.IN:
        return query.filter(column.in_(value))
    if spec.sql_op is SqlOp.ARRAY_ANY:
        # The Postgres `&&` overlap needs an array on both sides. A scalar here
        # (e.g. tags="foo" instead of ["foo"]) would cast to a malformed array
        # and fail at execution; reject it loudly at build time instead.
        if not isinstance(value, (list, tuple)):
            raise ValueError(
                f"filter {spec.key!r} expects a list value, got {type(value).__name__}"
            )
        bound = sql_cast(value, ARRAY(Text)) if spec.array_cast else value
        return query.filter(column.op("&&")(bound))
    if spec.sql_op is SqlOp.ILIKE_SUBSTR:
        return query.filter(column.ilike(f"%{escape_like(value)}%", escape="\\"))
    if spec.sql_op is SqlOp.ARRAY_ILIKE_SUBSTR:
        if not isinstance(value, (list, tuple)):
            raise ValueError(
                f"filter {spec.key!r} expects a list value, got {type(value).__name__}"
            )
        # Flatten the array to a single string so one ILIKE scans every element.
        # The unit-separator (0x1f) can't occur in an email header, so a query
        # value can't accidentally span two elements (and unlike NUL it is a
        # legal Postgres string literal). Match if any value is a substring (OR).
        flattened = func.array_to_string(column, "\x1f")
        clauses = [
            flattened.ilike(f"%{escape_like(v)}%", escape="\\") for v in value
        ]
        return query.filter(or_(*clauses)) if clauses else query
    raise ValueError(f"unhandled SQL op {spec.sql_op}")  # pragma: no cover


def lowercase_filter_value(value: Any) -> Any:
    """Lowercase a scalar or list filter value; pass anything else through.

    Used for email-address filters so the case-sensitive Qdrant exact match
    agrees with the lowercased payload (and the case-insensitive SQL arm).
    """
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, (list, tuple)):
        return [v.lower() if isinstance(v, str) else v for v in value]
    return value


def is_empty_value(value: Any) -> bool:
    """A filter value that should be treated as 'not provided'.

    Matches the falsy-but-keep-zero convention used by the legacy mappers:
    ``None``/``[]``/``{}``/``""`` are skipped, but ``0`` is a real bound.
    """
    return value is None or value == [] or value == {} or value == ""


def apply_registry_filters_sql(query, filters, *, joined: set[type] | None = None):
    """Fold all registry filters present in ``filters`` onto a SQL query.

    Used by both list/count (query over ``SourceItem``) and BM25 (query over
    ``Chunk`` already joined to ``SourceItem``); the same ``model.id ==
    SourceItem.id`` join works in both because the join is 1:1.

    Combining filters from two different subclasses can never match a row (an
    item is exactly one subclass) — that is the correct outcome, not a bug.

    Pass ``joined`` to share join-tracking with a caller that has already
    joined some subclass tables. Returns the updated query.
    """
    if joined is None:
        joined = set()
    for key, spec in FILTER_REGISTRY.items():
        value = filters.get(key)
        if is_empty_value(value):
            continue
        query = apply_spec_sql(query, spec, value, joined=joined)
    return query


def merge_range(filters: list[dict[str, Any]], key: str, op: QdrantOp, value: Any) -> None:
    """Add or extend a Qdrant range filter on payload ``key`` in place."""
    item = next((f for f in filters if f.get("key") == key and "range" in f), None)
    if item is None:
        item = {"key": key, "range": {}}
        filters.append(item)
    if op is QdrantOp.RANGE_GTE:
        item["range"]["gte"] = value
    elif op is QdrantOp.RANGE_LTE:
        item["range"]["lte"] = value


def build_registry_qdrant_filters(filters) -> list[dict[str, Any]]:
    """Translate registry filters present in ``filters`` into Qdrant dicts.

    Raises ``ValueError`` if a filter whose Qdrant translation is
    :data:`QDRANT_UNSUPPORTED` (e.g. ``subject``) was provided, so an
    unsupported Qdrant filter fails loudly instead of leaking unfiltered
    results.
    """
    result: list[dict[str, Any]] = []
    for key, spec in FILTER_REGISTRY.items():
        value = filters.get(key)
        if is_empty_value(value):
            continue
        if isinstance(spec.qdrant_op, QdrantUnsupported):
            raise ValueError(
                f"filter {key!r} has no faithful Qdrant translation "
                f"(SQL uses {spec.sql_op.value}); it cannot be applied to a "
                "vector search"
            )
        qkey = spec.qdrant_key
        # Past the QDRANT_UNSUPPORTED guard above, every remaining spec pairs a
        # concrete payload key with its qdrant_op; the registry never leaves
        # qdrant_key None alongside a usable op.
        assert qkey is not None
        if spec.lowercase_value:
            value = lowercase_filter_value(value)
        if spec.qdrant_op is QdrantOp.MATCH_VALUE:
            result.append({"key": qkey, "match": {"value": value}})
        elif spec.qdrant_op is QdrantOp.MATCH_ANY:
            result.append({"key": qkey, "match": {"any": value}})
        elif spec.qdrant_op in (QdrantOp.RANGE_GTE, QdrantOp.RANGE_LTE):
            merge_range(result, qkey, spec.qdrant_op, value)
    return result


def reject_unknown_filter_keys(filters, *, allowed: set[str]) -> None:
    """Raise ``ValueError`` on any non-empty filter key outside ``allowed``.

    One consistent loud failure for all three backends, replacing core's
    ad-hoc ValueError, Qdrant's warn-and-ignore, and BM25's silent skip. A
    filter a backend forgot to implement becomes an error instead of a
    confidently-wrong result.
    """
    leftover = {
        k for k, v in filters.items() if not is_empty_value(v) and k not in allowed
    }
    if leftover:
        raise ValueError(f"Unsupported filter(s): {sorted(leftover)}")
