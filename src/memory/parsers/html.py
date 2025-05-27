import hashlib
import logging
import pathlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as md
from PIL import Image as PILImage

from memory.common import settings

logger = logging.getLogger(__name__)


def fetch_html(url: str, as_bytes: bool = False) -> str | bytes:
    response = requests.get(
        url,
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:137.0) Gecko/20100101 Firefox/137.0"
        },
    )
    response.raise_for_status()
    if as_bytes:
        return response.content
    return response.text


@dataclass
class Article:
    """Structured representation of a web article."""

    title: str
    content: str  # Markdown content
    author: str | None = None
    published_date: datetime | None = None
    url: str = ""
    images: dict[str, PILImage.Image] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def get_base_url(url: str) -> str:
    """Extract base URL from full URL."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def to_absolute_url(url: str, base_url: str) -> str:
    """Convert relative URL to absolute URL."""
    parsed = urlparse(url)
    if parsed.scheme:
        return url
    return urljoin(base_url, url)


def remove_unwanted_elements(soup: BeautifulSoup, remove_selectors: list[str]) -> None:
    """Remove unwanted elements from the soup."""
    for selector in remove_selectors:
        for element in soup.select(selector):
            element.decompose()


def extract_title(soup: BeautifulSoup, title_selector: str) -> str:
    """Extract article title."""
    for selector in title_selector.split(","):
        element = soup.select_one(selector.strip())
        if element and element.get_text(strip=True):
            return element.get_text(strip=True)

    # Fallback to page title
    title_tag = soup.find("title")
    return title_tag.get_text(strip=True) if title_tag else "Untitled"


def extract_author(soup: BeautifulSoup, author_selector: str) -> str | None:
    """Extract article author."""
    for selector in author_selector.split(","):
        element = soup.select_one(selector.strip())
        if element:
            text = element.get_text(strip=True)
            # Clean up common author prefixes
            text = re.sub(r"^(by|written by|author:)\s*", "", text, flags=re.IGNORECASE)
            if text:
                return text
    return None


def parse_date(text: str, date_format: str = "%Y-%m-%d") -> datetime | None:
    """Parse date from text."""
    try:
        text = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", text)
        return datetime.strptime(text, date_format)
    except ValueError:
        return None


def extract_date(
    soup: BeautifulSoup, date_selector: str, date_format: str = "%Y-%m-%d"
) -> datetime | None:
    """Extract publication date."""
    for selector in date_selector.split(","):
        element = soup.select_one(selector.strip())
        if not element:
            continue

        datetime_attr = element.get("datetime")
        if datetime_attr:
            for format in [
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d",
                date_format,
            ]:
                if date := parse_date(str(datetime_attr), format):
                    return date

        for text in element.find_all(string=True):
            if text and (date := parse_date(str(text).strip(), date_format)):
                return date

    return None


def extract_content_element(
    soup: BeautifulSoup, content_selector: str, article_selector: str
) -> Tag | None:
    """Extract main content element."""
    # Try content selectors first
    for selector in content_selector.split(","):
        element = soup.select_one(selector.strip())
        if element:
            return element

    # Fallback to article selector
    for selector in article_selector.split(","):
        element = soup.select_one(selector.strip())
        if element:
            return element

    # Last resort - use body
    return soup.body


def process_image(url: str, image_dir: pathlib.Path) -> PILImage.Image | None:
    url_hash = hashlib.md5(url.encode()).hexdigest()
    ext = pathlib.Path(urlparse(url).path).suffix or ".jpg"
    filename = f"{url_hash}{ext}"
    local_path = image_dir / filename
    local_path.parent.mkdir(parents=True, exist_ok=True)

    # Download if not already cached
    if not local_path.exists():
        local_path.write_bytes(cast(bytes, fetch_html(url, as_bytes=True)))

    try:
        return PILImage.open(local_path)
    except IOError as e:
        logger.warning(f"Failed to open image as PIL Image {local_path}: {e}")
        return None


def process_images(
    content: Tag | None, base_url: str, image_dir: pathlib.Path
) -> tuple[Tag | None, dict[str, PILImage.Image]]:
    """
    Process all images in content: download them, update URLs, and return PIL Images.

    Returns:
        Tuple of (updated_content, dict_of_pil_images)
    """
    if not content:
        return content, {}

    images = {}

    for img_tag in content.find_all("img"):
        if not isinstance(img_tag, Tag):
            continue

        src = img_tag.get("src", "")
        if not src:
            continue

        try:
            url = to_absolute_url(str(src), base_url)
            image = process_image(url, image_dir)
            if not image:
                continue

            if not image.filename:  # type: ignore
                continue

            path = pathlib.Path(image.filename)  # type: ignore
            img_tag["src"] = str(path.relative_to(settings.FILE_STORAGE_DIR.resolve()))
            images[img_tag["src"]] = image
        except Exception as e:
            logger.warning(f"Failed to process image {src}: {e}")
            continue

    return content, images


def convert_to_markdown(content: Tag | None, base_url: str) -> str:
    """Convert HTML content to Markdown."""
    if not content:
        return ""

    # Update relative URLs to absolute (except for images which were already processed)
    for tag in content.find_all("a"):
        # Ensure we have a Tag object
        if not isinstance(tag, Tag):
            continue

        href = tag.get("href")
        if href:
            tag["href"] = to_absolute_url(str(href), base_url)

    # Convert to markdown
    markdown = md(str(content), heading_style="ATX", bullets="-")

    # Clean up excessive newlines
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)

    return markdown.strip()


def extract_meta_by_pattern(
    soup: BeautifulSoup, selector: dict[str, Any], prefix: str = ""
) -> dict[str, str]:
    """Extract metadata using CSS selector pattern."""
    metadata = {}

    for tag in soup.find_all("meta", **selector):
        if not isinstance(tag, Tag):
            continue

        # Determine the key attribute (property for OG, name for others)
        key_attr = "property" if "property" in selector else "name"
        key = tag.get(key_attr, "")
        content = tag.get("content")

        if key and content:
            # Remove prefix from key and add custom prefix
            clean_key = str(key).replace(prefix.replace(":", ""), "").lstrip(":")
            final_key = (
                f"{prefix.replace(':', '_')}{clean_key}" if prefix else clean_key
            )
            metadata[final_key] = str(content)

    return metadata


def extract_metadata(soup: BeautifulSoup) -> dict[str, Any]:
    """Extract additional metadata from the page."""
    metadata = {}

    # Open Graph metadata
    og_meta = extract_meta_by_pattern(
        soup, {"attrs": {"property": re.compile("^og:")}}, "og:"
    )
    metadata.update(og_meta)

    # Twitter metadata
    twitter_meta = extract_meta_by_pattern(
        soup, {"attrs": {"name": re.compile("^twitter:")}}, "twitter:"
    )
    metadata.update(twitter_meta)

    # Standard meta tags
    standard_tags = ["description", "author", "keywords", "robots"]
    for tag_name in standard_tags:
        tag = soup.find("meta", attrs={"name": tag_name})
        if tag and isinstance(tag, Tag):
            content = tag.get("content")
            if content:
                metadata[tag_name] = str(content)

    return metadata


def extract_url(soup: BeautifulSoup, selectors: str, base_url: str = "") -> str | None:
    for selector in selectors.split(","):
        next_link = soup.select_one(selector)
        if not (next_link and isinstance(next_link, Tag)):
            continue

        if not (href := next_link.get("href")):
            continue

        return to_absolute_url(str(href), base_url)

    return None


def is_substack(soup: BeautifulSoup | Tag) -> bool:
    return any(
        "https://substackcdn.com" == a.attrs.get("href")  # type: ignore
        for a in soup.find_all("link", {"rel": "preconnect"})
        if hasattr(a, "attrs")  # type: ignore
    )


def is_wordpress(soup: BeautifulSoup | Tag) -> bool:
    body_select = "body"
    # Check if this is an archived page
    if contents := soup.select_one("#CONTENT .html"):
        body_select = "#CONTENT .html"
        soup = contents
    return bool(soup.select_one(f"{body_select} .wp-singular"))


def is_bloomberg(soup: BeautifulSoup | Tag) -> bool:
    body_select = "body"
    # Check if this is an archived page
    if contents := soup.select_one("#CONTENT .html"):
        body_select = "#CONTENT .html"
        soup = contents
    urls = [a.attrs.get("href") for a in soup.select(f"{body_select} a")]  # type: ignore
    return any(u.endswith("https://www.bloomberg.com/company/") for u in urls[:5] if u)  # type: ignore


class BaseHTMLParser:
    """Base class for parsing HTML content from websites."""

    # CSS selectors - override in subclasses
    article_selector: str = "article, main, [role='main']"
    title_selector: str = "h1, .title, .post-title"
    author_selector: str = ".author, .by-line, .byline"
    date_selector: str = "time, .date, .published"
    date_format: str = "%Y-%m-%d"
    content_selector: str = ".content, .post-content, .entry-content"
    author: str | None = None

    # Tags to remove from content
    remove_selectors: list[str] = [
        "script",
        "style",
        "nav",
        "aside",
        ".comments",
        ".social-share",
        ".related-posts",
        ".advertisement",
    ]

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url
        self.image_dir = settings.WEBPAGE_STORAGE_DIR / str(urlparse(base_url).netloc)
        self.image_dir.mkdir(parents=True, exist_ok=True)

    def parse(self, html: str, url: str) -> Article:
        """Parse HTML content and return structured article data."""
        soup = BeautifulSoup(html, "html.parser")
        self.base_url = self.base_url or get_base_url(url)

        metadata = self._extract_metadata(soup)
        title = self._extract_title(soup)
        author = self.author or self._extract_author(soup) or metadata.get("author")
        date = self._extract_date(soup)

        self._remove_unwanted_elements(soup)
        content_element = self._extract_content_element(soup)

        updated_content, images = self._process_images(content_element, url)
        content = self._convert_to_markdown(updated_content, url)

        return Article(
            title=title,
            content=content,
            author=author,
            published_date=date,
            url=url,
            images=images,
            metadata=metadata,
        )

    def _get_base_url(self, url: str) -> str:
        """Extract base URL from full URL."""
        return get_base_url(url)

    def _remove_unwanted_elements(self, soup: BeautifulSoup) -> None:
        """Remove unwanted elements from the soup."""
        return remove_unwanted_elements(soup, self.remove_selectors)

    def _extract_title(self, soup: BeautifulSoup) -> str:
        """Extract article title."""
        return extract_title(soup, self.title_selector)

    def _extract_author(self, soup: BeautifulSoup) -> str | None:
        """Extract article author."""
        return extract_author(soup, self.author_selector)

    def _extract_date(self, soup: BeautifulSoup) -> datetime | None:
        """Extract publication date."""
        return extract_date(soup, self.date_selector, self.date_format)

    def _extract_content_element(self, soup: BeautifulSoup) -> Tag | None:
        """Extract main content element."""
        return extract_content_element(
            soup, self.content_selector, self.article_selector
        )

    def _process_images(
        self, content: Tag | None, base_url: str
    ) -> tuple[Tag | None, dict[str, PILImage.Image]]:
        """Process all images: download, update URLs, return PIL Images."""
        return process_images(content, base_url, self.image_dir)

    def _convert_to_markdown(self, content: Tag | None, base_url: str) -> str:
        """Convert HTML content to Markdown."""
        return convert_to_markdown(content, base_url)

    def _extract_metadata(self, soup: BeautifulSoup) -> dict[str, Any]:
        """Extract additional metadata from the page."""
        return extract_metadata(soup)
