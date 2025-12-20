# RAG Search Quality Investigation

## Summary

Investigation into why RAG search results "often aren't that good" when trying to find things with partial/vague memories.

**Date:** 2025-12-20
**Status:** Significant Progress Made

### Key Findings

1. **BM25 keyword search was broken** - Caused OOM with 250K chunks. ✅ FIXED: Replaced with PostgreSQL full-text search.

2. **Embeddings can't find "mentioned in passing" content** - Query "engineer fail-safe" ranks article about humility (that mentions engineers as example) at position 140 out of 145K. Articles specifically about engineering rank higher.

3. **Score propagation was broken** - ✅ FIXED: Scores now flow through properly.

4. **Chunk sizes are inconsistent** - Some chunks are 3MB (books), some are 3 bytes. Large chunks have diluted embeddings.

5. **"Half-remembered" queries don't match article keywords** - User describes concept, but article uses different terminology. E.g., "not using specific words" vs "taboo your words".

### What Works Now

- **Keyword-matching queries**: "clowns iconoclasts" → finds "Lonely Dissent" at rank 1 (score 0.815)
- **Direct concept queries**: "replacing words with definitions" → finds "Taboo Your Words" at rank 1
- **Hybrid search**: Results appearing in both embedding + FTS get 15% bonus

### Remaining Challenges

- **Conceptual queries**: "saying what you mean not using specific words" → target ranks 23rd (needs top 10)
- Query describes the *effect*, article describes the *technique*
- Need query expansion (HyDE) to bridge semantic gap

### Recommended Fix Priority

1. **Implement PostgreSQL full-text search** - ✅ DONE
2. **Add candidate pool multiplier** - ✅ DONE (5x internal limit)
3. **Add stopword filtering** - ✅ DONE
4. **Re-chunk oversized content** - Max 512 tokens, with context
5. **Implement HyDE query expansion** - For vague/conceptual queries

---

## PostgreSQL Full-Text Search Implementation (2025-12-20)

### Changes Made

1. **Created migration** `db/migrations/versions/20251220_130000_add_chunk_fulltext_search.py`
   - Added `search_vector` tsvector column to chunk table
   - Created GIN index for fast search
   - Added trigger to auto-update search_vector on insert/update
   - Populated existing 250K chunks with search vectors

2. **Rewrote bm25.py** to use PostgreSQL full-text search
   - Removed in-memory BM25 that caused OOM
   - Uses `ts_rank()` for relevance scoring
   - Uses AND matching with prefix wildcards: `engineer:* & fail:* & safe:*`
   - Normalized scores to 0-1 range

3. **Added search_vector column** to Chunk model in SQLAlchemy

### Test Results

For query "engineer fail safe":
- PostgreSQL FTS returns 100 results without OOM
- Source 157 (humility article) chunks rank **25th and 26th** (vs not appearing before)
- Search completes in ~100ms (vs OOM crash before)

### Hybrid Search Flow

With BM25 now working, the hybrid search combines:
- Embedding search (70% weight) - finds semantically similar content
- Full-text search (30% weight) - finds exact keyword matches
- +15% bonus for results appearing in both

This should significantly improve "half-remembered" searches where users recall specific words that appear in the article.

---

## Issues Fixed (This Session)

### 1. Scores Were Being Discarded (CRITICAL)

**Problem:** Both embedding and BM25 searches computed relevance scores but threw them away, returning only chunk IDs.

**Files Changed:**
- `src/memory/api/search/embeddings.py` - Now returns `dict[str, float]` (chunk_id -> score)
- `src/memory/api/search/bm25.py` - Now returns normalized scores (0-1 range)
- `src/memory/api/search/search.py` - Added `fuse_scores()` for hybrid ranking
- `src/memory/api/search/types.py` - Changed from mean to max chunk score

**Before:** All `search_score` values were 0.000
**After:** Meaningful scores like 0.443, 0.503, etc.

### 2. Score Fusion Implemented

Added weighted combination of embedding (70%) + BM25 (30%) scores with 15% bonus for results appearing in both searches.

