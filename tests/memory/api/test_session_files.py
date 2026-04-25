"""Tests for cloud-claude session file transfer endpoints.

The OAuth-authenticated mint and list endpoints are exposed via MCP tools
in `memory.api.MCP.servers.claude` — covered by tests there. What lives in
`cloud_claude.py` is the streaming `/claude/transfer/{pull,push}` pair that
curl talks to with a presigned token. That's what this file exercises.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from memory.api.transfer_tokens import (
    TransferTokenPayload,
    mint_token,
)


SECRET = "session-files-test-secret"


@pytest.fixture(autouse=True)
def patch_secret():
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", SECRET):
        yield


SESSION_ID = "u1-e6-deadbeefcafe"


def make_token(action="read", session_id=SESSION_ID, path="/workspace/report.md", user_id=1, exp_offset=60):
    payload = TransferTokenPayload(
        user_id=user_id,
        session_id=session_id,
        path=path,
        action=action,
        exp=int(time.time()) + exp_offset,
    )
    return mint_token(payload)


# -- transfer/pull (signed token in URL) -------------------------------------


@pytest.fixture
def mock_orch_pull():
    """Mock httpx to return a fake tar stream from the orchestrator."""

    fake_chunks = [b"FAKE-TAR-CHUNK-1", b"FAKE-TAR-CHUNK-2"]

    async def aiter_bytes(self):
        for c in fake_chunks:
            yield c

    upstream_resp = MagicMock()
    upstream_resp.status_code = 200
    upstream_resp.headers = {"content-type": "application/x-tar"}
    upstream_resp.aiter_bytes = lambda: aiter_bytes(upstream_resp)
    upstream_resp.aclose = AsyncMock()
    upstream_resp.aread = AsyncMock(return_value=b"".join(fake_chunks))

    send_mock = AsyncMock(return_value=upstream_resp)
    aclose_mock = AsyncMock()

    with patch.object(httpx.AsyncClient, "send", send_mock), \
         patch.object(httpx.AsyncClient, "aclose", aclose_mock):
        yield send_mock, fake_chunks


def test_transfer_pull_streams_tar(client, mock_orch_pull):
    token = make_token(action="read")
    resp = client.get(f"/claude/transfer/pull?token={token}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-tar"
    assert resp.content == b"FAKE-TAR-CHUNK-1FAKE-TAR-CHUNK-2"


def test_transfer_pull_rejects_write_token(client):
    token = make_token(action="write")
    resp = client.get(f"/claude/transfer/pull?token={token}")
    assert resp.status_code == 403


def test_transfer_pull_rejects_expired_token(client):
    token = make_token(action="read", exp_offset=-1)
    resp = client.get(f"/claude/transfer/pull?token={token}")
    assert resp.status_code == 401


def test_transfer_pull_rejects_missing_token(client):
    resp = client.get("/claude/transfer/pull")
    assert resp.status_code == 422  # FastAPI required-query-param error


def test_transfer_pull_rejects_tampered_token(client):
    token = make_token(action="read")
    bad = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    resp = client.get(f"/claude/transfer/pull?token={bad}")
    assert resp.status_code == 401


def test_transfer_pull_rejects_session_not_owned_by_token_user(client):
    """Even if token verifies, the session's user prefix must match the payload's user_id."""
    token = make_token(
        action="read", session_id="u9999-e6-aaaa", user_id=1
    )
    resp = client.get(f"/claude/transfer/pull?token={token}")
    assert resp.status_code == 403


def test_transfer_pull_rejects_token_with_traversal_path(client):
    """Defense in depth: a token minted (somehow) with `..` in the path
    must still be rejected at verify time."""
    payload = TransferTokenPayload(
        user_id=1,
        session_id=SESSION_ID,
        path="/workspace/../../../etc/passwd",
        action="read",
        exp=int(time.time()) + 60,
    )
    bad_token = mint_token(payload)
    resp = client.get(f"/claude/transfer/pull?token={bad_token}")
    assert resp.status_code == 400


def test_transfer_pull_rejects_token_with_invalid_session_id(client):
    """Defense in depth: token minted with malformed session_id rejected."""
    payload = TransferTokenPayload(
        user_id=1,
        session_id="u1-not-a-real-session-format!",
        path="/workspace/x",
        action="read",
        exp=int(time.time()) + 60,
    )
    bad_token = mint_token(payload)
    resp = client.get(f"/claude/transfer/pull?token={bad_token}")
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "bad_char_path",
    [
        "/workspace/list?path=/etc",
        "/workspace/file#frag",
        "/workspace/file;param",
        "/workspace/%2e%2e/etc",
        "/workspace/file&other",
        "/workspace/foo\\bar",
        "/workspace/has space.md",
    ],
)
def test_transfer_pull_rejects_token_with_url_meaningful_chars(client, bad_char_path):
    """Defense in depth: a token minted (somehow) with URL-meaningful chars
    in the path must still be rejected at verify time, even if mint_transfer_url
    was bypassed by hitting mint_token directly."""
    payload = TransferTokenPayload(
        user_id=1,
        session_id=SESSION_ID,
        path=bad_char_path,
        action="read",
        exp=int(time.time()) + 60,
    )
    bad_token = mint_token(payload)
    resp = client.get(f"/claude/transfer/pull?token={bad_token}")
    assert resp.status_code == 400


def test_container_files_url_percent_encodes_non_ascii(client, mock_orch_pull):
    """The orchestrator URL must percent-encode the path segment so non-ASCII
    filenames (and any character that slips past validate_transfer_path)
    survive transit through httpx without being mis-interpreted as URL syntax.

    Slashes stay literal because they're path separators."""
    # Non-ASCII path passes validate_transfer_path (no forbidden chars)
    # and should be percent-encoded when forwarded to the orchestrator.
    token = make_token(action="read", path="/workspace/レポート.md")
    resp = client.get(f"/claude/transfer/pull?token={token}")
    assert resp.status_code == 200

    # Inspect the URL that got built for the orchestrator
    assert mock_orch_pull[0].await_count == 1
    forwarded_request = mock_orch_pull[0].call_args[0][0]
    forwarded_url = str(forwarded_request.url)
    # Path separators preserved
    assert f"/containers/{SESSION_ID}/files/workspace/" in forwarded_url
    # Non-ASCII chars are percent-encoded — none of the original UTF-8 bytes
    # appear raw in the URL string
    assert "レポート" not in forwarded_url
    # And the percent-encoded form is present (UTF-8 of レ is E3 83 AC)
    assert "%E3%83%AC" in forwarded_url


