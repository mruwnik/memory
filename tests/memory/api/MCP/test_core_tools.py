"""Tests for MCP core tools: search, observe, fetch operations."""
# pyright: reportFunctionMemberAccess=false

import base64
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from PIL import Image

from memory.api.MCP.servers.core import (
    RawObservation,
    search,
    observe,
    search_observations,
    fetch_file,
    get_item,
    list_items,
    count_items,
    filter_observation_source_ids,
    filter_source_ids,
)
from memory.api.search.types import SearchFilters
from memory.common import extract


# ====== search tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.filter_source_ids")
async def test_search_basic_query(
    mock_filter_ids, mock_extract, mock_search_base
):
    """Basic search with query returns results."""
    mock_extract.extract_text.return_value = "extracted text"
    mock_result = MagicMock()
    mock_result.model_dump.return_value = {"id": 1, "score": 0.9}
    mock_search_base.return_value = [mock_result]
    mock_filter_ids.return_value = None

    results = await search.fn(query="test query")

    assert len(results) == 1
    assert results[0]["id"] == 1
    mock_extract.extract_text.assert_called_once_with("test query", skip_summary=True)
    mock_search_base.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.filter_source_ids")
async def test_search_with_modalities(
    mock_filter_ids, mock_extract, mock_search_base
):
    """Search filters by specified modalities."""
    mock_extract.extract_text.return_value = "extracted text"
    mock_search_base.return_value = []
    mock_filter_ids.return_value = None

    await search.fn(query="test", modalities={"mail", "blog"})

    call_kwargs = mock_search_base.call_args[1]
    assert "mail" in call_kwargs["modalities"]
    assert "blog" in call_kwargs["modalities"]


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.filter_source_ids")
async def test_search_excludes_observation_modalities(
    mock_filter_ids, mock_extract, mock_search_base
):
    """Search excludes observation modalities even if specified."""
    mock_extract.extract_text.return_value = "extracted text"
    mock_search_base.return_value = []
    mock_filter_ids.return_value = None

    await search.fn(query="test", modalities={"semantic", "temporal"})

    call_kwargs = mock_search_base.call_args[1]
    assert "semantic" not in call_kwargs["modalities"]
    assert "temporal" not in call_kwargs["modalities"]


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.filter_source_ids")
async def test_search_limit_enforced(
    mock_filter_ids, mock_extract, mock_search_base
):
    """Search enforces max limit of 100."""
    mock_extract.extract_text.return_value = "extracted text"
    mock_search_base.return_value = []
    mock_filter_ids.return_value = None

    await search.fn(query="test", limit=500)

    call_kwargs = mock_search_base.call_args[1]
    assert call_kwargs["config"].limit == 100


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.filter_source_ids")
async def test_search_with_filters(
    mock_filter_ids, mock_extract, mock_search_base
):
    """Search applies filters from filters parameter."""
    mock_extract.extract_text.return_value = "extracted text"
    mock_search_base.return_value = []
    mock_filter_ids.return_value = [1, 2, 3]

    filters = {"tags": ["important"], "min_size": 1000}
    await search.fn(query="test", filters=filters)

    call_kwargs = mock_search_base.call_args[1]
    assert call_kwargs["filters"]["source_ids"] == [1, 2, 3]


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.filter_source_ids")
async def test_search_previews_config(
    mock_filter_ids, mock_extract, mock_search_base
):
    """Search passes previews config correctly."""
    mock_extract.extract_text.return_value = "extracted text"
    mock_search_base.return_value = []
    mock_filter_ids.return_value = None

    await search.fn(query="test", previews=True)

    call_kwargs = mock_search_base.call_args[1]
    assert call_kwargs["config"].previews is True


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.filter_source_ids")
async def test_search_use_scores_config(
    mock_filter_ids, mock_extract, mock_search_base
):
    """Search passes useScores config correctly."""
    mock_extract.extract_text.return_value = "extracted text"
    mock_search_base.return_value = []
    mock_filter_ids.return_value = None

    await search.fn(query="test", use_scores=True)

    call_kwargs = mock_search_base.call_args[1]
    assert call_kwargs["config"].useScores is True


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.filter_source_ids")
async def test_search_empty_modalities_searches_all(
    mock_filter_ids, mock_extract, mock_search_base
):
    """Search with empty modalities searches all available."""

    mock_extract.extract_text.return_value = "extracted text"
    mock_search_base.return_value = []
    mock_filter_ids.return_value = None

    await search.fn(query="test", modalities=set())

    call_kwargs = mock_search_base.call_args[1]
    # Should include all collections minus observation collections
    assert len(call_kwargs["modalities"]) > 0


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.filter_source_ids")
async def test_search_returns_serialized_results(
    mock_filter_ids, mock_extract, mock_search_base
):
    """Search returns model_dump() of results."""
    mock_extract.extract_text.return_value = "extracted text"
    mock_result1 = MagicMock()
    mock_result1.model_dump.return_value = {"id": 1, "score": 0.9}
    mock_result2 = MagicMock()
    mock_result2.model_dump.return_value = {"id": 2, "score": 0.7}
    mock_search_base.return_value = [mock_result1, mock_result2]
    mock_filter_ids.return_value = None

    results = await search.fn(query="test")

    assert len(results) == 2
    assert results[0]["id"] == 1
    assert results[1]["id"] == 2
    mock_result1.model_dump.assert_called_once()
    mock_result2.model_dump.assert_called_once()


