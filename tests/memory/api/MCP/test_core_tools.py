"""Tests for MCP core tools: search, observe, fetch operations."""
# pyright: reportFunctionMemberAccess=false

import base64
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from memory.api.MCP.servers.core import (
    MAX_FETCH_FILE_BYTES,
    RawObservation,
    search,
    observe,
    search_observations,
    fetch,
    fetch_file,
    list_items,
    count_items,
    filter_observation_source_ids,
    filter_source_ids,
)
from memory.api.search.types import SearchFilters
from memory.common.db.models.source_item import SourceItem
from tests.conftest import mcp_auth_context


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


def _fake_mcp_user(user_id: int = 99):
    """Build a minimal user proxy that the observe gate accepts."""
    user = MagicMock()
    user.id = user_id
    return user


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.get_mcp_current_user", return_value=_fake_mcp_user())
@patch("memory.api.MCP.servers.core.celery_app")
@patch("memory.api.MCP.servers.core.settings")
async def test_observe_single_observation(mock_settings, mock_celery, _user):
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
@patch("memory.api.MCP.servers.core.get_mcp_current_user", return_value=_fake_mcp_user())
@patch("memory.api.MCP.servers.core.celery_app")
@patch("memory.api.MCP.servers.core.settings")
async def test_observe_multiple_observations(mock_settings, mock_celery, _user):
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
@patch("memory.api.MCP.servers.core.get_mcp_current_user", return_value=_fake_mcp_user(42))
@patch("memory.api.MCP.servers.core.celery_app")
@patch("memory.api.MCP.servers.core.settings")
async def test_observe_with_all_fields(mock_settings, mock_celery, _user):
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
    # creator_id is now threaded through so the row is owned and the
    # caller can read back their own observations (audit 56ad2afa).
    assert call_kwargs["creator_id"] == 42


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.get_mcp_current_user", return_value=_fake_mcp_user())
@patch("memory.api.MCP.servers.core.celery_app")
@patch("memory.api.MCP.servers.core.settings")
async def test_observe_truncates_long_content_in_task_ids(mock_settings, mock_celery, _user):
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
@patch("memory.api.MCP.servers.core.get_mcp_current_user", return_value=_fake_mcp_user())
@patch("memory.api.MCP.servers.core.celery_app")
@patch("memory.api.MCP.servers.core.settings")
async def test_observe_default_values(mock_settings, mock_celery, _user):
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
@patch("memory.api.MCP.servers.core.get_mcp_current_user", return_value=_fake_mcp_user())
@patch("memory.api.MCP.servers.core.celery_app")
@patch("memory.api.MCP.servers.core.settings")
async def test_observe_queue_name(mock_settings, mock_celery, _user):
    """Observation task sent to correct queue."""
    mock_settings.CELERY_QUEUE_PREFIX = "prod"
    mock_task = MagicMock()
    mock_task.id = "task-123"
    mock_celery.send_task.return_value = mock_task

    obs = RawObservation(subject="test", content="test")
    await observe.fn(observations=[obs])

    call_args = mock_celery.send_task.call_args
    assert call_args[1]["queue"] == "prod-notes"


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.get_mcp_current_user", return_value=None)
@patch("memory.api.MCP.servers.core.celery_app")
@patch("memory.api.MCP.servers.core.settings")
async def test_observe_rejects_unauthenticated(mock_settings, mock_celery, _user):
    """Without an authenticated principal we refuse to mint an unowned row.

    The visibility middleware already gates on SCOPE_OBSERVE_WRITE, so
    reaching this branch in production means an auth-context drift.
    The function must fail closed rather than create a row with no
    creator_id (which would be admin-only and silently dropped from
    the caller's read-back).
    """
    mock_settings.CELERY_QUEUE_PREFIX = "test"

    obs = RawObservation(subject="test", content="test content")
    result = await observe.fn(observations=[obs])

    assert result["status"] == "rejected"
    mock_celery.send_task.assert_not_called()


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


@pytest.fixture
def mock_fetch_file_auth():
    """Bypass the ownership check added in fetch_file so the existing
    transport-level tests still exercise the read paths.

    Auth + ownership are covered separately by integration-style tests."""
    with patch(
        "memory.api.MCP.servers.core.get_mcp_current_user",
        return_value=MagicMock(),
    ), patch(
        "memory.api.MCP.servers.core.get_accessible_source_item_by_filename",
        return_value=MagicMock(),
    ), patch("memory.api.MCP.servers.core.make_session") as mock_make_session:
        mock_make_session.return_value.__enter__.return_value = MagicMock()
        yield


