import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import anthropic
import httpx
import pytest

from memory.common import embedding, extract, qdrant as qdrant_tools, settings
from memory.common import summary_batch
from memory.common.content_processing import push_chunks_to_qdrant
from memory.common.db.models import SourceItem, SummaryRequest

LONG_TEXT = "Some interesting sentence about things. " * 200
RESPONSE = json.dumps({"summary": "the summary", "tags": ["t1", "t2"]})


def make_item(db_session, content: str = LONG_TEXT) -> SourceItem:
    item = SourceItem(
        content=content,
        modality="text",
        sha256=hashlib.sha256(content.encode()).digest(),
        tags=["bla"],
        mime_type="text/plain",
    )
    db_session.add(item)
    db_session.flush()
    return item


def add_request(db_session, item, **kwargs) -> SummaryRequest:
    request = SummaryRequest(source_id=item.id, prompt=f"prompt for {item.id}", **kwargs)
    db_session.add(request)
    db_session.commit()
    return request


def batch_result(custom_id, type_: str, text: str = RESPONSE):
    message = Mock(content=[Mock(type="thinking"), Mock(type="text", text=text)])
    return Mock(custom_id=str(custom_id), result=Mock(type=type_, message=message))


def ended_batch(age: timedelta = timedelta(hours=1)):
    return Mock(processing_status="ended", created_at=datetime.now(timezone.utc) - age)


def not_found(*_args, **_kwargs):
    request = httpx.Request("GET", "https://api.anthropic.com/v1/messages/batches/x")
    raise anthropic.NotFoundError(
        "not found", response=httpx.Response(404, request=request), body=None
    )


@pytest.fixture
def batches(mock_anthropic_client):
    batches = mock_anthropic_client.messages.batches
    batches.create.return_value = Mock(id="batch_1")
    batches.retrieve.return_value = ended_batch()
    batches.results.return_value = []
    with patch.object(settings, "SUMMARIZER_MODEL", "anthropic/claude-haiku-4-5"):
        yield batches


@pytest.mark.parametrize("use_batch", [True, False])
def test_embed_source_item_defers_summary_when_batching(db_session, use_batch):
    item = make_item(db_session)
    with patch.object(settings, "SUMMARIZER_USE_BATCH", use_batch):
        chunks = embedding.embed_source_item(item)
    db_session.commit()

    has_summary_chunk = any(c.content == "test summary" for c in chunks)
    requests = db_session.query(SummaryRequest).all()
    assert has_summary_chunk is not use_batch
    assert [(r.source_id, r.status, r.chunk_metadata) for r in requests] == (
        [(item.id, "pending", {})] if use_batch else []
    )
    assert all(LONG_TEXT.strip()[:100] in r.prompt for r in requests)


def test_reembedding_replaces_pending_requests(db_session):
    item = make_item(db_session)
    with patch.object(settings, "SUMMARIZER_USE_BATCH", True):
        embedding.embed_source_item(item)
        db_session.commit()
        embedding.embed_source_item(item)
        db_session.commit()

    assert db_session.query(SummaryRequest).count() == 1


def test_submit_pending_creates_batch_and_persists_id(db_session, batches):
    item = make_item(db_session)
    pending = [add_request(db_session, item), add_request(db_session, item)]
    submitted = add_request(db_session, item, status="submitted", batch_id="old")
    failed = add_request(db_session, item, status="failed")

    assert summary_batch.submit_pending(db_session) == "batch_1"

    requests = batches.create.call_args.kwargs["requests"]
    assert [r["custom_id"] for r in requests] == [str(r.id) for r in pending]
    params = requests[0]["params"]
    assert params["model"] == "claude-haiku-4-5"
    assert params["system"] == summary_batch.llms.SUMMARY_SYSTEM_PROMPT
    assert params["output_config"]["format"] == {
        "type": "json_schema",
        "schema": summary_batch.summarizer.SUMMARY_SCHEMA,
    }
    assert params["messages"][0]["role"] == "user"
    assert "extra_headers" not in params
    assert f"prompt for {item.id}" in str(params["messages"][0]["content"])

    db_session.expire_all()
    assert [(r.status, r.batch_id, r.attempts) for r in pending] == [
        ("submitted", "batch_1", 1)
    ] * 2
    assert (submitted.batch_id, failed.status) == ("old", "failed")


def test_submit_pending_respects_byte_budget(db_session, batches):
    item = make_item(db_session)
    first = add_request(db_session, item)
    add_request(db_session, item)

    with patch.object(settings, "SUMMARIZER_BATCH_MAX_BYTES", len(first.prompt) + 1):
        summary_batch.submit_pending(db_session)

    requests = batches.create.call_args.kwargs["requests"]
    assert [r["custom_id"] for r in requests] == [str(first.id)]


def test_submit_pending_without_pending_requests_does_nothing(db_session, batches):
    assert summary_batch.submit_pending(db_session) is None
    batches.create.assert_not_called()


def test_collect_applies_summary_and_is_idempotent(db_session, batches, qdrant):
    item = make_item(db_session)
    existing = item.make_chunk(extract.DataChunk(data=["body text"]))
    embedding.embed_by_model([existing], existing.embedding_model)
    db_session.add(existing)
    db_session.commit()
    push_chunks_to_qdrant([existing])
    request = add_request(
        db_session, item, status="submitted", batch_id="batch_1",
        chunk_metadata={"type": "summary"},
    )
    batches.results.return_value = [batch_result(request.id, "succeeded")]

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