# ====== observe tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.celery_app")
@patch("memory.api.MCP.servers.core.settings")
async def test_observe_single_observation(mock_settings, mock_celery):
    """Record single observation dispatches Celery task."""
    mock_settings.CELERY_QUEUE_PREFIX = "test"
    mock_task = MagicMock()
    mock_task.id = "task-123"
    mock_celery.send_task.return_value = mock_task

    obs = RawObservation(
        subject="user_preference",
        content="User prefers dark mode",
        observation_type="preference",
    )
    result = await observe.fn(observations=[obs])

    assert result["status"] == "queued"
    assert len(result["task_ids"]) == 1
    assert "User prefers dark mode" in result["task_ids"]
    mock_celery.send_task.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.celery_app")
@patch("memory.api.MCP.servers.core.settings")
async def test_observe_multiple_observations(mock_settings, mock_celery):
    """Record multiple observations dispatches multiple tasks."""
    mock_settings.CELERY_QUEUE_PREFIX = "test"
    mock_task1 = MagicMock()
    mock_task1.id = "task-1"
    mock_task2 = MagicMock()
    mock_task2.id = "task-2"
    mock_celery.send_task.side_effect = [mock_task1, mock_task2]

    obs1 = RawObservation(subject="pref1", content="Observation 1")
    obs2 = RawObservation(subject="pref2", content="Observation 2")
    result = await observe.fn(observations=[obs1, obs2])

    assert len(result["task_ids"]) == 2
    assert mock_celery.send_task.call_count == 2


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.celery_app")
@patch("memory.api.MCP.servers.core.settings")
async def test_observe_with_all_fields(mock_settings, mock_celery):
    """Observation with all fields passes them to Celery task."""
    mock_settings.CELERY_QUEUE_PREFIX = "test"
    mock_task = MagicMock()
    mock_task.id = "task-123"
    mock_celery.send_task.return_value = mock_task

    obs = RawObservation(
        subject="user_belief",
        content="User believes in type safety",
        observation_type="belief",
        confidences={"accuracy": 0.9},
        evidence={"quote": "I love TypeScript", "context": "coding discussion"},
        tags=["programming", "typescript"],
    )
    await observe.fn(
        observations=[obs], session_id="session-456", agent_model="gpt-4"
    )

    call_kwargs = mock_celery.send_task.call_args[1]["kwargs"]
    assert call_kwargs["subject"] == "user_belief"
    assert call_kwargs["content"] == "User believes in type safety"
    assert call_kwargs["observation_type"] == "belief"
    assert call_kwargs["confidences"] == {"accuracy": 0.9}
    assert call_kwargs["evidence"]["quote"] == "I love TypeScript"
    assert call_kwargs["tags"] == ["programming", "typescript"]
    assert call_kwargs["session_id"] == "session-456"
    assert call_kwargs["agent_model"] == "gpt-4"


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.celery_app")
@patch("memory.api.MCP.servers.core.settings")
async def test_observe_truncates_long_content_in_task_ids(mock_settings, mock_celery):
    """Long observation content is truncated in task_ids."""
    mock_settings.CELERY_QUEUE_PREFIX = "test"
    mock_task = MagicMock()
    mock_task.id = "task-123"
    mock_celery.send_task.return_value = mock_task

    long_content = "A" * 100
    obs = RawObservation(subject="test", content=long_content)
    result = await observe.fn(observations=[obs])

    # Content should be truncated to 47 chars + "..."
    task_key = list(result["task_ids"].keys())[0]
    assert len(task_key) == 50
    assert task_key.endswith("...")


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.celery_app")
@patch("memory.api.MCP.servers.core.settings")
async def test_observe_default_values(mock_settings, mock_celery):
    """Observation uses default values when not specified."""
    mock_settings.CELERY_QUEUE_PREFIX = "test"
    mock_task = MagicMock()
    mock_task.id = "task-123"
    mock_celery.send_task.return_value = mock_task

    obs = RawObservation(subject="test", content="test content")
    await observe.fn(observations=[obs])

    call_kwargs = mock_celery.send_task.call_args[1]["kwargs"]
    assert call_kwargs["observation_type"] == "general"
    assert call_kwargs["confidences"] == {}
    assert call_kwargs["evidence"] is None
    assert call_kwargs["tags"] == []
    assert call_kwargs["session_id"] is None
    assert call_kwargs["agent_model"] == "unknown"


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.celery_app")
@patch("memory.api.MCP.servers.core.settings")
async def test_observe_queue_name(mock_settings, mock_celery):
    """Observation task sent to correct queue."""
    mock_settings.CELERY_QUEUE_PREFIX = "prod"
    mock_task = MagicMock()
    mock_task.id = "task-123"
    mock_celery.send_task.return_value = mock_task

    obs = RawObservation(subject="test", content="test")
    await observe.fn(observations=[obs])

    call_args = mock_celery.send_task.call_args
    assert call_args[1]["queue"] == "prod-notes"


