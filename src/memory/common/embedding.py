import logging
import pathlib
import uuid
from typing import Any, Iterable, Literal, NotRequired, TypedDict, cast

import voyageai
from PIL import Image

from memory.common import extract, settings
from memory.common.chunker import chunk_text
from memory.common.db.models import Chunk

logger = logging.getLogger(__name__)


# Chunking configuration
MAX_TOKENS = 32000  # VoyageAI max context window
OVERLAP_TOKENS = 200  # Default overlap between chunks
CHARS_PER_TOKEN = 4


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
        "model": settings.TEXT_EMBEDDING_MODEL,
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


def get_mimetype(image: Image.Image) -> str | None:
    format_to_mime = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "GIF": "image/gif",
        "BMP": "image/bmp",
        "TIFF": "image/tiff",
        "WEBP": "image/webp",
    }

    if not image.format:
        return None

    return format_to_mime.get(image.format.upper(), f"image/{image.format.lower()}")


def get_modality(mime_type: str) -> str:
    for type, mime_types in TYPES.items():
        if mime_type in mime_types:
            return type
    stem = mime_type.split("/")[0]

    for type, mime_types in TYPES.items():
        if any(mime_type.startswith(stem) for mime_type in mime_types):
            return type
    return "unknown"


def embed_chunks(
    chunks: list[str] | list[list[extract.MulitmodalChunk]],
    model: str = settings.TEXT_EMBEDDING_MODEL,
    input_type: Literal["document", "query"] = "document",
) -> list[Vector]:
    vo = voyageai.Client()  # type: ignore
    if model == settings.MIXED_EMBEDDING_MODEL:
        return vo.multimodal_embed(
            chunks,  # type: ignore
            model=model,
            input_type=input_type,
        ).embeddings
    return vo.embed(chunks, model=model, input_type=input_type).embeddings  # type: ignore


def embed_text(
    texts: list[str],
    model: str = settings.TEXT_EMBEDDING_MODEL,
    input_type: Literal["document", "query"] = "document",
) -> list[Vector]:
    chunks = [
        c
        for text in texts
        if isinstance(text, str)
        for c in chunk_text(text, MAX_TOKENS, OVERLAP_TOKENS)
        if c.strip()
    ]
    if not chunks:
        return []

    try:
        return embed_chunks(chunks, model, input_type)
    except voyageai.error.InvalidRequestError as e:  # type: ignore
        logger.error(f"Error embedding text: {e}")
        logger.debug(f"Text: {texts}")
        raise


def embed_file(
    file_path: pathlib.Path, model: str = settings.TEXT_EMBEDDING_MODEL
) -> list[Vector]:
    return embed_text([file_path.read_text()], model)


def embed_mixed(
    items: list[extract.MulitmodalChunk],
    model: str = settings.MIXED_EMBEDDING_MODEL,
    input_type: Literal["document", "query"] = "document",
) -> list[Vector]:
    def to_chunks(item: extract.MulitmodalChunk) -> Iterable[extract.MulitmodalChunk]:
        if isinstance(item, str):
            return [
                c for c in chunk_text(item, MAX_TOKENS, OVERLAP_TOKENS) if c.strip()
            ]
        return [item]

    chunks = [c for item in items for c in to_chunks(item)]
    return embed_chunks([chunks], model, input_type)


def embed_page(page: extract.Page) -> list[Vector]:
    contents = page["contents"]
    if all(isinstance(c, str) for c in contents):
        return embed_text(
            cast(list[str], contents), model=settings.TEXT_EMBEDDING_MODEL
        )
    return embed_mixed(
        cast(list[extract.MulitmodalChunk], contents),
        model=settings.MIXED_EMBEDDING_MODEL,
    )


def write_to_file(chunk_id: str, item: extract.MulitmodalChunk) -> pathlib.Path:
    if isinstance(item, str):
        filename = settings.CHUNK_STORAGE_DIR / f"{chunk_id}.txt"
        filename.write_text(item)
    elif isinstance(item, bytes):
        filename = settings.CHUNK_STORAGE_DIR / f"{chunk_id}.bin"
        filename.write_bytes(item)
    elif isinstance(item, Image.Image):
        filename = settings.CHUNK_STORAGE_DIR / f"{chunk_id}.png"
        item.save(filename)
    else:
        raise ValueError(f"Unsupported content type: {type(item)}")
    return filename


def make_chunk(
    page: extract.Page, vector: Vector, metadata: dict[str, Any] = {}
) -> Chunk:
    """Create a Chunk object from a page and a vector.

    This is quite complex, because we need to handle the case where the page is a single string,
    a single image, or a list of strings and images.
    """
    chunk_id = str(uuid.uuid4())
    contents = page["contents"]
    content, filename = None, None
    if all(isinstance(c, str) for c in contents):
        content = "\n\n".join(cast(list[str], contents))
        model = settings.TEXT_EMBEDDING_MODEL
    elif len(contents) == 1:
        filename = write_to_file(chunk_id, contents[0]).absolute().as_posix()
        model = settings.MIXED_EMBEDDING_MODEL
    else:
        for i, item in enumerate(contents):
            write_to_file(f"{chunk_id}_{i}", item)
        model = settings.MIXED_EMBEDDING_MODEL
        filename = (settings.CHUNK_STORAGE_DIR / f"{chunk_id}_*").absolute().as_posix()

    return Chunk(
        id=chunk_id,
        file_path=filename,
        content=content,
        embedding_model=model,
        vector=vector,
        item_metadata=metadata,
    )


def embed(
    mime_type: str,
    content: bytes | str | pathlib.Path,
    metadata: dict[str, Any] = {},
) -> tuple[str, list[Chunk]]:
    modality = get_modality(mime_type)
    pages = extract.extract_content(mime_type, content)
    chunks = [
        make_chunk(page, vector, metadata)
        for page in pages
        for vector in embed_page(page)
    ]
    return modality, chunks


def embed_image(file_path: pathlib.Path, texts: list[str]) -> Chunk:
    image = Image.open(file_path)
    mime_type = get_mimetype(image)
    if mime_type is None:
        raise ValueError("Unsupported image format")

    vector = embed_mixed([image] + texts)[0]

    return Chunk(
        id=str(uuid.uuid4()),
        file_path=file_path.absolute().as_posix(),
        content=None,
        embedding_model=settings.MIXED_EMBEDDING_MODEL,
        vector=vector,
    )