def test_collect_tags_only_request_adds_no_chunk(db_session, batches, qdrant):
    item = make_item(db_session)
    request = add_request(db_session, item, status="submitted", batch_id="batch_1")
    batches.results.return_value = [batch_result(request.id, "succeeded")]

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
    db_session, batches, result_type, attempts, expected
):
    item = make_item(db_session)
    request = add_request(
        db_session, item, status="submitted", batch_id="batch_1", attempts=attempts
    )
    batches.results.return_value = [batch_result(request.id, result_type)]

    with patch.object(settings, "SUMMARIZER_BATCH_MAX_ATTEMPTS", 3):
        summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert (request.status, request.batch_id) == expected


@pytest.mark.parametrize(
    "text", ["", "<summary>s</summary>", '{"summary": "s", "tags": ["a']
)
def test_collect_retries_unparseable_results(db_session, batches, text):
    item = make_item(db_session)
    request = add_request(db_session, item, status="submitted", batch_id="batch_1")
    batches.results.return_value = [batch_result(request.id, "succeeded", text)]

    assert summary_batch.collect_finished(db_session) == {"apply_failed": 1}

    db_session.expire_all()
    assert (request.status, request.batch_id) == ("pending", None)


def test_collect_requeues_requests_missing_from_results(db_session, batches):
    item = make_item(db_session)
    request = add_request(db_session, item, status="submitted", batch_id="batch_1")

    summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert (request.status, request.batch_id) == ("pending", None)


def test_collect_leaves_unfinished_batches(db_session, batches):
    item = make_item(db_session)
    request = add_request(db_session, item, status="submitted", batch_id="batch_1")
    batches.retrieve.return_value = Mock(processing_status="in_progress")

    assert summary_batch.collect_finished(db_session) == {}

    batches.results.assert_not_called()
    db_session.expire_all()
    assert (request.status, request.batch_id) == ("submitted", "batch_1")


def test_failed_embedding_records_no_summary_request(db_session):
    item = make_item(db_session)
    with (
        patch.object(settings, "SUMMARIZER_USE_BATCH", True),
        patch.object(embedding, "embed_chunks", side_effect=RuntimeError("voyage down")),
        pytest.raises(RuntimeError),
    ):
        embedding.embed_source_item(item)
    db_session.commit()

    assert db_session.query(SummaryRequest).count() == 0


def test_reembedding_without_summary_drops_stale_requests(db_session):
    item = make_item(db_session)
    with patch.object(settings, "SUMMARIZER_USE_BATCH", True):
        embedding.embed_source_item(item)
        db_session.commit()
        item.content = "now short enough to need no summary"
        embedding.embed_source_item(item)
        db_session.commit()

    assert db_session.query(SummaryRequest).count() == 0


def test_collect_unreadable_batch_does_not_block_others(db_session, batches):
    item = make_item(db_session)
    broken = add_request(db_session, item, status="submitted", batch_id="batch_gone")
    healthy = add_request(db_session, item, status="submitted", batch_id="batch_1")

    def retrieve(batch_id):
        if batch_id == "batch_gone":
            raise RuntimeError("not found")
        return ended_batch()

    batches.retrieve.side_effect = retrieve

    summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert (broken.status, healthy.status) == ("submitted", "pending")


@pytest.mark.parametrize(
    "setup",
    [
        lambda b: setattr(b.retrieve, "side_effect", not_found),
        lambda b: setattr(b.results, "side_effect", not_found),
        lambda b: (
            setattr(b.retrieve, "return_value", ended_batch(timedelta(days=30))),
            setattr(b.results, "side_effect", RuntimeError("results gone")),
        ),
    ],
    ids=["batch_not_found", "results_not_found", "results_expired"],
)
def test_collect_requeues_requests_of_unrecoverable_batches(db_session, batches, setup):
    item = make_item(db_session)
    request = add_request(db_session, item, status="submitted", batch_id="batch_1")
    setup(batches)

    summary_batch.collect_finished(db_session)

    db_session.expire_all()
    assert (request.status, request.batch_id) == ("pending", None)


def test_collect_without_anthropic_model_does_nothing(db_session, batches):
    item = make_item(db_session)
    request = add_request(db_session, item, status="submitted", batch_id="batch_1")

    with patch.object(settings, "SUMMARIZER_MODEL", "openai/gpt-4o-mini"):
        assert summary_batch.collect_finished(db_session) == {}

    batches.retrieve.assert_not_called()
    db_session.expire_all()
    assert (request.status, request.batch_id) == ("submitted", "batch_1")


def test_collect_merges_tags_even_if_summary_push_fails(db_session, batches, qdrant):
    item = make_item(db_session)
    existing = item.make_chunk(extract.DataChunk(data=["body text"]))
    embedding.embed_by_model([existing], existing.embedding_model)
    db_session.add(existing)
    db_session.commit()
    push_chunks_to_qdrant([existing])
    request = add_request(
        db_session, item, status="submitted", batch_id="batch_1",
        chunk_metadata={"type": "summary"},
    )
    batches.results.return_value = [batch_result(request.id, "succeeded")]

    with patch.object(
        summary_batch, "push_chunks_to_qdrant", side_effect=RuntimeError("qdrant down")
    ):
        summary_batch.collect_finished(db_session)

    payloads = qdrant_tools.get_payloads(qdrant, "text", [str(existing.id)])
    assert set(payloads[str(existing.id)]["tags"]) == {"bla", "t1", "t2"}
