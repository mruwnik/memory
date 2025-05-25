import pytest
from datetime import datetime
from unittest.mock import Mock, patch

from memory.common.db.models import Comic
from memory.workers.tasks import comic
import requests


@pytest.fixture
def mock_comic_info():
    """Mock comic info data for testing."""
    return {
        "title": "Test Comic",
        "image_url": "https://example.com/comic.png",
        "url": "https://example.com/comic/1",
        "published_date": "2024-01-01T12:00:00Z",
    }


@pytest.fixture
def mock_feed_data():
    """Mock RSS feed data."""
    return {
        "entries": [
            {"link": "https://example.com/comic/1", "id": None},
            {"link": "https://example.com/comic/2", "id": None},
            {"link": None, "id": "https://example.com/comic/3"},
        ]
    }


@pytest.fixture
def mock_image_response():
    """Mock HTTP response for comic image."""
    # 1x1 PNG image (smallest valid PNG)
    png_data = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\tpHYs\x00\x00\x0b\x13"
        b"\x00\x00\x0b\x13\x01\x00\x9a\x9c\x18\x00\x00\x00\nIDATx\x9cc```"
        b"\x00\x00\x00\x02\x00\x01\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    response = Mock()
    response.status_code = 200
    response.content = png_data
    with patch.object(requests, "get", return_value=response):
        yield response


@patch("memory.workers.tasks.comic.feedparser.parse")
def test_find_new_urls_success(mock_parse, mock_feed_data, db_session):
    """Test successful URL discovery from RSS feed."""
    mock_parse.return_value = Mock(entries=mock_feed_data["entries"])

    result = comic.find_new_urls("https://example.com", "https://example.com/rss")

    assert result == {
        "https://example.com/comic/1",
        "https://example.com/comic/2",
        "https://example.com/comic/3",
    }
    mock_parse.assert_called_once_with("https://example.com/rss")


@patch("memory.workers.tasks.comic.feedparser.parse")
def test_find_new_urls_with_existing_comics(mock_parse, mock_feed_data, db_session):
    """Test URL discovery when some comics already exist."""
    mock_parse.return_value = Mock(entries=mock_feed_data["entries"])

    # Add existing comic to database
    existing_comic = Comic(
        title="Existing Comic",
        url="https://example.com/comic/1",
        author="https://example.com",
        filename="/test/path",
        sha256=b"test_hash",
        modality="comic",
        tags=["comic"],
    )
    db_session.add(existing_comic)
    db_session.commit()

    result = comic.find_new_urls("https://example.com", "https://example.com/rss")

    # Should only return URLs not in database
    assert result == {
        "https://example.com/comic/2",
        "https://example.com/comic/3",
    }


@patch("memory.workers.tasks.comic.feedparser.parse")
def test_find_new_urls_parse_error(mock_parse):
    """Test handling of RSS feed parsing errors."""
    mock_parse.side_effect = Exception("Parse error")

    assert (
        comic.find_new_urls("https://example.com", "https://example.com/rss") == set()
    )


@patch("memory.workers.tasks.comic.feedparser.parse")
def test_find_new_urls_empty_feed(mock_parse):
    """Test handling of empty RSS feed."""
    mock_parse.return_value = Mock(entries=[])

    result = comic.find_new_urls("https://example.com", "https://example.com/rss")

    assert result == set()


@patch("memory.workers.tasks.comic.feedparser.parse")
def test_find_new_urls_malformed_entries(mock_parse):
    """Test handling of malformed RSS entries."""
    mock_parse.return_value = Mock(
        entries=[
            {"link": None, "id": None},  # Both None
            {},  # Missing keys
        ]
    )

    result = comic.find_new_urls("https://example.com", "https://example.com/rss")

    assert result == set()


@patch("memory.workers.tasks.comic.sync_comic.delay")
@patch("memory.workers.tasks.comic.find_new_urls")
def test_fetch_new_comics_success(mock_find_urls, mock_sync_delay, mock_comic_info):
    """Test successful comic fetching."""
    mock_find_urls.return_value = {"https://example.com/comic/1"}
    mock_parser = Mock(return_value=mock_comic_info)

    result = comic.fetch_new_comics(
        "https://example.com", "https://example.com/rss", mock_parser
    )

    assert result == {"https://example.com/comic/1"}
    mock_parser.assert_called_once_with("https://example.com/comic/1")
    expected_call_args = {
        **mock_comic_info,
        "author": "https://example.com",
        "url": "https://example.com/comic/1",
    }
    mock_sync_delay.assert_called_once_with(**expected_call_args)


