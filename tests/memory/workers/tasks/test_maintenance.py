import uuid
from datetime import datetime, timedelta
from unittest.mock import patch, call

import pytest
from PIL import Image

from memory.common import qdrant as qd
from memory.common import embedding, settings
from memory.common.db.models import Chunk, SourceItem
from memory.workers.tasks.maintenance import (
    clean_collection,
    reingest_chunk,
    check_batch,
    reingest_missing_chunks,
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
        with patch.object(settings, "CHUNK_REINGEST_SINCE_MINUTES", 60):
            with patch.object(embedding, "embed_chunks", return_value=[[1] * 1024]):
                result = reingest_missing_chunks(batch_size=batch_size)

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


def test_reingest_missing_chunks_no_chunks(db_session):
    assert reingest_missing_chunks() == {}
