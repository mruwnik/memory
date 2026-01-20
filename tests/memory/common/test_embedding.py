from unittest.mock import Mock
import pytest
from typing import cast
from PIL import Image

from memory.common import collections, settings
from memory.common.embedding import (
    as_string,
    embed_chunks,
    embed_mixed,
    embed_text,
    break_chunk,
    embed_by_model,
)
from memory.common.extract import DataChunk, MulitmodalChunk
from memory.common.db.models import Chunk


@pytest.fixture
def mock_embed(mock_voyage_client):
    vectors = ([i] for i in range(1000))

    def embed_func(texts, model, input_type):
        return Mock(embeddings=[next(vectors) for _ in texts])

    mock_voyage_client.embed = Mock(side_effect=embed_func)
    mock_voyage_client.multimodal_embed = Mock(side_effect=embed_func)

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
    chunks = [DataChunk(data=["text1 with words"]), DataChunk(data=["text2"])]
    assert embed_text(chunks) == [[0], [1]]


def test_embed_mixed(mock_embed):
    items = [DataChunk(data=["text"])]
    assert embed_mixed(items) == [[0]]


@pytest.mark.parametrize(
    "input_data, expected_output",
    [
        ("hello world", "hello world"),
        ("  hello world  \n", "hello world"),
        (
            cast(list[MulitmodalChunk], ["first chunk", "second chunk", "third chunk"]),
            "first chunk\nsecond chunk\nthird chunk",
        ),
        (cast(list[MulitmodalChunk], []), ""),
        (
            cast(list[MulitmodalChunk], ["", "valid text", "  ", "another text"]),
            "valid text\n\nanother text",
        ),
    ],
)
def test_as_string_basic_cases(input_data, expected_output):
    assert as_string(input_data) == expected_output


def test_as_string_with_nested_lists():
    # This tests the recursive nature of as_string - kept separate due to different input type
    chunks = [["nested", "items"], "single item"]
    result = as_string(chunks)
    assert result == "nested\nitems\nsingle item"


def test_embed_chunks_with_text_model(mock_embed):
    chunks = cast(list[list[MulitmodalChunk]], [["text1"], ["text2"]])
    result = embed_chunks(chunks, model=settings.TEXT_EMBEDDING_MODEL)
    assert result == [[0], [1]]
    mock_embed.embed.assert_called_once_with(
        ["text1", "text2"],
        model=settings.TEXT_EMBEDDING_MODEL,
        input_type="document",
    )


def test_embed_chunks_with_mixed_model(mock_embed):
    chunks = cast(list[list[MulitmodalChunk]], [["text with image"], ["another chunk"]])
    result = embed_chunks(chunks, model=settings.MIXED_EMBEDDING_MODEL)
    assert result == [[0], [1]]
    mock_embed.multimodal_embed.assert_called_once_with(
        chunks, model=settings.MIXED_EMBEDDING_MODEL, input_type="document"
    )


def test_embed_chunks_with_query_input_type(mock_embed):
    chunks = cast(list[list[MulitmodalChunk]], [["query text"]])
    result = embed_chunks(chunks, input_type="query")
    assert result == [[0]]
    mock_embed.embed.assert_called_once_with(
        ["query text"], model=settings.TEXT_EMBEDDING_MODEL, input_type="query"
    )


def test_embed_chunks_empty_list(mock_embed):
    result = embed_chunks([])
    assert result == []


@pytest.mark.parametrize(
    "data, chunk_size, expected_result",
    [
        (["short text"], 100, ["short text"]),
        (["some text content"], 200, ["some text content"]),
        ([], 100, []),
    ],
)
def test_break_chunk_simple_cases(data, chunk_size, expected_result):
    chunk = DataChunk(data=data)
    result = break_chunk(chunk, chunk_size=chunk_size)
    assert result == expected_result


