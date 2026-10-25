import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import patch

import anthropic
import httpx
import httpx2
import openai
import pytest

from memory.agents import call
from memory.agents.system import SUMMARISE
from memory.common import embedding, extract, qdrant as qdrant_tools, settings
from memory.common import summary_batch
from memory.common.content_processing import push_chunks_to_qdrant
from memory.common.db.connection import make_session
from memory.common.db.models import LLMProviderKey, LLMSpend, SourceItem, SummaryRequest
from memory.common.llms.anthropic_provider import AnthropicProvider
from memory.common.llms import batches as batch_apis
from tests.memory.agents import fakes

LONG_TEXT = "Some interesting sentence about things. " * 200
SUMMARY = {"summary": "the summary", "tags": ["t1", "t2"]}
REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages/batches")
OPENAI_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/batches")


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
    monkeypatch.setattr(batch_apis, "anthropic_client", fake.client)
    return fake


class FakeOpenAI:
    """The Files and Batches APIs of one OpenAI key."""

    def __init__(self):
        self.uploads: dict[str, list[dict]] = {}
        self.created: list[dict] = []
        self.batches: dict[str, SimpleNamespace] = {}
        self.contents: dict[str, str] = {}
        self.deleted: list[str] = []
        self.create_error: Exception | None = None
        self.retrieve_error: Exception | None = None
        self.cancelled: list[str] = []
        self.on_create: Callable[[], None] = lambda: None

    def upload(self, file, purpose):
        file_id = f"file-{len(self.uploads)}"
        _, content = file
        self.uploads[file_id] = [json.loads(line) for line in content.read().splitlines()]
        return SimpleNamespace(id=file_id, purpose=purpose)

    def create(self, **kwargs):
        if self.create_error:
            raise self.create_error
        self.on_create()
        batch_id = f"batch-{len(self.created)}"
        self.created.append(kwargs)
        self.batches[batch_id] = SimpleNamespace(
            id=batch_id,
            status="in_progress",
            created_at=int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()),
            input_file_id=kwargs["input_file_id"],
            output_file_id=None,
            error_file_id=None,
            errors=None,
        )
        return self.batches[batch_id]

    def retrieve(self, batch_id: str):
        if self.retrieve_error:
            raise self.retrieve_error
        return self.batches[batch_id]

    def end(self, batch_id: str, status: str = "completed", output=(), errors=()):
        batch = self.batches[batch_id]
        batch.status = status
        for attr, lines in (("output_file_id", output), ("error_file_id", errors)):
            if lines:
                file_id = f"{batch_id}-{attr}"
                self.contents[file_id] = "\n".join(json.dumps(line) for line in lines) + "\n"
                setattr(batch, attr, file_id)

    def client(self, api_key):
        return SimpleNamespace(
            files=SimpleNamespace(
                create=self.upload,
                content=lambda file_id: SimpleNamespace(text=self.contents[file_id]),
                delete=self.deleted.append,
            ),
            batches=SimpleNamespace(
                create=self.create, retrieve=self.retrieve, cancel=self.cancelled.append
            ),
        )


@pytest.fixture
def openai_batches(monkeypatch, llm_key, db_session):
    llm_key.provider = "openai"
    db_session.commit()
    fake = FakeOpenAI()
    monkeypatch.setattr(batch_apis, "openai_client", fake.client)
    return fake


def openai_output(custom_id, data=SUMMARY) -> dict[str, Any]:
    call = {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_1",
        "name": "submit_result",
        "arguments": json.dumps(data),
        "status": "completed",
    }
    body = {
        "id": "resp_1",
        "status": "completed",
        "output": [call],
        "usage": {
            "input_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 100,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 1100,
        },
        "incomplete_details": None,
        "error": None,
    }
    return {
        "custom_id": str(custom_id),
        "response": {"status_code": 200, "body": body},
        "error": None,
    }


def openai_error(custom_id, status_code: int | None = 400, code: str | None = None):
    response = {"status_code": status_code, "body": {"error": {"message": "bad"}}}
    return {
        "custom_id": str(custom_id),
        "response": response if status_code else None,
        "error": {"code": code, "message": "not run"} if code else None,
    }


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
    return SimpleNamespace(to_dict=lambda: data, **data)


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


