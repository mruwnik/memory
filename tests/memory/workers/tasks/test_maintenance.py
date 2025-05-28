import uuid
from datetime import datetime, timedelta
from unittest.mock import patch, call

import pytest
from PIL import Image

from memory.common import qdrant as qd
from memory.common import settings
from memory.common.db.models import Chunk, SourceItem, MailMessage, BlogPost
from memory.workers.tasks.maintenance import (
    clean_collection,
    reingest_chunk,
    check_batch,
    reingest_missing_chunks,
    reingest_item,
    reingest_empty_source_items,
)


@pytest.fixture
def source(db_session):
    s = SourceItem(id=1, modality="text", sha256=b"123")
    db_session.add(s)
    db_session.commit()
    return s


@pytest.fixture
def mock_uuid4():
    i = 0

    def uuid4():
        nonlocal i
        i += 1
        return f"00000000-0000-0000-0000-00000000000{i}"

    with patch("uuid.uuid4", side_effect=uuid4):
        yield


@pytest.fixture
def test_image(mock_file_storage):
    img = Image.new("RGB", (100, 100), color=(73, 109, 137))
    img_path = settings.CHUNK_STORAGE_DIR / "test.png"
    img.save(img_path)
    return img_path


@pytest.fixture(params=["text", "photo"])
def chunk(request, test_image, db_session):
    """Parametrized fixture for chunk configuration"""
    collection = request.param
    if collection == "photo":
        content = None
        file_paths = [str(test_image)]
    else:
        content = "Test content for reingestion"
        file_paths = None

    chunk = Chunk(
        id=str(uuid.uuid4()),
        source=SourceItem(id=1, modality=collection, sha256=b"123"),
        content=content,
        file_paths=file_paths,
        embedding_model="test-model",
        checked_at=datetime(2025, 1, 1),
    )
    db_session.add(chunk)
    db_session.commit()
    return chunk


def test_clean_collection_no_mismatches(db_session, qdrant, source):
    """Test when all Qdrant points exist in the database - nothing should be deleted."""
    # Create chunks in the database
    chunk_ids = [str(uuid.uuid4()) for _ in range(3000)]
    collection = "text"

    # Add chunks to the database
    for chunk_id in chunk_ids:
        db_session.add(
            Chunk(
                id=chunk_id,
                source=source,
                content="Test content",
                embedding_model="test-model",
            )
        )
    db_session.commit()
    qd.ensure_collection_exists(qdrant, collection, 1024)
    qd.upsert_vectors(qdrant, collection, chunk_ids, [[1] * 1024] * len(chunk_ids))

    assert set(chunk_ids) == {
        str(i) for batch in qd.batch_ids(qdrant, collection) for i in batch
    }

    clean_collection(collection)

    # Check that the chunks are still in the database - no points were deleted
    assert set(chunk_ids) == {
        str(i) for batch in qd.batch_ids(qdrant, collection) for i in batch
    }


def test_clean_collection_with_orphaned_vectors(db_session, qdrant, source):
    """Test when there are vectors in Qdrant that don't exist in the database."""
    existing_ids = [str(uuid.uuid4()) for _ in range(3000)]
    orphaned_ids = [str(uuid.uuid4()) for _ in range(3000)]
    all_ids = existing_ids + orphaned_ids
    collection = "text"

    # Add only the existing chunks to the database
    for chunk_id in existing_ids:
        db_session.add(
            Chunk(
                id=chunk_id,
                source=source,
                content="Test content",
                embedding_model="test-model",
            )
        )
    db_session.commit()
    qd.ensure_collection_exists(qdrant, collection, 1024)
    qd.upsert_vectors(qdrant, collection, all_ids, [[1] * 1024] * len(all_ids))

    clean_collection(collection)

    # The orphaned vectors should be deleted
    assert set(existing_ids) == {
        str(i) for batch in qd.batch_ids(qdrant, collection) for i in batch
    }


def test_clean_collection_empty_batches(db_session, qdrant):
    collection = "text"
    qd.ensure_collection_exists(qdrant, collection, 1024)

    clean_collection(collection)

    assert not [i for b in qd.batch_ids(qdrant, collection) for i in b]


def test_reingest_chunk(db_session, qdrant, chunk):
    """Test reingesting a chunk using parameterized fixtures"""
    collection = chunk.source.modality
    qd.ensure_collection_exists(qdrant, collection, 1024)

    start = datetime.now()
    test_vector = [0.1] * 1024
    reingest_chunk(str(chunk.id), collection)

    vectors = qd.search_vectors(qdrant, collection, test_vector, limit=1)
    assert len(vectors) == 1
    assert str(vectors[0].id) == str(chunk.id)
    assert vectors[0].payload == chunk.source.as_payload()
    db_session.refresh(chunk)
    assert chunk.checked_at.isoformat() > start.isoformat()