# ====== search_observations tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.observation")
@patch("memory.api.MCP.servers.core.filter_observation_source_ids")
async def test_search_observations_basic_query(
    mock_filter_ids, mock_obs_formatter, mock_search_base
):
    """Basic observation search returns formatted results."""
    mock_obs_formatter.generate_semantic_text.return_value = "semantic text"
    mock_obs_formatter.generate_temporal_text.return_value = "temporal text"
    mock_filter_ids.return_value = None

    mock_result = MagicMock()
    mock_result.content = "User prefers dark mode"
    mock_result.tags = ["preference"]
    mock_result.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    mock_result.metadata = {"confidence": 0.9}
    mock_search_base.return_value = [mock_result]

    results = await search_observations.fn(query="dark mode preferences")

    assert len(results) == 1
    assert results[0]["content"] == "User prefers dark mode"
    assert results[0]["tags"] == ["preference"]
    assert results[0]["created_at"] == "2024-01-01T00:00:00+00:00"
    assert results[0]["metadata"] == {"confidence": 0.9}


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.observation")
@patch("memory.api.MCP.servers.core.filter_observation_source_ids")
async def test_search_observations_with_subject_filter(
    mock_filter_ids, mock_obs_formatter, mock_search_base
):
    """Search observations filters by subject."""
    mock_obs_formatter.generate_semantic_text.return_value = "semantic text"
    mock_obs_formatter.generate_temporal_text.return_value = "temporal text"
    mock_filter_ids.return_value = None
    mock_search_base.return_value = []

    await search_observations.fn(query="test", subject="user_preferences")

    call_kwargs = mock_search_base.call_args[1]
    assert call_kwargs["filters"]["subject"] == "user_preferences"


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.observation")
@patch("memory.api.MCP.servers.core.filter_observation_source_ids")
async def test_search_observations_with_tags_filter(
    mock_filter_ids, mock_obs_formatter, mock_search_base
):
    """Search observations filters by tags."""
    mock_obs_formatter.generate_semantic_text.return_value = "semantic text"
    mock_obs_formatter.generate_temporal_text.return_value = "temporal text"
    mock_filter_ids.return_value = [1, 2, 3]
    mock_search_base.return_value = []

    await search_observations.fn(query="test", tags=["programming", "typescript"])

    call_kwargs = mock_search_base.call_args[1]
    assert call_kwargs["filters"]["tags"] == ["programming", "typescript"]
    assert call_kwargs["filters"]["source_ids"] == [1, 2, 3]
    mock_filter_ids.assert_called_once_with(tags=["programming", "typescript"])


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.observation")
@patch("memory.api.MCP.servers.core.filter_observation_source_ids")
async def test_search_observations_with_observation_types(
    mock_filter_ids, mock_obs_formatter, mock_search_base
):
    """Search observations filters by observation types."""
    mock_obs_formatter.generate_semantic_text.return_value = "semantic text"
    mock_obs_formatter.generate_temporal_text.return_value = "temporal text"
    mock_filter_ids.return_value = None
    mock_search_base.return_value = []

    await search_observations.fn(
        query="test", observation_types=["belief", "preference"]
    )

    call_kwargs = mock_search_base.call_args[1]
    assert call_kwargs["filters"]["observation_types"] == ["belief", "preference"]


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.observation")
@patch("memory.api.MCP.servers.core.filter_observation_source_ids")
async def test_search_observations_with_min_confidences(
    mock_filter_ids, mock_obs_formatter, mock_search_base
):
    """Search observations filters by minimum confidence thresholds."""
    mock_obs_formatter.generate_semantic_text.return_value = "semantic text"
    mock_obs_formatter.generate_temporal_text.return_value = "temporal text"
    mock_filter_ids.return_value = None
    mock_search_base.return_value = []

    min_conf = {"accuracy": 0.8, "relevance": 0.7}
    await search_observations.fn(query="test", min_confidences=min_conf)

    call_kwargs = mock_search_base.call_args[1]
    assert call_kwargs["filters"]["min_confidences"] == min_conf


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.observation")
@patch("memory.api.MCP.servers.core.filter_observation_source_ids")
async def test_search_observations_limit_enforced(
    mock_filter_ids, mock_obs_formatter, mock_search_base
):
    """Search observations enforces max limit of 100."""
    mock_obs_formatter.generate_semantic_text.return_value = "semantic text"
    mock_obs_formatter.generate_temporal_text.return_value = "temporal text"
    mock_filter_ids.return_value = None
    mock_search_base.return_value = []

    await search_observations.fn(query="test", limit=500)

    call_kwargs = mock_search_base.call_args[1]
    assert call_kwargs["config"].limit == 100


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.observation")
@patch("memory.api.MCP.servers.core.filter_observation_source_ids")
async def test_search_observations_generates_semantic_text(
    mock_filter_ids, mock_obs_formatter, mock_search_base
):
    """Search observations generates semantic text from query and filters."""
    mock_obs_formatter.generate_semantic_text.return_value = "semantic text"
    mock_obs_formatter.generate_temporal_text.return_value = "temporal text"
    mock_filter_ids.return_value = None
    mock_search_base.return_value = []

    await search_observations.fn(
        query="dark mode",
        subject="ui_preferences",
        observation_types=["preference"],
    )

    mock_obs_formatter.generate_semantic_text.assert_called_once_with(
        subject="ui_preferences",
        observation_type="preference",
        content="dark mode",
        evidence=None,
    )


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.observation")
@patch("memory.api.MCP.servers.core.filter_observation_source_ids")
async def test_search_observations_generates_temporal_text(
    mock_filter_ids, mock_obs_formatter, mock_search_base
):
    """Search observations generates temporal text."""
    mock_obs_formatter.generate_semantic_text.return_value = "semantic text"
    mock_obs_formatter.generate_temporal_text.return_value = "temporal text"
    mock_filter_ids.return_value = None
    mock_search_base.return_value = []

    await search_observations.fn(query="test", subject="test_subject")

    mock_obs_formatter.generate_temporal_text.assert_called_once()
    call_args = mock_obs_formatter.generate_temporal_text.call_args[1]
    assert call_args["subject"] == "test_subject"
    assert call_args["content"] == "test"
    assert "created_at" in call_args


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.observation")
@patch("memory.api.MCP.servers.core.filter_observation_source_ids")
async def test_search_observations_searches_semantic_and_temporal_modalities(
    mock_filter_ids, mock_obs_formatter, mock_search_base
):
    """Search observations only searches semantic and temporal modalities."""
    mock_obs_formatter.generate_semantic_text.return_value = "semantic text"
    mock_obs_formatter.generate_temporal_text.return_value = "temporal text"
    mock_filter_ids.return_value = None
    mock_search_base.return_value = []

    await search_observations.fn(query="test")

    call_kwargs = mock_search_base.call_args[1]
    assert call_kwargs["modalities"] == {"semantic", "temporal"}


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.observation")
@patch("memory.api.MCP.servers.core.filter_observation_source_ids")
async def test_search_observations_handles_null_created_at(
    mock_filter_ids, mock_obs_formatter, mock_search_base
):
    """Search observations handles None created_at."""
    mock_obs_formatter.generate_semantic_text.return_value = "semantic text"
    mock_obs_formatter.generate_temporal_text.return_value = "temporal text"
    mock_filter_ids.return_value = None

    mock_result = MagicMock()
    mock_result.content = "Content"
    mock_result.tags = []
    mock_result.created_at = None
    mock_result.metadata = {}
    mock_search_base.return_value = [mock_result]

    results = await search_observations.fn(query="test")

    assert results[0]["created_at"] is None