@pytest.mark.parametrize(
    "claimed_ago, stats, status",
    [(timedelta(hours=2), {}, "submitted"), (timedelta(hours=4), {"dead_claims": 1}, "pending")],
    ids=["being_sent", "died_unsent"],
)
def test_collect_requeues_claims_whose_submit_died_without_using_an_attempt(
    db_session, llm_key, batches, caplog, claimed_ago, stats, status
):
    request = add_request(
        db_session,
        make_item(db_session),
        llm_key,
        status="submitted",
        submitted_at=datetime.now(timezone.utc) - claimed_ago,
    )

    assert summary_batch.collect_finished(db_session) == stats

    db_session.expire_all()
    assert (request.status, request.attempts, batches.retrieved) == (status, 0, [])
    assert ("never sent" in caplog.text) == bool(stats)


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


def test_a_summary_collected_without_a_voyage_key_logs_no_error(
    db_session, llm_key, batches, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "VOYAGE_API_KEY", "")
    request = add_request(
        db_session, make_item(db_session), llm_key, status="submitted", batch_id="batch_1",
        chunk_metadata={"type": "summary"},
    )
    batches.results["batch_1"] = [batch_result(request.id)]

    assert summary_batch.collect_finished(db_session) == {"apply_failed": 1}
    assert [r for r in caplog.records if r.name == "memory.common.summary_batch" and r.levelname == "ERROR"] == []


@pytest.mark.parametrize(
    "chunk_metadata, result",
    [(None, SUMMARY), ({"type": "summary"}, {"summary": None, "tags": ["t1"]})],
    ids=["tags_wanted_only", "content_needs_no_summary"],
)
def test_collect_tags_only_request_adds_no_chunk(
    db_session, llm_key, batches, qdrant, chunk_metadata, result
):
    item = make_item(db_session)
    request = add_request(
        db_session, item, llm_key, status="submitted", batch_id="batch_1",
        chunk_metadata=chunk_metadata,
    )
    batches.results["batch_1"] = [batch_result(request.id, blocks=[submit_block(result)])]

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


def test_an_openai_key_sends_its_requests_as_a_responses_batch(
    db_session, llm_key, openai_batches
):
    item = make_item(db_session)
    requests = [add_request(db_session, item, llm_key), add_request(db_session, item, llm_key)]

    assert summary_batch.submit_pending(db_session) == ["batch-0"]

    ((file_id, lines),) = openai_batches.uploads.items()
    assert [(line["custom_id"], line["method"], line["url"]) for line in lines] == [
        (str(r.id), "POST", "/v1/responses") for r in requests
    ]
    body = lines[0]["body"]
    assert (body["model"], body["instructions"], body["tool_choice"]) == (
        "gpt-6-luna",
        SUMMARISE.system_prompt,
        "required",
    )
    assert [t["name"] for t in body["tools"]] == ["submit_result"]
    assert f"prompt for {item.id}" in str(body["input"])
    (created,) = openai_batches.created
    assert (created["input_file_id"], created["endpoint"], created["completion_window"]) == (
        file_id,
        "/v1/responses",
        "24h",
    )
    db_session.expire_all()
    assert [(r.status, r.batch_id, r.attempts) for r in requests] == [
        ("submitted", "batch-0", 1)
    ] * 2


@pytest.mark.parametrize(
    "error, expected",
    [
        (
            openai.RateLimitError(
                "slow down", response=httpx.Response(429, request=OPENAI_REQUEST), body=None
            ),
            ("pending", 0, "ok"),
        ),
        (
            openai.BadRequestError(
                "too large", response=httpx.Response(400, request=OPENAI_REQUEST), body=None
            ),
            ("pending", 1, "ok"),
        ),
        (
            openai.AuthenticationError(
                "bad key", response=httpx.Response(401, request=OPENAI_REQUEST), body=None
            ),
            ("pending", 0, "invalid"),
        ),
    ],
    ids=["rate_limit", "refused", "rejected_key"],
)
def test_an_openai_batch_that_is_not_created_deletes_its_upload(
    db_session, llm_key, openai_batches, monkeypatch, error, expected
):
    monkeypatch.setattr(summary_batch, "notify_key_rejected", lambda key: None)
    request = add_request(db_session, make_item(db_session), llm_key)
    openai_batches.create_error = error

    assert summary_batch.submit_pending(db_session) == []

    db_session.expire_all()
    assert (request.status, request.attempts, llm_key.status) == expected
    assert openai_batches.deleted == list(openai_batches.uploads)


