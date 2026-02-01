# RAG Search Quality - Remaining Issues

**Last verified:** 2026-02-01

Investigation into search quality. Most issues from Dec 2025 have been fixed.

---

## Fixed Issues

- **BM25 OOM** - Replaced with PostgreSQL full-text search (tsvector/ts_rank)
- **Score propagation** - Scores now flow through properly
- **Score aggregation** - Uses max chunk score, not sum
- **Min score thresholds** - Text=0.25, Multimodal=0.4 (correct)
- **Candidate pool multiplier** - 5x internal limit implemented
- **Stopword filtering** - Added to FTS queries
- **Hybrid search** - 70% embedding + 30% FTS with 15% bonus for overlap

---

## Remaining Challenges

### Chunk Size Variance
- **Problem:** Chunks range from 3 bytes to 3.3MB
- **Impact:** Large chunks have diluted embeddings; small chunks lack context
- **Data:**
  | Collection | Avg Length | Max Length | Over 8KB |
  |------------|------------|------------|----------|
  | book       | 15,487     | 3.3MB      | 12,452   |
  | blog       | 3,661      | 710KB      | 2,874    |
  | forum      | 3,514      | 341KB      | 8,943    |
- **Fix:** Re-chunk with max ~512 tokens, 50-100 token overlap

### Conceptual Queries Don't Match Content
- **Problem:** User describes concept, article uses different terminology
- **Example:** "saying what you mean not using specific words" → target article "Taboo Your Words" ranks 23rd
- **Root cause:** Query describes the *effect*, article describes the *technique*
- **Fix:** Implement HyDE query expansion - generate hypothetical answer and embed that

### Embeddings Capture Theme, Not Details
- **Problem:** Brief mentions don't dominate chunk embeddings
- **Example:** Article about humility mentions "engineer fail-safe" as example; searching for that phrase finds articles specifically about engineering instead
- **Note:** This is inherent to how embeddings work; HyDE and better chunking would help

---

## Search Architecture

Current hybrid search flow:
1. Embed query with text-embedding-3-small (1536d)
2. Search Qdrant for similar chunks (70% weight)
3. Search PostgreSQL FTS for keyword matches (30% weight)
4. +15% bonus for results in both
5. Fuse scores, return top N

Weights defined in `src/memory/api/search/search.py`:
```python
EMBEDDING_WEIGHT = 0.7
BM25_WEIGHT = 0.3
HYBRID_BONUS = 0.15
```
