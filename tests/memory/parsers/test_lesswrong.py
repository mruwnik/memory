from datetime import datetime, timedelta
from unittest.mock import patch, Mock
import pytest
from PIL import Image as PILImage

from memory.parsers.lesswrong import (
    LessWrongPost,
    make_graphql_query,
    fetch_posts_from_api,
    is_valid_post,
    extract_authors,
    extract_description,
    parse_lesswrong_date,
    extract_body,
    format_post,
    fetch_lesswrong,
    fetch_lesswrong_posts,
)


@pytest.mark.parametrize(
    "after, af, limit, min_karma, expected_contains",
    [
        (
            datetime(2023, 1, 15, 10, 30),
            False,
            50,
            10,
            [
                "af: false",
                "limit: 50",
                "karmaThreshold: 10",
                'after: "2023-01-15T10:30:00Z"',
            ],
        ),
        (
            datetime(2023, 2, 20),
            True,
            25,
            5,
            [
                "af: true",
                "limit: 25",
                "karmaThreshold: 5",
                'after: "2023-02-20T00:00:00Z"',
            ],
        ),
    ],
)
def test_make_graphql_query(after, af, limit, min_karma, expected_contains):
    query = make_graphql_query(after, af, limit, min_karma)

    for expected in expected_contains:
        assert expected in query

    # Check that all required fields are present
    required_fields = [
        "_id",
        "title",
        "slug",
        "pageUrl",
        "postedAt",
        "modifiedAt",
        "score",
        "extendedScore",
        "baseScore",
        "voteCount",
        "commentCount",
        "wordCount",
        "tags",
        "user",
        "coauthors",
        "af",
        "htmlBody",
    ]
    for field in required_fields:
        assert field in query


@patch("memory.parsers.lesswrong.requests.post")
def test_fetch_posts_from_api_success(mock_post):
    mock_response = Mock()
    mock_response.json.return_value = {
        "data": {
            "posts": {
                "totalCount": 2,
                "results": [
                    {"_id": "1", "title": "Post 1"},
                    {"_id": "2", "title": "Post 2"},
                ],
            }
        }
    }
    mock_post.return_value = mock_response

    url = "https://www.lesswrong.com/graphql"
    query = "test query"

    result = fetch_posts_from_api(url, query)

    mock_post.assert_called_once_with(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/113.0"
        },
        json={"query": query},
        timeout=30,
    )

    assert result == {
        "totalCount": 2,
        "results": [
            {"_id": "1", "title": "Post 1"},
            {"_id": "2", "title": "Post 2"},
        ],
    }


@patch("memory.parsers.lesswrong.requests.post")
def test_fetch_posts_from_api_http_error(mock_post):
    mock_response = Mock()
    mock_response.raise_for_status.side_effect = Exception("HTTP Error")
    mock_post.return_value = mock_response

    with pytest.raises(Exception, match="HTTP Error"):
        fetch_posts_from_api("https://example.com", "query")


@pytest.mark.parametrize(
    "post_data, min_karma, expected",
    [
        # Valid post with content and karma
        ({"htmlBody": "<p>Content</p>", "baseScore": 15}, 10, True),
        # Valid post at karma threshold
        ({"htmlBody": "<p>Content</p>", "baseScore": 10}, 10, True),
        # Invalid: no content
        ({"htmlBody": "", "baseScore": 15}, 10, False),
        ({"htmlBody": None, "baseScore": 15}, 10, False),
        ({}, 10, False),
        # Invalid: below karma threshold
        ({"htmlBody": "<p>Content</p>", "baseScore": 5}, 10, False),
        ({"htmlBody": "<p>Content</p>"}, 10, False),  # No baseScore
        # Edge cases
        (
            {"htmlBody": "   ", "baseScore": 15},
            10,
            True,
        ),  # Whitespace only - actually valid
        ({"htmlBody": "<p>Content</p>", "baseScore": 0}, 0, True),  # Zero threshold
    ],
)
def test_is_valid_post(post_data, min_karma, expected):
    assert is_valid_post(post_data, min_karma) == expected


