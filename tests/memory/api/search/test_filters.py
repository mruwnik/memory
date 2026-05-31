"""Tests for the unified search-filter registry (memory.api.search.filters).

These tests are pure-Python where possible: the completeness invariant and the
SQL/Qdrant translation assertions need neither a live DB nor a Qdrant engine
(SQL is checked via compiled-statement strings). The "unknown key raises at all
three entry points" tests touch the three backends' rejection wiring; the
rejection fires before any DB session or Qdrant client opens, so they need no
live infrastructure either.
"""

import asyncio

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Query

from memory.api.search import filters as F
from memory.api.search.bm25 import search_bm25
from memory.api.search.embeddings import search_chunks
from memory.api.search.types import MCPSearchFilters, SearchFilters
from memory.api.MCP.servers.core import apply_item_filters
from memory.common.db.models import Chunk, SourceItem
from memory.common.extract import DataChunk


def compile_sql(query) -> str:
    return str(
        query.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def registry_query(filters: dict):
    return F.apply_registry_filters_sql(Query(SourceItem), filters)


# --- (a) completeness invariant ---------------------------------------------


def test_registry_completeness_holds():
    F.check_registry_completeness()


def test_every_mcp_key_is_registry_or_special():
    mcp_keys = set(MCPSearchFilters.__annotations__)
    assert mcp_keys == set(F.FILTER_REGISTRY) | (
        F.SPECIAL_FILTER_KEYS & mcp_keys
    )


def test_registry_and_special_are_disjoint():
    assert not (set(F.FILTER_REGISTRY) & F.SPECIAL_FILTER_KEYS)


def test_search_only_keys_are_special():
    search_only = set(SearchFilters.__annotations__) - set(
        MCPSearchFilters.__annotations__
    )
    assert search_only == {"source_ids", "access_filter"}
    assert search_only <= F.SPECIAL_FILTER_KEYS


def test_completeness_detects_unwired_key(monkeypatch):
    original = F.mcp_filter_keys()
    monkeypatch.setattr(
        F, "mcp_filter_keys", lambda: original | {"brand_new_filter"}
    )
    with pytest.raises(ValueError, match="not wired"):
        F.check_registry_completeness()


def test_completeness_detects_registry_special_overlap(monkeypatch):
    monkeypatch.setattr(F, "SPECIAL_FILTER_KEYS", F.SPECIAL_FILTER_KEYS | {"tags"})
    with pytest.raises(ValueError, match="both in registry and special"):
        F.check_registry_completeness()


# --- (b) each registry key produces expected SQL ----------------------------


# Joined-table-inheritance subclasses are aliased by SQLAlchemy when SourceItem
# is the primary entity (the subclass row already embeds source_item), so column
# refs appear as ``mail_message_1.sender`` etc. The aliasing predates this
# refactor (the original apply_item_filters joined the same way). We assert on a
# column/op regex rather than a hard-coded table-alias name.


@pytest.mark.parametrize(
    "filters, table, column, op",
    [
        ({"min_sent_at": "2024-01-01"}, "mail_message", "sent_at", ">= '2024-01-01'"),
        ({"max_sent_at": "2024-12-31"}, "mail_message", "sent_at", "<= '2024-12-31'"),
        ({"domain": "example.com"}, "blog_post", "domain", "= 'example.com'"),
        ({"author": "bob"}, "blog_post", "author", "= 'bob'"),
        ({"min_published": "2020-01-01"}, "blog_post", "published", ">= '2020-01-01'"),
        ({"max_published": "2020-12-31"}, "blog_post", "published", "<= '2020-12-31'"),
    ],
)
def test_registry_sql_subclass_fragment(filters, table, column, op):
    sql = compile_sql(registry_query(filters))
    assert f".{column} {op}" in sql
    assert table in sql


@pytest.mark.parametrize(
    "filters, fragment",
    [
        ({"min_size": 10}, "source_item.size >= 10"),
        ({"max_size": 99}, "source_item.size <= 99"),
    ],
)
def test_registry_sql_source_item_fragment(filters, fragment):
    # size lives on SourceItem itself — no subclass join, no alias.
    assert fragment in compile_sql(registry_query(filters))


def test_registry_sql_subject_uses_ilike():
    sql = compile_sql(registry_query({"subject": "hello"}))
    assert ".subject ILIKE" in sql
    assert "%hello%" in sql


def test_registry_sql_sender_uses_ilike_substring():
    # sender is matched as a case-insensitive substring (the bare address is
    # plaintext in the header even when the display name is MIME-encoded), not
    # exact equality.
    sql = compile_sql(registry_query({"sender": "notifications@github.com"}))
    assert ".sender ILIKE" in sql
    assert "%notifications@github.com%" in sql
    assert "mail_message" in sql


def test_registry_sql_recipients_substring_over_array():
    # recipients is an ARRAY column matched by flattening to a string and
    # substring-matching, so a bare address finds every display-name variant.
    sql = compile_sql(registry_query({"recipients": ["github@ahiru.pl"]}))
    assert "array_to_string" in sql
    assert "ILIKE" in sql
    assert "%github@ahiru.pl%" in sql


def test_registry_sql_recipients_multiple_values_or():
    sql = compile_sql(registry_query({"recipients": ["a@x.com", "b@y.com"]}))
    assert "%a@x.com%" in sql
    assert "%b@y.com%" in sql
    assert " OR " in sql


def test_registry_sql_folder_path_uses_ilike_on_google_doc():
    sql = compile_sql(registry_query({"folder_path": "Work"}))
    assert ".folder_path ILIKE" in sql
    assert "google_doc" in sql


def test_account_match_sql_is_outer_subquery():
    # account constrains the outer query as `source_item.id IN (subquery)`, so it
    # composes with the registry's mail-subclass join without double-joining the
    # outer query (the inner subquery's source_item<->mail_message join is JTI
    # mechanics, scoped to the subquery).
    clause = F.account_match_sql("ME@Ahiru.PL")
    sql = str(clause.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    ))
    assert sql.startswith("source_item.id IN (SELECT")
    assert "email_accounts" in sql
    # case-insensitive exact match on the account address (lowered both sides)
    assert "lower(email_accounts.email_address) = 'me@ahiru.pl'" in sql


