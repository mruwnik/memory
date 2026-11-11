"""HTML to markdown at a cost bounded by the input's size.

markdownify walks a BeautifulSoup tree with per-node parent searches, so
tag-dense HTML costs about 10 µs and 100 bytes of RAM per character. Only the
first MAX_HTML_TO_MARKDOWN_CHARS characters get that treatment; the rest has
its tags stripped in linear time, keeping the text searchable.
"""

import html
import re
from typing import Any

from markdownify import markdownify

from memory.common import settings

# `[^<>]` stops at the next `<`, so a run of unclosed `<` stays linear.
TAG = re.compile(r"<[^<>]*>")
RAW_TEXT_OPEN = re.compile(r"<(script|style)\b[^<>]*>", re.IGNORECASE)
RAW_TEXT_CLOSE = {
    "script": re.compile(r"</script", re.IGNORECASE),
    "style": re.compile(r"</style", re.IGNORECASE),
}
SPACES = re.compile(r"[ \t\r\f\v]+")
BLANK_LINES = re.compile(r"\n[ \t\r\f\v]*\n\s*")


def drop_raw_text_elements(text: str) -> str:
    """Remove <script> and <style> elements with their content; one left open
    runs to the end, as in a browser."""
    kept: list[str] = []
    pos = 0
    while opener := RAW_TEXT_OPEN.search(text, pos):
        kept.extend((text[pos : opener.start()], " "))
        closer = RAW_TEXT_CLOSE[opener.group(1).lower()].search(text, opener.end())
        if closer is None:
            return "".join(kept)
        end = text.find(">", closer.end())
        pos = len(text) if end < 0 else end + 1
    kept.append(text[pos:])
    return "".join(kept)


def strip_tags(text: str) -> str:
    """The text of an HTML fragment, in time linear in its length."""
    stripped = html.unescape(TAG.sub(" ", drop_raw_text_elements(text)))
    return BLANK_LINES.sub("\n\n", SPACES.sub(" ", stripped)).strip()


def split_point(text: str, limit: int) -> int:
    """Where to cut HTML near `limit` without splitting a tag."""
    cut = text.rfind("<", 0, limit + 1)
    return cut if cut > 0 else limit


def convert(text: str, **options: Any) -> str:
    """markdownify, or the stripped text when the HTML nests too deeply for
    its recursion."""
    try:
        return markdownify(text, **options).strip()
    except RecursionError:
        return strip_tags(text)


def html_to_markdown(text: str, limit: int | None = None, **options: Any) -> str:
    """Markdown for the first `limit` characters of HTML (default
    MAX_HTML_TO_MARKDOWN_CHARS), plain text for the rest."""
    limit = settings.MAX_HTML_TO_MARKDOWN_CHARS if limit is None else limit
    if len(text) <= limit:
        return convert(text, **options)
    cut = split_point(text, limit)
    return f"{convert(text[:cut], **options)}\n\n{strip_tags(text[cut:])}".strip()