@pytest.mark.parametrize(
    "post_data, expected",
    [
        # User only
        (
            {"user": {"displayName": "Alice"}},
            ["Alice"],
        ),
        # User with coauthors
        (
            {
                "user": {"displayName": "Alice"},
                "coauthors": [{"displayName": "Bob"}, {"displayName": "Charlie"}],
            },
            ["Alice", "Bob", "Charlie"],
        ),
        # Coauthors only (no user)
        (
            {"coauthors": [{"displayName": "Bob"}]},
            ["Bob"],
        ),
        # Empty coauthors list
        (
            {"user": {"displayName": "Alice"}, "coauthors": []},
            ["Alice"],
        ),
        # No authors at all
        ({}, ["anonymous"]),
        ({"user": None, "coauthors": None}, ["anonymous"]),
        ({"user": None, "coauthors": []}, ["anonymous"]),
    ],
)
def test_extract_authors(post_data, expected):
    assert extract_authors(post_data) == expected


@pytest.mark.parametrize(
    "body, expected",
    [
        # Short content
        ("This is a short paragraph.", "This is a short paragraph."),
        # Multiple paragraphs - only first
        ("First paragraph.\n\nSecond paragraph.", "First paragraph."),
        # Long content - truncated
        (
            "A" * 350,
            "A" * 300 + "...",
        ),
        # Empty content
        ("", ""),
        # Whitespace only
        ("   \n\n   ", "   "),
    ],
)
def test_extract_description(body, expected):
    assert extract_description(body) == expected


@pytest.mark.parametrize(
    "date_str, expected",
    [
        # Standard ISO formats
        ("2023-01-15T10:30:00.000Z", datetime(2023, 1, 15, 10, 30, 0)),
        ("2023-01-15T10:30:00Z", datetime(2023, 1, 15, 10, 30, 0)),
        ("2023-01-15T10:30:00.000", datetime(2023, 1, 15, 10, 30, 0)),
        ("2023-01-15T10:30:00", datetime(2023, 1, 15, 10, 30, 0)),
        # Fallback to fromisoformat
        ("2023-01-15T10:30:00.123456", datetime(2023, 1, 15, 10, 30, 0, 123456)),
        # Invalid dates
        ("invalid-date", None),
        ("", None),
        (None, None),
        ("2023-13-45T25:70:70Z", None),  # Invalid date components
    ],
)
def test_parse_lesswrong_date(date_str, expected):
    assert parse_lesswrong_date(date_str) == expected


@patch("memory.parsers.lesswrong.process_images")
@patch("memory.parsers.lesswrong.markdownify")
@patch("memory.parsers.lesswrong.settings")
def test_extract_body(mock_settings, mock_markdownify, mock_process_images):
    from pathlib import Path

    mock_settings.FILE_STORAGE_DIR = Path("/tmp")
    mock_markdownify.return_value = "# Markdown content"
    mock_images = {"image1.jpg": Mock(spec=PILImage.Image)}
    mock_process_images.return_value = (Mock(), mock_images)

    post = {
        "htmlBody": "<h1>HTML content</h1>",
        "pageUrl": "https://lesswrong.com/posts/abc123/test-post",
    }

    body, images = extract_body(post)

    assert body == "# Markdown content"
    assert images == mock_images
    mock_process_images.assert_called_once()
    mock_markdownify.assert_called_once()


@patch("memory.parsers.lesswrong.process_images")
def test_extract_body_empty_content(mock_process_images):
    post = {"htmlBody": ""}
    body, images = extract_body(post)

    assert body == ""
    assert images == {}
    mock_process_images.assert_not_called()


