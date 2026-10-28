"""Tests for the claude MCP server (archived Claude Code transcript search)."""
# pyright: reportFunctionMemberAccess=false

import json
import uuid as uuid_lib
from unittest.mock import AsyncMock, patch

import pytest

from memory.api.search.types import SearchResult
from memory.common import settings as settings_module
from memory.common.db.models import Session as CodingSession
from memory.common.db.models import CodingProject, SessionSegment
from tests.conftest import mcp_auth_context


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

    with mcp_auth_context(user_session.token):
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

    with mcp_auth_context(user_session.token):
        page1 = await session_fetch.fn(session_id=str(session.id), limit=4)

    assert page1["project"] == "/code/memory"
    assert page1["summary"] == "worked on the thing"
    assert [m["index"] for m in page1["messages"]] == [0, 1, 2, 3]
    assert page1["messages"][0]["role"] == "user"
    assert page1["messages"][0]["text"] == "question number 0"
    assert page1["next_index"] == 4

    with mcp_auth_context(user_session.token):
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

    with mcp_auth_context(user_session.token):
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

    with mcp_auth_context(user_session.token):
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
        with mcp_auth_context(user_session.token):
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
        with mcp_auth_context(user_session.token):
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

    with mcp_auth_context(admin_session.token):
        listed = await list_items.fn(modalities={"session"})
        counted = await count_items.fn(modalities={"session"})
        fetched = await fetch.fn(ids=[segment.id])

    assert listed["items"] == []
    assert counted["total"] == 0
    assert fetched == []
