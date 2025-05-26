from unittest.mock import patch
from urllib.parse import urlparse, parse_qs

import pytest

from memory.common.parsers.archives import (
    ArchiveFetcher,
    LinkFetcher,
    HTMLArchiveFetcher,
    SubstackArchiveFetcher,
    ACOUPArchiveFetcher,
    HTMLNextUrlArchiveFetcher,
    html_parser,
    get_archive_fetcher,
    FETCHER_REGISTRY,
)
from memory.common.parsers.feeds import (
    FeedItem,
    FeedParser,
    HTMLListParser,
    DanluuParser,
    SubstackAPIParser,
)


class MockParser(FeedParser):
    def __init__(
        self, url: str, items: list[FeedItem] | None = None, content: str = ""
    ):
        super().__init__(url)
        self.items = items or []
        self.content = content

    def parse_feed(self):
        return self.items


def test_archive_fetcher_make_parser():
    fetcher = ArchiveFetcher(
        parser_class=MockParser,
        start_url="https://example.com",
        parser_kwargs={"custom_attr": "value"},
    )

    parser = fetcher.make_parser("https://example.com/page1")

    assert isinstance(parser, MockParser)
    assert parser.url == "https://example.com/page1"
    assert getattr(parser, "custom_attr") == "value"


def test_archive_fetcher_find_next_page_base():
    fetcher = ArchiveFetcher(MockParser, "https://example.com")
    parser = MockParser("https://example.com")

    assert fetcher._find_next_page(parser, 0) is None


@patch("memory.common.parsers.archives.time.sleep")
def test_archive_fetcher_fetch_all_items_single_page(mock_sleep):
    items = [
        FeedItem(title="Item 1", url="https://example.com/1"),
        FeedItem(title="Item 2", url="https://example.com/2"),
    ]

    fetcher = ArchiveFetcher(
        parser_class=MockParser,
        start_url="https://example.com",
        delay_between_requests=0.5,
    )

    with patch.object(fetcher, "make_parser") as mock_make_parser:
        mock_parser = MockParser("https://example.com", items)
        mock_make_parser.return_value = mock_parser

        result = list(fetcher.fetch_all_items())

        assert result == items
        mock_make_parser.assert_called_once_with("https://example.com")
        mock_sleep.assert_not_called()  # No delay for single page


@patch("memory.common.parsers.archives.time.sleep")
def test_archive_fetcher_fetch_all_items_multiple_pages(mock_sleep):
    page1_items = [FeedItem(title="Item 1", url="https://example.com/1")]
    page2_items = [FeedItem(title="Item 2", url="https://example.com/2")]

    class TestFetcher(ArchiveFetcher):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.call_count = 0

        def _find_next_page(self, parser, current_page=0):
            self.call_count += 1
            if self.call_count == 1:
                return "https://example.com/page2"
            return None

    fetcher = TestFetcher(
        parser_class=MockParser,
        start_url="https://example.com",
        delay_between_requests=0.1,
    )

    with patch.object(fetcher, "make_parser") as mock_make_parser:
        mock_make_parser.side_effect = [
            MockParser("https://example.com", page1_items),
            MockParser("https://example.com/page2", page2_items),
        ]

        result = list(fetcher.fetch_all_items())

        assert result == page1_items + page2_items
        assert mock_make_parser.call_count == 2
        mock_sleep.assert_called_once_with(0.1)


def test_archive_fetcher_fetch_all_items_max_pages():
    class TestFetcher(ArchiveFetcher):
        def _find_next_page(self, parser, current_page=0):
            return f"https://example.com/page{current_page + 2}"

    fetcher = TestFetcher(
        parser_class=MockParser,
        start_url="https://example.com",
        max_pages=2,
        delay_between_requests=0,
    )

    items = [FeedItem(title="Item", url="https://example.com/item")]

    with patch.object(fetcher, "make_parser") as mock_make_parser:
        mock_make_parser.return_value = MockParser("https://example.com", items)

        result = list(fetcher.fetch_all_items())

        assert len(result) == 2  # 2 pages * 1 item per page
        assert mock_make_parser.call_count == 2