# -- transfer/push (signed token in header) ----------------------------------


@pytest.fixture
def mock_orch_push():
    upstream_resp = MagicMock()
    upstream_resp.status_code = 200
    upstream_resp.headers = {"content-type": "application/json"}
    upstream_resp.aread = AsyncMock(
        return_value=b'{"status": "extracted", "path": "/workspace"}'
    )
    upstream_resp.aclose = AsyncMock()

    send_mock = AsyncMock(return_value=upstream_resp)
    aclose_mock = AsyncMock()

    with patch.object(httpx.AsyncClient, "send", send_mock), \
         patch.object(httpx.AsyncClient, "aclose", aclose_mock):
        yield send_mock


def test_transfer_push_accepts_tar_with_write_token(client, mock_orch_push):
    token = make_token(action="write", path="/workspace")
    resp = client.put(
        "/claude/transfer/push",
        content=b"FAKE-TAR-BYTES",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-tar",
        },
    )
    assert resp.status_code == 200


def test_transfer_push_rejects_read_token(client):
    token = make_token(action="read")
    resp = client.put(
        "/claude/transfer/push",
        content=b"x",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_transfer_push_rejects_missing_auth_header(client):
    resp = client.put("/claude/transfer/push", content=b"x")
    assert resp.status_code == 401


def test_transfer_push_rejects_expired(client):
    token = make_token(action="write", exp_offset=-1)
    resp = client.put(
        "/claude/transfer/push",
        content=b"x",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_transfer_push_buffers_body_for_orchestrator(client, mock_orch_push):
    """Verify the PUT body actually arrives at the orchestrator with a
    Content-Length header (not chunked transfer encoding). The orchestrator's
    Unix-socket HTTP parser does not handle chunked encoding — pushes that
    came in chunked must be buffered before forwarding."""
    token = make_token(action="write", path="/workspace")
    body = b"FAKE-TAR-BYTES-" + b"x" * 100

    resp = client.put(
        "/claude/transfer/push",
        content=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-tar",
        },
    )
    assert resp.status_code == 200

    # Inspect what got forwarded to the (mocked) orchestrator
    assert mock_orch_push.await_count == 1
    forwarded_request = mock_orch_push.call_args[0][0]
    forwarded_headers = {k.lower(): v for k, v in forwarded_request.headers.items()}
    assert "content-length" in forwarded_headers
    assert int(forwarded_headers["content-length"]) == len(body)
    assert "transfer-encoding" not in forwarded_headers


def test_transfer_push_rejects_bearer_with_no_token(client):
    """`Authorization: Bearer` (no token) used to raise IndexError → 500."""
    resp = client.put(
        "/claude/transfer/push",
        content=b"x",
        headers={"Authorization": "Bearer"},
    )
    assert resp.status_code == 401


def test_transfer_push_rejects_bearer_with_only_whitespace_token(client):
    resp = client.put(
        "/claude/transfer/push",
        content=b"x",
        headers={"Authorization": "Bearer   "},
    )
    assert resp.status_code == 401
