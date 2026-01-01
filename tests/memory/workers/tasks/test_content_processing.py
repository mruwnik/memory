import hashlib
from unittest.mock import MagicMock, patch

import pytest

from memory.common.db.models import (
    BlogPost,
    Chunk,
    ChatMessage,
    MailMessage,
    SourceItem,
)
from memory.workers.tasks.content_processing import (
    check_content_exists,
    create_content_hash,
    create_task_result,
    embed_source_item,
    process_content_item,
    push_to_qdrant,
    safe_task_execution,
    by_collection,
)


@pytest.fixture
def mock_uuid4():
    ids = (f"00000000-0000-0000-0000-000000000{i:04d}" for i in range(1, 1000))
    with patch("uuid.uuid4", side_effect=ids):
        yield


@pytest.fixture
def sample_mail_message():
    """Create a standard MailMessage for testing."""
    return MailMessage(
        sha256=b"test_hash" + bytes(24),
        tags=["test"],
        size=100,
        mime_type="message/rfc822",
        embed_status="RAW",
        message_id="<test@example.com>",
        subject="Test Subject",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        content="Test content",
        folder="INBOX",
        modality="mail",
    )


@pytest.fixture
def sample_chunks():
    """Create sample chunks for testing."""
    return [
        Chunk(
            id="00000000-0000-0000-0000-000000000001",
            content="chunk 1 content",
            embedding_model="test-model",
        ),
        Chunk(
            id="00000000-0000-0000-0000-000000000002",
            content="chunk 2 content",
            embedding_model="test-model",
        ),
    ]


@pytest.fixture
def mock_chunk():
    """Create a mock chunk with required attributes."""
    chunk = MagicMock()
    chunk.id = "00000000-0000-0000-0000-000000000001"
    chunk.vector = [0.1] * 1024
    chunk.item_metadata = {"source_id": 1, "tags": ["test"]}
    chunk.collection_name = "mail"
    return chunk


@pytest.mark.parametrize(
    "search_attr,search_value,expected_found",
    [
        ("sha256", b"test_hash" + bytes(24), True),
        ("message_id", "<test@example.com>", True),
        ("nonexistent_attr", "value", False),
        ("sha256", b"different_hash" + bytes(24), False),
    ],
)
def test_check_content_exists(
    db_session, sample_mail_message, search_attr, search_value, expected_found
):
    db_session.add(sample_mail_message)
    db_session.commit()

    result = check_content_exists(
        db_session, MailMessage, **{search_attr: search_value}
    )

    if expected_found:
        assert result is not None
        assert result.id == sample_mail_message.id
    else:
        assert result is None


@pytest.mark.parametrize(
    "search_params,should_find",
    [
        (
            {"sha256": b"test_hash" + bytes(24), "message_id": "<test@example.com>"},
            True,
        ),
        (
            {
                "sha256": b"different_hash" + bytes(24),
                "message_id": "<test@example.com>",
            },
            True,
        ),
        ({"subject": "Test Subject", "sender": "sender@example.com"}, True),
        ({"subject": "Wrong Subject", "sender": "wrong@example.com"}, False),
    ],
)
def test_check_content_exists_multiple_attributes(
    db_session, sample_mail_message, search_params, should_find
):
    db_session.add(sample_mail_message)
    db_session.commit()

    result = check_content_exists(db_session, MailMessage, **search_params)

    if should_find:
        assert result is not None
        assert result.id == sample_mail_message.id
    else:
        assert result is None


def test_check_content_exists_no_matches(db_session):
    result = check_content_exists(
        db_session,
        MailMessage,
        sha256=b"nonexistent_hash" + bytes(24),
        message_id="<nonexistent@example.com>",
    )
    assert result is None


@pytest.mark.parametrize(
    "content,additional_data,expected_hash",
    [
        ("test content", (), hashlib.sha256(b"test content").digest()),
        ("test content", ("extra1",), hashlib.sha256(b"test contentextra1").digest()),
        (
            "test content",
            ("extra1", "extra2"),
            hashlib.sha256(b"test contentextra1extra2").digest(),
        ),
        ("", (), hashlib.sha256(b"").digest()),
        ("unicode: 🚀", (), hashlib.sha256("unicode: 🚀".encode()).digest()),
    ],
)
def test_create_content_hash(content, additional_data, expected_hash):
    result = create_content_hash(content, *additional_data)
    assert result == expected_hash


