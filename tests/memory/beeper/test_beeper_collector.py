"""The Beeper WebSocket collector, against a local WebSocket server shaped like Beeper Server's."""

import asyncio
import contextlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from memory.beeper import collector, state
from memory.beeper.collector import AccountCollector, AccountInfo, CollectorManager
from memory.common import settings
from memory.common.celery_app import HANDLE_BEEPER_EVENT, SYNC_BEEPER_ACCOUNT
from memory.common.db.models import BeeperAccount

TOKEN = "socket-token"
SUBSCRIBE = {"type": "subscriptions.set", "chatIDs": ["*"], "app": {"state": True}}


def domain_event(kind: str, seq: int) -> dict[str, Any]:
    return {"type": kind, "seq": seq, "ts": "2026-11-05T12:00:00.000Z", "chatID": "!c:beeper.local", "ids": [str(seq)]}


def app_state(state: str, verified: bool, user_id: str = "@me:beeper.com") -> dict[str, Any]:
    return {
        "type": "app.state.updated", "seq": 9, "ts": "2026-11-05T12:00:00.000Z",
        "appState": {
            "state": state,
            "matrix": {"userID": user_id, "deviceID": "DEVICE", "homeserver": "https://matrix.beeper.com/"},
            "e2ee": {"initialized": True, "secretStorage": True, "crossSigning": True, "verified": verified,
                     "keyBackup": True, "firstSyncDone": True, "hasBackedUpRecoveryKey": False,
                     "secrets": {}},
        },
    }


class FakeSocket:
    """Greets with ``ready``, confirms the subscription, then sends ``events``;
    drops the connection after them when ``drop`` is set."""

    def __init__(self):
        self.url = ""
        self.events: list[dict[str, Any]] = []
        self.drop = False
        self.refuse_with: Response | None = None
        self.authorizations: list[str | None] = []
        self.subscriptions: list[dict[str, Any]] = []
        self.closed = asyncio.Event()

    def process_request(self, connection: ServerConnection, request) -> Response | None:
        self.authorizations.append(request.headers.get("Authorization"))
        return self.refuse_with

    async def handler(self, ws: ServerConnection) -> None:
        await ws.send(json.dumps({"type": "ready", "version": 1, "chatIDs": []}))
        self.subscriptions.append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"type": "subscriptions.updated", "chatIDs": ["*"], "app": {"state": True}}))
        for event in self.events:
            await ws.send(json.dumps(event))
        if not self.drop:
            await ws.wait_closed()
            self.closed.set()


@pytest_asyncio.fixture
async def socket_server(monkeypatch):
    fake = FakeSocket()
    async with serve(fake.handler, "127.0.0.1", 0, process_request=fake.process_request) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        fake.url = f"http://127.0.0.1:{port}"
        monkeypatch.setattr(settings, "BEEPER_SERVER_URLS", [fake.url])
        yield fake


@pytest.fixture
def quick_retries(monkeypatch):
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def sleep(seconds: float) -> None:
        delays.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(collector, "retry_sleep", sleep)
    return delays


def add_account(db_session, user, url: str, **fields) -> BeeperAccount:
    values = {"is_active": True, "verified": True, "setup_state": "ready", "beeper_user_id": "@me:beeper.com"}
    account = BeeperAccount(created_by_user_id=user.id, server_url=url, **(values | fields))
    account.token = TOKEN
    db_session.add(account)
    db_session.commit()
    return account


def calls(mock, name: str) -> list:
    return [call.kwargs for call in mock.call_args_list if call.args[0] == name]


async def until(condition, timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_the_collector_subscribes_and_queues_events(
    db_session, regular_user, socket_server, no_celery_dispatch
):
    account = add_account(db_session, regular_user, socket_server.url)
    socket_server.events = [
        domain_event("message.upserted", 1),
        domain_event("chat.deleted", 2),
        domain_event("message.deleted", 3),
        domain_event("chat.upserted", 4),
    ]
    task = asyncio.create_task(AccountCollector(AccountInfo(account.id, socket_server.url, TOKEN)).run())

    await until(lambda: len(calls(no_celery_dispatch, HANDLE_BEEPER_EVENT)) == 3)
    task.cancel()

    assert socket_server.authorizations == [f"Bearer {TOKEN}"]
    assert socket_server.subscriptions == [SUBSCRIBE]
    assert calls(no_celery_dispatch, SYNC_BEEPER_ACCOUNT) == [{"args": [account.id]}]
    assert [c["kwargs"]["event"]["seq"] for c in calls(no_celery_dispatch, HANDLE_BEEPER_EVENT)] == [1, 3, 4]
    assert {c["kwargs"]["account_id"] for c in calls(no_celery_dispatch, HANDLE_BEEPER_EVENT)} == {account.id}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state, verified",
    [("needs-verification", False), ("ready", True)],
)
async def test_app_state_updates_the_account(db_session, regular_user, socket_server, state, verified):
    account = add_account(db_session, regular_user, socket_server.url, setup_state="initializing", verified=True)
    socket_server.events = [app_state(state, verified)]
    task = asyncio.create_task(AccountCollector(AccountInfo(account.id, socket_server.url, TOKEN)).run())

    def updated() -> bool:
        db_session.expire_all()
        return db_session.get(BeeperAccount, account.id).setup_state == state

    await until(updated)
    task.cancel()
    assert db_session.get(BeeperAccount, account.id).verified is verified


