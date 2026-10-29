# FIXME: Most of this was vibe-coded
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, call
from typing import cast

import pytest
from celery.backends.database.models import Task as CeleryTaskMeta
from PIL import Image
from sqlalchemy import insert, text, update
from sqlalchemy.orm import Session

from memory.common import qdrant as qd
from memory.common import settings
from memory.common.db.models import (
    Chunk,
    SourceItem,
    MailMessage,
    EmailAttachment,
    ForumComment,
    ForumPost,
    BlogPost,
    EmailAccount,
    Meeting,
    Project,
    Task,
    TranscriptAccount,
)
from memory.workers.tasks.maintenance import (
    clean_collection,
    clean_mail_headers,
    reingest_chunk,
    check_batch,
    reingest_missing_chunks,
    reingest_item,
    reingest_empty_source_items,
    update_metadata_for_item,
    update_metadata_for_source_items,
    update_source_access_control,
    reconcile_access_control,
    get_item_class,
    _payloads_equal,
)
import memory.workers.tasks.maintenance as maintenance_module

from memory.common.db.models import (
    AuditEvent,
    JobStatus,
    LLMSpend,
    PendingJob,
    ScheduledTask,
    TaskExecution,
    TelemetryEvent,
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

    [vectors] = qd.search_vectors_batch(qdrant, collection, [test_vector], limit=1)
    assert [str(v.id) for v in vectors] == [str(chunk.id)]
    [point] = qdrant.retrieve(collection, ids=[str(chunk.id)])
    assert point.payload == chunk.source.as_payload()
    db_session.refresh(chunk)
    assert chunk.checked_at.isoformat() > start.isoformat()


@pytest.mark.parametrize("chunk", ["text"], indirect=True)
def test_reingest_chunk_without_a_voyage_key_logs_no_error(db_session, qdrant, chunk, monkeypatch, caplog):
    monkeypatch.setattr(settings, "VOYAGE_API_KEY", "")

    reingest_chunk(str(chunk.id), "text")

    assert [r for r in caplog.records if r.name == "memory.workers.tasks.maintenance" and r.levelname == "ERROR"] == []
    db_session.refresh(chunk)
    assert chunk.checked_at is not None


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


@pytest.mark.transactional_db
def test_check_batch(db_session, qdrant):
    modalities = ["text", "photo", "mail"]
    chunks = [
        Chunk(
            id=f"00000000-0000-0000-0000-0000000000{i:02d}",
            source=SourceItem(modality=modality, sha256=f"123{i}".encode()),
            content="Test content",
            file_paths=None,
            embedding_model="test-model",
            collection_name=modality,
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
    # check_batch updates checked_at for *every* chunk it inspects (not only
    # the ones that were found in qdrant) so a chunk waiting in the reingest
    # queue isn't re-dispatched on the next hourly run.
    for chunk in chunks:
        assert chunk.checked_at is not None
        assert chunk.checked_at.isoformat() > start_time.isoformat()


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
            collection_name=modality,
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
            collection_name=modality,
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
@pytest.mark.transactional_db
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
            collection_name=item.modality,
            embedding_model="test-model",
        )
        for i, chunk_id in enumerate(chunk_ids)
    ]
    db_session.add_all(chunks)
    db_session.commit()

    # Add vectors to Qdrant
    modality = cast(str, item.modality)
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
                collection_name=modality,
                vector=[0.1] * 1024,
                item_metadata={"source_id": item.id, "tags": ["test"]},
            ),
            Chunk(
                id=str(uuid.uuid4()),
                content="New chunk content 2",
                embedding_model="test-model",
                collection_name=modality,
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
                collection_name=item.modality,
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
        collection_name=item_with_chunks.modality,
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


@pytest.mark.parametrize(
    "embed_status, has_chunks, requeued",
    [
        ("RAW", False, True),
        ("FAILED", False, True),
        # A failed Qdrant push leaves chunks behind but the item unfetchable.
        ("FAILED", True, True),
        ("STORED", False, True),
        ("STORED", True, False),
        ("SKIPPED", False, False),
    ],
)
def test_reingest_empty_source_items_selection(
    db_session, embed_status, has_chunks, requeued
):
    item = MailMessage(
        sha256=b"selection_hash" + bytes(18),
        tags=["test"],
        size=100,
        mime_type="message/rfc822",
        embed_status=embed_status,
        message_id="<selection@example.com>",
        subject="Subject",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        content="Content",
        folder="INBOX",
        modality="mail",
    )
    db_session.add(item)
    db_session.flush()
    chunks = [
        Chunk(id=str(uuid.uuid4()), source=item, content="c", embedding_model="m")
    ]
    db_session.add_all(chunks[: int(has_chunks)])
    db_session.commit()

    with patch.object(reingest_item, "delay") as mock_reingest:
        result = reingest_empty_source_items("MailMessage")

    assert result == {"status": "success", "items": int(requeued)}
    assert mock_reingest.call_args_list == [call(item.id, "MailMessage")][: int(requeued)]


@pytest.mark.parametrize("item_type", [None, "mail_message"])
@pytest.mark.parametrize(
    "embed_status, inserted_hours_ago, updated_hours_ago, raw_queued, failed_queued",
    [
        ("RAW", 3, 2, True, False),
        # Probably still being embedded by the task that created it.
        ("RAW", 1, 0.5, False, False),
        ("FAILED", 24, 0, False, True),
        # Retries bump updated_at, so only inserted_at bounds them.
        ("FAILED", 72, 0, False, False),
        ("STORED", 3, 2, False, False),
        ("SKIPPED", 3, 2, False, False),
    ],
)
def test_process_raw_items_selection(
    db_session,
    item_type,
    embed_status,
    inserted_hours_ago,
    updated_hours_ago,
    raw_queued,
    failed_queued,
):
    now = datetime.now(timezone.utc)
    item = MailMessage(
        sha256=b"raw_selection" + bytes(19),
        size=100,
        mime_type="message/rfc822",
        embed_status=embed_status,
        message_id="<raw-selection@example.com>",
        subject="Subject",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        content="Content",
        folder="INBOX",
        modality="mail",
        inserted_at=now - timedelta(hours=inserted_hours_ago),
        updated_at=now - timedelta(hours=updated_hours_ago),
    )
    db_session.add(item)
    db_session.commit()

    with (
        patch.object(maintenance_module.process_raw_item, "delay") as mock_process,
        patch.object(reingest_item, "delay") as mock_reingest,
    ):
        result = maintenance_module.process_raw_items(item_type=item_type)

    assert result == {"status": "success", "queued": int(raw_queued or failed_queued)}
    assert mock_process.call_args_list == [call(item.id, "mail_message")][: int(raw_queued)]
    assert mock_reingest.call_args_list == [call(item.id, "mail_message")][
        : int(failed_queued)
    ]


def test_process_raw_items_retries_newest_failures_first(db_session):
    now = datetime.now(timezone.utc)
    items = [
        MailMessage(
            sha256=bytes([i]) * 32,
            size=100,
            mime_type="message/rfc822",
            embed_status="FAILED",
            message_id=f"<order-{i}@example.com>",
            subject="Subject",
            sender="sender@example.com",
            recipients=["recipient@example.com"],
            content="Content",
            folder="INBOX",
            modality="mail",
            inserted_at=now - timedelta(hours=hours_ago),
        )
        for i, hours_ago in enumerate([40, 2, 20], start=1)
    ]
    db_session.add_all(items)
    db_session.commit()

    with patch.object(reingest_item, "delay") as mock_reingest:
        maintenance_module.process_raw_items(batch_size=2)

    assert mock_reingest.call_args_list == [
        call(items[1].id, "mail_message"),
        call(items[2].id, "mail_message"),
    ]


def test_reingest_empty_source_items_invalid_type(db_session):
    """Test reingesting empty source items with invalid type."""
    result = reingest_empty_source_items("invalid_type")

    assert result["status"] == "error"
    assert "Unsupported item type invalid_type" in result["error"]
    assert "Available types:" in result["error"]


def test_reingest_missing_chunks_no_chunks(db_session):
    assert reingest_missing_chunks() == {}


@pytest.mark.parametrize(
    "payload1,payload2,expected",
    [
        # Identical payloads
        (
            {"tags": ["a", "b"], "source_id": 1, "title": "Test"},
            {"tags": ["a", "b"], "source_id": 1, "title": "Test"},
            True,
        ),
        # Different tag order (should be equal)
        (
            {"tags": ["a", "b"], "source_id": 1},
            {"tags": ["b", "a"], "source_id": 1},
            True,
        ),
        # Different tags
        (
            {"tags": ["a", "b"], "source_id": 1},
            {"tags": ["a", "c"], "source_id": 1},
            False,
        ),
        # Different non-tag fields
        ({"tags": ["a"], "source_id": 1}, {"tags": ["a"], "source_id": 2}, False),
        # Missing tags in one payload
        ({"tags": ["a"], "source_id": 1}, {"source_id": 1}, False),
        # Empty tags (should be equal)
        ({"tags": [], "source_id": 1}, {"source_id": 1}, True),
    ],
)
def test_payloads_equal(payload1, payload2, expected):
    """Test the _payloads_equal helper function."""
    assert _payloads_equal(payload1, payload2) == expected


@pytest.mark.parametrize("item_type", ["MailMessage", "BlogPost"])
def test_update_metadata_for_item_success(db_session, qdrant, item_type):
    """Test successful metadata update for an item with chunks."""
    if item_type == "MailMessage":
        item = MailMessage(
            sha256=b"test_hash" + bytes(24),
            tags=["original", "test"],
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
        )
        modality = "mail"
    else:
        item = BlogPost(
            sha256=b"test_hash" + bytes(24),
            tags=["original", "test"],
            size=100,
            mime_type="text/html",
            embed_status="STORED",
            url="https://example.com/post",
            title="Test Blog Post",
            author="Author Name",
            content="Test blog content",
            modality="blog",
        )
        modality = "blog"

    db_session.add(item)
    db_session.flush()

    # Add chunks to the item
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

    # Setup Qdrant with existing payloads
    qd.ensure_collection_exists(qdrant, modality, 1024)

    existing_payloads = [
        {"tags": ["existing", "qdrant"], "source_id": item.id, "old_field": "value"}
        for _ in chunk_ids
    ]
    qd.upsert_vectors(
        qdrant, modality, chunk_ids, [[1] * 1024] * len(chunk_ids), existing_payloads
    )

    # Mock the qdrant functions to track calls
    with (
        patch(
            "memory.workers.tasks.maintenance.qdrant.get_payloads"
        ) as mock_get_payloads,
        patch(
            "memory.workers.tasks.maintenance.qdrant.set_payload"
        ) as mock_set_payload,
    ):
        # Return the existing payloads
        mock_get_payloads.return_value = {
            chunk_id: payload for chunk_id, payload in zip(chunk_ids, existing_payloads)
        }

        result = update_metadata_for_item(str(item.id), item_type)

    # Verify result
    assert result["status"] == "success"
    assert result["updated_chunks"] == 3
    assert result["errors"] == 0

    # Verify batch retrieval was called once
    mock_get_payloads.assert_called_once_with(qdrant, modality, chunk_ids)

    # Verify set_payload was called for each chunk with merged tags
    assert mock_set_payload.call_count == 3
    for mock_call in mock_set_payload.call_args_list:
        args, kwargs = mock_call
        client, collection, chunk_id, updated_payload = args

        # Check that tags were merged (existing + new)
        assert set(updated_payload["tags"]) == {"existing", "qdrant", "original", "test"}

        # Check that new metadata is present
        assert updated_payload["source_id"] == item.id


def test_update_metadata_for_item_no_changes(db_session, qdrant):
    """Test that no updates are made when metadata hasn't changed."""
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
        content="Test content",
        folder="INBOX",
        modality="mail",
    )
    db_session.add(item)
    db_session.flush()

    chunk_id = str(uuid.uuid4())
    chunk = Chunk(
        id=chunk_id,
        source=item,
        content="Test chunk content",
        embedding_model="test-model",
    )
    db_session.add(chunk)
    db_session.commit()

    # Setup payload that matches what the item would generate
    item_payload = item.as_payload()
    existing_payload = {chunk_id: item_payload}

    with (
        patch(
            "memory.workers.tasks.maintenance.qdrant.get_payloads"
        ) as mock_get_payloads,
        patch(
            "memory.workers.tasks.maintenance.qdrant.set_payload"
        ) as mock_set_payload,
    ):
        mock_get_payloads.return_value = existing_payload

        result = update_metadata_for_item(str(item.id), "MailMessage")

    # Verify no updates were made
    assert result["status"] == "success"
    assert result["updated_chunks"] == 0
    assert result["errors"] == 0

    # Verify set_payload was never called since nothing changed
    mock_set_payload.assert_not_called()


