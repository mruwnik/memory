import pytest

from memory.common import settings
from memory.common.html_text import html_to_markdown, split_point, strip_tags


@pytest.mark.parametrize(
    "html, expected",
    [
        ("<p>a</p><p>b &amp; c</p>", "a b & c"),
        ("x<style>p { color: red }</style>y", "x y"),
        ("x<SCRIPT type='a'>alert(1)</script >y", "x y"),
        ("x<style>never closed <p>hidden</p>", "x"),
        ("a<b<c>d", "a<b d"),
        ("<<<<", "<<<<"),
        ("a\n \n\n \nb", "a\n\nb"),
    ],
)
def test_strip_tags(html, expected):
    assert strip_tags(html) == expected


@pytest.mark.parametrize(
    "html, limit, expected",
    [("abc<p>def</p>", 5, 3), ("abcdef", 3, 3), ("<p>abcdef", 4, 4), ("ab<p>", 4, 2)],
)
def test_split_point_never_splits_a_tag(html, limit, expected):
    assert split_point(html, limit) == expected


def test_html_to_markdown_converts_only_up_to_the_limit():
    html = "<p><b>x</b></p>" * 10

    assert html_to_markdown(html, limit=45) == "**x**\n\n**x**\n\n**x**\n\nx x x x x x x"


def test_html_to_markdown_default_limit_is_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "MAX_HTML_TO_MARKDOWN_CHARS", 15)

    assert html_to_markdown("<p><b>x</b></p><h1>y</h1>") == "**x**\n\ny"


def test_html_to_markdown_under_the_limit_is_plain_markdownify():
    assert html_to_markdown("<h1>T</h1><p><a href='u'>l</a></p>", heading_style="ATX") == "# T\n\n[l](u)"


def test_html_to_markdown_survives_deep_nesting():
    assert html_to_markdown("<div>" * 3000 + "deep") == "deep"