@patch("memory.workers.tasks.comic.sync_comic.delay")
@patch("memory.workers.tasks.comic.find_new_urls")
def test_fetch_new_comics_no_new_urls(mock_find_urls, mock_sync_delay):
    """Test when no new URLs are found."""
    mock_find_urls.return_value = set()
    mock_parser = Mock()

    result = comic.fetch_new_comics(
        "https://example.com", "https://example.com/rss", mock_parser
    )

    assert result == set()
    mock_parser.assert_not_called()
    mock_sync_delay.assert_not_called()


@patch("memory.workers.tasks.comic.sync_comic.delay")
@patch("memory.workers.tasks.comic.find_new_urls")
def test_fetch_new_comics_multiple_urls(
    mock_find_urls, mock_sync_delay, mock_comic_info
):
    """Test fetching multiple new comics."""
    urls = {"https://example.com/comic/1", "https://example.com/comic/2"}
    mock_find_urls.return_value = urls
    mock_parser = Mock(return_value=mock_comic_info)

    result = comic.fetch_new_comics(
        "https://example.com", "https://example.com/rss", mock_parser
    )

    assert result == urls
    assert mock_parser.call_count == 2
    assert mock_sync_delay.call_count == 2


@patch("memory.workers.tasks.comic.requests.get")
def test_sync_comic_success(mock_get, mock_image_response, db_session, qdrant):
    """Test successful comic synchronization."""
    mock_get.return_value = mock_image_response

    comic.sync_comic(
        url="https://example.com/comic/1",
        image_url="https://example.com/image.png",
        title="Test Comic",
        author="https://example.com",
        published_date=datetime(2024, 1, 1, 12, 0, 0),
    )

    # Verify comic was created in database
    saved_comic = (
        db_session.query(Comic)
        .filter(Comic.url == "https://example.com/comic/1")
        .first()
    )
    assert saved_comic is not None
    assert saved_comic.title == "Test Comic"
    assert saved_comic.author == "https://example.com"
    assert saved_comic.mime_type == "image/png"
    assert saved_comic.size == len(mock_image_response.content)
    assert "comic" in saved_comic.tags
    assert "https://example.com" in saved_comic.tags

    # Verify vectors were added to Qdrant
    vectors, _ = qdrant.scroll(collection_name="comic")
    expected_vectors = [
        (
            {
                "author": "https://example.com",
                "published": "2024-01-01T12:00:00",
                "tags": ["comic", "https://example.com"],
                "title": "Test Comic",
                "url": "https://example.com/comic/1",
                "source_id": 1,
            },
            None,
        )
    ]
    assert [
        ({**v.payload, "tags": sorted(v.payload["tags"])}, v.vector) for v in vectors
    ] == expected_vectors


def test_sync_comic_already_exists(db_session):
    """Test that duplicate comics are not processed."""
    # Add existing comic
    existing_comic = Comic(
        title="Existing Comic",
        url="https://example.com/comic/1",
        author="https://example.com",
        filename="/test/path",
        sha256=b"test_hash",
        modality="comic",
        tags=["comic"],
    )
    db_session.add(existing_comic)
    db_session.commit()

    with patch("memory.workers.tasks.comic.requests.get") as mock_get:
        result = comic.sync_comic(
            url="https://example.com/comic/1",
            image_url="https://example.com/image.png",
            title="Test Comic",
            author="https://example.com",
        )

        # Should return early without making HTTP request
        mock_get.assert_not_called()
        assert result == {"comic_id": 1, "status": "already_exists"}


@patch("memory.workers.tasks.comic.requests.get")
def test_sync_comic_http_error(mock_get, db_session, qdrant):
    """Test handling of HTTP errors when downloading image."""
    mock_response = Mock()
    mock_response.status_code = 404
    mock_response.content = b""
    mock_get.return_value = mock_response

    comic.sync_comic(
        url="https://example.com/comic/1",
        image_url="https://example.com/image.png",
        title="Test Comic",
        author="https://example.com",
    )

    assert not (
        db_session.query(Comic)
        .filter(Comic.url == "https://example.com/comic/1")
        .first()
    )


@patch("memory.workers.tasks.comic.requests.get")
def test_sync_comic_no_published_date(
    mock_get, mock_image_response, db_session, qdrant
):
    """Test comic sync without published date."""
    mock_get.return_value = mock_image_response

    comic.sync_comic(
        url="https://example.com/comic/1",
        image_url="https://example.com/image.png",
        title="Test Comic",
        author="https://example.com",
        published_date=None,
    )

    saved_comic = (
        db_session.query(Comic)
        .filter(Comic.url == "https://example.com/comic/1")
        .first()
    )
    assert saved_comic is not None
    assert saved_comic.published is None