def test_update_metadata_for_item_no_chunks(db_session):
    """Test updating metadata for an item with no chunks."""
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

    result = update_metadata_for_item(str(item.id), "MailMessage")

    assert result["status"] == "success"
    assert result["updated_chunks"] == 0
    assert result["errors"] == 0


def test_update_metadata_for_item_not_found(db_session):
    """Test updating metadata for a non-existent item."""
    non_existent_id = "999"
    result = update_metadata_for_item(non_existent_id, "MailMessage")

    assert result == {"status": "error", "error": f"Item {non_existent_id} not found"}


def test_update_metadata_for_item_invalid_type(db_session):
    """Test updating metadata with an invalid item type."""
    result = update_metadata_for_item("1", "invalid_type")

    assert result["status"] == "error"
    assert "Unsupported item type invalid_type" in result["error"]
    assert "Available types:" in result["error"]


def test_update_metadata_for_item_missing_chunks_in_qdrant(db_session, qdrant):
    """Test when some chunks exist in DB but not in Qdrant."""
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
        content="Test content",
        folder="INBOX",
        modality="mail",
    )
    db_session.add(item)
    db_session.flush()

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

    # Mock qdrant to return payloads for only some chunks
    with (
        patch(
            "memory.workers.tasks.maintenance.qdrant.get_payloads"
        ) as mock_get_payloads,
        patch(
            "memory.workers.tasks.maintenance.qdrant.set_payload"
        ) as mock_set_payload,
    ):
        # Only return payload for first chunk
        mock_get_payloads.return_value = {
            chunk_ids[0]: {"tags": ["existing"], "source_id": item.id}
        }

        result = update_metadata_for_item(str(item.id), "MailMessage")

    # Should only update the chunk that was found in Qdrant
    assert result["status"] == "success"
    assert result["updated_chunks"] == 1
    assert result["errors"] == 0

    # Only one set_payload call for the found chunk
    assert mock_set_payload.call_count == 1


