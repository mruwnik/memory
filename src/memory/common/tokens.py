CHARS_PER_TOKEN = 4


def approx_token_count(s: str) -> int:
    return len(s) // CHARS_PER_TOKEN