def test_archive_fetcher_fetch_all_items_visited_url():
    class TestFetcher(ArchiveFetcher):
        def _find_next_page(self, parser, current_page=0):
            return "https://example.com"  # Return same URL to trigger visited check

    fetcher = TestFetcher(MockParser, "https://example.com", delay_between_requests=0)
    items = [FeedItem(title="Item", url="https://example.com/item")]

    with patch.object(fetcher, "make_parser") as mock_make_parser:
        mock_make_parser.return_value = MockParser("https://example.com", items)

        result = list(fetcher.fetch_all_items())

        assert len(result) == 1  # Only first page processed
        mock_make_parser.assert_called_once()


def test_archive_fetcher_fetch_all_items_no_items():
    fetcher = ArchiveFetcher(
        MockParser, "https://example.com", delay_between_requests=0
    )

    with patch.object(fetcher, "make_parser") as mock_make_parser:
        mock_make_parser.return_value = MockParser("https://example.com", [])

        result = list(fetcher.fetch_all_items())

        assert result == []
        mock_make_parser.assert_called_once()


def test_archive_fetcher_fetch_all_items_exception():
    fetcher = ArchiveFetcher(
        MockParser, "https://example.com", delay_between_requests=0
    )

    with patch.object(fetcher, "make_parser") as mock_make_parser:
        mock_make_parser.side_effect = Exception("Network error")

        result = list(fetcher.fetch_all_items())

        assert result == []


@pytest.mark.parametrize(
    "start_url, per_page, current_page, expected_params",
    [
        ("https://example.com", 10, 0, {"offset": ["10"], "limit": ["10"]}),
        (
            "https://example.com?existing=value",
            20,
            1,
            {"existing": ["value"], "offset": ["40"], "limit": ["20"]},
        ),
        (
            "https://example.com?offset=0&limit=5",
            15,
            2,
            {"offset": ["45"], "limit": ["15"]},
        ),
    ],
)
def test_link_fetcher_find_next_page(
    start_url, per_page, current_page, expected_params
):
    fetcher = LinkFetcher(MockParser, start_url, per_page=per_page)
    parser = MockParser(start_url)

    next_url = fetcher._find_next_page(parser, current_page)

    assert next_url is not None
    parsed = urlparse(next_url)
    params = parse_qs(parsed.query)

    for key, value in expected_params.items():
        assert params[key] == value


@pytest.mark.parametrize(
    "html, selectors, expected_url",
    [
        (
            '<a rel="next" href="/page2">Next</a>',
            ['a[rel="next"]'],
            "https://example.com/page2",
        ),
        (
            '<div class="next"><a href="/page2">Next</a></div>',
            [".next a"],
            "https://example.com/page2",
        ),
        (
            '<a class="next" href="/page2">Next</a>',
            ["a.next"],
            "https://example.com/page2",
        ),
        (
            '<div class="pagination"><span class="next"><a href="/page2">Next</a></span></div>',
            [".pagination .next"],
            None,  # This won't match because it's looking for .pagination .next directly
        ),
        (
            '<div class="pagination next"><a href="/page2">Next</a></div>',
            [".pagination.next"],
            None,  # This selector isn't in default list
        ),
        (
            '<nav class="page"><a href="/page1">1</a><a href="/page2">2</a></nav>',
            ["nav.page a:last-of-type"],
            "https://example.com/page2",
        ),
        ("<div>No next link</div>", ['a[rel="next"]'], None),
    ],
)
def test_html_archive_fetcher_find_next_page(html, selectors, expected_url):
    fetcher = HTMLArchiveFetcher(
        MockParser, "https://example.com", next_page_selectors=selectors
    )
    parser = MockParser("https://example.com", content=html)

    with patch("memory.common.parsers.archives.extract_url") as mock_extract:
        mock_extract.return_value = expected_url

        result = fetcher._find_next_page(parser)

        if expected_url:
            mock_extract.assert_called_once()
            assert result == expected_url
        else:
            # extract_url might still be called but return None
            assert result is None


