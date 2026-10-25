# pyright: reportPrivateImportUsage=false
import sys
import threading
from unittest.mock import Mock
import anthropic
import httpx2
import pytest
from typing import cast
from PIL import Image

from memory.common import collections, content_processing, embedding, extract, settings
from memory.common.embedding import (
    as_string,
    embed_chunks,
    embed_mixed,
    embed_text,
    break_chunk,
    embed_by_model,
)
from memory.common.extract import DataChunk, MulitmodalChunk
from memory.common.db.models import Chunk, LLMProviderKey, Note, SummaryRequest
from tests.conftest import unique_sha256
from tests.memory.agents.fakes import key_rejected, submitted


@pytest.fixture
def mock_embed(mock_voyage_client):
    vectors = ([i] for i in range(1000))

    def embed_func(texts, model, input_type):
        return Mock(embeddings=[next(vectors) for _ in texts])

    mock_voyage_client.embed = Mock(side_effect=embed_func)
    mock_voyage_client.multimodal_embed = Mock(side_effect=embed_func)

    return mock_voyage_client


@pytest.mark.parametrize(
    "input_type, timeout, attempts",
    [
        ("query", settings.QUERY_EMBEDDING_TIMEOUT, 2),
        ("document", 60.0, 3),
    ],
)
def test_embed_chunks_query_fails_fast(mock_voyage_client, monkeypatch, input_type, timeout, attempts):
    mock_voyage_client.embed = Mock(side_effect=RuntimeError("voyage down"))
    monkeypatch.setattr(embedding.time, "sleep", Mock())

    with pytest.raises(embedding.EmbeddingError):
        embed_chunks([["q"]], input_type=input_type)

    assert cast(Mock, embedding.voyageai.Client).call_args.kwargs["timeout"] == timeout
    assert mock_voyage_client.embed.call_count == attempts


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


def test_embed_chunks_does_not_retry_decompression_bomb(mock_voyage_client, monkeypatch):
    # A decompression bomb is deterministic: retrying it just burns backoff
    # sleeps before the inevitable failure. It must propagate on the first try.
    import memory.common.embedding as emb

    mock_voyage_client.multimodal_embed = Mock(
        side_effect=Image.DecompressionBombError("boom")
    )
    slept = Mock()
    monkeypatch.setattr(emb.time, "sleep", slept)

    with pytest.raises(Image.DecompressionBombError):
        embed_chunks([["x"]], model=settings.MIXED_EMBEDDING_MODEL)

    mock_voyage_client.multimodal_embed.assert_called_once()
    slept.assert_not_called()


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


def test_query_embedding_cache_survives_concurrent_threads(monkeypatch):
    # Query embedding runs in asyncio.to_thread workers; text and multimodal
    # searches embed at the same time. A tiny switch interval makes an
    # unlocked cache fail here almost every run.
    monkeypatch.setattr(embedding, "_query_embedding_cache", {})
    errors = []

    def churn(thread_no: int):
        try:
            for i in range(2000):
                query = f"{thread_no}-{i % 300}"
                embedding._cache_embedding(query, "model", [0.0])
                embedding._get_cached_embedding(query, "model")
        except Exception as e:
            errors.append(e)

    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=churn, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(previous)

    assert errors == []


LONG = "word " * 5000


def note_by(db, user_id):
    note = Note(modality="note", sha256=unique_sha256("note"), content=LONG, creator_id=user_id)
    db.add(note)
    db.flush()
    return note


def test_items_with_a_key_get_a_summary_chunk_paid_with_it(db_session, regular_user, llm_key, scripted_llm):
    scripted_llm.script = [submitted({"summary": "short", "tags": ["t"]})]
    note = note_by(db_session, regular_user.id)

    chunks = embedding.embed_source_item(note)

    assert "short" in [c.content for c in chunks]
    assert scripted_llm.requests[0]["api_key"] == "sk-test-default"


def test_items_without_a_key_are_embedded_without_a_summary(db_session, regular_user, scripted_llm):
    note = note_by(db_session, regular_user.id)

    chunks = embedding.embed_source_item(note)

    assert chunks
    assert scripted_llm.requests == []


def test_items_without_a_voyage_key_are_stored_failed_without_summarising(
    db_session, regular_user, llm_key, scripted_llm, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "VOYAGE_API_KEY", "")
    note = note_by(db_session, regular_user.id)

    assert content_processing.embed_source_item(note) == 0

    assert note.embed_status == "FAILED"
    assert scripted_llm.requests == []
    assert not [r for r in caplog.records if r.exc_info]


async def connection_dropped():
    raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))


async def stream_dropped():
    raise httpx2.ReadError("connection reset mid-stream")


@pytest.mark.parametrize("failure", [key_rejected, connection_dropped, stream_dropped])
def test_a_failed_summary_still_embeds_the_item(db_session, regular_user, llm_key, scripted_llm, failure):
    scripted_llm.script = [failure]
    note = note_by(db_session, regular_user.id)

    chunks = embedding.embed_source_item(note)

    assert len(scripted_llm.requests) == 1
    assert len(chunks) == len(extract.extract_text(LONG, skip_summary=True))


def test_batching_defers_keyed_summaries_without_calling_the_llm(
    db_session, regular_user, llm_key, scripted_llm, monkeypatch
):
    monkeypatch.setattr(settings, "SUMMARIZER_USE_BATCH", True)
    note = note_by(db_session, regular_user.id)

    embedding.embed_source_item(note)

    assert scripted_llm.requests == []
    assert len(note.summary_requests) == 1


def test_openai_keys_summarise_inline_because_only_anthropic_batches(
    db_session, regular_user, scripted_llm, monkeypatch
):
    monkeypatch.setattr(settings, "SUMMARIZER_USE_BATCH", True)
    gpt = LLMProviderKey(user_id=regular_user.id, provider="openai", label="gpt", is_default=True)
    gpt.key = "sk-gpt"
    db_session.add(gpt)
    scripted_llm.script = [submitted({"summary": "short", "tags": ["t"]})]
    note = note_by(db_session, regular_user.id)

    embedding.embed_source_item(note)

    assert [r["model"] for r in scripted_llm.requests] == ["openai/gpt-6-luna"]
    assert note.summary_requests == []


def test_reembedding_an_item_that_lost_its_key_drops_its_pending_requests(
    db_session, regular_user, llm_key, scripted_llm, monkeypatch
):
    monkeypatch.setattr(settings, "SUMMARIZER_USE_BATCH", True)
    note = note_by(db_session, regular_user.id)
    embedding.embed_source_item(note)
    db_session.commit()
    queued = db_session.query(SummaryRequest).count()
    llm_key.status = "invalid"
    db_session.commit()

    embedding.embed_source_item(note)
    db_session.commit()

    assert (queued, db_session.query(SummaryRequest).count()) == (1, 0)
    assert scripted_llm.requests == []
