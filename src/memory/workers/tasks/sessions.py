"""
Celery tasks for session summary generation and search indexing.

Provides scheduled tasks for:
- Generating AI summaries of coding sessions
- Indexing session transcripts for search (SessionSegment items)
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from memory.common import settings
from memory.common.celery_app import (
    app,
    INDEX_SESSION,
    INDEX_STALE_SESSIONS,
    SUMMARIZE_SESSION,
    SUMMARIZE_STALE_SESSIONS,
)
from memory.common.content_processing import (
    check_content_exists,
    create_content_hash,
    process_content_item,
)
from memory.common.db.connection import make_session
from memory.common.db.models import Session, SessionSegment
from memory.common.jobs import tracked_task
from memory.common.llms import create_provider, Message, MessageRole, LLMSettings
from memory.parsers import claude_sessions

logger = logging.getLogger(__name__)

# Model to use for session summarization
SUMMARIZER_MODEL = os.getenv("SESSION_SUMMARIZER_MODEL", settings.SUMMARIZER_MODEL)

# Maximum conversation length to send to the summarizer (in characters)
MAX_CONVERSATION_LENGTH = 50000


def extract_conversation_text(transcript_path: str) -> str:
    """
    Extract user and assistant text messages from a session transcript.

    Excludes:
    - Thinking blocks
    - Tool use blocks
    - Tool results
    - System messages

    Returns a formatted conversation string.
    """
    transcript_file = settings.SESSIONS_STORAGE_DIR / transcript_path
    if not transcript_file.exists():
        return ""

    messages = []
    for line in transcript_file.read_text().splitlines():
        if not line.strip():
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type")
        if event_type not in ("user", "assistant"):
            continue

        msg = event.get("message", {})
        content = msg.get("content", [])

        # Extract text blocks only
        text_parts = []
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))

        if text_parts:
            role = event_type.capitalize()
            messages.append(f"{role}: {' '.join(text_parts)}")

    return "\n\n".join(messages)


def generate_summary(conversation: str) -> str:
    """
    Generate a summary of the conversation using an LLM.

    Args:
        conversation: The extracted conversation text

    Returns:
        A 1-2 sentence summary of what was done in the session
    """
    if not conversation.strip():
        return ""

    # Truncate if too long
    if len(conversation) > MAX_CONVERSATION_LENGTH:
        conversation = conversation[:MAX_CONVERSATION_LENGTH] + "\n\n[... truncated ...]"

    provider = create_provider(model=SUMMARIZER_MODEL)

    system_prompt = """You are summarizing a coding session between a user and an AI assistant.