def test_create_content_hash_deterministic():
    content = "test content"
    additional = ("extra1", "extra2")

    hash1 = create_content_hash(content, *additional)
    hash2 = create_content_hash(content, *additional)

    assert hash1 == hash2


@pytest.mark.parametrize(
    "mock_return,expected_count,expected_status",
    [
        ("chunks", 2, "QUEUED"),  # Success case
        ([], 0, "FAILED"),  # No chunks case
        ("exception", 0, "FAILED"),  # Exception case
    ],
)
def test_embed_source_item(
    sample_mail_message, sample_chunks, mock_return, expected_count, expected_status
):
    if mock_return == "chunks":
        mock_value = sample_chunks
    elif mock_return == []:
        mock_value = []
    else:  # exception case
        mock_value = Exception("Embedding failed")

    patch_target = "memory.common.embedding.embed_source_item"

    if mock_return == "exception":
        with patch(patch_target, side_effect=mock_value):
            result = embed_source_item(sample_mail_message)
    else:
        with patch(patch_target, return_value=mock_value):
            result = embed_source_item(sample_mail_message)

    assert result == expected_count
    assert str(sample_mail_message.embed_status) == expected_status
    if mock_return == "chunks":
        assert sample_mail_message.chunks == sample_chunks


def test_push_to_qdrant_success(qdrant):
    # Create items with different statuses
    item1 = MailMessage(
        sha256=b"test_hash1" + bytes(23),
        tags=["test"],
        size=100,
        mime_type="message/rfc822",
        embed_status="QUEUED",
        message_id="<test1@example.com>",
        subject="Test Subject 1",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        content="Test content 1",
        folder="INBOX",
        modality="mail",
    )

    item2 = MailMessage(
        sha256=b"test_hash2" + bytes(23),
        tags=["test"],
        size=100,
        mime_type="message/rfc822",
        embed_status="QUEUED",
        message_id="<test2@example.com>",
        subject="Test Subject 2",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        content="Test content 2",
        folder="INBOX",
        modality="mail",
    )

    # Create mock chunks
    mock_chunk1 = MagicMock()
    mock_chunk1.id = "00000000-0000-0000-0000-000000000001"
    mock_chunk1.vector = [0.1] * 1024
    mock_chunk1.item_metadata = {"source_id": 1, "tags": ["test"]}
    mock_chunk1.collection_name = "mail"

    mock_chunk2 = MagicMock()
    mock_chunk2.id = "00000000-0000-0000-0000-000000000002"
    mock_chunk2.vector = [0.2] * 1024
    mock_chunk2.item_metadata = {"source_id": 2, "tags": ["test"]}
    mock_chunk2.collection_name = "mail"

    # Assign chunks directly (bypassing SQLAlchemy relationship)
    item1.chunks = [mock_chunk1]
    item2.chunks = [mock_chunk2]

    push_to_qdrant([item1, item2])

    assert str(item1.embed_status) == "STORED"
    assert str(item2.embed_status) == "STORED"


@pytest.mark.parametrize(
    "item1_status,item1_has_chunks,item2_status,item2_has_chunks,expected_item1_status,expected_item2_status",
    [
        ("RAW", False, "QUEUED", False, "RAW", "QUEUED"),  # Wrong status and no chunks
        ("QUEUED", True, "RAW", False, "STORED", "RAW"),  # Mixed scenarios
    ],
)
def test_push_to_qdrant_no_processing(
    item1_status,
    item1_has_chunks,
    item2_status,
    item2_has_chunks,
    expected_item1_status,
    expected_item2_status,
):
    def create_item(suffix, status, has_chunks):
        item = MailMessage(
            sha256=f"test_hash{suffix}".encode()
            + bytes(24 - len(f"test_hash{suffix}")),
            tags=["test"],
            size=100,
            mime_type="message/rfc822",
            embed_status=status,
            message_id=f"<test{suffix}@example.com>",
            subject=f"Test Subject {suffix}",
            sender="sender@example.com",
            recipients=["recipient@example.com"],
            content=f"Test content {suffix}",
            folder="INBOX",
            modality="mail",
        )
        if has_chunks:
            mock_chunk = MagicMock()
            mock_chunk.id = f"00000000-0000-0000-0000-00000000000{suffix}"
            mock_chunk.vector = [0.1] * 1024
            mock_chunk.item_metadata = {"source_id": int(suffix), "tags": ["test"]}
            mock_chunk.collection_name = "mail"
            item.chunks = [mock_chunk]
        else:
            item.chunks = []
        return item

    item1 = create_item("1", item1_status, item1_has_chunks)
    item2 = create_item("2", item2_status, item2_has_chunks)

    push_to_qdrant([item1, item2])

    assert str(item1.embed_status) == expected_item1_status
    assert str(item2.embed_status) == expected_item2_status


