"""Ingestion summaries via the Anthropic Message Batches API (half the price of sync calls),
grouped into one batch per provider key, on that key.

Embedding records a pending `SummaryRequest` per deferred summarize call
instead of calling the LLM (see `summarizer.deferring`), stamped with the key
that pays for it. `submit_pending` marks each key's pending requests
submitted, sends them as one batch and then stamps them with its id. A request
left submitted without a batch id (the process died in between) is not
retried, since it may already be billed; set it back to pending to retry it.
`collect_finished` applies the results of ended
batches: it adds the summary chunk (embedded and pushed to Qdrant), merges the
tags into the item's existing chunk payloads, and deletes the request. Failed
results go back to pending until they run out of attempts.
"""

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import accumulate
from typing import Any

import anthropic
import httpx
import httpx2
from sqlalchemy import func
from sqlalchemy.orm import Session

from memory.agents import caps
from memory.agents.call import parse_result, request_args
from memory.agents.provider_keys import Credential, credential, notify_key_rejected, reject_key
from memory.agents.system import SUMMARISE
from memory.common import embedding, extract, qdrant, settings, summarizer
from memory.common.content_processing import push_chunks_to_qdrant
from memory.common.db.models import Chunk, KeyStatus, LLMProviderKey, SummaryRequest
from memory.common.llms.anthropic_provider import AnthropicProvider
from memory.common.llms.registry import split_model

logger = logging.getLogger(__name__)

# The Batches API keeps results for 29 days after the batch is created
RESULTS_RETENTION = timedelta(days=29)
# The Batches API bills half the synchronous price.
BATCH_DISCOUNT = Decimal("0.5")
# Failures that say nothing about the requests, so they don't use up an attempt
TRANSIENT_ERRORS = (
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    httpx2.RequestError,
    httpx.RequestError,
)


def batch_client(api_key: str) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key)


def summary_provider(key: Credential) -> AnthropicProvider:
    return AnthropicProvider(api_key=key.key, model=split_model(SUMMARISE.model)[1])


def batch_request(provider: AnthropicProvider, request: SummaryRequest) -> dict[str, Any]:
    return {
        "custom_id": str(request.id),
        "params": provider.build_agent_request(**request_args(SUMMARISE, request.prompt)),
    }


def submit_pending(session: Session) -> list[str]:
    """Submit each key's pending requests as one batch on that key; return the batch ids."""
    key_ids = [
        key_id
        for (key_id,) in session.query(SummaryRequest.provider_key_id)
        .filter(SummaryRequest.status == "pending")
        .distinct()
    ]
    return [batch_id for key_id in key_ids if (batch_id := submit_for_key(session, key_id))]


