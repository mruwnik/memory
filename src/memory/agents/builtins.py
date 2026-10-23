"""Runner-level builtin tools: served natively by the model when it can, else locally."""

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

import httpx
from markdownify import markdownify

from memory.common.downloads import DEFAULT_MAX_REDIRECTS as MAX_REDIRECTS
from memory.common.llms.tools import ToolDefinition
from memory.common.ssrf import validate_public_url

WEB_SEARCH = ToolDefinition(
    name="web_search",
    description="Search the web. Returns result titles, URLs and snippets.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
WEB_FETCH = ToolDefinition(
    name="web_fetch",
    description="Fetch a public web page and return its content as markdown.",
    input_schema={
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
        "additionalProperties": False,
    },
)
BUILTIN_DEFS = {t.name: t for t in (WEB_SEARCH, WEB_FETCH)}
MAX_FETCH_BYTES = 5 * 1024 * 1024
FETCH_DEADLINE_SEC = 30


class SearchBackend(Protocol):
    def search(self, query: str, max_results: int) -> list[dict[str, str]]: ...


def search_backend() -> SearchBackend | None:
    # No local backend ships until a provider without native search is added;
    # spec validation rejects web_search on such models meanwhile.
    return None


@dataclass
class Page:
    content_type: str = ""
    encoding: str | None = None
    body: bytearray = field(default_factory=bytearray)
    cut: str | None = None


async def read_page(url: str, page: Page) -> None:
    """Fill `page` from `url`, re-validating every redirect hop like safe_get does."""
    # Only encodings httpx always decodes: br/zstd need optional packages.
    headers = {"Accept-Encoding": "gzip, deflate"}
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=None, headers=headers
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            validate_public_url(url)
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    url = str(response.url.join(response.headers["location"]))
                    continue
                response.raise_for_status()
                page.content_type = response.headers.get("content-type", "")
                page.encoding = response.charset_encoding
                if not readable(page.content_type):
                    return
                async for chunk in response.aiter_bytes():
                    page.body += chunk
                    if len(page.body) > MAX_FETCH_BYTES:
                        del page.body[MAX_FETCH_BYTES:]
                        page.cut = f"page larger than {MAX_FETCH_BYTES} bytes"
                        return
                return
    raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects")


def readable(content_type: str) -> bool:
    return (
        "html" in content_type
        or content_type.startswith("text/")
        or "json" in content_type
    )


async def web_fetch(url: str) -> str:
    """Fetch a public page; private, loopback and metadata targets are refused, redirects included.

    One deadline covers connecting, headers, redirects and the body, so a
    server dripping bytes can't hold a run past it.
    """
    page = Page()
    try:
        async with asyncio.timeout(FETCH_DEADLINE_SEC):
            await read_page(url, page)
    except TimeoutError:
        page.cut = f"page took longer than {FETCH_DEADLINE_SEC}s"
    if page.content_type and not readable(page.content_type):
        return f"[unsupported content type: {page.content_type}]"
    text = bytes(page.body).decode(page.encoding or "utf-8", errors="replace")
    if "html" in page.content_type:
        text = markdownify(text).strip()
    return text + (f"\n[truncated: {page.cut}]" if page.cut else "")
