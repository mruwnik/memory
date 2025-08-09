import logging
import re
from datetime import datetime
from urllib.parse import urlparse
from typing import cast

from bs4 import BeautifulSoup, Tag

from memory.parsers.html import (
    BaseHTMLParser,
    Article,
    parse_date,
    extract_title,
    extract_date,
    fetch_html,
    is_wordpress,
    is_substack,
    is_bloomberg,
)


logger = logging.getLogger(__name__)


class SubstackParser(BaseHTMLParser):
    """Parser specifically for Substack articles."""

    article_selector = "article.post"
    title_selector = "h1.post-title, h1"
    author_selector = ".post-header .author-name, .byline-names"
    date_selector = ".post-header"
    date_format = "%b %d, %Y"
    content_selector = ".available-content, .post-content"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".subscribe-widget",
        ".subscription-widget-wrap",
        ".post-footer",
        ".share-dialog",
        ".comments-section",
    ]


class WordPressParser(BaseHTMLParser):
    """Parser for WordPress blogs with common themes."""

    article_selector = "article, .post, .hentry"
    title_selector = ".entry-title, h1.post-title, h1"
    author_selector = ".entry-meta .author, .by-author, .author-name, .by"
    date_selector = ".entry-meta .entry-date, .post-date, time[datetime]"
    date_format = "%b %d, %Y"
    content_selector = ".entry-content, .post-content, .content"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".sharedaddy",
        ".jp-relatedposts",
        ".post-navigation",
        ".author-bio",
    ]


class MediumParser(BaseHTMLParser):
    """Parser for Medium articles."""

    article_selector = "article"
    title_selector = "h1"
    author_selector = "[data-testid='authorName']"
    date_selector = "[data-testid='storyPublishDate']"
    content_selector = "section"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        "[data-testid='audioPlayButton']",
        "[data-testid='headerClapButton']",
        "[data-testid='responsesSection']",
    ]


class AcoupBlogParser(BaseHTMLParser):
    """Parser for acoup.blog (A Collection of Unmitigated Pedantry)."""

    article_selector = "article, .post, .entry"
    title_selector = "h1.entry-title, h1"
    author_selector = ".entry-meta .author, .byline"
    date_selector = ".entry-meta .posted-on, .entry-date"
    date_format = "%B %d, %Y"  # "May 23, 2025" format
    content_selector = ".entry-content, .post-content"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".entry-meta",
        ".post-navigation",
        ".related-posts",
        ".social-sharing",
        ".comments-area",
    ]


class GuzeyParser(BaseHTMLParser):
    """Parser for guzey.com personal blog."""

    article_selector = "main, .content, body"
    title_selector = "h1.article-title"
    author_selector = ".author, .byline"  # Fallback, likely will use metadata
    date_selector = ".post-date time"
    date_format = "%Y-%m-%d"  # Based on "2018-08-07" format seen
    content_selector = "main, .post-content, .content"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".header",
        ".navigation",
        ".sidebar",
        ".footer",
        ".date-info",  # Remove the "created:/modified:" lines
        "hr",  # Remove horizontal rules that separate sections
    ]


class AkarlinParser(BaseHTMLParser):
    """Parser for akarlin.com (Anatoly Karlin's blog)."""

    article_selector = "article, .entry-content, main"
    title_selector = "h1.entry-title, h1"
    author_selector = ".entry-meta .author, .author-name"
    date_selector = ".posted-on .published, .post-date"
    date_format = "%B %d, %Y"  # "December 31, 2023" format
    content_selector = ".entry-content, .post-content, article"
    author = "Anatoly Karlin"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".entry-meta",
        ".post-navigation",
        ".author-bio",
        ".related-posts",
        ".comments",
        ".wp-block-group",  # WordPress blocks
        "header",
        "footer",
        ".site-header",
        ".site-footer",
    ]


class AphyrParser(BaseHTMLParser):
    """Parser for aphyr.com (Kyle Kingsbury's blog)."""

    article_selector = "article, .post, main"
    title_selector = "h1"
    author_selector = ".author, .byline"
    date_selector = ".date, time"
    date_format = "%Y-%m-%d"  # "2025-05-21" format
    content_selector = ".content, .post-content, article"
    author = "Kyle Kingsbury"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".comments",
        ".comment-form",
        "form",
        ".post-navigation",
        ".tags",
        ".categories",
        "header nav",
        "footer",
        ".copyright",
    ]


class AppliedDivinityStudiesParser(BaseHTMLParser):
    """Parser for applieddivinitystudies.com."""

    article_selector = "article, .post, main, .content"
    title_selector = "h1"
    author_selector = ".author, .byline"
    date_selector = ".date, time"
    date_format = "%Y-%m-%d"  # "2025-05-10" format
    content_selector = ".content, .post-content, article, main"
    author = "Applied Divinity Studies"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".header",
        ".site-header",
        ".navigation",
        ".footer",
        ".site-footer",
        ".subscribe",
        ".about",
        ".archives",
        ".previous-post",
        ".next-post",
    ]