@pytest.mark.parametrize(
    "mime_type",
    ["text/plain", "text/html", "text/markdown"],
)
@pytest.mark.usefixtures("mock_fetch_file_auth")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_text_type_detection(
    mock_settings, mock_paths, mock_extract, mime_type
):
    """Fetch file correctly returns text content for text file types."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = "text content"
    mock_paths.validate_path_within_directory.return_value = mock_path

    mock_extract.get_mime_type.return_value = mime_type
    mock_extract.is_text_file.return_value = True

    result = fetch_file.fn(filename="test.txt")

    assert len(result["content"]) == 1
    assert result["content"][0]["type"] == "text"
    assert result["content"][0]["mime_type"] == mime_type
    assert result["content"][0]["data"] == "text content"


@pytest.mark.parametrize(
    "mime_type",
    ["image/jpeg", "image/png"],
)
@pytest.mark.usefixtures("mock_fetch_file_auth")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_image_type_detection(
    mock_settings, mock_paths, mock_extract, mime_type
):
    """Image files come back as a single raw-bytes block typed 'image'."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_path.read_bytes.return_value = b"\x89PNG raw image bytes"
    mock_paths.validate_path_within_directory.return_value = mock_path

    mock_extract.get_mime_type.return_value = mime_type
    mock_extract.is_text_file.return_value = False

    result = fetch_file.fn(filename="test.png")

    assert len(result["content"]) == 1
    assert result["content"][0]["type"] == "image"
    assert result["content"][0]["mime_type"] == mime_type
    # The actual file bytes are returned, not the embedding-pipeline output.
    mock_extract.extract_data_chunks.assert_not_called()


@pytest.mark.usefixtures("mock_fetch_file_auth")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_text_content(mock_settings, mock_paths, mock_extract):
    """Fetch file returns text content as string without chunking."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = "Hello, world!"
    mock_paths.validate_path_within_directory.return_value = mock_path

    mock_extract.get_mime_type.return_value = "text/plain"
    mock_extract.is_text_file.return_value = True

    result = fetch_file.fn(filename="test.txt")

    assert result["content"][0]["data"] == "Hello, world!"
    assert result["content"][0]["type"] == "text"
    # Should NOT call extract_data_chunks for text files
    mock_extract.extract_data_chunks.assert_not_called()


@pytest.mark.usefixtures("mock_fetch_file_auth")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_image_content_base64(mock_settings, mock_paths, mock_extract):
    """Non-text fetches return the raw file bytes, base64-encoded verbatim."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    raw = b"\x89PNG\r\n\x1a\n raw file bytes"
    mock_path.read_bytes.return_value = raw
    mock_paths.validate_path_within_directory.return_value = mock_path

    mock_extract.get_mime_type.return_value = "image/png"
    mock_extract.is_text_file.return_value = False

    result = fetch_file.fn(filename="test.png")

    assert result["content"][0]["type"] == "image"
    content = result["content"][0]["data"]
    assert isinstance(content, str)
    # Round-trips to the exact file bytes (no re-encoding/extraction).
    assert base64.b64decode(content) == raw


@pytest.mark.usefixtures("mock_fetch_file_auth")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_binary_returns_raw_bytes(mock_settings, mock_paths, mock_extract):
    """A PDF (or any non-image binary) is returned verbatim as one 'blob' — not
    rasterized into page images or run through the extraction pipeline."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    raw = b"%PDF-1.7 actual pdf bytes \x00\x01\x02"
    mock_path.read_bytes.return_value = raw
    mock_paths.validate_path_within_directory.return_value = mock_path

    mock_extract.get_mime_type.return_value = "application/pdf"
    mock_extract.is_text_file.return_value = False

    result = fetch_file.fn(filename="test.pdf")

    assert len(result["content"]) == 1
    assert result["content"][0]["type"] == "blob"
    assert result["content"][0]["mime_type"] == "application/pdf"
    assert base64.b64decode(result["content"][0]["data"]) == raw
    mock_extract.extract_data_chunks.assert_not_called()


@pytest.mark.usefixtures("mock_fetch_file_auth")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_rejects_oversize(mock_settings, mock_paths, mock_extract):
    """Files past the inline cap fail loudly instead of overrunning the MCP
    transport (the bug that surfaced as 'session expired' on large PDFs)."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_path.read_bytes.return_value = b"x" * (MAX_FETCH_FILE_BYTES + 1)
    mock_paths.validate_path_within_directory.return_value = mock_path

    mock_extract.get_mime_type.return_value = "application/pdf"
    mock_extract.is_text_file.return_value = False

    with pytest.raises(ValueError, match="too large"):
        fetch_file.fn(filename="big.pdf")


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


