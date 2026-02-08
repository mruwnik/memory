import logging
from typing import Iterable, Any
import re

from memory.common import settings, tokens

logger = logging.getLogger(__name__)


# Chunking configuration
EMBEDDING_MAX_TOKENS = settings.EMBEDDING_MAX_TOKENS
DEFAULT_CHUNK_TOKENS = settings.DEFAULT_CHUNK_TOKENS
OVERLAP_TOKENS = settings.OVERLAP_TOKENS


Vector = list[float]
Embedding = tuple[str, Vector, dict[str, Any]]


# Regex for sentence splitting
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def yield_word_chunks(
    text: str, max_tokens: int = DEFAULT_CHUNK_TOKENS
) -> Iterable[str]:
    words = text.split()
    if not words:
        return

    current = ""
    for word in words:
        new_chunk = f"{current} {word}".strip()
        if current and tokens.approx_token_count(new_chunk) > max_tokens:
            yield current
            current = word
        else:
            current = new_chunk
    if current:  # Only yield non-empty final chunk
        yield current


def yield_spans(
    text: str, max_tokens: int = DEFAULT_CHUNK_TOKENS
) -> Iterable[tuple[str, bool]]:
    """
    Yield (text, is_paragraph_start) spans in priority order: paragraphs, sentences, words.
    Each span is guaranteed to be under max_tokens.

    The is_paragraph_start flag indicates whether this span begins a new paragraph,
    allowing callers to preserve paragraph boundaries (\\n\\n) vs intra-paragraph
    spacing (single space).

    Args:
        text: The text to split
        max_tokens: Maximum tokens per chunk

    Yields:
        Tuples of (span_text, is_paragraph_start)
    """
    # Return early for empty text
    if not text.strip():
        return

    for paragraph in text.split("\n\n"):
        if not paragraph.strip():
            continue

        if tokens.approx_token_count(paragraph) <= max_tokens:
            yield paragraph, True
            continue

        is_first = True
        for sentence in _SENT_SPLIT_RE.split(paragraph):
            if not sentence.strip():
                continue

            if tokens.approx_token_count(sentence) <= max_tokens:
                yield sentence, is_first
                is_first = False
                continue

            for chunk in yield_word_chunks(sentence, max_tokens):
                yield chunk, is_first
                is_first = False


def chunk_text(
    text: str, max_tokens: int = DEFAULT_CHUNK_TOKENS, overlap: int = OVERLAP_TOKENS
) -> Iterable[str]:
    """
    Split text into chunks respecting semantic boundaries while staying within token limits.

    Args:
        text: The text to chunk
        max_tokens: Maximum tokens per chunk (default: 512 for optimal semantic search)
        overlap: Number of tokens to overlap between chunks (default: 50)

    Returns:
        List of text chunks
    """
    text = text.strip()
    if not text:
        return

    if tokens.approx_token_count(text) <= max_tokens:
        yield text
        return

    overlap_chars = overlap * tokens.CHARS_PER_TOKEN
    current = ""

    for span, is_para_start in yield_spans(text, max_tokens):
        # Use \n\n between paragraphs, space within a paragraph
        sep = "\n\n" if is_para_start else " "
        new_chunk = f"{current}{sep}{span}".strip() if current else span
        if tokens.approx_token_count(new_chunk) <= max_tokens:
            current = new_chunk
            continue

        # Adding span would exceed limit - yield current first (if non-empty)
        if current:
            yield current

        # Handle overlap for the next chunk
        if overlap <= 0 or not current:
            current = span
            continue

        # Try to find a clean break point for overlap
        overlap_text = current[-overlap_chars:] if len(current) > overlap_chars else current
        clean_break = max(
            overlap_text.rfind(". "), overlap_text.rfind("! "), overlap_text.rfind("? ")
        )

        if clean_break < 0:
            current = span
            continue

        # Start new chunk with overlap from clean break
        break_offset = -len(overlap_text) + clean_break + 1
        overlap_portion = current[break_offset:].strip()
        current = f"{overlap_portion} {span}".strip() if overlap_portion else span

    if current:
        yield current.strip()
