"""Tests for MCP claude (cloud-claude session files) server."""
# pyright: reportFunctionMemberAccess=false

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.api.orchestrator_client import SessionInfo as OrchSessionInfo
from memory.api.transfer_tokens import verify_token
from tests.conftest import mcp_auth_context


SECRET = "claude-mcp-test-secret"


@pytest.fixture(autouse=True)
def patch_secret():
    with patch("memory.common.settings.TRANSFER_TOKEN_SECRET", SECRET):
        yield


@pytest.fixture(autouse=True)
def patch_server_url():
    with patch(
        "memory.common.settings.SERVER_URL", "https://memory.test.example.com"
    ):
        yield


def get_fn(tool):
    return getattr(tool, "fn", tool)


# -- session_list -----------------------------------------------------------


@pytest.mark.asyncio
async def test_session_list_returns_only_user_owned(db_session, admin_user, admin_session):
    from memory.api.MCP.servers.claude import session_list

    fake_orch_sessions = [
        OrchSessionInfo(
            session_id=f"u{admin_user.id}-e6-aaaa1111",
            container_name="claude-aaaa1111",
            status="running",
        ),
        OrchSessionInfo(
            session_id="u9999-e6-bbbb2222",  # different user
            container_name="claude-bbbb2222",
            status="running",
        ),
        OrchSessionInfo(
            session_id=f"u{admin_user.id}-s3-cccc3333",
            container_name="claude-cccc3333",
            status="exited",
        ),
    ]

    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.list_containers",
        new=AsyncMock(return_value=fake_orch_sessions),
    ):
        with mcp_auth_context(admin_session.id):
            result = await get_fn(session_list)()

    ids = [s["session_id"] for s in result]
    assert f"u{admin_user.id}-e6-aaaa1111" in ids
    assert f"u{admin_user.id}-s3-cccc3333" in ids
    assert "u9999-e6-bbbb2222" not in ids
    statuses = {s["session_id"]: s["status"] for s in result}
    assert statuses[f"u{admin_user.id}-e6-aaaa1111"] == "running"
    assert statuses[f"u{admin_user.id}-s3-cccc3333"] == "exited"


@pytest.mark.asyncio
async def test_session_list_unauthenticated_returns_empty():
    from memory.api.MCP.servers.claude import session_list

    # No mcp_auth_context — no auth
    result = await get_fn(session_list)()
    assert result == []


# -- session_pull_url -------------------------------------------------------


@pytest.mark.asyncio
async def test_session_pull_url_mints_read_token(db_session, admin_user, admin_session):
    from memory.api.MCP.servers.claude import session_pull_url

    sid = f"u{admin_user.id}-e6-deadbeef"
    with mcp_auth_context(admin_session.id):
        result = await get_fn(session_pull_url)(
            session_id=sid, path="/workspace/report.md"
        )

    assert "url" in result and "expires_in" in result
    assert "https://memory.test.example.com/claude/transfer/pull?token=" in result["url"]
    token = result["url"].split("token=", 1)[1]
    payload = verify_token(token)
    assert payload.user_id == admin_user.id
    assert payload.session_id == sid
    assert payload.path == "/workspace/report.md"
    assert payload.action == "read"


@pytest.mark.asyncio
async def test_session_pull_url_rejects_other_users_session(
    db_session, admin_user, admin_session
):
    from memory.api.MCP.servers.claude import session_pull_url

    foreign = "u9999-e6-aaaaaaaa"
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="not found"):
            await get_fn(session_pull_url)(
                session_id=foreign, path="/workspace/x"
            )


# -- session_push_url -------------------------------------------------------


@pytest.mark.asyncio
async def test_session_push_url_mints_write_token_separate_field(
    db_session, admin_user, admin_session
):
    from memory.api.MCP.servers.claude import session_push_url

    sid = f"u{admin_user.id}-e6-feedface"
    with mcp_auth_context(admin_session.id):
        result = await get_fn(session_push_url)(
            session_id=sid, path="/workspace"
        )

    assert "/claude/transfer/push" in result["url"]
    assert "token=" not in result["url"]  # token in body, not URL
    payload = verify_token(result["token"])
    assert payload.action == "write"
    assert payload.path == "/workspace"


# -- "latest" sentinel ------------------------------------------------------


@pytest.mark.asyncio
async def test_session_pull_url_resolves_latest(
    db_session, admin_user, admin_session
):
    from memory.api.MCP.servers.claude import session_pull_url

    fake_sessions = [
        OrchSessionInfo(
            session_id=f"u{admin_user.id}-e6-aaaa1111aaaa",
            status="exited",
        ),
        OrchSessionInfo(
            session_id=f"u{admin_user.id}-e6-bbbb2222bbbb",
            status="running",
        ),
    ]
    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.list_containers",
        new=AsyncMock(return_value=fake_sessions),
    ):
        with mcp_auth_context(admin_session.id):
            result = await get_fn(session_pull_url)(
                session_id="latest", path="/workspace/report.md"
            )

    token = result["url"].split("token=", 1)[1]
    payload = verify_token(token)
    # Must pick a running session if available
    assert payload.session_id == f"u{admin_user.id}-e6-bbbb2222bbbb"


