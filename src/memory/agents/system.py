"""System agents: the prompts memory itself runs, defined in code.

Each names one model, like any agent; it runs for a user only on their key for
that model's provider. Prompts and schemas are fixed, so they stay cacheable
and can be listed; anything that varies per call goes in the prompt passed to
run_agent. Worker-safe: no API imports.
"""

from dataclasses import dataclass
from typing import Any, Literal

from memory.common.llms.neutral import GenerationParams, ReasoningLevel
from memory.common.llms.registry import split_model

Use = Literal["ingest", "search", "sessions"]


@dataclass(frozen=True)
class SystemAgent:
    slug: str
    title: str
    description: str
    use: Use
    model: str
    system_prompt: str
    max_output_tokens: int
    result_schema: dict[str, Any] | None = None
    reasoning: ReasoningLevel = "off"

    @property
    def provider(self) -> str:
        return split_model(self.model)[0]

    def params(self) -> GenerationParams:
        return GenerationParams(
            max_output_tokens=self.max_output_tokens, reasoning=self.reasoning, temperature=None
        )

    def spec_dict(self) -> dict[str, Any]:
        """The agent as an AgentSpec payload, for run snapshots and the agents view."""
        return {
            "system_prompt": self.system_prompt,
            "model": self.model,
            "max_output_tokens": self.max_output_tokens,
            "reasoning": self.reasoning,
            "result_schema": self.result_schema,
            "max_turns": 1,
        }


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "tags"],
    "additionalProperties": False,
}

SUMMARISE = SystemAgent(
    slug="system:summarise",
    title="Summaries and tags",
    description="Summarises long items and tags them when they are ingested.",
    use="ingest",
    model="anthropic/claude-haiku-5-5",
    system_prompt="You are a helpful assistant that creates concise summaries and identifies key topics.",
    max_output_tokens=2048,
    result_schema=SUMMARY_SCHEMA,
)

SYSTEM_AGENTS: dict[str, SystemAgent] = {a.slug: a for a in (SUMMARISE,)}

# Every ingest agent runs on this provider, so one explicit key per source serves them all.
SOURCE_PROVIDER = SUMMARISE.provider


def system_agent(slug: str) -> SystemAgent:
    if slug not in SYSTEM_AGENTS:
        raise ValueError(f"unknown system agent {slug!r}")
    return SYSTEM_AGENTS[slug]
