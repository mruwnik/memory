"""Ingestion summaries via the Anthropic Message Batches API (half the price of sync calls).

Embedding records a pending `SummaryRequest` per deferred summarize call
instead of calling the LLM (see `summarizer.deferring`). `submit_pending`
marks pending requests submitted, sends them as one batch and then stamps them
with its id. A request left submitted without a batch id (the process died in
between) is not retried, since it may already be billed; set it back to
pending to retry it. `collect_finished` applies the results of ended
batches: it adds the summary chunk (embedded and pushed to Qdrant), merges the
tags into the item's existing chunk payloads, and deletes the request. Failed
results go back to pending until they run out of attempts.
"""

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from itertools import accumulate
from typing import Any

import anthropic
from sqlalchemy import func
from sqlalchemy.orm import Session

from memory.agents.system import SUMMARISE, SUMMARY_SCHEMA
from memory.common import embedding, extract, llms, qdrant, settings, summarizer
from memory.common.content_processing import push_chunks_to_qdrant
from memory.common.db.models import Chunk, SummaryRequest
from memory.common.llms import AnthropicProvider, Message, MessageRole, TextContent

logger = logging.getLogger(__name__)

# The Batches API keeps results for 29 days after the batch is created
RESULTS_RETENTION = timedelta(days=29)


def summary_provider() -> AnthropicProvider:
    provider = llms.create_provider(model=settings.SUMMARIZER_MODEL)
    if not isinstance(provider, AnthropicProvider):
        raise ValueError(f"Batch summaries need an anthropic model, not {settings.SUMMARIZER_MODEL}")
    return provider


def batch_request(provider: AnthropicProvider, request: SummaryRequest) -> dict[str, Any]:
    return provider.batch_request(
        str(request.id),
        [Message(role=MessageRole.USER, content=[TextContent(text=request.prompt)])],
        SUMMARISE.system_prompt,
        llms.summary_settings(SUMMARY_SCHEMA),
    )


def submit_pending(session: Session) -> str | None:
    """Submit pending requests as one batch; return its id, or None if nothing was pending."""
    # Row locks keep a concurrent submitter from sending the same requests
    rows = (
        session.query(SummaryRequest.id, func.octet_length(SummaryRequest.prompt))
        .filter(SummaryRequest.status == "pending")
        .order_by(SummaryRequest.id)
        .limit(settings.SUMMARIZER_BATCH_MAX_REQUESTS)
        .with_for_update(skip_locked=True)
        .all()
    )
    sizes = accumulate(size for _, size in rows)
    ids = [id for (id, _), total in zip(rows, sizes) if total <= settings.SUMMARIZER_BATCH_MAX_BYTES]
    if not ids:
        return None

    requests = (
        session.query(SummaryRequest)
        .filter(SummaryRequest.id.in_(ids))
        .order_by(SummaryRequest.id)
        .all()
    )
    provider = summary_provider()
    payload = [batch_request(provider, r) for r in requests]
    # Claimed before sending: if the commit after the call failed, requests
    # still pending would be sent, and billed, again.
    for request in requests:
        request.status = "submitted"
    session.commit()
    try:
        batch = provider.client.messages.batches.create(requests=payload)  # type: ignore[arg-type]
    except Exception:
        for request in requests:
            request.status = "pending"
        session.commit()
        raise
    for request in requests:
        request.batch_id = batch.id
        request.attempts += 1
    session.commit()
    logger.info(f"Submitted summary batch {batch.id} with {len(requests)} requests")
    return batch.id


def retry_or_fail(request: SummaryRequest) -> None:
    if request.attempts >= settings.SUMMARIZER_BATCH_MAX_ATTEMPTS:
        logger.warning(f"Summary request {request.id} failed {request.attempts} times, giving up")
        request.status = "failed"
        return
    request.status = "pending"
    request.batch_id = None


def add_tags(chunk_refs: list[tuple[str, str]], tags: list[str]) -> None:
    """Merge `tags` into the Qdrant payload tags of the (collection, chunk id) points."""
    if not tags or not chunk_refs:
        return

    by_collection: dict[str, list[str]] = defaultdict(list)
    for collection, chunk_id in chunk_refs:
        by_collection[collection].append(chunk_id)

    client = qdrant.get_qdrant_client()
    for collection, ids in by_collection.items():
        for point_id, payload in qdrant.get_payloads(client, collection, ids).items():
            merged = sorted(set(payload.get("tags", [])) | set(tags))
            qdrant.set_payload(client, collection, point_id, {"tags": merged})


