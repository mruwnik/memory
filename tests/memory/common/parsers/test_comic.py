import textwrap
from memory.common.parsers.comics import extract_smbc, extract_xkcd
import pytest
from unittest.mock import patch, Mock
import requests


MOCK_SMBC_HTML = """
<!DOCTYPE html>
<html>
        <head>
            <title>Saturday Morning Breakfast Cereal - Time</title>
            <meta property="og:image" content="https://www.smbc-comics.com/comics/1746375102-20250504.webp" />

            <script type='application/ld+json'>
                {
                    "@context": "http://www.schema.org",
                    "@type": "ComicStory",
                    "name": "Saturday Morning Breakfast Cereal - Time",
                    "url": "https://www.smbc-comics.com/comic/time-6",
                    "author":"Zach Weinersmith",
                    "about":"Saturday Morning Breakfast Cereal - Time",
                    "image":"https://www.smbc-comics.com/comics/1746375102-20250504.webp",
                    "thumbnailUrl":"https://www.smbc-comics.com/comicsthumbs/1746375102-20250504.webp",
                    "datePublished":"2025-05-04T12:11:21-04:00"
                }
            </script>
        </head>
        <body>
            <div id="cc-comicbody">
                <img title="I don't know why either, but it was fun to draw." 
                    src="https://www.smbc-comics.com/comics/1746375102-20250504.webp" 
                    id="cc-comic" />
            </div>
            <div id="permalink">
                <input id="permalinktext" type="text" value="http://www.smbc-comics.com/comic/time-6" />
            </div>
        </body>
    </html>
"""


