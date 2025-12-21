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

# How many candidates to pass to reranker (multiplier of final limit)
# Higher = more accurate but slower and more expensive
RERANK_CANDIDATE_MULTIPLIER = 3

# Bonus for chunks containing query terms (added to RRF score)
QUERY_TERM_BOOST = 0.005

# Bonus when query terms match the source title (stronger signal)
TITLE_MATCH_BOOST = 0.01

# Bonus when source title matches LLM-recalled content exactly
# This is larger than regular title boost because it's a strong signal
# that the user is looking for specific known content
RECALLED_TITLE_BOOST = 0.05

# Bonus multiplier for popularity (applied as: score * (1 + POPULARITY_BOOST * (popularity - 1)))
# This gives a small boost to popular items without dominating relevance
POPULARITY_BOOST = 0.02

# Recency boost settings
# Maximum bonus for brand new content (additive)
RECENCY_BOOST_MAX = 0.005
# Half-life in days: content loses half its recency boost every N days
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
