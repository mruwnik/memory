import logging
from typing import Literal, NotRequired, TypedDict

from PIL import Image

from memory.common import settings

logger = logging.getLogger(__name__)


DistanceType = Literal["Cosine", "Dot", "Euclidean"]
Vector = list[float]


class Collection(TypedDict):
    dimension: int
    distance: DistanceType
    on_disk: NotRequired[bool]
    shards: NotRequired[int]
    text: bool
    multimodal: bool


ALL_COLLECTIONS: dict[str, Collection] = {
    "mail": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": True,
        "multimodal": False,
    },
    "chat": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": True,
        "multimodal": True,
    },
    "message": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": True,
        "multimodal": True,
    },
    "git": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": True,
        "multimodal": False,
    },
    "book": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": True,
        "multimodal": False,
    },
    "blog": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": True,
        "multimodal": True,
    },
    "forum": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": True,
        "multimodal": True,
    },
    "github": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": True,
        "multimodal": False,
    },
    "text": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": True,
        "multimodal": False,
    },
    "meeting": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": True,
        "multimodal": False,
    },
    "photo": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": False,
        "multimodal": True,
    },
    "comic": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": False,
        "multimodal": True,
    },
    "doc": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": True,
        "multimodal": True,
    },
    "report": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": True,
        "multimodal": True,
    },
    "calendar": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": True,
        "multimodal": False,
    },
    # Observations
    "semantic": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": True,
        "multimodal": False,
    },
    "temporal": {
        "dimension": 1024,
        "distance": "Cosine",
        "text": True,
        "multimodal": False,
    },
}
TEXT_COLLECTIONS = {
    coll for coll, params in ALL_COLLECTIONS.items() if params.get("text")
}
MULTIMODAL_COLLECTIONS = {
    coll for coll, params in ALL_COLLECTIONS.items() if params.get("multimodal")
}
OBSERVATION_COLLECTIONS = {"semantic", "temporal"}

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


def collection_model(
    collection: str, text: str, images: list[Image.Image]
) -> str | None:
    """Determine the appropriate embedding model for a collection.

    Returns None if no suitable model can be determined, rather than
    falling back to an invalid placeholder.
    """
    config = ALL_COLLECTIONS.get(collection, {})
    if images and config.get("multimodal"):
        return settings.MIXED_EMBEDDING_MODEL
    if text and config.get("text"):
        return settings.TEXT_EMBEDDING_MODEL
    return None