def test_html_archive_fetcher_find_next_page_no_content():
    fetcher = HTMLArchiveFetcher(MockParser, "https://example.com")
    parser = MockParser("https://example.com", content="")

    result = fetcher._find_next_page(parser)

    assert result is None


def test_html_parser_factory():
    CustomParser = html_parser(
        item_selector="article", title_selector="h1", custom_attr="value"
    )

    parser = CustomParser("https://example.com")

    assert isinstance(parser, HTMLListParser)
    assert parser.item_selector == "article"
    assert parser.title_selector == "h1"
    assert getattr(parser, "custom_attr") == "value"


@pytest.mark.parametrize(
    "start_url, expected_api_url",
    [
        ("https://example.substack.com", "https://example.substack.com/api/v1/archive"),
        (
            "https://example.substack.com/posts",
            "https://example.substack.com/api/v1/archive",
        ),
        (
            "https://example.substack.com/api/v1/archive",
            "https://example.substack.com/api/v1/archive",
        ),
    ],
)
def test_substack_archive_fetcher_post_init(start_url, expected_api_url):
    with patch("memory.common.parsers.archives.get_base_url") as mock_get_base:
        mock_get_base.return_value = "https://example.substack.com"

        fetcher = SubstackArchiveFetcher(SubstackAPIParser, start_url)

        assert fetcher.start_url == expected_api_url


def test_acoup_archive_fetcher_find_next_page():
    html = """
    <div class="widget_archive">
        <a href="https://acoup.blog/2019/04/">April 2019</a>
        <a href="https://acoup.blog/2019/05/">May 2019</a>
        <a href="https://acoup.blog/2019/06/">June 2019</a>
    </div>
    """

    fetcher = ACOUPArchiveFetcher(MockParser, "https://acoup.blog/2019/05/")
    parser = MockParser("https://acoup.blog/2019/05/", content=html)

    result = fetcher._find_next_page(parser)

    assert result == "https://acoup.blog/2019/04/"


def test_acoup_archive_fetcher_find_next_page_no_match():
    html = """
    <div class="widget_archive">
        <a href="https://acoup.blog/2019/04/">April 2019</a>
        <a href="https://acoup.blog/2019/06/">June 2019</a>
    </div>
    """

    fetcher = ACOUPArchiveFetcher(MockParser, "https://acoup.blog/2019/05/")
    parser = MockParser("https://acoup.blog/2019/05/", content=html)

    result = fetcher._find_next_page(parser)

    assert result is None


def test_acoup_archive_fetcher_find_next_page_no_content():
    fetcher = ACOUPArchiveFetcher(MockParser, "https://acoup.blog/2019/05/")
    parser = MockParser("https://acoup.blog/2019/05/", content="")

    result = fetcher._find_next_page(parser)

    assert result is None


@pytest.mark.parametrize(
    "start_url, next_url, expected_next_url",
    [
        (
            "https://example.com",
            "",
            "https://example.com",
        ),  # Empty next_url defaults to start_url
        (
            "https://example.com",
            "https://other.com/archive",
            "https://other.com/archive",  # Full URL is preserved
        ),
        (
            "https://example.com",
            "/archive",
            "/archive",
        ),  # Absolute path is preserved
        (
            "https://example.com",
            "archive",
            "https://example.com/archive",
        ),  # Relative path gets prepended
    ],
)
def test_html_next_url_archive_fetcher_post_init(
    start_url, next_url, expected_next_url
):
    fetcher = HTMLNextUrlArchiveFetcher(MockParser, start_url, next_url=next_url)

    assert fetcher.next_url == expected_next_url


def test_html_next_url_archive_fetcher_find_next_page():
    fetcher = HTMLNextUrlArchiveFetcher(
        MockParser, "https://example.com", next_url="https://example.com/archive"
    )
    parser = MockParser("https://example.com")

    result = fetcher._find_next_page(parser, 2)

    assert result == "https://example.com/archive/3"