# ====== fetch_file tests ======


@pytest.mark.parametrize(
    "mime_type,expected_type",
    [
        ("text/plain", "text"),
        ("text/html", "text"),
        ("application/pdf", "text"),
        ("image/jpeg", "image"),
        ("image/png", "image"),
    ],
)
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_type_detection(
    mock_settings, mock_paths, mock_extract, mime_type, expected_type
):
    """Fetch file correctly detects text vs image content."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_paths.validate_path_within_directory.return_value = mock_path

    mock_extract.get_mime_type.return_value = mime_type

    if expected_type == "text":
        chunk = extract.DataChunk(data=["text content"], mime_type=mime_type)
    else:
        img = Image.new("RGB", (10, 10))
        chunk = extract.DataChunk(data=[img], mime_type=mime_type)

    mock_extract.extract_data_chunks.return_value = [chunk]

    result = fetch_file.fn(filename="test.txt")

    assert len(result["content"]) == 1
    assert result["content"][0]["type"] == expected_type
    assert result["content"][0]["mime_type"] == mime_type


@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_text_content(mock_settings, mock_paths, mock_extract):
    """Fetch file returns text content as string."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_paths.validate_path_within_directory.return_value = mock_path

    mock_extract.get_mime_type.return_value = "text/plain"
    chunk = extract.DataChunk(data=["Hello, world!"], mime_type="text/plain")
    mock_extract.extract_data_chunks.return_value = [chunk]

    result = fetch_file.fn(filename="test.txt")

    assert result["content"][0]["data"] == "Hello, world!"
    assert result["content"][0]["type"] == "text"


