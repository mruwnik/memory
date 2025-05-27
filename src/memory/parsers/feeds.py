from datetime import datetime
import logging
import json
import re
from dataclasses import dataclass, field
from typing import Any, Generator, Sequence, cast
from urllib.parse import urljoin, urlparse

import feedparser
from bs4 import BeautifulSoup, Tag
import requests

from memory.parsers.html import (
    get_base_url,
    to_absolute_url,
    extract_title,
    extract_date,
    fetch_html,
)

logger = logging.getLogger(__name__)


ObjectPath = list[str | int]


def select_in(data: Any, path: ObjectPath) -> Any:
    if not path:
        return data

    key, *rest = path
    try:
        return select_in(data[key], rest)
    except (KeyError, TypeError, IndexError):
        return None


@dataclass
class FeedItem:
    """Represents a single item from a feed."""

    title: str
    url: str
    description: str = ""
    author: str | None = None
    published_date: datetime | None = None
    guid: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeedParser:
    """Base class for feed parsers."""

    url: str
    content: str | None = None
    since: datetime | None = None

    @property
    def base_url(self) -> str:
        """Get the base URL of the feed."""
        return get_base_url(self.url)

    def fetch_items(self) -> Sequence[Any]:
        """Fetch items from the feed. Override in subclasses."""
        return []

    def parse_item(self, item: Any) -> FeedItem:
        return FeedItem(
            title=self.extract_title(item),
            url=self.extract_url(item),
            description=self.extract_description(item),
            author=self.extract_author(item),
            published_date=self.extract_date(item),
            guid=self.extract_guid(item),
            metadata=self.extract_metadata(item),
        )

    def valid_item(self, item: FeedItem) -> bool:
        return bool(item.url)

    def parse_feed(self) -> Generator[FeedItem, None, None]:
        """Parse feed content and return list of feed items."""
        for item in self.fetch_items():
            parsed_item = self.parse_item(item)
            if self.valid_item(parsed_item):
                yield parsed_item

    def extract_title(self, entry: Any) -> str:
        """Extract title from feed entry. Override in subclasses."""
        return "Untitled"

    def extract_url(self, entry: Any) -> str:
        """Extract URL from feed entry. Override in subclasses."""
        return ""

    def extract_description(self, entry: Any) -> str:
        """Extract description from feed entry. Override in subclasses."""
        return ""

    def extract_author(self, entry: Any) -> str | None:
        """Extract author from feed entry. Override in subclasses."""
        return None

    def extract_date(self, entry: Any) -> datetime | None:
        """Extract publication date from feed entry. Override in subclasses."""
        return None

    def extract_guid(self, entry: Any) -> str | None:
        """Extract GUID from feed entry. Override in subclasses."""
        return None

    def extract_metadata(self, entry: Any) -> dict[str, Any]:
        """Extract additional metadata from feed entry. Override in subclasses."""
        return {}


class JSONParser(FeedParser):
    title_path: ObjectPath = ["title"]
    url_path: ObjectPath = ["url"]
    description_path: ObjectPath = ["description"]
    date_path: ObjectPath = ["date"]
    author_path: ObjectPath = ["author"]
    guid_path: ObjectPath = ["guid"]
    metadata_path: ObjectPath = ["metadata"]

    def fetch_items(self) -> Sequence[Any]:
        if not self.content:
            self.content = cast(str, fetch_html(self.url))
        try:
            return json.loads(self.content)
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON: {e}")
            return []

    def extract_title(self, entry: Any) -> str:
        return select_in(entry, self.title_path)

    def extract_url(self, entry: Any) -> str:
        return select_in(entry, self.url_path)

    def extract_description(self, entry: Any) -> str:
        return select_in(entry, self.description_path)

    def extract_date(self, entry: Any) -> datetime:
        return select_in(entry, self.date_path)

    def extract_author(self, entry: Any) -> str:
        return select_in(entry, self.author_path)

    def extract_guid(self, entry: Any) -> str:
        return select_in(entry, self.guid_path)

    def extract_metadata(self, entry: Any) -> dict[str, Any]:
        return select_in(entry, self.metadata_path)


class RSSAtomParser(FeedParser):
    """Parser for RSS and Atom feeds using feedparser."""

    def fetch_items(self) -> Sequence[Any]:
        """Fetch items from the feed."""
        if self.since:
            feed = feedparser.parse(self.content or self.url, modified=self.since)
        else:
            feed = feedparser.parse(self.content or self.url)
        return feed.entries

    def extract_title(self, entry: Any) -> str:
        """Extract title from RSS/Atom entry."""
        return getattr(entry, "title", "Untitled")

    def extract_url(self, entry: Any) -> str:
        """Extract URL from RSS/Atom entry."""
        url = getattr(entry, "link", "")
        if url and not urlparse(url).scheme:
            url = urljoin(self.base_url, url)
        return url

    def extract_description(self, entry: Any) -> str:
        """Extract description from RSS/Atom entry."""
        return getattr(entry, "summary", "") or getattr(entry, "description", "")

    def extract_author(self, entry: Any) -> str | None:
        """Extract author from RSS/Atom entry."""
        return getattr(entry, "author", None) or getattr(
            entry, "author_detail", {}
        ).get("name", None)

    def extract_date(self, entry: Any) -> datetime | None:
        """Extract publication date from RSS/Atom entry."""
        for date_attr in ["published_parsed", "updated_parsed"]:
            time_struct = getattr(entry, date_attr, None)
            if not time_struct:
                continue
            try:
                return datetime(*time_struct[:6])
            except (TypeError, ValueError):
                continue
        return None

    def extract_guid(self, entry: Any) -> str | None:
        """Extract GUID from RSS/Atom entry."""
        return getattr(entry, "id", None) or getattr(entry, "guid", None)

    def extract_metadata(self, entry: Any) -> dict[str, Any]:
        """Extract additional metadata from RSS/Atom entry."""
        return {
            attr: getattr(entry, attr)
            for attr in ["tags", "category", "categories", "enclosures"]
            if hasattr(entry, attr)
        }


