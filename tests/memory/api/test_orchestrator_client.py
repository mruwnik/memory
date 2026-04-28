"""Tests for OrchestratorClient methods that don't get exercised end-to-end
via API endpoints (e.g. list_dir, which is now MCP-only)."""

from unittest.mock import patch

import pytest

from memory.api.orchestrator_client import OrchestratorClient, OrchestratorError


SESSION_ID = "u1-e6-deadbeefcafe"


def _capture_request(retval=(200, {"path": "/workspace", "entries": [], "truncated": False})):
    """Patch _request and capture what URL it gets called with."""
    captured: dict = {}

    async def fake_request(self, method, url, body=None):
        captured["method"] = method
        captured["url"] = url
        captured["body"] = body
        return retval

    return captured, patch.object(OrchestratorClient, "_request", new=fake_request)


@pytest.mark.asyncio
async def test_list_dir_uses_query_param_for_path():
    """The orchestrator's list endpoint takes path as a query param
    (`/files/list?path=...`), not embedded in the URL path. Regression
    guard: a previous version embedded the path, which broke for absolute
    paths with slashes."""
    captured, p = _capture_request()
    with p:
        await OrchestratorClient().list_dir(SESSION_ID, "/workspace")
    assert captured["method"] == "GET"
    assert captured["url"].startswith(f"/containers/{SESSION_ID}/files/list?")
    assert "path=%2Fworkspace" in captured["url"]


@pytest.mark.asyncio
async def test_list_dir_url_encodes_non_ascii_path():
    """Paths with non-ASCII characters must be percent-encoded as UTF-8
    bytes in the orchestrator URL. A future refactor that swapped urlencode
    for naive interpolation would silently regress this for any path with
    non-ASCII characters (e.g. Japanese filenames)."""
    captured, p = _capture_request()
    with p:
        await OrchestratorClient().list_dir(SESSION_ID, "/workspace/レポート.md")
    url = captured["url"]
    # UTF-8 bytes for "レポート" should be percent-encoded:
    #   レ -> E3 83 AC, ポ -> E3 83 9D, ー -> E3 83 BC, ト -> E3 83 88
    assert "%E3%83%AC%E3%83%9D%E3%83%BC%E3%83%88" in url
    assert "レポート" not in url


@pytest.mark.asyncio
async def test_list_dir_passes_recursive_flag():
    captured, p = _capture_request()
    with p:
        await OrchestratorClient().list_dir(SESSION_ID, "/workspace", recursive=True)
    assert "recursive=true" in captured["url"]


@pytest.mark.asyncio
async def test_list_dir_omits_recursive_by_default():
    captured, p = _capture_request()
    with p:
        await OrchestratorClient().list_dir(SESSION_ID, "/workspace")
    assert "recursive" not in captured["url"]


@pytest.mark.asyncio
async def test_list_dir_passes_max_entries():
    captured, p = _capture_request()
    with p:
        await OrchestratorClient().list_dir(
            SESSION_ID, "/workspace", max_entries=42
        )
    assert "max_entries=42" in captured["url"]


@pytest.mark.asyncio
async def test_list_dir_returns_manifest():
    manifest = {
        "path": "/workspace",
        "entries": [
            {"name": "x.md", "type": "file", "size": 10, "mtime": "..."}
        ],
        "truncated": False,
    }
    _, p = _capture_request(retval=(200, manifest))
    with p:
        result = await OrchestratorClient().list_dir(SESSION_ID, "/workspace")
    assert result == manifest


@pytest.mark.parametrize("orch_status", [400, 404, 409, 504])
@pytest.mark.asyncio
async def test_list_dir_raises_with_status_code_on_4xx_5xx(orch_status):
    """OrchestratorError carries the upstream status so the API layer can
    forward client-actionable errors (404 missing, 409 not running, 400 bad
    path, 504 timeout) instead of squashing everything to a generic 503."""
    _, p = _capture_request(retval=(orch_status, {"detail": "orch said no"}))
    with p:
        with pytest.raises(OrchestratorError) as exc_info:
            await OrchestratorClient().list_dir(SESSION_ID, "/missing")
    assert exc_info.value.status_code == orch_status
    assert "orch said no" in str(exc_info.value)


# -- stats endpoints -------------------------------------------------------


SAMPLE_SNAPSHOT = {
    "ts": "2026-04-27T14:18:05.813523+00:00",
    "global": {
        "running": 1, "max": 12,
        "memory_mb": {"used": 600, "allocated": 6144, "max": 49152},
        "cpus": {"used": 0.04, "allocated": 2.0, "max": 8},
    },
    "containers": [
        {
            "id": SESSION_ID,
            "status": "running",
            "allocated": {"memory_mb": 6144, "cpus": 2.0},
            "used": {"memory_mb": 598, "memory_pct": 9.74, "cpu_pct": 4.05},
        }
    ],
}


@pytest.mark.asyncio
async def test_stats_round_trips_snapshot():
    _, p = _capture_request(retval=(200, SAMPLE_SNAPSHOT))
    with p:
        result = await OrchestratorClient().stats()
    assert result == SAMPLE_SNAPSHOT


@pytest.mark.asyncio
async def test_stats_calls_correct_url():
    captured, p = _capture_request(retval=(200, SAMPLE_SNAPSHOT))
    with p:
        await OrchestratorClient().stats()
    assert captured["method"] == "GET"
    assert captured["url"] == "/stats"


@pytest.mark.asyncio
async def test_container_stats_returns_dict():
    payload = SAMPLE_SNAPSHOT["containers"][0]
    captured, p = _capture_request(retval=(200, payload))
    with p:
        result = await OrchestratorClient().container_stats(SESSION_ID)
    assert result == payload
    assert captured["url"] == f"/containers/{SESSION_ID}/stats"


@pytest.mark.asyncio
async def test_container_stats_returns_none_on_404():
    """404 from orchestrator means session isn't currently managed —
    surfaced as None rather than raising, mirroring get_container."""
    _, p = _capture_request(retval=(404, {"detail": "not found"}))
    with p:
        result = await OrchestratorClient().container_stats("u1-x-deadbeef")
    assert result is None


@pytest.mark.asyncio
async def test_stats_history_passes_query_params():
    captured, p = _capture_request(
        retval=(200, {"points": [], "count": 0, "truncated": False})
    )
    with p:
        await OrchestratorClient().stats_history(
            session_id=SESSION_ID,
            since="2026-04-27T13:00:00Z",
            max_points=200,
        )
    url = captured["url"]
    assert url.startswith("/stats/history?")
    assert f"session_id={SESSION_ID}" in url
    assert "since=2026-04-27T13%3A00%3A00Z" in url
    assert "max=200" in url


@pytest.mark.asyncio
async def test_stats_history_omits_optional_params():
    captured, p = _capture_request(
        retval=(200, {"points": [], "count": 0, "truncated": False})
    )
    with p:
        await OrchestratorClient().stats_history()
    url = captured["url"]
    assert "session_id=" not in url
    assert "since=" not in url
    assert "max=1000" in url


@pytest.mark.asyncio
async def test_stats_history_400_raises_with_status():
    """Bad `since` or out-of-range `max` round-trip the 400 with detail."""
    _, p = _capture_request(retval=(400, {"detail": "max out of range"}))
    with p:
        with pytest.raises(OrchestratorError) as exc_info:
            await OrchestratorClient().stats_history(max_points=99999)
    assert exc_info.value.status_code == 400
    assert "max out of range" in str(exc_info.value)