@patch("memory.parsers.lesswrong.extract_body")
def test_format_post(mock_extract_body):
    mock_extract_body.return_value = ("Markdown body", {"img1.jpg": Mock()})

    post_data = {
        "_id": "abc123",
        "title": "Test Post",
        "slug": "test-post",
        "pageUrl": "https://lesswrong.com/posts/abc123/test-post",
        "postedAt": "2023-01-15T10:30:00Z",
        "modifiedAt": "2023-01-16T11:00:00Z",
        "score": 25,
        "extendedScore": 30,
        "baseScore": 20,
        "voteCount": 15,
        "commentCount": 5,
        "wordCount": 1000,
        "tags": [{"name": "AI"}, {"name": "Rationality"}],
        "user": {"displayName": "Author"},
        "coauthors": [{"displayName": "Coauthor"}],
        "af": True,
        "htmlBody": "<p>HTML content</p>",
    }

    result = format_post(post_data)

    expected: LessWrongPost = {
        "title": "Test Post",
        "url": "https://lesswrong.com/posts/abc123/test-post",
        "description": "Markdown body",
        "content": "Markdown body",
        "authors": ["Author", "Coauthor"],
        "published_at": datetime(2023, 1, 15, 10, 30, 0),
        "guid": "abc123",
        "karma": 20,
        "votes": 15,
        "comments": 5,
        "words": 1000,
        "tags": ["AI", "Rationality"],
        "af": True,
        "score": 25,
        "extended_score": 30,
        "modified_at": "2023-01-16T11:00:00Z",
        "slug": "test-post",
        "images": ["img1.jpg"],
    }

    assert result == expected


@patch("memory.parsers.lesswrong.extract_body")
def test_format_post_minimal_data(mock_extract_body):
    mock_extract_body.return_value = ("", {})

    post_data = {}

    result = format_post(post_data)

    expected: LessWrongPost = {
        "title": "Untitled",
        "url": "",
        "description": "",
        "content": "",
        "authors": ["anonymous"],
        "published_at": None,
        "guid": None,
        "karma": 0,
        "votes": 0,
        "comments": 0,
        "words": 0,
        "tags": [],
        "af": False,
        "score": 0,
        "extended_score": 0,
        "modified_at": None,
        "slug": None,
        "images": [],
    }

    assert result == expected


@patch("memory.parsers.lesswrong.fetch_posts_from_api")
@patch("memory.parsers.lesswrong.make_graphql_query")
@patch("memory.parsers.lesswrong.format_post")
@patch("memory.parsers.lesswrong.is_valid_post")
def test_fetch_lesswrong_success(
    mock_is_valid, mock_format, mock_query, mock_fetch_api
):
    mock_query.return_value = "test query"
    mock_fetch_api.return_value = {
        "results": [
            {"_id": "1", "title": "Post 1"},
            {"_id": "2", "title": "Post 2"},
        ]
    }
    mock_is_valid.side_effect = [True, False]  # First valid, second invalid
    mock_format.return_value = {"title": "Formatted Post"}

    url = "https://lesswrong.com/graphql"
    current_date = datetime(2023, 1, 15)

    result = fetch_lesswrong(url, current_date, af=True, min_karma=5, limit=25)

    mock_query.assert_called_once_with(current_date, True, 25, 5)
    mock_fetch_api.assert_called_once_with(url, "test query")
    assert mock_is_valid.call_count == 2
    mock_format.assert_called_once_with({"_id": "1", "title": "Post 1"})
    assert result == [{"title": "Formatted Post"}]


@patch("memory.parsers.lesswrong.fetch_posts_from_api")
def test_fetch_lesswrong_empty_results(mock_fetch_api):
    mock_fetch_api.return_value = {"results": []}

    result = fetch_lesswrong("url", datetime.now())
    assert result == []


@patch("memory.parsers.lesswrong.fetch_posts_from_api")
def test_fetch_lesswrong_same_item_as_last(mock_fetch_api):
    mock_fetch_api.return_value = {
        "results": [{"pageUrl": "https://lesswrong.com/posts/same"}]
    }

    result = fetch_lesswrong(
        "url", datetime.now(), last_url="https://lesswrong.com/posts/same"
    )
    assert result == []


@patch("memory.parsers.lesswrong.fetch_lesswrong")
@patch("memory.parsers.lesswrong.time.sleep")
def test_fetch_lesswrong_posts_success(mock_sleep, mock_fetch):
    since = datetime(2023, 1, 15)

    # Mock three batches of posts
    mock_fetch.side_effect = [
        [
            {"published_at": datetime(2023, 1, 14), "url": "post1"},
            {"published_at": datetime(2023, 1, 13), "url": "post2"},
        ],
        [
            {"published_at": datetime(2023, 1, 12), "url": "post3"},
        ],
        [],  # Empty result to stop iteration
    ]

    posts = list(
        fetch_lesswrong_posts(
            since=since,
            min_karma=10,
            limit=50,
            cooldown=0.1,
            max_items=100,
            af=False,
            url="https://lesswrong.com/graphql",
        )
    )

    assert len(posts) == 3
    assert posts[0]["url"] == "post1"
    assert posts[1]["url"] == "post2"
    assert posts[2]["url"] == "post3"

    # Should have called sleep twice (after first two batches)
    assert mock_sleep.call_count == 2
    mock_sleep.assert_called_with(0.1)


