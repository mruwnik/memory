import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

from memory.common import settings, tokens, llms

logger = logging.getLogger(__name__)

MAX_TOKENS = 200000
TAGS_PROMPT = """
The following text is already concise. Please identify 3-5 relevant tags that capture the main topics or themes.

Tags should be lowercase and use hyphens instead of spaces, e.g. "machine-learning" instead of "Machine Learning".

Use this as the summary, verbatim:
{summary}

Text:
{content}
"""

SUMMARY_PROMPT = """
Please summarize the following text into approximately {target_tokens} tokens ({target_chars} characters).
Also provide 3-5 relevant tags that capture the main topics or themes.

Tags should be lowercase and use hyphens instead of spaces, e.g. "machine-learning" instead of "Machine Learning".

Text to summarize:
{content}
"""


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "tags"],
    "additionalProperties": False,
}


@dataclass
class DeferredSummary:
    """A summarize call postponed to the Message Batches API.

    ``chunk_metadata`` is the metadata of the summary chunk to add once the
    result arrives; None means only the tags are wanted (no summary chunk).
    """

    prompt: str
    chunk_metadata: dict[str, Any] | None
    modality: str | None = None


_deferred: ContextVar[list[DeferredSummary] | None] = ContextVar(
    "deferred_summaries", default=None
)


def batch_enabled() -> bool:
    return settings.SUMMARIZER_USE_BATCH and settings.SUMMARIZER_MODEL.startswith(
        "anthropic/"
    )


@contextmanager
def deferring(enabled: bool) -> Iterator[list[DeferredSummary]]:
    """Collect `summarize_or_defer` calls made inside the block instead of running them."""
    collected: list[DeferredSummary] = []
    if not enabled:
        yield collected
        return

    token = _deferred.set(collected)
    try:
        yield collected
    finally:
        _deferred.reset(token)


def build_prompt(content: str, target_tokens: int) -> str:
    # Already short enough content only needs tags
    if tokens.approx_token_count(content) <= target_tokens:
        prompt = TAGS_PROMPT.format(content=content, summary=content[:1000])
    else:
        prompt = SUMMARY_PROMPT.format(
            target_tokens=target_tokens,
            target_chars=target_tokens * tokens.CHARS_PER_TOKEN,
            content=content,
        )

    if tokens.approx_token_count(prompt) > MAX_TOKENS:
        logger.warning(
            f"Prompt too long ({tokens.approx_token_count(prompt)} tokens), truncating"
        )
        prompt = llms.truncate(prompt, MAX_TOKENS - 20)
    return prompt


def summarize_or_defer(
    content: str, chunk_metadata: dict[str, Any] | None, modality: str | None = None
) -> tuple[str, list[str]] | None:
    """`summarize`, or None when inside `deferring(True)`, which records the call instead."""
    collected = _deferred.get()
    if collected is None:
        return summarize(content)

    if content.strip():
        prompt = build_prompt(content, settings.DEFAULT_CHUNK_TOKENS)
        collected.append(DeferredSummary(prompt, chunk_metadata, modality))
    return None


def parse_summary(response: str) -> tuple[str, list[str]]:
    """(summary, tags) from a `SUMMARY_SCHEMA` response, capping overlong summaries; ValueError if unparseable."""
    result = json.loads(response)
    summary = result["summary"]
    target_tokens = settings.DEFAULT_CHUNK_TOKENS
    if tokens.approx_token_count(summary) > target_tokens * 1.5:
        summary = llms.truncate(summary, target_tokens)
    return summary, result["tags"]


def summarize(content: str, target_tokens: int | None = None) -> tuple[str, list[str]]:
    """
    Summarize content to approximately target_tokens length and generate tags.

    Args:
        content: Text to summarize
        target_tokens: Target length in tokens (defaults to DEFAULT_CHUNK_TOKENS)

    Returns:
        Tuple of (summary, tags)
    """
    if not content or not content.strip():
        return "", []

    if target_tokens is None:
        target_tokens = settings.DEFAULT_CHUNK_TOKENS

    summary, tags = content, []
    prompt = build_prompt(content, target_tokens)

    try:
        response = llms.summarize(
            prompt, settings.SUMMARIZER_MODEL, json_schema=SUMMARY_SCHEMA
        )
        result = json.loads(response)
        summary, tags = result["summary"], result["tags"]
    except Exception:
        logger.exception("Summarization failed")

    summary_tokens = tokens.approx_token_count(summary)
    if summary_tokens > target_tokens * 1.5:
        logger.warning(f"Summary too long ({summary_tokens} tokens), truncating")
        summary = llms.truncate(content, target_tokens)

    return summary, tags
