import logging
from typing import Literal, cast

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


def as_string(
    chunk: extract.MulitmodalChunk | list[extract.MulitmodalChunk],
) -> str:
    if isinstance(chunk, str):
        return chunk.strip()
    if isinstance(chunk, list):
        return "\n".join(as_string(i) for i in chunk).strip()
    return ""


def embed_chunks(
    chunks: list[list[extract.MulitmodalChunk]],
    model: str = settings.TEXT_EMBEDDING_MODEL,
    input_type: Literal["document", "query"] = "document",
) -> list[Vector]:
    logger.debug(f"Embedding chunks: {model} - {str(chunks)} {len(chunks)}")
    vo = voyageai.Client()  # type: ignore
    if model == settings.MIXED_EMBEDDING_MODEL:
        return vo.multimodal_embed(
            chunks,
            model=model,
            input_type=input_type,
        ).embeddings

    texts = [as_string(c) for c in chunks]
    logger.debug(f"Embedding texts: {texts}")
    return cast(
        list[Vector], vo.embed(texts, model=model, input_type=input_type).embeddings
    )


def break_chunk(
    chunk: extract.DataChunk, chunk_size: int = DEFAULT_CHUNK_TOKENS
) -> list[extract.MulitmodalChunk]:
    result = []
    for c in chunk.data:
        if isinstance(c, str):
            result += chunk_text(c, chunk_size, OVERLAP_TOKENS)
        else:
            result.append(chunk)
    return result


def embed_text(
    chunks: list[extract.DataChunk],
    model: str = settings.TEXT_EMBEDDING_MODEL,
    input_type: Literal["document", "query"] = "document",
    chunk_size: int = DEFAULT_CHUNK_TOKENS,
) -> list[Vector]:
    chunked_chunks = [break_chunk(chunk, chunk_size) for chunk in chunks if chunk.data]
    if not any(chunked_chunks):
        return []

    return embed_chunks(chunked_chunks, model, input_type)


def embed_mixed(
    items: list[extract.DataChunk],
    model: str = settings.MIXED_EMBEDDING_MODEL,
    input_type: Literal["document", "query"] = "document",
    chunk_size: int = DEFAULT_CHUNK_TOKENS,
) -> list[Vector]:
    chunked_chunks = [break_chunk(item, chunk_size) for item in items if item.data]
    return embed_chunks(chunked_chunks, model, input_type)


def embed_by_model(chunks: list[Chunk], model: str) -> list[Chunk]:
    model_chunks = [
        chunk for chunk in chunks if cast(str, chunk.embedding_model) == model
    ]
    if not model_chunks:
        return []

    vectors = embed_chunks([chunk.chunks for chunk in model_chunks], model)
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