@pytest.mark.usefixtures("mock_fetch_file_auth")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_strips_whitespace(mock_settings, mock_paths, mock_extract):
    """Fetch file strips whitespace from filename."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = "content"
    mock_paths.validate_path_within_directory.return_value = mock_path

    mock_extract.get_mime_type.return_value = "text/plain"
    mock_extract.is_text_file.return_value = True

    fetch_file.fn(filename="  test.txt  ")

    # verify path validation was called with stripped filename
    call_args = mock_paths.validate_path_within_directory.call_args[0]
    assert call_args[1] == "test.txt"


@pytest.mark.usefixtures("mock_fetch_file_auth")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.paths")
@patch("memory.api.MCP.servers.core.settings")
def test_fetch_file_unicode_decode_error(mock_settings, mock_paths, mock_extract):
    """Fetch file handles UnicodeDecodeError with replacement characters."""
    mock_settings.FILE_STORAGE_DIR = Path("/storage")
    mock_path = MagicMock(spec=Path)
    mock_path.exists.return_value = True
    mock_path.read_text.side_effect = [
        UnicodeDecodeError("utf-8", b"", 0, 1, "invalid"),
        "fallback content",
    ]
    mock_paths.validate_path_within_directory.return_value = mock_path

    mock_extract.get_mime_type.return_value = "text/plain"
    mock_extract.is_text_file.return_value = True

    result = fetch_file.fn(filename="test.txt")

    assert result["content"][0]["data"] == "fallback content"
    assert mock_path.read_text.call_count == 2
    mock_path.read_text.assert_called_with(errors="replace")


def test_fetch_file_round_trip_for_note(db_session, admin_user, admin_session):
    """Regression: Note files must be fetchable via the same FILE_STORAGE_DIR-
    relative path that listing returns.

    Earlier, ``Note.filename`` was stored relative to NOTES_STORAGE_DIR
    (no ``notes/`` prefix) while every other SourceItem stored it relative to
    FILE_STORAGE_DIR. The ownership check in ``fetch_file`` looked up by the
    FILE_STORAGE_DIR-relative path, so Note rows never matched and the fetch
    failed even though the file was on disk. This test pins the unified
    convention end-to-end.
    """
    from memory.common import settings
    from memory.common.db.models import Note

    note_path = settings.NOTES_STORAGE_DIR / "regression.md"
    note_path.write_text("hello from a note")

    db_filename = note_path.relative_to(settings.FILE_STORAGE_DIR.resolve()).as_posix()
    assert db_filename == "notes/regression.md"

    note = Note(
        modality="text",
        mime_type="text/markdown",
        subject="regression",
        content="hello from a note",
        filename=db_filename,
        sha256=db_filename.encode() + b"\x00content",
        size=len("hello from a note"),
        creator_id=admin_user.id,
    )
    db_session.add(note)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = fetch_file.fn(filename=f"/{db_filename}")

    assert result["content"][0]["data"] == "hello from a note"
    assert result["content"][0]["mime_type"] == "text/markdown"


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


# ====== fetch tests ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.get_current_user_access_filter", return_value=None)
@patch("memory.api.MCP.servers.core.make_session")
async def test_fetch_returns_full_details(mock_make_session, mock_access_filter):
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

    mock_session.query.return_value.options.return_value.filter.return_value.all.return_value = [mock_item]

    result = await fetch.fn(id=123, include_content=True)

    assert result["id"] == 123
    assert result["modality"] == "blog"
    assert result["title"] == "Test Article"
    assert result["content"] == "Article content here"
    assert result["tags"] == ["tech", "python"]
    assert result["metadata"]["author"] == "Test Author"


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.get_current_user_access_filter", return_value=None)
@patch("memory.api.MCP.servers.core.make_session")
async def test_fetch_without_content(mock_make_session, mock_access_filter):
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

    mock_session.query.return_value.options.return_value.filter.return_value.all.return_value = [mock_item]

    result = await fetch.fn(id=123, include_content=False)

    assert "content" not in result
    assert result["id"] == 123


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.get_current_user_access_filter", return_value=None)
@patch("memory.api.MCP.servers.core.make_session")
async def test_fetch_not_found(mock_make_session, mock_access_filter):
    """Get source item raises error when not found."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session
    mock_session.query.return_value.options.return_value.filter.return_value.all.return_value = []

    with pytest.raises(ValueError, match="Item 999 not found"):
        await fetch.fn(id=999)


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.get_current_user_access_filter", return_value=None)
@patch("memory.api.MCP.servers.core.make_session")
async def test_fetch_handles_null_inserted_at(mock_make_session, mock_access_filter):
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

    mock_session.query.return_value.options.return_value.filter.return_value.all.return_value = [mock_item]

    result = await fetch.fn(id=123, include_content=False)

    assert result["inserted_at"] is None


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.get_current_user_access_filter", return_value=None)
@patch("memory.api.MCP.servers.core.get_mcp_current_user")
@patch("memory.api.MCP.servers.core.make_session")
async def test_fetch_with_journal_entries(mock_make_session, mock_get_user, mock_access_filter):
    """Fetch source item with journal entries when requested."""
    mock_session = MagicMock()
    mock_make_session.return_value.__enter__.return_value = mock_session

    mock_user = MagicMock()
    mock_user.id = 1
    mock_get_user.return_value = mock_user

    mock_item = MagicMock()
    mock_item.id = 123
    mock_item.modality = "blog"
    mock_item.title = "Test"
    mock_item.mime_type = "text/html"
    mock_item.filename = "test.html"
    mock_item.size = 1000
    mock_item.tags = []
    mock_item.inserted_at = None
    mock_item.content = "Content"
    mock_item.as_payload.return_value = {}

    mock_entry1 = MagicMock()
    mock_entry1.as_payload.return_value = {"id": 1, "content": "Entry 1"}
    mock_entry2 = MagicMock()
    mock_entry2.as_payload.return_value = {"id": 2, "content": "Entry 2"}

    mock_entry1.target_id = 123
    mock_entry2.target_id = 123

    # Setup query chains
    item_query = MagicMock()
    item_query.options.return_value = item_query
    item_query.filter.return_value = item_query
    item_query.all.return_value = [mock_item]

    journal_query = MagicMock()
    journal_query.filter.return_value = journal_query
    journal_query.order_by.return_value = journal_query
    journal_query.all.return_value = [mock_entry1, mock_entry2]

    from memory.common.db.models import SourceItem
    from memory.common.db.models.journal import JournalEntry

    _query_map = {SourceItem: item_query, JournalEntry: journal_query}
    mock_session.query.side_effect = lambda model: _query_map.get(model, MagicMock())

    result = await fetch.fn(id=123, include_content=False, include_journal=True)

    assert "journal_entries" in result
    assert len(result["journal_entries"]) == 2
    assert result["journal_entries"][0]["content"] == "Entry 1"


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.get_current_user_access_filter", return_value=None)
@patch("memory.api.MCP.servers.core.make_session")
async def test_fetch_without_journal_entries(mock_make_session, mock_access_filter):
    """Fetch source item without journal entries by default."""
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

    mock_session.query.return_value.options.return_value.filter.return_value.all.return_value = [mock_item]

    result = await fetch.fn(id=123, include_content=False)

    assert "journal_entries" not in result