def test_update_metadata_for_item_per_chunk_failure_does_not_abort_loop(
    db_session, qdrant
):
    """A transient failure on one chunk MUST NOT strand the rest with stale payload.

    Pre-fix the try/except wrapped the whole loop, so a single failed
    set_payload call left every chunk after it with stale ``project_id`` /
    ``sensitivity`` — an access-control consistency bug when sources get
    reprojected. The error count was also pegged to 1 regardless of how
    many chunks actually went unprocessed.
    """
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
        content="Test content",
        folder="INBOX",
        modality="mail",
    )
    db_session.add(item)
    db_session.flush()

    chunk_ids = [str(uuid.uuid4()) for _ in range(5)]
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

    payloads = {
        cid: {"tags": ["existing"], "source_id": item.id, "old_field": "x"}
        for cid in chunk_ids
    }

    # Make set_payload fail on the middle chunk only.
    failing_chunk = chunk_ids[2]

    def fake_set_payload(client, collection, chunk_id, payload):
        if chunk_id == failing_chunk:
            raise RuntimeError("simulated qdrant blip")

    with (
        patch(
            "memory.workers.tasks.maintenance.qdrant.get_payloads",
            return_value=payloads,
        ),
        patch(
            "memory.workers.tasks.maintenance.qdrant.set_payload",
            side_effect=fake_set_payload,
        ) as mock_set_payload,
    ):
        result = update_metadata_for_item(str(item.id), "MailMessage")

    # All 5 chunks were attempted (the loop did not abort on the failure).
    assert mock_set_payload.call_count == 5
    # 4 succeeded, 1 errored — the dashboard sees the true failure count.
    assert result["status"] == "success"
    assert result["updated_chunks"] == 4
    assert result["errors"] == 1


def test_update_metadata_for_item_setup_failure_returns_error(db_session):
    """Failing get_payloads for the whole item is correctly reported as an error."""
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
        content="Test content",
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

    with patch(
        "memory.workers.tasks.maintenance.qdrant.get_payloads",
        side_effect=RuntimeError("qdrant unreachable"),
    ):
        result = update_metadata_for_item(str(item.id), "MailMessage")

    assert result["status"] == "error"
    assert result["errors"] == 1
    assert "qdrant unreachable" in result["error"]


@pytest.mark.parametrize("item_type", ["MailMessage", "BlogPost"])
def test_update_metadata_for_source_items_success(db_session, item_type):
    """Test updating metadata for all items of a given type."""
    if item_type == "MailMessage":
        items = [
            MailMessage(
                sha256=f"test_hash_{i}".encode() + bytes(32 - len(f"test_hash_{i}")),
                tags=["test"],
                size=100,
                mime_type="message/rfc822",
                embed_status="STORED",
                message_id=f"<test{i}@example.com>",
                subject=f"Test Subject {i}",
                sender="sender@example.com",
                recipients=["recipient@example.com"],
                content=f"Test content {i}",
                folder="INBOX",
                modality="mail",
            )
            for i in range(3)
        ]
    else:
        items = [
            BlogPost(
                sha256=f"test_hash_{i}".encode() + bytes(32 - len(f"test_hash_{i}")),
                tags=["test"],
                size=100,
                mime_type="text/html",
                embed_status="STORED",
                url=f"https://example.com/post{i}",
                title=f"Test Blog Post {i}",
                author="Author Name",
                content=f"Test blog content {i}",
                modality="blog",
            )
            for i in range(3)
        ]

    db_session.add_all(items)
    db_session.commit()

    with patch.object(update_metadata_for_item, "delay") as mock_update:
        result = update_metadata_for_source_items(item_type)

    assert result == {"status": "success", "items": 3}

    # Verify that update_metadata_for_item.delay was called for each item
    assert mock_update.call_count == 3
    expected_calls = [call(item.id, item_type) for item in items]
    mock_update.assert_has_calls(expected_calls, any_order=True)


def test_update_metadata_for_source_items_no_items(db_session):
    """Test when there are no items of the specified type."""
    with patch.object(update_metadata_for_item, "delay") as mock_update:
        result = update_metadata_for_source_items("MailMessage")

    assert result == {"status": "success", "items": 0}
    mock_update.assert_not_called()


def test_update_metadata_for_source_items_invalid_type(db_session):
    """Test updating metadata for an invalid item type."""
    result = update_metadata_for_source_items("invalid_type")

    assert result["status"] == "error"
    assert "Unsupported item type invalid_type" in result["error"]
    assert "Available types:" in result["error"]


# ====== get_item_class lookup tests ======


@pytest.fixture(autouse=True)
def reset_item_class_cache():
    """Force the item-class lookup map to rebuild between tests."""
    maintenance_module._ITEM_CLASSES = None
    yield
    maintenance_module._ITEM_CLASSES = None