@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_image_content_base64(mock_settings, mock_paths, mock_extract):
    """Fetch file returns image content as base64."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_paths.validate_path_within_directory.return_value = mock_path

    mock_extract.get_mime_type.return_value = "image/png"
    img = Image.new("RGB", (10, 10))
    chunk = extract.DataChunk(data=[img], mime_type="image/png")
    mock_extract.extract_data_chunks.return_value = [chunk]

    result = fetch_file.fn(filename="test.png")

    assert result["content"][0]["type"] == "image"
    # Should be base64 encoded
    content = result["content"][0]["data"]
    assert isinstance(content, str)
    # Verify it's valid base64
    decoded = base64.b64decode(content)
    assert isinstance(decoded, bytes)


@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_multiple_chunks(mock_settings, mock_paths, mock_extract):
    """Fetch file handles multiple data chunks."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_paths.validate_path_within_directory.return_value = mock_path

    mock_extract.get_mime_type.return_value = "text/plain"
    chunk1 = extract.DataChunk(data=["chunk 1", "chunk 2"], mime_type="text/plain")
    chunk2 = extract.DataChunk(data=["chunk 3"], mime_type="text/plain")
    mock_extract.extract_data_chunks.return_value = [chunk1, chunk2]

    result = fetch_file.fn(filename="test.txt")

    assert len(result["content"]) == 3
    assert result["content"][0]["data"] == "chunk 1"
    assert result["content"][1]["data"] == "chunk 2"
    assert result["content"][2]["data"] == "chunk 3"


@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_not_found(mock_settings, mock_paths):
    """Fetch file raises error for nonexistent file."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = False
    mock_paths.validate_path_within_directory.return_value = mock_path

    with pytest.raises(FileNotFoundError, match="File not found"):
        fetch_file.fn(filename="nonexistent.txt")


@pytest.mark.parametrize(
    "malicious_filename",
    [
        "../etc/passwd",
        "../../etc/passwd",
        "subdir/../../../etc/passwd",
    ],
)
@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_blocks_path_traversal(
    mock_settings, mock_paths, malicious_filename
):
    """Fetch file blocks path traversal attempts."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_paths.validate_path_within_directory.side_effect = ValueError("Invalid path")

    with pytest.raises(ValueError, match="Invalid path"):
        fetch_file.fn(filename=malicious_filename)


@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_strips_whitespace(mock_settings, mock_paths, mock_extract):
    """Fetch file strips whitespace from filename."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_paths.validate_path_within_directory.return_value = mock_path

    mock_extract.get_mime_type.return_value = "text/plain"
    chunk = extract.DataChunk(data=["content"], mime_type="text/plain")
    mock_extract.extract_data_chunks.return_value = [chunk]

    fetch_file.fn(filename="  test.txt  ")

    # verify path validation was called with stripped filename
    call_args = mock_paths.validate_path_within_directory.call_args[0]
    assert call_args[1] == "test.txt"


@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_skip_summary(mock_settings, mock_paths, mock_extract):
    """Fetch file calls extract with skip_summary=True."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_paths.validate_path_within_directory.return_value = mock_path

    mock_extract.get_mime_type.return_value = "text/plain"
    chunk = extract.DataChunk(data=["content"], mime_type="text/plain")
    mock_extract.extract_data_chunks.return_value = [chunk]

    fetch_file.fn(filename="test.txt")

    call_kwargs = mock_extract.extract_data_chunks.call_args[1]
    assert call_kwargs["skip_summary"] is True