# ====== bulk fetch tests ======
#
# These tests use the real database (db_session fixture) and MCP auth context
# rather than mocking internals, so they exercise the full fetch code path.


@pytest.mark.asyncio
async def test_fetch_rejects_both_id_and_ids():
    """Fetch raises when both id and ids are provided."""
    with pytest.raises(ValueError, match="Cannot provide both"):
        await fetch.fn(id=1, ids=[2, 3])


@pytest.mark.asyncio
async def test_fetch_rejects_neither_id_nor_ids():
    """Fetch raises when neither id nor ids are provided."""
    with pytest.raises(ValueError, match="Must provide either"):
        await fetch.fn()


@pytest.mark.asyncio
async def test_fetch_bulk_max_200_limit():
    """Fetch raises when ids list exceeds 200."""
    with pytest.raises(ValueError, match="Cannot fetch more than 200"):
        await fetch.fn(ids=list(range(201)))


def make_source_item(db_session, n: int, **overrides) -> "SourceItem":
    """Create and persist a SourceItem with embed_status=STORED for fetch tests."""
    from tests.conftest import unique_sha256

    defaults = dict(
        modality="blog",
        sha256=unique_sha256(f"bulk-fetch-{n}"),
        content=f"Content {n}",
        mime_type="text/html",
        tags=[],
        embed_status="STORED",
    )
    defaults.update(overrides)
    item = SourceItem(**defaults)
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


