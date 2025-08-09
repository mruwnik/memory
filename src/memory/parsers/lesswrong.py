from dataclasses import dataclass, field
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Generator, TypedDict, NotRequired

from bs4 import BeautifulSoup
from PIL import Image as PILImage
from memory.common import settings
import requests
from markdownify import markdownify

from memory.parsers.html import parse_date, process_images

logger = logging.getLogger(__name__)


class LessWrongPost(TypedDict):
    """Represents a post from LessWrong."""

    title: str
    url: str
    description: str
    content: str
    authors: list[str]
    published_at: datetime | None
    guid: str | None
    karma: int
    votes: int
    comments: int
    words: int
    tags: list[str]
    af: bool
    score: int
    extended_score: int
    modified_at: NotRequired[str | None]
    slug: NotRequired[str | None]
    images: NotRequired[list[str]]


def make_graphql_query(
    after: datetime, af: bool = False, limit: int = 50, min_karma: int = 10
) -> str:
    """Create GraphQL query for fetching posts."""
    return f"""
    {{
        posts(input: {{
            terms: {{
                excludeEvents: true
                view: "old"
                af: {str(af).lower()}
                limit: {limit}
                karmaThreshold: {min_karma}
                after: "{after.isoformat()}Z"
                filter: "tagged"
            }}
        }}) {{
            totalCount
            results {{
                _id
                title
                slug
                pageUrl
                postedAt
                modifiedAt
                score
                extendedScore
                baseScore
                voteCount
                commentCount
                wordCount
                tags {{
                    name
                }}
                user {{
                    displayName
                }}
                coauthors {{
                    displayName
                }}
                af
                htmlBody
            }}
        }}
    }}
    """


def fetch_posts_from_api(url: str, query: str) -> dict[str, Any]:
    """Fetch posts from LessWrong GraphQL API."""
    response = requests.post(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/113.0"
        },
        json={"query": query},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["data"]["posts"]


def is_valid_post(post: dict[str, Any], min_karma: int = 10) -> bool:
    """Check if post should be included."""
    # Must have content
    if not post.get("htmlBody"):
        return False

    # Must meet karma threshold
    if post.get("baseScore", 0) < min_karma:
        return False

    return True


def extract_authors(post: dict[str, Any]) -> list[str]:
    """Extract authors from post data."""
    authors = post.get("coauthors", []) or []
    if post.get("user"):
        authors = [post["user"]] + authors
    return [a["displayName"] for a in authors] or ["anonymous"]


def extract_description(body: str) -> str:
    """Extract description from post HTML content."""
    first_paragraph = body.split("\n\n")[0]

    # Truncate if too long
    if len(first_paragraph) > 300:
        first_paragraph = first_paragraph[:300] + "..."

    return first_paragraph


def parse_lesswrong_date(date_str: str | None) -> datetime | None:
    """Parse ISO date string from LessWrong API to datetime."""
    if not date_str:
        return None

    # Try multiple ISO formats that LessWrong might use
    formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ",  # 2023-01-15T10:30:00.000Z
        "%Y-%m-%dT%H:%M:%SZ",  # 2023-01-15T10:30:00Z
        "%Y-%m-%dT%H:%M:%S.%f",  # 2023-01-15T10:30:00.000
        "%Y-%m-%dT%H:%M:%S",  # 2023-01-15T10:30:00
    ]

    for fmt in formats:
        if result := parse_date(date_str, fmt):
            return result

    # Fallback: try removing 'Z' and using fromisoformat
    try:
        clean_date = date_str.rstrip("Z")
        return datetime.fromisoformat(clean_date)
    except (ValueError, TypeError):
        logger.warning(f"Could not parse date: {date_str}")
        return None


def extract_body(post: dict[str, Any]) -> tuple[str, dict[str, PILImage.Image]]:
    """Extract body from post data."""
    if not (body := post.get("htmlBody", "").strip()):
        return "", {}

    url = post.get("pageUrl", "")
    image_dir = settings.FILE_STORAGE_DIR / "lesswrong" / url

    soup = BeautifulSoup(body, "html.parser")
    soup, images = process_images(soup, url, image_dir)
    body = markdownify(str(soup)).strip()
    return body, images


