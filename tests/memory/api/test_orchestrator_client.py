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


# =============================================================================
# tail_log_text seek-from-end (the audit fix)
# =============================================================================


def test_tail_log_text_short_file(tmp_path):
    """Files smaller than one chunk read everything and tail correctly."""
    from memory.api.orchestrator_client import tail_log_text

    log = tmp_path / "short.log"
    log.write_text("a\nb\nc\nd\ne\n")

    assert tail_log_text(log, tail=2) == "d\ne"
    assert tail_log_text(log, tail=10) == "a\nb\nc\nd\ne"


def test_tail_log_text_seeks_for_long_file(tmp_path, monkeypatch):
    """A file larger than _TAIL_READ_CHUNK must use the seek-loop, not slurp."""
    import memory.api.orchestrator_client as oc

    # Force a tiny chunk so the seek loop actually runs even on small fixtures.
    monkeypatch.setattr(oc, "_TAIL_READ_CHUNK", 16)

    log = tmp_path / "long.log"
    # 200 lines of "lineN\n" — well over a 16-byte chunk.
    log.write_text("".join(f"line{i:03d}\n" for i in range(200)))

    assert oc.tail_log_text(log, tail=3) == "line197\nline198\nline199"
    assert oc.tail_log_text(log, tail=1) == "line199"


def test_tail_log_text_caps_response_size(tmp_path, monkeypatch):
    """Even a huge tail request is bounded by LOG_TAIL_MAX_BYTES."""
    import memory.api.orchestrator_client as oc

    # 200 bytes of cap is enough to clearly bound the result.
    monkeypatch.setattr(oc, "LOG_TAIL_MAX_BYTES", 200)
    monkeypatch.setattr(oc, "_TAIL_READ_CHUNK", 64)

    log = tmp_path / "big.log"
    # 1000 bytes of content — caller asks for everything.
    log.write_text("x" * 1000 + "\n")

    result = oc.tail_log_text(log, tail=10_000_000)
    # Must be at most LOG_TAIL_MAX_BYTES.
    assert len(result.encode()) <= 200


def test_tail_log_text_with_tail_zero_returns_whole_capped_file(
    tmp_path, monkeypatch
):
    """tail=0 means "give me the file" but still subject to the size cap."""
    import memory.api.orchestrator_client as oc

    monkeypatch.setattr(oc, "LOG_TAIL_MAX_BYTES", 50)

    log = tmp_path / "all.log"
    log.write_text("\n".join(f"line{i}" for i in range(20)))

    result = oc.tail_log_text(log, tail=0)
    # Must be at most LOG_TAIL_MAX_BYTES — the cap is on bytes, not lines.
    assert len(result.encode()) <= 50


def test_tail_log_text_handles_no_trailing_newline(tmp_path):
    """A log with no trailing newline must still tail correctly."""
    from memory.api.orchestrator_client import tail_log_text

    log = tmp_path / "no_newline.log"
    log.write_text("a\nb\nc")  # no \n at end

    assert tail_log_text(log, tail=2) == "b\nc"


def test_tail_log_text_handles_invalid_utf8(tmp_path):
    """Binary garbage in the log shouldn't crash the tail."""
    from memory.api.orchestrator_client import tail_log_text

    log = tmp_path / "binary.log"
    log.write_bytes(b"line1\n\xff\xfe garbage\nline3\n")

    result = tail_log_text(log, tail=3)
    # \xff\xfe gets replaced with U+FFFD; structure is preserved.
    assert "line1" in result
    assert "line3" in result
