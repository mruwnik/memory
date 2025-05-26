import logging
from typing import Iterable, Literal, cast

import voyageai

from memory.common import extract, settings
from memory.common.chunker import (
    DEFAULT_CHUNK_TOKENS,
    OVERLAP_TOKENS,
    chunk_text,
)
from memory.common.collections import Vector
from memory.common.db.models import Chunk, SourceItem

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


def embed_chunk(chunk: Chunk) -> Chunk:
    model = cast(str, chunk.embedding_model)
    if model == settings.TEXT_EMBEDDING_MODEL:
        content = cast(str, chunk.content)
    elif model == settings.MIXED_EMBEDDING_MODEL:
        content = [cast(str, chunk.content)] + chunk.images
    else:
        raise ValueError(f"Unsupported model: {chunk.embedding_model}")
    vectors = embed_chunks([content], model)  # type: ignore
    chunk.vector = vectors[0]  # type: ignore
    return chunk


def embed_source_item(item: SourceItem) -> list[Chunk]:
    return [embed_chunk(chunk) for chunk in item.data_chunks()]