@pytest.mark.parametrize(
    "key",
    [
        # Polymorphic identity (snake_case) — what process_raw_items dispatches with.
        "mail_message",
        "blog_post",
        # Class name (CapsCase), which `run_celery_task.py reingest-item` accepts.
        "MailMessage",
        "BlogPost",
    ],
)
def test_get_item_class_accepts_both_naming_conventions(key):
    """get_item_class must resolve both polymorphic identity and class name.

    process_raw_items passes SourceItem.type (the polymorphic discriminator);
    the reingest-item CLI accepts a class name too.
    """
    cls = get_item_class(key)
    # Both naming conventions must resolve to a real SourceItem subclass.
    assert issubclass(cls, SourceItem)


def test_get_item_class_resolves_to_same_class_for_both_keys():
    """Class-name lookup and polymorphic-identity lookup must agree."""
    by_identity = get_item_class("mail_message")
    by_classname = get_item_class("MailMessage")
    assert by_identity is by_classname is MailMessage


def test_get_item_class_rejects_unknown_type():
    """Unknown type raises ValueError listing available types."""
    with pytest.raises(ValueError, match="Unsupported item type"):
        get_item_class("definitely_not_a_real_type")


def test_get_item_class_does_not_use_private_class_registry():
    """Regression: lookup must NOT rely on SourceItem.registry._class_registry,
    which is a SQLAlchemy private attribute that has been reorganised
    across versions and is keyed only by class name."""
    # Build the public-API map and assert polymorphic identities are
    # present — _class_registry only contains class names, so if anyone
    # reverts to it these polymorphic-identity keys disappear.
    m = maintenance_module._build_item_class_map()
    assert "mail_message" in m
    assert "blog_post" in m


def test_update_source_access_control_persists_to_sql_rows(
    db_session, qdrant, test_user
):
    """update_source_access_control writes the resolved project_id /
    sensitivity onto inherited SQL rows (the BM25/vector-search asymmetry
    fix) while leaving explicit overrides untouched."""
    account_project = Project(title="Account Project", state="open")
    other_project = Project(title="Other Project", state="open")
    db_session.add_all([account_project, other_project])
    db_session.flush()

    account = EmailAccount(
        name="AC Account",
        email_address="ac@example.com",
        imap_server="imap.example.com",
        imap_port=993,
        username="ac@example.com",
        password="pw",
        use_ssl=True,
        folders=["INBOX"],
        tags=[],
        active=True,
        user_id=test_user.id,
        project_id=account_project.id,
        sensitivity="internal",
    )
    db_session.add(account)
    db_session.flush()

    # Inherited message: no project assigned -> should inherit the account's.
    inherited = MailMessage(
        sha256=b"inherited" + bytes(23),
        subject="inherited",
        email_account_id=account.id,
    )
    # Explicit override: pinned to a different project -> must NOT be touched.
    explicit = MailMessage(
        sha256=b"explicit" + bytes(24),
        subject="explicit",
        email_account_id=account.id,
        project_id=other_project.id,
    )
    db_session.add_all([inherited, explicit])
    db_session.commit()

    # Preconditions: the set-listener classified the rows on assignment.
    assert inherited.project_id is None
    assert inherited.project_id_inherited is True
    assert explicit.project_id == other_project.id
    assert explicit.project_id_inherited is False

    result = update_source_access_control(
        "email_account", account.id, account.config_version
    )
    assert result["status"] == "success"

    db_session.refresh(inherited)
    db_session.refresh(explicit)

    # Inherited row now carries the account's resolved values...
    assert inherited.project_id == account_project.id
    assert inherited.sensitivity == "internal"
    assert inherited.project_id_inherited is True
    assert inherited.creator_id == test_user.id
    # ...and the explicit override is left exactly as it was.
    assert explicit.project_id == other_project.id
    assert explicit.project_id_inherited is False


def test_update_source_access_control_reresolves_when_source_moves(
    db_session, qdrant, test_user
):
    """A row whose project_id was previously resolved (inherited=True) is
    re-resolved — not skipped — when the source moves to a new project."""
    project_a = Project(title="Project A", state="open")
    project_b = Project(title="Project B", state="open")
    db_session.add_all([project_a, project_b])
    db_session.flush()

    account = EmailAccount(
        name="Moving Account",
        email_address="move@example.com",
        imap_server="imap.example.com",
        imap_port=993,
        username="move@example.com",
        password="pw",
        use_ssl=True,
        folders=["INBOX"],
        tags=[],
        active=True,
        user_id=test_user.id,
        project_id=project_a.id,
        sensitivity="basic",
    )
    db_session.add(account)
    db_session.flush()

    msg = MailMessage(
        sha256=b"moving" + bytes(26),
        subject="moving",
        email_account_id=account.id,
    )
    db_session.add(msg)
    db_session.commit()

    # First run: resolves to project A.
    update_source_access_control("email_account", account.id, account.config_version)
    db_session.refresh(msg)
    assert msg.project_id == project_a.id
    assert msg.project_id_inherited is True

    # Source moves to project B; bump config_version like the real flow does.
    account.project_id = project_b.id
    account.config_version += 1
    db_session.commit()

    # Second run: the stale inherited value must be re-resolved, not kept.
    update_source_access_control("email_account", account.id, account.config_version)
    db_session.refresh(msg)
    assert msg.project_id == project_b.id
    assert msg.project_id_inherited is True


def test_update_source_access_control_crosses_batch_boundary(
    db_session, qdrant, test_user
):
    """>100 inherited items on one source: every row across the 100-item
    batch boundary is resolved. Exercises the per-batch commit and the
    ordered pagination in get_items_for_source."""
    project = Project(title="Batch Project", state="open")
    db_session.add(project)
    db_session.flush()

    account = EmailAccount(
        name="Batch Account",
        email_address="batch@example.com",
        imap_server="imap.example.com",
        imap_port=993,
        username="batch@example.com",
        password="pw",
        use_ssl=True,
        folders=["INBOX"],
        tags=[],
        active=True,
        user_id=test_user.id,
        project_id=project.id,
        sensitivity="basic",
    )
    db_session.add(account)
    db_session.flush()

    n = 150  # > one 100-item batch
    messages = [
        MailMessage(
            sha256=b"batchmsg" + str(i).zfill(24).encode(),
            subject=f"m{i}",
            email_account_id=account.id,
        )
        for i in range(n)
    ]
    db_session.add_all(messages)
    db_session.commit()
    message_ids = [m.id for m in messages]

    result = update_source_access_control(
        "email_account", account.id, account.config_version
    )
    assert result["status"] == "success"
    assert result["updated_items"] == n

    # update_source_access_control wrote through its own session; force the
    # test session to re-read from DB rather than return cached instances.
    db_session.expire_all()
    rows = (
        db_session.query(MailMessage)
        .filter(MailMessage.id.in_(message_ids))
        .all()
    )
    assert len(rows) == n
    # No row skipped or left stale across the batch boundary.
    assert all(r.project_id == project.id for r in rows)
    assert all(r.project_id_inherited is True for r in rows)


