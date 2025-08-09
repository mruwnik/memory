import logging
import re
import time
from dataclasses import dataclass, field
from typing import Generator, cast
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from memory.parsers.blogs import is_substack
from memory.parsers.feeds import (
    DanluuParser,
    FeedItem,
    FeedParser,
    HTMLListParser,
    RiftersParser,
    SubstackAPIParser,
)
from memory.parsers.html import extract_url, fetch_html, get_base_url

logger = logging.getLogger(__name__)


@dataclass
class ArchiveFetcher:
    """Fetches complete backlogs from sites with pagination."""

    parser_class: type[FeedParser]
    start_url: str
    max_pages: int = 100
    delay_between_requests: float = 1.0
    parser_kwargs: dict = field(default_factory=dict)

    def make_parser(self, url: str) -> FeedParser:
        parser = self.parser_class(url=url)
        for key, value in self.parser_kwargs.items():
            setattr(parser, key, value)
        return parser

    def fetch_all_items(self) -> Generator[FeedItem, None, None]:
        """Fetch all items from all pages."""
        visited_urls = set()
        current_url = self.start_url
        page_count = 0
        total_items = 0

        while current_url and page_count < self.max_pages:
            if current_url in visited_urls:
                logger.warning(f"Already visited {current_url}, stopping")
                break

            logger.info(f"Fetching page {page_count + 1}: {current_url}")
            visited_urls.add(current_url)

            try:
                parser = self.make_parser(current_url)

                items = parser.parse_feed()
                if not items:
                    break

                prev_items = total_items
                for item in items:
                    total_items += 1
                    yield item

                if prev_items == total_items:
                    logger.warning(f"No new items found on page {page_count + 1}")
                    break

                current_url = self._find_next_page(parser, page_count)
                if not current_url:
                    logger.info("No more pages found")
                    break

                page_count += 1

                if self.delay_between_requests > 0:
                    time.sleep(self.delay_between_requests)

            except Exception as e:
                logger.error(f"Error processing {current_url}: {e}")
                break

    def _find_next_page(self, parser: FeedParser, current_page: int = 0) -> str | None:
        return None


@dataclass
class LinkFetcher(ArchiveFetcher):
    per_page: int = 10

    def _find_next_page(self, parser: FeedParser, current_page: int = 0):
        next_page = current_page + 1
        parsed = urlparse(self.start_url)
        params = parse_qs(parsed.query)
        params["offset"] = [str(next_page * self.per_page)]
        params["limit"] = [str(self.per_page)]

        new_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))


@dataclass
class HTMLArchiveFetcher(ArchiveFetcher):
    next_page_selectors: list[str] = field(
        default_factory=lambda: [
            'a[rel="next"]',
            ".next a",
            "a.next",
            ".pagination .next",
            ".pager .next",
            "nav.page a:last-of-type",
            ".navigation a:last-of-type",
        ]
    )

    def _find_next_page(self, parser: FeedParser, current_page: int = 0) -> str | None:
        if not parser.content:
            return None
        soup = BeautifulSoup(parser.content, "html.parser")
        selectors = ",".join(self.next_page_selectors)
        return extract_url(soup, selectors, parser.url)


def html_parser(**kwargs) -> type[HTMLListParser]:
    class ConfiguredHTMLListParser(HTMLListParser):
        def __init__(self, url: str):
            super().__init__(url)
            for key, value in kwargs.items():
                setattr(self, key, value)

    return ConfiguredHTMLListParser


@dataclass
class SubstackArchiveFetcher(LinkFetcher):
    def __post_init__(self):
        if "api/v1/archive" not in self.start_url:
            base_url = get_base_url(self.start_url)
            self.start_url = f"{base_url}/api/v1/archive"


@dataclass
class ACOUPArchiveFetcher(HTMLArchiveFetcher):
    def _find_next_page(self, parser: FeedParser, current_page: int = 0) -> str | None:
        if not parser.content:
            return None
        soup = BeautifulSoup(parser.content, "html.parser")
        urls = reversed([i.attrs.get("href") for i in soup.select(".widget_archive a")])
        urls = (cast(str, u) for u in urls if u)
        for url in urls:
            if url.rstrip("/") == parser.url.rstrip("/"):
                return next(urls, None)


@dataclass
class HTMLNextUrlArchiveFetcher(HTMLArchiveFetcher):
    next_url: str = ""

    def __post_init__(self):
        if not self.next_url:
            self.next_url = self.start_url
        if not self.next_url.startswith("http") and not self.next_url.startswith("/"):
            self.next_url = f"{self.start_url}/{self.next_url}"

    def _find_next_page(self, parser: FeedParser, current_page: int = 0) -> str | None:
        return f"{self.next_url}/{current_page + 1}"


