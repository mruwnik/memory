from datetime import datetime, timedelta, timezone

import pytest

from memory.parsers.blogs import ExUrbeParser


def exurbe_page(published: str) -> str:
    return f"""
    <html><body><article>
      <h1>A history post</h1>
      <div class="post_date_time">{published}</div>
      <div class="entry-content"><p>Body text.</p></div>
    </article></body></html>
    """


@pytest.mark.parametrize(
    "published, expected",
    [
        (
            '<span class="published" content="2020-06-04T10:30:00+00:00">June 4, 2020</span>',
            datetime(2020, 6, 4, 10, 30, tzinfo=timezone.utc),
        ),
        (
            '<span class="published" content="2025-03-05T08:00:00-05:00">March 5, 2025</span>',
            datetime(2025, 3, 5, 8, 0, tzinfo=timezone(timedelta(hours=-5))),
        ),
        ('<span class="published">June 4, 2020</span>', datetime(2020, 6, 4)),
        ('<span class="published" content="not a date">June 4th, 2020</span>', datetime(2020, 6, 4)),
        ('<span class="other">no date here</span>', None),
    ],
)
def test_exurbe_extracts_published_date(published, expected):
    article = ExUrbeParser().parse(exurbe_page(published), "https://exurbe.com/post")

    assert article.published_date == expected