def apply_summary(session: Session, request: SummaryRequest, response: str) -> None:
    item = request.source
    summary, tags = summarizer.parse_summary(json.loads(response))
    existing = [(str(c.collection_name), str(c.id)) for c in item.chunks]

    new_chunks: list[Chunk] = []
    if request.chunk_metadata is not None and summary:
        chunk = item.make_chunk(
            extract.DataChunk(
                data=[summary],
                metadata=extract.merge_metadata(request.chunk_metadata, {"tags": tags}),
                modality=request.modality,
            )
        )
        new_chunks = embedding.embed_by_model([chunk], str(chunk.embedding_model))
        session.add(chunk)

    session.delete(request)
    session.commit()

    try:
        push_chunks_to_qdrant(new_chunks)
    except Exception:
        # The chunk is committed; reingest_missing_chunks restores a missing vector
        logger.exception(f"Failed to push summary of item {item.id} to Qdrant")
    try:
        add_tags(existing, tags)
    except Exception:
        logger.exception(f"Failed to merge summary tags of item {item.id} into Qdrant")


def claim(session: Session, batch_id: str, custom_id: str) -> SummaryRequest | None:
    """Lock the request a result belongs to, unless it was already handled or is being handled."""
    return (
        session.query(SummaryRequest)
        .filter(
            SummaryRequest.id == int(custom_id),
            SummaryRequest.batch_id == batch_id,
            SummaryRequest.status == "submitted",
        )
        .with_for_update(skip_locked=True)
        .one_or_none()
    )


def collect_batch(session: Session, client: Any, batch_id: str) -> Counter[str]:
    stats: Counter[str] = Counter()
    for result in client.messages.batches.results(batch_id):
        request = claim(session, batch_id, result.custom_id)
        if request is None:
            continue

        if result.result.type != "succeeded":
            stats[result.result.type] += 1
            retry_or_fail(request)
            session.commit()
            continue

        message = result.result.message
        text = "".join(b.text for b in message.content if b.type == "text")
        try:
            apply_summary(session, request, text)
            stats["applied"] += 1
        except Exception:
            logger.exception(f"Failed to apply summary request {request.id}")
            session.rollback()
            stats["apply_failed"] += 1
            # Re-fetch: the rollback expired it, and it may since have been claimed
            if request := claim(session, batch_id, result.custom_id):
                retry_or_fail(request)
                session.commit()

    # Requests the ended batch returned no result for go round again
    stats["missing"] += requeue_batch(session, batch_id)
    return stats


def requeue_batch(session: Session, batch_id: str) -> int:
    """Send the batch's still-submitted requests round again; return how many."""
    requests = (
        session.query(SummaryRequest)
        .filter(SummaryRequest.batch_id == batch_id, SummaryRequest.status == "submitted")
        .with_for_update(skip_locked=True)
        .all()
    )
    for request in requests:
        retry_or_fail(request)
    session.commit()
    return len(requests)


def collect_finished(session: Session) -> dict[str, int]:
    """Apply the results of every ended batch that still has submitted requests."""
    # Without an anthropic model there is no client to read batches with; the
    # requests wait until one is configured again
    if not settings.SUMMARIZER_MODEL.startswith("anthropic/"):
        return {}

    batch_ids = [
        batch_id
        for (batch_id,) in session.query(SummaryRequest.batch_id)
        .filter(SummaryRequest.status == "submitted", SummaryRequest.batch_id.is_not(None))
        .distinct()
    ]
    if not batch_ids:
        return {}

    client = summary_provider().client
    stats: Counter[str] = Counter()
    for batch_id in batch_ids:
        # One unreadable batch (deleted, expired results, API blip) must not
        # starve the others
        try:
            batch = client.messages.batches.retrieve(batch_id)
            if batch.processing_status != "ended":
                continue
            if batch.created_at < datetime.now(timezone.utc) - RESULTS_RETENTION:
                stats["expired_batch"] += requeue_batch(session, batch_id)
                continue
            stats += collect_batch(session, client, batch_id)
        except anthropic.NotFoundError:
            logger.warning(f"Summary batch {batch_id} not found, requeueing its requests")
            session.rollback()
            stats["missing_batch"] += requeue_batch(session, batch_id)
        except Exception:
            logger.exception(f"Failed to collect summary batch {batch_id}")
            session.rollback()
            stats["batch_errors"] += 1
    return dict(stats)