class BitsAboutMoneyParser(BaseHTMLParser):
    """Parser for bitsaboutmoney.com (Patrick McKenzie's blog)."""

    article_selector = "article, .post, main"
    title_selector = "h1"
    author_selector = ".author, .byline"
    date_selector = ".date, time"
    date_format = "%b %d, %Y"
    content_selector = ".content, .post-content, article"
    author = "Patrick McKenzie (patio11)"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".header",
        ".site-header",
        ".navigation",
        ".footer",
        ".site-footer",
        ".newsletter-signup",
        ".subscribe",
        ".memberships",
        ".author-bio",
        ".next-post",
        ".prev-post",
    ]


class DanLuuParser(BaseHTMLParser):
    """Parser for danluu.com (Dan Luu's technical blog)."""

    article_selector = "main, article, .content"
    title_selector = "h1"
    author_selector = ".author, .byline"
    date_selector = ".date, time"
    date_format = "%Y-%m-%d"
    content_selector = "main, article, .content"
    author = "Dan Luu"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".header",
        ".footer",
        ".navigation",
        ".site-nav",
        ".archive-links",
        ".patreon-links",
        ".social-links",
    ]


class McFunleyParser(BaseHTMLParser):
    """Parser for mcfunley.com (Dan McKinley's blog)."""

    article_selector = "main, article, .content"
    title_selector = "h4, h1"  # Uses h4 for titles based on the content
    author_selector = ".author, .byline"
    date_selector = ".post-heading small, .date, time"
    date_format = "%B %d, %Y"  # "February 9th, 2017" format - will be handled by ordinal stripping
    content_selector = "main, article, .content"
    author = "Dan McKinley"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".header",
        ".footer",
        ".navigation",
        ".social-links",
        ".copyright",
    ]


class ExUrbeParser(BaseHTMLParser):
    """Parser for exurbe.com (Ada Palmer's history blog)."""

    article_selector = "article, .post, main"
    title_selector = "h1, h2.entry-title"
    author_selector = ".author, .byline"
    date_selector = ".post_date_time .published"
    date_format = "%B %d, %Y"  # "June 4, 2020" format
    content_selector = ".entry-content, .post-content, article"
    author = "Ada Palmer"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".widget",
        ".sidebar",
        ".navigation",
        ".site-header",
        ".site-footer",
        ".entry-meta",
        ".post-navigation",
        ".related-posts",
        ".comments-area",
        ".search-form",
        ".recommended-posts",
        ".categories",
        ".tags",
    ]

    def _extract_date(self, soup: BeautifulSoup) -> datetime | None:
        """Extract date, handling ordinal formats like 'Mar 5th, 2025'."""
        date = soup.select_one(".published")
        if date:
            return date.attrs.get("content")  # type: ignore
        return super()._extract_date(soup)


class FlyingMachineStudiosParser(BaseHTMLParser):
    """Parser for flyingmachinestudios.com (Daniel Higginbotham's blog)."""

    article_selector = "article, .post, main"
    title_selector = "h1"
    author_selector = ".author, .byline"
    date_selector = ".date, time"
    date_format = "%d %B %Y"  # "13 August 2019" format
    content_selector = ".content, .post-content, article"
    author = "Daniel Higginbotham"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".header",
        ".footer",
        ".navigation",
        ".sidebar",
        ".popular-posts",
        ".recent-posts",
        ".projects",
        ".comments",
        ".social-sharing",
    ]


class RiftersParser(BaseHTMLParser):
    """Parser for rifters.com (Peter Watts' blog)."""

    article_selector = "article, .post, .entry"
    title_selector = "h2.entry-title, h1"
    author_selector = ".author, .byline"
    date_selector = ".entry-date, .post-date"
    date_format = "%d %B %Y"  # "12 May 2025" format
    content_selector = ".entry-content, .post-content"
    author = "Peter Watts"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".sidebar",
        ".widget",
        ".navigation",
        ".site-header",
        ".site-footer",
        ".entry-meta",
        ".post-navigation",
        ".comments",
        ".related-posts",
        ".categories",
        ".tags",
        ".rss-links",
    ]

    def _extract_date(self, soup: BeautifulSoup) -> datetime | None:
        """Extract date, handling ordinal formats like 'Mar 5th, 2025'."""
        date = soup.select_one(".entry-date")
        if not date:
            return None
        date_str = date.text.replace("\n", " ").strip()
        if date := parse_date(date_str, "%d %b %Y"):
            return date
        return None


