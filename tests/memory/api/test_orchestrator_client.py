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