# ====== filter_observation_source_ids tests ======


@patch("memory.api.MCP.servers.core.make_session")
def test_filter_observation_source_ids_no_filters(mock_make_session):
    """Returns None when no filters provided."""
    result = filter_observation_source_ids(tags=None, observation_types=None)
    assert result is None


@patch("memory.api.MCP.servers.core.make_session")
def test_filter_observation_source_ids_by_tags(mock_make_session):
    """Filters observations by tags."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_obs1 = MagicMock()
    mock_obs1.id = 1
    mock_obs2 = MagicMock()
    mock_obs2.id = 2
    mock_session.query.return_value.filter.return_value.all.return_value = [
        mock_obs1,
        mock_obs2,
    ]

    result = filter_observation_source_ids(tags=["programming"])

    assert result == [1, 2]


@patch("memory.api.MCP.servers.core.make_session")
def test_filter_observation_source_ids_by_observation_types(mock_make_session):
    """Filters observations by observation types."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_obs = MagicMock()
    mock_obs.id = 1
    mock_session.query.return_value.filter.return_value.all.return_value = [mock_obs]

    result = filter_observation_source_ids(observation_types=["belief", "preference"])

    assert result == [1]


@patch("memory.api.MCP.servers.core.make_session")
def test_filter_observation_source_ids_by_both(mock_make_session):
    """Filters observations by both tags and types."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_obs = MagicMock()
    mock_obs.id = 1
    mock_session.query.return_value.filter.return_value.filter.return_value.all.return_value = [
        mock_obs
    ]

    result = filter_observation_source_ids(
        tags=["programming"], observation_types=["belief"]
    )

    assert result == [1]


# ====== filter_source_ids tests ======


@patch("memory.api.MCP.servers.core.make_session")
def test_filter_source_ids_with_existing_ids(mock_make_session):
    """Returns existing source_ids if provided."""
    filters = SearchFilters(source_ids=[1, 2, 3])
    result = filter_source_ids(set(), filters)
    assert result == [1, 2, 3]


@patch("memory.api.MCP.servers.core.make_session")
def test_filter_source_ids_no_filters(mock_make_session):
    """Returns None when no applicable filters."""
    filters = SearchFilters()
    result = filter_source_ids(set(), filters)
    assert result is None


@patch("memory.api.MCP.servers.core.make_session")
def test_filter_source_ids_by_tags(mock_make_session):
    """Filters source items by tags."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_item = MagicMock()
    mock_item.id = 1
    mock_session.query.return_value.filter.return_value.all.return_value = [mock_item]

    filters = SearchFilters(tags=["important"])
    result = filter_source_ids(set(), filters)

    assert result == [1]


@patch("memory.api.MCP.servers.core.make_session")
def test_filter_source_ids_by_size(mock_make_session):
    """Filters source items by size."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_item = MagicMock()
    mock_item.id = 1
    mock_session.query.return_value.filter.return_value.all.return_value = [mock_item]

    filters = SearchFilters(max_size=1000)
    result = filter_source_ids(set(), filters)

    assert result == [1]


@patch("memory.api.MCP.servers.core.make_session")
def test_filter_source_ids_by_modalities(mock_make_session):
    """Filters source items by modalities."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_item = MagicMock()
    mock_item.id = 1
    mock_session.query.return_value.filter.return_value.filter.return_value.all.return_value = [
        mock_item
    ]

    filters = SearchFilters(tags=["test"])
    result = filter_source_ids({"mail", "blog"}, filters)

    assert result == [1]


