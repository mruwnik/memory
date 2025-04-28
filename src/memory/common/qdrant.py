import logging
from typing import Any, cast

import qdrant_client
from qdrant_client.http import models as qdrant_models
from qdrant_client.http.exceptions import UnexpectedResponse
from memory.common import settings
from memory.common.embedding import Collection, DEFAULT_COLLECTIONS, DistanceType, Vector

logger = logging.getLogger(__name__)


def get_qdrant_client() -> qdrant_client.QdrantClient:
    """Create and return a Qdrant client using environment configuration."""
    logger.info(f"Connecting to Qdrant at {settings.QDRANT_HOST}:{settings.QDRANT_PORT}")
    
    return qdrant_client.QdrantClient(
        host=settings.QDRANT_HOST,
        port=settings.QDRANT_PORT,
        grpc_port=settings.QDRANT_GRPC_PORT if settings.QDRANT_PREFER_GRPC else None,
        prefer_grpc=settings.QDRANT_PREFER_GRPC,
        api_key=settings.QDRANT_API_KEY,
        timeout=settings.QDRANT_TIMEOUT,
    )


def ensure_collection_exists(
    client: qdrant_client.QdrantClient,
    collection_name: str,
    dimension: int,
    distance: DistanceType = "Cosine",
    on_disk: bool = True,
    shards: int = 1,
) -> bool:
    """
    Ensure a collection exists with the specified parameters.
    
    Args:
        client: Qdrant client
        collection_name: Name of the collection
        dimension: Vector dimension
        distance: Distance metric (Cosine, Dot, Euclidean)
        on_disk: Whether to store vectors on disk
        shards: Number of shards for the collection
        
    Returns:
        True if the collection was created, False if it already existed
    """
    try:
        client.get_collection(collection_name)
        logger.debug(f"Collection {collection_name} already exists")
        return False
    except (UnexpectedResponse, ValueError):
        logger.info(f"Creating collection {collection_name} with dimension {dimension}")
        client.create_collection(
            collection_name=collection_name,
            vectors_config=qdrant_models.VectorParams(
                size=dimension,
                distance=cast(qdrant_models.Distance, distance),
            ),
            on_disk_payload=on_disk,
            shard_number=shards,
        )
        
        # Create common payload indexes
        client.create_payload_index(
            collection_name=collection_name,
            field_name="tags",
            field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
        )
        
        return True


def initialize_collections(client: qdrant_client.QdrantClient, collections: dict[str, Collection] = None) -> None:
    """
    Initialize all required collections in Qdrant.
    
    Args:
        client: Qdrant client
        collections: Dictionary mapping collection names to their parameters.
                    If None, defaults to the DEFAULT_COLLECTIONS.
    """
    if collections is None:
        collections = DEFAULT_COLLECTIONS
    
    for name, params in collections.items():
        ensure_collection_exists(
            client,
            collection_name=name,
            dimension=params["dimension"],
            distance=params.get("distance", "Cosine"),
            on_disk=params.get("on_disk", True),
            shards=params.get("shards", 1),
        )


def setup_qdrant() -> qdrant_client.QdrantClient:
    """Get a Qdrant client and initialize collections.
    
    Returns:
        Configured Qdrant client
    """
    client = get_qdrant_client()
    initialize_collections(client)
    return client


def upsert_vectors(
    client: qdrant_client.QdrantClient, 
    collection_name: str, 
    ids: list[str],
    vectors: list[Vector],
    payloads: list[dict[str, Any]] = None,
) -> None:
    """Upsert vectors into a collection.
    
    Args:
        client: Qdrant client
        collection_name: Name of the collection
        ids: List of vector IDs (as strings)
        vectors: List of vectors
        payloads: List of payloads, one per vector
    """
    if payloads is None:
        payloads = [{} for _ in ids]
    
    points = [
        qdrant_models.PointStruct(
            id=id_str,
            vector=vector,
            payload=payload,
        )
        for id_str, vector, payload in zip(ids, vectors, payloads)
    ]
    
    client.upsert(
        collection_name=collection_name,
        points=points,
    )
    
    logger.debug(f"Upserted {len(ids)} vectors into {collection_name}")


def search_vectors(
    client: qdrant_client.QdrantClient,
    collection_name: str,
    query_vector: Vector,
    filter_params: dict = None,
    limit: int = 10,
) -> list[qdrant_models.ScoredPoint]:
    """Search for similar vectors in a collection.
    
    Args:
        client: Qdrant client
        collection_name: Name of the collection
        query_vector: Query vector
        filter_params: Filter parameters to apply (e.g., {"tags": {"value": "work"}})
        limit: Maximum number of results to return
        
    Returns:
        List of scored points
    """
    filter_obj = None
    if filter_params:
        filter_obj = qdrant_models.Filter(**filter_params)
    
    return client.search(
        collection_name=collection_name,
        query_vector=query_vector,
        query_filter=filter_obj,
        limit=limit,
    )


def delete_vectors(
    client: qdrant_client.QdrantClient,
    collection_name: str,
    ids: list[str],
) -> None:
    """
    Delete vectors from a collection.
    
    Args:
        client: Qdrant client
        collection_name: Name of the collection
        ids: List of vector IDs to delete
    """
    client.delete(
        collection_name=collection_name,
        points_selector=qdrant_models.PointIdsList(
            points=ids,
        ),
    )
    
    logger.debug(f"Deleted {len(ids)} vectors from {collection_name}")


def get_collection_info(client: qdrant_client.QdrantClient, collection_name: str) -> dict:
    """
    Get information about a collection.
    
    Args:
        client: Qdrant client
        collection_name: Name of the collection
        
    Returns:
        Dictionary with collection information
    """
    info = client.get_collection(collection_name)
    return info.model_dump()