@pytest.mark.asyncio
async def test_app_state_of_another_beeper_user_stops_collecting(
    db_session, regular_user, socket_server, no_celery_dispatch
):
    account = add_account(db_session, regular_user, socket_server.url)
    socket_server.events = [app_state("ready", True, user_id="@someone-else:beeper.com")]
    task = asyncio.create_task(AccountCollector(AccountInfo(account.id, socket_server.url, TOKEN)).run())

    def unverified() -> bool:
        db_session.expire_all()
        return not db_session.get(BeeperAccount, account.id).verified

    await until(unverified)
    task.cancel()

    assert db_session.get(BeeperAccount, account.id).setup_state == "ready"
    assert collector.collected_accounts() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("minutes_ago, syncs", [(None, 1), (1, 0), (10, 1)])
async def test_a_reconnect_reconciles_unless_a_reconcile_just_ran(
    db_session, regular_user, socket_server, no_celery_dispatch, quick_retries, minutes_ago, syncs
):
    last = None if minutes_ago is None else datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    account = add_account(db_session, regular_user, socket_server.url, last_reconcile_at=last)
    socket_server.drop = True
    task = asyncio.create_task(AccountCollector(AccountInfo(account.id, socket_server.url, TOKEN)).run())

    await until(lambda: len(socket_server.subscriptions) >= 3)
    task.cancel()

    assert len(calls(no_celery_dispatch, SYNC_BEEPER_ACCOUNT)) == syncs


@pytest.mark.asyncio
async def test_the_connection_state_is_shared(db_session, regular_user, socket_server):
    account = add_account(db_session, regular_user, socket_server.url)
    task = asyncio.create_task(AccountCollector(AccountInfo(account.id, socket_server.url, TOKEN)).run())

    await until(lambda: state.is_connected(account.id))
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert not state.is_connected(account.id)


@pytest.mark.asyncio
async def test_reconnects_back_off_while_the_server_refuses(
    db_session, regular_user, socket_server, quick_retries
):
    account = add_account(db_session, regular_user, socket_server.url)
    socket_server.refuse_with = Response(503, "Service Unavailable", Headers())
    task = asyncio.create_task(AccountCollector(AccountInfo(account.id, socket_server.url, TOKEN)).run())

    await until(lambda: len(quick_retries) >= 12)
    task.cancel()

    assert quick_retries[:3] == [1, 2, 4]
    assert quick_retries == sorted(quick_retries)
    assert max(quick_retries) == collector.MAX_RETRY_SECONDS


@pytest.mark.asyncio
async def test_redirects_are_not_followed(db_session, regular_user, socket_server, quick_retries):
    account = add_account(db_session, regular_user, socket_server.url)
    elsewhere = FakeSocket()
    async with serve(elsewhere.handler, "127.0.0.1", 0, process_request=elsewhere.process_request) as other:
        port = next(iter(other.sockets)).getsockname()[1]
        socket_server.refuse_with = Response(302, "Found", Headers({"Location": f"ws://127.0.0.1:{port}/v1/ws"}))
        task = asyncio.create_task(AccountCollector(AccountInfo(account.id, socket_server.url, TOKEN)).run())

        await until(lambda: len(quick_retries) >= 2)
        task.cancel()

    assert elsewhere.authorizations == []


@pytest.fixture
def idle_collectors(monkeypatch):
    async def run_forever(self):
        await asyncio.Event().wait()

    monkeypatch.setattr(AccountCollector, "run", run_forever)


def running(manager: CollectorManager) -> set[int]:
    return set(manager.collectors)


@pytest.mark.asyncio
async def test_the_manager_follows_added_paused_and_unverified_accounts(
    db_session, regular_user, socket_server, idle_collectors, monkeypatch
):
    monkeypatch.setattr(settings, "BEEPER_SERVER_URLS", [socket_server.url, "http://two:1", "http://three:1"])
    first = add_account(db_session, regular_user, socket_server.url)
    unverified = add_account(db_session, regular_user, "http://two:1", verified=False)
    not_allowed = add_account(db_session, regular_user, "http://off-the-list:1")
    manager = CollectorManager()

    await manager.sync()
    assert running(manager) == {first.id}

    unverified.verified = True
    first.is_active = False
    third = add_account(db_session, regular_user, "http://three:1")
    db_session.commit()
    await manager.sync()
    assert running(manager) == {unverified.id, third.id}

    third.token = "rotated"
    db_session.commit()
    await manager.sync()
    assert manager.collectors[third.id].info.token == "rotated"
    assert not_allowed.id not in running(manager)
    await manager.stop_all()


@pytest.mark.asyncio
async def test_stopping_closes_the_connection(db_session, regular_user, socket_server):
    add_account(db_session, regular_user, socket_server.url)
    manager = CollectorManager()
    await manager.sync()
    await until(lambda: socket_server.subscriptions)

    await manager.stop_all()

    await asyncio.wait_for(socket_server.closed.wait(), 5)
    assert manager.collectors == {}