# ====== get_item tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.get_current_user_access_filter", return_value=None)
@patch("memory.api.MCP.servers.core.make_session")
async def test_get_item_returns_full_details(mock_make_session, mock_access_filter):
    """Get source item returns full item details with content."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_item = MagicMock()
    mock_item.id = 123
    mock_item.modality = "blog"
    mock_item.title = "Test Article"
    mock_item.mime_type = "text/html"
    mock_item.filename = "article.html"
    mock_item.size = 5000
    mock_item.tags = ["tech", "python"]
    mock_item.inserted_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    mock_item.content = "Article content here"
    mock_item.as_payload.return_value = {"author": "Test Author"}

    mock_session.query.return_value.filter.return_value.first.return_value = mock_item

    result = await get_item.fn(id=123, include_content=True)

    assert result["id"] == 123
    assert result["modality"] == "blog"
    assert result["title"] == "Test Article"
    assert result["content"] == "Article content here"
    assert result["tags"] == ["tech", "python"]
    assert result["metadata"]["author"] == "Test Author"


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.get_current_user_access_filter", return_value=None)
@patch("memory.api.MCP.servers.core.make_session")
async def test_get_item_without_content(mock_make_session, mock_access_filter):
    """Get source item without content when requested."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_item = MagicMock()
    mock_item.id = 123
    mock_item.modality = "blog"
    mock_item.title = "Test"
    mock_item.mime_type = "text/html"
    mock_item.filename = "test.html"
    mock_item.size = 1000
    mock_item.tags = []
    mock_item.inserted_at = None
    mock_item.content = "Should not be included"
    mock_item.as_payload.return_value = {}

    mock_session.query.return_value.filter.return_value.first.return_value = mock_item

    result = await get_item.fn(id=123, include_content=False)

    assert "content" not in result
    assert result["id"] == 123


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.get_current_user_access_filter", return_value=None)
@patch("memory.api.MCP.servers.core.make_session")
async def test_get_item_not_found(mock_make_session, mock_access_filter):
    """Get source item raises error when not found."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session
    mock_session.query.return_value.filter.return_value.first.return_value = None

    with pytest.raises(ValueError, match="Item 999 not found"):
        await get_item.fn(id=999)


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.get_current_user_access_filter", return_value=None)
@patch("memory.api.MCP.servers.core.make_session")
async def test_get_item_handles_null_inserted_at(mock_make_session, mock_access_filter):
    """Get source item handles None inserted_at."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_item = MagicMock()
    mock_item.id = 123
    mock_item.modality = "blog"
    mock_item.title = "Test"
    mock_item.mime_type = "text/html"
    mock_item.filename = "test.html"
    mock_item.size = 1000
    mock_item.tags = []
    mock_item.inserted_at = None
    mock_item.as_payload.return_value = {}

    mock_session.query.return_value.filter.return_value.first.return_value = mock_item

    result = await get_item.fn(id=123, include_content=False)

    assert result["inserted_at"] is None


# ====== list_items tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.make_session")
async def test_list_items_returns_items(mock_make_session):
    """List items returns paginated results."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_item1 = MagicMock()
    mock_item1.id = 1
    mock_item1.modality = "blog"
    mock_item1.title = "Item 1"
    mock_item1.mime_type = "text/html"
    mock_item1.filename = "item1.html"
    mock_item1.size = 1000
    mock_item1.tags = ["tag1"]
    mock_item1.inserted_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    mock_item1.content = "Content 1"
    mock_item1.as_payload.return_value = {}

    # Setup query chain
    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.count.return_value = 1
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = [mock_item1]

    result = await list_items.fn()

    assert result["total"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == 1
    assert result["has_more"] is False


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.make_session")
async def test_list_items_with_modalities_filter(mock_make_session):
    """List items filters by modalities."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.count.return_value = 0
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_items.fn(modalities={"blog", "book"})

    # Verify filter was called (modality filter)
    assert query_mock.filter.call_count >= 1


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.make_session")
async def test_list_items_enforces_max_limit(mock_make_session):
    """List items enforces max limit of 200."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.count.return_value = 0
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_items.fn(limit=500)

    # Verify limit was capped at 200
    query_mock.limit.assert_called_once_with(200)


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.make_session")
async def test_list_items_with_pagination(mock_make_session):
    """List items supports offset pagination."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.count.return_value = 0
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_items.fn(limit=10, offset=20)

    query_mock.offset.assert_called_once_with(20)
    query_mock.limit.assert_called_once_with(10)


@pytest.mark.parametrize(
    "sort_by,sort_order",
    [
        ("inserted_at", "desc"),
        ("inserted_at", "asc"),
        ("size", "desc"),
        ("size", "asc"),
        ("id", "desc"),
        ("id", "asc"),
    ],
)
@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.make_session")
async def test_list_items_sort_options(mock_make_session, sort_by, sort_order):
    """List items supports different sort options."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.count.return_value = 0
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_items.fn(sort_by=sort_by, sort_order=sort_order)

    # Verify order_by was called
    query_mock.order_by.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.make_session")
async def test_list_items_defaults_invalid_sort_by(mock_make_session):
    """List items defaults to inserted_at for invalid sort_by."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.count.return_value = 0
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = []

    await list_items.fn(sort_by="invalid_field")

    # Should still work, defaulting to inserted_at
    query_mock.order_by.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.make_session")