def test_reingest_chunk_not_found(db_session, qdrant):
    """Test when the chunk to reingest doesn't exist."""
    non_existent_id = str(uuid.uuid4())
    collection = "text"

    reingest_chunk(non_existent_id, collection)


def test_reingest_chunk_unsupported_collection(db_session, qdrant, source):
    """Test reingesting with an unsupported collection type."""
    chunk_id = str(uuid.uuid4())
    chunk = Chunk(
        id=chunk_id,
        source=source,
        content="Test content",
        embedding_model="test-model",
    )
    db_session.add(chunk)
    db_session.commit()

    unsupported_collection = "unsupported"
    qd.ensure_collection_exists(qdrant, unsupported_collection, 1024)

    with pytest.raises(
        ValueError, match=f"Unsupported collection {unsupported_collection}"
    ):
        reingest_chunk(chunk_id, unsupported_collection)


def test_check_batch_empty(db_session, qdrant):
    assert check_batch([]) == {}


def test_check_batch(db_session, qdrant):
    modalities = ["text", "photo", "mail"]
    chunks = [
        Chunk(
            id=f"00000000-0000-0000-0000-0000000000{i:02d}",
            source=SourceItem(modality=modality, sha256=f"123{i}".encode()),
            content="Test content",
            file_paths=None,
            embedding_model="test-model",
            checked_at=datetime(2025, 1, 1),
        )
        for modality in modalities
        for i in range(5)
    ]
    db_session.add_all(chunks)
    db_session.commit()
    start_time = datetime.now()

    for modality in modalities:
        qd.ensure_collection_exists(qdrant, modality, 1024)

    for chunk in chunks[::2]:
        qd.upsert_vectors(qdrant, chunk.source.modality, [str(chunk.id)], [[1] * 1024])

    with patch.object(reingest_chunk, "delay") as mock_reingest:
        stats = check_batch(chunks)

    assert mock_reingest.call_args_list == [
        call("00000000-0000-0000-0000-000000000001", "text"),
        call("00000000-0000-0000-0000-000000000003", "text"),
        call("00000000-0000-0000-0000-000000000000", "photo"),
        call("00000000-0000-0000-0000-000000000002", "photo"),
        call("00000000-0000-0000-0000-000000000004", "photo"),
        call("00000000-0000-0000-0000-000000000001", "mail"),
        call("00000000-0000-0000-0000-000000000003", "mail"),
    ]
    assert stats == {
        "mail": {"correct": 3, "missing": 2, "total": 5},
        "text": {"correct": 3, "missing": 2, "total": 5},
        "photo": {"correct": 2, "missing": 3, "total": 5},
    }
    db_session.commit()
    for chunk in chunks[::2]:
        assert chunk.checked_at.isoformat() > start_time.isoformat()
    for chunk in chunks[1::2]:
        assert chunk.checked_at.isoformat()[:19] == "2025-01-01T00:00:00"


@pytest.mark.parametrize("batch_size", [4, 10, 100])
def test_reingest_missing_chunks(db_session, qdrant, batch_size):
    now = datetime.now()
    old_time = now - timedelta(minutes=120)  # Older than the threshold

    modalities = ["text", "photo", "mail"]
    ids_generator = (f"00000000-0000-0000-0000-00000000{i:04d}" for i in range(1000))

    old_chunks = [
        Chunk(
            id=next(ids_generator),
            source=SourceItem(modality=modality, sha256=f"{modality}-{i}".encode()),
            content="Old content",
            file_paths=None,
            embedding_model="test-model",
            checked_at=old_time,
        )
        for modality in modalities
        for i in range(20)
    ]

    recent_chunks = [
        Chunk(
            id=next(ids_generator),
            source=SourceItem(
                modality=modality, sha256=f"recent-{modality}-{i}".encode()
            ),
            content="Recent content",
            file_paths=None,
            embedding_model="test-model",
            checked_at=now,
        )
        for modality in modalities
        for i in range(5)
    ]

    db_session.add_all(old_chunks + recent_chunks)
    db_session.commit()

    for modality in modalities:
        qd.ensure_collection_exists(qdrant, modality, 1024)

    for chunk in old_chunks[::2]:
        qd.upsert_vectors(qdrant, chunk.source.modality, [str(chunk.id)], [[1] * 1024])

    with patch.object(reingest_chunk, "delay", reingest_chunk):
        result = reingest_missing_chunks(batch_size=batch_size, minutes_ago=60)

    assert result == {
        "photo": {"correct": 10, "missing": 10, "total": 20},
        "mail": {"correct": 10, "missing": 10, "total": 20},
        "text": {"correct": 10, "missing": 10, "total": 20},
    }

    db_session.commit()
    # All the old chunks should be reingested
    client = qd.get_qdrant_client()
    for modality in modalities:
        qdrant_ids = [
            i for b in qd.batch_ids(client, modality, batch_size=1000) for i in b
        ]
        db_ids = [str(c.id) for c in old_chunks if c.source.modality == modality]
        assert set(qdrant_ids) == set(db_ids)


