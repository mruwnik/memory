import pathlib
import uuid
from unittest.mock import Mock, patch
from typing import cast
import pytest
from PIL import Image

from memory.common import settings, collections
from memory.common.embedding import (
    embed_mixed,
    embed_text,
    make_chunk,
    write_to_file,
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
    assert collections.get_modality(mime_type) == expected_modality


def test_embed_text(mock_embed):
    texts = ["text1 with words", "text2"]
    assert embed_text(texts) == [[0], [1]]


def test_embed_mixed(mock_embed):
    items = ["text", {"type": "image", "data": "base64"}]
    assert embed_mixed(items) == [[0]]


def test_write_to_file_text(mock_file_storage):
    """Test writing a string to a file."""
    chunk_id = "test-chunk-id"
    content = "This is a test string"

    file_path = write_to_file(chunk_id, content)

    assert file_path == settings.CHUNK_STORAGE_DIR / f"{chunk_id}.txt"
    assert file_path.exists()
    assert file_path.read_text() == content


def test_write_to_file_bytes(mock_file_storage):
    """Test writing bytes to a file."""
    chunk_id = "test-chunk-id"
    content = b"These are test bytes"

    file_path = write_to_file(chunk_id, content)  # type: ignore

    assert file_path == settings.CHUNK_STORAGE_DIR / f"{chunk_id}.bin"
    assert file_path.exists()
    assert file_path.read_bytes() == content


def test_write_to_file_image(mock_file_storage):
    """Test writing an image to a file."""
    img = Image.new("RGB", (100, 100), color=(73, 109, 137))
    chunk_id = "test-chunk-id"

    file_path = write_to_file(chunk_id, img)  # type: ignore

    assert file_path == settings.CHUNK_STORAGE_DIR / f"{chunk_id}.png"
    assert file_path.exists()
    # Verify it's a valid image file by opening it
    image = Image.open(file_path)
    assert image.size == (100, 100)


def test_write_to_file_unsupported_type(mock_file_storage):
    """Test that an error is raised for unsupported content types."""
    chunk_id = "test-chunk-id"
    content = 123  # Integer is not a supported type

    with pytest.raises(ValueError, match="Unsupported content type"):
        write_to_file(chunk_id, content)  # type: ignore


def test_make_chunk_text_only(mock_file_storage, db_session):
    """Test creating a chunk from string content."""
    contents = ["text content 1", "text content 2"]
    vector = [0.1, 0.2, 0.3]
    metadata = {"doc_type": "test", "source": "unit-test"}

    with patch.object(
        uuid, "uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000001")
    ):
        chunk = make_chunk(contents, vector, metadata)  # type: ignore

    assert cast(str, chunk.id) == "00000000-0000-0000-0000-000000000001"
    assert cast(str, chunk.content) == "text content 1\n\ntext content 2"
    assert chunk.file_path is None
    assert cast(str, chunk.embedding_model) == settings.TEXT_EMBEDDING_MODEL
    assert chunk.vector == vector
    assert chunk.item_metadata == metadata


def test_make_chunk_single_image(mock_file_storage, db_session):
    """Test creating a chunk from a single image."""
    img = Image.new("RGB", (100, 100), color=(73, 109, 137))
    contents = [img]
    vector = [0.1, 0.2, 0.3]
    metadata = {"doc_type": "test", "source": "unit-test"}

    with patch.object(
        uuid, "uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000002")
    ):
        chunk = make_chunk(contents, vector, metadata)  # type: ignore

    assert cast(str, chunk.id) == "00000000-0000-0000-0000-000000000002"
    assert chunk.content is None
    assert cast(str, chunk.file_path) == str(
        settings.CHUNK_STORAGE_DIR / "00000000-0000-0000-0000-000000000002.png",
    )
    assert cast(str, chunk.embedding_model) == settings.MIXED_EMBEDDING_MODEL
    assert chunk.vector == vector
    assert chunk.item_metadata == metadata

    # Verify the file exists
    assert pathlib.Path(cast(str, chunk.file_path)).exists()


def test_make_chunk_mixed_content(mock_file_storage, db_session):
    """Test creating a chunk from mixed content (string and image)."""
    img = Image.new("RGB", (100, 100), color=(73, 109, 137))
    contents = ["text content", img]
    vector = [0.1, 0.2, 0.3]
    metadata = {"doc_type": "test", "source": "unit-test"}

    with patch.object(
        uuid, "uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000003")
    ):
        chunk = make_chunk(contents, vector, metadata)  # type: ignore

    assert cast(str, chunk.id) == "00000000-0000-0000-0000-000000000003"
    assert chunk.content is None
    assert cast(str, chunk.file_path) == str(
        settings.CHUNK_STORAGE_DIR / "00000000-0000-0000-0000-000000000003_*",
    )
    assert cast(str, chunk.embedding_model) == settings.MIXED_EMBEDDING_MODEL
    assert chunk.vector == vector
    assert chunk.item_metadata == metadata

    # Verify the files exist
    assert (
        settings.CHUNK_STORAGE_DIR / "00000000-0000-0000-0000-000000000003_0.txt"
    ).exists()
    assert (
        settings.CHUNK_STORAGE_DIR / "00000000-0000-0000-0000-000000000003_1.png"
    ).exists()


