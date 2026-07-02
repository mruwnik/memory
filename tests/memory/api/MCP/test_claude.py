"""Tests for MCP claude (cloud-claude session files) server."""
# pyright: reportFunctionMemberAccess=false

import json
import uuid as uuid_lib
from unittest.mock import AsyncMock, patch

import pytest

from memory.api.orchestrator_client import SessionInfo as OrchSessionInfo
from memory.api.search.types import SearchResult
from memory.api.transfer_tokens import verify_token
from memory.common import settings as settings_module
from memory.common.db.models import Session as CodingSession
from memory.common.db.models import CodingProject, SessionSegment
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
            session_id=f"u{admin_user.id}-e6-aaaa1111aaaa1111aaaa1111aaaa1111",
            container_name="claude-aaaa1111aaaa1111aaaa1111aaaa1111",
            status="running",
        ),
        OrchSessionInfo(
            session_id="u9999-e6-bbbb2222bbbb2222bbbb2222bbbb2222",  # different user
            container_name="claude-bbbb2222bbbb2222bbbb2222bbbb2222",
            status="running",
        ),
        OrchSessionInfo(
            session_id=f"u{admin_user.id}-s3-cccc3333cccc3333cccc3333cccc3333",
            container_name="claude-cccc3333cccc3333cccc3333cccc3333",
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
    assert f"u{admin_user.id}-e6-aaaa1111aaaa1111aaaa1111aaaa1111" in ids
    assert f"u{admin_user.id}-s3-cccc3333cccc3333cccc3333cccc3333" in ids
    assert "u9999-e6-bbbb2222bbbb2222bbbb2222bbbb2222" not in ids
    statuses = {s["session_id"]: s["status"] for s in result}
    assert statuses[f"u{admin_user.id}-e6-aaaa1111aaaa1111aaaa1111aaaa1111"] == "running"
    assert statuses[f"u{admin_user.id}-s3-cccc3333cccc3333cccc3333cccc3333"] == "exited"


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

    sid = f"u{admin_user.id}-e6-deadbeefdeadbeefdeadbeefdeadbeef"
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

    sid = f"u{admin_user.id}-e6-feedfacefeedfacefeedfacefeedface"
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
            session_id=f"u{admin_user.id}-e6-aaaa1111aaaa1111aaaa1111aaaa1111",
            status="exited",
        ),
        OrchSessionInfo(
            session_id=f"u{admin_user.id}-e6-bbbb2222bbbb2222bbbb2222bbbb2222",
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
    assert payload.session_id == f"u{admin_user.id}-e6-bbbb2222bbbb2222bbbb2222bbbb2222"


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
            session_id=f"u{admin_user.id}-e6-aaaa1111aaaa1111aaaa1111aaaa1111", status="running"
        ),
        OrchSessionInfo(
            session_id=f"u{admin_user.id}-e6-cccc3333cccc3333cccc3333cccc3333", status="running"
        ),
        OrchSessionInfo(
            session_id=f"u{admin_user.id}-e6-bbbb2222bbbb2222bbbb2222bbbb2222", status="running"
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
    assert payload.session_id == f"u{admin_user.id}-e6-cccc3333cccc3333cccc3333cccc3333"

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
    assert payload2.session_id == f"u{admin_user.id}-e6-cccc3333cccc3333cccc3333cccc3333"


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
            session_id=f"u{admin_user.id}-s3-aaaa1111aaaa1111aaaa1111aaaa1111", status="running"
        ),
        OrchSessionInfo(
            session_id=f"u{admin_user.id}-e6-bbbb2222bbbb2222bbbb2222bbbb2222", status="running"
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
    # Env session wins because bbbb2222... > aaaa1111..., despite e < s lexically.
    assert payload.session_id == f"u{admin_user.id}-e6-bbbb2222bbbb2222bbbb2222bbbb2222"


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

    sid = f"u{admin_user.id}-e6-aaaabbbbccccaaaabbbbccccaaaabbbb"
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

    sid = f"u{admin_user.id}-e6-aaaa1111aaaa1111aaaa1111aaaa1111"
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError):
            await get_fn(session_pull_url)(session_id=sid, path=bad_path)