@pytest.mark.asyncio
async def test_fetch_bulk_returns_list(db_session, admin_session):
    """Bulk fetch returns a list of dicts, not a single dict."""
    item_a = make_source_item(db_session, 1)
    item_b = make_source_item(db_session, 2)

    with mcp_auth_context(admin_session.id):
        result = await fetch.fn(ids=[item_a.id, item_b.id])

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["id"] == item_a.id
    assert result[1]["id"] == item_b.id


@pytest.mark.asyncio
async def test_fetch_bulk_preserves_order(db_session, admin_session):
    """Bulk fetch returns items in the same order as the input ids."""
    items = [make_source_item(db_session, n) for n in range(3)]

    # Request in reverse order
    requested_ids = [items[2].id, items[0].id, items[1].id]
    with mcp_auth_context(admin_session.id):
        result = await fetch.fn(ids=requested_ids)

    assert [r["id"] for r in result] == requested_ids


@pytest.mark.asyncio
async def test_fetch_bulk_skips_missing_items(db_session, admin_session):
    """Bulk fetch silently skips items not found in the database."""
    item = make_source_item(db_session, 1)
    missing_id = item.id + 9999

    with mcp_auth_context(admin_session.id):
        result = await fetch.fn(ids=[item.id, missing_id])

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["id"] == item.id


@pytest.mark.asyncio
async def test_fetch_bulk_skips_inaccessible_items(db_session, user_session, admin_user):
    """Bulk fetch filters out items the user cannot access.

    Items without a project_id are only visible to superadmins, so a regular
    user should not see them.  Items assigned to the user's project are visible.
    """
    from memory.common.db.models.sources import (
        Person, Project, Team, team_members, project_teams,
    )

    # Create a project and team the regular user belongs to
    team = Team(name="Test Team", slug="test-team-acl-bulk")
    db_session.add(team)
    db_session.flush()

    project = Project(title="Test Project", state="open")
    db_session.add(project)
    db_session.flush()

    # Assign team to project
    db_session.execute(
        project_teams.insert().values(project_id=project.id, team_id=team.id)
    )

    # Link regular user to the team via Person
    person = Person(
        identifier="bulk-fetch-test-person",
        display_name="Regular Person",
    )
    db_session.add(person)
    db_session.flush()

    db_session.execute(
        team_members.insert().values(
            team_id=team.id, person_id=person.id, role="member",
        )
    )

    # Link the regular user (from user_session) to the person
    person.user_id = user_session.user_id
    db_session.flush()

    # Accessible item: belongs to the project
    accessible_item = make_source_item(db_session, 1, project_id=project.id)
    # Inaccessible item: no project_id (superadmin-only)
    inaccessible_item = make_source_item(db_session, 2, project_id=None)
    db_session.commit()

    with mcp_auth_context(user_session.id):
        result = await fetch.fn(ids=[accessible_item.id, inaccessible_item.id])

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["id"] == accessible_item.id


@pytest.mark.asyncio
async def test_fetch_bulk_deduplicates_ids(db_session, admin_session):
    """Bulk fetch deduplicates repeated IDs in the input."""
    item = make_source_item(db_session, 1)

    with mcp_auth_context(admin_session.id):
        result = await fetch.fn(ids=[item.id, item.id, item.id])

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["id"] == item.id


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
async def test_list_items_uses_preview_text_property(mock_make_session):
    """List items passes through the model's preview_text property."""
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
    mock_item.preview_text = "A" * 300 + "..."
    mock_item.as_payload.return_value = {}

    query_mock = mock_session.query.return_value
    query_mock.filter.return_value = query_mock
    query_mock.count.return_value = 1
    query_mock.order_by.return_value = query_mock
    query_mock.offset.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.all.return_value = [mock_item]

    result = await list_items.fn()

    # list_items should use the preview_text property from the model
    assert result["items"][0]["preview"] == mock_item.preview_text


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