```python
EMBEDDING_WEIGHT = 0.7
BM25_WEIGHT = 0.3
HYBRID_BONUS = 0.15
```

### 3. Changed from Mean to Max Chunk Score

**Before:** Documents with many chunks were penalized (averaging diluted scores)
**After:** Uses max chunk score - finds documents with at least one highly relevant section

---

## Current Issues Identified

### Issue 1: BM25 is Disabled AND Causes OOM

**Finding:** `ENABLE_BM25_SEARCH=False` in docker-compose.yaml

**Impact:** Keyword matching doesn't work. Queries like "engineer fail-safe" won't find articles containing those exact words unless the embedding similarity is high enough.

**When Enabled:** BM25 causes OOM crash!
- Database has 250,048 chunks total
- Forum collection alone has 147,546 chunks
- BM25 implementation loads ALL chunks into memory and builds index on each query
- Container killed (exit code 137) when attempting BM25 search

**Root Cause:** Current BM25 implementation in `bm25.py` is not scalable:
```python
items = items_query.all()  # Loads ALL chunks into memory
corpus = [item.content.lower().strip() for item in items]  # Copies all content
retriever.index(corpus_tokens)  # Builds index from scratch each query
```

**Recommendation:**
1. Build persistent BM25 index (store on disk, load once)
2. Or use PostgreSQL full-text search instead
3. Or limit BM25 to smaller collections only

### Issue 2: Embeddings Capture Theme, Not Details

**Test Case:** Article 157 about "humility in science" contains an example about engineers designing fail-safe mechanisms.

| Query | Result |
|-------|--------|
| "humility in science creationist evolution" | Rank 1, score 0.475 |
| "types of humility epistemic" | Rank 1, score 0.443 |
| "being humble about scientific knowledge" | Rank 1, score 0.483 |
| "engineer fail-safe mechanisms humble design" | Not in top 10 |
| "student double-checks math test answers" | Not in top 10 |
| "creationism debate" | Not in top 10 |

**Analysis:**
- Query "engineer fail-safe" has 0.52 cosine similarity to target chunks
- Other documents in corpus have 0.61+ similarity to that query
- The embedding captures the article's main theme (humility) but not incidental details (engineer example)

**Root Cause:** Embeddings are designed to capture semantic meaning of the whole chunk. Brief examples or mentions don't dominate the embedding.

### Issue 3: Chunk Context May Be Insufficient

**Finding:** The article's "engineer fail-safe" example appears in chunks, but:
- Some chunks are cut mid-word (e.g., "fail\-s" instead of "fail-safe")
- The engineer example may lack surrounding context

**Chunk Analysis for Article 157:**
- 7 chunks total
- Chunks containing "engineer": 2 (chunks 2 and 6)
- Chunk 2 ends with "fail\-s" (word cut off)
- The engineer example is brief (~2 sentences) within larger chunks about humility

---

## Embedding Similarity Analysis

For query "engineer fail-safe mechanisms humble design":

| Chunk | Similarity | Content Preview |
|-------|------------|-----------------|
| 3097f4d6 | 0.522 | "It is widely recognized that good science requires..." |
| db87f54d | 0.486 | "It is widely recognized that good science requires..." |
| f3e97d77 | 0.462 | "You'd still double-check your calculations..." |
| 9153d1f5 | 0.435 | "They ought to be more humble..." |
| 3375ae64 | 0.424 | "Dennett suggests that much 'religious belief'..." |
| 047e7a9a | 0.353 | Summary chunk |
| 80ff7a03 | 0.267 | References chunk |

**Problem:** Top results in the forum collection score 0.61+, so these 0.52 scores don't make the cut.

---

## Recommendations

### High Priority

1. **Enable BM25 Search**
   - Set `ENABLE_BM25_SEARCH=True`
   - This will find keyword matches that embeddings miss
   - Already implemented score fusion to combine results

2. **Lower Embedding Threshold for Text Collections**
   - Current: 0.25 minimum score
   - Consider: 0.20 to catch more marginal matches
   - Trade-off: May increase noise

3. **Increase Search Limit Before Fusion**
   - Current: Uses same `limit` for both embedding and BM25
   - Consider: Search for 2-3x more candidates, then fuse and return top N