def format_post(post: dict[str, Any]) -> LessWrongPost:
    """Convert raw API post data to GreaterWrongPost."""
    body, images = extract_body(post)

    result: LessWrongPost = {
        "title": post.get("title", "Untitled"),
        "url": post.get("pageUrl", ""),
        "description": extract_description(body),
        "content": body,
        "authors": extract_authors(post),
        "published_at": parse_lesswrong_date(post.get("postedAt")),
        "guid": post.get("_id"),
        "karma": post.get("baseScore", 0),
        "votes": post.get("voteCount", 0),
        "comments": post.get("commentCount", 0),
        "words": post.get("wordCount", 0),
        "tags": [tag["name"] for tag in post.get("tags", [])],
        "af": post.get("af", False),
        "score": post.get("score", 0),
        "extended_score": post.get("extendedScore", 0),
        "modified_at": post.get("modifiedAt"),
        "slug": post.get("slug"),
        "images": list(images.keys()),
    }

    return result


def fetch_lesswrong(
    url: str,
    current_date: datetime,
    af: bool = False,
    min_karma: int = 10,
    limit: int = 50,
    last_url: str | None = None,
) -> list[LessWrongPost]:
    """
    Fetch a batch of posts from LessWrong.

    Returns:
        (posts, next_date, last_item) where next_date is None if iteration should stop
    """
    query = make_graphql_query(current_date, af, limit, min_karma)
    api_response = fetch_posts_from_api(url, query)

    if not api_response["results"]:
        return []

    # If we only get the same item we started with, we're done
    if (
        len(api_response["results"]) == 1
        and last_url
        and api_response["results"][0]["pageUrl"] == last_url
    ):
        return []

    return [
        format_post(post)
        for post in api_response["results"]
        if is_valid_post(post, min_karma)
    ]


def fetch_lesswrong_posts(
    since: datetime | None = None,
    until: datetime | None = None,
    min_karma: int = 10,
    limit: int = 50,
    cooldown: float = 0.5,
    max_items: int = 1000,
    af: bool = False,
    url: str = "https://www.lesswrong.com/graphql",
) -> Generator[LessWrongPost, None, None]:
    """
    Fetch posts from LessWrong.

    Args:
        url: GraphQL endpoint URL
        af: Whether to fetch Alignment Forum posts
        min_karma: Minimum karma threshold for posts
        limit: Number of posts per API request
        start_year: Default start year if no since date provided
        since: Start date for fetching posts
        cooldown: Delay between API requests in seconds
        max_pages: Maximum number of pages to fetch

    Returns:
        List of GreaterWrongPost objects
    """
    if not since:
        since = datetime.now() - timedelta(days=1)

    logger.info(f"Starting from {since}")

    last_url = None
    next_date = since
    items_count = 0

    while next_date and items_count < max_items:
        try:
            page_posts = fetch_lesswrong(url, next_date, af, min_karma, limit, last_url)
        except Exception as e:
            logger.error(f"Error fetching posts: {e}")
            break

        if not page_posts or next_date is None:
            break

        for post in page_posts:
            published_at = post.get("published_at")
            if published_at and until and published_at > until:
                break
            yield post

        last_item = page_posts[-1]
        prev_date = next_date
        next_date = last_item.get("published_at")

        if not next_date or prev_date == next_date:
            logger.warning(
                f"Could not advance through dataset, stopping at {next_date}"
            )
            break

        # The articles are paged by date (inclusive) so passing the last date as
        # is will return the same article again.
        next_date += timedelta(seconds=1)
        items_count += len(page_posts)
        last_url = last_item["url"]

        if cooldown > 0:
            time.sleep(cooldown)

    logger.info(f"Fetched {items_count} items")
