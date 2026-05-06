"""Periodic poll for meeting transcripts from external providers.

Iterates active TranscriptAccount rows, fetches new transcripts via the
provider's API, and dispatches process_meeting for each. Provider-agnostic
shell — provider-specific logic lives in dedicated parsers
(e.g. memory.parsers.fireflies).

Sync model: a single "walk newest-first from now down to a floor" primitive
serves both jobs. Stubs whose ``external_id`` already exists in the DB are
skipped before the heavyweight ``get_transcript`` fetch (saves ~N wasted
API calls per page on the steady-state where most stubs are already known).

  - **Quick sync** (every 2h, cheap): floor = MIN(meeting_date for this
    account) or now-N days if the DB is empty. Capped at a small number of
    pages. Catches recent additions.

  - **Full rescan** (weekly, bulletproof): floor = now - LOOKBACK_DAYS,
    capped high enough to walk the whole window. Catches drift the cheap
    sync may have missed (>page-size bursts, late-arriving uploads, etc.).

The floor is computed from the Meeting table on every run rather than
stored on the account, so a crash mid-sync leaves no inconsistency.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from memory.common import settings
from memory.common.celery_app import (
    PROCESS_MEETING,
    RESCAN_ALL_TRANSCRIPT_ACCOUNTS,
    RESCAN_TRANSCRIPT_ACCOUNT,
    SYNC_ALL_TRANSCRIPT_ACCOUNTS,
    SYNC_TRANSCRIPT_ACCOUNT,
    app,
)
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import TranscriptAccount
from memory.common.db.models.source_items import Meeting
from memory.common.content_processing import safe_task_execution
from memory.parsers.fireflies import (
    DEFAULT_PAGE_LIMIT,
    FirefliesClient,
    FirefliesError,
    build_meeting_kwargs,
)

logger = logging.getLogger(__name__)


# Quick sync: bootstrap window when DB is empty. Quick is single-page —
# trusts that the workload is small enough for one page to cover it; the
# weekly full rescan catches anything that overflowed.
QUICK_BOOTSTRAP_LOOKBACK_DAYS = 7

# Full rescan page cap. With page=50 this allows up to 5000 transcripts per
# run — far more than any user accumulates in the lookback window.
RESCAN_MAX_PAGES = 100


def meeting_min_date(db: DBSession, account_id: int) -> datetime | None:
    """Return MIN(meeting_date) of Meetings synced for this account, or None."""
    return (
        db.query(func.min(Meeting.meeting_date))
        .filter(Meeting.transcript_account_id == account_id)
        .scalar()
    )


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
    if account.tags:
        kwargs["tags"] = list(account.tags)

    result = app.send_task(PROCESS_MEETING, kwargs=kwargs)
    return result.id


def walk_window(
    client: FirefliesClient,
    from_date: datetime,
    max_pages: int = 1,
) -> list[dict]:
    """Return every transcript stub in [from_date, now], paginating with skip.

    Oldest-first ordering. Bounded by `max_pages` as a circuit breaker.
    `max_pages=1` is the quick-sync case (single page); higher values are
    used by the weekly rescan where the window may exceed one page.
    """
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


def fireflies_walk(
    account: TranscriptAccount,
    db: DBSession,
    *,
    floor: datetime,
    max_pages: int = 1,
) -> int:
    """Walk-then-dispatch: fetch every stub in the [floor, now] window
    (paginated up to `max_pages`), then dispatch the unseen ones.

    Default `max_pages=1` matches the quick-sync path; the weekly rescan
    explicitly passes RESCAN_MAX_PAGES.
    """
    client = FirefliesClient(account.api_key)
    stubs = walk_window(client, from_date=floor, max_pages=max_pages)
    logger.info(
        f"Fireflies walk (floor {floor.isoformat()}, max_pages={max_pages}): "
        f"{len(stubs)} stubs"
    )
    return dispatch_unseen(stubs, client, account, db)


# Provider dispatch — one walk function per provider. Both task wrappers
# (quick / full) use the same table; only the floor + max_pages differ.
PROVIDERS: dict[str, Callable[..., int]] = {
    "fireflies": fireflies_walk,
}


def quick_floor(account: TranscriptAccount, db: DBSession) -> datetime:
    """Quick-sync floor: ``max(min_date, now - QUICK_BOOTSTRAP_LOOKBACK_DAYS)``.

    The floor is the *more recent* of those two values:

    - When min_date is recent (DB has a meeting newer than the bootstrap
      window), we walk back only as far as that meeting; the prefilter
      handles overlap so we don't re-fetch known transcripts.

    - When min_date is older than the bootstrap window (or the DB is
      empty), we cap lookback at the bootstrap window. Older drift is the
      weekly rescan's job — quick sync intentionally never walks further
      back than ``QUICK_BOOTSTRAP_LOOKBACK_DAYS`` regardless of how old the
      DB is.
    """
    bootstrap = datetime.now(timezone.utc) - timedelta(
        days=QUICK_BOOTSTRAP_LOOKBACK_DAYS
    )
    min_date = meeting_min_date(db, account.id)
    if min_date is None:
        return bootstrap
    return max(min_date, bootstrap)


def full_floor(account: TranscriptAccount, db: DBSession) -> datetime:
    """Walk back the configured rescan lookback, regardless of min_date.

    Both parameters are ignored; the signature is kept symmetric with
    :func:`quick_floor` so both can be passed as ``floor_fn`` to
    :func:`run_walk` interchangeably (``Callable[[TranscriptAccount,
    DBSession], datetime]``).
    """
    del account, db  # unused; signature is kept symmetric with quick_floor.
    return datetime.now(timezone.utc) - timedelta(
        days=settings.TRANSCRIPTS_RESCAN_LOOKBACK_DAYS
    )


def run_walk(
    account_id: int,
    *,
    floor_fn: Callable[[TranscriptAccount, DBSession], datetime],
    max_pages: int,
    label: str,
) -> dict:
    """Shared shell for quick and full sync tasks. Loads the account,
    dispatches to the provider walk with the right floor and page cap,
    persists sync_error on FirefliesError, propagates retryable errors so
    Celery's autoretry_for picks them up.
    """
    with make_session() as db:
        account = db.get(TranscriptAccount, account_id)
        if account is None or not account.active:
            return {"status": "error", "error": "account not found or inactive"}

        walk = PROVIDERS.get(account.provider)
        if walk is None:
            msg = f"unsupported provider: {account.provider}"
            logger.error(msg)
            account.sync_error = msg
            db.commit()
            return {"status": "error", "error": msg}

        try:
            floor = floor_fn(account, db)
            dispatched = walk(account, db, floor=floor, max_pages=max_pages)
            account.last_sync_at = datetime.now(timezone.utc)
            account.sync_error = None
            db.commit()
        except FirefliesError as exc:
            logger.exception(f"{label} failed for account {account_id}")
            account.sync_error = str(exc)
            db.commit()
            if exc.retryable:
                raise
            return {"status": "error", "error": str(exc)}

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
    """Cheap quick sync (every TRANSCRIPTS_SYNC_INTERVAL).

    Walks back to the account's MIN(meeting_date) (or the bootstrap window
    if the DB is empty), capped at QUICK_MAX_PAGES. Pre-filters known
    external_ids so steady-state polls do almost no get_transcript calls.
    """
    logger.info(f"Quick-syncing transcript account {account_id}")
    return run_walk(
        account_id,
        floor_fn=quick_floor,
        max_pages=1,
        label="Quick sync",
    )


@app.task(
    name=RESCAN_TRANSCRIPT_ACCOUNT,
    autoretry_for=(FirefliesError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def rescan_transcript_account(account_id: int) -> dict:
    """Periodic deep rescan (weekly).

    Walks the full TRANSCRIPTS_RESCAN_LOOKBACK_DAYS window. Same prefilter
    applies, so a steady-state weekly rescan with no missed items is mostly
    list-page calls + DB queries — no wasted get_transcript fetches.
    """
    logger.info(f"Full-rescanning transcript account {account_id}")
    return run_walk(
        account_id,
        floor_fn=full_floor,
        max_pages=RESCAN_MAX_PAGES,
        label="Full rescan",
    )


@app.task(name=SYNC_ALL_TRANSCRIPT_ACCOUNTS)
@safe_task_execution
def sync_all_transcript_accounts() -> list[dict]:
    """Dispatch a quick sync for every active TranscriptAccount."""
    with make_session() as db:
        accounts = (
            db.query(TranscriptAccount).filter(TranscriptAccount.active).all()
        )
        return [
            {
                "account_id": account.id,
                "provider": account.provider,
                "task_id": sync_transcript_account.delay(account.id).id,  # type: ignore[attr-defined]
            }
            for account in accounts
        ]


@app.task(name=RESCAN_ALL_TRANSCRIPT_ACCOUNTS)
@safe_task_execution
def rescan_all_transcript_accounts() -> list[dict]:
    """Dispatch a full rescan for every active TranscriptAccount."""
    with make_session() as db:
        accounts = (
            db.query(TranscriptAccount).filter(TranscriptAccount.active).all()
        )
        return [
            {
                "account_id": account.id,
                "provider": account.provider,
                "task_id": rescan_transcript_account.delay(account.id).id,  # type: ignore[attr-defined]
            }
            for account in accounts
        ]
