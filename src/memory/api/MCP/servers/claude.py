"""MCP subserver for searching archived Claude Code session transcripts.

Strictly owner-only: sessions are personal working data, so even admins only
see their own here.
"""

import logging
from collections import deque
from datetime import datetime
from typing import Any
from uuid import UUID as PyUUID

from fastmcp import FastMCP
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from memory.api.MCP.access import (
    build_mcp_user_access_filter,
    get_mcp_current_user,
    log_search_access,
)
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.api.search.search import search as search_base
from memory.api.search.types import SearchConfig, SearchFilters
from memory.common import extract, settings
from memory.common.dates import parse_iso_datetime_utc
from memory.common.db.connection import make_session
from memory.common.db.models import CodingProject, Session, SessionSegment
from memory.common.scopes import SCOPE_READ
from memory.parsers import claude_sessions

logger = logging.getLogger(__name__)

MAX_FETCH_MESSAGES = 100

claude_mcp = FastMCP("memory-claude")


def like_escape(value: str) -> str:
    """Backslash-escape LIKE metacharacters (%, _, \\) for use in a pattern."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def owned_segment_ids(
    user_id: int,
    project: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    role: str | None = None,
) -> list[int]:
    """IDs of SessionSegments owned by ``user_id`` matching the filters.

    This is the owner-only gate for transcript search: passing the result
    as ``source_ids`` restricts the whole hybrid pipeline (Qdrant + BM25)
    to the caller's own sessions, on top of the regular access filter.
    """
    with make_session() as db:
        query = (
            db.query(SessionSegment.id)
            .join(Session, SessionSegment.session_id == Session.id)
            .filter(Session.user_id == user_id)
        )
        if project:
            query = query.join(
                CodingProject, Session.coding_project_id == CodingProject.id
            ).filter(
                CodingProject.directory.ilike(
                    f"%{like_escape(project)}%", escape="\\"
                )
            )
        # Timestamp-less segments (transcripts whose events carry no
        # timestamps) match any date range rather than silently vanishing.
        if start_time:
            query = query.filter(
                or_(
                    SessionSegment.end_time >= start_time,
                    SessionSegment.end_time.is_(None),
                )
            )
        if end_time:
            query = query.filter(
                or_(
                    SessionSegment.start_time <= end_time,
                    SessionSegment.start_time.is_(None),
                )
            )
        if role:
            query = query.filter(SessionSegment.roles.contains([role]))
        return [row[0] for row in query.all()]


def parse_time_arg(value: str | None, name: str) -> datetime | None:
    if not value:
        return None
    parsed = parse_iso_datetime_utc(value)
    if parsed is None:
        raise ValueError(f"Invalid {name}: {value!r} (expected ISO format)")
    return parsed


@claude_mcp.tool()
@visible_when(require_scopes(SCOPE_READ))
async def session_search(
    query: str,
    project: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    role: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search your archived Claude Code session transcripts.

    Hybrid semantic + keyword search over the conversational text of past
    sessions (tool traffic is not indexed). Only your own sessions are
    searched. Each hit points into one session's transcript; use
    session_fetch with the hit's session_id and start_index to read the
    surrounding conversation.

    Args:
        query: Natural language description of what you're looking for.
        project: Substring match on the project directory (e.g. "memory").
        start_date: Only match segments ending at/after this ISO timestamp.
        end_date: Only match segments starting at/before this ISO timestamp.
        role: Only match segments containing this role: "user" or "assistant".
        limit: Maximum hits to return (default 10, max 50).

    Returns:
        Hits sorted by relevance, each with session_id, project directory,
        session_summary, start_index/end_index (transcript line range),
        start_time/end_time, roles, models, score, and a text snippet.
    """
    user = get_mcp_current_user()
    if not user or user.id is None:
        raise ValueError("Not authenticated")

    start_time = parse_time_arg(start_date, "start_date")
    end_time = parse_time_arg(end_date, "end_date")

    source_ids = owned_segment_ids(user.id, project, start_time, end_time, role)
    if not source_ids:
        return []

    with make_session() as session:
        access_filter = build_mcp_user_access_filter(session, user)
    filters = SearchFilters(source_ids=source_ids, access_filter=access_filter)
    # Transcript search should stay cheap/local: no LLM round-trips for HyDE
    # or query analysis — the BM25 + embedding hybrid still applies.
    config = SearchConfig(
        limit=max(1, min(limit, 50)), useHyde=False, useQueryAnalysis=False
    )
    results = await search_base(
        extract.extract_text(query, skip_summary=True),
        modalities={"session"},
        filters=filters,
        config=config,
    )

    session_ids = {
        meta["session_id"]
        for r in results
        if (meta := r.metadata) and meta.get("session_id")
    }
    with make_session() as db:
        sessions = (
            db.query(Session)
            .options(joinedload(Session.coding_project))
            .filter(Session.id.in_([PyUUID(sid) for sid in session_ids]))
            .all()
        )
        sessions_by_id = {
            str(s.id): {
                "project": s.coding_project.directory if s.coding_project else None,
                "summary": s.summary,
            }
            for s in sessions
        }

    hits = []
    for r in results:
        meta = r.metadata or {}
        session_info = sessions_by_id.get(meta.get("session_id") or "", {})
        snippet = (r.chunks[0] if r.chunks else r.content) or ""
        hits.append(
            {
                "session_id": meta.get("session_id"),
                "project": session_info.get("project"),
                "session_summary": session_info.get("summary"),
                "start_index": meta.get("start_index"),
                "end_index": meta.get("end_index"),
                "start_time": meta.get("start_time"),
                "end_time": meta.get("end_time"),
                "roles": meta.get("roles"),
                "models": meta.get("models"),
                "score": r.search_score,
                "snippet": str(snippet)[:500],
            }
        )

    try:
        log_search_access(user.id, query, len(hits))
    except Exception:
        logger.exception("log_search_access failed for user_id=%s", user.id)

    return hits


