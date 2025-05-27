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
    logger.debug(f"Embedding chunks: {model} - {str(chunks)[:100]}")
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


def embed_by_model(chunks: list[Chunk], model: str) -> list[Chunk]:
    model_chunks = [
        chunk for chunk in chunks if cast(str, chunk.embedding_model) == model
    ]
    if not model_chunks:
        return []

    vectors = embed_chunks([chunk.content for chunk in model_chunks], model)  # type: ignore
    for chunk, vector in zip(model_chunks, vectors):
        chunk.vector = vector
    return model_chunks


def embed_source_item(item: SourceItem) -> list[Chunk]:
    chunks = list(item.data_chunks())
    if not chunks:
        return []

    text_chunks = embed_by_model(chunks, settings.TEXT_EMBEDDING_MODEL)
    mixed_chunks = embed_by_model(chunks, settings.MIXED_EMBEDDING_MODEL)
    return text_chunks + mixed_chunks