class PaulGrahamParser(BaseHTMLParser):
    """Parser for paulgraham.com (Paul Graham's essays)."""

    article_selector = "table, td, body"
    title_selector = (
        "img[alt], h1, title"  # PG essays often have titles in image alt text
    )
    author_selector = ".author, .byline"
    date_selector = ".date, time"
    date_format = "%B %Y"  # "March 2024" format
    content_selector = "table td, body"
    author = "Paul Graham"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        "img[src*='trans_1x1.gif']",  # Remove spacer images
        "img[src*='essays-']",  # Remove header graphics
        ".navigation",
        ".header",
        ".footer",
    ]

    def _extract_title(self, soup: BeautifulSoup) -> str:
        """Extract title from image alt text or other sources."""
        # Check for title in image alt attribute (common in PG essays)
        img_with_alt = soup.find("img", alt=True)
        if img_with_alt and isinstance(img_with_alt, Tag):
            alt_text = img_with_alt.get("alt")
            if alt_text:
                return str(alt_text)

        # Fallback to standard title extraction
        return extract_title(soup, self.title_selector)

    def _extract_date(self, soup: BeautifulSoup) -> datetime | None:
        """Extract date from essay content."""
        # Look for date patterns in the text content (often at the beginning)
        text_content = soup.get_text()

        # Look for patterns like "March 2024" at the start
        date_match = re.search(r"\b([A-Z][a-z]+ \d{4})\b", text_content[:500])
        if date_match:
            date_str = date_match.group(1)
            if date := parse_date(date_str, self.date_format):
                return date

        return extract_date(soup, self.date_selector, self.date_format)


class PutanumonitParser(BaseHTMLParser):
    """Parser for putanumonit.com (Jacob Falkovich's rationality blog)."""

    article_selector = "article, .post, .entry"
    title_selector = "h1.entry-title, h1"
    author_selector = ".entry-meta .author, .author-name"
    date_selector = ".entry-meta .entry-date, .posted-on"
    date_format = "%B %d, %Y"  # "August 19, 2023" format
    content_selector = ".entry-content, .post-content"
    author = "Jacob Falkovich"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".widget",
        ".sidebar",
        ".navigation",
        ".site-header",
        ".site-footer",
        ".entry-meta",
        ".post-navigation",
        ".related-posts",
        ".comments-area",
        ".wp-block-group",
        ".categories",
        ".tags",
        ".monthly-archives",
        ".recent-posts",
        ".recent-comments",
        ".subscription-widget-wrap",
        ".reblog-subscribe",
    ]


class TheRedHandFilesParser(BaseHTMLParser):
    """Parser for theredhandfiles.com (Nick Cave's Q&A website)."""

    article_selector = "article, .post, main"
    title_selector = "h1"
    author_selector = ""
    date_selector = ".issue-date, .date"
    date_format = "%B %Y"  # "May 2025" format
    content_selector = ".content, .post-content, main"
    author = "Nick Cave"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".header",
        ".site-header",
        ".navigation",
        ".footer",
        ".site-footer",
        ".sidebar",
        ".recent-posts",
        ".subscription",
        ".ask-question",
        ".privacy-policy",
    ]

    def _extract_date(self, soup: BeautifulSoup) -> datetime | None:
        """Extract date from issue header."""
        # Look for issue date pattern like "Issue #325 / May 2025"
        text_content = soup.get_text()

        # Look for patterns like "Issue #XXX / Month Year"
        date_match = re.search(r"Issue #\d+ / ([A-Z][a-z]+ \d{4})", text_content)
        if date_match:
            date_str = date_match.group(1)
            if date := parse_date(date_str, self.date_format):
                return date

        # Fallback to parent method
        return extract_date(soup, self.date_selector, self.date_format)


class RachelByTheBayParser(BaseHTMLParser):
    """Parser for rachelbythebay.com technical blog."""

    article_selector = "body, main, .content"
    title_selector = "title, h1"
    author_selector = ".author, .byline"
    date_selector = ".date, time"
    date_format = "%A, %B %d, %Y"
    content_selector = "body, main, .content"
    author = "Rachel Kroll"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".header",
        ".footer",
        ".navigation",
        ".sidebar",
        ".comments",
    ]

    def _extract_date(self, soup: BeautifulSoup) -> datetime | None:
        """Extract date from URL structure if available."""
        # Try to get current URL from canonical link or other sources
        canonical = soup.find("link", rel="canonical")
        if canonical and isinstance(canonical, Tag):
            href = canonical.get("href")
            if href:
                # Look for date pattern in URL like /2025/05/22/
                date_match = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", str(href))
                if date_match:
                    year, month, day = date_match.groups()
                    date_str = f"{year}/{month}/{day}"
                    if date := parse_date(date_str, self.date_format):
                        return date

        # Fallback to parent method
        return extract_date(soup, self.date_selector, self.date_format)