@pytest.mark.parametrize("item_type", ["MailMessage", "BlogPost"])
def test_reingest_item_success(db_session, qdrant, item_type):
    """Test successful reingestion of an item with existing chunks."""
    if item_type == "MailMessage":
        item = MailMessage(
            sha256=b"test_hash" + bytes(24),
            tags=["test"],
            size=100,
            mime_type="message/rfc822",
            embed_status="STORED",
            message_id="<test@example.com>",
            subject="Test Subject",
            sender="sender@example.com",
            recipients=["recipient@example.com"],
            content="Test content for reingestion",
            folder="INBOX",
            modality="mail",
        )
    else:  # blog_post
        item = BlogPost(
            sha256=b"test_hash" + bytes(24),
            tags=["test"],
            size=100,
            mime_type="text/html",
            embed_status="STORED",
            url="https://example.com/post",
            title="Test Blog Post",
            author="Author Name",
            content="Test blog content for reingestion",
            modality="blog",
        )

    db_session.add(item)
    db_session.flush()

    # Add some chunks to the item
    chunk_ids = [str(uuid.uuid4()) for _ in range(3)]
    chunks = [
        Chunk(
            id=chunk_id,
            source=item,
            content=f"Test chunk content {i}",
            embedding_model="test-model",
        )
        for i, chunk_id in enumerate(chunk_ids)
    ]
    db_session.add_all(chunks)
    db_session.commit()

    # Add vectors to Qdrant
    modality = "mail" if item_type == "MailMessage" else "blog"
    qd.ensure_collection_exists(qdrant, modality, 1024)
    qd.upsert_vectors(qdrant, modality, chunk_ids, [[1] * 1024] * len(chunk_ids))

    # Verify chunks exist in Qdrant before reingestion
    qdrant_ids_before = {
        str(i) for batch in qd.batch_ids(qdrant, modality) for i in batch
    }
    assert set(chunk_ids).issubset(qdrant_ids_before)

    # Mock the embedding function to return chunks
    with patch("memory.common.embedding.embed_source_item") as mock_embed:
        mock_embed.return_value = [
            Chunk(
                id=str(uuid.uuid4()),
                content="New chunk content 1",
                embedding_model="test-model",
                vector=[0.1] * 1024,
                item_metadata={"source_id": item.id, "tags": ["test"]},
            ),
            Chunk(
                id=str(uuid.uuid4()),
                content="New chunk content 2",
                embedding_model="test-model",
                vector=[0.2] * 1024,
                item_metadata={"source_id": item.id, "tags": ["test"]},
            ),
        ]

        result = reingest_item(str(item.id), item_type)

    assert result["status"] == "processed"
    assert result[f"{item_type.lower()}_id"] == item.id
    assert result["chunks_count"] == 2
    assert result["embed_status"] == "STORED"

    # Verify old chunks were deleted from database
    db_session.refresh(item)
    remaining_chunks = db_session.query(Chunk).filter(Chunk.id.in_(chunk_ids)).all()
    assert len(remaining_chunks) == 0

    # Verify old vectors were deleted from Qdrant
    qdrant_ids_after = {
        str(i) for batch in qd.batch_ids(qdrant, modality) for i in batch
    }
    assert not set(chunk_ids).intersection(qdrant_ids_after)


def test_reingest_item_not_found(db_session):
    """Test reingesting a non-existent item."""
    non_existent_id = "999"
    result = reingest_item(non_existent_id, "MailMessage")

    assert result == {"status": "error", "error": f"Item {non_existent_id} not found"}


def test_reingest_item_invalid_type(db_session):
    """Test reingesting with an invalid item type."""
    result = reingest_item("1", "invalid_type")

    assert result["status"] == "error"
    assert "Unsupported item type invalid_type" in result["error"]
    assert "Available types:" in result["error"]