def test_registry_sql_mail_folder_exact_match():
    # Mail folder is distinct from GoogleDoc.folder_path: exact match on the
    # mail_message subclass, not a substring on google_doc.
    sql = compile_sql(registry_query({"folder": "INBOX"}))
    assert ".folder = 'INBOX'" in sql
    assert "mail_message" in sql
    assert "google_doc" not in sql


def test_registry_sql_tags_array_overlap_cast_text():
    sql = compile_sql(registry_query({"tags": ["x", "y"]}))
    assert "&&" in sql
    # tags is an ARRAY(Text) column, so the cast is TEXT[].
    assert "AS TEXT[]" in sql


def test_registry_sql_authors_uses_in():
    sql = compile_sql(registry_query({"authors": ["a", "b"]}))
    assert ".author IN" in sql
    assert "blog_post" in sql


def test_registry_sql_joins_each_subclass_once():
    sql = compile_sql(
        registry_query({"sender": "a", "subject": "b", "min_sent_at": "2024-01-01"})
    )
    # One join into the mail_message subclass even though three mail filters
    # target it.
    assert sql.count("JOIN mail_message") == 1


def test_registry_sql_empty_values_skipped():
    sql = compile_sql(registry_query({"sender": "", "tags": [], "recipients": None}))
    assert "JOIN" not in sql
    assert "WHERE" not in sql


# --- (b') each registry key produces expected Qdrant filter -----------------


@pytest.mark.parametrize(
    "filters, expected",
    [
        ({"tags": ["x"]}, {"key": "tags", "match": {"any": ["x"]}}),
        # recipients/sender filter the derived clean-address payload keys.
        ({"recipients": ["a"]}, {"key": "recipient_emails", "match": {"any": ["a"]}}),
        ({"authors": ["a"]}, {"key": "authors", "match": {"any": ["a"]}}),
        ({"sender": "s"}, {"key": "sender_email", "match": {"value": "s"}}),
        ({"domain": "d"}, {"key": "domain", "match": {"value": "d"}}),
        ({"author": "a"}, {"key": "author", "match": {"value": "a"}}),
        ({"folder_path": "p"}, {"key": "folder_path", "match": {"value": "p"}}),
        ({"folder": "INBOX"}, {"key": "folder", "match": {"value": "INBOX"}}),
        ({"min_size": 5}, {"key": "size", "range": {"gte": 5}}),
        ({"max_size": 9}, {"key": "size", "range": {"lte": 9}}),
    ],
)
def test_qdrant_single_filter(filters, expected):
    assert F.build_registry_qdrant_filters(filters) == [expected]


