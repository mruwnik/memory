import pathlib
from typing import Literal, TypedDict

DistanceType = Literal["Cosine", "Dot", "Euclidean"]
Vector = list[float]

class Collection(TypedDict):
    dimension: int
    distance: DistanceType
    on_disk: bool
    shards: int


DEFAULT_COLLECTIONS: dict[str, Collection] = {
    "mail": {"dimension": 1536, "distance": "Cosine"},
    "chat": {"dimension": 1536, "distance": "Cosine"},
    "git": {"dimension": 1536, "distance": "Cosine"},
    "photo": {"dimension": 512, "distance": "Cosine"},
    "book": {"dimension": 1536, "distance": "Cosine"},
    "blog": {"dimension": 1536, "distance": "Cosine"},
    "doc": {"dimension": 1536, "distance": "Cosine"},
}

TYPES = {
    "doc": ["text/*"],
    "photo": ["image/*"],
    "book": ["application/pdf", "application/epub+zip", "application/mobi", "application/x-mobipocket-ebook"],
}


def get_type(mime_type: str) -> str:
    for type, mime_types in TYPES.items():
        if mime_type in mime_types:
            return type
    stem = mime_type.split("/")[0]

    for type, mime_types in TYPES.items():
        if any(mime_type.startswith(stem) for mime_type in mime_types):
            return type
    return "unknown"


def embed_text(text: str, model: str = "text-embedding-3-small", n_dimensions: int = 1536) -> list[float]:
    """
    Embed a text using OpenAI's API.
    """
    return [0.0] * n_dimensions  # Placeholder n_dimensions-dimensional vector


def embed_file(file_path: str, model: str = "text-embedding-3-small", n_dimensions: int = 1536) -> list[float]:
    """
    Embed a file using OpenAI's API.
    """
    return [0.0] * n_dimensions  # Placeholder n_dimensions-dimensional vector


def embed(mime_type: str, content: bytes | str | pathlib.Path, model: str = "text-embedding-3-small", n_dimensions: int = 1536) -> tuple[str, list[float]]:
    collection = get_type(mime_type)

    return collection, [0.0] * n_dimensions  # Placeholder n_dimensions-dimensional vector