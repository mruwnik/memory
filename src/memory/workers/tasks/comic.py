import hashlib
import logging
from datetime import datetime
from typing import Callable

import feedparser
import requests

from memory.common import embedding, qdrant, settings
from memory.common.db.connection import make_session
from memory.common.db.models import Comic, clean_filename
from memory.common.parsers import comics
from memory.workers.celery_app import app

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

    urls = {item.get("link") or item.get("id") for item in feed.entries}

    with make_session() as session:
        known = {
            c.url
            for c in session.query(Comic.url).filter(
                Comic.author == base_url,
                Comic.url.in_(urls),
            )
        }

    return urls - known


def fetch_new_comics(
    base_url: str, rss_url: str, parser: Callable[[str], comics.ComicInfo]
) -> set[str]:
    new_urls = find_new_urls(base_url, rss_url)

    for url in new_urls:
        data = parser(url) | {"author": base_url, "url": url}
        sync_comic.delay(**data)
    return new_urls


@app.task(name=SYNC_COMIC)
def sync_comic(
    url: str,
    image_url: str,
    title: str,
    author: str,
    published_date: datetime | None = None,
):
    """Synchronize a comic from a URL."""
    with make_session() as session:
        if session.query(Comic).filter(Comic.url == url).first():
            return

    response = requests.get(image_url)
    file_type = image_url.split(".")[-1]
    mime_type = f"image/{file_type}"
    filename = (
        settings.COMIC_STORAGE_DIR / clean_filename(author) / f"{title}.{file_type}"
    )
    if response.status_code == 200:
        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_bytes(response.content)

    sha256 = hashlib.sha256(f"{image_url}{published_date}".encode()).digest()
    comic = Comic(
        title=title,
        url=url,
        published=published_date,
        author=author,
        filename=filename.resolve().as_posix(),
        mime_type=mime_type,
        size=len(response.content),
        sha256=sha256,
        tags={"comic", author},
        modality="comic",
    )
    chunk = embedding.embed_image(filename, [title, author])
    comic.chunks = [chunk]

    with make_session() as session:
        session.add(comic)
        session.add(chunk)
        session.flush()

        qdrant.upsert_vectors(
            client=qdrant.get_qdrant_client(),
            collection_name="comic",
            ids=[str(chunk.id)],
            vectors=[chunk.vector],
            payloads=[comic.as_payload()],
        )

        session.commit()


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
    sync_smbc.delay()
    sync_xkcd.delay()


@app.task(name="memory.workers.tasks.comic.full_sync_comic")
def trigger_comic_sync():
    def prev_smbc_comic(url: str) -> str | None:
        from bs4 import BeautifulSoup

        response = requests.get(url)
        soup = BeautifulSoup(response.text, "html.parser")
        if link := soup.find("a", attrs={"class", "cc-prev"}):
            return link.attrs["href"]
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
            sync_comic.delay(**data)
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
            sync_comic.delay(**data)
        except Exception as e:
            logger.error(f"failed to sync {url}: {e}")
