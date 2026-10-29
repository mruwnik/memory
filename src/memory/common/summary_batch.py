"""Ingestion summaries via the providers' batch APIs (half the price of sync calls),
grouped into one batch per provider key, on that key.

Embedding records a pending `SummaryRequest` per deferred summarize call
instead of calling the LLM (see `summarizer.deferring`), stamped with the key
that pays for it. `submit_pending` marks each key's pending requests
submitted, sends them as one batch and then stamps them with its id. A request
left submitted without a batch id for an hour (the process died in between) is
sent round again.
`collect_finished` applies the results of ended
batches: it adds the summary chunk (embedded and pushed to Qdrant), merges the
tags into the item's existing chunk payloads, and deletes the request. Failed
results go back to pending until they run out of attempts.
"""

import logging
from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from itertools import accumulate
from typing import Any

from sqlalchemy import ColumnElement, case, func, update
from sqlalchemy.orm import Query, Session, load_only

from memory.agents import caps
from memory.agents.call import parse_result, report_model_unavailable, request_args
from memory.agents.provider_keys import (
    Credential,
    credential,
    ingest_user,
    notify_key_rejected,
    reject_key,
)
from memory.agents.system import SUMMARISE
from memory.common import embedding, extract, qdrant, settings, summarizer
from memory.common.content_processing import push_chunks_to_qdrant
from memory.common.db.models import Chunk, KeyStatus, LLMProviderKey, SourceItem, SummaryRequest
from memory.common.llms.batches import (
    BATCH_APIS,
    RESULTS_RETENTION,
    TRANSIENT_ERRORS,
    BatchAPI,
)
from memory.common.llms.neutral import AgentUsage, Pricing
from memory.common.llms.registry import split_model

logger = logging.getLogger(__name__)

# The batch APIs bill half the synchronous price.
BATCH_DISCOUNT = Decimal("0.5")
# Well past the SDK's worst case for sending a batch (600s timeouts, 2 retries, two
# calls, a slow uplink); a claim older than this died unsent
CLAIM_HOLD = timedelta(hours=3)
# Providers end a batch within 24h; one still unreadable well after that is given up
STALE_BATCH = timedelta(hours=30)
# Prompts read from the DB at a time while a batch is built
PROMPT_CHUNK = 1000
# Keys already warned that their daily pool can't pay for one request; only
# submit_for_key keeps it in step with the keys' caps
_short_of_one_request: set[int] = set()