# ====== audit-logging on search and fetch ======


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.log_search_access")
@patch("memory.api.MCP.servers.core.get_mcp_current_user")
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.filter_source_ids")
async def test_search_logs_access(
    mock_filter_ids,
    mock_extract,
    mock_search_base,
    mock_get_user,
    mock_log_search,
):
    """Regression: ``core.search`` must call log_search_access with the
    user id, query string, and result count. Previously the audit-log
    helper existed but was never invoked, falsifying the "all access is
    logged" claim in access_control.py and AccessLog's docstring.
    """
    mock_extract.extract_text.return_value = "x"
    mock_filter_ids.return_value = None
    fake_results = [MagicMock(), MagicMock(), MagicMock()]
    for r in fake_results:
        r.model_dump.return_value = {}
    mock_search_base.return_value = fake_results

    mock_user = MagicMock()
    mock_user.id = 7
    mock_get_user.return_value = mock_user

    await search.fn(query="hello world")

    mock_log_search.assert_called_once_with(7, "hello world", 3)


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.log_search_access")
@patch("memory.api.MCP.servers.core.get_mcp_current_user")
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.filter_source_ids")
async def test_search_logging_failure_does_not_fail_request(
    mock_filter_ids,
    mock_extract,
    mock_search_base,
    mock_get_user,
    mock_log_search,
):
    """A logging failure must not surface as a search error — the audit
    log is best-effort, the user's search must still return.
    """
    mock_extract.extract_text.return_value = "x"
    mock_filter_ids.return_value = None
    mock_search_base.return_value = []
    mock_user = MagicMock()
    mock_user.id = 7
    mock_get_user.return_value = mock_user
    mock_log_search.side_effect = RuntimeError("DB blew up")

    # Must not raise.
    results = await search.fn(query="hello")
    assert results == []
    mock_log_search.assert_called_once()


@pytest.mark.asyncio
@patch("memory.api.MCP.servers.core.log_search_access")
@patch("memory.api.MCP.servers.core.get_mcp_current_user")
@patch("memory.api.MCP.servers.core.search_base")
@patch("memory.api.MCP.servers.core.extract")
@patch("memory.api.MCP.servers.core.filter_source_ids")
async def test_search_skips_logging_when_no_user(
    mock_filter_ids,
    mock_extract,
    mock_search_base,
    mock_get_user,
    mock_log_search,
):
    """No user id (anonymous / disabled-auth dev mode) → no log row attempt."""
    mock_extract.extract_text.return_value = "x"
    mock_filter_ids.return_value = None
    mock_search_base.return_value = []
    mock_get_user.return_value = None

    await search.fn(query="anonymous")
    mock_log_search.assert_not_called()


# ====== apply_item_filters + list/count parity (real DB) ======


def make_mail(
    sha: bytes, *, sender, recipients, subject, sent_at, content="body", folder="INBOX"
):
    """Build a STORED MailMessage row for enumeration tests."""
    from memory.common.db.models import MailMessage

    return MailMessage(
        sha256=sha,
        content=content,
        size=len(content),
        mime_type="message/rfc822",
        modality="mail",
        embed_status="STORED",
        tags=[],
        sender=sender,
        recipients=recipients,
        subject=subject,
        sent_at=sent_at,
        folder=folder,
    )


