"""Beeper WebSocket collector: one ``/v1/ws`` connection per active, verified account.

Runs in ingest-hub (``python -m memory.beeper.collector``). Message and chat
events go to Celery. The socket delivers at most once with no replay, so a
(re)connect queues a sync of the account, which re-reads its collected chats,
unless one ran or was queued within RECONNECT_SYNC_AFTER_SECONDS: a flapping
socket would otherwise flood the workers.
"""

import asyncio
import contextlib
import json
import logging
import signal
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any

import redis
from celery import Celery
from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidStatus, WebSocketException

from memory.beeper import state
from memory.beeper.access import account_access
from memory.common import beeper
from memory.common.celery_app import HANDLE_BEEPER_EVENT, SYNC_BEEPER_ACCOUNT
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import make_session
from memory.common.db.models import BeeperAccount

logger = logging.getLogger(__name__)

# How often the manager picks up accounts added, paused, signed out or unverified.
SYNC_INTERVAL_SECONDS = 60
MAX_RETRY_SECONDS = 5 * 60
# A connection that lasted this long resets the backoff.
STABLE_SECONDS = 60
RECONNECT_SYNC_AFTER_SECONDS = 5 * 60
MAX_MESSAGE_BYTES = 16 * 1024 * 1024
QUEUED_EVENTS = frozenset({"chat.upserted", "message.upserted", "message.deleted"})
SUBSCRIBE = {"type": "subscriptions.set", "chatIDs": ["*"], "app": {"state": True}}

retry_sleep = asyncio.sleep


@dataclass(frozen=True)
class AccountInfo:
    """What a collector runs with, detached from the session; a change restarts it."""

    id: int
    server_url: str
    token: str


def collected_accounts() -> list[AccountInfo]:
    with make_session() as session:
        accounts = session.query(BeeperAccount).filter_by(is_active=True, verified=True).order_by(BeeperAccount.id)
        return [
            AccountInfo(access.account_id, access.server_url, access.token)
            for account in accounts
            if (access := account_access(account))
        ]


def last_reconcile_at(account_id: int) -> datetime | None:
    with make_session() as session:
        account = session.get(BeeperAccount, account_id)
        return account.last_reconcile_at if account else None


def socket_url(server_url: str) -> str:
    return "ws" + beeper.origin(server_url).removeprefix("http") + "/v1/ws"


def record_app_state(account_id: int, state: dict[str, Any]) -> None:
    """Keep the account's setup state as its Server reports it, so a Server
    that loses its sign-in or verification shows up."""
    try:
        snapshot = beeper.SetupSession.model_validate(state)
    except ValidationError:
        logger.warning("Beeper account %s: unreadable app state", account_id)
        return
    with make_session() as session:
        account = session.get(BeeperAccount, account_id)
        if account is None:
            return
        if snapshot.matrix and account.beeper_user_id and snapshot.matrix.user_id != account.beeper_user_id:
            # Unverified, the account's events are skipped and the manager drops its socket.
            logger.warning("Beeper account %s: Server serves a different Beeper user; collection stopped", account_id)
            account.verified = False
            session.commit()
            return
        account.setup_state = snapshot.state
        account.verified = snapshot.e2ee.verified
        session.commit()


async def record_connected(account_id: int, connected: bool) -> None:
    """Share the socket's state; Redis being down mustn't take the collector with it."""
    try:
        await asyncio.to_thread(state.set_connected, account_id, connected)
    except redis.RedisError:
        logger.warning("Beeper account %s: could not record the WebSocket state", account_id)


class no_redirects(connect):
    """A redirect could lead off the allowlist with the token."""

    def process_redirect(self, exc: Exception) -> Exception:
        return exc