### Medium Priority

4. **Implement Query Expansion / HyDE**
   - For vague queries, generate a hypothetical answer and embed that
   - Example: "engineer fail-safe" -> generate "An article discussing how engineers design fail-safe mechanisms as an example of good humility..."

5. **Improve Chunking Overlap**
   - Ensure examples carry context from surrounding paragraphs
   - Consider semantic chunking (split on topic changes, not just size)

6. **Add Document-Level Context to Chunks**
   - Prepend document title/summary to each chunk before embedding
   - Helps chunks maintain connection to main theme

### Lower Priority

7. **Tune Fusion Weights**
   - Current: 70% embedding, 30% BM25
   - May need adjustment based on use case

8. **Add Temporal Decay**
   - Prefer recent content for certain query types

---

## Architectural Issues

### Issue A: BM25 Implementation is Not Scalable

The current BM25 implementation cannot handle 250K chunks:

```python
# Current approach (in bm25.py):
items = items_query.all()  # Loads ALL matching chunks into memory
corpus = [item.content.lower().strip() for item in items]  # Makes copies
retriever.index(corpus_tokens)  # Rebuilds index from scratch per query
```

**Why this fails:**
- 147K forum chunks × ~3KB avg = ~440MB just for text
- Plus tokenization, BM25 index structures → OOM

**Solutions (in order of recommendation):**

1. **PostgreSQL Full-Text Search** (Recommended)
   - Already have PostgreSQL in stack
   - Add `tsvector` column to Chunk table
   - Create GIN index for fast search
   - Use `ts_rank` for relevance scoring
   - No additional infrastructure needed

2. **Persistent BM25 Index**
   - Build index once at ingestion time
   - Store on disk, load once at startup
   - Update incrementally on new chunks
   - More complex to maintain

3. **External Search Engine**
   - Elasticsearch or Meilisearch
   - Adds operational complexity
   - May be overkill for current scale

### Issue B: Chunk Size Variance

Chunks range from 3 bytes to 3.3MB. This causes:
- Large chunks have diluted embeddings
- Small chunks lack context
- Inconsistent search quality across collections

**Solution:** Re-chunk existing content with:
- Max ~512 tokens per chunk (optimal for embeddings)
- 50-100 token overlap between chunks
- Prepend document title/context to each chunk

### Issue C: Search Timeout (2 seconds)

The default 2-second timeout is too aggressive for:
- Large collections (147K forum chunks)
- Cold Qdrant cache
- Network latency

**Solution:** Increase to 5-10 seconds for initial search, with progressive loading UX.

---

## Test Queries for Validation

After making changes, test with these queries against article 157:

```python
# Should find article 157 (humility in science)
test_cases = [
    # Main topic - currently working
    ("humility in science", "main topic"),
    ("types of humility epistemic", "topic area"),

    # Specific examples - currently failing
    ("engineer fail-safe mechanisms", "specific example"),
    ("student double-checks math test", "specific example"),

    # Tangential mentions - currently failing
    ("creationism debate", "mentioned topic"),

    # Vague/half-remembered - currently failing
    ("checking your work", "vague concept"),
    ("when engineers make mistakes", "tangential"),
]
```

---

## Session Log

### 2025-12-20

1. **Initial Investigation**
   - Found scores were all 0.000
   - Traced to embeddings.py and bm25.py discarding scores

2. **Fixed Score Propagation**
   - Modified 4 files to preserve and fuse scores
   - Rebuilt Docker images
   - Verified scores now appear (0.4-0.5 range)

3. **Quality Testing**
   - Selected random article (ID 157, humility in science)
   - Tested 10 query types from specific to vague
   - Found 3/10 queries succeed (main topic only)

4. **Root Cause Analysis**
   - BM25 disabled - no keyword matching
   - Embeddings capture theme, not details
   - Target chunks have 0.52 similarity vs 0.61 for top results

5. **Next Steps**
   - Enable BM25 and retest
   - Consider HyDE for query expansion
   - Investigate chunking improvements