@pytest.mark.asyncio
async def test_session_push_url_rejects_traversal(
    db_session, admin_user, admin_session
):
    from memory.api.MCP.servers.claude import session_push_url

    sid = f"u{admin_user.id}-e6-aaaa1111aaaa1111aaaa1111aaaa1111"
    with mcp_auth_context(admin_session.id):
        with pytest.raises(ValueError):
            await get_fn(session_push_url)(
                session_id=sid, path="/workspace/../../etc"
            )




# -- archived transcript search ----------------------------------------------


@pytest.fixture
def sessions_dir(tmp_path):
    sessions = tmp_path / "session_transcripts"
    with patch.object(settings_module, "SESSIONS_STORAGE_DIR", sessions):
        yield sessions


def transcript_event(event_type: str, text: str, minute: int) -> dict:
    return {
        "uuid": str(uuid_lib.uuid4()),
        "type": event_type,
        "timestamp": f"2026-07-01T12:{minute:02d}:00Z",
        "message": {"role": event_type, "content": text},
    }


def make_coding_session(db_session, user, sessions_dir, events=None, directory=None):
    project = None
    if directory:
        project = CodingProject(user_id=user.id, directory=directory)
        db_session.add(project)
        db_session.flush()

    session = CodingSession(
        id=uuid_lib.uuid4(),
        user_id=user.id,
        coding_project_id=project and project.id,
        transcript_path=f"{user.id}/{uuid_lib.uuid4()}.jsonl",
        summary="worked on the thing",
    )
    db_session.add(session)
    db_session.commit()

    if events is not None:
        file = sessions_dir / session.transcript_path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    return session


def make_segment(db_session, session, start=0, end=4, roles=("user", "assistant")):
    segment = SessionSegment(
        session_id=session.id,
        start_index=start,
        end_index=end,
        roles=list(roles),
        models=["claude-fable-5"],
        content=f"User: segment content {start}",
        sha256=uuid_lib.uuid4().bytes + uuid_lib.uuid4().bytes,
        size=30,
        creator_id=session.user_id,
        project_id=None,
        embed_status="STORED",
    )
    db_session.add(segment)
    db_session.commit()
    return segment


@pytest.mark.asyncio
async def test_session_fetch_owner_only(
    db_session, admin_user, regular_user, user_session, sessions_dir
):
    from memory.api.MCP.servers.claude import session_fetch

    other_session = make_coding_session(db_session, admin_user, sessions_dir, events=[])

    with mcp_auth_context(user_session.id):
        with pytest.raises(Exception, match="Session not found"):
            await session_fetch.fn(session_id=str(other_session.id))


@pytest.mark.asyncio
async def test_session_fetch_pagination(
    db_session, regular_user, user_session, sessions_dir
):
    from memory.api.MCP.servers.claude import session_fetch

    events = [
        transcript_event("user", f"question number {i}", minute=i) for i in range(6)
    ]
    session = make_coding_session(
        db_session, regular_user, sessions_dir, events=events, directory="/code/memory"
    )

    with mcp_auth_context(user_session.id):
        page1 = await session_fetch.fn(session_id=str(session.id), limit=4)

    assert page1["project"] == "/code/memory"
    assert page1["summary"] == "worked on the thing"
    assert [m["index"] for m in page1["messages"]] == [0, 1, 2, 3]
    assert page1["messages"][0]["role"] == "user"
    assert page1["messages"][0]["text"] == "question number 0"
    assert page1["next_index"] == 4

    with mcp_auth_context(user_session.id):
        page2 = await session_fetch.fn(
            session_id=str(session.id), start_index=page1["next_index"], limit=4
        )

    assert [m["index"] for m in page2["messages"]] == [4, 5]
    assert page2["next_index"] is None


@pytest.mark.asyncio
async def test_session_fetch_around_time(
    db_session, regular_user, user_session, sessions_dir
):
    from memory.api.MCP.servers.claude import session_fetch

    events = [
        transcript_event("user", f"message at minute {i}", minute=i) for i in range(10)
    ]
    session = make_coding_session(db_session, regular_user, sessions_dir, events=events)

    with mcp_auth_context(user_session.id):
        result = await session_fetch.fn(
            session_id=str(session.id),
            around_time="2026-07-01T12:05:00Z",
            limit=4,
        )

    indices = [m["index"] for m in result["messages"]]
    assert len(indices) == 4
    assert 5 in indices  # pivot message included
    assert indices == sorted(indices)
    assert indices[0] < 5  # window includes context before the pivot