def test_openai_results_are_applied_retried_and_their_files_deleted(
    db_session, llm_key, openai_batches, monkeypatch
):
    applied = []

    def apply(session, request, data):
        applied.append(data)
        session.delete(request)

    monkeypatch.setattr(summary_batch, "apply_summary", apply)
    item = make_item(db_session)
    answered, refused, unrun, missing = [add_request(db_session, item, llm_key) for _ in range(4)]
    (batch_id,) = summary_batch.submit_pending(db_session)
    openai_batches.end(
        batch_id,
        "expired",
        output=[openai_output(answered.id)],
        errors=[openai_error(refused.id), openai_error(unrun.id, None, "batch_expired")],
    )

    stats = summary_batch.collect_finished(db_session)

    # gpt-6-luna: 1000 x $0.10/MTok + 100 x $0.50/MTok = $0.00015, halved.
    assert stats == {"applied": 1, "errored": 1, "expired": 1, "missing": 1}
    assert applied == [SUMMARY]
    assert [s.cost_usd for s in db_session.query(LLMSpend)] == [Decimal("0.000075")]
    db_session.expire_all()
    assert [(r.status, r.batch_id) for r in (refused, unrun, missing)] == [("pending", None)] * 3
    assert sorted(openai_batches.deleted) == sorted(
        ["file-0", f"{batch_id}-output_file_id", f"{batch_id}-error_file_id"]
    )


@pytest.mark.parametrize(
    "status, expected",
    [
        ("validating", ("submitted", "batch-0", [])),
        ("in_progress", ("submitted", "batch-0", [])),
        ("finalizing", ("submitted", "batch-0", [])),
        ("cancelling", ("submitted", "batch-0", [])),
        ("failed", ("pending", None, ["file-0"])),
    ],
)
def test_openai_batches_are_collected_once_they_end(
    db_session, llm_key, openai_batches, status, expected
):
    request = add_request(db_session, make_item(db_session), llm_key)
    summary_batch.submit_pending(db_session)
    openai_batches.batches["batch-0"].status = status

    summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert (request.status, request.batch_id, openai_batches.deleted) == expected


@pytest.mark.parametrize("status_code, reported", [(404, 1), (400, 0)])
def test_openai_not_found_results_are_reported_at_error(
    db_session, llm_key, openai_batches, caplog, monkeypatch, status_code, reported
):
    monkeypatch.setattr(call, "reported_unavailable", {})
    request = add_request(db_session, make_item(db_session), llm_key)
    (batch_id,) = summary_batch.submit_pending(db_session)
    openai_batches.end(batch_id, errors=[openai_error(request.id, status_code)])

    summary_batch.collect_finished(db_session)

    assert len([r for r in caplog.records if r.levelname == "ERROR"]) == reported


def fail_validation(fake: FakeOpenAI, batch_id: str, code: str = "token_limit_exceeded"):
    batch = fake.batches[batch_id]
    batch.status = "failed"
    batch.errors = SimpleNamespace(data=[SimpleNamespace(code=code)])


def test_queue_limit_refusals_halve_the_keys_batches_until_one_request_uses_an_attempt(
    db_session, llm_key, openai_batches
):
    item = make_item(db_session)
    requests = [add_request(db_session, item, llm_key) for _ in range(4)]
    sizes = []
    for _ in range(4):
        (batch_id,) = summary_batch.submit_pending(db_session)
        sizes.append(len(openai_batches.uploads[openai_batches.created[-1]["input_file_id"]]))
        fail_validation(openai_batches, batch_id)
        summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert sizes == [4, 2, 1, 1]
    # Only the lone request the provider still refused used up its attempts
    assert [r.attempts for r in requests] == [2, 0, 0, 0]