@pytest.mark.parametrize(
    "url, expected_fetcher_type",
    [
        ("https://danluu.com", HTMLArchiveFetcher),
        ("https://www.rifters.com", HTMLArchiveFetcher),
        ("https://putanumonit.com", HTMLArchiveFetcher),
        ("https://acoup.blog", ACOUPArchiveFetcher),
        ("https://unknown.com", None),
    ],
)
def test_get_archive_fetcher_registry_matches(url, expected_fetcher_type):
    with patch("memory.common.parsers.archives.fetch_html") as mock_fetch:
        mock_fetch.return_value = "<html><body>Not substack</body></html>"

        with patch("memory.common.parsers.archives.is_substack") as mock_is_substack:
            mock_is_substack.return_value = False

            fetcher = get_archive_fetcher(url)

            if expected_fetcher_type:
                assert isinstance(fetcher, expected_fetcher_type)
            else:
                assert fetcher is None


def test_get_archive_fetcher_tuple_registry():
    url = "https://putanumonit.com"

    with patch("memory.common.parsers.archives.fetch_html") as mock_fetch:
        mock_fetch.return_value = "<html><body>Not substack</body></html>"

        fetcher = get_archive_fetcher(url)

        assert isinstance(fetcher, HTMLArchiveFetcher)
        assert fetcher.start_url == "https://putanumonit.com/full-archive"


def test_get_archive_fetcher_direct_parser_registry():
    url = "https://danluu.com"

    with patch("memory.common.parsers.archives.fetch_html") as mock_fetch:
        mock_fetch.return_value = "<html><body>Not substack</body></html>"

        fetcher = get_archive_fetcher(url)

        assert isinstance(fetcher, HTMLArchiveFetcher)
        assert fetcher.parser_class == DanluuParser
        assert fetcher.start_url == url


def test_get_archive_fetcher_substack():
    url = "https://example.substack.com"

    with patch("memory.common.parsers.archives.fetch_html") as mock_fetch:
        mock_fetch.return_value = "<html><body>Substack content</body></html>"

        with patch("memory.common.parsers.archives.is_substack") as mock_is_substack:
            mock_is_substack.return_value = True

            fetcher = get_archive_fetcher(url)

            assert isinstance(fetcher, SubstackArchiveFetcher)
            assert fetcher.parser_class == SubstackAPIParser


def test_get_archive_fetcher_no_match():
    url = "https://unknown.com"

    with patch("memory.common.parsers.archives.fetch_html") as mock_fetch:
        mock_fetch.return_value = "<html><body>Regular website</body></html>"

        with patch("memory.common.parsers.archives.is_substack") as mock_is_substack:
            mock_is_substack.return_value = False

            fetcher = get_archive_fetcher(url)

            assert fetcher is None


def test_fetcher_registry_structure():
    """Test that FETCHER_REGISTRY has expected structure."""
    assert isinstance(FETCHER_REGISTRY, dict)

    for pattern, fetcher in FETCHER_REGISTRY.items():
        assert isinstance(pattern, str)
        assert (
            isinstance(fetcher, type)
            and issubclass(fetcher, FeedParser)
            or isinstance(fetcher, tuple)
            or isinstance(fetcher, ArchiveFetcher)
        )


@pytest.mark.parametrize(
    "pattern, test_url, should_match",
    [
        (r"https://danluu.com", "https://danluu.com", True),
        (r"https://danluu.com", "https://danluu.com/", True),
        (r"https://danluu.com", "https://other.com", False),
        (r"https://www.rifters.com", "https://www.rifters.com/crawl", True),
        (r"https://putanumonit.com", "https://putanumonit.com/archive", True),
    ],
)
def test_registry_pattern_matching(pattern, test_url, should_match):
    import re

    match = re.search(pattern, test_url.rstrip("/"))
    assert bool(match) == should_match