@pytest.mark.asyncio
async def test_session_fetch_no_transcript(
    db_session, regular_user, user_session, sessions_dir
):
    from memory.api.MCP.servers.claude import session_fetch

    session = make_coding_session(db_session, regular_user, sessions_dir)

    with mcp_auth_context(user_session.id):
        result = await session_fetch.fn(session_id=str(session.id))

    assert result["messages"] == []
    assert result["next_index"] is None


def test_owned_segment_ids_scopes_to_owner(
    db_session, admin_user, regular_user, sessions_dir
):
    from memory.api.MCP.servers.claude import owned_segment_ids

    mine = make_coding_session(
        db_session, regular_user, sessions_dir, directory="/code/memory"
    )
    theirs = make_coding_session(
        db_session, admin_user, sessions_dir, directory="/code/memory"
    )
    my_segment = make_segment(db_session, mine)
    make_segment(db_session, theirs)

    assert owned_segment_ids(regular_user.id) == [my_segment.id]


def test_owned_segment_ids_filters(db_session, regular_user, sessions_dir):
    from memory.api.MCP.servers.claude import owned_segment_ids

    memory_session = make_coding_session(
        db_session, regular_user, sessions_dir, directory="/code/memory"
    )
    other_session = make_coding_session(
        db_session, regular_user, sessions_dir, directory="/code/other"
    )
    memory_segment = make_segment(db_session, memory_session)
    user_only_segment = make_segment(
        db_session, other_session, start=10, end=12, roles=("user",)
    )

    assert owned_segment_ids(regular_user.id, project="memory") == [memory_segment.id]
    assert owned_segment_ids(regular_user.id, role="assistant") == [memory_segment.id]
    assert sorted(owned_segment_ids(regular_user.id, role="user")) == sorted(
        [memory_segment.id, user_only_segment.id]
    )


@pytest.mark.asyncio
async def test_session_search_no_sessions_skips_search(
    db_session, regular_user, user_session, sessions_dir
):
    from memory.api.MCP.servers import claude

    with patch.object(claude, "search_base", new=AsyncMock()) as mock_search:
        with mcp_auth_context(user_session.id):
            result = await claude.session_search.fn(query="anything")

    assert result == []
    mock_search.assert_not_called()


@pytest.mark.asyncio
async def test_session_search_formats_hits(
    db_session, regular_user, user_session, sessions_dir
):
    from memory.api.MCP.servers import claude

    session = make_coding_session(
        db_session, regular_user, sessions_dir, directory="/code/memory"
    )
    segment = make_segment(db_session, session)

    fake_result = SearchResult(
        id=segment.id,
        chunks=["User: how do I fuse RRF scores?"],
        metadata=dict(segment.as_payload()),
        search_score=0.87,
    )

    with patch.object(
        claude, "search_base", new=AsyncMock(return_value=[fake_result])
    ) as mock_search:
        with mcp_auth_context(user_session.id):
            hits = await claude.session_search.fn(query="rrf fusion")

    mock_search.assert_called_once()
    _, kwargs = mock_search.call_args
    assert kwargs["modalities"] == {"session"}
    assert kwargs["filters"]["source_ids"] == [segment.id]

    assert len(hits) == 1
    hit = hits[0]
    assert hit["session_id"] == str(session.id)
    assert hit["project"] == "/code/memory"
    assert hit["session_summary"] == "worked on the thing"
    assert hit["start_index"] == 0
    assert hit["end_index"] == 4
    assert hit["roles"] == ["user", "assistant"]
    assert hit["models"] == ["claude-fable-5"]
    assert hit["score"] == 0.87
    assert "RRF" in hit["snippet"]


@pytest.mark.asyncio
async def test_generic_tools_never_expose_session_segments(
    db_session, admin_user, admin_session, sessions_dir
):
    """Even superadmins must not reach session segments via list/count/fetch."""
    from memory.api.MCP.servers.core import count_items, fetch, list_items

    session = make_coding_session(db_session, admin_user, sessions_dir)
    segment = make_segment(db_session, session)

    with mcp_auth_context(admin_session.id):
        listed = await list_items.fn(modalities={"session"})
        counted = await count_items.fn(modalities={"session"})
        fetched = await fetch.fn(ids=[segment.id])

    assert listed["items"] == []
    assert counted["total"] == 0
    assert fetched == []