def email_account_with_attachment(db_session, owner, project):
    account = EmailAccount(
        name="Derived",
        email_address="derived@example.com",
        imap_server="imap.example.com",
        imap_port=993,
        username="derived@example.com",
        password="pw",
        use_ssl=True,
        folders=["INBOX"],
        tags=[],
        active=True,
        user_id=owner.id,
        project_id=project.id,
        sensitivity="internal",
    )
    message = MailMessage(sha256=b"derived-msg" + bytes(21), email_account=account)
    attachment = EmailAttachment(
        sha256=b"derived-att" + bytes(21), modality="doc", mail_message=message
    )
    db_session.add_all([message, attachment])
    return account, attachment


def forum_post_with_comment(db_session, owner, project):
    post = ForumPost(
        sha256=b"derived-post" + bytes(20),
        modality="forum",
        creator_id=owner.id,
        project_id=project.id,
        sensitivity="internal",
    )
    comment = ForumComment(
        sha256=b"derived-comment" + bytes(17), modality="forum", comment_id="c1", post=post
    )
    db_session.add_all([post, comment])
    return post, comment


def transcript_account_with_task(db_session, owner, project):
    account = TranscriptAccount(
        user_id=owner.id,
        name="Derived FF",
        provider="fireflies",
        project_id=project.id,
        sensitivity="internal",
    )
    account.api_key = "k"
    meeting = Meeting(sha256=b"derived-meeting" + bytes(17), transcript_account=account)
    task = Task(
        sha256=b"derived-task" + bytes(20), task_title="Follow up", source_item=meeting
    )
    db_session.add_all([meeting, task])
    return account, task


@pytest.mark.parametrize(
    "source_type,make_source",
    [
        ("email_account", email_account_with_attachment),
        ("transcript_account", transcript_account_with_task),
    ],
)
def test_update_source_access_control_resolves_derived_items(
    db_session, qdrant, test_user, source_type, make_source
):
    """Attachments and meeting action items inherit from their parent item,
    so a source's reconciliation must reach them too."""
    project = Project(title="Derived Project", state="open")
    db_session.add(project)
    db_session.flush()
    account, child = make_source(db_session, test_user, project)
    db_session.commit()

    result = update_source_access_control(
        source_type, account.id, account.config_version
    )

    assert result["updated_items"] == 2
    db_session.refresh(child)
    assert (child.project_id, child.sensitivity, child.creator_id) == (
        project.id,
        "internal",
        test_user.id,
    )


def make_recon_setup(db_session, test_user, n_messages):
    """Create a project, an EmailAccount on it, and n inherited MailMessages."""
    project = Project(title="Reconcile Project", state="open")
    db_session.add(project)
    db_session.flush()
    account = EmailAccount(
        name="Recon Account",
        email_address="recon@example.com",
        imap_server="imap.example.com",
        imap_port=993,
        username="recon@example.com",
        password="pw",
        use_ssl=True,
        folders=["INBOX"],
        tags=[],
        active=True,
        user_id=test_user.id,
        project_id=project.id,
    )
    db_session.add(account)
    db_session.flush()
    messages = [
        MailMessage(
            sha256=b"recon" + str(i).zfill(27).encode(),
            subject=f"m{i}",
            email_account_id=account.id,
        )
        for i in range(n_messages)
    ]
    db_session.add_all(messages)
    db_session.commit()
    return project, account, messages


def test_reconcile_access_control_full_resolves_all_items(
    db_session, qdrant, test_user
):
    """With no window, the sweep reconciles every source item — inherited
    rows get their project_id resolved onto the SQL row."""
    project, account, messages = make_recon_setup(db_session, test_user, 3)
    assert all(m.project_id is None for m in messages)  # precondition

    result = reconcile_access_control()

    assert result["status"] == "success"
    assert result["reconciled"] == 3
    assert result["changed"] == 3
    for message in messages:
        db_session.refresh(message)
        assert message.project_id == project.id
        assert message.project_id_inherited is True


def test_reconcile_access_control_recent_window_includes_fresh_items(
    db_session, qdrant, test_user
):
    """The recent-window mode reconciles just-ingested items (their
    updated_at is within the window)."""
    project, account, messages = make_recon_setup(db_session, test_user, 2)

    result = reconcile_access_control(updated_within_seconds=3600)

    assert result["reconciled"] == 2
    for message in messages:
        db_session.refresh(message)
        assert message.project_id == project.id


def test_reconcile_access_control_recent_window_excludes_old_items(
    db_session, qdrant, test_user
):
    """An item whose updated_at predates the window is skipped entirely."""
    project, account, messages = make_recon_setup(db_session, test_user, 2)
    # Backdate one item via a Core UPDATE. updated_at is explicit in the SET
    # clause, so onupdate=func.now() does not override it.
    old = datetime.now(timezone.utc) - timedelta(hours=5)
    db_session.execute(
        update(SourceItem)
        .where(SourceItem.id == messages[0].id)
        .values(updated_at=old)
    )
    db_session.commit()

    result = reconcile_access_control(updated_within_seconds=3600)

    assert result["reconciled"] == 1  # only the fresh item examined
    db_session.refresh(messages[0])
    db_session.refresh(messages[1])
    assert messages[0].project_id is None  # excluded -> still inherited NULL
    assert messages[1].project_id == project.id  # included -> resolved


def test_reconcile_access_control_updates_qdrant_payload(
    db_session, qdrant, test_user
):
    """A reconciled item's chunks get their Qdrant payload rewritten with the
    resolved project_id / sensitivity."""
    project, account, messages = make_recon_setup(db_session, test_user, 1)
    chunk = Chunk(
        id=str(uuid.uuid4()),
        source=messages[0],
        content="chunk",
        embedding_model="test-model",
        collection_name="mail",
    )
    db_session.add(chunk)
    db_session.commit()
    chunk_id = str(chunk.id)

    with patch("memory.workers.tasks.maintenance.qdrant.set_payload") as mock_set:
        result = reconcile_access_control()

    assert result["changed"] == 1
    mock_set.assert_called_once()
    _client, collection, point_id, payload = mock_set.call_args.args
    assert collection == "mail"
    assert point_id == chunk_id
    assert payload == {
        "project_id": project.id,
        "sensitivity": "basic",
        "creator_id": test_user.id,
    }


