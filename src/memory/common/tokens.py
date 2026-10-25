CHARS_PER_TOKEN = 4


def approx_token_count(s: str) -> int:
    return len(s) // CHARS_PER_TOKEN


def fits(text: str, max_tokens: int) -> bool:
    return approx_token_count(text) <= max_tokens
