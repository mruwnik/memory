"""Catch-up poll for meeting transcripts from external providers.

Fireflies accounts get meetings by webhook (``ingest_transcript_meeting``),
but Fireflies only sends webhooks for meetings the account holder organizes.
The daily sync walks the last ``SYNC_LOOKBACK_DAYS`` newest-first and
dispatches process_meeting for any transcript not yet stored, which covers
other people's meetings and missed webhooks. The manual full rescan walks
``TRANSCRIPTS_RESCAN_LOOKBACK_DAYS`` the same way.

Stubs whose ``external_id`` already exists in the DB are skipped before the
heavyweight ``get_transcript`` fetch, so a steady-state walk is mostly
list-page calls.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from memory.common import settings
from memory.common.celery_app import (
    INGEST_TRANSCRIPT_MEETING,
    PROCESS_MEETING,
    RESCAN_TRANSCRIPT_ACCOUNT,
    SYNC_ALL_TRANSCRIPT_ACCOUNTS,
    SYNC_TRANSCRIPT_ACCOUNT,
    app,
)
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import TranscriptAccount
from memory.common.db.models.source_items import Meeting
from memory.common.content_processing import safe_task_execution
from memory.common.transcripts import POLLING_PROVIDERS
from memory.parsers.fireflies import (
    DEFAULT_PAGE_LIMIT,
    FirefliesClient,
    FirefliesError,
    build_meeting_kwargs,
)

logger = logging.getLogger(__name__)


# Long enough that a worker outage of a week or a transcript Fireflies
# finishes days late is still caught by the daily sync.
SYNC_LOOKBACK_DAYS = 14

# Circuit breaker for one walk. With page=50 this allows up to 5000
# transcripts per run, far more than any user accumulates in the lookback.
MAX_PAGES = 100


def known_external_ids(db: DBSession, stub_ids: list[str]) -> set[str]:
    """Return the subset of stub_ids already present as Meeting.external_id.

    Scoped *globally*, not per-account, to match the global partial unique
    index on ``meeting.external_id`` (see source_items.py
    ``meeting_external_id_idx``) and ``process_meeting``'s idempotency check
    which is also global. If two accounts polling the same Fireflies
    workspace see the same transcript, the first dispatch wins; subsequent
    polls from any account skip it here, and even if they didn't,
    ``process_meeting`` would short-circuit on the global match.

    Used to skip wasted ``get_transcript`` fetches in ``dispatch_unseen``.
    """
    if not stub_ids:
        return set()
    rows = (
        db.query(Meeting.external_id)
        .filter(Meeting.external_id.in_(stub_ids))
        .all()
    )
    return {row[0] for row in rows if row[0]}


def dispatch_transcript(
    transcript: dict,
    account: TranscriptAccount,
    replace: bool = False,
) -> str | None:
    """Dispatch a single fetched transcript to process_meeting.

    Returns the Celery task id, or None if dispatch was skipped (no
    transcript text after formatting).
    """
    kwargs = build_meeting_kwargs(transcript)
    if not kwargs.get("transcript"):
        logger.info(
            f"Skipping transcript {kwargs.get('external_id')} — no usable text"
        )
        return None

    kwargs["transcript_account_id"] = account.id
    kwargs["replace"] = replace
    if account.tags:
        kwargs["tags"] = list(account.tags)

    result = app.send_task(PROCESS_MEETING, kwargs=kwargs)
    return result.id


def walk_window(
    client: FirefliesClient,
    from_date: datetime,
    max_pages: int = MAX_PAGES,
) -> list[dict]:
    """Return every transcript stub in [from_date, now], oldest-first,
    paginating with skip up to `max_pages`."""
    pages: list[list[dict]] = []
    for page_idx in range(max_pages):
        page = client.list_transcripts(
            from_date=from_date,
            skip=page_idx * DEFAULT_PAGE_LIMIT,
            limit=DEFAULT_PAGE_LIMIT,
        )
        pages.append(page)
        if len(page) < DEFAULT_PAGE_LIMIT:
            break
    else:
        logger.warning(
            f"walk_window hit max_pages={max_pages} (from_date "
            f"{from_date.isoformat()}); some older entries may need a wider rescan."
        )

    # Each page is newest-first; pages[0] is newest. Concat reversed so the
    # final list is oldest-first overall.
    return [stub for page in reversed(pages) for stub in reversed(page)]


def dispatch_unseen(
    stubs: list[dict],
    client: FirefliesClient,
    account: TranscriptAccount,
    db: DBSession,
) -> int:
    """Pre-filter against existing external_ids, then fetch + dispatch each
    unseen stub. Avoids wasted `get_transcript` calls on items we already
    have. Returns count of successful dispatches.
    """
    stub_ids = [s["id"] for s in stubs if s.get("id")]
    already = known_external_ids(db, stub_ids)
    dispatched = 0

    for stub in stubs:
        sid = stub.get("id")
        if not sid or sid in already:
            continue
        full = client.get_transcript(sid)
        if not full:
            continue
        if dispatch_transcript(full, account):
            dispatched += 1
    return dispatched


def fireflies_walk(account: TranscriptAccount, db: DBSession, *, floor: datetime) -> int:
    """Fetch every stub in the [floor, now] window, then dispatch the unseen ones."""
    client = FirefliesClient(account.api_key)
    stubs = walk_window(client, from_date=floor)
    logger.info(f"Fireflies walk (floor {floor.isoformat()}): {len(stubs)} stubs")
    return dispatch_unseen(stubs, client, account, db)


# Provider dispatch — one walk function per provider. Both task wrappers
# (sync / rescan) use the same table; only the floor differs.
PROVIDERS: dict[str, Callable[..., int]] = {
    "fireflies": fireflies_walk,
}

# The api lists polling providers from memory.common.transcripts (it
# can't import this module without dragging in workers.tasks.* deps like
# caldav). Assert the two stay in sync — drift would let the api accept a
# provider name the worker can't dispatch (every sync would then fail
# with "unsupported provider").
assert set(PROVIDERS) == set(POLLING_PROVIDERS), (
    f"PROVIDERS keys {sorted(PROVIDERS)} != "
    f"common.transcripts.POLLING_PROVIDERS {POLLING_PROVIDERS}"
)


def run_walk(account_id: int, *, lookback_days: int, label: str) -> dict:
    """Shared shell for the sync and rescan tasks. Loads the account,
    walks the provider back `lookback_days`, persists sync_error on
    FirefliesError, propagates retryable errors so Celery's autoretry_for
    picks them up.
    """
    with make_session() as db:
        account = db.get(TranscriptAccount, account_id)
        if account is None or not account.active:
            return {"status": "error", "error": "account not found or inactive"}

        walk = PROVIDERS.get(account.provider)
        if walk is None:
            msg = f"unsupported provider: {account.provider}"
            logger.error(msg)
            account.record_sync(msg)
            db.commit()
            return {"status": "error", "error": msg}

        try:
            floor = datetime.now(timezone.utc) - timedelta(days=lookback_days)
            dispatched = walk(account, db, floor=floor)
            account.last_sync_at = datetime.now(timezone.utc)
            account.record_sync(None)
            db.commit()
        except FirefliesError as exc:
            logger.exception(f"{label} failed for account {account_id}")
            account.record_sync(exc)
            db.commit()
            if exc.retryable:
                raise
            return {"status": "error", "error": str(exc)}
        except Exception as exc:
            logger.exception(f"{label} failed for account {account_id}")
            db.rollback()
            account.record_sync(exc)
            db.commit()
            raise

        return {
            "status": "completed",
            "account_id": account_id,
            "provider": account.provider,
            "dispatched": dispatched,
        }


@app.task(
    name=SYNC_TRANSCRIPT_ACCOUNT,
    autoretry_for=(FirefliesError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def sync_transcript_account(account_id: int) -> dict:
    """Daily catch-up: dispatch any transcript from the last SYNC_LOOKBACK_DAYS not yet stored."""
    logger.info(f"Syncing transcript account {account_id}")
    return run_walk(account_id, lookback_days=SYNC_LOOKBACK_DAYS, label="Sync")


@app.task(
    name=RESCAN_TRANSCRIPT_ACCOUNT,
    autoretry_for=(FirefliesError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def rescan_transcript_account(account_id: int) -> dict:
    """Manual deep rescan over TRANSCRIPTS_RESCAN_LOOKBACK_DAYS."""
    logger.info(f"Full-rescanning transcript account {account_id}")
    return run_walk(
        account_id,
        lookback_days=settings.TRANSCRIPTS_RESCAN_LOOKBACK_DAYS,
        label="Full rescan",
    )


def polled_accounts(db: DBSession) -> list[TranscriptAccount]:
    return (
        db.query(TranscriptAccount)
        .filter(
            TranscriptAccount.active,
            TranscriptAccount.provider.in_(POLLING_PROVIDERS),
        )
        .all()
    )


@app.task(
    name=INGEST_TRANSCRIPT_MEETING,
    autoretry_for=(FirefliesError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def ingest_transcript_meeting(account_id: int, meeting_id: str, reprocessed: bool = False) -> dict:
    """Fetch and dispatch one meeting a provider pushed a notification for.

    A ``reprocessed`` notification re-fetches a meeting already stored and
    replaces its transcript.
    """
    with make_session() as db:
        account = db.get(TranscriptAccount, account_id)
        if account is None or not account.active or account.provider != "fireflies":
            return {"status": "error", "error": "account not found, inactive or not fireflies"}
        if not reprocessed and known_external_ids(db, [meeting_id]):
            return {"status": "exists", "meeting_id": meeting_id}

        try:
            transcript = FirefliesClient(account.api_key).get_transcript(meeting_id)
        except FirefliesError as exc:
            logger.exception(f"push fetch failed for account {account_id}")
            account.record_sync(exc)
            db.commit()
            if exc.retryable:
                raise
            return {"status": "error", "error": str(exc)}
        account.record_sync(None)
        db.commit()
        if not transcript:
            return {"status": "error", "error": "transcript not found"}
        task_id = dispatch_transcript(transcript, account, replace=reprocessed)
        return {"status": "queued" if task_id else "empty", "task_id": task_id}


@app.task(name=SYNC_ALL_TRANSCRIPT_ACCOUNTS)
@safe_task_execution
def sync_all_transcript_accounts() -> list[dict]:
    """Dispatch a sync for every active polling TranscriptAccount."""
    with make_session() as db:
        accounts = polled_accounts(db)
        return [
            {
                "account_id": account.id,
                "provider": account.provider,
                "task_id": sync_transcript_account.delay(account.id).id,  # type: ignore[attr-defined]
            }
            for account in accounts
        ]