def test_reconcile_access_control_crosses_batch_boundary(
    db_session, qdrant, test_user
):
    """>100 items: the keyset-paginated sweep reconciles every one across
    the batch boundary."""
    n = 150
    project, account, messages = make_recon_setup(db_session, test_user, n)

    result = reconcile_access_control()

    assert result["reconciled"] == n
    assert result["changed"] == n
    rows = (
        db_session.query(MailMessage)
        .filter(MailMessage.id.in_([m.id for m in messages]))
        .all()
    )
    assert len(rows) == n
    assert all(r.project_id == project.id for r in rows)


def test_reconcile_access_control_writes_qdrant_for_explicit_override(
    db_session, qdrant, test_user
):
    """Regression for #86: items with an item-level explicit override
    (``project_id_inherited=False``) must have their Qdrant payload rewritten
    even when ``apply_inherited_access_control`` is a no-op on the SQL row.

    Item-level overrides have no event-driven dispatch path (only data-source
    config changes trigger ``update_source_access_control``), so the periodic
    sweep is the only thing that can land the override in Qdrant. The
    ``before == after`` short-circuit silently assumes Qdrant is already in
    sync, which is exactly what breaks when the override was just applied.
    """
    project, account, messages = make_recon_setup(db_session, test_user, 1)
    msg = messages[0]
    other_project = Project(title="Override Project", state="open")
    db_session.add(other_project)
    db_session.flush()
    # Direct assignment trips the `set` listener -> project_id_inherited=False.
    msg.project_id = other_project.id
    chunk = Chunk(
        id=str(uuid.uuid4()),
        source=msg,
        content="chunk",
        embedding_model="test-model",
        collection_name="mail",
    )
    db_session.add(chunk)
    db_session.commit()
    chunk_id = str(chunk.id)
    assert msg.project_id_inherited is False  # precondition

    with patch("memory.workers.tasks.maintenance.qdrant.set_payload") as mock_set:
        result = reconcile_access_control()

    assert result["changed"] == 1
    mock_set.assert_called_once()
    _client, collection, point_id, payload = mock_set.call_args.args
    assert collection == "mail"
    assert point_id == chunk_id
    assert payload == {
        "project_id": other_project.id,
        "sensitivity": "basic",
        "creator_id": test_user.id,
    }


@pytest.mark.parametrize("rewrite_payloads", [False, True])
def test_reconcile_access_control_rewrite_payloads_writes_unchanged_items(
    db_session, qdrant, test_user, rewrite_payloads
):
    """Rows the migration backfilled have no SQL change to detect, so their
    payloads get creator_id only from an explicit rewrite."""
    project, account, messages = make_recon_setup(db_session, test_user, 1)
    messages[0].apply_inherited_access_control()
    db_session.add(
        Chunk(
            id=str(uuid.uuid4()),
            source=messages[0],
            content="chunk",
            embedding_model="test-model",
            collection_name="mail",
        )
    )
    db_session.commit()

    with patch("memory.workers.tasks.maintenance.qdrant.set_payload") as mock_set:
        reconcile_access_control(rewrite_payloads=rewrite_payloads)

    expected = {"project_id": project.id, "sensitivity": "basic", "creator_id": test_user.id}
    assert [c.args[3] for c in mock_set.call_args_list] == [expected] * rewrite_payloads


@pytest.mark.parametrize("rewrite_payloads", [False, True])
@pytest.mark.parametrize("child_age", [timedelta(0), timedelta(hours=5)])
@pytest.mark.parametrize(
    "make_source",
    [email_account_with_attachment, transcript_account_with_task, forum_post_with_comment],
)
def test_reconcile_access_control_propagates_parent_override_to_derived_items(
    db_session, qdrant, test_user, make_source, child_age, rewrite_payloads
):
    """A child outside the window still follows its moved parent, and a
    child the sweep covers itself isn't written a second time."""
    project = Project(title="Derived Project", state="open")
    moved_to = Project(title="Moved", state="open")
    db_session.add_all([project, moved_to])
    db_session.flush()
    _account, child = make_source(db_session, test_user, project)
    db_session.flush()
    parent = child.get_data_source()
    parent.apply_inherited_access_control()
    child.apply_inherited_access_control()
    chunk = Chunk(
        id=str(uuid.uuid4()),
        source=child,
        content="chunk",
        embedding_model="test-model",
        collection_name="doc",
    )
    db_session.add(chunk)
    db_session.commit()
    db_session.execute(
        update(SourceItem)
        .where(SourceItem.id == child.id)
        .values(updated_at=datetime.now(timezone.utc) - child_age)
    )
    parent.project_id = moved_to.id
    db_session.commit()

    with patch("memory.workers.tasks.maintenance.qdrant.set_payload") as mock_set:
        reconcile_access_control(
            updated_within_seconds=3600, rewrite_payloads=rewrite_payloads
        )

    db_session.refresh(child)
    assert (child.project_id, child.creator_id) == (moved_to.id, test_user.id)
    assert [c.args[2:] for c in mock_set.call_args_list] == [
        (
            str(chunk.id),
            {
                "project_id": moved_to.id,
                "sensitivity": "internal",
                "creator_id": test_user.id,
            },
        )
    ]


