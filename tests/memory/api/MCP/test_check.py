"""Tests for the MCP check subserver."""
# pyright: reportFunctionMemberAccess=false

from datetime import datetime, timedelta

import fakeredis.aioredis as fakeaioredis
import pytest

from memory.api.MCP.servers.check import ask, delete, list_jobs, wait_for_answer
from memory.common.check import store
from memory.common.check.schemas import SubmitRequest
from memory.common.db.models import HumanUser, UserSession
from tests.conftest import mcp_auth_context

pytestmark = pytest.mark.asyncio


@pytest.fixture
def fake_redis(monkeypatch):
    """Shared fakeredis for all check tool calls in one test."""
    client = fakeaioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(
        "memory.api.MCP.servers.check.get_check_redis", lambda: client
    )
    return client


def make_user_session(db_session, email: str, scopes: list[str]):
    user = HumanUser(
        name=email,
        email=email,
        password_hash="bcrypt_hash_placeholder",
        scopes=scopes,
    )
    db_session.add(user)
    db_session.commit()
    session = UserSession(
        id=f"session-{email}",
        user_id=user.id,
        expires_at=datetime.now() + timedelta(days=1),
    )
    db_session.add(session)
    db_session.commit()
    return user, session


@pytest.fixture
def check_user(db_session):
    return make_user_session(db_session, "checker@example.com", ["check"])


@pytest.fixture
def other_user(db_session):
    return make_user_session(db_session, "other@example.com", ["check"])


async def test_ask_returns_job(db_session, fake_redis, check_user):
    user, session = check_user
    with mcp_auth_context(session.id):
        result = await ask.fn(text="is the sky blue?")
    assert result["job_id"].startswith("chk_")
    assert result["status"] == "queued"


async def test_ask_oversized_raises_clean_error(db_session, fake_redis, check_user):
    user, session = check_user
    big = "a" * (64 * 1024 + 1)
    with mcp_auth_context(session.id):
        with pytest.raises(ValueError, match="exceeds"):
            await ask.fn(text=big)


async def test_wait_for_answer_returns_result(db_session, fake_redis, check_user):
    user, session = check_user
    job_id = await store.submit_job(
        fake_redis, user_id=user.id, req=SubmitRequest(text="claim me")
    )
    nxt = await store.claim_next(fake_redis, user_id=user.id, wait=0)
    assert nxt is not None
    await store.complete_job(
        fake_redis,
        job_id,
        nxt.lease_id,
        status="ok",
        result={"answer": "yes"},
        error=None,
    )
    with mcp_auth_context(session.id):
        result = await wait_for_answer.fn(job_id, seconds=0)
    assert result["status"] == "ok"
    assert result["result"] == {"answer": "yes"}


async def test_wait_for_answer_other_user_denied(
    db_session, fake_redis, check_user, other_user
):
    owner, _ = other_user
    user, session = check_user
    job_id = await store.submit_job(
        fake_redis, user_id=owner.id, req=SubmitRequest(text="not yours")
    )
    with mcp_auth_context(session.id):
        with pytest.raises(ValueError, match="unknown job"):
            await wait_for_answer.fn(job_id, seconds=0)


async def test_list_jobs_returns_own(db_session, fake_redis, check_user):
    user, session = check_user
    with mcp_auth_context(session.id):
        await ask.fn(text="one")
        await ask.fn(text="two")
        result = await list_jobs.fn()
    assert len(result["jobs"]) == 2


async def test_delete_removes(db_session, fake_redis, check_user):
    user, session = check_user
    with mcp_auth_context(session.id):
        submitted = await ask.fn(text="delete me")
        job_id = submitted["job_id"]
        result = await delete.fn(job_id)
    assert result == {"deleted": True, "job_id": job_id}
    assert await store.get_job(fake_redis, job_id) is None


async def test_delete_other_user_denied(
    db_session, fake_redis, check_user, other_user
):
    owner, _ = other_user
    user, session = check_user
    job_id = await store.submit_job(
        fake_redis, user_id=owner.id, req=SubmitRequest(text="not yours")
    )
    with mcp_auth_context(session.id):
        with pytest.raises(ValueError, match="unknown job"):
            await delete.fn(job_id)
