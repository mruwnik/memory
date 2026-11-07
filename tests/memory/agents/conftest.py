from dataclasses import replace
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sse_starlette.sse import AppStatus

from memory.agents.prebuilt import FACT_CHECK, PREBUILT_AGENTS
from memory.agents.system import SYSTEM_AGENTS
from memory.common.db.models import HumanUser, LLMProviderKey, UserSession
from memory.common.llms import registry
from memory.common import safe_http
from memory.common.safe_http import NetworkPolicy
from tests.memory.agents.fake_mcp import add_server, run_fake_mcp
from tests.memory.agents.fakes import FakePoor, FakeRich, ScriptedProvider


@pytest.fixture
def regular_user(db_session):
    user = HumanUser(
        name="Regular User",
        email="regular@example.com",
        password_hash="bcrypt_hash_placeholder",
        scopes=["teams"],
    )
    db_session.add(user)
    db_session.commit()
    return user


def work_session(db, user_id: int, token: str) -> UserSession:
    session = UserSession(
        token=token, user_id=user_id, expires_at=datetime.now() + timedelta(days=1)
    )
    db.add(session)
    db.commit()
    return session


@pytest.fixture
def worker_session(db_session, regular_user):
    """The runs' owner, working on them from a worker."""
    regular_user.scopes = [*regular_user.scopes, "agents:work"]
    db_session.commit()
    return work_session(db_session, regular_user.id, "worker-session-token")


def default_keys(db, user) -> dict[str, LLMProviderKey]:
    keys = {}
    for provider in ("anthropic", "openai"):
        key = LLMProviderKey(
            user_id=user.id, provider=provider, label=f"fake-{provider}", is_default=True
        )
        key.key = "fake-key"
        db.add(key)
        keys[provider] = key
    db.commit()
    return keys


@pytest.fixture
def fake_providers(monkeypatch, db_session, regular_user):
    """Fakes in the anthropic and openai slots, and a default key for each so runs can pay."""
    monkeypatch.setitem(registry.PROVIDERS, "anthropic", FakeRich)
    monkeypatch.setitem(registry.PROVIDERS, "openai", FakePoor)
    ScriptedProvider.script = []
    ScriptedProvider.requests = []
    ScriptedProvider.keys = default_keys(db_session, regular_user)
    yield ScriptedProvider


@pytest.fixture
def code_agents(monkeypatch, fake_providers):
    """A prebuilt agent ("echo") and a system agent ("system:echo") on the fake model."""
    monkeypatch.setitem(
        PREBUILT_AGENTS, "echo", replace(FACT_CHECK, slug="echo", tools=(), models=("anthropic/m",))
    )
    summarise = SYSTEM_AGENTS["system:summarise"]
    monkeypatch.setitem(
        SYSTEM_AGENTS, "system:echo", replace(summarise, slug="system:echo", models=("anthropic/m",))
    )


@pytest.fixture
def admin_keys(db_session, admin_user, fake_providers):
    """Default keys for the admin, for tests whose runs belong to them."""
    return default_keys(db_session, admin_user)


@pytest.fixture
def loopback_allowed(monkeypatch):
    """Test servers bind loopback, which the real policy always blocks."""
    policy = NetworkPolicy(loopback_allowed=True)
    monkeypatch.setattr(safe_http, "default_policy", lambda: policy)


@pytest_asyncio.fixture
async def fake_mcp(request, monkeypatch, loopback_allowed):
    """A live MCP server on loopback; parametrize indirectly with (auth, transport)."""
    auth, transport = getattr(request, "param", ("oauth", "http"))
    # sse_starlette keeps one process-wide exit event, bound to the first test's loop.
    monkeypatch.setattr(AppStatus, "should_exit_event", None)
    async with run_fake_mcp(auth, transport) as fake:
        yield fake


@pytest.fixture
def connected_server(db_session, regular_user):
    server = add_server(db_session, regular_user, "https://mcp.example.com/mcp")
    server.status = "connected"
    server.tools = [{"name": "kept", "description": "", "read_only": True}]
    db_session.commit()
    return server
