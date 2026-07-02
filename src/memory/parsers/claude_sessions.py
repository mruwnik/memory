"""Parsing of Claude Code session transcripts (JSONL files).

A transcript is one JSON event per line, as ingested by
``memory.api.sessions``. This module extracts the conversational messages
(user/assistant text, skipping tool traffic and meta events) and groups
them into segments sized for embedding. Line indices are preserved so
segments and search hits can point back into the transcript for
context windows.
"""

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from memory.common import chunker, tokens
from memory.common.dates import parse_iso_datetime_utc

logger = logging.getLogger(__name__)

# Individual messages longer than this are truncated before indexing —
# pasted logs / file dumps would otherwise dominate a segment's embedding.
MAX_MESSAGE_CHARS = 5000

CONVERSATION_ROLES = ("user", "assistant")


@dataclass
class TranscriptMessage:
    """One conversational message, tied back to its transcript line."""

    index: int  # 0-based line index in the JSONL file
    role: str  # "user" or "assistant"
    text: str
    timestamp: datetime | None = None
    model: str | None = None  # assistant messages only

    @property
    def formatted(self) -> str:
        return f"{self.role.capitalize()}: {self.text}"


@dataclass
class TranscriptSegment:
    """A run of consecutive messages, sized to roughly one embedding chunk."""

    messages: list[TranscriptMessage] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(m.formatted for m in self.messages)

    @property
    def start_index(self) -> int:
        return self.messages[0].index

    @property
    def end_index(self) -> int:
        """Line index of the last message in the segment (inclusive)."""
        return self.messages[-1].index

    @property
    def start_time(self) -> datetime | None:
        return next((m.timestamp for m in self.messages if m.timestamp), None)

    @property
    def end_time(self) -> datetime | None:
        return next(
            (m.timestamp for m in reversed(self.messages) if m.timestamp), None
        )

    @property
    def roles(self) -> list[str]:
        return sorted({m.role for m in self.messages})

    @property
    def models(self) -> list[str]:
        return sorted({m.model for m in self.messages if m.model})


def extract_text_blocks(content: Any) -> str:
    """Pull the human-readable text out of a message's content field.

    Content is either a plain string or a list of typed blocks; only
    ``text`` blocks count — tool_use, tool_result, and thinking blocks are
    tool traffic, not conversation.
    """
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(p for p in parts if p).strip()


def parse_event(index: int, event: dict[str, Any]) -> TranscriptMessage | None:
    """Convert one transcript event to a message, or None if it's not conversation."""
    role = event.get("type")
    if role not in CONVERSATION_ROLES or event.get("is_meta"):
        return None

    message = event.get("message") or {}
    text = extract_text_blocks(message.get("content"))
    if not text:
        return None
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[:MAX_MESSAGE_CHARS] + " [... truncated]"

    return TranscriptMessage(
        index=index,
        role=role,
        text=text,
        timestamp=parse_iso_datetime_utc(event.get("timestamp")),
        model=message.get("model") if role == "assistant" else None,
    )


def iter_transcript_messages(
    file: Path, start_index: int = 0
) -> Iterator[TranscriptMessage]:
    """Stream conversational messages from a transcript file.

    ``start_index`` skips lines before that index (the indexing watermark).
    Blank and malformed lines still advance the line index so that indices
    stay stable as the file grows.

    Each call re-reads the file from the top, so reads are O(start_index):
    fine at current transcript sizes, a conscious tradeoff over maintaining
    a byte-offset index.
    """
    with open(file) as fh:
        for i, line in enumerate(fh):
            if i < start_index or not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message := parse_event(i, event):
                yield message


def build_segments(
    messages: Iterator[TranscriptMessage] | list[TranscriptMessage],
    max_tokens: int = chunker.DEFAULT_CHUNK_TOKENS,
) -> list[TranscriptSegment]:
    """Group consecutive messages into segments of at most ``max_tokens``.

    Deterministic for a given message stream: re-running over the same
    transcript prefix reproduces byte-identical segments, which is what
    lets sha256 dedup make reindexing idempotent. A single message longer
    than the budget gets its own segment (extract_text sub-chunks it at
    embedding time).
    """
    segments: list[TranscriptSegment] = []
    current = TranscriptSegment()
    current_tokens = 0

    for message in messages:
        message_tokens = tokens.approx_token_count(message.formatted)
        if current.messages and current_tokens + message_tokens > max_tokens:
            segments.append(current)
            current = TranscriptSegment()
            current_tokens = 0
        current.messages.append(message)
        current_tokens += message_tokens

    if current.messages:
        segments.append(current)
    return segments
