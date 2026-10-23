import pytest

from memory.common.llms import registry
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
