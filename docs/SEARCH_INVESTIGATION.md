# RAG Search Quality - Notes

**Last verified:** 2026-10-23

Working notes on search quality. Most issues from the Dec 2025 investigation have been
addressed; what remains is mostly inherent retrieval-quality tradeoffs.

---

## Resolved

- **BM25 OOM** — replaced with PostgreSQL full-text search (tsvector / `ts_rank`).
- **Score propagation** — scores now flow through the pipeline correctly.
- **Score aggregation** — uses the max chunk score, not the sum.
- **Min score thresholds** — text and multimodal collections have separate floors.
- **Candidate pool multiplier** — an internal limit multiplier widens the candidate pool
  before reranking.
- **Stopword filtering** — applied to FTS queries.
- **HyDE** — implemented (`src/memory/api/search/hyde.py`); off by default
  (`ENABLE_HYDE_EXPANSION`): in retrieval evals it added ~0.5 s per search without
  improving MRR.

---

## Search Architecture (current)

Implemented in `src/memory/api/search/`. Each stage is independently toggleable via an
`ENABLE_*` setting (see `src/memory/common/settings.py`).

1. **Query analysis** (`query_analysis.py`, `ENABLE_QUERY_ANALYSIS`) — an LLM extracts
   intent / structured hints from the raw query.
2. **HyDE expansion** (`hyde.py`, `ENABLE_HYDE_EXPANSION`, off by default) — generate a
   hypothetical answer and embed that alongside the raw query.
3. **Embedding** — Voyage `voyage-3-large` (1024d) for text, `voyage-multimodal-3` for
   mixed text+image. See `src/memory/common/embedding.py`.
4. **Vector search** — Qdrant similarity search over the embedded query strings: one
   batched request per collection.
5. **BM25 full-text** (`bm25.py`, `ENABLE_BM25_SEARCH`) — PostgreSQL tsvector keyword
   matching.
6. **Fusion** — the vector and BM25 result lists are merged with Reciprocal Rank Fusion
   (`fuse_scores_rrf` in `search.py`, `RRF_K = 60` in `constants.py`). RRF is rank-based;
   there are no embedding/BM25 weight constants.
7. **Boosts** (`_apply_boosts` in `search.py`, constants in `constants.py`) — the top
   `limit * FINAL_CANDIDATE_MULTIPLIER` fused candidates get a boost from query terms
   in the chunk, query terms or LLM-recalled titles in the source title, popularity,
   recency and being the summary chunk. A boost is a fraction of the score
   (`score + |score| * boost`, `types.chunk_score`), kept apart from it so it applies
   to whichever score comes last; every cut and ordering below ranks by it.
8. **Reranking** (`rerank.py`, `ENABLE_RERANKING`) — Voyage `rerank-2.5` cross-encoder
   scores the candidates, which are then cut to `limit` by boosted score; past
   `RERANK_TIMEOUT` the fused scores are kept.
9. **LLM scoring** (`scorer.py`, `ENABLE_SEARCH_SCORING`, per request `useScores`) —
   an LLM scores those `limit` chunks; chunks under `min_score` (boosted) are dropped.

Access-control filters are applied at the Qdrant, BM25, and final-merge layers (defense
in depth).

---

## Open challenges

### Chunk size variance

- **Problem:** chunk lengths vary enormously (single-digit bytes up to multi-MB).
- **Impact:** very large chunks have diluted embeddings; very small chunks lack context.
- **Possible fix:** re-chunk with a bounded max (~512 tokens) and 50–100 token overlap.

### Embeddings capture theme, not details

- **Problem:** brief mentions don't dominate a chunk embedding, so searching for a
  phrase that appears only in passing tends to surface documents *about* that phrase
  rather than the document that merely mentions it.
- **Note:** this is inherent to dense retrieval. HyDE and tighter chunking mitigate it
  but don't eliminate it.