FETCHER_REGISTRY = {
    r"https://putanumonit.com": (
        "https://putanumonit.com/full-archive",
        html_parser(
            item_selector="article p", title_selector="a strong", url_selector="a"
        ),
    ),
    r"https://danluu.com": DanluuParser,
    r"https://www.rifters.com": RiftersParser,
    r"https://rachelbythebay.com": html_parser(
        item_selector="div.post",
        url_selector="a",
    ),
    r"https://guzey.com": (
        "https://guzey.com/archive/",
        html_parser(item_selector="article li"),
    ),
    r"https://aphyr.com": html_parser(
        item_selector="article.post",
        title_selector="h1",
        url_selector="h1 a",
        description_selector=".body",
        date_selector=".meta time",
    ),
    r"https://www.applieddivinitystudies.com": html_parser(
        item_selector="article.article",
        title_selector="header.article-header h1",
        url_selector="header.article-header h1 a",
        description_selector=".article-entry",
        date_selector=".article-meta time",
    ),
    r"https://www.flyingmachinestudios.com": html_parser(
        item_selector="#main #articles li",
        title_selector="header .title",
        description_selector="p",
        date_selector="header .date",
        date_format="%d %B %Y",
    ),
    r"https://slimemoldtimemold.com": html_parser(
        item_selector="article .wp-block-list li", title_selector="a"
    ),
    r"https://www.paulgraham.com": (
        "https://www.paulgraham.com/articles.html",
        html_parser(item_selector="img + font"),
    ),
    r"https://slatestarcodex.com": (
        "https://slatestarcodex.com/archives/",
        html_parser(item_selector="#sya_container li"),
    ),
    r"https://mcfunley.com": (
        "https://mcfunley.com/writing",
        html_parser(item_selector="article", title_selector="h6"),
    ),
    r"https://www.bitsaboutmoney.com": HTMLArchiveFetcher(
        html_parser(
            item_selector="article",
            title_selector="h1",
            description_selector="p",
            date_selector="time",
        ),
        "https://www.bitsaboutmoney.com/archive/",
        next_page_selectors=["nav.pagination a.older-posts"],
    ),
    r"https://acoup.blog": ACOUPArchiveFetcher(
        html_parser(
            item_selector="article",
            title_selector="a",
            description_selector=".entry-content",
            date_selector=".published-on time",
        ),
        "https://acoup.blog/2019/05/",
    ),
    r"https://www.theredhandfiles.com": html_parser(
        item_selector="article", title_selector="h3", description_selector="h2"
    ),
}


def get_archive_fetcher(url: str) -> ArchiveFetcher | None:
    for pattern, fetcher in FETCHER_REGISTRY.items():
        if re.search(pattern, url.rstrip("/")):
            if isinstance(fetcher, ArchiveFetcher):
                return fetcher
            elif isinstance(fetcher, tuple):
                base_url, html_fetcher = fetcher
                return HTMLArchiveFetcher(html_fetcher, base_url)
            else:
                return HTMLArchiveFetcher(fetcher, url)

    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    if is_substack(soup):
        return SubstackArchiveFetcher(SubstackAPIParser, url)


feeds = [
    "https://archive.ph/o/IQUoT/https://www.bloomberg.com/opinion/authors/ARbTQlRLRjE/matthew-s-levine",
    "https://www.rifters.com/crawl/",
    "https://rachelbythebay.com/w/",
    "https://danluu.com/",
    "https://guzey.com",
    "https://aphyr.com/",
    "https://www.applieddivinitystudies.com/",
    "https://www.imightbewrong.org/",
    "https://www.kvetch.au/",
    "https://www.overcomingbias.com/",
    "https://samkriss.substack.com/",
    "https://www.richardhanania.com/",
    "https://skunkledger.substack.com/",
    "https://taipology.substack.com/",
    "https://putanumonit.com/",
    "https://www.flyingmachinestudios.com/",
    "https://www.theintrinsicperspective.com/",
    "https://www.strangeloopcanon.com/",
    "https://slimemoldtimemold.com/",
    "https://zeroinputagriculture.substack.com/",
    "https://nayafia.substack.com",
    "https://www.paulgraham.com/articles.html",
    "https://mcfunley.com/writing",
    "https://www.bitsaboutmoney.com/",
    "https://akarlin.com",
    "https://www.exurbe.com/",
    "https://acoup.blog/",
    "https://www.theredhandfiles.com/",
    "https://karlin.blog/",
    "https://slatestarcodex.com/",
    "https://www.astralcodexten.com/",
    "https://nayafia.substack.com",
    "https://homosabiens.substack.com",
    "https://usefulfictions.substack.com",
]
