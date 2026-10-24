from memory.agents.system import SUMMARISE
from memory.common import llms


def test_summarize_returns_generated_text(monkeypatch):
    calls = []

    class StubProvider:
        def generate(self, messages, system_prompt=None, tools=None, mcp_servers=None, settings=None):
            calls.append((messages, system_prompt, settings))
            return "a summary"

    monkeypatch.setattr("memory.common.llms.create_provider", lambda model: StubProvider())

    assert llms.summarize("text", model="anthropic/claude-haiku-5-5") == "a summary"
    assert calls[0][1] == SUMMARISE.system_prompt
