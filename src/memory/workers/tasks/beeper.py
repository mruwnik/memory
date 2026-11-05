"""Celery tasks for Beeper: account sync, per-chat backfill and reconcile, and live events.

Every task here calls a Beeper Server, so the workers running them must be on
its network (``beepernet`` in docker-compose).
"""

import asyncio
import logging
import random
from typing import Any

from memory.beeper import sync
from memory.common import settings
from memory.common.beeper import BeeperError
from memory.common.celery_app import (
    HANDLE_BEEPER_EVENT,
    SYNC_ALL_BEEPER_ACCOUNTS,
    SYNC_BEEPER_ACCOUNT,
    SYNC_BEEPER_CHAT,
    app,
)
from memory.common.db.connection import make_session
from memory.common.db.models import BeeperAccount
from memory.common.jobs import tracked_task
from memory.common.redis_lock import distributed_lock

logger = logging.getLogger(__name__)

# Longer than a run's budget takes; a crashed run's lock lapses for the next.
CHAT_LOCK_TTL_SECONDS = 60 * 60
# Spreads an account's chat reads, so a reconcile doesn't hit Server all at once.
CHAT_JITTER_SECONDS = 60
CONTINUE_SECONDS = 5
# A run that stopped on empty pages waits for Server to fetch older history.
STALLED_SECONDS = 5 * 60


def failed(error: BeeperError, **ids: Any) -> dict[str, Any]:
    logger.warning("Beeper Server call failed (%s, %s) for %s", error.status, error.code, ids)
    return {"status": "error", "error": error.code, "http_status": error.status, **ids}


def start_chat_sync(chat_pk: int, countdown: int = 0) -> None:
    app.send_task(SYNC_BEEPER_CHAT, args=[chat_pk], countdown=countdown)


@app.task(name=SYNC_ALL_BEEPER_ACCOUNTS)
@tracked_task
def sync_all_beeper_accounts() -> dict[str, Any]:
    """The scheduled reconcile: sync every active, verified, signed-in account."""
    with make_session() as session:
        accounts = (
            session.query(BeeperAccount)
            .filter_by(is_active=True, verified=True)
            .filter(BeeperAccount.token_encrypted.is_not(None))
            .order_by(BeeperAccount.id)
            .all()
        )
        ids = [account.id for account in accounts]
    for account_id in ids:
        app.send_task(SYNC_BEEPER_ACCOUNT, args=[account_id])
    return {"status": "ok", "scheduled": len(ids)}


@app.task(name=SYNC_BEEPER_ACCOUNT)
@tracked_task
def sync_beeper_account(account_id: int) -> dict[str, Any]:
    """Refresh an account's networks and chats, then read each collected chat."""
    try:
        result = asyncio.run(sync.sync_account(account_id))
    except BeeperError as e:
        return failed(e, account_id=account_id)
    for chat_pk in result.get("collected", []):
        start_chat_sync(chat_pk, random.randint(0, CHAT_JITTER_SECONDS))
    return result


@app.task(name=SYNC_BEEPER_CHAT)
@tracked_task
def sync_beeper_chat(chat_id: int) -> dict[str, Any]:
    """Read a chat's new messages and backfill its history, a budget at a time;
    a run that doesn't finish queues the next."""
    with distributed_lock(f"memory:lock:beeper-chat:{chat_id}", CHAT_LOCK_TTL_SECONDS) as lock:
        if lock is None:
            return {"status": "locked", "chat_id": chat_id}
        try:
            result = asyncio.run(sync.sync_chat(chat_id, settings.BEEPER_SYNC_MAX_MESSAGES_PER_RUN))
        except BeeperError as e:
            return failed(e, chat_id=chat_id)
    if result["status"] == "ok" and not result["done"]:
        stalled = result["processed"] == 0
        start_chat_sync(chat_id, STALLED_SECONDS if stalled else CONTINUE_SECONDS)
    return result


@app.task(name=HANDLE_BEEPER_EVENT)
@tracked_task
def handle_beeper_event(account_id: int, event: dict[str, Any]) -> dict[str, Any]:
    """Apply one event from an account's WebSocket. Idempotent: the reconcile
    and repeated events store the same result."""
    try:
        result = asyncio.run(sync.handle_event(account_id, event))
    except BeeperError as e:
        return failed(e, account_id=account_id)
    if chat_pk := result.get("start_sync"):
        start_chat_sync(chat_pk)
    return result