DEFAULT_SKIP_PATTERNS = [
    r"^#",  # Fragment-only links
    r"mailto:",
    r"tel:",
    r"javascript:",
    r"\.pdf$",
    r"\.jpg$",
    r"\.png$",
    r"\.gif$",
]


class HTMLListParser(FeedParser):
    """Parser for HTML pages containing lists of article links.

    Requires explicit selectors to be specified - no magic defaults.
    """

    item_selector: str = "li"
    url_selector: str = "a[href]"
    skip_patterns: list[str] = DEFAULT_SKIP_PATTERNS
    title_selector: str | None = None
    description_selector: str | None = None
    date_selector: str | None = None
    date_format: str = "%Y-%m-%d"

    def fetch_items(self) -> Sequence[Any]:
        """Fetch items from the HTML page."""
        if not self.content:
            self.content = cast(str, fetch_html(self.url))

        soup = BeautifulSoup(self.content, "html.parser")
        items = []
        seen_urls = set()

        tags = soup.select(self.item_selector)

        for tag in tags:
            if not isinstance(tag, Tag):
                continue

            url = self.extract_url(tag)
            if url in seen_urls or self._should_skip_url(url):
                continue
            seen_urls.add(url)
            items.append(tag)

        return items

    def _should_skip_url(self, url: str) -> bool:
        """Check if URL should be skipped."""
        return any(
            re.search(pattern, url, re.IGNORECASE) for pattern in self.skip_patterns
        )

    def extract_title(self, entry: Any) -> str | None:
        """Extract title from HTML entry."""
        if self.title_selector:
            return extract_title(entry, self.title_selector)

    def extract_description(self, entry: Any) -> str | None:
        """Extract description from HTML entry."""
        if not self.description_selector:
            return None
        desc = entry.select_one(self.description_selector)
        return desc and desc.get_text(strip=True)

    def extract_url(self, entry: Any) -> str:
        """Extract URL from HTML entry."""
        if not (link := entry.select_one(self.url_selector)):
            return ""
        if not (href := link.get("href")):
            return ""
        return to_absolute_url(href, self.base_url)

    def extract_date(self, entry: Any) -> datetime | None:
        if self.date_selector:
            return extract_date(entry, self.date_selector, self.date_format)


class SubstackAPIParser(JSONParser):
    url_path = ["canonical_url"]
    author_path = ["publishedBylines", 0, "name"]
    date_path = ["post_date"]


class DanluuParser(HTMLListParser):
    skip_patterns = DEFAULT_SKIP_PATTERNS + [r"^https://danluu\.com/?#"]

    def valid_item(self, item: FeedItem) -> bool:
        return item.url.startswith(self.base_url)


class GuzeyParser(HTMLListParser):
    item_selector = "li a[href]"
    skip_patterns = DEFAULT_SKIP_PATTERNS + [r"docs\.google\.com"]

    def valid_item(self, item: FeedItem) -> bool:
        # Only include items that are actual blog posts (relative URLs or guzey.com URLs)
        return (
            item.url.startswith(self.base_url)
            or item.url.startswith("../")
            or not item.url.startswith("http")
        )


class PaulGrahamParser(HTMLListParser):
    item_selector = "img + font"
    title_selector = "a"
    skip_patterns = DEFAULT_SKIP_PATTERNS + [
        r"\.txt$",  # Skip text files
        r"turbifycdn\.com",  # Skip CDN links
    ]

    def valid_item(self, item: FeedItem) -> bool:
        # Only include items that are actual essays (relative URLs ending in .html)
        return (
            item.url.endswith(".html")
            and len(item.title) > 5  # Filter out very short titles
        )


class NadiaXyzParser(HTMLListParser):
    item_selector = ".blog.all li"
    skip_patterns = DEFAULT_SKIP_PATTERNS + [
        r"twitter\.com",
        r"newsletter",
        r"projects",
        r"notes",
    ]
    date_selector = ".date"
    date_format = "%B %d, %Y"
    description_selector = "p"

    def valid_item(self, item: FeedItem) -> bool:
        # Only include actual blog posts (relative URLs or nadia.xyz URLs)
        return (
            item.url.startswith(self.base_url)
            or item.url.startswith("/")
            or (not item.url.startswith("http") and item.url.endswith("/"))
        )


