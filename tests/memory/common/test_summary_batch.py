import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import anthropic
import httpx2
import pytest

from memory.agents import call
from memory.agents.system import SUMMARISE
from memory.common import embedding, extract, qdrant as qdrant_tools, settings
from memory.common import summary_batch
from memory.common.content_processing import push_chunks_to_qdrant
from memory.common.db.connection import make_session
from memory.common.db.models import LLMProviderKey, LLMSpend, SourceItem, SummaryRequest
from tests.memory.agents import fakes

LONG_TEXT = "Some interesting sentence about things. " * 200
SUMMARY = {"summary": "the summary", "tags": ["t1", "t2"]}
REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages/batches")


class FakeBatches:
    """The Batches API of every key: records created batches, answers `results[batch_id]`."""

    def __init__(self):
        self.created: list[tuple[str, list[dict]]] = []
        self.clients: list[str] = []
        self.retrieved: list[tuple[str, str]] = []
        self.results: dict[str, list] = {}
        self.processing_status = "ended"
        self.age = timedelta(hours=1)
        self.create_errors: dict[str, Exception] = {}
        self.retrieve_errors: dict[str, Exception] = {}
        self.results_errors: dict[str, Exception] = {}

    def create(self, requests):
        batch_id = f"batch-{len(self.created)}"
        self.created.append((batch_id, requests))
        return SimpleNamespace(id=batch_id)

    def client(self, api_key):
        self.clients.append(api_key)

        def create(requests):
            if error := self.create_errors.get(api_key):
                raise error
            return self.create(requests)

        def retrieve(batch_id):
            self.retrieved.append((api_key, batch_id))
            if error := self.retrieve_errors.get(batch_id):
                raise error
            return SimpleNamespace(
                processing_status=self.processing_status,
                created_at=datetime.now(timezone.utc) - self.age,
            )

        def results(batch_id):
            if error := self.results_errors.get(batch_id):
                raise error
            return iter(self.results.get(batch_id, []))

        return SimpleNamespace(
            messages=SimpleNamespace(
                batches=SimpleNamespace(create=create, retrieve=retrieve, results=results)
            )
        )


@pytest.fixture
def batches(monkeypatch):
    fake = FakeBatches()
    monkeypatch.setattr(summary_batch, "batch_client", fake.client)
    return fake


def make_item(db_session, content: str = LONG_TEXT, creator_id: int | None = None) -> SourceItem:
    item = SourceItem(
        content=content,
        modality="text",
        sha256=hashlib.sha256(content.encode()).digest(),
        tags=["bla"],
        mime_type="text/plain",
        creator_id=creator_id,
    )
    db_session.add(item)
    db_session.flush()
    return item


@pytest.fixture
def keyed_item(db_session, regular_user, llm_key):
    """An item whose creator has a key to pay for its summary."""
    return make_item(db_session, creator_id=regular_user.id)


def add_request(db_session, item, key, **kwargs) -> SummaryRequest:
    request = SummaryRequest(
        source_id=item.id, prompt=f"prompt for {item.id}", provider_key_id=key.id, **kwargs
    )
    db_session.add(request)
    db_session.commit()
    return request


def second_key(db_session, user, label: str) -> LLMProviderKey:
    key = LLMProviderKey(user_id=user.id, provider="anthropic", label=label)
    key.key = f"sk-{label}"
    db_session.add(key)
    db_session.commit()
    return key