def window_around_time(
    file, target: datetime, limit: int
) -> list[claude_sessions.TranscriptMessage]:
    """A window of up to ``limit`` messages centered on ``target``.

    Streams the transcript once: keeps the last limit//2 messages before
    the first message at/after ``target``, then fills the rest of the
    window going forward.
    """
    before: deque[claude_sessions.TranscriptMessage] = deque(maxlen=limit // 2)
    after: list[claude_sessions.TranscriptMessage] = []
    pivot_seen = False

    for message in claude_sessions.iter_transcript_messages(file):
        if not pivot_seen and message.timestamp and message.timestamp >= target:
            pivot_seen = True
        if pivot_seen:
            after.append(message)
            if len(after) >= limit - len(before):
                break
        else:
            before.append(message)

    return list(before) + after


@claude_mcp.tool()
@visible_when(require_scopes(SCOPE_READ))
async def session_fetch(
    session_id: str,
    start_index: int = 0,
    around_time: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Read conversational messages from one of your archived session transcripts.

    Returns an ordered window of user/assistant messages (tool traffic is
    omitted; individual messages are truncated to ~5000 chars). Use after
    session_search to read the context around a hit, or to page through a
    session from the start.

    Args:
        session_id: Session UUID (from session_search or the sessions API).
        start_index: Transcript line index to start from (a hit's
            start_index, or a previous call's next_index).
        around_time: ISO timestamp; returns a window centered on it
            (overrides start_index).
        limit: Maximum messages to return (default 20, max 100).

    Returns:
        {session_id, project, summary, messages: [{index, role, timestamp,
        model, text}], next_index} — next_index is null once the transcript
        is exhausted.
    """
    user = get_mcp_current_user()
    if not user or user.id is None:
        raise ValueError("Not authenticated")

    try:
        session_uuid = PyUUID(session_id)
    except ValueError:
        raise ValueError(f"Invalid session UUID: {session_id}")

    with make_session() as db:
        session = db.get(Session, session_uuid)
        if not session or session.user_id != user.id:
            raise ValueError("Session not found")
        transcript_path = session.transcript_path
        summary = session.summary
        project = session.coding_project.directory if session.coding_project else None

    limit = max(1, min(limit, MAX_FETCH_MESSAGES))
    messages: list[claude_sessions.TranscriptMessage] = []

    transcript_file = (
        settings.SESSIONS_STORAGE_DIR / transcript_path if transcript_path else None
    )
    if transcript_file and transcript_file.exists():
        if target := parse_time_arg(around_time, "around_time"):
            messages = window_around_time(transcript_file, target, limit)
        else:
            iterator = claude_sessions.iter_transcript_messages(
                transcript_file, start_index=max(0, start_index)
            )
            for message in iterator:
                messages.append(message)
                if len(messages) >= limit:
                    break

    return {
        "session_id": session_id,
        "project": project,
        "summary": summary,
        "messages": [
            {
                "index": m.index,
                "role": m.role,
                "timestamp": m.timestamp.isoformat() if m.timestamp else None,
                "model": m.model,
                "text": m.text,
            }
            for m in messages
        ],
        "next_index": messages[-1].index + 1 if len(messages) >= limit else None,
    }
