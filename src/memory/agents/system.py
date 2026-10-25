"""System agents: the prompts memory itself runs, defined in code.

Each names one model per provider, in the order a payer's keys are tried, so a
user with only an OpenAI key still gets summaries and search help. Prompts and
schemas are fixed, so they stay cacheable and can be listed; anything that varies
per call goes in the prompt passed to run_agent. Worker-safe: no API imports.
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
    system_prompt: str
    max_output_tokens: int
    result_schema: dict[str, Any] | None = None
    reasoning: ReasoningLevel = "off"
    models: tuple[str, ...] = (
        "anthropic/claude-haiku-5-5",
        # The cheapest OpenAI model, and the only one that can run with reasoning off.
        "openai/gpt-6-luna",
    )

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(split_model(m)[0] for m in self.models)

    def model_for(self, provider: str) -> str | None:
        return next((m for m in self.models if split_model(m)[0] == provider), None)

    def params(self) -> GenerationParams:
        return GenerationParams(
            max_output_tokens=self.max_output_tokens, reasoning=self.reasoning, temperature=None
        )

    def spec_dict(self, model: str) -> dict[str, Any]:
        """The agent on `model` as an AgentSpec payload, for run snapshots."""
        return {
            "system_prompt": self.system_prompt,
            "model": model,
            "max_output_tokens": self.max_output_tokens,
            "reasoning": self.reasoning,
            "result_schema": self.result_schema,
            "max_turns": 1,
        }


NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}
# Content already short enough is only tagged; its summary is null
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": NULLABLE_STRING,
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
    system_prompt=(
        "You summarise and tag items ingested into a personal knowledge base: emails, documents, "
        "notes, web pages, chat. Summaries are embedded for search, so keep the names, dates, "
        "decisions and specific terms someone would later search for."
    ),
    max_output_tokens=2048,
    result_schema=SUMMARY_SCHEMA,
)

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2-3 sentence summary"},
        "notes": {
            "type": "string",
            "description": "Markdown bullet list of key points and decisions",
        },
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "assignee": NULLABLE_STRING | {"description": "Person name"},
                    "due_date": NULLABLE_STRING | {"description": "YYYY-MM-DD"},
                    "priority": {
                        "anyOf": [
                            {
                                "type": "string",
                                "enum": ["low", "medium", "high", "urgent"],
                            },
                            {"type": "null"},
                        ]
                    },
                },
                "required": ["description", "assignee", "due_date", "priority"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "notes", "action_items"],
    "additionalProperties": False,
}

MEETING_EXTRACT = SystemAgent(
    slug="system:meeting-extract",
    title="Meeting notes and action items",
    description="Summarises meeting transcripts and turns their action items into tasks.",
    use="ingest",
    system_prompt=(
        "You are an expert at analyzing meeting transcripts and extracting actionable information.\n\n"
        "From the meeting transcript you are given, extract:\n"
        "1. A concise 2-3 sentence summary of the meeting's main purpose and outcomes\n"
        "2. Key discussion points, decisions made, and important information (as bullet points)\n"
        "3. Action items - tasks that were assigned or need to be done, with assignee, due date, "
        "and priority if mentioned"
    ),
    max_output_tokens=4096,
    result_schema=EXTRACTION_SCHEMA,
)

# Telling the model the passage is never shown and may be invented stops it
# refusing (or caveating at length) on queries about specific people and events;
# those refusals were both useless to embed and the slowest responses.
HYDE_SYSTEM_PROMPT = """You write hypothetical passages for HyDE retrieval in a private knowledge base (emails, chat, documents, notes).
Given a search query, write a short passage (2-3 sentences) that could appear in a document answering it.
The passage is never shown to anyone: it is only embedded to find real documents with similar wording.
So it is fine, and expected, to invent plausible names, dates, numbers and details; accuracy does not matter, vocabulary does.

Always write the passage. Never refuse, ask questions, add caveats, or comment on the query.
Write in the style of the likely source (email, chat message, article, issue, notes) and use the terminology such a document would use."""

HYDE = SystemAgent(
    slug="system:hyde",
    title="Search: hypothetical passages",
    description="Writes a passage that would answer a vague query, so search can match its wording (HyDE).",
    use="search",
    system_prompt=HYDE_SYSTEM_PROMPT,
    # A 2-3 sentence passage is ~150 tokens; the cap bounds the latency tail under the
    # search timeout, and a truncated passage still embeds fine.
    max_output_tokens=512,
)

QUERY_ANALYSIS_PROMPT = """Analyze this search query and extract structured information.

