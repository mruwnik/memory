import pytest
from unittest.mock import MagicMock, patch

import qdrant_client
from qdrant_client.http import models as qdrant_models
from qdrant_client.http.exceptions import UnexpectedResponse

from memory.workers.qdrant import (
    DEFAULT_COLLECTIONS,
    ensure_collection_exists,
    initialize_collections,
    upsert_vectors,
    search_vectors,
    delete_vectors,
)


@pytest.fixture
def mock_qdrant_client():
    with patch.object(qdrant_client, "QdrantClient", return_value=MagicMock()) as mock_client:
        yield mock_client


def test_ensure_collection_exists_existing(mock_qdrant_client):
    mock_qdrant_client.get_collection.return_value = {}
    assert not ensure_collection_exists(mock_qdrant_client, "test_collection", 128)
    
    mock_qdrant_client.get_collection.assert_called_once_with("test_collection")
    mock_qdrant_client.create_collection.assert_not_called()


def test_ensure_collection_exists_new(mock_qdrant_client):
    mock_qdrant_client.get_collection.side_effect = UnexpectedResponse(
        status_code=404, reason_phrase='asd', content=b'asd', headers=None
    )
    
    assert ensure_collection_exists(mock_qdrant_client, "test_collection", 128)
    
    mock_qdrant_client.get_collection.assert_called_once_with("test_collection")
    mock_qdrant_client.create_collection.assert_called_once()
    mock_qdrant_client.create_payload_index.assert_called_once()


def test_initialize_collections(mock_qdrant_client):
    initialize_collections(mock_qdrant_client)
    
    assert mock_qdrant_client.get_collection.call_count == len(DEFAULT_COLLECTIONS)


def test_upsert_vectors(mock_qdrant_client):
    ids = ["1", "2"]
    vectors = [[0.1, 0.2], [0.3, 0.4]]
    payloads = [{"tag": "test1"}, {"tag": "test2"}]
    
    upsert_vectors(mock_qdrant_client, "test_collection", ids, vectors, payloads)
    
    mock_qdrant_client.upsert.assert_called_once()
    args, kwargs = mock_qdrant_client.upsert.call_args
    assert kwargs["collection_name"] == "test_collection"
    assert len(kwargs["points"]) == 2
    
    # Check points were created correctly
    points = kwargs["points"]
    assert points[0].id == "1"
    assert points[0].vector == [0.1, 0.2]
    assert points[0].payload == {"tag": "test1"}
    assert points[1].id == "2"
    assert points[1].vector == [0.3, 0.4]
    assert points[1].payload == {"tag": "test2"}


def test_delete_vectors(mock_qdrant_client):
    ids = ["1", "2"]
    
    delete_vectors(mock_qdrant_client, "test_collection", ids)
    
    mock_qdrant_client.delete.assert_called_once()
    args, kwargs = mock_qdrant_client.delete.call_args
    
    assert kwargs["collection_name"] == "test_collection"
    assert isinstance(kwargs["points_selector"], qdrant_models.PointIdsList)
    assert kwargs["points_selector"].points == ids 