class AccountCollector:
    def __init__(self, info: AccountInfo, celery: Celery = celery_app):
        self.info = info
        self.celery = celery
        self.connected = False
        self.sync_queued_at: float | None = None

    async def run(self) -> None:
        """Stay connected until cancelled, backing off exponentially while
        connections fail or drop quickly."""
        failures = 0
        while True:
            started = monotonic()
            try:
                await self.connect_once()
            except InvalidStatus as e:
                logger.warning(
                    "Beeper account %s: Server refused the WebSocket (%s)", self.info.id, e.response.status_code
                )
            except (OSError, WebSocketException, ValueError) as e:
                logger.info("Beeper account %s: WebSocket closed (%s)", self.info.id, type(e).__name__)
            except Exception:
                logger.exception("Beeper account %s: WebSocket failed", self.info.id)
            finally:
                self.connected = False
                await record_connected(self.info.id, False)
            failures = 0 if monotonic() - started >= STABLE_SECONDS else failures + 1
            await retry_sleep(min(2 ** max(failures - 1, 0), MAX_RETRY_SECONDS))

    async def connect_once(self) -> None:
        if not beeper.server_url_allowed(self.info.server_url):
            raise ValueError("server is not on the Beeper allowlist")
        async with no_redirects(
            socket_url(self.info.server_url),
            additional_headers={"Authorization": f"Bearer {self.info.token}"},
            proxy=None,
            max_size=MAX_MESSAGE_BYTES,
            open_timeout=30,
        ) as ws:
            async for raw in ws:
                await self.handle(ws, json.loads(raw))

    async def handle(self, ws: ClientConnection, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "ready":
            await ws.send(json.dumps(SUBSCRIBE))
        elif kind == "subscriptions.updated":
            self.connected = True
            await record_connected(self.info.id, True)
            logger.info("Beeper account %s: WebSocket subscribed", self.info.id)
            if await self.reconcile_due():
                self.sync_queued_at = monotonic()
                self.celery.send_task(SYNC_BEEPER_ACCOUNT, args=[self.info.id])
        elif kind == "app.state.updated":
            await asyncio.to_thread(record_app_state, self.info.id, message.get("appState") or {})
        elif kind in QUEUED_EVENTS:
            self.celery.send_task(HANDLE_BEEPER_EVENT, kwargs={"account_id": self.info.id, "event": message})
        elif kind == "error":
            logger.warning("Beeper account %s: WebSocket error %s", self.info.id, message.get("code"))


    async def reconcile_due(self) -> bool:
        if self.sync_queued_at is not None and monotonic() - self.sync_queued_at < RECONNECT_SYNC_AFTER_SECONDS:
            return False
        last = await asyncio.to_thread(last_reconcile_at, self.info.id)
        return last is None or datetime.now(timezone.utc) - last >= timedelta(seconds=RECONNECT_SYNC_AFTER_SECONDS)


class CollectorManager:
    """Runs one collector per account that should be connected."""

    def __init__(self, celery: Celery = celery_app):
        self.celery = celery
        self.collectors: dict[int, AccountCollector] = {}
        self.tasks: dict[int, asyncio.Task] = {}

    async def sync(self) -> None:
        """Run exactly the accounts that should be connected, restarting any
        whose URL or token changed or whose collector died."""
        wanted = {info.id: info for info in await asyncio.to_thread(collected_accounts)}
        for account_id, running in list(self.collectors.items()):
            if wanted.get(account_id) != running.info or self.tasks[account_id].done():
                await self.stop(account_id)
        for account_id, info in wanted.items():
            if account_id not in self.collectors:
                self.start(info)

    async def keep_synced(self) -> None:
        while True:
            await asyncio.sleep(SYNC_INTERVAL_SECONDS)
            try:
                await self.sync()
                for collector in self.collectors.values():
                    if collector.connected:
                        await record_connected(collector.info.id, True)
            except Exception:
                logger.exception("Beeper collector sync failed")

    def start(self, info: AccountInfo) -> None:
        collector = AccountCollector(info, self.celery)
        self.collectors[info.id] = collector
        self.tasks[info.id] = asyncio.create_task(collector.run())
        logger.info("Beeper account %s: collector started", info.id)

    async def stop(self, account_id: int) -> None:
        self.collectors.pop(account_id, None)
        task = self.tasks.pop(account_id, None)
        if task is None:
            return
        task.cancel()
        # A collector that died has its exception logged here, not lost.
        [outcome] = await asyncio.gather(task, return_exceptions=True)
        if isinstance(outcome, Exception):
            logger.error("Beeper account %s: collector died (%s)", account_id, type(outcome).__name__)
        logger.info("Beeper account %s: collector stopped", account_id)

    async def stop_all(self) -> None:
        for account_id in list(self.collectors):
            await self.stop(account_id)


async def main() -> None:
    manager = CollectorManager()
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    await manager.sync()
    syncing = asyncio.create_task(manager.keep_synced())
    logger.info("Beeper collector started")
    await stopping.wait()
    syncing.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await syncing
    await manager.stop_all()
    logger.info("Beeper collector stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