The user is searching a personal knowledge base; the content types it holds are listed with the query.

Guidelines:
- Restrict modalities only when the query names a content type ("that email
  from...", "the book about..."); otherwise return empty modalities to search all
- Remove meta-language like "there was something about", "I remember reading"
- For recalled_content: if you recognize the topic, suggest specific titles/essays
  that might be relevant (e.g., "predetermined conclusions" -> "The Bottom Line")
- start_date/end_date: inclusive YYYY-MM-DD bounds on when the content was sent,
  published or happened, resolved against the current date given with the query.
  Set them only when the query names or implies a time window ("yesterday",
  "since Monday", "last week", "on 12 October", "in March", "today's meetings",
  "next sync", "upcoming"); otherwise null. Leave a side null when it is open
  ("since March" has no end_date). Weeks start on Monday. For future references
  ("next", "upcoming") start today and end a few weeks ahead. Vague recency
  ("latest", "recent") gets no range.
- Drop time expressions from cleaned_query and query_variants once they are
  captured by the date range."""

QUERY_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "modalities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Content types to search (empty = search all); only those listed with the query",
        },
        "sources": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Specific sources/authors mentioned",
        },
        "cleaned_query": {
            "type": "string",
            "description": "Query with meta-language removed",
        },
        "query_variants": {
            "type": "array",
            "items": {"type": "string"},
            "description": "1-3 alternative phrasings",
        },
        "recalled_content": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Specific titles/essays/concepts you recall that match",
        },
        "start_date": {
            "anyOf": [{"type": "string", "format": "date"}, {"type": "null"}],
            "description": "First day of the time window the query refers to",
        },
        "end_date": {
            "anyOf": [{"type": "string", "format": "date"}, {"type": "null"}],
            "description": "Last day of the time window the query refers to",
        },
    },
    "required": [
        "modalities",
        "sources",
        "cleaned_query",
        "query_variants",
        "recalled_content",
        "start_date",
        "end_date",
    ],
    "additionalProperties": False,
}

QUERY_ANALYSIS = SystemAgent(
    slug="system:query-analysis",
    title="Search: query analysis",
    description="Picks content types, date ranges and alternative phrasings from a search query.",
    use="search",
    system_prompt=QUERY_ANALYSIS_PROMPT,
    max_output_tokens=1024,
    result_schema=QUERY_ANALYSIS_SCHEMA,
)

SCORE_CHUNK_SYSTEM_PROMPT = """
You are a helpful assistant that scores how relevant a chunk of text is to a query.

You are given a query and a chunk of text. The chunk should be relevant to the query, but often won't be. Score the chunk based on how relevant it is to the query and assign a score on a gradient between 0 and 1, which is the probability that the chunk is relevant to the query.
"""

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "number",
            "description": "Probability between 0 and 1 that the chunk is relevant",
        }
    },
    "required": ["score"],
    "additionalProperties": False,
}

CHUNK_SCORE = SystemAgent(
    slug="system:chunk-score",
    title="Search: relevance scoring",
    description="Scores each search result's relevance to the query when a search asks for scores.",
    use="search",
    system_prompt=SCORE_CHUNK_SYSTEM_PROMPT,
    max_output_tokens=256,
    result_schema=SCORE_SCHEMA,
)

SESSION_SUMMARY = SystemAgent(
    slug="system:session-summary",
    title="Claude session summaries",
    description="Writes a one-line summary of each Claude Code session.",
    use="sessions",
    system_prompt=(
        "You are summarizing a coding session between a user and an AI assistant.\n"
        "Write a concise 1-2 sentence summary of what was accomplished in this session.\n"
        "Focus on the main task or goal, not the individual steps.\n"
        "Be specific about what was built, fixed, or discussed.\n"
        "Start with what was done, as if completing \"This session...\"."
    ),
    max_output_tokens=1024,
)

SYSTEM_AGENTS: dict[str, SystemAgent] = {
    a.slug: a
    for a in (SUMMARISE, MEETING_EXTRACT, HYDE, QUERY_ANALYSIS, CHUNK_SCORE, SESSION_SUMMARY)
}

# Every ingest agent runs on these providers, so one explicit key per source serves them all.
SOURCE_PROVIDERS = SUMMARISE.providers


def system_agent(slug: str) -> SystemAgent:
    if slug not in SYSTEM_AGENTS:
        raise ValueError(f"unknown system agent {slug!r}")
    return SYSTEM_AGENTS[slug]
