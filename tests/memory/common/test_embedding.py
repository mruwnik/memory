import uuid
import pytest
from unittest.mock import Mock, patch
from PIL import Image
import pathlib
from memory.common import settings
from memory.common.embedding import (
    get_modality,
    embed_text,
    embed_file,
    embed_mixed,
    embed_page,
    embed,
    write_to_file,
    make_chunk,
)


@pytest.fixture
def mock_embed(mock_voyage_client):
    vectors = ([i] for i in range(1000))

    def embed(texts, model, input_type):
        return Mock(embeddings=[next(vectors) for _ in texts])

    mock_voyage_client.embed.side_effect = embed
    mock_voyage_client.multimodal_embed.side_effect = embed

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
    assert get_modality(mime_type) == expected_modality


def test_embed_text(mock_embed):
    texts = ["text1 with words", "text2"]
    assert embed_text(texts) == [[0], [1]]


def test_embed_file(mock_embed, tmp_path):
    mock_file = tmp_path / "test.txt"
    mock_file.write_text("file content")

    assert embed_file(mock_file) == [[0]]


def test_embed_mixed(mock_embed):
    items = ["text", {"type": "image", "data": "base64"}]
    assert embed_mixed(items) == [[0]]


def test_embed_page_text_only(mock_embed):
    page = {"contents": ["text1", "text2"]}
    assert embed_page(page) == [[0], [1]]


def test_embed_page_mixed_content(mock_embed):
    page = {"contents": ["text", {"type": "image", "data": "base64"}]}
    assert embed_page(page) == [[0]]


def test_embed(mock_embed):
    mime_type = "text/plain"
    content = "sample content"
    metadata = {"source": "test"}

    with patch.object(uuid, "uuid4", return_value="id1"):
        modality, chunks = embed(mime_type, content, metadata)

    assert modality == "text"
    assert [
        {
            "id": c.id,
            "file_path": c.file_path,
            "content": c.content,
            "embedding_model": c.embedding_model,
            "vector": c.vector,
            "item_metadata": c.item_metadata,
        }
        for c in chunks
    ] == [
        {
            "content": "sample content",
            "embedding_model": "voyage-3-large",
            "file_path": None,
            "id": "id1",
            "item_metadata": {"source": "test"},
            "vector": [0],
        },
    ]


def test_write_to_file_text(mock_file_storage):
    """Test writing a string to a file."""
    chunk_id = "test-chunk-id"
    content = "This is a test string"

    file_path = write_to_file(chunk_id, content)

    assert file_path == settings.FILE_STORAGE_DIR / f"{chunk_id}.txt"
    assert file_path.exists()
    assert file_path.read_text() == content


def test_write_to_file_bytes(mock_file_storage):
    """Test writing bytes to a file."""
    chunk_id = "test-chunk-id"
    content = b"These are test bytes"

    file_path = write_to_file(chunk_id, content)

    assert file_path == settings.FILE_STORAGE_DIR / f"{chunk_id}.bin"
    assert file_path.exists()
    assert file_path.read_bytes() == content


def test_write_to_file_image(mock_file_storage):
    """Test writing an image to a file."""
    img = Image.new("RGB", (100, 100), color=(73, 109, 137))
    chunk_id = "test-chunk-id"

    file_path = write_to_file(chunk_id, img)

    assert file_path == settings.FILE_STORAGE_DIR / f"{chunk_id}.png"
    assert file_path.exists()
    # Verify it's a valid image file by opening it
    image = Image.open(file_path)
    assert image.size == (100, 100)


def test_write_to_file_unsupported_type(mock_file_storage):
    """Test that an error is raised for unsupported content types."""
    chunk_id = "test-chunk-id"
    content = 123  # Integer is not a supported type

    with pytest.raises(ValueError, match="Unsupported content type"):
        write_to_file(chunk_id, content)


def test_make_chunk_text_only(mock_file_storage, db_session):
    """Test creating a chunk from string content."""
    page = {
        "contents": ["text content 1", "text content 2"],
        "metadata": {"source": "test"},
    }
    vector = [0.1, 0.2, 0.3]
    metadata = {"doc_type": "test", "source": "unit-test"}

    with patch.object(
        uuid, "uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000001")
    ):
        chunk = make_chunk(page, vector, metadata)

    assert chunk.id == "00000000-0000-0000-0000-000000000001"
    assert chunk.content == "text content 1\n\ntext content 2"
    assert chunk.file_path is None
    assert chunk.embedding_model == settings.TEXT_EMBEDDING_MODEL
    assert chunk.vector == vector
    assert chunk.item_metadata == metadata


def test_make_chunk_single_image(mock_file_storage, db_session):
    """Test creating a chunk from a single image."""
    img = Image.new("RGB", (100, 100), color=(73, 109, 137))
    page = {"contents": [img], "metadata": {"source": "test"}}
    vector = [0.1, 0.2, 0.3]
    metadata = {"doc_type": "test", "source": "unit-test"}

    with patch.object(
        uuid, "uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000002")
    ):
        chunk = make_chunk(page, vector, metadata)

    assert chunk.id == "00000000-0000-0000-0000-000000000002"
    assert chunk.content is None
    assert chunk.file_path == (
        settings.FILE_STORAGE_DIR / "00000000-0000-0000-0000-000000000002.png",
    )
    assert chunk.embedding_model == settings.MIXED_EMBEDDING_MODEL
    assert chunk.vector == vector
    assert chunk.item_metadata == metadata

    # Verify the file exists
    assert pathlib.Path(chunk.file_path[0]).exists()


def test_make_chunk_mixed_content(mock_file_storage, db_session):
    """Test creating a chunk from mixed content (string and image)."""
    img = Image.new("RGB", (100, 100), color=(73, 109, 137))
    page = {"contents": ["text content", img], "metadata": {"source": "test"}}
    vector = [0.1, 0.2, 0.3]
    metadata = {"doc_type": "test", "source": "unit-test"}

    with patch.object(
        uuid, "uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000003")
    ):
        chunk = make_chunk(page, vector, metadata)

    assert chunk.id == "00000000-0000-0000-0000-000000000003"
    assert chunk.content is None
    assert chunk.file_path == (
        settings.FILE_STORAGE_DIR / "00000000-0000-0000-0000-000000000003_*",
    )
    assert chunk.embedding_model == settings.MIXED_EMBEDDING_MODEL
    assert chunk.vector == vector
    assert chunk.item_metadata == metadata

    # Verify the files exist
    assert (
        settings.FILE_STORAGE_DIR / "00000000-0000-0000-0000-000000000003_0.txt"
    ).exists()
    assert (
        settings.FILE_STORAGE_DIR / "00000000-0000-0000-0000-000000000003_1.png"
    ).exists()