def submit_for_key(session: Session, key_id: int) -> str | None:
    key = session.get(LLMProviderKey, key_id)
    if key is None or key.status != KeyStatus.OK:
        return None
    try:
        caps.key_allowance(session, key)
    except caps.CapExceeded as e:
        logger.info("summary batch for key %s waits: %s", key_id, e)
        return None
    cred = credential(key)

    # Row locks keep a concurrent submitter from sending the same requests
    rows = (
        session.query(SummaryRequest.id, func.octet_length(SummaryRequest.prompt))
        .filter(SummaryRequest.status == "pending", SummaryRequest.provider_key_id == key_id)
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
    provider = summary_provider(cred)
    payload = [batch_request(provider, r) for r in requests]
    # Claimed before sending: if the commit after the call failed, requests
    # still pending would be sent, and billed, again.
    for request in requests:
        request.status = "submitted"
    session.commit()
    try:
        batch = batch_client(cred.key).messages.batches.create(requests=payload)  # type: ignore[arg-type]
    except anthropic.AuthenticationError:
        release(session, requests)
        if reject_key(cred.id, cred.key):
            notify_key_rejected(cred)
        return None
    # One key's outage or rate limit must not hold up the other keys' batches
    except TRANSIENT_ERRORS as e:
        release(session, requests)
        logger.warning("summary batch for key %s not sent: %s", key_id, type(e).__name__)
        return None
    except anthropic.APIError as e:
        logger.warning("summary batch for key %s refused: %s", key_id, e.message)
        for request in requests:
            request.attempts += 1
            retry_or_fail(request)
        session.commit()
        return None
    except Exception:
        release(session, requests)
        raise
    for request in requests:
        request.batch_id = batch.id
        request.attempts += 1
    session.commit()
    logger.info(f"Submitted summary batch {batch.id} with {len(requests)} requests")
    return batch.id


def release(session: Session, requests: list[SummaryRequest]) -> None:
    for request in requests:
        request.status = "pending"
    session.commit()


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


def apply_summary(session: Session, request: SummaryRequest, data: dict[str, Any]) -> None:
    item = request.source
    summary, tags = summarizer.parse_summary(data)
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


def collect_batch(
    session: Session,
    client: anthropic.Anthropic,
    provider: AnthropicProvider,
    key: Credential,
    batch_id: str,
) -> Counter[str]:
    stats: Counter[str] = Counter()
    pricing = provider.model_info(provider.model).pricing
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
        usage = AnthropicProvider.agent_usage(message)
        cost = pricing.cost(usage) * BATCH_DISCOUNT
        # Committed with the apply: committing first would drop the claim's lock
        # and let a concurrent collector apply, and charge, the result again
        caps.record_spend(session, key_id=key.id, agent=SUMMARISE.slug, cost=cost)
        try:
            completion = provider.agent_completion(
                [b.to_dict() for b in message.content], message.stop_reason or "", usage, cost
            )
            _, data = parse_result(SUMMARISE, completion)
            apply_summary(session, request, data or {})
            stats["applied"] += 1
        except Exception:
            logger.exception(f"Failed to apply summary request {request.id}")
            session.rollback()
            stats["apply_failed"] += 1
            # Re-fetch: the rollback expired it, and it may since have been claimed
            if request := claim(session, batch_id, result.custom_id):
                caps.record_spend(session, key_id=key.id, agent=SUMMARISE.slug, cost=cost)
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


def collect_from_key(session: Session, batch_id: str, key_id: int) -> Counter[str]:
    # Requests cascade away with their key, so it is still there
    row = session.get_one(LLMProviderKey, key_id)
    # The batch keeps its results; they are read once the key is replaced
    if row.status != KeyStatus.OK:
        return Counter()
    key = credential(row)
    client = batch_client(key.key)
    try:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status != "ended":
            return Counter()
        if batch.created_at < datetime.now(timezone.utc) - RESULTS_RETENTION:
            return Counter(expired_batch=requeue_batch(session, batch_id))
        return collect_batch(session, client, summary_provider(key), key, batch_id)
    except anthropic.AuthenticationError:
        session.rollback()
        if reject_key(key.id, key.key):
            notify_key_rejected(key)
        return Counter(rejected_key=1)
    except anthropic.NotFoundError:
        logger.warning(f"Summary batch {batch_id} not found, requeueing its requests")
        session.rollback()
        return Counter(missing_batch=requeue_batch(session, batch_id))


def collect_finished(session: Session) -> dict[str, int]:
    """Apply the results of every ended batch that still has submitted requests."""
    batches = (
        session.query(SummaryRequest.batch_id, SummaryRequest.provider_key_id)
        .filter(SummaryRequest.status == "submitted", SummaryRequest.batch_id.is_not(None))
        .distinct()
        .all()
    )
    stats: Counter[str] = Counter()
    for batch_id, key_id in batches:
        # One unreadable batch or key (deleted, expired results, API blip) must
        # not starve the others
        try:
            stats += collect_from_key(session, batch_id, key_id)
        except Exception:
            logger.exception(f"Failed to collect summary batch {batch_id}")
            session.rollback()
            stats["batch_errors"] += 1
    return dict(stats)
