from collections.abc import Sequence
import logging
import pathlib
import uuid
from typing import Any, Iterable, Literal, cast

import voyageai
from PIL import Image

from memory.common import extract, settings
from memory.common.chunker import chunk_text, DEFAULT_CHUNK_TOKENS, OVERLAP_TOKENS
from memory.common.collections import ALL_COLLECTIONS, Vector
from memory.common.db.models import Chunk, SourceItem
from memory.common.extract import DataChunk

logger = logging.getLogger(__name__)


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
    chunk_size: int = DEFAULT_CHUNK_TOKENS,
) -> list[Vector]:
    chunks = [
        c
        for text in texts
        if isinstance(text, str)
        for c in chunk_text(text, chunk_size, OVERLAP_TOKENS)
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


def embed_mixed(
    items: list[extract.MulitmodalChunk],
    model: str = settings.MIXED_EMBEDDING_MODEL,
    input_type: Literal["document", "query"] = "document",
    chunk_size: int = DEFAULT_CHUNK_TOKENS,
) -> list[Vector]:
    def to_chunks(item: extract.MulitmodalChunk) -> Iterable[extract.MulitmodalChunk]:
        if isinstance(item, str):
            return [
                c for c in chunk_text(item, chunk_size, OVERLAP_TOKENS) if c.strip()
            ]
        return [item]

    chunks = [c for item in items for c in to_chunks(item)]
    return embed_chunks([chunks], model, input_type)


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
    contents: Sequence[extract.MulitmodalChunk],
    vector: Vector,
    metadata: dict[str, Any] = {},
) -> Chunk:
    """Create a Chunk object from a page and a vector.

    This is quite complex, because we need to handle the case where the page is a single string,
    a single image, or a list of strings and images.
    """
    chunk_id = str(uuid.uuid4())
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


def embed_data_chunk(
    chunk: DataChunk,
    metadata: dict[str, Any] = {},
    chunk_size: int | None = None,
) -> list[Chunk]:
    chunk_size = chunk.max_size or chunk_size or DEFAULT_CHUNK_TOKENS

    model = chunk.embedding_model
    if not model and chunk.collection:
        model = ALL_COLLECTIONS.get(chunk.collection, {}).get("model")
    if not model:
        model = settings.TEXT_EMBEDDING_MODEL

    if model == settings.TEXT_EMBEDDING_MODEL:
        vectors = embed_text(cast(list[str], chunk.data), chunk_size=chunk_size)
    elif model == settings.MIXED_EMBEDDING_MODEL:
        vectors = embed_mixed(
            cast(list[extract.MulitmodalChunk], chunk.data),
            chunk_size=chunk_size,
        )
    else:
        raise ValueError(f"Unsupported model: {model}")

    metadata = metadata | chunk.metadata
    return [make_chunk(chunk.data, vector, metadata) for vector in vectors]


def embed_source_item(
    item: SourceItem,
    metadata: dict[str, Any] = {},
    chunk_size: int | None = None,
) -> list[Chunk]:
    return [
        chunk
        for data_chunk in item.data_chunks()
        for chunk in embed_data_chunk(
            data_chunk, item.as_payload() | metadata, chunk_size
        )
    ]
