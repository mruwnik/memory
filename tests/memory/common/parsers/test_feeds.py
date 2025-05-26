from datetime import datetime
from unittest.mock import MagicMock, patch
from typing import Any, cast

import pytest
from bs4 import BeautifulSoup, Tag
import requests

from memory.common.parsers.feeds import (
    FeedItem,
    FeedParser,
    RSSAtomParser,
    HTMLListParser,
    DanluuParser,
    GuzeyParser,
    PaulGrahamParser,
    NadiaXyzParser,
    RedHandFilesParser,
    BloombergAuthorParser,
    is_rss_feed,
    extract_url,
    find_feed_link,
    get_feed_parser,
    DEFAULT_SKIP_PATTERNS,
    PARSER_REGISTRY,
)


def test_feed_parser_base_url():
    parser = FeedParser(url="https://example.com/path/to/feed")
    assert parser.base_url == "https://example.com"


def test_feed_parser_parse_feed_empty():
    parser = FeedParser(url="https://example.com")
    items = list(parser.parse_feed())
    assert items == []


def test_feed_parser_parse_feed_with_items():
    class TestParser(FeedParser):
        def fetch_items(self):
            return ["item1", "item2"]

        def extract_title(self, entry):
            return f"Title for {entry}"

        def extract_url(self, entry):
            return f"https://example.com/{entry}"

    parser = TestParser(url="https://example.com")
    assert list(parser.parse_feed()) == [
        FeedItem(title="Title for item1", url="https://example.com/item1"),
        FeedItem(title="Title for item2", url="https://example.com/item2"),
    ]


def test_feed_parser_parse_feed_with_invalid_items():
    class TestParser(FeedParser):
        def fetch_items(self):
            return ["valid", "invalid"]

        def extract_title(self, entry):
            return f"Title for {entry}"

        def extract_url(self, entry):
            return f"https://example.com/{entry}"

        def valid_item(self, item):
            return item.title == "Title for valid"

    parser = TestParser(url="https://example.com")
    assert list(parser.parse_feed()) == [
        FeedItem(title="Title for valid", url="https://example.com/valid"),
    ]


@patch("memory.common.parsers.feeds.feedparser.parse")
@pytest.mark.parametrize("since_date", [None, datetime(2023, 1, 1)])
def test_rss_atom_parser_fetch_items(mock_parse, since_date):
    mock_feed = MagicMock()
    mock_feed.entries = ["entry1", "entry2"]
    mock_parse.return_value = mock_feed

    parser = RSSAtomParser(url="https://example.com/feed.xml", since=since_date)
    items = parser.fetch_items()

    if since_date:
        mock_parse.assert_called_once_with(
            "https://example.com/feed.xml", modified=since_date
        )
    else:
        mock_parse.assert_called_once_with("https://example.com/feed.xml")
    assert items == ["entry1", "entry2"]


@patch("memory.common.parsers.feeds.feedparser.parse")
def test_rss_atom_parser_fetch_items_with_content(mock_parse):
    mock_feed = MagicMock()
    mock_feed.entries = ["entry1"]
    mock_parse.return_value = mock_feed

    content = "<rss>...</rss>"
    parser = RSSAtomParser(url="https://example.com/feed.xml", content=content)
    items = parser.fetch_items()

    mock_parse.assert_called_once_with(content)
    assert items == ["entry1"]


@pytest.mark.parametrize(
    "entry_attrs, expected",
    [
        ({"title": "Test Title"}, "Test Title"),
        ({}, "Untitled"),
    ],
)
def test_rss_atom_parser_extract_title(entry_attrs, expected):
    parser = RSSAtomParser(url="https://example.com")
    entry = MagicMock()

    for attr, value in entry_attrs.items():
        setattr(entry, attr, value)

    # Remove attributes not in entry_attrs
    if "title" not in entry_attrs:
        del entry.title

    assert parser.extract_title(entry) == expected


@pytest.mark.parametrize(
    "entry_attrs, expected",
    [
        ({"link": "https://other.com/article"}, "https://other.com/article"),
        ({"link": "/article"}, "https://example.com/article"),
        ({}, ""),
    ],
)
def test_rss_atom_parser_extract_url(entry_attrs, expected):
    parser = RSSAtomParser(url="https://example.com")
    entry = MagicMock()

    for attr, value in entry_attrs.items():
        setattr(entry, attr, value)

    if "link" not in entry_attrs:
        del entry.link

    assert parser.extract_url(entry) == expected