def test_break_chunk_with_long_text():
    # Create text that will exceed chunk size
    long_text = "word " * 200  # Should be much longer than default chunk size
    chunk = DataChunk(data=[long_text])
    result = break_chunk(chunk, chunk_size=50)

    # Should be broken into multiple chunks
    assert len(result) > 1
    assert all(isinstance(item, str) for item in result)


def test_break_chunk_with_mixed_data_types():
    # Mock image object
    mock_image = Mock(spec=Image.Image)
    chunk = DataChunk(data=["text content", mock_image])
    result = break_chunk(chunk, chunk_size=100)

    # Should have text chunks plus the image (non-string items are passed through)
    assert len(result) >= 2
    assert any(isinstance(item, str) for item in result)
    # The individual non-string item (image) should be in result, not the DataChunk
    assert mock_image in result
    # The DataChunk itself should NOT be in the result
    assert chunk not in result


def test_break_chunk_preserves_non_string_items():
    """Non-string items (like images) should be preserved individually."""
    mock_image1 = Mock(spec=Image.Image)
    mock_image2 = Mock(spec=Image.Image)
    chunk = DataChunk(data=[mock_image1, "some text", mock_image2])
    result = break_chunk(chunk, chunk_size=100)

    # Both images should be in result
    assert mock_image1 in result
    assert mock_image2 in result
    # Text should be chunked
    assert "some text" in result
    # Total should be 3 items (2 images + 1 short text)
    assert len(result) == 3


def test_embed_by_model_with_matching_chunks(mock_embed):
    # Create mock chunks with specific embedding model
    chunk1 = Mock(spec=Chunk)
    chunk1.embedding_model = "test-model"
    chunk1.chunks = ["chunk1 content"]

    chunk2 = Mock(spec=Chunk)
    chunk2.embedding_model = "test-model"
    chunk2.chunks = ["chunk2 content"]

    chunks = cast(list[Chunk], [chunk1, chunk2])
    result = embed_by_model(chunks, "test-model")

    assert len(result) == 2
    assert chunk1.vector == [0]
    assert chunk2.vector == [1]
    assert result == [chunk1, chunk2]


def test_embed_by_model_with_no_matching_chunks(mock_embed):
    chunk1 = Mock(spec=Chunk)
    chunk1.embedding_model = "different-model"
    # Ensure the chunk doesn't have a vector initially
    del chunk1.vector

    chunks = cast(list[Chunk], [chunk1])
    result = embed_by_model(chunks, "test-model")

    assert result == []
    assert not hasattr(chunk1, "vector")


def test_embed_by_model_with_mixed_models(mock_embed):
    chunk1 = Mock(spec=Chunk)
    chunk1.embedding_model = "test-model"
    chunk1.chunks = ["chunk1 content"]

    chunk2 = Mock(spec=Chunk)
    chunk2.embedding_model = "other-model"
    chunk2.chunks = ["chunk2 content"]

    chunk3 = Mock(spec=Chunk)
    chunk3.embedding_model = "test-model"
    chunk3.chunks = ["chunk3 content"]

    chunks = cast(list[Chunk], [chunk1, chunk2, chunk3])
    result = embed_by_model(chunks, "test-model")

    assert len(result) == 2
    assert chunk1 in result
    assert chunk3 in result
    assert chunk2 not in result
    assert chunk1.vector == [0]
    assert chunk3.vector == [1]


def test_embed_by_model_with_empty_chunks(mock_embed):
    result = embed_by_model([], "test-model")
    assert result == []


def test_embed_by_model_calls_embed_chunks_correctly(mock_embed):
    chunk1 = Mock(spec=Chunk)
    chunk1.embedding_model = "test-model"
    chunk1.chunks = ["content1"]

    chunk2 = Mock(spec=Chunk)
    chunk2.embedding_model = "test-model"
    chunk2.chunks = ["content2"]

    chunks = cast(list[Chunk], [chunk1, chunk2])
    embed_by_model(chunks, "test-model")

    # Verify embed_chunks was called with the right model
    mock_embed.embed.assert_called_once_with(
        ["content1", "content2"], model="test-model", input_type="document"
    )