def test_a_refused_older_batch_does_not_raise_a_lowered_limit(
    db_session, llm_key, openai_batches
):
    item = make_item(db_session)
    for _ in range(4):
        add_request(db_session, item, llm_key)
    (batch_id,) = summary_batch.submit_pending(db_session)
    llm_key.summary_batch_limit = 1
    db_session.commit()
    fail_validation(openai_batches, batch_id)

    summary_batch.collect_finished(db_session)

    db_session.refresh(llm_key)
    assert llm_key.summary_batch_limit == 1


@pytest.mark.parametrize("limit, grown", [(1, 2), (2, None), (3, None)])
def test_a_successful_batch_doubles_the_keys_batch_size_back_to_the_default(
    db_session, llm_key, openai_batches, monkeypatch, limit, grown
):
    monkeypatch.setattr(batch_apis.OpenAIBatches, "MAX_REQUESTS", 4)
    monkeypatch.setattr(summary_batch, "apply_summary", lambda s, r, d: s.delete(r))
    llm_key.summary_batch_limit = limit
    request = add_request(db_session, make_item(db_session), llm_key)
    (batch_id,) = summary_batch.submit_pending(db_session)
    openai_batches.end(batch_id, output=[openai_output(request.id)])

    summary_batch.collect_finished(db_session)

    db_session.refresh(llm_key)
    assert llm_key.summary_batch_limit == grown


def test_an_openai_batch_refused_for_its_model_is_reported_and_uses_an_attempt(
    db_session, llm_key, openai_batches, caplog, monkeypatch
):
    monkeypatch.setattr(call, "reported_unavailable", {})
    request = add_request(db_session, make_item(db_session), llm_key)
    (batch_id,) = summary_batch.submit_pending(db_session)
    fail_validation(openai_batches, batch_id, "model_not_found")

    summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert (request.status, request.attempts) == ("pending", 1)
    (record,) = [r for r in caplog.records if r.levelname == "ERROR"]
    assert "gpt-6-luna" in record.getMessage()


def test_an_openai_key_waits_for_its_batch_in_flight_before_sending_another(
    db_session, llm_key, openai_batches
):
    item = make_item(db_session)
    add_request(db_session, item, llm_key)
    assert summary_batch.submit_pending(db_session) == ["batch-0"]
    waiting = add_request(db_session, item, llm_key)

    assert summary_batch.submit_pending(db_session) == []
    fail_validation(openai_batches, "batch-0", "invalid_request")
    summary_batch.collect_finished(db_session)
    assert summary_batch.submit_pending(db_session) == ["batch-1"]

    db_session.expire_all()
    assert (waiting.status, waiting.attempts) == ("submitted", 1)


def test_a_batch_takes_only_what_the_keys_daily_pool_can_pay_for(
    db_session, llm_key, openai_batches
):
    # gpt-6-luna at half price: 2048 output tokens cost at most $0.000512 a request
    llm_key.max_cost_usd_per_day = Decimal("0.0015")
    item = make_item(db_session)
    for _ in range(5):
        add_request(db_session, item, llm_key)

    summary_batch.submit_pending(db_session)

    (lines,) = openai_batches.uploads.values()
    assert len(lines) == 2


def test_an_unreadable_openai_result_is_retried_without_blocking_the_rest(
    db_session, llm_key, openai_batches, monkeypatch
):
    monkeypatch.setattr(summary_batch, "apply_summary", lambda s, r, d: s.delete(r))
    item = make_item(db_session)
    broken = add_request(db_session, item, llm_key)
    answered = add_request(db_session, item, llm_key)
    (batch_id,) = summary_batch.submit_pending(db_session)
    unreadable = openai_output(broken.id)
    del unreadable["response"]["body"]["usage"]["input_tokens_details"]["cache_write_tokens"]
    openai_batches.end(batch_id, output=[unreadable, openai_output(answered.id)])

    stats = summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert (stats["applied"], broken.status) == (1, "pending")
    assert len(openai_batches.deleted) == 2