def seed_mail(db_session):
    """Two mail messages differing on sender/recipients/subject/sent_at."""
    a = make_mail(
        b"mail-a",
        sender="alice@example.com",
        recipients=["bob@example.com"],
        subject="Quarterly report",
        sent_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    b = make_mail(
        b"mail-b",
        sender="carol@example.com",
        recipients=["dave@example.com"],
        subject="Lunch plans",
        sent_at=datetime(2023, 6, 1, tzinfo=timezone.utc),
    )
    db_session.add_all([a, b])
    db_session.commit()
    return a, b


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"sender": "alice@example.com"},
        {"recipients": ["bob@example.com"]},
        {"subject": "report"},
        {"min_sent_at": "2022-01-01T00:00:00+00:00"},
    ],
)
async def test_list_and_count_agree_on_total(db_session, admin_session, filters):
    """list_items total and count_items total never diverge for the same filters."""
    seed_mail(db_session)
    with mcp_auth_context(admin_session.id):
        listed = await list_items.fn(modalities={"mail"}, filters=filters)
        counted = await count_items.fn(modalities={"mail"}, filters=filters)
    assert listed["total"] == counted["total"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters, expected_subjects",
    [
        ({"sender": "alice@example.com"}, {"Quarterly report"}),
        ({"recipients": ["bob@example.com"]}, {"Quarterly report"}),
        ({"recipients": ["dave@example.com"]}, {"Lunch plans"}),
        ({"subject": "lunch"}, {"Lunch plans"}),
        ({"min_sent_at": "2022-01-01T00:00:00+00:00"}, {"Lunch plans"}),
        ({"max_sent_at": "2022-01-01T00:00:00+00:00"}, {"Quarterly report"}),
    ],
)
async def test_mail_filters_discriminate_on_both_tools(
    db_session, admin_session, filters, expected_subjects
):
    """recipients/subject/sent_at actually filter, identically, on list and count."""
    seed_mail(db_session)
    with mcp_auth_context(admin_session.id):
        listed = await list_items.fn(modalities={"mail"}, filters=filters)
        counted = await count_items.fn(modalities={"mail"}, filters=filters)

    got_subjects = {item["metadata"]["subject"] for item in listed["items"]}
    assert got_subjects == expected_subjects
    assert listed["total"] == len(expected_subjects)
    assert counted["total"] == len(expected_subjects)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [list_items, count_items])
async def test_unsupported_filter_raises_value_error(db_session, admin_session, tool):
    """A filter key only meaningful for observation search is rejected, not ignored."""
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="Unsupported filter"):
            await tool.fn(filters={"observation_types": ["belief"]})


@pytest.mark.asyncio
async def test_recipients_substring_unifies_display_name_variants(
    db_session, admin_session
):
    """A bare-address recipients filter finds the same mailbox whether it was
    stored bare or with a display name — executes array_to_string on Postgres."""
    bare = make_mail(
        b"rcpt-bare", sender="s@x.com", recipients=["github@ahiru.pl"],
        subject="bare", sent_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
    )
    named = make_mail(
        b"rcpt-named", sender="s@x.com", recipients=["mruwnik <github@ahiru.pl>"],
        subject="named", sent_at=datetime(2021, 1, 2, tzinfo=timezone.utc),
    )
    other = make_mail(
        b"rcpt-other", sender="s@x.com", recipients=["someone@else.com"],
        subject="other", sent_at=datetime(2021, 1, 3, tzinfo=timezone.utc),
    )
    db_session.add_all([bare, named, other])
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await list_items.fn(
            modalities={"mail"}, filters={"recipients": ["github@ahiru.pl"]}
        )

    got = {item["metadata"]["subject"] for item in result["items"]}
    assert got == {"bare", "named"}


@pytest.mark.asyncio
async def test_sender_substring_matches_mime_encoded_display_name(
    db_session, admin_session
):
    """A bare-address sender filter matches even when the display name is
    MIME-encoded (the address itself is plaintext in the header)."""
    match = make_mail(
        b"snd-mime",
        sender="=?UTF-8?B?UmFkZWsgQnVkennFhHNraQ==?= <notifications@github.com>",
        recipients=["r@x.com"], subject="match",
        sent_at=datetime(2021, 2, 1, tzinfo=timezone.utc),
    )
    nomatch = make_mail(
        b"snd-other", sender="Someone <other@example.com>",
        recipients=["r@x.com"], subject="nomatch",
        sent_at=datetime(2021, 2, 2, tzinfo=timezone.utc),
    )
    db_session.add_all([match, nomatch])
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await list_items.fn(
            modalities={"mail"}, filters={"sender": "notifications@github.com"}
        )

    got = {item["metadata"]["subject"] for item in result["items"]}
    assert got == {"match"}