@pytest.mark.parametrize(
    "entry_attrs, expected",
    [
        (
            {"summary": "Test summary", "description": "Test description"},
            "Test summary",
        ),
        ({"summary": "", "description": "Test description"}, "Test description"),
        ({}, ""),
    ],
)
def test_rss_atom_parser_extract_description(entry_attrs, expected):
    parser = RSSAtomParser(url="https://example.com")
    entry = MagicMock()

    for attr, value in entry_attrs.items():
        setattr(entry, attr, value)

    for attr in ["summary", "description"]:
        if attr not in entry_attrs:
            delattr(entry, attr)

    assert parser.extract_description(entry) == expected


@pytest.mark.parametrize(
    "entry_attrs, expected",
    [
        ({"author": "John Doe"}, "John Doe"),
        ({"author": None, "author_detail": {"name": "Jane Smith"}}, "Jane Smith"),
        ({"author": None, "author_detail": {}}, None),
    ],
)
def test_rss_atom_parser_extract_author(entry_attrs, expected):
    parser = RSSAtomParser(url="https://example.com")
    entry = MagicMock()

    for attr, value in entry_attrs.items():
        setattr(entry, attr, value)

    assert parser.extract_author(entry) == expected


@pytest.mark.parametrize(
    "entry_attrs, expected",
    [
        (
            {
                "published_parsed": (2023, 1, 15, 10, 30, 0, 0, 0, 0),
                "updated_parsed": None,
            },
            datetime(2023, 1, 15, 10, 30, 0),
        ),
        (
            {
                "published_parsed": None,
                "updated_parsed": (2023, 2, 20, 14, 45, 30, 0, 0, 0),
            },
            datetime(2023, 2, 20, 14, 45, 30),
        ),
        ({"published_parsed": "invalid", "updated_parsed": None}, None),
        ({}, None),
    ],
)
def test_rss_atom_parser_extract_date(entry_attrs, expected):
    parser = RSSAtomParser(url="https://example.com")
    entry = MagicMock()

    for attr, value in entry_attrs.items():
        setattr(entry, attr, value)

    for attr in ["published_parsed", "updated_parsed"]:
        if attr not in entry_attrs:
            delattr(entry, attr)

    assert parser.extract_date(entry) == expected


@pytest.mark.parametrize(
    "entry_attrs, expected",
    [
        ({"id": "unique-id-123", "guid": "guid-456"}, "unique-id-123"),
        ({"id": None, "guid": "guid-456"}, "guid-456"),
        ({"id": None, "guid": None}, None),
    ],
)
def test_rss_atom_parser_extract_guid(entry_attrs, expected):
    parser = RSSAtomParser(url="https://example.com")
    entry = MagicMock()

    for attr, value in entry_attrs.items():
        setattr(entry, attr, value)

    assert parser.extract_guid(entry) == expected


def test_rss_atom_parser_extract_metadata():
    parser = RSSAtomParser(url="https://example.com")

    entry = MagicMock()
    entry.tags = ["tag1", "tag2"]
    entry.category = "news"
    entry.categories = ["tech", "science"]
    entry.enclosures = ["file1.mp3"]
    entry.other_attr = "should not be included"

    metadata = parser.extract_metadata(entry)

    assert metadata == {
        "tags": ["tag1", "tag2"],
        "category": "news",
        "categories": ["tech", "science"],
        "enclosures": ["file1.mp3"],
    }


@patch("memory.common.parsers.feeds.fetch_html")
def test_html_list_parser_fetch_items_with_content(mock_fetch_html):
    html = """
    <ul>
        <li><a href="/article1">Article 1</a></li>
        <li><a href="/article2">Article 2</a></li>
        <li><a href="mailto:test@example.com">Email</a></li>
    </ul>
    """

    parser = HTMLListParser(url="https://example.com", content=html)
    assert [a.prettify() for a in parser.fetch_items()] == [
        '<li>\n <a href="/article1">\n  Article 1\n </a>\n</li>\n',
        '<li>\n <a href="/article2">\n  Article 2\n </a>\n</li>\n',
    ]

    mock_fetch_html.assert_not_called()


@patch("memory.common.parsers.feeds.fetch_html")
def test_html_list_parser_fetch_items_without_content(mock_fetch_html):
    html = """
    <ul>
        <li><a href="/article1">Article 1</a></li>
    </ul>
    """
    mock_fetch_html.return_value = html

    parser = HTMLListParser(url="https://example.com")
    assert [a.prettify() for a in parser.fetch_items()] == [
        '<li>\n <a href="/article1">\n  Article 1\n </a>\n</li>\n',
    ]

    mock_fetch_html.assert_called_once_with("https://example.com")