@patch("memory.parsers.lesswrong.fetch_lesswrong")
def test_fetch_lesswrong_posts_default_since(mock_fetch):
    mock_fetch.return_value = []

    with patch("memory.parsers.lesswrong.datetime") as mock_datetime:
        mock_now = datetime(2023, 1, 15, 12, 0, 0)
        mock_datetime.now.return_value = mock_now

        list(fetch_lesswrong_posts())

        # Should use yesterday as default
        expected_since = mock_now - timedelta(days=1)
        mock_fetch.assert_called_with(
            "https://www.lesswrong.com/graphql", expected_since, False, 10, 50, None
        )


@patch("memory.parsers.lesswrong.fetch_lesswrong")
def test_fetch_lesswrong_posts_max_items_limit(mock_fetch):
    # Return posts that would exceed max_items
    mock_fetch.side_effect = [
        [{"published_at": datetime(2023, 1, 14), "url": f"post{i}"} for i in range(8)],
        [
            {"published_at": datetime(2023, 1, 13), "url": f"post{i}"}
            for i in range(8, 16)
        ],
    ]

    posts = list(
        fetch_lesswrong_posts(
            since=datetime(2023, 1, 15),
            max_items=7,  # Should stop after 7 items
            cooldown=0,
        )
    )

    # The logic checks items_count < max_items before fetching, so it will fetch the first batch
    # Since items_count (8) >= max_items (7) after first batch, it won't fetch the second batch
    assert len(posts) == 8


@patch("memory.parsers.lesswrong.fetch_lesswrong")
def test_fetch_lesswrong_posts_api_error(mock_fetch):
    mock_fetch.side_effect = Exception("API Error")

    posts = list(fetch_lesswrong_posts(since=datetime(2023, 1, 15)))
    assert posts == []


@patch("memory.parsers.lesswrong.fetch_lesswrong")
def test_fetch_lesswrong_posts_no_date_progression(mock_fetch):
    # Mock posts with same date to trigger stopping condition
    same_date = datetime(2023, 1, 15)
    mock_fetch.side_effect = [
        [{"published_at": same_date, "url": "post1"}],
        [{"published_at": same_date, "url": "post2"}],  # Same date, should stop
    ]

    posts = list(fetch_lesswrong_posts(since=same_date, cooldown=0))

    assert len(posts) == 1  # Should stop after first batch


@patch("memory.parsers.lesswrong.fetch_lesswrong")
def test_fetch_lesswrong_posts_none_date(mock_fetch):
    # Mock posts with None date to trigger stopping condition
    mock_fetch.side_effect = [
        [{"published_at": None, "url": "post1"}],
    ]

    posts = list(fetch_lesswrong_posts(since=datetime(2023, 1, 15), cooldown=0))

    assert len(posts) == 1  # Should stop after first batch


def test_lesswrong_post_type():
    """Test that LessWrongPost TypedDict has correct structure."""
    # This is more of a documentation test to ensure the type is correct
    post: LessWrongPost = {
        "title": "Test",
        "url": "https://example.com",
        "description": "Description",
        "content": "Content",
        "authors": ["Author"],
        "published_at": datetime.now(),
        "guid": "123",
        "karma": 10,
        "votes": 5,
        "comments": 2,
        "words": 100,
        "tags": ["tag"],
        "af": False,
        "score": 15,
        "extended_score": 20,
    }

    # Optional fields
    post["modified_at"] = "2023-01-15T10:30:00Z"
    post["slug"] = "test-slug"
    post["images"] = ["image.jpg"]

    # Should not raise any type errors
    assert post["title"] == "Test"