class RedHandFilesParser(HTMLListParser):
    item_selector = "article, .issue, .post"
    url_selector = "a[href]"
    title_selector = "h2, .issue-title"
    description_selector = "p"
    skip_patterns = DEFAULT_SKIP_PATTERNS + [
        r"/joy",
        r"/about",
        r"/subscribe",
        r"/ask",
        r"privacy-policy",
        r"#",
    ]

    def valid_item(self, item: FeedItem) -> bool:
        # Only include actual issues (should have "Issue #" in title or URL)
        return (
            item.url.startswith(self.base_url)
            and ("issue" in item.url.lower() or "issue #" in item.title.lower())
            and len(item.title) > 10
        )

    def extract_title(self, entry: Any) -> str:
        """Extract title, combining issue number and question."""
        # Look for issue number
        issue_elem = entry.select_one("h3, .issue-number")
        issue_text = issue_elem.get_text(strip=True) if issue_elem else ""

        # Look for the main question/title
        title_elem = entry.select_one("h2, .issue-title, .question")
        title_text = title_elem.get_text(strip=True) if title_elem else ""

        # Combine them
        if issue_text and title_text:
            return f"{issue_text}: {title_text}"
        elif title_text:
            return title_text
        elif issue_text:
            return issue_text

        # Fallback to any link text
        link = entry.select_one(self.url_selector)
        return link.get_text(strip=True) if link else "Untitled"

    def extract_description(self, entry: Any) -> str:
        """Extract the question text as description."""
        # Look for the question text in h2 or similar
        desc_elem = entry.select_one("h2, .question, .issue-title")
        if desc_elem:
            text = desc_elem.get_text(strip=True)
            # Clean up and truncate if too long
            if len(text) > 200:
                text = text[:200] + "..."
            return text
        return ""


class RiftersParser(HTMLListParser):
    item_selector = "#content .post"
    title_selector = "h2 a"
    url_selector = "h2 a"
    description_selector = ".entry-content"


class BloombergAuthorParser(HTMLListParser):
    item_selector = "section#author_page article"
    url_selector = "a[href]"
    title_selector = "article div a"
    description_selector = "article div section"
    skip_patterns = DEFAULT_SKIP_PATTERNS + [
        r"/authors/",
        r"/topics/",
        r"/subscribe",
        r"/newsletter/",
        r"#",
        r"mailto:",
    ]

    def valid_item(self, item: FeedItem) -> bool:
        # Only include actual articles
        return (
            (
                item.url.startswith("https://www.bloomberg.com")
                or item.url.startswith("https://archive.ph")
                or item.url.startswith("/")
            )
            and (
                "opinion" in item.url.lower()
                or "news" in item.url.lower()
                or len(item.url.split("/")) > 4
            )
            and len(item.title) > 10
        )


def is_rss_feed(content: str) -> bool:
    """Check if content appears to be an XML feed."""
    content_lower = content.strip().lower()
    return (
        content_lower.startswith("<?xml")
        or "<rss" in content_lower
        or "<feed" in content_lower
        or "<atom" in content_lower
    )


def clean_url(element: Tag, base_url: str) -> str | None:
    if not (href := element.get("href")):
        return None

    return to_absolute_url(str(href), base_url)


def find_feed_link(url: str, soup: BeautifulSoup) -> str | None:
    head = soup.find("head")
    if not head:
        return None
    for type_ in ["application/rss+xml", "application/atom+xml"]:
        links = head.find_all("link", {"rel": "alternate", "type": type_})  # type: ignore
        for link in links:
            if not isinstance(link, Tag):
                continue
            if not (link_url := clean_url(link, url)):
                continue
            if link_url.rstrip("/") != url.rstrip("/"):
                return link_url
    return None


FEED_REGISTRY = {
    r"https://danluu.com": DanluuParser,
    r"https://guzey.com/archive": GuzeyParser,
    r"https://www.paulgraham.com/articles": PaulGrahamParser,
    r"https://nadia.xyz/posts": NadiaXyzParser,
    r"https://www.theredhandfiles.com": RedHandFilesParser,
    r"https://archive.ph/.*?/https://www.bloomberg.com/opinion/authors/": BloombergAuthorParser,
}


def get_feed_parser(url: str, check_from: datetime | None = None) -> FeedParser | None:
    for pattern, parser_class in FEED_REGISTRY.items():
        if re.search(pattern, url.rstrip("/")):
            return parser_class(url=url, since=check_from)

    text = cast(str, fetch_html(url))
    if is_rss_feed(text):
        return RSSAtomParser(url=url, content=text, since=check_from)

    soup = BeautifulSoup(text, "html.parser")
    if feed_link := find_feed_link(url, soup):
        return RSSAtomParser(url=feed_link, since=check_from)

    for path in ["/archive", "/posts", "/feed"]:
        if url.rstrip("/").endswith(path):
            continue
        try:
            if parser := get_feed_parser(url + path, check_from):
                return parser
        except requests.HTTPError:
            continue

    return None