@pytest.mark.parametrize(
    "to_remove, overrides",
    [
        # Normal case - all data present
        ("", {}),
        # Missing title attribute on image
        (
            'title="I don\'t know why either, but it was fun to draw."',
            {"title": "Saturday Morning Breakfast Cereal - Time"},
        ),
        # Missing src attribute on image
        (
            'src="https://www.smbc-comics.com/comics/1746375102-20250504.webp"',
            {"image_url": ""},
        ),
        # # Missing entire img tag
        (
            '<img title="I don\'t know why either, but it was fun to draw." \n                    src="https://www.smbc-comics.com/comics/1746375102-20250504.webp" \n                    id="cc-comic" />',
            {"title": "Saturday Morning Breakfast Cereal - Time", "image_url": ""},
        ),
        # # Corrupt JSON-LD data
        (
            '"datePublished":"2025-05-04T12:11:21-04:00"',
            {"published_date": "", "url": "http://www.smbc-comics.com/comic/time-6"},
        ),
        # # Missing JSON-LD script entirely
        (
            '<script type=\'application/ld+json\'>\n                {\n                    "@context": "http://www.schema.org",\n                    "@type": "ComicStory",\n                    "name": "Saturday Morning Breakfast Cereal - Time",\n                    "url": "https://www.smbc-comics.com/comic/time-6",\n                    "author":"Zach Weinersmith",\n                    "about":"Saturday Morning Breakfast Cereal - Time",\n                    "image":"https://www.smbc-comics.com/comics/1746375102-20250504.webp",\n                    "thumbnailUrl":"https://www.smbc-comics.com/comicsthumbs/1746375102-20250504.webp",\n                    "datePublished":"2025-05-04T12:11:21-04:00"\n                }\n            </script>',
            {"published_date": "", "url": "http://www.smbc-comics.com/comic/time-6"},
        ),
        # # Missing permalink input
        (
            '<div id="permalink">\n                <input id="permalinktext" type="text" value="http://www.smbc-comics.com/comic/time-6" />\n            </div>',
            {},
        ),
        # Missing URL in JSON-LD
        (
            '"url": "https://www.smbc-comics.com/comic/time-6",',
            {"url": "http://www.smbc-comics.com/comic/time-6"},
        ),
    ],
)
def test_extract_smbc(to_remove, overrides):
    """Test successful extraction of comic info from SMBC."""
    expected = {
        "title": "I don't know why either, but it was fun to draw.",
        "image_url": "https://www.smbc-comics.com/comics/1746375102-20250504.webp",
        "published_date": "2025-05-04T12:11:21-04:00",
        "url": "https://www.smbc-comics.com/comic/time-6",
    }
    with patch("requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.text = MOCK_SMBC_HTML.replace(to_remove, "")
        assert extract_smbc("https://www.smbc-comics.com/") == expected | overrides


# Create a stripped-down version of the XKCD HTML
MOCK_XKCD_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>xkcd: Unstoppable Force and Immovable Object</title>
<meta property="og:title" content="Unstoppable Force and Immovable Object">
<meta property="og:url" content="https://xkcd.com/3084/">
<meta property="og:image" content="https://imgs.xkcd.com/comics/unstoppable_force_and_immovable_object_2x.png">
</head>
<body>
<div id="ctitle">Unstoppable Force and Immovable Object</div>

<div id="comic">
<img src="//imgs.xkcd.com/comics/unstoppable_force_and_immovable_object.png" 
     title="Unstoppable force-carrying particles can&#39;t interact with immovable matter by definition." 
     alt="Unstoppable Force and Immovable Object" />
</div>

Permanent link to this comic: <a href="https://xkcd.com/3084">https://xkcd.com/3084/</a><br />
Image URL (for hotlinking/embedding): <a href="https://imgs.xkcd.com/comics/unstoppable_force_and_immovable_object.png">
https://imgs.xkcd.com/comics/unstoppable_force_and_immovable_object.png</a>
</body>
</html>
"""


@pytest.mark.parametrize(
    "to_remove, overrides",
    [
        # Normal case - all data present
        ("", {}),
        # Missing title attribute on image
        (
            'title="Unstoppable force-carrying particles can&#39;t interact with immovable matter by definition."',
            {
                "title": "Unstoppable Force and Immovable Object"
            },  # Falls back to og:title
        ),
        # Missing og:title meta tag - falls back to ctitle
        (
            '<meta property="og:title" content="Unstoppable Force and Immovable Object">',
            {},  # Still gets title from image title
        ),
        # Missing both title and og:title - falls back to ctitle
        (
            'title="Unstoppable force-carrying particles can&#39;t interact with immovable matter by definition."\n<meta property="og:title" content="Unstoppable Force and Immovable Object">',
            {"title": "Unstoppable Force and Immovable Object"},  # Falls back to ctitle
        ),
        # Missing image src attribute
        (
            'src="//imgs.xkcd.com/comics/unstoppable_force_and_immovable_object.png"',
            {"image_url": ""},
        ),
        # Missing entire img tag
        (
            '<img src="//imgs.xkcd.com/comics/unstoppable_force_and_immovable_object.png" \n     title="Unstoppable force-carrying particles can&#39;t interact with immovable matter by definition." \n     alt="Unstoppable Force and Immovable Object" />',
            {
                "image_url": "",
                "title": "Unstoppable Force and Immovable Object",
            },  # Falls back to og:title
        ),
        # Missing entire comic div
        (
            '<div id="comic">\n<img src="//imgs.xkcd.com/comics/unstoppable_force_and_immovable_object.png" \n     title="Unstoppable force-carrying particles can&#39;t interact with immovable matter by definition." \n     alt="Unstoppable Force and Immovable Object" />\n</div>',
            {"image_url": "", "title": "Unstoppable Force and Immovable Object"},
        ),
        # Missing og:url tag
        (
            '<meta property="og:url" content="https://xkcd.com/3084/">',
            {},  # Should fallback to permalink link
        ),
        # Missing permanent link
        (
            'Permanent link to this comic: <a href="https://xkcd.com/3084">https://xkcd.com/3084/</a><br />',
            {"url": "https://xkcd.com/3084/"},  # Should still get URL from og:url
        ),
        # Missing both og:url and permanent link
        (
            '<meta property="og:url" content="https://xkcd.com/3084/">\nPermanent link to this comic: <a href="https://xkcd.com/3084">https://xkcd.com/3084/</a><br />',
            {"url": "https://xkcd.com/test"},  # Falls back to original URL
        ),
    ],
)
def test_extract_xkcd(to_remove, overrides):
    """Test successful extraction of comic info from XKCD."""
    expected = {
        "title": "Unstoppable force-carrying particles can't interact with immovable matter by definition.",
        "image_url": "https://imgs.xkcd.com/comics/unstoppable_force_and_immovable_object.png",
        "url": "https://xkcd.com/3084/",
    }

    with patch("requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        modified_html = MOCK_XKCD_HTML
        for item in to_remove.split("\n"):
            modified_html = modified_html.replace(item, "")

        mock_get.return_value.text = modified_html
        result = extract_xkcd("https://xkcd.com/test")
        assert result == expected | overrides