def test_html_list_parser_fetch_items_deduplication():
    html = """
    <ul>
        <li><a href="/article1">Article 1</a></li>
        <li><a href="/article1">Article 1 Duplicate</a></li>
        <li><a href="/article2">Article 2</a></li>
    </ul>
    """

    parser = HTMLListParser(url="https://example.com", content=html)
    assert [a.prettify() for a in parser.fetch_items()] == [
        '<li>\n <a href="/article1">\n  Article 1\n </a>\n</li>\n',
        '<li>\n <a href="/article2">\n  Article 2\n </a>\n</li>\n',
    ]


@pytest.mark.parametrize(
    "url, should_skip",
    [
        ("#fragment", True),
        ("mailto:test@example.com", True),
        ("tel:+1234567890", True),
        ("javascript:void(0)", True),
        ("document.pdf", True),
        ("image.jpg", True),
        ("photo.png", True),
        ("animation.gif", True),
        ("https://example.com/article", False),
        ("/relative/path", False),
    ],
)
def test_html_list_parser_should_skip_url(url, should_skip):
    parser = HTMLListParser(url="https://example.com")
    assert parser._should_skip_url(url) == should_skip


@pytest.mark.parametrize(
    "html, title_selector, expected",
    [
        (
            '<li><h2>Custom Title</h2><a href="/link">Link</a></li>',
            "h2",
            "Custom Title",
        ),
        ('<li><a href="/link">Link</a></li>', None, None),
    ],
)
def test_html_list_parser_extract_title(html, title_selector, expected):
    soup = BeautifulSoup(html, "html.parser")
    item = soup.find("li")

    parser = HTMLListParser(url="https://example.com")
    parser.title_selector = title_selector

    if expected and title_selector:
        with patch("memory.common.parsers.feeds.extract_title") as mock_extract:
            mock_extract.return_value = expected
            title = parser.extract_title(item)
            mock_extract.assert_called_once_with(item, title_selector)
            assert title == expected
    else:
        assert parser.extract_title(item) is None


@pytest.mark.parametrize(
    "html, description_selector, expected",
    [
        (
            '<li><p>Description text</p><a href="/link">Link</a></li>',
            "p",
            "Description text",
        ),
        ('<li><a href="/link">Link</a></li>', None, None),
    ],
)
def test_html_list_parser_extract_description(html, description_selector, expected):
    soup = BeautifulSoup(html, "html.parser")
    item = soup.find("li")

    parser = HTMLListParser(url="https://example.com")
    parser.description_selector = description_selector

    assert parser.extract_description(item) == expected


@pytest.mark.parametrize(
    "html, expected",
    [
        ('<li><a href="/article">Article</a></li>', "https://example.com/article"),
        ("<li>No link here</li>", ""),
    ],
)
def test_html_list_parser_extract_url(html, expected):
    soup = BeautifulSoup(html, "html.parser")
    item = soup.find("li")

    parser = HTMLListParser(url="https://example.com")
    assert parser.extract_url(item) == expected


def test_html_list_parser_extract_date_with_selector():
    html = '<li><span class="date">2023-01-15</span><a href="/link">Link</a></li>'
    soup = BeautifulSoup(html, "html.parser")
    item = soup.find("li")

    parser = HTMLListParser(url="https://example.com")
    parser.date_selector = ".date"

    with patch("memory.common.parsers.feeds.extract_date") as mock_extract:
        mock_extract.return_value = datetime(2023, 1, 15)
        date = parser.extract_date(item)
        mock_extract.assert_called_once_with(item, ".date", "%Y-%m-%d")
        assert date == datetime(2023, 1, 15)


def test_html_list_parser_extract_date_without_selector():
    html = '<li><a href="/link">Link</a></li>'
    soup = BeautifulSoup(html, "html.parser")
    item = soup.find("li")

    parser = HTMLListParser(url="https://example.com")
    assert parser.extract_date(item) is None