def test_an_openai_batch_past_retention_is_requeued_and_its_files_deleted(
    db_session, llm_key, openai_batches
):
    request = add_request(db_session, make_item(db_session), llm_key)
    (batch_id,) = summary_batch.submit_pending(db_session)
    openai_batches.end(batch_id, output=[openai_output(request.id)])
    openai_batches.batches[batch_id].created_at -= 30 * 24 * 3600

    assert summary_batch.collect_finished(db_session) == {"expired_batch": 1}
    assert sorted(openai_batches.deleted) == ["batch-0-output_file_id", "file-0"]


def test_a_forbidden_batch_is_logged_at_error(db_session, llm_key, openai_batches, caplog):
    request = add_request(db_session, make_item(db_session), llm_key)
    openai_batches.create_error = openai.PermissionDeniedError(
        "no batch access", response=httpx.Response(403, request=OPENAI_REQUEST), body=None
    )

    summary_batch.submit_pending(db_session)

    db_session.expire_all()
    assert request.attempts == 1
    (record,) = [r for r in caplog.records if r.levelname == "ERROR"]
    assert f"key {llm_key.id}" in record.getMessage()


def test_replacing_a_keys_secret_restores_its_full_batch_size(db_session, llm_key):
    llm_key.summary_batch_limit = 2

    llm_key.key = "sk-another-project"

    assert llm_key.summary_batch_limit is None


@pytest.mark.parametrize("hours, status", [(1, "submitted"), (31, "pending")])
def test_a_batch_that_cannot_be_collected_is_given_up_after_its_window(
    db_session, llm_key, openai_batches, caplog, hours, status
):
    request = add_request(db_session, make_item(db_session), llm_key)
    (batch_id,) = summary_batch.submit_pending(db_session)
    request.submitted_at = datetime.now(timezone.utc) - timedelta(hours=hours)
    db_session.commit()
    openai_batches.retrieve_error = RuntimeError("forbidden")

    summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert request.status == status
    gave_up = [r for r in caplog.records if "giving up" in r.getMessage()]
    assert [(r.levelname, batch_id in r.getMessage()) for r in gave_up] == [("ERROR", True)] * (
        hours > 24
    )


def test_a_key_whose_pool_cannot_pay_for_one_request_is_logged_once(
    db_session, llm_key, openai_batches, caplog, monkeypatch
):
    monkeypatch.setattr(summary_batch, "_short_of_one_request", set())
    llm_key.max_cost_usd_per_day = Decimal("0.0001")
    add_request(db_session, make_item(db_session), llm_key)

    summary_batch.submit_pending(db_session)
    summary_batch.submit_pending(db_session)

    assert openai_batches.created == []
    assert [r.levelname for r in caplog.records if f"key {llm_key.id}" in r.getMessage()] == [
        "WARNING"
    ]


def test_an_anthropic_batch_takes_only_what_the_pool_has_left_after_batches_in_flight(
    db_session, llm_key, batches
):
    item = make_item(db_session)
    for n in range(2):
        add_request(db_session, item, llm_key, status="submitted", batch_id=f"old-{n}")
    pending = [add_request(db_session, item, llm_key) for _ in range(3)]
    pricing = AnthropicProvider.model_info("claude-haiku-5-5").pricing
    # Room for three requests and a half at most (the cap is stored to 6 places): two are in flight
    one = summary_batch.max_cost(pricing, len(pending[0].prompt))
    llm_key.max_cost_usd_per_day = one * Decimal("3.5")
    db_session.commit()

    summary_batch.submit_pending(db_session)

    ((_, requests),) = batches.created
    assert [r["custom_id"] for r in requests] == [str(pending[0].id)]


def test_a_freshly_claimed_batch_holds_the_keys_slot(db_session, llm_key, openai_batches):
    item = make_item(db_session)
    add_request(
        db_session, item, llm_key, status="submitted", submitted_at=datetime.now(timezone.utc)
    )
    add_request(db_session, item, llm_key)

    assert summary_batch.submit_pending(db_session) == []