@pytest.mark.asyncio
async def test_folder_filter_exact_match(db_session, admin_session):
    """The mail folder filter matches the stored folder value exactly."""
    inbox = make_mail(
        b"fld-inbox", sender="s@x.com", recipients=["r@x.com"], subject="inbox",
        sent_at=datetime(2021, 3, 1, tzinfo=timezone.utc), folder="INBOX",
    )
    sent = make_mail(
        b"fld-sent", sender="s@x.com", recipients=["r@x.com"], subject="sent",
        sent_at=datetime(2021, 3, 2, tzinfo=timezone.utc), folder="[Gmail]/Sent Mail",
    )
    db_session.add_all([inbox, sent])
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await list_items.fn(
            modalities={"mail"}, filters={"folder": "INBOX"}
        )

    got = {item["metadata"]["subject"] for item in result["items"]}
    assert got == {"inbox"}


@pytest.mark.asyncio
async def test_account_filter_groups_aliases_and_both_directions(
    db_session, admin_user, admin_session
):
    """The account filter returns everything the account ingested — incoming
    under any alias AND sent — grouped by email_account_id, regardless of the
    header address, and resolves the account address case-insensitively."""
    from memory.common.db.models import EmailAccount

    acct = EmailAccount(
        user_id=admin_user.id, name="Ahiru",
        email_address="me@ahiru.pl", account_type="imap",
    )
    other = EmailAccount(
        user_id=admin_user.id, name="Other",
        email_address="other@x.com", account_type="imap",
    )
    db_session.add_all([acct, other])
    db_session.flush()

    # Incoming addressed to a DIFFERENT alias than the account's canonical name.
    incoming = make_mail(
        b"acc-in", sender="someone@ext.com", recipients=["d.oconnell@ahiru.pl"],
        subject="incoming-alias", sent_at=datetime(2022, 1, 1, tzinfo=timezone.utc),
    )
    sent = make_mail(
        b"acc-sent", sender="d.oconnell@ahiru.pl", recipients=["dest@ext.com"],
        subject="sent", sent_at=datetime(2022, 1, 2, tzinfo=timezone.utc),
    )
    foreign = make_mail(
        b"acc-foreign", sender="x@x.com", recipients=["y@y.com"],
        subject="foreign", sent_at=datetime(2022, 1, 3, tzinfo=timezone.utc),
    )
    incoming.email_account_id = acct.id
    sent.email_account_id = acct.id
    foreign.email_account_id = other.id
    db_session.add_all([incoming, sent, foreign])
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        # Mixed-case address still resolves the account.
        result = await list_items.fn(
            modalities={"mail"}, filters={"account": "ME@Ahiru.PL"}
        )

    got = {item["metadata"]["subject"] for item in result["items"]}
    # Both directions, the alias-addressed incoming, and not the foreign account.
    assert got == {"incoming-alias", "sent"}


@pytest.mark.asyncio
async def test_account_qdrant_filter_resolves_to_email_account_id(
    db_session, admin_user
):
    """The Qdrant arm resolves the address to email_account_id(s) and matches the
    indexed payload key (case-insensitively)."""
    from memory.common.db.models import EmailAccount
    from memory.api.search.embeddings import build_qdrant_special_filters

    acct = EmailAccount(
        user_id=admin_user.id, name="Ahiru",
        email_address="me@ahiru.pl", account_type="imap",
    )
    db_session.add(acct)
    db_session.commit()

    result = build_qdrant_special_filters({"account": "ME@AHIRU.PL"})
    assert result == [{"key": "email_account_id", "match": {"any": [acct.id]}}]

    # Unknown address -> empty id set -> match nothing (not "match all").
    assert build_qdrant_special_filters({"account": "nobody@x.test"}) == [
        {"key": "email_account_id", "match": {"any": []}}
    ]


@pytest.mark.asyncio
async def test_account_filter_unknown_address_returns_nothing(
    db_session, admin_session
):
    """An address matching no account resolves to an empty id set -> no mail."""
    m = make_mail(
        b"acc-none", sender="a@b.com", recipients=["c@d.com"], subject="x",
        sent_at=datetime(2022, 4, 1, tzinfo=timezone.utc),
    )
    db_session.add(m)
    db_session.commit()

    with mcp_auth_context(admin_session.id):
        result = await list_items.fn(
            modalities={"mail"}, filters={"account": "nobody@nowhere.test"}
        )

    assert result["items"] == []