def test_push_to_qdrant_exception(sample_mail_message, mock_chunk):
    sample_mail_message.embed_status = "QUEUED"
    sample_mail_message.chunks = [mock_chunk]

    with patch(
        "memory.workers.tasks.content_processing.qdrant.upsert_vectors",
        side_effect=Exception("Qdrant error"),
    ):
        with pytest.raises(Exception, match="Qdrant error"):
            push_to_qdrant([sample_mail_message])

    assert str(sample_mail_message.embed_status) == "FAILED"


@pytest.mark.parametrize(
    "item_factory,status,additional_fields,expected_id_key",
    [
        (
            lambda: MailMessage(
                id=123,
                sha256=b"test_hash" + bytes(24),
                tags=["test"],
                size=100,
                mime_type="message/rfc822",
                embed_status="STORED",
                message_id="<test@example.com>",
                subject="Test Subject",
                sender="sender@example.com",
                recipients=["recipient@example.com"],
                content="Test content",
                folder="INBOX",
                modality="mail",
            ),
            "processed",
            {},
            "mailmessage_id",
        ),
        (
            lambda: ChatMessage(
                id=456,
                sha256=b"test_hash" + bytes(24),
                tags=["test"],
                size=100,
                mime_type="text/plain",
                embed_status="FAILED",
                platform="discord",
                channel_id="123456",
                author="user123",
                content="Test chat message",
                modality="chat",
            ),
            "failed",
            {"error": "test error"},
            "chatmessage_id",
        ),
        (
            lambda: BlogPost(
                id=789,
                sha256=b"test_hash" + bytes(24),
                tags=["test"],
                size=100,
                mime_type="text/html",
                embed_status="STORED",
                url="https://example.com/post",
                title="Test Blog Post",
                author="Author Name",
                content="Test blog content",
                modality="blog",
            ),
            "processed",
            {"url": "https://example.com"},
            "blogpost_id",
        ),
    ],
)
def test_create_task_result(item_factory, status, additional_fields, expected_id_key):
    item = item_factory()
    item.chunks = [MagicMock(), MagicMock()]  # Mock 2 chunks

    result = create_task_result(item, status, **additional_fields)

    expected_keys = {expected_id_key, "title", "status", "chunks_count", "embed_status"}
    expected_keys.update(additional_fields.keys())

    assert set(result.keys()) == expected_keys
    assert result["status"] == status
    assert result["chunks_count"] == 2
    assert result["embed_status"] == str(item.embed_status)
    assert result[expected_id_key] == item.id


def test_create_task_result_no_title():
    item = SourceItem(
        id=123,
        sha256=b"test_hash" + bytes(24),
        tags=["test"],
        size=100,
        mime_type="text/plain",
        embed_status="STORED",
        content="Test content",
        modality="text",
    )
    item.chunks = []

    result = create_task_result(item, "processed")

    assert result["title"] is None
    assert result["chunks_count"] == 0


def test_by_collection_empty_chunks():
    result = by_collection([])
    assert result == {}


def test_by_collection_single_chunk():
    chunk = Chunk(
        id="00000000-0000-0000-0000-000000000001",
        content="test content",
        embedding_model="test-model",
        vector=[0.1, 0.2, 0.3],
        item_metadata={"source_id": 1, "tags": ["test"]},
        collection_name="test_collection",
    )

    result = by_collection([chunk])

    assert len(result) == 1
    assert "test_collection" in result
    assert result["test_collection"]["ids"] == ["00000000-0000-0000-0000-000000000001"]
    assert result["test_collection"]["vectors"] == [[0.1, 0.2, 0.3]]
    assert result["test_collection"]["payloads"] == [{"source_id": 1, "tags": ["test"]}]