Write a concise 1-2 sentence summary of what was accomplished in this session.
Focus on the main task or goal, not the individual steps.
Be specific about what was built, fixed, or discussed.
Do not start with "The user" or "In this session" - just describe what was done."""

    messages = [
        Message(
            role=MessageRole.USER,
            content=f"Summarize this coding session:\n\n{conversation}",
        )
    ]

    llm_settings = LLMSettings(temperature=0.3, max_tokens=150)

    try:
        response = provider.generate(
            messages=messages,
            system_prompt=system_prompt,
            settings=llm_settings,
        )
        return response.strip()
    except Exception as e:
        logger.error(f"Failed to generate summary: {e}")
        return ""


@app.task(name=SUMMARIZE_SESSION)
@tracked_task
def summarize_session(session_id: str) -> dict:
    """
    Generate and save a summary for a single session.

    Args:
        session_id: UUID of the session to summarize

    Returns:
        Dict with status and summary
    """
    logger.info(f"Summarizing session {session_id}")

    try:
        session_uuid = UUID(session_id)
    except ValueError:
        logger.error(f"Invalid session UUID: {session_id}")
        return {"status": "error", "message": "Invalid session UUID"}

    with make_session() as db:
        session = db.get(Session, session_uuid)
        if not session:
            logger.error(f"Session not found: {session_id}")
            return {"status": "error", "message": "Session not found"}

        if not session.transcript_path:
            logger.info(f"Session {session_id} has no transcript")
            return {"status": "skipped", "message": "No transcript"}

        # Check if summary is already fresh (avoids redundant LLM calls
        # when multiple tasks are queued for the same session)
        transcript_file = settings.SESSIONS_STORAGE_DIR / session.transcript_path
        if session.summary_updated_at and transcript_file.exists():
            file_mtime = datetime.fromtimestamp(
                transcript_file.stat().st_mtime, tz=timezone.utc
            )
            if session.summary_updated_at >= file_mtime:
                logger.info(f"Session {session_id} summary is already fresh, skipping")
                return {"status": "skipped", "message": "Summary already fresh"}

        # Extract conversation and generate summary
        conversation = extract_conversation_text(session.transcript_path)
        if not conversation:
            logger.info(f"Session {session_id} has no conversation content")
            return {"status": "skipped", "message": "No conversation content"}

        summary = generate_summary(conversation)
        if not summary:
            logger.warning(f"Failed to generate summary for session {session_id}")
            return {"status": "error", "message": "Summary generation failed"}

        # Update session
        session.summary = summary
        session.summary_updated_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(f"Summarized session {session_id}: {summary[:100]}...")
        return {"status": "success", "summary": summary}


@app.task(name=SUMMARIZE_STALE_SESSIONS)
@tracked_task
def summarize_stale_sessions() -> dict:
    """
    Find and summarize sessions that have been modified since their last summary.

    This runs hourly and processes sessions where:
    - The transcript file has been modified since summary_updated_at
    - Or the session has no summary yet

    Returns:
        Dict with counts of processed sessions
    """
    logger.info("Checking for sessions needing summarization")

    processed = 0
    skipped = 0
    errors = 0

    with make_session() as db:
        # Get all sessions with transcripts
        sessions = db.query(Session).filter(Session.transcript_path.isnot(None)).all()

        for session in sessions:
            if not session.transcript_path:
                continue
            transcript_file = settings.SESSIONS_STORAGE_DIR / session.transcript_path

            if not transcript_file.exists():
                skipped += 1
                continue

            # Check if summary is stale
            file_mtime = datetime.fromtimestamp(
                transcript_file.stat().st_mtime, tz=timezone.utc
            )

            needs_update = (
                session.summary_updated_at is None
                or file_mtime > session.summary_updated_at
            )

            if not needs_update:
                skipped += 1
                continue

            # Queue summarization task
            try:
                summarize_session.delay(str(session.id))  # type: ignore[attr-defined]
                processed += 1
            except Exception as e:
                logger.error(f"Failed to queue summarization for {session.id}: {e}")
                errors += 1

    logger.info(
        f"Session summarization check complete: {processed} queued, {skipped} skipped, {errors} errors"
    )
    return {"queued": processed, "skipped": skipped, "errors": errors}


def transcript_idle(transcript_file: Path, now: datetime) -> bool:
    """Whether the transcript has been quiet long enough to index its tail."""
    mtime = datetime.fromtimestamp(transcript_file.stat().st_mtime, tz=timezone.utc)
    return (now - mtime).total_seconds() >= settings.SESSION_INDEX_MIN_IDLE_SECONDS


@app.task(name=INDEX_SESSION)
@tracked_task
def index_session(session_id: str) -> dict:
    """
    Create SessionSegment search items for a session's transcript.

    Processes transcript lines past the session's ``indexed_up_to``
    watermark, groups the conversational messages into embedding-sized
    segments, and runs each through the standard content pipeline
    (chunks + Qdrant + BM25). The trailing partial segment is held back
    until the transcript has been idle for SESSION_INDEX_MIN_IDLE_SECONDS
    so a still-running session doesn't produce overlapping segments.

    Segments are owner-only: creator_id is the session owner and
    project_id is an explicit NULL.
    """
    try:
        session_uuid = UUID(session_id)
    except ValueError:
        logger.error(f"Invalid session UUID: {session_id}")
        return {"status": "error", "message": "Invalid session UUID"}

    now = datetime.now(timezone.utc)

    with make_session() as db:
        session = db.get(Session, session_uuid)
        if not session:
            return {"status": "error", "message": "Session not found"}
        if not session.transcript_path:
            return {"status": "skipped", "message": "No transcript"}

        transcript_file = settings.SESSIONS_STORAGE_DIR / session.transcript_path
        if not transcript_file.exists():
            return {"status": "skipped", "message": "Transcript file missing"}

        messages = claude_sessions.iter_transcript_messages(
            transcript_file, start_index=session.indexed_up_to
        )
        segments = claude_sessions.build_segments(messages)

        if segments and not transcript_idle(transcript_file, now):
            segments = segments[:-1]

        created, duplicates = 0, 0
        failed_at: int | None = None
        for segment in segments:
            content = segment.text
            sha256 = create_content_hash(
                content, str(session.id), str(segment.start_index)
            )
            if check_content_exists(db, SessionSegment, sha256=sha256):
                duplicates += 1
            else:
                item = SessionSegment(
                    session_id=session.id,
                    start_index=segment.start_index,
                    end_index=segment.end_index,
                    start_time=segment.start_time,
                    end_time=segment.end_time,
                    roles=segment.roles,
                    models=segment.models,
                    content=content,
                    size=len(content),
                    mime_type="text/plain",
                    sha256=sha256,
                    creator_id=session.user_id,
                    project_id=None,  # explicit NULL: owner-only, never inherited
                )
                try:
                    result = process_content_item(item, db)
                except IntegrityError:
                    # A concurrent index_session run inserted this segment
                    # between our existence check and the flush; theirs is
                    # identical (deterministic segmentation), so treat as
                    # a duplicate and keep going.
                    db.rollback()
                    duplicates += 1
                else:
                    if result.get("status") == "failed":
                        # Embedding failed (e.g. a transient Voyage outage).
                        # Drop the FAILED row and halt the watermark here so
                        # the hourly sweep retries this slice — advancing
                        # past it would leave a permanent gap in the index,
                        # since nothing re-embeds zero-chunk rows.
                        db.delete(item)
                        db.commit()
                        failed_at = segment.start_index
                        break
                    created += 1
            session.indexed_up_to = segment.end_index + 1

        # Only mark the run complete on full success: a stale indexed_at
        # keeps the sweep's requeue condition true, which is what retries
        # the failed slice.
        if failed_at is None:
            session.indexed_at = now
        db.commit()

        if failed_at is not None:
            logger.warning(
                f"Indexing session {session_id} halted at segment {failed_at} "
                f"(embedding failed); {created} segments created before halt"
            )
            return {
                "status": "partial",
                "created": created,
                "duplicates": duplicates,
                "failed_at": failed_at,
                "indexed_up_to": session.indexed_up_to,
            }

        logger.info(
            f"Indexed session {session_id}: {created} segments created, "
            f"{duplicates} already present, watermark {session.indexed_up_to}"
        )
        return {
            "status": "success",
            "created": created,
            "duplicates": duplicates,
            "indexed_up_to": session.indexed_up_to,
        }


@app.task(name=INDEX_STALE_SESSIONS)
@tracked_task
def index_stale_sessions() -> dict:
    """
    Queue indexing for sessions whose transcripts have unindexed content.

    A session needs (re)indexing when:
    - it has never been indexed, or
    - the transcript was modified after the last indexing run, or
    - the last run happened while the transcript was still hot (its tail
      segment was held back and no further writes will bump the mtime).
    """
    queued = 0
    skipped = 0

    with make_session() as db:
        # Fetch only the columns the sweep needs instead of full ORM
        # entities: the freshness check (file mtime vs indexed_at) can't
        # move into SQL, so every candidate row still gets a per-file stat,
        # but this keeps the hourly scan cheap as the session count grows.
        rows = (
            db.query(Session.id, Session.transcript_path, Session.indexed_at)
            .filter(Session.transcript_path.isnot(None))
            .all()
        )

        for session_id, transcript_path, indexed_at in rows:
            if not transcript_path:
                continue
            transcript_file = settings.SESSIONS_STORAGE_DIR / transcript_path
            if not transcript_file.exists():
                skipped += 1
                continue

            mtime = datetime.fromtimestamp(
                transcript_file.stat().st_mtime, tz=timezone.utc
            )
            tail_may_be_pending = indexed_at is not None and (
                (indexed_at - mtime).total_seconds()
                < settings.SESSION_INDEX_MIN_IDLE_SECONDS
            )
            needs_indexing = (
                indexed_at is None or mtime > indexed_at or tail_may_be_pending
            )
            if not needs_indexing:
                skipped += 1
                continue

            index_session.delay(str(session_id))  # type: ignore[attr-defined]
            queued += 1

    logger.info(f"Session indexing check: {queued} queued, {skipped} skipped")
    return {"queued": queued, "skipped": skipped}