async def test_list_items_has_more_flag(mock_make_session):
    """List items sets has_more flag correctly."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_item = MagicMock()
    mock_item.id = 1
    mock_item.modality = "blog"
    mock_item.title = "Test"
    mock_item.mime_type = "text/html"
    mock_item.filename = "test.html"
    mock_item.size = 1000
    mock_item.tags = []
    mock_item.inserted_at = None
    mock_item.content = None
    mock_item.as_payload.return_value = {}

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.count.return_value = 100  # Total 100 items
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = [mock_item] * 10  # Return 10 items

    result = await list_items.fn(limit=10, offset=0)

    assert result["has_more"] is True  # 0 + 10 < 100

    result = await list_items.fn(limit=10, offset=90)

    assert result["has_more"] is False  # 90 + 10 >= 100


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.make_session")
async def test_list_items_preview_truncation(mock_make_session):
    """List items truncates long content in preview."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_item = MagicMock()
    mock_item.id = 1
    mock_item.modality = "blog"
    mock_item.title = "Test"
    mock_item.mime_type = "text/html"
    mock_item.filename = "test.html"
    mock_item.size = 1000
    mock_item.tags = []
    mock_item.inserted_at = None
    mock_item.content = "A" * 300  # Long content
    mock_item.as_payload.return_value = {}

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.count.return_value = 1
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = [mock_item]

    result = await list_items.fn()

    # Preview should be truncated to 200 chars + "..."
    assert len(result["items"][0]["preview"]) == 203
    assert result["items"][0]["preview"].endswith("...")


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.make_session")
async def test_list_items_without_metadata(mock_make_session):
    """List items excludes metadata when requested."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_item = MagicMock()
    mock_item.id = 1
    mock_item.modality = "blog"
    mock_item.title = "Test"
    mock_item.mime_type = "text/html"
    mock_item.filename = "test.html"
    mock_item.size = 1000
    mock_item.tags = []
    mock_item.inserted_at = None
    mock_item.content = None
    mock_item.as_payload.return_value = {"should": "not appear"}

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.count.return_value = 1
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = [mock_item]

    result = await list_items.fn(include_metadata=False)

    assert result["items"][0]["metadata"] is None


# ====== count_items tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.make_session")
async def test_count_items_returns_total_and_by_modality(mock_make_session):
    """Count items returns total and breakdown by modality."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.count.return_value = 150

    # Mock the by_modality query
    by_modality_mock = query_mock.with_entities.return_value
    by_modality_mock.group_by.return_value = by_modality_mock
    by_modality_mock.all.return_value = [
        ("blog", 50),
        ("book", 30),
        ("mail", 70),
    ]

    result = await count_items.fn()

    assert result["total"] == 150
    assert result["by_modality"]["blog"] == 50
    assert result["by_modality"]["book"] == 30
    assert result["by_modality"]["mail"] == 70


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.make_session")
async def test_count_items_with_modalities_filter(mock_make_session):
    """Count items filters by modalities."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.count.return_value = 50

    by_modality_mock = query_mock.with_entities.return_value
    by_modality_mock.group_by.return_value = by_modality_mock
    by_modality_mock.all.return_value = [("blog", 50)]

    result = await count_items.fn(modalities={"blog"})

    assert result["total"] == 50
    assert result["by_modality"]["blog"] == 50
    # Verify filter was called
    assert query_mock.filter.call_count >= 1


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.make_session")
async def test_count_items_with_tags_filter(mock_make_session):
    """Count items filters by tags."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.count.return_value = 25

    by_modality_mock = query_mock.with_entities.return_value
    by_modality_mock.group_by.return_value = by_modality_mock
    by_modality_mock.all.return_value = [("blog", 25)]

    result = await count_items.fn(filters={"tags": ["programming"]})

    assert result["total"] == 25


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.make_session")
async def test_count_items_with_size_filters(mock_make_session):
    """Count items filters by size range."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.count.return_value = 10

    by_modality_mock = query_mock.with_entities.return_value
    by_modality_mock.group_by.return_value = by_modality_mock
    by_modality_mock.all.return_value = [("blog", 10)]

    result = await count_items.fn(filters={"min_size": 1000, "max_size": 5000})

    assert result["total"] == 10


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.make_session")
async def test_count_items_with_source_ids_filter(mock_make_session):
    """Count items filters by source IDs."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.count.return_value = 3

    by_modality_mock = query_mock.with_entities.return_value
    by_modality_mock.group_by.return_value = by_modality_mock
    by_modality_mock.all.return_value = [("blog", 3)]

    result = await count_items.fn(filters={"source_ids": [1, 2, 3]})

    assert result["total"] == 3
