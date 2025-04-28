import pathlib
from typing import Literal, TypedDict, Iterable
import voyageai
import re

# Chunking configuration
MAX_TOKENS = 32000  # VoyageAI max context window
OVERLAP_TOKENS = 200  # Default overlap between chunks
CHARS_PER_TOKEN = 4


DistanceType = Literal["Cosine", "Dot", "Euclidean"]
Vector = list[float]

class Collection(TypedDict):
    dimension: int
    distance: DistanceType
    on_disk: bool
    shards: int


DEFAULT_COLLECTIONS: dict[str, Collection] = {
    "mail": {"dimension": 1024, "distance": "Cosine"},
    "chat": {"dimension": 1024, "distance": "Cosine"},
    "git": {"dimension": 1024, "distance": "Cosine"},
    "photo": {"dimension": 512, "distance": "Cosine"},
    "book": {"dimension": 1024, "distance": "Cosine"},
    "blog": {"dimension": 1024, "distance": "Cosine"},
    "doc": {"dimension": 1024, "distance": "Cosine"},
}

TYPES = {
    "doc": ["text/*"],
    "photo": ["image/*"],
    "book": ["application/pdf", "application/epub+zip", "application/mobi", "application/x-mobipocket-ebook"],
}


def get_modality(mime_type: str) -> str:
    for type, mime_types in TYPES.items():
        if mime_type in mime_types:
            return type
    stem = mime_type.split("/")[0]

    for type, mime_types in TYPES.items():
        if any(mime_type.startswith(stem) for mime_type in mime_types):
            return type
    return "unknown"


# Regex for sentence splitting
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def approx_token_count(s: str) -> int:
    return len(s) // CHARS_PER_TOKEN


def yield_word_chunks(text: str, max_tokens: int = MAX_TOKENS) -> Iterable[str]:
    words = text.split()
    if not words:
        return
        
    current = ""
    for word in words:
        new_chunk = f"{current} {word}".strip()
        if current and approx_token_count(new_chunk) > max_tokens:
            yield current
            current = word
        else:
            current = new_chunk
    if current:  # Only yield non-empty final chunk
        yield current


def yield_spans(text: str, max_tokens: int = MAX_TOKENS) -> Iterable[str]:
    """
    Yield text spans in priority order: paragraphs, sentences, words.
    Each span is guaranteed to be under max_tokens.
    
    Args:
        text: The text to split
        max_tokens: Maximum tokens per chunk
        
    Yields:
        Spans of text that fit within token limits
    """
    # Return early for empty text
    if not text.strip():
        return
        
    for paragraph in text.split("\n\n"):
        if not paragraph.strip():
            continue
            
        if approx_token_count(paragraph) <= max_tokens:
            yield paragraph
            continue
        
        for sentence in _SENT_SPLIT_RE.split(paragraph):
            if not sentence.strip():
                continue
                
            if approx_token_count(sentence) <= max_tokens:
                yield sentence
                continue
            
            for chunk in yield_word_chunks(sentence, max_tokens):
                yield chunk


def chunk_text(text: str, max_tokens: int = MAX_TOKENS, overlap: int = OVERLAP_TOKENS) -> Iterable[str]:
    """
    Split text into chunks respecting semantic boundaries while staying within token limits.
    
    Args:
        text: The text to chunk
        max_tokens: Maximum tokens per chunk (default: VoyageAI max context)
        overlap: Number of tokens to overlap between chunks (default: 200)
    
    Returns:
        List of text chunks
    """
    text = text.strip()
    if not text:
        return
        
    if approx_token_count(text) <= max_tokens:
        yield text
        return
    
    overlap_chars = overlap * CHARS_PER_TOKEN
    current = ""

    for span in yield_spans(text, max_tokens):
        current = f"{current} {span}".strip()
        if approx_token_count(current) < max_tokens:
            continue

        if overlap <= 0:
            yield current
            current = ""
            continue
        
        overlap_text = current[-overlap_chars:]
        clean_break = max(
            overlap_text.rfind(". "), 
            overlap_text.rfind("! "), 
            overlap_text.rfind("? ")
        )

        if clean_break < 0:
            yield current
            current = ""
            continue
        
        break_offset = -overlap_chars + clean_break + 1
        chunk = current[break_offset:].strip()
        yield current
        current = chunk

    if current:
        yield current.strip()


def embed_text(text: str, model: str = "voyage-3-large", n_dimensions: int = 1536) -> list[Vector]:
    vo = voyageai.Client()
    return vo.embed(chunk_text(text, MAX_TOKENS, OVERLAP_TOKENS), model=model)


def embed_file(file_path: pathlib.Path, model: str = "voyage-3-large", n_dimensions: int = 1536) -> list[Vector]:
    return embed_text(file_path.read_text(), model, n_dimensions)


def embed(mime_type: str, content: bytes | str, model: str = "voyage-3-large", n_dimensions: int = 1536) -> tuple[str, list[Vector]]:
    if isinstance(content, bytes):
        content = content.decode("utf-8")

    return get_modality(mime_type), embed_text(content, model, n_dimensions)
