from unittest.mock import Mock
import pytest

from memory.common import collections
from memory.common.embedding import (
    embed_mixed,
    embed_text,
)


@pytest.fixture
def mock_embed(mock_voyage_client):
    vectors = ([i] for i in range(1000))

    def embed(texts, model, input_type):
        return Mock(embeddings=[next(vectors) for _ in texts])

    mock_voyage_client.embed = embed
    mock_voyage_client.multimodal_embed = embed

    return mock_voyage_client


@pytest.mark.parametrize(
    "mime_type, expected_modality",
    [
        ("text/plain", "text"),
        ("text/html", "blog"),
        ("image/jpeg", "photo"),
        ("image/png", "photo"),
        ("application/pdf", "doc"),
        ("application/epub+zip", "book"),
        ("application/mobi", "book"),
        ("application/x-mobipocket-ebook", "book"),
        ("audio/mp3", "unknown"),
        ("video/mp4", "unknown"),
        ("text/something-new", "text"),  # Should match by 'text/' stem
        ("image/something-new", "photo"),  # Should match by 'image/' stem
        ("custom/format", "unknown"),  # No matching stem
    ],
)
def test_get_modality(mime_type, expected_modality):
    assert collections.get_modality(mime_type) == expected_modality


def test_embed_text(mock_embed):
    texts = ["text1 with words", "text2"]
    assert embed_text(texts) == [[0], [1]]


def test_embed_mixed(mock_embed):
    items = ["text", {"type": "image", "data": "base64"}]
    assert embed_mixed(items) == [[0]]
