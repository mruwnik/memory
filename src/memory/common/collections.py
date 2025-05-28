import logging
from typing import Literal, NotRequired, TypedDict


from memory.common import settings

logger = logging.getLogger(__name__)


DistanceType = Literal["Cosine", "Dot", "Euclidean"]
Vector = list[float]


class Collection(TypedDict):
    dimension: int
    distance: DistanceType
    model: str
    on_disk: NotRequired[bool]
    shards: NotRequired[int]


ALL_COLLECTIONS: dict[str, Collection] = {
    "mail": {
        "dimension": 1024,
        "distance": "Cosine",
        "model": settings.TEXT_EMBEDDING_MODEL,
    },
    "chat": {
        "dimension": 1024,
        "distance": "Cosine",
        "model": settings.TEXT_EMBEDDING_MODEL,
    },
    "git": {
        "dimension": 1024,
        "distance": "Cosine",
        "model": settings.TEXT_EMBEDDING_MODEL,
    },
    "book": {
        "dimension": 1024,
        "distance": "Cosine",
        "model": settings.TEXT_EMBEDDING_MODEL,
    },
    "blog": {
        "dimension": 1024,
        "distance": "Cosine",
        "model": settings.MIXED_EMBEDDING_MODEL,
    },
    "forum": {
        "dimension": 1024,
        "distance": "Cosine",
        "model": settings.MIXED_EMBEDDING_MODEL,
    },
    "text": {
        "dimension": 1024,
        "distance": "Cosine",
        "model": settings.TEXT_EMBEDDING_MODEL,
    },
    # Multimodal
    "photo": {
        "dimension": 1024,
        "distance": "Cosine",
        "model": settings.MIXED_EMBEDDING_MODEL,
    },
    "comic": {
        "dimension": 1024,
        "distance": "Cosine",
        "model": settings.MIXED_EMBEDDING_MODEL,
    },
    "doc": {
        "dimension": 1024,
        "distance": "Cosine",
        "model": settings.MIXED_EMBEDDING_MODEL,
    },
}
TEXT_COLLECTIONS = {
    coll
    for coll, params in ALL_COLLECTIONS.items()
    if params["model"] == settings.TEXT_EMBEDDING_MODEL
}
MULTIMODAL_COLLECTIONS = {
    coll
    for coll, params in ALL_COLLECTIONS.items()
    if params["model"] == settings.MIXED_EMBEDDING_MODEL
}

TYPES = {
    "doc": ["application/pdf", "application/docx", "application/msword"],
    "text": ["text/*"],
    "blog": ["text/markdown", "text/html"],
    "photo": ["image/*"],
    "book": [
        "application/epub+zip",
        "application/mobi",
        "application/x-mobipocket-ebook",
    ],
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


def collection_model(collection: str) -> str | None:
    return ALL_COLLECTIONS.get(collection, {}).get("model")