# Row locks only hold between separate connections
@pytest.mark.transactional_db
def test_a_key_being_submitted_elsewhere_is_skipped(db_session, llm_key, openai_batches):
    add_request(db_session, make_item(db_session), llm_key)

    with make_session() as other:
        other.query(LLMProviderKey).filter_by(id=llm_key.id).with_for_update().one()
        assert summary_batch.submit_pending(db_session) == []


def test_an_openai_batch_is_not_held_to_the_anthropic_sized_request_cap(
    db_session, llm_key, openai_batches, monkeypatch
):
    monkeypatch.setattr(settings, "SUMMARIZER_BATCH_MAX_REQUESTS", 2)
    item = make_item(db_session)
    for _ in range(3):
        add_request(db_session, item, llm_key)

    summary_batch.submit_pending(db_session)

    (lines,) = openai_batches.uploads.values()
    assert len(lines) == 3


def test_a_claim_that_died_unsent_does_not_reserve_the_pool(db_session, llm_key, batches):
    item = make_item(db_session)
    add_request(
        db_session,
        item,
        llm_key,
        status="submitted",
        submitted_at=datetime.now(timezone.utc) - timedelta(hours=4),
    )
    pending = add_request(db_session, item, llm_key)
    pricing = AnthropicProvider.model_info("claude-haiku-5-5").pricing
    # Room for one request and a half (the cap is stored to 6 places)
    llm_key.max_cost_usd_per_day = summary_batch.max_cost(
        pricing, len(pending.prompt)
    ) * Decimal("1.5")
    db_session.commit()

    summary_batch.submit_pending(db_session)

    ((_, requests),) = batches.created
    assert [r["custom_id"] for r in requests] == [str(pending.id)]


@pytest.mark.parametrize(
    "error, status",
    [
        (RuntimeError("forbidden"), "pending"),
        (
            openai.InternalServerError(
                "down", response=httpx.Response(500, request=OPENAI_REQUEST), body=None
            ),
            "submitted",
        ),
    ],
    ids=["persistent", "transient"],
)
def test_only_a_persistent_collect_failure_gives_up_a_stale_batch(
    db_session, llm_key, openai_batches, error, status
):
    request = add_request(db_session, make_item(db_session), llm_key)
    summary_batch.submit_pending(db_session)
    request.submitted_at = datetime.now(timezone.utc) - timedelta(hours=31)
    db_session.commit()
    openai_batches.retrieve_error = error

    summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert request.status == status


# The requeue and the new claim run on another connection while the first submit is sending
@pytest.mark.transactional_db
def test_a_batch_sent_after_its_claim_was_requeued_is_cancelled_and_leaves_the_new_claim(
    db_session, llm_key, openai_batches, caplog
):
    request = add_request(db_session, make_item(db_session), llm_key)
    reclaimed = datetime.now(timezone.utc)

    def claimed_again():
        with make_session() as other:
            other.query(SummaryRequest).filter_by(id=request.id).update(
                {"submitted_at": reclaimed}
            )
            other.commit()

    openai_batches.on_create = claimed_again

    assert summary_batch.submit_pending(db_session) == []

    db_session.expire_all()
    assert (request.status, request.batch_id, request.attempts, request.submitted_at) == (
        "submitted",
        None,
        0,
        reclaimed,
    )
    assert openai_batches.cancelled == ["batch-0"]
    assert "cancelling it" in caplog.text


def test_requests_a_refused_batch_fails_are_logged(db_session, llm_key, batches, caplog):
    item = make_item(db_session)
    for attempts in (0, 2, 2):
        add_request(db_session, item, llm_key, attempts=attempts)
    batches.create_errors["sk-test-default"] = anthropic.BadRequestError(
        "bad", response=httpx2.Response(400, request=REQUEST), body=None
    )

    with patch.object(settings, "SUMMARIZER_BATCH_MAX_ATTEMPTS", 3):
        summary_batch.submit_pending(db_session)

    assert f"2 summary requests of key {llm_key.id} failed too often" in caplog.text
