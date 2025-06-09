"""
Search endpoints for the knowledge base API.
"""

from hashlib import sha256
import logging

import bm25s
import Stemmer
from memory.api.search.utils import SourceData, AnnotatedChunk, SearchFilters

from memory.common.db.connection import make_session
from memory.common.db.models import Chunk, ConfidenceScore

logger = logging.getLogger(__name__)


async def search_bm25(
    query: str,
    modalities: set[str],
    limit: int = 10,
    filters: SearchFilters = SearchFilters(),
) -> list[tuple[SourceData, AnnotatedChunk]]:
    with make_session() as db:
        items_query = db.query(Chunk.id, Chunk.content).filter(
            Chunk.collection_name.in_(modalities),
            Chunk.content.isnot(None),
        )

        if source_ids := filters.get("source_ids"):
            items_query = items_query.filter(Chunk.source_id.in_(source_ids))

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

    with make_session() as db:
        chunks = db.query(Chunk).filter(Chunk.id.in_(item_scores.keys())).all()
        results = []
        for chunk in chunks:
            # Prefetch all needed source data while in session
            source_data = SourceData.from_chunk(chunk)

            annotated = AnnotatedChunk(
                id=str(chunk.id),
                score=item_scores[chunk.id],
                metadata=chunk.source.as_payload(),
                preview=None,
                search_method="bm25",
            )
            results.append((source_data, annotated))

        return results
