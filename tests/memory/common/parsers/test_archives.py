import pytest
from unittest.mock import Mock, patch
from bs4 import BeautifulSoup

from memory.common.parsers.archives import (
    ArchiveParser,
    WordPressArchiveParser,
    SubstackArchiveParser,
    get_archive_parser,
)


class TestArchiveParser:
    def test_init(self):
        parser = ArchiveParser(url="https://example.com")
        assert parser.url == "https://example.com"
        assert parser._visited_urls == set()
        assert parser._all_items == []
        assert parser.max_pages == 100
        assert parser.delay_between_requests == 1.0

    def test_extract_items_from_page(self):
        html = """
        <div>
            <li><a href="/post1">Post 1</a></li>
            <li><a href="/post2">Post 2</a></li>
            <li><a href="/post1">Post 1</a></li>  <!-- Duplicate -->
        </div>
        """
        soup = BeautifulSoup(html, "html.parser")
        parser = ArchiveParser(url="https://example.com")

        items = parser._extract_items_from_page(soup)
        assert len(items) == 2  # Duplicates should be filtered out

    def test_find_next_page_url_with_selector(self):
        html = '<div><a class="next" href="/page/2">Next</a></div>'
        soup = BeautifulSoup(html, "html.parser")
        parser = ArchiveParser(url="https://example.com")
        parser.next_page_selector = ".next"

        next_url = parser._find_next_page_url(soup, "https://example.com/page/1")
        assert next_url == "https://example.com/page/2"

    def test_find_next_page_url_heuristic(self):
        html = '<div><a rel="next" href="/page/2">Next</a></div>'
        soup = BeautifulSoup(html, "html.parser")
        parser = ArchiveParser(url="https://example.com")

        next_url = parser._find_next_page_url(soup, "https://example.com/page/1")
        assert next_url == "https://example.com/page/2"

    def test_find_next_page_url_contains_text(self):
        html = '<div><a href="/page/2">Next →</a></div>'
        soup = BeautifulSoup(html, "html.parser")
        parser = ArchiveParser(url="https://example.com")

        next_url = parser._find_next_page_heuristic(soup)
        assert next_url == "https://example.com/page/2"

    def test_find_next_numeric_page(self):
        parser = ArchiveParser(url="https://example.com")
        parser.page_url_pattern = "/page/{page}"

        # Test with existing page number
        next_url = parser._find_next_numeric_page("https://example.com/page/3")
        assert next_url == "https://example.com/page/4"

        # Test without page number (assume page 1)
        next_url = parser._find_next_numeric_page("https://example.com/archive")
        assert next_url == "https://example.com/archive/page/2"

    @patch("memory.common.parsers.archives.fetch_html")
    @patch("time.sleep")
    def test_fetch_items_pagination(self, mock_sleep, mock_fetch):
        # Mock HTML for two pages
        page1_html = """
        <div>
            <li><a href="/post1">Post 1</a></li>
            <li><a href="/post2">Post 2</a></li>
            <a rel="next" href="/page/2">Next</a>
        </div>
        """
        page2_html = """
        <div>
            <li><a href="/post3">Post 3</a></li>
            <li><a href="/post4">Post 4</a></li>
        </div>
        """

        mock_fetch.side_effect = [page1_html, page2_html]

        parser = ArchiveParser(url="https://example.com/page/1")
        parser.delay_between_requests = 0.1  # Speed up test

        items = parser.fetch_items()

        assert len(items) == 4
        assert mock_fetch.call_count == 2
        assert mock_sleep.call_count == 1  # One delay between requests

    @patch("memory.common.parsers.archives.fetch_html")
    def test_fetch_items_stops_at_max_pages(self, mock_fetch):
        # Mock HTML that always has a next page
        html_with_next = """
        <div>
            <li><a href="/post">Post</a></li>
            <a rel="next" href="/page/999">Next</a>
        </div>
        """

        mock_fetch.return_value = html_with_next

        parser = ArchiveParser(url="https://example.com/page/1")
        parser.max_pages = 3
        parser.delay_between_requests = 0  # No delay for test

        items = parser.fetch_items()

        assert mock_fetch.call_count == 3  # Should stop at max_pages

    @patch("memory.common.parsers.archives.fetch_html")
    def test_fetch_items_handles_duplicate_urls(self, mock_fetch):
        # Mock HTML that creates a cycle
        page1_html = """
        <div>
            <li><a href="/post1">Post 1</a></li>
            <a rel="next" href="/page/2">Next</a>
        </div>
        """
        page2_html = """
        <div>
            <li><a href="/post2">Post 2</a></li>
            <a rel="next" href="/page/1">Back to page 1</a>
        </div>
        """

        mock_fetch.side_effect = [page1_html, page2_html]

        parser = ArchiveParser(url="https://example.com/page/1")
        parser.delay_between_requests = 0

        items = parser.fetch_items()

        assert len(items) == 2
        assert mock_fetch.call_count == 2  # Should stop when it hits visited URL


class TestWordPressArchiveParser:
    def test_selectors(self):
        parser = WordPressArchiveParser(url="https://example.wordpress.com")
        assert parser.item_selector == "article, .post"
        assert parser.next_page_selector == '.nav-previous a, .next a, a[rel="next"]'
        assert parser.title_selector == ".entry-title a, h1 a, h2 a"


class TestSubstackArchiveParser:
    def test_selectors(self):
        parser = SubstackArchiveParser(url="https://example.substack.com")
        assert parser.item_selector == ".post-preview, .post"
        assert parser.next_page_selector == ".pagination .next"


class TestGetArchiveParser:
    @pytest.mark.parametrize(
        "url,expected_class",
        [
            ("https://example.wordpress.com/archive", WordPressArchiveParser),
            ("https://example.substack.com/archive", SubstackArchiveParser),
            ("https://example.com/archive", ArchiveParser),  # Default
        ],
    )
    def test_get_archive_parser(self, url, expected_class):
        parser = get_archive_parser(url)
        assert isinstance(parser, expected_class)
        assert parser.url == url
