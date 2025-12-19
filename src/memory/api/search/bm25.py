"""
Search endpoints for the knowledge base API.
"""

import asyncio
from hashlib import sha256
import logging

import bm25s
import Stemmer
from memory.api.search.types import SearchFilters

from memory.common import extract
from memory.common.db.connection import make_session
from memory.common.db.models import Chunk, ConfidenceScore, SourceItem

logger = logging.getLogger(__name__)


async def search_bm25(
    query: str,
    modalities: set[str],
    limit: int = 10,
    filters: SearchFilters = SearchFilters(),
) -> list[str]:
    with make_session() as db:
        items_query = db.query(Chunk.id, Chunk.content).filter(
            Chunk.collection_name.in_(modalities),
            Chunk.content.isnot(None),
        )

        # Join with SourceItem if we need size filters
        needs_source_join = any(filters.get(k) for k in ["min_size", "max_size"])
        if needs_source_join:
            items_query = items_query.join(
                SourceItem, SourceItem.id == Chunk.source_id
            )

        if source_ids := filters.get("source_ids"):
            items_query = items_query.filter(Chunk.source_id.in_(source_ids))

        # Size filters
        if min_size := filters.get("min_size"):
            items_query = items_query.filter(SourceItem.size >= min_size)
        if max_size := filters.get("max_size"):
            items_query = items_query.filter(SourceItem.size <= max_size)

        # Observation type filter - restricts to specific collection types
        if observation_types := filters.get("observation_types"):
            items_query = items_query.filter(
                Chunk.collection_name.in_(observation_types)
            )

        # Add confidence filtering if specified
        if min_confidences := filters.get("min_confidences"):
            for confidence_type, min_score in min_confidences.items():
                items_query = items_query.join(
                    ConfidenceScore,
                    (ConfidenceScore.source_item_id == Chunk.source_id)
                    & (ConfidenceScore.confidence_type == confidence_type)
                    & (ConfidenceScore.score >= min_score),
                )

        items = items_query.all()
        if not items:
            return []

        item_ids = {
            sha256(item.content.lower().strip().encode("utf-8")).hexdigest(): item.id
            for item in items
            if item.content
        }
        corpus = [item.content.lower().strip() for item in items]

    stemmer = Stemmer.Stemmer("english")
    corpus_tokens = bm25s.tokenize(corpus, stopwords="en", stemmer=stemmer)
    retriever = bm25s.BM25()
    retriever.index(corpus_tokens)

    query_tokens = bm25s.tokenize(query, stemmer=stemmer)
    results, scores = retriever.retrieve(
        query_tokens, k=min(limit, len(corpus)), corpus=corpus
    )

    item_scores = {
        item_ids[sha256(doc.encode("utf-8")).hexdigest()]: score
        for doc, score in zip(results[0], scores[0])
    }
    return list(item_scores.keys())


async def search_bm25_chunks(
    data: list[extract.DataChunk],
    modalities: set[str] = set(),
    limit: int = 10,
    filters: SearchFilters = SearchFilters(),
    timeout: int = 2,
) -> list[str]:
    query = " ".join([c for chunk in data for c in chunk.data if isinstance(c, str)])
    return await asyncio.wait_for(
        search_bm25(query, modalities, limit, filters),
        timeout,
    )