def batch_api(key: Credential) -> BatchAPI[Any]:
    model = SUMMARISE.model_for(key.provider)
    if model is None or key.provider not in BATCH_APIS:
        raise ValueError(f"{SUMMARISE.slug} has no {key.provider} batch model")
    return BATCH_APIS[key.provider](api_key=key.key, model=split_model(model)[1])


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
    # Locked until the claim commits, so a concurrent submitter skips the key
    # rather than sending a second batch past the in-flight check
    key = (
        session.query(LLMProviderKey)
        .filter(LLMProviderKey.id == key_id)
        .with_for_update(skip_locked=True)
        .one_or_none()
    )
    if key is None or key.status != KeyStatus.OK:
        return None
    try:
        allowance = caps.key_allowance(session, key)
    except caps.CapExceeded as e:
        logger.info("summary batch for key %s waits: %s", key_id, e)
        return None
    cred = credential(key, None)
    api = batch_api(cred)
    if api.ONE_AT_A_TIME and in_flight(session, key_id):
        return None

    # Row locks keep a concurrent submitter from sending the same requests
    limit = min(max_requests(api), key.summary_batch_limit or max_requests(api))
    rows = (
        session.query(SummaryRequest.id, func.octet_length(SummaryRequest.prompt))
        .filter(SummaryRequest.status == "pending", SummaryRequest.provider_key_id == key_id)
        .order_by(SummaryRequest.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .all()
    )
    pricing = api.provider.model_info(api.provider.model).pricing
    left = allowance - in_flight_cost(session, key_id, pricing)
    sizes = accumulate(size for _, size in rows)
    costs = accumulate(max_cost(pricing, size) for _, size in rows)
    ids = [
        id
        for (id, _), total, cost in zip(rows, sizes, costs)
        if total <= settings.SUMMARIZER_BATCH_MAX_BYTES and cost <= left
    ]
    if not (rows and max_cost(pricing, rows[0][1]) > allowance):
        _short_of_one_request.discard(key_id)
    elif key_id not in _short_of_one_request:
        _short_of_one_request.add(key_id)
        logger.warning("summary batches for key %s wait: its cap is below one request", key_id)
    if not ids:
        return None

    # Claimed before sending: if the commit after the call failed, requests
    # still pending would be sent, and billed, again.
    stamp = datetime.now(timezone.utc)
    claimed(session, ids).update(
        {SummaryRequest.status: "submitted", SummaryRequest.submitted_at: stamp}
    )
    session.commit()
    # Every later update is to this claim only: one given up as dead may since
    # have been claimed, and sent, again
    claim = Claim(ids, stamp)
    try:
        batch_id = api.create(turns(session, ids))
    except api.KEY_REJECTED:
        release(session, claim)
        if reject_key(cred.id, cred.key):
            notify_key_rejected(cred)
        return None
    # One key's outage or rate limit must not hold up the other keys' batches
    except api.TRANSIENT as e:
        release(session, claim)
        logger.warning("summary batch for key %s not sent: %s", key_id, type(e).__name__)
        return None
    except api.FORBIDDEN:
        logger.error("summary batch for key %s forbidden: no batch API access?", key_id)
        refuse(session, claim, key_id)
        return None
    except api.REFUSED as e:
        logger.warning("summary batch for key %s refused: %s", key_id, e.message)
        refuse(session, claim, key_id)
        return None
    except Exception:
        session.rollback()
        release(session, claim)
        raise
    stamped = claim.rows(session).update(
        {SummaryRequest.batch_id: batch_id, SummaryRequest.attempts: SummaryRequest.attempts + 1}
    )
    session.commit()
    if stamped < len(ids):
        abandon_sent_batch(session, api, batch_id, key_id, lost=len(ids) - stamped)
        return None
    logger.info(f"Submitted summary batch {batch_id} with {len(ids)} requests")
    return batch_id


@dataclass(frozen=True)
class Claim:
    """The requests one submit claimed, and when."""

    ids: list[int]
    stamp: datetime

    def criteria(self) -> list[ColumnElement[bool]]:
        return [
            SummaryRequest.id.in_(self.ids),
            SummaryRequest.status == "submitted",
            SummaryRequest.batch_id.is_(None),
            SummaryRequest.submitted_at == self.stamp,
        ]

    def rows(self, session: Session) -> Query[SummaryRequest]:
        return session.query(SummaryRequest).filter(*self.criteria())


def abandon_sent_batch(
    session: Session, api: BatchAPI[Any], batch_id: str, key_id: int, lost: int
) -> None:
    """Cancel a batch sent after part of its claim was given up as dead and claimed
    again, so those requests aren't billed twice; the rest go back to pending."""
    logger.warning(
        "summary batch %s of key %s was sent after %s of its requests were requeued; cancelling it",
        batch_id,
        key_id,
        lost,
    )
    try:
        api.cancel(batch_id)
    except Exception as e:
        logger.warning("summary batch %s not cancelled: %s", batch_id, type(e).__name__)
    session.query(SummaryRequest).filter(
        SummaryRequest.batch_id == batch_id, SummaryRequest.status == "submitted"
    ).update(
        {
            SummaryRequest.status: "pending",
            SummaryRequest.batch_id: None,
            SummaryRequest.attempts: SummaryRequest.attempts - 1,
        }
    )
    session.commit()


def claimed(session: Session, ids: list[int]) -> Query[SummaryRequest]:
    return session.query(SummaryRequest).filter(SummaryRequest.id.in_(ids))


def turns(session: Session, ids: list[int]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Each request's custom id and turn, its prompt read a chunk at a time, so a
    batch never holds all its prompts at once."""
    for start in range(0, len(ids), PROMPT_CHUNK):
        rows = (
            session.query(SummaryRequest.id, SummaryRequest.prompt)
            .filter(SummaryRequest.id.in_(ids[start : start + PROMPT_CHUNK]))
            .order_by(SummaryRequest.id)
        )
        for id, prompt in rows:
            yield str(id), request_args(SUMMARISE, prompt)


def holds_slot() -> ColumnElement[bool]:
    """Submitted requests with a batch out, or claimed recently and being sent."""
    claim_cutoff = datetime.now(timezone.utc) - CLAIM_HOLD
    return (SummaryRequest.status == "submitted") & (
        SummaryRequest.batch_id.is_not(None) | (SummaryRequest.submitted_at > claim_cutoff)
    )


def in_flight(session: Session, key_id: int) -> bool:
    """Whether the key has a batch out, or one claimed and being sent."""
    return session.query(
        session.query(SummaryRequest)
        .filter(SummaryRequest.provider_key_id == key_id, holds_slot())
        .exists()
    ).scalar()


def in_flight_cost(session: Session, key_id: int, pricing: Pricing) -> Decimal:
    """The estimated cost of the key's requests in flight, not yet charged."""
    sizes = session.query(func.octet_length(SummaryRequest.prompt)).filter(
        SummaryRequest.provider_key_id == key_id, holds_slot()
    )
    return sum((max_cost(pricing, size) for (size,) in sizes), Decimal(0))


def max_cost(pricing: Pricing, prompt_bytes: int) -> Decimal:
    """A request's estimated cost at most: about 4 bytes a token, and every
    output token used. The system prompt and tool schema aren't counted."""
    usage = AgentUsage(input_tokens=prompt_bytes // 4, output_tokens=SUMMARISE.max_output_tokens)
    return pricing.cost(usage) * BATCH_DISCOUNT


def refuse(session: Session, claim: Claim, key_id: int) -> None:
    """Count an attempt against the requests of a refused batch, and retry or fail them."""
    attempts = SummaryRequest.attempts + 1
    statuses = session.execute(
        update(SummaryRequest)
        .where(*claim.criteria())
        .values(
            attempts=attempts,
            status=case(
                (attempts >= settings.SUMMARIZER_BATCH_MAX_ATTEMPTS, "failed"), else_="pending"
            ),
        )
        .returning(SummaryRequest.status)
        .execution_options(synchronize_session=False)
    ).scalars()
    if failed := sum(status == "failed" for status in statuses):
        logger.warning("%s summary requests of key %s failed too often, giving up", failed, key_id)
    session.commit()


def release(session: Session, claim: Claim) -> None:
    claim.rows(session).update({SummaryRequest.status: "pending"})
    session.commit()


def requeue_dead_claims(session: Session) -> int:
    """Send round again the claims whose submit died before it got a batch id; they
    used no attempt. One whose batch was sent and only its last commit failed is
    billed twice, the price of not losing the rest."""
    cutoff = datetime.now(timezone.utc) - CLAIM_HOLD
    keys = session.execute(
        update(SummaryRequest)
        .where(
            SummaryRequest.status == "submitted",
            SummaryRequest.batch_id.is_(None),
            SummaryRequest.submitted_at.is_(None) | (SummaryRequest.submitted_at < cutoff),
        )
        .values(status="pending")
        .returning(SummaryRequest.provider_key_id)
        .execution_options(synchronize_session=False)
    ).scalars().all()
    session.commit()
    if keys:
        logger.warning(
            "requeued %s summary requests of keys %s claimed but never sent",
            len(keys),
            sorted(set(keys)),
        )
    return len(keys)


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
            extract.summary_chunk(summary, tags, request.chunk_metadata, request.modality)
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


def spenders(session: Session, batch_id: str) -> dict[int, int | None]:
    """Who each of the batch's requests was made for, read in one query rather than one per
    result; only an item without a creator walks its source chain."""
    rows = (
        session.query(SummaryRequest.id, SourceItem)
        .join(SummaryRequest.source)
        .options(load_only(SourceItem.id, SourceItem.creator_id))
        .filter(SummaryRequest.batch_id == batch_id, SummaryRequest.status == "submitted")
    )
    return {request_id: ingest_user(item) for request_id, item in rows}


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
    api: BatchAPI[Any],
    key: Credential,
    batch_id: str,
) -> Counter[str]:
    stats: Counter[str] = Counter()
    pricing = api.provider.model_info(api.provider.model).pricing
    model = f"{key.provider}/{api.provider.model}"
    spent_for = spenders(session, batch_id)
    for result in api.results(batch_id):
        request = claim(session, batch_id, result.custom_id)
        if request is None:
            continue

        if result.completion is None:
            stats[result.failure or "errored"] += 1
            if result.model_unavailable:
                report_model_unavailable(key, api.provider.model, SUMMARISE.slug)
            retry_or_fail(request)
            session.commit()
            continue

        cost = pricing.cost(result.usage) * BATCH_DISCOUNT
        charge = partial(
            caps.record_spend,
            session,
            key_id=key.id,
            user_id=spent_for[request.id],
            agent=SUMMARISE.slug,
            model=model,
            cost=cost,
            usage=result.usage,
        )
        # Committed with the apply: committing first would drop the claim's lock
        # and let a concurrent collector apply, and charge, the result again
        charge()
        try:
            _, data = parse_result(SUMMARISE, result.completion(cost))
            apply_summary(session, request, data or {})
            stats["applied"] += 1
        except Exception as e:
            if isinstance(e, embedding.EmbeddingsNotConfigured):
                embedding.warn_not_configured()
            else:
                logger.exception(f"Failed to apply summary request {request.id}")
            session.rollback()
            stats["apply_failed"] += 1
            # Re-fetch: the rollback expired it, and it may since have been claimed
            if request := claim(session, batch_id, result.custom_id):
                charge()
                retry_or_fail(request)
                session.commit()

    # Requests the ended batch returned no result for go round again
    stats["missing"] += requeue_batch(session, batch_id)
    return stats


def requeue_batch(session: Session, batch_id: str, refund: bool = False) -> int:
    """Send the batch's still-submitted requests round again; return how many.
    `refund` gives back the attempt their submit used."""
    requests = (
        session.query(SummaryRequest)
        .filter(SummaryRequest.batch_id == batch_id, SummaryRequest.status == "submitted")
        .with_for_update(skip_locked=True)
        .all()
    )
    for request in requests:
        if refund:
            request.attempts -= 1
        retry_or_fail(request)
    session.commit()
    return len(requests)


def shrink_batches(session: Session, key: LLMProviderKey, batch_id: str) -> int:
    """Halve the key's batches after the provider refused this one for its queue
    limit, and requeue its requests without using an attempt. A lone request it
    still refuses uses one, like any refused batch, so it can't loop forever."""
    size = (
        session.query(SummaryRequest)
        .filter(SummaryRequest.batch_id == batch_id, SummaryRequest.status == "submitted")
        .count()
    )
    logger.warning(f"Summary batch {batch_id} of {size} requests is over the queue limit")
    # An older, bigger batch refused later mustn't raise a limit already lowered
    key.summary_batch_limit = max(min(key.summary_batch_limit or size, size // 2), 1)
    return requeue_batch(session, batch_id, refund=size > 1)


def max_requests(api: BatchAPI[Any]) -> int:
    return api.MAX_REQUESTS or settings.SUMMARIZER_BATCH_MAX_REQUESTS


def grow_batches(key: LLMProviderKey, ceiling: int) -> None:
    """Double the key's batches after a success, back to no limit once at the default;
    stepping back up keeps a backlog over the queue limit from swinging to full size."""
    grown = (key.summary_batch_limit or 0) * 2
    key.summary_batch_limit = grown if grown < ceiling else None


def collect_from_key(session: Session, batch_id: str, key_id: int) -> Counter[str]:
    # Requests cascade away with their key, so it is still there
    row = session.get_one(LLMProviderKey, key_id)
    # The batch keeps its results; they are read once the key is replaced by
    # one of the same project or workspace
    if row.status != KeyStatus.OK:
        return Counter()
    key = credential(row, None)
    api = batch_api(key)
    try:
        state = api.state(batch_id)
        if not state.ended:
            return Counter()
        if state.created_at < datetime.now(timezone.utc) - RESULTS_RETENTION:
            stats = Counter(expired_batch=requeue_batch(session, batch_id))
        elif state.model_unavailable:
            report_model_unavailable(key, api.provider.model, SUMMARISE.slug)
            stats = Counter(model_unavailable=requeue_batch(session, batch_id))
        elif state.over_queue_limit:
            stats = Counter(over_queue_limit=shrink_batches(session, row, batch_id))
        else:
            stats = collect_batch(session, api, key, batch_id)
            if stats["applied"] and row.summary_batch_limit:
                grow_batches(row, max_requests(api))
                session.commit()
    except api.KEY_REJECTED:
        session.rollback()
        if reject_key(key.id, key.key):
            notify_key_rejected(key)
        return Counter(rejected_key=1)
    except api.NOT_FOUND:
        logger.warning(f"Summary batch {batch_id} not found, requeueing its requests")
        session.rollback()
        return Counter(missing_batch=requeue_batch(session, batch_id))
    api.finish(batch_id)
    return stats


def give_up_if_stale(session: Session, batch_id: str, key_id: int) -> int:
    """Requeue a batch still unreadable long after its provider would have ended it,
    so it stops holding its key's slot; return how many requests it had."""
    # The failure that brought us here may be the DB's; the next batches still get their turn
    try:
        submitted = (
            session.query(func.min(SummaryRequest.submitted_at))
            .filter(SummaryRequest.batch_id == batch_id, SummaryRequest.status == "submitted")
            .scalar()
        )
        if submitted is None or submitted > datetime.now(timezone.utc) - STALE_BATCH:
            return 0
        logger.error(
            "Summary batch %s of key %s can't be collected; giving up on it", batch_id, key_id
        )
        return requeue_batch(session, batch_id)
    except Exception:
        logger.exception(f"Failed to give up summary batch {batch_id}")
        session.rollback()
        return 0


def collect_finished(session: Session) -> dict[str, int]:
    """Apply the results of every ended batch that still has submitted requests."""
    batches = (
        session.query(SummaryRequest.batch_id, SummaryRequest.provider_key_id)
        .filter(SummaryRequest.status == "submitted", SummaryRequest.batch_id.is_not(None))
        .distinct()
        .all()
    )
    stats: Counter[str] = Counter(dead_claims=requeue_dead_claims(session))
    for batch_id, key_id in batches:
        # One unreadable batch or key (deleted, expired results, API blip) must
        # not starve the others
        try:
            stats += collect_from_key(session, batch_id, key_id)
        except Exception as e:
            logger.exception(f"Failed to collect summary batch {batch_id}")
            session.rollback()
            stats["batch_errors"] += 1
            # A failure that persists would hold the key's slot for good
            if not isinstance(e, TRANSIENT_ERRORS):
                stats["abandoned_batch"] += give_up_if_stale(session, batch_id, key_id)
    return dict(+stats)