6. **Deep Dive: Database Statistics**
   - Total chunks: 250,048
   - Forum: 147,546 (58.9%)
   - Blog: 46,159 (18.5%)
   - Book: 34,586 (13.8%)
   - Text: 10,823 (4.3%)

7. **Chunk Size Analysis (MAJOR ISSUE)**
   Found excessively large chunks that dilute embedding quality:

   | Collection | Avg Length | Max Length | Over 8KB | Over 128KB |
   |------------|------------|------------|----------|------------|
   | book       | 15,487     | 3.3MB      | 12,452   | 474        |
   | blog       | 3,661      | 710KB      | 2,874    | 19         |
   | forum      | 3,514      | 341KB      | 8,943    | 47         |

   Books have 36% of chunks over 8KB - too large for good embedding quality.
   The Voyage embedding model has 32K token limit, but chunks over 8KB (~2K tokens)
   start to lose fine-grained detail in the embedding.

8. **Detailed Score Analysis for "engineer fail-safe mechanisms humble design"**
   - Query returns 145,632 results from forum collection
   - Top results score 0.61, median 0.34
   - Source 157 (target article) chunks score:
     - 3097f4d6: 0.5222 (rank 140/145,632) - main content
     - db87f54d: 0.4863 (rank 710/145,632) - full text chunk
     - f3e97d77: 0.4622 (rank 1,952/145,632)
     - 047e7a9a: 0.3528 (rank 58,949/145,632) - summary

   **Key Finding:** Target chunks rank 140th-710th, but with limit=10,
   they never appear. BM25 would find exact keyword match "engineer fail-safe".

9. **Top Results Analysis**
   The chunks scoring 0.61 (beating our target) are about:
   - CloudFlare incident (software failure)
   - AI safety testing (risk/mitigation mechanisms)
   - Generic "mechanisms to prevent failure" content

   These are semantically similar to "engineer fail-safe mechanisms"
   but NOT about humility. Embeddings capture concept, not context.

10. **Root Cause Confirmed**
    The fundamental problem is:
    1. Embeddings capture semantic meaning of query concepts
    2. Query "engineer fail-safe" embeds as "engineering safety mechanisms"
    3. Articles specifically about engineering/failure rank higher
    4. Article about humility (that merely mentions engineers as example) ranks lower
    5. Only keyword search (BM25) can find "mentioned in passing" content

11. **Implemented Candidate Pool Multiplier**
    Added `CANDIDATE_MULTIPLIER = 5` to search.py:
    - Internal searches now fetch 5x the requested limit
    - Results from both methods are fused, then top N returned
    - This helps surface results that rank well in one method but not both

12. **Added Stopword Filtering to FTS**
    Updated bm25.py to filter common English stopwords before building tsquery:
    - Words like "what", "you", "not", "the" are filtered out
    - This makes AND matching less strict
    - Query "saying what you mean" becomes "saying:* & mean:*" instead of 8 terms

13. **Testing: "Taboo Your Words" Query**
    Query: "saying what you mean not using specific words"
    Target: Source 735 ("Taboo Your Words" article)

    Results:
    - Embedding search ranks target at position 21 (score 0.606)
    - Top 10 results score 0.62-0.64 (about language/communication generally)
    - FTS doesn't match because article lacks "saying" and "specific"
    - After fusion: target ranks 23rd, cutoff is 20th

    **Key Insight:** The query describes the *concept* ("not using specific words")
    but the article is about a *technique* ("taboo your words = replace with definitions").
    These are semantically adjacent but not equivalent.

    With direct query "replacing words with their definitions" → ranks 1st!

14. **Testing: "Clowns Iconoclasts" Query**
    Query: "clowns being the real iconoclasts"
    Target: "Lonely Dissent" article

    Results: Found at rank 1 with score 0.815 (hybrid boost!)
    - Both embedding AND FTS match
    - 0.15 hybrid bonus applied
    - This is an ideal case where keywords match content

15. **Remaining Challenges**
    - "Half-remembered" queries describing concepts vs actual content
    - Need query expansion (HyDE) to bridge semantic gap
    - Or return more results for user to scan
    - Consider showing "You might also be looking for..." suggestions