def test_by_collection_multiple_chunks_same_collection():
    chunks = [
        Chunk(
            id="00000000-0000-0000-0000-000000000001",
            content="test content 1",
            embedding_model="test-model",
            vector=[0.1, 0.2],
            item_metadata={"source_id": 1},
            collection_name="collection_a",
        ),
        Chunk(
            id="00000000-0000-0000-0000-000000000002",
            content="test content 2",
            embedding_model="test-model",
            vector=[0.3, 0.4],
            item_metadata={"source_id": 2},
            collection_name="collection_a",
        ),
    ]

    result = by_collection(chunks)

    assert len(result) == 1
    assert "collection_a" in result
    assert result["collection_a"]["ids"] == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ]
    assert result["collection_a"]["vectors"] == [[0.1, 0.2], [0.3, 0.4]]
    assert result["collection_a"]["payloads"] == [{"source_id": 1}, {"source_id": 2}]


def test_by_collection_multiple_chunks_different_collections():
    chunks = [
        Chunk(
            id="00000000-0000-0000-0000-000000000001",
            content="test content 1",
            embedding_model="test-model",
            vector=[0.1, 0.2],
            item_metadata={"source_id": 1},
            collection_name="collection_a",
        ),
        Chunk(
            id="00000000-0000-0000-0000-000000000002",
            content="test content 2",
            embedding_model="test-model",
            vector=[0.3, 0.4],
            item_metadata={"source_id": 2},
            collection_name="collection_b",
        ),
        Chunk(
            id="00000000-0000-0000-0000-000000000003",
            content="test content 3",
            embedding_model="test-model",
            vector=[0.5, 0.6],
            item_metadata={"source_id": 3},
            collection_name="collection_a",
        ),
    ]

    result = by_collection(chunks)

    assert len(result) == 2
    assert "collection_a" in result
    assert "collection_b" in result

    # Check collection_a
    assert result["collection_a"]["ids"] == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000003",
    ]
    assert result["collection_a"]["vectors"] == [[0.1, 0.2], [0.5, 0.6]]
    assert result["collection_a"]["payloads"] == [{"source_id": 1}, {"source_id": 3}]

    # Check collection_b
    assert result["collection_b"]["ids"] == ["00000000-0000-0000-0000-000000000002"]
    assert result["collection_b"]["vectors"] == [[0.3, 0.4]]
    assert result["collection_b"]["payloads"] == [{"source_id": 2}]


@pytest.mark.parametrize(
    "collection_names,expected_collections",
    [
        (["col1", "col1", "col1"], 1),
        (["col1", "col2", "col3"], 3),
        (["col1", "col2", "col1", "col2"], 2),
        (["single"], 1),
    ],
)
def test_by_collection_various_groupings(collection_names, expected_collections):
    chunks = [
        Chunk(
            id=f"00000000-0000-0000-0000-00000000000{i}",
            content=f"test content {i}",
            embedding_model="test-model",
            vector=[float(i)],
            item_metadata={"index": i},
            collection_name=collection_name,
        )
        for i, collection_name in enumerate(collection_names, 1)
    ]

    result = by_collection(chunks)

    assert len(result) == expected_collections
    # Verify all chunks are accounted for
    total_chunks = sum(len(coll["ids"]) for coll in result.values())
    assert total_chunks == len(chunks)


def test_by_collection_with_none_values():
    chunks = [
        Chunk(
            id="00000000-0000-0000-0000-000000000001",
            content="test content",
            embedding_model="test-model",
            vector=None,  # None vector
            item_metadata=None,  # None metadata
            collection_name="test_collection",
        ),
        Chunk(
            id="00000000-0000-0000-0000-000000000002",
            content="test content 2",
            embedding_model="test-model",
            vector=[0.1, 0.2],
            item_metadata={"key": "value"},
            collection_name="test_collection",
        ),
    ]

    result = by_collection(chunks)

    assert len(result) == 1
    assert "test_collection" in result
    assert result["test_collection"]["ids"] == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ]
    assert result["test_collection"]["vectors"] == [None, [0.1, 0.2]]
    assert result["test_collection"]["payloads"] == [None, {"key": "value"}]


