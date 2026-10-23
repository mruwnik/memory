import pytest
import pytest_asyncio
from sse_starlette.sse import AppStatus

from memory.common.llms import registry
from memory.common import safe_http
from memory.common.safe_http import NetworkPolicy
from tests.memory.agents.fake_mcp import run_fake_mcp
from tests.memory.agents.fakes import FakePoor, FakeRich, ScriptedProvider


@pytest.fixture
def fake_providers(monkeypatch):
    monkeypatch.setitem(registry.PROVIDERS, "fake-rich", FakeRich)
    monkeypatch.setitem(registry.PROVIDERS, "fake-poor", FakePoor)
    monkeypatch.setitem(registry.API_KEYS, "fake-rich", lambda: "fake-key")
    monkeypatch.setitem(registry.API_KEYS, "fake-poor", lambda: "fake-key")
    ScriptedProvider.script = []
    ScriptedProvider.requests = []
    yield ScriptedProvider


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