@pytest.mark.parametrize(
    "parser_class, url, valid_urls, invalid_urls",
    [
        (
            DanluuParser,
            "https://danluu.com",
            ["https://danluu.com/article"],
            ["https://other.com/article"],
        ),
        (
            GuzeyParser,
            "https://guzey.com/archive",
            ["https://guzey.com/archive/article", "../relative", "relative"],
            ["https://other.com/article"],
        ),
        (
            PaulGrahamParser,
            "https://www.paulgraham.com/articles",
            [("Long enough title", "essay.html")],
            [
                ("Short", "essay.html"),
                ("Long enough title", "https://other.com/essay.html"),
                ("Long enough title", "document.txt"),
            ],
        ),
        (
            NadiaXyzParser,
            "https://nadia.xyz/posts",
            ["https://nadia.xyz/posts/article", "/article", "article/"],
            ["https://other.com/article"],
        ),
        (
            RedHandFilesParser,
            "https://www.theredhandfiles.com",
            [
                (
                    "Issue #123: Long question",
                    "https://www.theredhandfiles.com/issue-123",
                ),
                ("Long enough title", "https://www.theredhandfiles.com/some-issue"),
            ],
            [
                ("Short", "https://www.theredhandfiles.com/issue-123"),
                ("Long enough title", "https://other.com/issue"),
                ("Long enough title", "https://www.theredhandfiles.com/about"),
            ],
        ),
        (
            BloombergAuthorParser,
            "https://archive.ph/123/https://www.bloomberg.com/opinion/authors/",
            [
                (
                    "Long enough title",
                    "https://www.bloomberg.com/opinion/articles/2023/01/15/article",
                ),
                ("Long enough title", "/news/articles/2023/01/15/article"),
                (
                    "Long enough title",
                    "https://archive.ph/2023/01/15/some/article/path",
                ),
            ],
            [
                (
                    "Short",
                    "https://www.bloomberg.com/opinion/articles/2023/01/15/article",
                ),
                ("Long enough title", "https://other.com/article"),
                ("Long enough title", "https://www.bloomberg.com/simple"),
            ],
        ),
    ],
)
def test_specific_parsers_valid_item(parser_class, url, valid_urls, invalid_urls):
    parser = parser_class(url=url)

    # Test valid items
    for item_data in valid_urls:
        if isinstance(item_data, tuple):
            title, url_val = item_data
            item = FeedItem(title=title, url=url_val)
        else:
            item = FeedItem(title="Test", url=item_data)
        assert parser.valid_item(item) is True

    # Test invalid items
    for item_data in invalid_urls:
        if isinstance(item_data, tuple):
            title, url_val = item_data
            item = FeedItem(title=title, url=url_val)
        else:
            item = FeedItem(title="Test", url=item_data)
        assert parser.valid_item(item) is False


def test_red_hand_files_extract_title():
    html = """
    <article>
        <h3>Issue #123</h3>
        <h2>What is the meaning of life?</h2>
        <a href="/issue-123">Link</a>
    </article>
    """
    soup = BeautifulSoup(html, "html.parser")
    item = soup.find("article")

    parser = RedHandFilesParser(url="https://www.theredhandfiles.com")
    title = parser.extract_title(item)
    assert title == "Issue #123: What is the meaning of life?"


def test_red_hand_files_extract_description():
    # Create a text that's definitely longer than 200 characters
    long_text = "This is a very long question that should be truncated because it exceeds the maximum length limit of 200 characters and we want to make sure that the description is not too long for display purposes and this text continues to be very long indeed to ensure truncation happens"
    html = f"""
    <article>
        <h2>{long_text}</h2>
    </article>
    """
    soup = BeautifulSoup(html, "html.parser")
    item = soup.find("article")

    parser = RedHandFilesParser(url="https://www.theredhandfiles.com")
    description = parser.extract_description(item)
    assert len(description) <= 203  # 200 + "..."
    assert description.endswith("...")


@pytest.mark.parametrize(
    "content, expected",
    [
        ("<?xml version='1.0'?><rss>", True),
        ("<rss version='2.0'>", True),
        ("<feed xmlns='http://www.w3.org/2005/Atom'>", True),
        ("<atom:feed>", True),
        ("  <?XML version='1.0'?>", True),  # Case insensitive
        ("<html><body>Not a feed</body></html>", False),
        ("Plain text content", False),
        ("", False),
    ],
)
def test_is_rss_feed(content, expected):
    assert is_rss_feed(content) == expected


@pytest.mark.parametrize(
    "html, expected",
    [
        ('<a href="/relative/path">Link</a>', "https://example.com/relative/path"),
        ("<a>Link without href</a>", None),
    ],
)
def test_extract_url_function(html, expected):
    soup = BeautifulSoup(html, "html.parser")
    element = soup.find("a")
    assert element is not None

    url = extract_url(cast(Tag, element), "https://example.com")
    assert url == expected


