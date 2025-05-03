import pytest
from memory.common.chunker import yield_word_chunks, yield_spans, chunk_text, CHARS_PER_TOKEN, approx_token_count


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", []),
        ("hello", ["hello"]),
        ("This is a simple sentence", ["This is a simple sentence"]),
        ("word1   word2", ["word1 word2"]),
        ("  ", []),  # Just spaces
        ("\n\t ", []),  # Whitespace characters
        ("word1 \n word2\t word3", ["word1 word2 word3"]),  # Mixed whitespace
    ]
)
def test_yield_word_chunk_basic_behavior(text, expected):
    """Test basic behavior of yield_word_chunks with various inputs"""
    assert list(yield_word_chunks(text)) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "word1 word2 word3 word4 verylongwordthatexceedsthelimit word5",
            ['word1 word2 word3 word4', 'verylongwordthatexceedsthelimit word5'],
        ),
        (
            "supercalifragilisticexpialidocious",
            ["supercalifragilisticexpialidocious"],
        )
    ]
)
def test_yield_word_chunk_long_text(text, expected):
    """Test chunking with long text that exceeds token limits"""
    assert list(yield_word_chunks(text, max_tokens=10)) == expected


def test_yield_word_chunk_single_long_word():
    """Test behavior with a single word longer than the token limit"""
    max_tokens = 5  # 5 tokens = 20 chars with CHARS_PER_TOKEN = 4
    long_word = "x" * (max_tokens * CHARS_PER_TOKEN * 2)  # Word twice as long as max
    
    chunks = list(yield_word_chunks(long_word, max_tokens))
    # With our changes, this should be a single chunk
    assert len(chunks) == 1
    assert chunks[0] == long_word


def test_yield_word_chunk_small_token_limit():
    """Test with a very small max_tokens value to force chunking"""
    text = "one two three four five"
    max_tokens = 1  # Very small to force chunking after each word
    
    assert list(yield_word_chunks(text, max_tokens)) == ["one two", "three", "four", "five"]


@pytest.mark.parametrize(
    "text, max_tokens, expected_chunks",
    [
        # Empty text
        ("", 10, []),
        # Text below token limit
        ("hello world", 10, ["hello world"]),
        # Text right at token limit
        (
            "word1 word2",  # 11 chars with space
            3,  # 12 chars limit
            ["word1 word2"]
        ),
        # Text just over token limit should split
        (
            "word1 word2 word3",  # 17 chars with spaces
            4,  # 16 chars limit
            ["word1 word2 word3"]
        ),
        # Each word exactly at token limit
        (
            "aaaa bbbb cccc",  # Each word is exactly 4 chars (1 token)
            1,  # 1 token limit (4 chars)
            ["aaaa", "bbbb", "cccc"]
        ),
    ]
)
def test_yield_word_chunk_various_token_limits(text, max_tokens, expected_chunks):
    """Test different combinations of text and token limits"""
    assert list(yield_word_chunks(text, max_tokens)) == expected_chunks


def test_yield_word_chunk_real_world_example():
    """Test with a realistic text example"""
    text = (
        "The yield_word_chunks function splits text into chunks based on word boundaries. "
        "It tries to maximize chunk size while staying under the specified token limit. "
        "This behavior is essential for processing large documents efficiently."
    )
    
    max_tokens = 10  # 40 chars with CHARS_PER_TOKEN = 4
    assert list(yield_word_chunks(text, max_tokens)) == [
        'The yield_word_chunks function splits text',
        'into chunks based on word boundaries. It',
        'tries to maximize chunk size while staying',
        'under the specified token limit. This',
        'behavior is essential for processing large',
        'documents efficiently.',
    ]


# Tests for yield_spans function
@pytest.mark.parametrize(
    "text, expected",
    [
        ("", []),  # Empty text should yield nothing
        ("Simple paragraph", ["Simple paragraph"]),  # Single paragraph under token limit
        ("  ", []),  # Just whitespace
    ]
)
def test_yield_spans_basic_behavior(text, expected):
    """Test basic behavior of yield_spans with various inputs"""
    assert list(yield_spans(text)) == expected


def test_yield_spans_paragraphs():
    """Test splitting by paragraphs"""
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    expected = ["Paragraph one.", "Paragraph two.", "Paragraph three."]
    assert list(yield_spans(text)) == expected


def test_yield_spans_sentences():
    """Test splitting by sentences when paragraphs exceed token limit"""
    # Create a paragraph that exceeds token limit but sentences are within limit
    max_tokens = 5  # 20 chars with CHARS_PER_TOKEN = 4
    sentence1 = "Short sentence one."  # ~20 chars
    sentence2 = "Another short sentence."  # ~24 chars
    text = f"{sentence1} {sentence2}"  # Combined exceeds 5 tokens
    
    # Function should now preserve punctuation
    expected = ["Short sentence one.", "Another short sentence."]
    assert list(yield_spans(text, max_tokens)) == expected


def test_yield_spans_words():
    """Test splitting by words when sentences exceed token limit"""
    max_tokens = 3  # 12 chars with CHARS_PER_TOKEN = 4
    long_sentence = "This sentence has several words and needs word-level chunking."
    
    assert list(yield_spans(long_sentence, max_tokens)) == ['This sentence', 'has several', 'words and needs', 'word-level', 'chunking.'] 