@pytest.mark.asyncio
async def test_session_pull_url_latest_sorts_deterministically(
    db_session, admin_user, admin_session
):
    """When multiple running sessions exist, `latest` picks deterministically.

    The orchestrator currently doesn't expose timestamps; we sort by
    session_id descending so the result is stable across orchestrator
    restarts (the bug the previous "trust list order" implementation had).
    """
    from memory.api.MCP.servers.claude import session_pull_url

    fake_sessions = [
        OrchSessionInfo(
            session_id=f"u{admin_user.id}-e6-aaaa1111", status="running"
        ),
        OrchSessionInfo(
            session_id=f"u{admin_user.id}-e6-cccc3333", status="running"
        ),
        OrchSessionInfo(
            session_id=f"u{admin_user.id}-e6-bbbb2222", status="running"
        ),
    ]
    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.list_containers",
        new=AsyncMock(return_value=fake_sessions),
    ):
        with mcp_auth_context(admin_session.id):
            result = await get_fn(session_pull_url)(
                session_id="latest", path="/workspace/report.md"
            )

    token = result["url"].split("token=", 1)[1]
    payload = verify_token(token)
    # Deterministic: lexically max session_id wins
    assert payload.session_id == f"u{admin_user.id}-e6-cccc3333"

    # Re-run with the list in a different order — same result.
    reshuffled = list(reversed(fake_sessions))
    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.list_containers",
        new=AsyncMock(return_value=reshuffled),
    ):
        with mcp_auth_context(admin_session.id):
            result2 = await get_fn(session_pull_url)(
                session_id="latest", path="/workspace/report.md"
            )

    payload2 = verify_token(result2["url"].split("token=", 1)[1])
    assert payload2.session_id == f"u{admin_user.id}-e6-cccc3333"


@pytest.mark.asyncio
async def test_session_pull_url_latest_cross_source_uses_random_suffix(
    db_session, admin_user, admin_session
):
    """Cross-source `latest` resolution must not be biased by source letter.

    Session IDs have shape ``u<user>-<src>-<random_hex>``. If we sorted by
    the full session_id, ``s`` (snapshot) would always beat ``e`` (env) and
    ``x`` lexically, regardless of which session is actually most recent.
    Sorting on the random_hex suffix gives an unbiased (still arbitrary,
    but stable) tiebreak across sources.
    """
    from memory.api.MCP.servers.claude import session_pull_url

    # Snapshot session has a *smaller* random suffix than the env session.
    # If we sorted by full session_id, the snapshot would still win because
    # 's' > 'e' lexically. With suffix-only sorting, the env wins because
    # its suffix sorts higher.
    fake_sessions = [
        OrchSessionInfo(
            session_id=f"u{admin_user.id}-s3-aaaa1111", status="running"
        ),
        OrchSessionInfo(
            session_id=f"u{admin_user.id}-e6-bbbb2222", status="running"
        ),
    ]
    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.list_containers",
        new=AsyncMock(return_value=fake_sessions),
    ):
        with mcp_auth_context(admin_session.id):
            result = await get_fn(session_pull_url)(
                session_id="latest", path="/workspace/report.md"
            )

    payload = verify_token(result["url"].split("token=", 1)[1])
    # Env session wins because bbbb2222 > aaaa1111, despite e < s lexically.
    assert payload.session_id == f"u{admin_user.id}-e6-bbbb2222"


@pytest.mark.asyncio
async def test_session_pull_url_latest_with_no_sessions(
    db_session, admin_user, admin_session
):
    from memory.api.MCP.servers.claude import session_pull_url

    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.list_containers",
        new=AsyncMock(return_value=[]),
    ):
        with mcp_auth_context(admin_session.id):
            with pytest.raises(ValueError, match="No active session"):
                await get_fn(session_pull_url)(
                    session_id="latest", path="/workspace/x"
                )


# -- session_list_dir -------------------------------------------------------


@pytest.mark.asyncio
async def test_session_list_dir_proxies_to_orchestrator(
    db_session, admin_user, admin_session
):
    from memory.api.MCP.servers.claude import session_list_dir

    sid = f"u{admin_user.id}-e6-aaaabbbbcccc"
    fake_manifest = {
        "path": "/workspace",
        "entries": [
            {"name": "x.md", "type": "file", "size": 10, "mtime": "2026-04-25T00:00:00Z"}
        ],
        "truncated": False,
    }

    with patch(
        "memory.api.orchestrator_client.OrchestratorClient.list_dir",
        new=AsyncMock(return_value=fake_manifest),
    ):
        with mcp_auth_context(admin_session.id):
            result = await get_fn(session_list_dir)(
                session_id=sid, path="/workspace"
            )

    assert result == fake_manifest


@pytest.mark.asyncio
async def test_session_list_dir_rejects_foreign_session(
    db_session, admin_user, admin_session
):
    from memory.api.MCP.servers.claude import session_list_dir

    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError, match="not found"):
            await get_fn(session_list_dir)(
                session_id="u9999-e6-zzzzzzzz", path="/workspace"
            )


# -- adversarial path validation through MCP --------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_path",
    [
        "../../etc/passwd",
        "/workspace/../../../etc/passwd",
        "/workspace/./report.md",
        "",
        '/workspace/foo"bar',
        "/workspace/foo\nbar",
    ],
)
async def test_session_pull_url_rejects_bad_path(
    db_session, admin_user, admin_session, bad_path
):
    """The MCP layer must validate paths too — Metis flagged that mint
    happens at three sites (two HTTP endpoints + MCP) and all three need
    consistent validation."""
    from memory.api.MCP.servers.claude import session_pull_url

    sid = f"u{admin_user.id}-e6-aaaa1111"
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError):
            await get_fn(session_pull_url)(session_id=sid, path=bad_path)


@pytest.mark.asyncio
async def test_session_push_url_rejects_traversal(
    db_session, admin_user, admin_session
):
    from memory.api.MCP.servers.claude import session_push_url

    sid = f"u{admin_user.id}-e6-aaaa1111"
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError):
            await get_fn(session_push_url)(
                session_id=sid, path="/workspace/../../etc"
            )


