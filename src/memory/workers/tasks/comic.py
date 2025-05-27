import logging
from datetime import datetime
from typing import Callable, cast

import feedparser
import requests

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import Comic, clean_filename
from memory.parsers import comics
from memory.workers.celery_app import app
from memory.workers.tasks.content_processing import (
    check_content_exists,
    create_content_hash,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)

SYNC_ALL_COMICS = "memory.workers.tasks.comic.sync_all_comics"
SYNC_SMBC = "memory.workers.tasks.comic.sync_smbc"
SYNC_XKCD = "memory.workers.tasks.comic.sync_xkcd"
SYNC_COMIC = "memory.workers.tasks.comic.sync_comic"

BASE_SMBC_URL = "https://www.smbc-comics.com/"
SMBC_RSS_URL = "https://www.smbc-comics.com/comic/rss"

BASE_XKCD_URL = "https://xkcd.com/"
XKCD_RSS_URL = "https://xkcd.com/atom.xml"


def find_new_urls(base_url: str, rss_url: str) -> set[str]:
    try:
        feed = feedparser.parse(rss_url)
    except Exception as e:
        logger.error(f"Failed to fetch or parse {rss_url}: {e}")
        return set()

    urls = {cast(str, item.get("link") or item.get("id")) for item in feed.entries}
    urls = {url for url in urls if url}

    with make_session() as session:
        known = {
            c.url
            for c in session.query(Comic.url).filter(
                Comic.author == base_url,
                Comic.url.in_(urls),
            )
        }

    return cast(set[str], urls - known)


def fetch_new_comics(
    base_url: str, rss_url: str, parser: Callable[[str], comics.ComicInfo]
) -> set[str]:
    new_urls = find_new_urls(base_url, rss_url)

    for url in new_urls:
        data = parser(url) | {"author": base_url, "url": url}
        sync_comic.delay(**data)  # type: ignore
    return new_urls


@app.task(name=SYNC_COMIC)
@safe_task_execution
def sync_comic(
    url: str,
    image_url: str,
    title: str,
    author: str,
    published_date: datetime | None = None,
):
    """Synchronize a comic from a URL."""
    with make_session() as session:
        existing_comic = check_content_exists(session, Comic, url=url)
        if existing_comic:
            return {"status": "already_exists", "comic_id": existing_comic.id}

    response = requests.get(image_url)
    if response.status_code != 200:
        return {
            "status": "failed",
            "error": f"Failed to download image: {response.status_code}",
        }

    file_type = image_url.split(".")[-1]
    mime_type = f"image/{file_type}"
    filename = (
        settings.COMIC_STORAGE_DIR / clean_filename(author) / f"{title}.{file_type}"
    )

    filename.parent.mkdir(parents=True, exist_ok=True)
    filename.write_bytes(response.content)

    comic = Comic(
        title=title,
        url=url,
        published=published_date,
        author=author,
        filename=filename.resolve().as_posix(),
        mime_type=mime_type,
        size=len(response.content),
        sha256=create_content_hash(f"{image_url}{published_date}"),
        tags={"comic", author},
        modality="comic",
    )

    with make_session() as session:
        return process_content_item(comic, "comic", session)


@app.task(name=SYNC_SMBC)
def sync_smbc() -> set[str]:
    """Synchronize SMBC comics from RSS feed."""
    return fetch_new_comics(BASE_SMBC_URL, SMBC_RSS_URL, comics.extract_smbc)


@app.task(name=SYNC_XKCD)
def sync_xkcd() -> set[str]:
    """Synchronize XKCD comics from RSS feed."""
    return fetch_new_comics(BASE_XKCD_URL, XKCD_RSS_URL, comics.extract_xkcd)


@app.task(name=SYNC_ALL_COMICS)
def sync_all_comics():
    """Synchronize all active comics."""
    sync_smbc.delay()  # type: ignore
    sync_xkcd.delay()  # type: ignore


@app.task(name="memory.workers.tasks.comic.full_sync_comic")
def trigger_comic_sync():
    def prev_smbc_comic(url: str) -> str | None:
        from bs4 import BeautifulSoup

        response = requests.get(url)
        soup = BeautifulSoup(response.text, "html.parser")
        if link := soup.find("a", attrs={"class": "cc-prev"}):
            return link.attrs["href"]  # type: ignore
        return None

    next_url = "https://www.smbc-comics.com"
    urls = []
    logger.info(f"syncing {next_url}")
    while next_url := prev_smbc_comic(next_url):
        if len(urls) % 10 == 0:
            logger.info(f"got {len(urls)}")
        try:
            data = comics.extract_smbc(next_url) | {
                "author": "https://www.smbc-comics.com/"
            }
            sync_comic.delay(**data)  # type: ignore
        except Exception as e:
            logger.error(f"failed to sync {next_url}: {e}")
        urls.append(next_url)

    logger.info(f"syncing {BASE_XKCD_URL}")
    for i in range(1, 308):
        if i % 10 == 0:
            logger.info(f"got {i}")
        url = f"{BASE_XKCD_URL}/{i}"
        try:
            data = comics.extract_xkcd(url) | {"author": "https://xkcd.com/"}
            sync_comic.delay(**data)  # type: ignore
        except Exception as e:
            logger.error(f"failed to sync {url}: {e}")
