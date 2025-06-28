import pytest
from memory.common.tokens import CHARS_PER_TOKEN, approx_token_count


@pytest.mark.parametrize(
    "string, expected_count",
    [
        ("", 0),
        ("a" * CHARS_PER_TOKEN, 1),
        ("a" * (CHARS_PER_TOKEN * 2), 2),
        ("a" * (CHARS_PER_TOKEN * 2 + 1), 2),  # Truncation
        ("a" * (CHARS_PER_TOKEN - 1), 0),  # Truncation
    ],
)
def test_approx_token_count(string, expected_count):
    assert approx_token_count(string) == expected_count