def make_task(db_session, user):
    task = ScheduledTask(
        id=str(uuid.uuid4()),
        user_id=user.id,
        task_type="notification",
        cron_expression="0 9 * * *",
        next_scheduled_time=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(task)
    db_session.flush()
    return task


def test_cleanup_old_done_oneoff_tasks(db_session, sample_user):
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Fired one-off, old: cron None + next None + created long ago -> delete
    old_oneoff = ScheduledTask(
        id=str(uuid.uuid4()), user_id=sample_user.id, task_type="notification",
        cron_expression=None, next_scheduled_time=None,
        created_at=now - timedelta(days=120),
    )
    # Fired one-off, recent -> keep
    recent_oneoff = ScheduledTask(
        id=str(uuid.uuid4()), user_id=sample_user.id, task_type="notification",
        cron_expression=None, next_scheduled_time=None,
        created_at=now - timedelta(days=5),
    )
    # Paused recurring (cron set, next None), old -> keep (not a one-off)
    paused_recurring = ScheduledTask(
        id=str(uuid.uuid4()), user_id=sample_user.id, task_type="notification",
        cron_expression="0 9 * * *", next_scheduled_time=None,
        created_at=now - timedelta(days=120),
    )
    # Active one-off (future) -> keep
    pending_oneoff = ScheduledTask(
        id=str(uuid.uuid4()), user_id=sample_user.id, task_type="notification",
        cron_expression=None, next_scheduled_time=now + timedelta(days=1),
        created_at=now - timedelta(days=120),
    )
    db_session.add_all([old_oneoff, recent_oneoff, paused_recurring, pending_oneoff])
    db_session.commit()

    # capture ids before the cleanup runs (it deletes via a separate session)
    old_id = old_oneoff.id
    keep_ids = {recent_oneoff.id, paused_recurring.id, pending_oneoff.id}

    result = maintenance_module.cleanup_old_done_oneoff_tasks(retention_days=90)

    db_session.expire_all()
    assert result["deleted"] == 1
    remaining = {t.id for t in db_session.query(ScheduledTask).all()}
    assert old_id not in remaining
    assert keep_ids <= remaining


def test_cleanup_old_done_oneoff_tasks_skips_in_flight_delivery(db_session, sample_user):
    """A fired far-future one-off (old created_at, cron/next None) whose
    delivery is still PENDING must not be deleted out from under the worker."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    in_flight = ScheduledTask(
        id=str(uuid.uuid4()), user_id=sample_user.id, task_type="notification",
        cron_expression=None, next_scheduled_time=None,
        created_at=now - timedelta(days=120),
    )
    db_session.add(in_flight)
    db_session.flush()
    db_session.add(
        TaskExecution(
            id=str(uuid.uuid4()), task_id=in_flight.id,
            scheduled_time=now - timedelta(minutes=1),
            started_at=None, finished_at=None, status="pending",
        )
    )
    db_session.commit()
    in_flight_id = in_flight.id

    result = maintenance_module.cleanup_old_done_oneoff_tasks(retention_days=90)

    db_session.expire_all()
    assert result["deleted"] == 0
    assert in_flight_id in {t.id for t in db_session.query(ScheduledTask).all()}


def test_cleanup_old_task_executions_deletes_old_terminal(db_session, sample_user):
    task = make_task(db_session, sample_user)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    old_id = str(uuid.uuid4())
    recent_id = str(uuid.uuid4())
    running_id = str(uuid.uuid4())

    db_session.add_all([
        TaskExecution(
            id=old_id, task_id=task.id,
            scheduled_time=now - timedelta(days=40),
            finished_at=now - timedelta(days=40), status="completed",
        ),
        TaskExecution(
            id=recent_id, task_id=task.id,
            scheduled_time=now - timedelta(days=2),
            finished_at=now - timedelta(days=2), status="completed",
        ),
        TaskExecution(
            id=running_id, task_id=task.id,
            scheduled_time=now - timedelta(days=40),
            started_at=now - timedelta(days=40), finished_at=None, status="running",
        ),
    ])
    db_session.commit()

    result = maintenance_module.cleanup_old_task_executions(retention_days=30)

    assert result["deleted"] == 1
    db_session.expire_all()
    remaining = {e.id for e in db_session.query(TaskExecution).all()}
    assert old_id not in remaining
    assert recent_id in remaining
    assert running_id in remaining  # never delete un-finished executions


@pytest.fixture
def taskmeta_table(db_session):
    # Celery creates its result table lazily; alembic ignores it.
    CeleryTaskMeta.__table__.create(db_session.connection(), checkfirst=True)


def add_task_results(session, *rows: tuple[str, int]) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session.execute(
        insert(CeleryTaskMeta.__table__),
        [
            {"task_id": task_id, "status": "SUCCESS", "date_done": now - timedelta(days=age)}
            for task_id, age in rows
        ],
    )
    session.commit()


def test_cleanup_old_task_results_deletes_only_expired(db_session, taskmeta_table):
    add_task_results(db_session, ("old", 10), ("recent", 2))

    result = maintenance_module.cleanup_old_task_results(retention_days=7)

    db_session.expire_all()
    assert result["deleted"] == 1
    assert {r.task_id for r in db_session.query(CeleryTaskMeta)} == {"recent"}


def test_cleanup_old_task_results_deletes_across_batches(
    db_session, taskmeta_table, monkeypatch
):
    monkeypatch.setattr(maintenance_module, "RETENTION_DELETE_BATCH_SIZE", 2)
    add_task_results(db_session, *[(f"old-{i}", 10) for i in range(5)])

    result = maintenance_module.cleanup_old_task_results(retention_days=7)

    assert result["deleted"] == 5
    assert db_session.query(CeleryTaskMeta).count() == 0


def test_purge_old_usage_keeps_each_table_to_its_window(db_session, regular_user, monkeypatch):
    monkeypatch.setattr(settings, "USAGE_RETENTION_DAYS", 365)
    monkeypatch.setattr(settings, "AUDIT_RETENTION_DAYS", 730)
    now = datetime.now(timezone.utc)
    for age in (300, 400, 800):
        at = now - timedelta(days=age)
        db_session.add_all(
            [
                TelemetryEvent(timestamp=at, user_id=regular_user.id, event_type="metric", name=f"t{age}"),
                LLMSpend(agent=f"s{age}", hour=at, cost_usd=0, calls=1),
                AuditEvent(timestamp=at, action=f"a{age}"),
            ]
        )
    db_session.commit()

    result = maintenance_module.purge_old_usage()

    db_session.expire_all()
    assert result == {"telemetry_events": 2, "llm_spend": 2, "audit_events": 1}
    assert sorted(e.name for e in db_session.query(TelemetryEvent)) == ["t300"]
    assert sorted(s.agent for s in db_session.query(LLMSpend)) == ["s300"]
    assert sorted(a.action for a in db_session.query(AuditEvent)) == ["a300", "a400"]


def make_job(status: str, age_days: int, **kwargs) -> PendingJob:
    created = datetime.now(timezone.utc) - timedelta(days=age_days)
    return PendingJob(
        job_type="test",
        status=status,
        created_at=created,
        updated_at=created,
        **kwargs,
    )


@pytest.mark.parametrize(
    "status, age_days, kept",
    [
        (JobStatus.COMPLETE.value, 60, False),
        (JobStatus.FAILED.value, 60, False),
        (JobStatus.COMPLETE.value, 5, True),
        (JobStatus.FAILED.value, 5, True),
        (JobStatus.PENDING.value, 60, True),
        # A lost processing job is failed first, then pruned like any failed job.
        (JobStatus.PROCESSING.value, 60, False),
    ],
)
def test_cleanup_old_pending_jobs(db_session, status, age_days, kept):
    job = make_job(status, age_days)
    db_session.add(job)
    db_session.commit()
    job_id = job.id

    maintenance_module.cleanup_old_pending_jobs(retention_days=30)

    db_session.expire_all()
    assert (db_session.get(PendingJob, job_id) is not None) == kept


@pytest.mark.parametrize(
    "status, started_hours_ago, expected_status",
    [
        (JobStatus.PROCESSING.value, 3, JobStatus.FAILED.value),
        (JobStatus.PROCESSING.value, 1, JobStatus.PROCESSING.value),
        (JobStatus.PENDING.value, 3, JobStatus.PENDING.value),
        (JobStatus.COMPLETE.value, 3, JobStatus.COMPLETE.value),
    ],
)
def test_cleanup_old_pending_jobs_fails_lost_jobs(
    db_session, status, started_hours_ago, expected_status
):
    # Created long ago but (re)started recently must count from the start.
    job = make_job(status, 10)
    job.updated_at = datetime.now(timezone.utc) - timedelta(hours=started_hours_ago)
    db_session.add(job)
    db_session.commit()
    job_id = job.id

    maintenance_module.cleanup_old_pending_jobs(retention_days=30)

    db_session.expire_all()
    assert cast(PendingJob, db_session.get(PendingJob, job_id)).status == expected_status


def test_cleanup_old_pending_jobs_fails_lost_jobs_across_batches(db_session, monkeypatch):
    monkeypatch.setattr(maintenance_module, "RETENTION_DELETE_BATCH_SIZE", 2)
    db_session.add_all([make_job(JobStatus.PROCESSING.value, 1) for _ in range(5)])
    db_session.commit()

    result = maintenance_module.cleanup_old_pending_jobs(retention_days=30)

    db_session.expire_all()
    assert result["failed_lost"] == 5
    assert {
        (j.status, j.error_message, j.completed_at is not None)
        for j in db_session.query(PendingJob).filter(PendingJob.job_type == "test")
    } == {(JobStatus.FAILED.value, maintenance_module.LOST_JOB_ERROR, True)}


@pytest.mark.parametrize(
    "started_hours_ago, expected_status",
    [
        (1, JobStatus.PROCESSING.value),
        (3, JobStatus.FAILED.value),
    ],
)
def test_fail_lost_jobs_allows_twice_the_task_time_limit(
    db_session, started_hours_ago, expected_status
):
    job = make_job(JobStatus.PROCESSING.value, 1)
    job.updated_at = datetime.now(timezone.utc) - timedelta(hours=started_hours_ago)
    db_session.add(job)
    db_session.commit()

    maintenance_module.fail_lost_jobs()

    db_session.expire_all()
    assert cast(PendingJob, db_session.get(PendingJob, job.id)).status == expected_status


def wait_for_lock_waiter(session: Session):
    deadline = time.monotonic() + 10
    waiting = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    while not session.execute(waiting).scalar():
        assert time.monotonic() < deadline, "fail_lost_jobs never blocked on the row lock"
        session.rollback()
        time.sleep(0.05)


@pytest.mark.transactional_db
def test_fail_lost_jobs_keeps_job_that_completes_concurrently(db_session, db_engine):
    job = make_job(JobStatus.PROCESSING.value, 1)
    job.updated_at = datetime.now(timezone.utc) - timedelta(hours=3)
    db_session.add(job)
    db_session.commit()

    with Session(db_engine) as worker, ThreadPoolExecutor(1) as pool:
        worker.execute(
            update(PendingJob)
            .where(PendingJob.id == job.id)
            .values(status=JobStatus.COMPLETE.value)
        )
        failed = pool.submit(maintenance_module.fail_lost_jobs)
        wait_for_lock_waiter(db_session)
        worker.commit()
        assert failed.result(timeout=10) == 0

    db_session.expire_all()
    assert (
        cast(PendingJob, db_session.get(PendingJob, job.id)).status
        == JobStatus.COMPLETE.value
    )


def test_cleanup_old_pending_jobs_deletes_across_batches(db_session, monkeypatch):
    monkeypatch.setattr(maintenance_module, "RETENTION_DELETE_BATCH_SIZE", 2)
    db_session.add_all([make_job(JobStatus.COMPLETE.value, 60) for _ in range(5)])
    db_session.commit()

    result = maintenance_module.cleanup_old_pending_jobs(retention_days=30)

    # The tracked_task wrapper adds its own running job, which is recent and kept.
    assert result["deleted"] == 5


@pytest.mark.parametrize(
    "use_batch, expected",
    [
        (True, {"status": "success", "batch_ids": ["batch-a", "batch-b"]}),
        (False, {"status": "disabled"}),
    ],
)
def test_submit_summary_batch_reports_each_keys_batch(monkeypatch, use_batch, expected):
    monkeypatch.setattr(settings, "SUMMARIZER_USE_BATCH", use_batch)
    monkeypatch.setattr(
        maintenance_module.summary_batch, "submit_pending", lambda session: ["batch-a", "batch-b"]
    )

    assert maintenance_module.submit_summary_batch() == expected


def test_clean_mail_headers_decodes_rows_and_strips_address_tags(db_session, qdrant):
    raw_sender = "=?UTF-8?Q?Karolina_Wal=C4=99cik?= <karolina@far.ai>"
    message = MailMessage(
        sha256=b"clean-mail" + bytes(22),
        modality="mail",
        sender=raw_sender,
        subject="=?UTF-8?Q?Cze=C5=9B=C4=87?=",
        recipients=["r@x.com"],
        tags=["work"],
    )
    attachment = EmailAttachment(
        sha256=b"clean-att" + bytes(23),
        modality="doc",
        mail_message=message,
        tags=["email", "work"],
    )
    mail_chunk, doc_chunk = str(uuid.uuid4()), str(uuid.uuid4())
    db_session.add_all(
        [
            message,
            attachment,
            Chunk(id=mail_chunk, source=message, content="m", embedding_model="m", collection_name="mail"),
            Chunk(id=doc_chunk, source=attachment, content="d", embedding_model="m", collection_name="doc"),
        ]
    )
    db_session.commit()
    for collection, chunk_id, tags in [
        ("mail", mail_chunk, ["work", raw_sender, "r@x.com", "summary-tag"]),
        ("doc", doc_chunk, ["email", raw_sender, "work"]),
    ]:
        qd.ensure_collection_exists(qdrant, collection, 1024)
        qd.upsert_vectors(qdrant, collection, [chunk_id], [[1] * 1024], [{"tags": tags, "sender": raw_sender}])

    clean_mail_headers()
    clean_mail_headers()

    db_session.refresh(message)
    assert (message.sender, message.subject) == ("Karolina Walęcik <karolina@far.ai>", "Cześć")
    assert qd.get_payloads(qdrant, "mail", [mail_chunk])[mail_chunk] == {
        "tags": ["work", "summary-tag"],
        "sender": "Karolina Walęcik <karolina@far.ai>",
        "subject": "Cześć",
    }
    assert qd.get_payloads(qdrant, "doc", [doc_chunk])[doc_chunk]["tags"] == ["email", "work"]
