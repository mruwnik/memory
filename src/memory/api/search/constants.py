"""
Constants for search functionality.
"""

# Reciprocal Rank Fusion constant (k parameter)
# Higher values reduce the influence of top-ranked documents
# 60 is the standard value from the original RRF paper
RRF_K = 60

# Multiplier for internal search limit before fusion
# We search for more candidates than requested, fuse scores, then return top N
# This helps find results that rank well in one method but not the other
CANDIDATE_MULTIPLIER = 5

# How many fused candidates (multiple of the final limit) are boosted and reranked
# before the cut to the limit. Higher = more accurate but slower and more expensive
FINAL_CANDIDATE_MULTIPLIER = 3

# Boosts are fractions of a chunk's final score: score + |score| * (sum of boosts),
# applied after reranking and LLM scoring (types.chunk_score), so they mean the same on
# every score scale. They nudge near-ties; none should beat a clearly better match.

# When query terms appear in the chunk text (scaled by the fraction matched)
QUERY_TERM_BOOST = 0.05

# When query terms appear in the source title, which the reranker doesn't see
TITLE_MATCH_BOOST = 0.1

# When the source title matches LLM-recalled content: a strong sign the user is
# looking for that specific item
RECALLED_TITLE_BOOST = 0.5

# For a match on the item's summary chunk, which is about the whole item
SUMMARY_MATCH_BOOST = 0.05

# Applied as POPULARITY_BOOST * (popularity - 1); popularity runs 0.5..2.5
POPULARITY_BOOST = 0.05

# For brand new content, halving every RECENCY_HALF_LIFE_DAYS
RECENCY_BOOST_MAX = 0.05
RECENCY_HALF_LIFE_DAYS = 90

# Common words to ignore when checking for query term presence
STOPWORDS = frozenset({
    # Articles
    "a", "an", "the",
    # Be verbs
    "is", "are", "was", "were", "be", "been", "being",
    # Have verbs
    "have", "has", "had",
    # Do verbs
    "do", "does", "did",
    # Modal verbs
    "will", "would", "could", "should", "may", "might", "must", "shall", "can",
    "need", "dare", "ought", "used",
    # Prepositions
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as", "into",
    "through", "during", "before", "after", "above", "below", "between", "under",
    # Adverbs
    "again", "further", "then", "once", "here", "there", "when", "where", "why", "how",
    # Quantifiers
    "all", "each", "few", "more", "most", "other", "some", "such",
    # Negation
    "no", "nor", "not",
    # Other common words
    "only", "own", "same", "so", "than", "too", "very", "just",
    # Conjunctions
    "and", "but", "if", "or", "because", "until", "while", "although", "though",
    # Relative pronouns
    "what", "which", "who", "whom",
    # Demonstratives
    "this", "that", "these", "those",
    # Personal pronouns
    "i", "me", "my", "myself",
    "we", "our", "ours", "ourselves",
    "you", "your", "yours", "yourself", "yourselves",
    "he", "him", "his", "himself",
    "she", "her", "hers", "herself",
    "it", "its", "itself",
    "they", "them", "their", "theirs", "themselves",
    # Misc common words
    "about", "get", "got", "getting", "like", "also",
})