def test_reingest_item_no_chunks(db_session, qdrant):
    """Test reingesting an item that has no chunks."""
    item = MailMessage(
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
    db_session.add(item)
    db_session.commit()

    # Mock the embedding function to return a chunk
    with patch("memory.common.embedding.embed_source_item") as mock_embed:
        mock_embed.return_value = [
            Chunk(
                id=str(uuid.uuid4()),
                content="New chunk content",
                embedding_model="test-model",
                vector=[0.1] * 1024,
                item_metadata={"source_id": item.id, "tags": ["test"]},
            ),
        ]

        result = reingest_item(str(item.id), "MailMessage")

    assert result["status"] == "processed"
    assert result["mailmessage_id"] == item.id
    assert result["chunks_count"] == 1
    assert result["embed_status"] == "STORED"


@pytest.mark.parametrize("item_type", ["MailMessage", "BlogPost"])
def test_reingest_empty_source_items_success(db_session, item_type):
    """Test reingesting empty source items."""
    # Create items with and without chunks
    if item_type == "MailMessage":
        empty_items = [
            MailMessage(
                sha256=f"empty_hash_{i}".encode() + bytes(32 - len(f"empty_hash_{i}")),
                tags=["test"],
                size=100,
                mime_type="message/rfc822",
                embed_status="RAW",
                message_id=f"<empty{i}@example.com>",
                subject=f"Empty Subject {i}",
                sender="sender@example.com",
                recipients=["recipient@example.com"],
                content=f"Empty content {i}",
                folder="INBOX",
                modality="mail",
            )
            for i in range(3)
        ]

        item_with_chunks = MailMessage(
            sha256=b"with_chunks_hash" + bytes(16),
            tags=["test"],
            size=100,
            mime_type="message/rfc822",
            embed_status="STORED",
            message_id="<with_chunks@example.com>",
            subject="With Chunks Subject",
            sender="sender@example.com",
            recipients=["recipient@example.com"],
            content="Content with chunks",
            folder="INBOX",
            modality="mail",
        )
    else:  # blog_post
        empty_items = [
            BlogPost(
                sha256=f"empty_hash_{i}".encode() + bytes(32 - len(f"empty_hash_{i}")),
                tags=["test"],
                size=100,
                mime_type="text/html",
                embed_status="RAW",
                url=f"https://example.com/empty{i}",
                title=f"Empty Post {i}",
                author="Author Name",
                content=f"Empty blog content {i}",
                modality="blog",
            )
            for i in range(3)
        ]

        item_with_chunks = BlogPost(
            sha256=b"with_chunks_hash" + bytes(16),
            tags=["test"],
            size=100,
            mime_type="text/html",
            embed_status="STORED",
            url="https://example.com/with_chunks",
            title="With Chunks Post",
            author="Author Name",
            content="Blog content with chunks",
            modality="blog",
        )

    db_session.add_all(empty_items + [item_with_chunks])
    db_session.flush()

    # Add a chunk to the item_with_chunks
    chunk = Chunk(
        id=str(uuid.uuid4()),
        source=item_with_chunks,
        content="Test chunk content",
        embedding_model="test-model",
    )
    db_session.add(chunk)
    db_session.commit()

    with patch.object(reingest_item, "delay") as mock_reingest:
        result = reingest_empty_source_items(item_type)

    assert result == {"status": "success", "items": 3}

    # Verify that reingest_item.delay was called for each empty item
    assert mock_reingest.call_count == 3
    expected_calls = [call(item.id, item_type) for item in empty_items]
    mock_reingest.assert_has_calls(expected_calls, any_order=True)


def test_reingest_empty_source_items_no_empty_items(db_session):
    """Test when there are no empty source items."""
    # Create an item with chunks
    item = MailMessage(
        sha256=b"with_chunks_hash" + bytes(16),
        tags=["test"],
        size=100,
        mime_type="message/rfc822",
        embed_status="STORED",
        message_id="<with_chunks@example.com>",
        subject="With Chunks Subject",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        content="Content with chunks",
        folder="INBOX",
        modality="mail",
    )
    db_session.add(item)
    db_session.flush()

    chunk = Chunk(
        id=str(uuid.uuid4()),
        source=item,
        content="Test chunk content",
        embedding_model="test-model",
    )
    db_session.add(chunk)
    db_session.commit()

    with patch.object(reingest_item, "delay") as mock_reingest:
        result = reingest_empty_source_items("MailMessage")

    assert result == {"status": "success", "items": 0}
    mock_reingest.assert_not_called()


def test_reingest_empty_source_items_invalid_type(db_session):
    """Test reingesting empty source items with invalid type."""
    result = reingest_empty_source_items("invalid_type")

    assert result["status"] == "error"
    assert "Unsupported item type invalid_type" in result["error"]
    assert "Available types:" in result["error"]


def test_reingest_missing_chunks_no_chunks(db_session):
    assert reingest_missing_chunks() == {}