@pytest.mark.parametrize(
    "data,embedding_model,collection,expected_model,expected_count,expected_has_content",
    [
        # Text-only with default model
        (
            ["text content 1", "text content 2"],
            None,
            None,
            settings.TEXT_EMBEDDING_MODEL,
            2,
            True,
        ),
        # Text with explicit mixed model - but make_chunk still uses TEXT_EMBEDDING_MODEL for text-only content
        (
            ["text content"],
            settings.MIXED_EMBEDDING_MODEL,
            None,
            settings.TEXT_EMBEDDING_MODEL,
            1,
            True,
        ),
        # Text collection model selection - make_chunk uses TEXT_EMBEDDING_MODEL for text-only content
        (["text content"], None, "mail", settings.TEXT_EMBEDDING_MODEL, 1, True),
        (["text content"], None, "photo", settings.TEXT_EMBEDDING_MODEL, 1, True),
        (["text content"], None, "doc", settings.TEXT_EMBEDDING_MODEL, 1, True),
        # Unknown collection falls back to default
        (["text content"], None, "unknown", settings.TEXT_EMBEDDING_MODEL, 1, True),
        # Explicit model takes precedence over collection
        (
            ["text content"],
            settings.TEXT_EMBEDDING_MODEL,
            "photo",
            settings.TEXT_EMBEDDING_MODEL,
            1,
            True,
        ),
    ],
)
def test_embed_data_chunk_scenarios(
    data,
    embedding_model,
    collection,
    expected_model,
    expected_count,
    expected_has_content,
    mock_embed,
    mock_file_storage,
):
    """Test various embedding scenarios for data chunks."""
    from memory.common.extract import DataChunk
    from memory.common.embedding import embed_data_chunk

    chunk = DataChunk(
        data=data,
        embedding_model=embedding_model,
        collection=collection,
        metadata={"source": "test"},
    )

    result = embed_data_chunk(chunk, {"doc_type": "test"})

    assert len(result) == expected_count
    assert all(cast(str, c.embedding_model) == expected_model for c in result)
    if expected_has_content:
        assert all(c.content is not None for c in result)
        assert all(c.file_path is None for c in result)
    else:
        assert all(c.content is None for c in result)
        assert all(c.file_path is not None for c in result)
    assert all(
        c.item_metadata == {"source": "test", "doc_type": "test"} for c in result
    )


def test_embed_data_chunk_mixed_content(mock_embed, mock_file_storage):
    """Test embedding mixed content (text and images)."""
    from memory.common.extract import DataChunk
    from memory.common.embedding import embed_data_chunk

    img = Image.new("RGB", (100, 100), color=(73, 109, 137))
    chunk = DataChunk(
        data=["text content", img],
        embedding_model=settings.MIXED_EMBEDDING_MODEL,
        metadata={"source": "test"},
    )

    result = embed_data_chunk(chunk)

    assert len(result) == 1  # Mixed content returns single vector
    assert result[0].content is None  # Mixed content stored in files
    assert result[0].file_path is not None
    assert cast(str, result[0].embedding_model) == settings.MIXED_EMBEDDING_MODEL


@pytest.mark.parametrize(
    "chunk_max_size,chunk_size_param,expected_chunk_size",
    [
        (512, 1024, 512),  # chunk.max_size takes precedence
        (None, 2048, 2048),  # chunk_size parameter used when max_size is None
        (256, None, 256),  # chunk.max_size used when parameter is None
    ],
)
def test_embed_data_chunk_chunk_size_handling(
    chunk_max_size, chunk_size_param, expected_chunk_size, mock_embed, mock_file_storage
):
    """Test chunk size parameter handling."""
    from memory.common.extract import DataChunk
    from memory.common.embedding import embed_data_chunk

    chunk = DataChunk(
        data=["text content"], max_size=chunk_max_size, metadata={"source": "test"}
    )

    with patch("memory.common.embedding.embed_text") as mock_embed_text:
        mock_embed_text.return_value = [[0.1, 0.2, 0.3]]

        result = embed_data_chunk(chunk, chunk_size=chunk_size_param)

        mock_embed_text.assert_called_once()
        args, kwargs = mock_embed_text.call_args
        assert kwargs["chunk_size"] == expected_chunk_size


def test_embed_data_chunk_metadata_merging(mock_embed, mock_file_storage):
    """Test that chunk metadata and parameter metadata are properly merged."""
    from memory.common.extract import DataChunk
    from memory.common.embedding import embed_data_chunk

    chunk = DataChunk(
        data=["text content"], metadata={"source": "test", "type": "chunk"}
    )
    metadata = {
        "doc_type": "test",
        "source": "override",
    }  # chunk.metadata takes precedence over parameter metadata

    result = embed_data_chunk(chunk, metadata)

    assert len(result) == 1
    expected_metadata = {
        "source": "test",
        "type": "chunk",
        "doc_type": "test",
    }  # chunk source wins
    assert result[0].item_metadata == expected_metadata


def test_embed_data_chunk_unsupported_model(mock_embed, mock_file_storage):
    """Test error handling for unsupported embedding model."""
    from memory.common.extract import DataChunk
    from memory.common.embedding import embed_data_chunk

    chunk = DataChunk(
        data=["text content"],
        embedding_model="unsupported-model",
        metadata={"source": "test"},
    )

    with pytest.raises(ValueError, match="Unsupported model: unsupported-model"):
        embed_data_chunk(chunk)


def test_embed_data_chunk_empty_data(mock_embed, mock_file_storage):
    """Test handling of empty data."""
    from memory.common.extract import DataChunk
    from memory.common.embedding import embed_data_chunk

    chunk = DataChunk(data=[], metadata={"source": "test"})

    # Should handle empty data gracefully
    with patch("memory.common.embedding.embed_text") as mock_embed_text:
        mock_embed_text.return_value = []

        result = embed_data_chunk(chunk)

        assert result == []
