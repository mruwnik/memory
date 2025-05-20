import logging
from typing import TypedDict, NotRequired

from bs4 import BeautifulSoup, Tag
import requests
import json

logger = logging.getLogger(__name__)


class ComicInfo(TypedDict):
    title: str
    image_url: str
    published_date: NotRequired[str]
    url: str


def extract_smbc(url: str) -> ComicInfo:
    """
    Extract the title, published date, and image URL from a SMBC comic.

    Returns:
        ComicInfo with title, image_url, published_date, and comic_url
    """
    response = requests.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    comic_img = soup.find("img", id="cc-comic")
    title = ""
    image_url = ""

    if comic_img and isinstance(comic_img, Tag):
        if comic_img.has_attr("src"):
            image_url = str(comic_img["src"])
        if comic_img.has_attr("title"):
            title = str(comic_img["title"])

    published_date = ""
    comic_url = ""

    script_ld = soup.find("script", type="application/ld+json")
    if script_ld and isinstance(script_ld, Tag) and script_ld.string:
        try:
            data = json.loads(script_ld.string)
            published_date = data.get("datePublished", "")

            # Use JSON-LD URL if available
            title = title or data.get("name", "")
            comic_url = data.get("url")
        except (json.JSONDecodeError, AttributeError):
            pass

    permalink_input = soup.find("input", id="permalinktext")
    if not comic_url and permalink_input and isinstance(permalink_input, Tag):
        comic_url = permalink_input.get("value", "")

    return {
        "title": title,
        "image_url": image_url,
        "published_date": published_date,
        "url": comic_url or url,
    }


def extract_xkcd(url: str) -> ComicInfo:
    """
    Extract comic information from an XKCD comic.

    This function parses an XKCD comic page to extract the title from the hover text,
    the image URL, and the permanent URL of the comic.

    Args:
        url: The URL of the XKCD comic to parse

    Returns:
        ComicInfo with title, image_url, and comic_url
    """
    response = requests.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    def get_comic_img() -> Tag | None:
        """Extract the comic image tag."""
        comic_div = soup.find("div", id="comic")
        if comic_div and isinstance(comic_div, Tag):
            img = comic_div.find("img")
            return img if isinstance(img, Tag) else None
        return None

    def get_title() -> str:
        """Extract title from image title attribute with fallbacks."""
        # Primary source: hover text from the image (most informative)
        img = get_comic_img()
        if img and img.has_attr("title"):
            return str(img["title"])

        # Secondary source: og:title meta tag
        og_title = soup.find("meta", property="og:title")
        if og_title and isinstance(og_title, Tag) and og_title.has_attr("content"):
            return str(og_title["content"])

        # Last resort: page title div
        title_div = soup.find("div", id="ctitle")
        return title_div.text.strip() if title_div else ""

    def get_image_url() -> str:
        """Extract and normalize the image URL."""
        img = get_comic_img()
        if not img or not img.has_attr("src"):
            return ""

        image_src = str(img["src"])
        return f"https:{image_src}" if image_src.startswith("//") else image_src

    def get_permanent_url() -> str:
        """Extract the permanent URL to the comic."""
        og_url = soup.find("meta", property="og:url")
        if og_url and isinstance(og_url, Tag) and og_url.has_attr("content"):
            return str(og_url["content"])

        # Fallback: look for permanent link text
        for a_tag in soup.find_all("a"):
            text = a_tag.get_text()
            if text.startswith("https://xkcd.com/") and text.strip().endswith("/"):
                return str(text.strip())
        return url

    return {
        "title": get_title(),
        "image_url": get_image_url(),
        "url": get_permanent_url(),
    }