@patch("memory.workers.tasks.comic.requests.get")
def test_sync_comic_special_characters_in_title(
    mock_get, mock_image_response, db_session, qdrant
):
    """Test comic sync with special characters in title."""
    mock_get.return_value = mock_image_response

    comic.sync_comic(
        url="https://example.com/comic/1",
        image_url="https://example.com/image.png",
        title="Test/Comic: With*Special?Characters",
        author="https://example.com",
    )

    # Verify comic was created with cleaned title
    saved_comic = (
        db_session.query(Comic)
        .filter(Comic.url == "https://example.com/comic/1")
        .first()
    )
    assert saved_comic is not None
    assert saved_comic.title == "Test/Comic: With*Special?Characters"


@patch(
    "memory.common.embedding.embed_source_item",
    side_effect=Exception("Embedding failed"),
)
def test_sync_comic_embedding_failure(
    mock_embed_source_item, mock_image_response, db_session, qdrant
):
    """Test handling of embedding failures."""
    result = comic.sync_comic(
        url="https://example.com/comic/1",
        image_url="https://example.com/image.png",
        title="Test Comic",
        author="https://example.com",
    )
    assert result == {
        "comic_id": 1,
        "title": "Test Comic",
        "status": "processed",
        "chunks_count": 0,
        "embed_status": "FAILED",
        "content_length": 90,
    }


@patch("memory.workers.tasks.comic.sync_xkcd.delay")
@patch("memory.workers.tasks.comic.sync_smbc.delay")
def test_sync_all_comics(mock_smbc_delay, mock_xkcd_delay):
    """Test synchronization of all comics."""
    comic.sync_all_comics()

    mock_smbc_delay.assert_called_once()
    mock_xkcd_delay.assert_called_once()


@patch("memory.workers.tasks.comic.sync_comic.delay")
@patch("memory.workers.tasks.comic.comics.extract_xkcd")
@patch("memory.workers.tasks.comic.comics.extract_smbc")
@patch("requests.get")
def test_trigger_comic_sync_smbc_navigation(
    mock_get, mock_extract_smbc, mock_extract_xkcd, mock_sync_delay, mock_comic_info
):
    """Test full SMBC comic sync with navigation."""
    # Mock HTML responses for navigation
    mock_responses = [
        Mock(text='<a class="cc-prev" href="https://smbc.com/comic/2"></a>'),
        Mock(text='<a class="cc-prev" href="https://smbc.com/comic/1"></a>'),
        Mock(text="<div>No prev link</div>"),  # End of navigation
    ]
    mock_get.side_effect = mock_responses
    mock_extract_smbc.return_value = mock_comic_info
    mock_extract_xkcd.return_value = mock_comic_info

    comic.trigger_comic_sync()

    # Should have called extract_smbc for each discovered URL
    assert mock_extract_smbc.call_count == 2
    mock_extract_smbc.assert_any_call("https://smbc.com/comic/2")
    mock_extract_smbc.assert_any_call("https://smbc.com/comic/1")

    # Should have called extract_xkcd for range 1-307
    assert mock_extract_xkcd.call_count == 307

    # Should have queued sync tasks
    assert mock_sync_delay.call_count == 2 + 307  # SMBC + XKCD


@patch("memory.workers.tasks.comic.sync_comic.delay")
@patch("memory.workers.tasks.comic.comics.extract_smbc")
@patch("requests.get")
def test_trigger_comic_sync_smbc_extraction_error(
    mock_get, mock_extract_smbc, mock_sync_delay
):
    """Test handling of extraction errors during full sync."""
    # Mock responses: first one has a prev link, second one doesn't
    mock_responses = [
        Mock(text='<a class="cc-prev" href="https://smbc.com/comic/1"></a>'),
        Mock(text="<div>No prev link</div>"),
    ]
    mock_get.side_effect = mock_responses
    mock_extract_smbc.side_effect = Exception("Extraction failed")

    # Should not raise exception, just log error
    comic.trigger_comic_sync()

    mock_extract_smbc.assert_called_once_with("https://smbc.com/comic/1")
    mock_sync_delay.assert_not_called()


@patch("memory.workers.tasks.comic.sync_comic.delay")
@patch("memory.workers.tasks.comic.comics.extract_xkcd")
@patch("requests.get")
def test_trigger_comic_sync_xkcd_extraction_error(
    mock_get, mock_extract_xkcd, mock_sync_delay
):
    """Test handling of XKCD extraction errors during full sync."""
    mock_get.return_value = Mock(text="<div>No prev link</div>")
    mock_extract_xkcd.side_effect = Exception("XKCD extraction failed")

    # Should not raise exception, just log errors
    comic.trigger_comic_sync()

    # Should attempt all 307 XKCD comics
    assert mock_extract_xkcd.call_count == 307
    mock_sync_delay.assert_not_called()