def test_yield_spans_complex_document():
    """Test with a document containing multiple paragraphs and sentences"""
    max_tokens = 10  # 40 chars with CHARS_PER_TOKEN = 4
    text = (
        "Paragraph one with a short sentence. And another sentence that should be split.\n\n"
        "Paragraph two is also here. It has multiple sentences. Some are short. "
        "This one is longer and might need word splitting depending on the limit.\n\n"
        "Final short paragraph."
    )
    
    assert list(yield_spans(text, max_tokens)) == [
        "Paragraph one with a short sentence.",
        "And another sentence that should be split.",
        "Paragraph two is also here.",
        "It has multiple sentences.",
        "Some are short.",
        "This one is longer and might need word",
        "splitting depending on the limit.",
        "Final short paragraph."
    ]


def test_yield_spans_very_long_word():
    """Test with a word that exceeds the token limit"""
    max_tokens = 2  # 8 chars with CHARS_PER_TOKEN = 4
    long_word = "supercalifragilisticexpialidocious"  # Much longer than 8 chars
    
    assert list(yield_spans(long_word, max_tokens)) == [long_word]


def test_yield_spans_with_punctuation():
    """Test sentence splitting with various punctuation"""
    text = "First sentence! Second sentence? Third sentence."
    
    assert list(yield_spans(text, max_tokens=10)) == ["First sentence!", "Second sentence?", "Third sentence."]


def test_yield_spans_edge_cases():
    """Test edge cases like empty paragraphs, single character paragraphs"""
    text = "\n\nA\n\n\n\nB\n\n"
    
    assert list(yield_spans(text, max_tokens=10)) == ["A", "B"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", []),  # Empty text
        ("Short text", ["Short text"]),  # Text below token limit
        ("  ", []),  # Just whitespace
    ]
)
def test_chunk_text_basic_behavior(text, expected):
    """Test basic behavior of chunk_text with various inputs"""
    assert list(chunk_text(text)) == expected


def test_chunk_text_single_paragraph():
    """Test chunking a single paragraph that fits within token limit"""
    text = "This is a simple paragraph that should fit in one chunk."
    assert list(chunk_text(text, max_tokens=20)) == [text]


def test_chunk_text_multi_paragraph():
    """Test chunking multiple paragraphs"""
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    assert list(chunk_text(text, max_tokens=20)) == [text]


def test_chunk_text_long_text():
    """Test chunking with long text that exceeds token limit"""
    # Create a long text that will need multiple chunks
    sentences = [f"This is sentence {i:02}." for i in range(50)]
    text = " ".join(sentences)
    
    max_tokens = 10  # 10 tokens = ~40 chars
    assert list(chunk_text(text, max_tokens=max_tokens, overlap=6)) == [
        f'This is sentence {i:02}. This is sentence {i + 1:02}.' for i in range(49)
    ] + [
        'This is sentence 49.'
    ]
    

def test_chunk_text_with_overlap():
    """Test chunking with overlap between chunks"""
    # Create text with distinct parts to test overlap
    text = "Part A. Part B. Part C. Part D. Part E."
    
    assert list(chunk_text(text, max_tokens=4, overlap=3)) == ['Part A. Part B. Part C.', 'Part C. Part D. Part E.', 'Part E.']


def test_chunk_text_zero_overlap():
    """Test chunking with zero overlap"""
    text = "Part A. Part B. Part C. Part D. Part E."
    
    # 2 tokens = ~8 chars
    assert list(chunk_text(text, max_tokens=2, overlap=0)) == ['Part A. Part B.', 'Part C. Part D.', 'Part E.']


def test_chunk_text_clean_break():
    """Test that chunking attempts to break at sentence boundaries"""
    text = "First sentence. Second sentence. Third sentence. Fourth sentence."
    
    max_tokens = 5  # Enough for about 2 sentences
    assert list(chunk_text(text, max_tokens=max_tokens, overlap=3)) == ['First sentence. Second sentence.', 'Third sentence. Fourth sentence.']


def test_chunk_text_very_long_sentences():
    """Test with very long sentences that exceed the token limit"""
    text = "This is a very long sentence with many many words that will definitely exceed the token limit we set for this particular test case and should be split into multiple chunks by the function."
    
    max_tokens = 5  # Small limit to force splitting
    assert list(chunk_text(text, max_tokens=max_tokens)) == [
        'This is a very long sentence with many many',
        'words that will definitely exceed the',
        'token limit we set for',
        'this particular test',
        'case and should be split into multiple',
        'chunks by the function.',
    ]


@pytest.mark.parametrize(
    "string, expected_count",
    [
        ("", 0),
        ("a" * CHARS_PER_TOKEN, 1),
        ("a" * (CHARS_PER_TOKEN * 2), 2),
        ("a" * (CHARS_PER_TOKEN * 2 + 1), 2),  # Truncation
        ("a" * (CHARS_PER_TOKEN - 1), 0),  # Truncation
    ]
)
def test_approx_token_count(string, expected_count):
    assert approx_token_count(string) == expected_count