def submit_block(data: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": "r1", "name": "submit_result", "input": data}


def sdk_block(data: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(to_dict=lambda: data)


def batch_result(
    custom_id,
    type_: str = "succeeded",
    blocks: list[dict[str, Any]] | None = None,
    stop_reason: str = "tool_use",
    error_type: str = "api_error",
):
    message = SimpleNamespace(
        content=[sdk_block(b) for b in blocks or [submit_block(SUMMARY)]],
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=1000,
            output_tokens=100,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            server_tool_use=None,
        ),
    )
    return SimpleNamespace(
        custom_id=str(custom_id), result=SimpleNamespace(
            type=type_, message=message, error=SimpleNamespace(error=SimpleNamespace(type=error_type))
        ),
    )


def not_found():
    return anthropic.NotFoundError(
        "not found", response=httpx2.Response(404, request=REQUEST), body=None
    )


@pytest.mark.parametrize(
    "use_batch, has_summary_chunk, expected_statuses",
    [(True, False, ["pending"]), (False, True, [])],
)
def test_embed_source_item_defers_summary_on_the_items_key(
    db_session, keyed_item, llm_key, scripted_llm, use_batch, has_summary_chunk, expected_statuses
):
    scripted_llm.script = [fakes.submitted({"summary": "test summary", "tags": ["t"]})]
    item = keyed_item
    with patch.object(settings, "SUMMARIZER_USE_BATCH", use_batch):
        chunks = embedding.embed_source_item(item)
    db_session.commit()

    requests = db_session.query(SummaryRequest).all()
    assert any(c.content == "test summary" for c in chunks) is has_summary_chunk
    assert [(r.source_id, r.status, r.chunk_metadata, r.provider_key_id) for r in requests] == [
        (item.id, status, {}, llm_key.id) for status in expected_statuses
    ]
    assert all(LONG_TEXT.strip()[:100] in r.prompt for r in requests)


def test_reembedding_replaces_pending_requests(db_session, keyed_item):
    item = keyed_item
    with patch.object(settings, "SUMMARIZER_USE_BATCH", True):
        embedding.embed_source_item(item)
        db_session.commit()
        embedding.embed_source_item(item)
        db_session.commit()

    assert db_session.query(SummaryRequest).count() == 1


def test_failed_embedding_records_no_summary_request(db_session, keyed_item):
    with (
        patch.object(settings, "SUMMARIZER_USE_BATCH", True),
        patch.object(embedding, "embed_chunks", side_effect=RuntimeError("voyage down")),
        pytest.raises(RuntimeError),
    ):
        embedding.embed_source_item(keyed_item)
    db_session.commit()

    assert db_session.query(SummaryRequest).count() == 0


def test_reembedding_without_summary_drops_stale_requests(db_session, keyed_item):
    item = keyed_item
    with patch.object(settings, "SUMMARIZER_USE_BATCH", True):
        embedding.embed_source_item(item)
        db_session.commit()
        item.content = "now short enough to need no summary"
        embedding.embed_source_item(item)
        db_session.commit()

    assert db_session.query(SummaryRequest).count() == 0


def test_each_key_gets_its_own_batch_on_its_own_credentials(
    db_session, regular_user, llm_key, batches
):
    other = second_key(db_session, regular_user, "work")
    item = make_item(db_session)
    mine = add_request(db_session, item, llm_key)
    theirs = add_request(db_session, item, other)

    assert len(summary_batch.submit_pending(db_session)) == 2

    sent = {batch_id: [r["custom_id"] for r in requests] for batch_id, requests in batches.created}
    db_session.expire_all()
    assert sorted(batches.clients) == ["sk-test-default", "sk-work"]
    assert sorted(sent[cast(str, r.batch_id)] for r in (mine, theirs)) == [
        [str(mine.id)],
        [str(theirs.id)],
    ]
    assert {r.status for r in db_session.query(SummaryRequest)} == {"submitted"}


def test_submit_pending_sends_the_summarise_agents_request(db_session, llm_key, batches):
    item = make_item(db_session)
    pending = [add_request(db_session, item, llm_key), add_request(db_session, item, llm_key)]
    submitted = add_request(db_session, item, llm_key, status="submitted", batch_id="old")
    failed = add_request(db_session, item, llm_key, status="failed")

    assert summary_batch.submit_pending(db_session) == ["batch-0"]

    ((_, requests),) = batches.created
    assert [r["custom_id"] for r in requests] == [str(r.id) for r in pending]
    params = requests[0]["params"]
    assert (params["model"], params["system"], params["max_tokens"]) == (
        "claude-haiku-5-5",
        SUMMARISE.system_prompt,
        SUMMARISE.max_output_tokens,
    )
    assert [t["name"] for t in params["tools"]] == ["submit_result"]
    assert params["tool_choice"] == {"type": "any"}
    assert f"prompt for {item.id}" in str(params["messages"][0]["content"])

    db_session.expire_all()
    assert [(r.status, r.batch_id, r.attempts) for r in pending] == [
        ("submitted", "batch-0", 1)
    ] * 2
    assert (submitted.batch_id, failed.status) == ("old", "failed")


def test_submit_pending_claims_requests_before_sending_them(db_session, llm_key, batches):
    request_id = add_request(db_session, make_item(db_session), llm_key).id
    seen = []

    def create(requests):
        # A failed commit after this call must not leave the requests pending.
        with make_session() as other:
            seen.append(cast(SummaryRequest, other.get(SummaryRequest, request_id)).status)
        return SimpleNamespace(id="batch-0")

    batches.create = create

    assert summary_batch.submit_pending(db_session) == ["batch-0"]
    assert seen == ["submitted"]


@pytest.mark.parametrize(
    "error",
    [
        anthropic.APIConnectionError(request=REQUEST),
        anthropic.RateLimitError(
            "slow down", response=httpx2.Response(429, request=REQUEST), body=None
        ),
        anthropic.InternalServerError(
            "overloaded", response=httpx2.Response(529, request=REQUEST), body=None
        ),
        httpx2.ConnectError("connection reset", request=REQUEST),
    ],
    ids=["connection", "rate_limit", "overloaded", "transport"],
)
def test_a_transient_provider_failure_returns_the_keys_requests_and_moves_on(
    db_session, regular_user, llm_key, batches, error
):
    other = second_key(db_session, regular_user, "work")
    item = make_item(db_session)
    failing = add_request(db_session, item, llm_key)
    sent = add_request(db_session, item, other)
    batches.create_errors["sk-test-default"] = error

    assert summary_batch.submit_pending(db_session) == ["batch-0"]

    db_session.expire_all()
    assert [(r.status, r.batch_id, r.attempts) for r in (failing, sent)] == [
        ("pending", None, 0),
        ("submitted", "batch-0", 1),
    ]


@pytest.mark.parametrize(
    "attempts, expected",
    [(0, ("pending", None, 1)), (2, ("failed", None, 3))],
)
def test_a_refused_batch_counts_an_attempt_and_logs_why(
    db_session, regular_user, llm_key, batches, caplog, attempts, expected
):
    other = second_key(db_session, regular_user, "work")
    item = make_item(db_session)
    refused = add_request(db_session, item, llm_key, attempts=attempts)
    sent = add_request(db_session, item, other)
    batches.create_errors["sk-test-default"] = anthropic.BadRequestError(
        "requests.0.params.max_tokens: too large",
        response=httpx2.Response(400, request=REQUEST),
        body=None,
    )

    with patch.object(settings, "SUMMARIZER_BATCH_MAX_ATTEMPTS", 3):
        assert summary_batch.submit_pending(db_session) == ["batch-0"]

    db_session.expire_all()
    assert (refused.status, refused.batch_id, refused.attempts) == expected
    assert sent.status == "submitted"
    assert "max_tokens: too large" in caplog.text
    assert "sk-test-default" not in caplog.text


def test_not_found_results_are_reported_once_per_key_at_error_with_the_backlog(
    db_session, llm_key, batches, caplog, monkeypatch
):
    monkeypatch.setattr(call, "reported_unavailable", {})
    requests = [
        add_request(
            db_session,
            make_item(db_session, LONG_TEXT + str(n)),
            llm_key,
            status="submitted",
            batch_id="batch_1",
        )
        for n in range(2)
    ]
    add_request(db_session, make_item(db_session, LONG_TEXT + "pending"), llm_key)
    batches.results["batch_1"] = [
        batch_result(r.id, "errored", error_type="not_found_error") for r in requests
    ]

    summary_batch.collect_finished(db_session)

    (record,) = [r for r in caplog.records if r.levelname == "ERROR"]
    assert f"key {llm_key.id}" in record.getMessage()
    assert "1 pending" in record.getMessage()
    assert "sk-test-default" not in caplog.text


def test_other_errored_results_are_not_reported(db_session, llm_key, batches, caplog, monkeypatch):
    monkeypatch.setattr(call, "reported_unavailable", {})
    request = add_request(
        db_session, make_item(db_session), llm_key, status="submitted", batch_id="batch_1"
    )
    batches.results["batch_1"] = [batch_result(request.id, "errored", error_type="api_error")]

    summary_batch.collect_finished(db_session)

    assert [r for r in caplog.records if r.levelname == "ERROR"] == []


def test_submit_pending_returns_requests_on_an_unexpected_error(db_session, llm_key, batches):
    request = add_request(db_session, make_item(db_session), llm_key)
    batches.create_errors["sk-test-default"] = RuntimeError("bug")

    with pytest.raises(RuntimeError):
        summary_batch.submit_pending(db_session)

    db_session.expire_all()
    assert (request.status, request.batch_id, request.attempts) == ("pending", None, 0)


@pytest.mark.parametrize("status,cap", [("invalid", None), ("ok", Decimal("0.000001"))])
def test_keys_that_cannot_pay_keep_their_requests_pending(
    db_session, llm_key, batches, status, cap
):
    llm_key.status, llm_key.max_cost_usd_per_day = status, cap
    summary_batch.caps.record_spend(
        db_session, key_id=llm_key.id, agent="system:hyde", cost=Decimal("1")
    )
    db_session.commit()
    add_request(db_session, make_item(db_session), llm_key)

    assert summary_batch.submit_pending(db_session) == []
    assert batches.created == []
    assert db_session.query(SummaryRequest).one().status == "pending"


def test_a_key_the_provider_rejects_is_marked_invalid_and_its_requests_stay_pending(
    db_session, llm_key, batches, monkeypatch
):
    notified = []
    monkeypatch.setattr(summary_batch, "notify_key_rejected", lambda key: notified.append(key.id))
    batches.create_errors["sk-test-default"] = anthropic.AuthenticationError(
        "bad key", response=httpx2.Response(401, request=REQUEST), body=None
    )
    add_request(db_session, make_item(db_session), llm_key)

    assert summary_batch.submit_pending(db_session) == []

    db_session.refresh(llm_key)
    assert (llm_key.status, notified, db_session.query(SummaryRequest).one().status) == (
        "invalid",
        [llm_key.id],
        "pending",
    )


def test_submit_pending_respects_byte_budget(db_session, llm_key, batches):
    item = make_item(db_session)
    first = add_request(db_session, item, llm_key)
    add_request(db_session, item, llm_key)

    with patch.object(settings, "SUMMARIZER_BATCH_MAX_BYTES", len(first.prompt) + 1):
        summary_batch.submit_pending(db_session)

    ((_, requests),) = batches.created
    assert [r["custom_id"] for r in requests] == [str(first.id)]


def test_submit_pending_without_pending_requests_does_nothing(db_session, batches):
    assert summary_batch.submit_pending(db_session) == []
    assert batches.created == []


def test_collect_skips_requests_claimed_by_an_unfinished_submit(db_session, llm_key, batches):
    add_request(db_session, make_item(db_session), llm_key, status="submitted")

    assert summary_batch.collect_finished(db_session) == {}
    assert batches.retrieved == []


def test_collect_reads_each_batch_with_its_own_key(db_session, regular_user, llm_key, batches):
    other = second_key(db_session, regular_user, "work")
    item = make_item(db_session)
    add_request(db_session, item, llm_key, status="submitted", batch_id="batch-mine")
    add_request(db_session, item, other, status="submitted", batch_id="batch-theirs")

    summary_batch.collect_finished(db_session)

    assert sorted(batches.retrieved) == [
        ("sk-test-default", "batch-mine"),
        ("sk-work", "batch-theirs"),
    ]


def test_collected_results_are_applied_and_charged_at_the_batch_discount(
    db_session, llm_key, batches, monkeypatch
):
    applied = []
    monkeypatch.setattr(
        summary_batch, "apply_summary", lambda session, request, data: applied.append(data)
    )
    request = add_request(db_session, make_item(db_session), llm_key)
    (batch_id,) = summary_batch.submit_pending(db_session)
    batches.results[batch_id] = [batch_result(request.id)]

    summary_batch.collect_finished(db_session)

    # haiku-5-5: 1000 x $0.10/MTok + 100 x $0.50/MTok = $0.00015, halved.
    spend = db_session.query(LLMSpend).one()
    assert applied == [SUMMARY]
    assert (spend.provider_key_id, spend.agent, spend.cost_usd) == (
        llm_key.id,
        SUMMARISE.slug,
        Decimal("0.000075"),
    )


# Row locks only hold between separate connections
@pytest.mark.transactional_db
def test_a_result_stays_claimed_until_it_is_applied(db_session, llm_key, batches, monkeypatch):
    request = add_request(
        db_session, make_item(db_session), llm_key, status="submitted", batch_id="batch_1"
    )
    batches.results["batch_1"] = [batch_result(request.id)]
    claimed_elsewhere = []

    def apply(session, request, data):
        # A concurrent collector must not apply, or charge, the same result again
        with make_session() as other:
            claimed_elsewhere.append(summary_batch.claim(other, "batch_1", str(request.id)))
        session.delete(request)
        session.commit()

    monkeypatch.setattr(summary_batch, "apply_summary", apply)

    summary_batch.collect_finished(db_session)

    assert claimed_elsewhere == [None]
    assert db_session.query(LLMSpend).count() == 1


def test_collect_on_a_rejected_key_marks_it_invalid_and_stops_reading(
    db_session, llm_key, batches, monkeypatch
):
    notified = []
    monkeypatch.setattr(summary_batch, "notify_key_rejected", lambda key: notified.append(key.id))
    request = add_request(
        db_session, make_item(db_session), llm_key, status="submitted", batch_id="batch_1"
    )
    batches.retrieve_errors["batch_1"] = anthropic.AuthenticationError(
        "bad key", response=httpx2.Response(401, request=REQUEST), body=None
    )

    summary_batch.collect_finished(db_session)
    summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert (llm_key.status, notified, len(batches.retrieved)) == ("invalid", [llm_key.id], 1)
    assert (request.status, request.batch_id) == ("submitted", "batch_1")


def test_collect_applies_summary_and_is_idempotent(db_session, llm_key, batches, qdrant):
    item = make_item(db_session)
    existing = item.make_chunk(extract.DataChunk(data=["body text"]))
    embedding.embed_by_model([existing], cast(str, existing.embedding_model))
    db_session.add(existing)
    db_session.commit()
    push_chunks_to_qdrant([existing])
    request = add_request(
        db_session, item, llm_key, status="submitted", batch_id="batch_1",
        chunk_metadata={"type": "summary"},
    )
    batches.results["batch_1"] = [batch_result(request.id)]

    assert summary_batch.collect_finished(db_session) == {"applied": 1}
    # The batch is still downloadable, but its results were already applied
    summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert db_session.query(SummaryRequest).count() == 0
    summary_chunks = [c for c in item.chunks if c.content == "the summary"]
    assert len(summary_chunks) == 1
    payloads = qdrant_tools.get_payloads(
        qdrant, "text", [str(existing.id), str(summary_chunks[0].id)]
    )
    assert set(payloads[str(existing.id)]["tags"]) == {"bla", "t1", "t2"}
    assert set(payloads[str(summary_chunks[0].id)]["tags"]) == {"bla", "t1", "t2"}
    assert payloads[str(summary_chunks[0].id)]["type"] == "summary"


def test_collect_tags_only_request_adds_no_chunk(db_session, llm_key, batches, qdrant):
    item = make_item(db_session)
    request = add_request(db_session, item, llm_key, status="submitted", batch_id="batch_1")
    batches.results["batch_1"] = [batch_result(request.id)]

    summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert item.chunks == []
    assert db_session.query(SummaryRequest).count() == 0


@pytest.mark.parametrize("result_type", ["errored", "expired", "canceled"])
@pytest.mark.parametrize(
    "attempts, expected",
    [(1, ("pending", None)), (3, ("failed", "batch_1"))],
)
def test_collect_retries_failed_results_a_bounded_number_of_times(
    db_session, llm_key, batches, result_type, attempts, expected
):
    request = add_request(
        db_session, make_item(db_session), llm_key,
        status="submitted", batch_id="batch_1", attempts=attempts,
    )
    batches.results["batch_1"] = [batch_result(request.id, result_type)]

    with patch.object(settings, "SUMMARIZER_BATCH_MAX_ATTEMPTS", 3):
        summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert (request.status, request.batch_id) == expected
    assert db_session.query(LLMSpend).count() == 0


@pytest.mark.parametrize(
    "blocks, stop_reason",
    [
        ([{"type": "text", "text": "the summary"}], "end_turn"),
        ([submit_block(SUMMARY)], "max_tokens"),
        ([submit_block({"summary": "s"})], "tool_use"),
    ],
    ids=["no_submission", "cut_off", "missing_tags"],
)
def test_collect_retries_unusable_results(db_session, llm_key, batches, blocks, stop_reason):
    request = add_request(
        db_session, make_item(db_session), llm_key, status="submitted", batch_id="batch_1"
    )
    batches.results["batch_1"] = [batch_result(request.id, blocks=blocks, stop_reason=stop_reason)]

    assert summary_batch.collect_finished(db_session) == {"apply_failed": 1}

    db_session.expire_all()
    assert (request.status, request.batch_id) == ("pending", None)
    # The provider billed the answer even though it was unusable
    assert [s.cost_usd for s in db_session.query(LLMSpend)] == [Decimal("0.000075")]


def test_collect_requeues_requests_missing_from_results(db_session, llm_key, batches):
    request = add_request(
        db_session, make_item(db_session), llm_key, status="submitted", batch_id="batch_1"
    )

    summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert (request.status, request.batch_id) == ("pending", None)


def test_collect_leaves_unfinished_batches(db_session, llm_key, batches):
    request = add_request(
        db_session, make_item(db_session), llm_key, status="submitted", batch_id="batch_1"
    )
    batches.processing_status = "in_progress"

    assert summary_batch.collect_finished(db_session) == {}

    db_session.expire_all()
    assert (request.status, request.batch_id) == ("submitted", "batch_1")


def test_collect_unreadable_batch_does_not_block_others(db_session, llm_key, batches):
    item = make_item(db_session)
    broken = add_request(db_session, item, llm_key, status="submitted", batch_id="batch_gone")
    healthy = add_request(db_session, item, llm_key, status="submitted", batch_id="batch_1")
    batches.retrieve_errors["batch_gone"] = RuntimeError("api blip")

    summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert (broken.status, healthy.status) == ("submitted", "pending")


@pytest.mark.parametrize(
    "setup",
    [
        lambda b: b.retrieve_errors.update(batch_1=not_found()),
        lambda b: b.results_errors.update(batch_1=not_found()),
        lambda b: (
            setattr(b, "age", timedelta(days=30)),
            b.results_errors.update(batch_1=RuntimeError("results gone")),
        ),
    ],
    ids=["batch_not_found", "results_not_found", "results_expired"],
)
def test_collect_requeues_requests_of_unrecoverable_batches(db_session, llm_key, batches, setup):
    request = add_request(
        db_session, make_item(db_session), llm_key, status="submitted", batch_id="batch_1"
    )
    setup(batches)

    summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert (request.status, request.batch_id) == ("pending", None)


def test_collect_merges_tags_even_if_summary_push_fails(db_session, llm_key, batches, qdrant):
    item = make_item(db_session)
    existing = item.make_chunk(extract.DataChunk(data=["body text"]))
    embedding.embed_by_model([existing], cast(str, existing.embedding_model))
    db_session.add(existing)
    db_session.commit()
    push_chunks_to_qdrant([existing])
    request = add_request(
        db_session, item, llm_key, status="submitted", batch_id="batch_1",
        chunk_metadata={"type": "summary"},
    )
    batches.results["batch_1"] = [batch_result(request.id)]

    with patch.object(
        summary_batch, "push_chunks_to_qdrant", side_effect=RuntimeError("qdrant down")
    ):
        summary_batch.collect_finished(db_session)

    payloads = qdrant_tools.get_payloads(qdrant, "text", [str(existing.id)])
    assert set(payloads[str(existing.id)]["tags"]) == {"bla", "t1", "t2"}