def test_by_collection_preserves_order():
    chunks = []
    for i in range(5):
        chunks.append(
            Chunk(
                id=f"00000000-0000-0000-0000-00000000000{i}",
                content=f"test content {i}",
                embedding_model="test-model",
                vector=[float(i)],
                item_metadata={"order": i},
                collection_name="ordered_collection",
            )
        )

    result = by_collection(chunks)

    assert len(result) == 1
    assert result["ordered_collection"]["ids"] == [
        f"00000000-0000-0000-0000-00000000000{i}" for i in range(5)
    ]
    assert result["ordered_collection"]["vectors"] == [[float(i)] for i in range(5)]
    assert result["ordered_collection"]["payloads"] == [{"order": i} for i in range(5)]


@pytest.mark.parametrize(
    "embedding_return,qdrant_error,expected_status,expected_embed_status",
    [
        ("success", False, "processed", "STORED"),
        ("success", True, "failed", "FAILED"),
        ("empty", False, "failed", "FAILED"),
    ],
)
def test_process_content_item(
    db_session,
    qdrant,
    mock_uuid4,
    embedding_return,
    qdrant_error,
    expected_status,
    expected_embed_status,
):
    # Create a fresh mail message for each test to avoid fixture contamination
    mail_message = MailMessage(
        sha256=b"test_hash" + bytes(24),
        tags=["test"],
        size=100,
        mime_type="message/rfc822",
        embed_status="RAW",
        message_id="<test@example.com>",
        subject="Test Subject",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        content="Test content",
        folder="INBOX",
        modality="mail",
    )

    if embedding_return == "success":
        # Create real Chunk objects to avoid SQLAlchemy issues
        real_chunk = Chunk(
            id="00000000-0000-0000-0000-000000000001",
            content="test chunk content",
            embedding_model="test-model",
            vector=[0.1] * 1024,
            item_metadata={"source_id": 1, "tags": ["test"]},
            collection_name="mail",
        )
        mock_chunks = [real_chunk]
    else:  # empty
        mock_chunks = []

    # Mock the embedding function to return our chunks
    with patch("memory.common.embedding.embed_source_item", return_value=mock_chunks):
        if qdrant_error:
            with patch(
                "memory.workers.tasks.content_processing.push_to_qdrant",
                side_effect=Exception("Qdrant error"),
            ):
                result = process_content_item(mail_message, db_session)
        else:
            result = process_content_item(mail_message, db_session)

    assert result["status"] == expected_status
    assert result["embed_status"] == expected_embed_status
    assert result["mailmessage_id"] == mail_message.id

    # Verify database persistence
    db_item = db_session.query(MailMessage).filter_by(id=mail_message.id).first()
    assert db_item is not None
    assert str(db_item.embed_status) == expected_embed_status


def test_safe_task_execution_success():
    """Test that safe_task_execution passes through successful results."""

    @safe_task_execution
    def test_task(arg1, arg2):
        return {"status": "success", "result": arg1 + arg2}

    result = test_task(1, 2)
    assert result["status"] == "success"
    assert result["result"] == 3


def test_safe_task_execution_reraises_exceptions():
    """Test that safe_task_execution logs but re-raises exceptions for Celery retries."""

    @safe_task_execution
    def test_task():
        raise ValueError("Test error message")

    with pytest.raises(ValueError, match="Test error message"):
        test_task()


def test_safe_task_execution_preserves_function_name():
    @safe_task_execution
    def test_function():
        return {"status": "success"}

    # @wraps(func) should preserve the original function name
    assert test_function.__name__ == "test_function"


def test_safe_task_execution_with_kwargs():
    @safe_task_execution
    def task_with_kwargs(arg1, arg2=None, **kwargs):
        return {"status": "success", "arg1": arg1, "arg2": arg2, "kwargs": kwargs}

    result = task_with_kwargs(1, arg2=2, extra="value")
    assert result == {
        "status": "success",
        "arg1": 1,
        "arg2": 2,
        "kwargs": {"extra": "value"},
    }


def test_safe_task_execution_exception_logging(caplog):
    """Test that exceptions are logged before being re-raised."""

    @safe_task_execution
    def failing_task():
        raise RuntimeError("Test runtime error")

    with pytest.raises(RuntimeError, match="Test runtime error"):
        failing_task()

    assert "Task failing_task failed:" in caplog.text
    assert "Test runtime error" in caplog.text
