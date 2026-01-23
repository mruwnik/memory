"""Tests for prediction market utilities."""

import pytest

from memory.common.markets import question_similarity


@pytest.mark.parametrize(
    "q1,q2,expected_min,expected_max",
    [
        # Identical questions
        ("Will Trump win 2024?", "Will Trump win 2024?", 1.0, 1.0),
        # Very similar questions
        (
            "Will Trump win the 2024 presidential election?",
            "Trump wins 2024 presidential election",
            0.5,
            1.0,
        ),
        # Somewhat similar (same topic, different framing)
        # Note: "100k" and "100000" don't match as words, so similarity is lower
        (
            "Bitcoin reaches $100k by 2025",
            "Will Bitcoin price exceed $100,000 in 2025?",
            0.2,
            0.5,
        ),
        # Completely different questions
        (
            "Will Ukraine ceasefire happen by 2025?",
            "XRP reaches new all time high in 2024",
            0.0,
            0.2,
        ),
        # Empty strings
        ("", "", 0.0, 0.0),
        ("Some question", "", 0.0, 0.0),
        ("", "Some question", 0.0, 0.0),
        # Only stop words
        ("the a an is are", "will be to of in", 0.0, 0.0),
    ],
)
def test_question_similarity(q1, q2, expected_min, expected_max):
    """question_similarity returns expected range for various question pairs."""
    similarity = question_similarity(q1, q2)
    assert expected_min <= similarity <= expected_max, (
        f"Expected similarity between {expected_min} and {expected_max}, "
        f"got {similarity} for '{q1}' vs '{q2}'"
    )


def test_question_similarity_symmetric():
    """question_similarity is symmetric."""
    q1 = "Will AGI be developed before 2030?"
    q2 = "AGI development timeline: before 2030"
    assert question_similarity(q1, q2) == question_similarity(q2, q1)


def test_question_similarity_case_insensitive():
    """question_similarity is case insensitive."""
    q1 = "TRUMP WINS 2024"
    q2 = "trump wins 2024"
    assert question_similarity(q1, q2) == 1.0