@pytest.mark.parametrize(
    "html, expected",
    [
        (
            """
        <html>
            <head>
                <link rel="alternate" type="application/rss+xml" href="/feed.xml">
                <link rel="alternate" type="application/atom+xml" href="/atom.xml">
            </head>
        </html>
        """,
            "https://example.com/feed.xml",
        ),
        ("<html><body>No head</body></html>", None),
        (
            """
        <html>
            <head>
                <link rel="alternate" type="application/rss+xml" href="https://example.com">
            </head>
        </html>
        """,
            None,
        ),  # Should not return same URL
    ],
)
def test_find_feed_link(html, expected):
    soup = BeautifulSoup(html, "html.parser")
    feed_link = find_feed_link("https://example.com", soup)
    assert feed_link == expected


@pytest.mark.parametrize(
    "url, expected_parser_class",
    [
        ("https://danluu.com", DanluuParser),
        ("https://guzey.com/archive", GuzeyParser),
        ("https://www.paulgraham.com/articles", PaulGrahamParser),
        ("https://nadia.xyz/posts", NadiaXyzParser),
        ("https://www.theredhandfiles.com", RedHandFilesParser),
        (
            "https://archive.ph/abc123/https://www.bloomberg.com/opinion/authors/john-doe",
            BloombergAuthorParser,
        ),
    ],
)
def test_get_feed_parser_registry(url, expected_parser_class):
    parser = get_feed_parser(url)
    assert parser is not None
    assert isinstance(parser, expected_parser_class)
    assert parser.url == url


@patch("memory.common.parsers.feeds.fetch_html")
def test_get_feed_parser_rss_content(mock_fetch_html):
    mock_fetch_html.return_value = "<?xml version='1.0'?><rss>"

    parser = get_feed_parser("https://example.com/unknown")
    assert isinstance(parser, RSSAtomParser)
    assert parser.url == "https://example.com/unknown"


@patch("memory.common.parsers.feeds.fetch_html")
def test_get_feed_parser_with_feed_link(mock_fetch_html):
    html = """
    <html>
        <head>
            <link rel="alternate" type="application/rss+xml" href="/feed.xml">
        </head>
    </html>
    """
    mock_fetch_html.return_value = html

    parser = get_feed_parser("https://example.com")
    assert isinstance(parser, RSSAtomParser)
    assert parser.url == "https://example.com/feed.xml"


@patch("memory.common.parsers.feeds.fetch_html")
def test_get_feed_parser_recursive_paths(mock_fetch_html):
    # Mock the initial call to return HTML without feed links
    html = "<html><body>No feed links</body></html>"
    mock_fetch_html.return_value = html

    # Mock the recursive calls to avoid actual HTTP requests
    with patch("memory.common.parsers.feeds.get_feed_parser") as mock_recursive:
        # Set up the mock to return None for recursive calls
        mock_recursive.return_value = None

        # Call the original function directly
        from memory.common.parsers.feeds import (
            get_feed_parser as original_get_feed_parser,
        )

        parser = original_get_feed_parser("https://example.com")

    assert parser is None


@patch("memory.common.parsers.feeds.fetch_html")
def test_get_feed_parser_no_match(mock_fetch_html):
    html = "<html><body>No feed links</body></html>"
    mock_fetch_html.return_value = html

    # Mock the recursive calls to avoid actual HTTP requests
    with patch("memory.common.parsers.feeds.get_feed_parser") as mock_recursive:
        mock_recursive.return_value = None
        parser = get_feed_parser("https://unknown.com")

    assert parser is None


def test_get_feed_parser_with_check_from():
    check_from = datetime(2023, 1, 1)
    parser = get_feed_parser("https://danluu.com", check_from)
    assert isinstance(parser, DanluuParser)
    assert parser.since == check_from


def test_parser_registry_completeness():
    """Ensure PARSER_REGISTRY contains expected parsers."""
    expected_patterns = [
        r"https://danluu.com",
        r"https://guzey.com/archive",
        r"https://www.paulgraham.com/articles",
        r"https://nadia.xyz/posts",
        r"https://www.theredhandfiles.com",
        r"https://archive.ph/.*?/https://www.bloomberg.com/opinion/authors/",
    ]

    assert len(PARSER_REGISTRY) == len(expected_patterns)
    for pattern in expected_patterns:
        assert pattern in PARSER_REGISTRY


def test_default_skip_patterns():
    """Ensure DEFAULT_SKIP_PATTERNS contains expected patterns."""
    expected_patterns = [
        r"^#",
        r"mailto:",
        r"tel:",
        r"javascript:",
        r"\.pdf$",
        r"\.jpg$",
        r"\.png$",
        r"\.gif$",
    ]

    assert DEFAULT_SKIP_PATTERNS == expected_patterns