class NadiaXyzParser(BaseHTMLParser):
    """Parser for nadia.xyz (Nadia Asparouhova's blog)."""

    article_selector = "main, article, body"
    title_selector = "h1"
    author_selector = ".author, .byline"
    date_selector = ".post__date"
    date_format = "%B %d, %Y"  # "May 3, 2018" format
    content_selector = "main, article, body"
    author = "Nadia Asparouhova"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".header",
        ".navigation",
        ".footer",
        ".sidebar",
        ".menu",
        ".nav",
        "nav",
    ]


class SlateStarCodexParser(BaseHTMLParser):
    """Parser for slatestarcodex.com (Scott Alexander's blog)."""

    article_selector = ".post, .hentry, [id^='post-']"
    title_selector = "h1.pjgm-posttitle, h1"
    author_selector = ".author.vcard a, .url.fn.n"
    date_selector = ".entry-date"
    date_format = "%B %d, %Y"  # "January 21, 2021" format
    content_selector = ".pjgm-postcontent"
    author = "Scott Alexander"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".pjgm-postmeta",
        ".pjgm-postutility",
        ".pjgm-navigation",
        "#pjgm-navbelow",
        "#comments",
        ".commentlist",
        ".widget-area",
        "#left-sidebar",
        "#primary",
        ".sidebar-toggle",
        ".aar_div",  # Advertisement divs
        ".pjgm-header",
        ".pjgm-footer",
        "#pjgm-menubar",
        "#pjgm-bigtitle",
    ]


class BloombergParser(BaseHTMLParser):
    """Parser for bloomberg.com."""

    article_selector = "main, article, body, #content"
    title_selector = "h1, title"
    author_selector = ".author, .byline, .post-author"
    date_selector = ".date, .published, time"
    content_selector = "main, article, body, #content"

    remove_selectors = BaseHTMLParser.remove_selectors + [
        ".archive-banner",
        ".archive-header",
        ".wayback-banner",
        ".archive-notice",
        "#wm-ipp",  # Wayback machine banner
        ".archive-toolbar",
        ".archive-metadata",
    ]

    def _extract_author(self, soup: BeautifulSoup) -> str | None:
        if author := soup.find("a", attrs={"rel": "author"}):
            return author.text.strip()
        return super()._extract_author(soup)


PARSER_REGISTRY = {
    r"\.substack\.com": SubstackParser,
    r"substack\.com": SubstackParser,
    r"medium\.com": MediumParser,
    r"wordpress\.com": WordPressParser,
    r"acoup\.blog": AcoupBlogParser,
    r"guzey\.com": GuzeyParser,
    r"akarlin\.com": AkarlinParser,
    r"aphyr\.com": AphyrParser,
    r"applieddivinitystudies\.com": AppliedDivinityStudiesParser,
    r"bitsaboutmoney\.com": BitsAboutMoneyParser,
    r"danluu\.com": DanLuuParser,
    r"mcfunley\.com": McFunleyParser,
    r"exurbe\.com": ExUrbeParser,
    r"flyingmachinestudios\.com": FlyingMachineStudiosParser,
    r"rifters\.com": RiftersParser,
    r"paulgraham\.com": PaulGrahamParser,
    r"putanumonit\.com": PutanumonitParser,
    r"theredhandfiles\.com": TheRedHandFilesParser,
    r"rachelbythebay\.com": RachelByTheBayParser,
    r"nadia\.xyz": NadiaXyzParser,
    r"slatestarcodex\.com": SlateStarCodexParser,
    r"nayafia\.substack\.com": SubstackParser,
    r"homosabiens\.substack\.com": SubstackParser,
    r"usefulfictions\.substack\.com": SubstackParser,
}


def get_parser_for_url(url: str, html: str) -> BaseHTMLParser:
    """Get the appropriate parser for a given URL."""
    domain = urlparse(url).netloc

    for pattern, parser_class in PARSER_REGISTRY.items():
        if re.search(pattern, domain):
            return parser_class(url)

    soup = BeautifulSoup(html, "html.parser")
    if is_wordpress(soup):
        return WordPressParser(url)

    if is_substack(soup):
        return SubstackParser(url)

    if is_bloomberg(soup):
        return BloombergParser(url)

    return BaseHTMLParser(url)


def parse_webpage(url: str) -> Article:
    """
    Parse a webpage and extract article content.

    Args:
        url: URL of the webpage to parse

    Returns:
        Article object with extracted content and metadata
    """
    html = cast(str, fetch_html(url))
    parser = get_parser_for_url(url, html)
    return parser.parse(html, url)