@pytest.mark.parametrize(
    "filters, expected",
    [
        ({"sender": "John@X.COM"}, {"key": "sender_email", "match": {"value": "john@x.com"}}),
        (
            {"recipients": ["GitHub@Ahiru.PL", "Two@Y.com"]},
            {"key": "recipient_emails", "match": {"any": ["github@ahiru.pl", "two@y.com"]}},
        ),
    ],
)
def test_qdrant_address_filters_lowercase_value(filters, expected):
    # The Qdrant payload keys store lowercased addresses, so the query value is
    # lowercased too — otherwise a mixed-case query exact-misses on the vector arm.
    assert F.build_registry_qdrant_filters(filters) == [expected]


def test_qdrant_sent_at_maps_to_date_payload_key():
    # Regression: the payload stores the email date under "date", not "sent_at".
    result = F.build_registry_qdrant_filters(
        {"min_sent_at": "2024-01-01", "max_sent_at": "2024-12-31"}
    )
    assert result == [
        {"key": "date", "range": {"gte": "2024-01-01", "lte": "2024-12-31"}}
    ]


def test_qdrant_published_range_merges():
    result = F.build_registry_qdrant_filters(
        {"min_published": "2020-01-01", "max_published": "2020-12-31"}
    )
    assert result == [
        {"key": "published", "range": {"gte": "2020-01-01", "lte": "2020-12-31"}}
    ]


# --- (c) UNSUPPORTED-over-Qdrant raises -------------------------------------


def test_qdrant_subject_raises():
    with pytest.raises(ValueError, match="no faithful Qdrant translation"):
        F.build_registry_qdrant_filters({"subject": "anything"})


def test_qdrant_subject_skipped_when_empty():
    # An empty subject is "not provided" and must not raise.
    assert F.build_registry_qdrant_filters({"subject": ""}) == []


# --- (d) unknown key rejection ----------------------------------------------


def test_reject_unknown_filter_keys_raises():
    with pytest.raises(ValueError, match="Unsupported filter"):
        F.reject_unknown_filter_keys(
            {"bogus": "x"}, allowed=set(F.FILTER_REGISTRY)
        )


def test_reject_unknown_filter_keys_ignores_empty():
    # Empty values for unknown keys are not "provided", so no error.
    F.reject_unknown_filter_keys({"bogus": None, "other": []}, allowed=set())


def test_reject_unknown_filter_keys_allows_known():
    F.reject_unknown_filter_keys({"sender": "x"}, allowed=set(F.FILTER_REGISTRY))


# --- (d') unknown key raises at all three backend entry points ---------------


def test_core_entry_rejects_unknown_key():
    with pytest.raises(ValueError, match="Unsupported filter"):
        apply_item_filters(Query(SourceItem), set(), {"bogus": "x"})  # type: ignore[arg-type]


def test_bm25_entry_rejects_unknown_key():
    with pytest.raises(ValueError, match="Unsupported filter"):
        asyncio.run(
            search_bm25(
                "hello",
                {"mail"},
                filters={"access_filter": None, "bogus": "x"},  # type: ignore[typeddict-unknown-key]
            )
        )


def test_embeddings_entry_rejects_unknown_key():
    with pytest.raises(ValueError, match="Unsupported filter"):
        asyncio.run(
            search_chunks(
                [DataChunk(data=["q"])],
                {"mail"},
                filters={"access_filter": None, "bogus": "x"},  # type: ignore[typeddict-unknown-key]
            )
        )


def test_embeddings_entry_rejects_subject_loudly():
    # subject has no faithful Qdrant translation: it must fail, not leak.
    with pytest.raises(ValueError, match="no faithful Qdrant translation"):
        asyncio.run(
            search_chunks(
                [DataChunk(data=["q"])],
                {"mail"},
                filters={"access_filter": None, "subject": "hi"},
            )
        )


def test_bm25_mail_filter_join_chain():
    """Registry mail filters fold onto a bm25-style Chunk->SourceItem query.

    bm25's base query is (Chunk.id, rank) joined to SourceItem; passing
    joined={SourceItem} must suppress a duplicate SourceItem join and chain
    MailMessage on SourceItem.id, yielding inner joins (no cartesian product).
    """
    base = Query(Chunk.id).join(SourceItem, SourceItem.id == Chunk.source_id)
    q = F.apply_registry_filters_sql(
        base, {"sender": "a@b.com", "recipients": ["x@y.com"]}, joined={SourceItem}
    )
    sql = compile_sql(q)
    # SourceItem joined exactly once (pre-joined), MailMessage chained on its id
    # via the raw table (joining the polymorphic entity would re-alias
    # source_item and produce a cartesian product).
    assert sql.count("JOIN source_item") == 1
    assert "JOIN mail_message ON mail_message.id = source_item.id" in sql
    assert "mail_message.sender ILIKE" in sql
    assert "array_to_string(mail_message.recipients" in sql
