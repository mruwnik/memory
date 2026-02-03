"""
Celery tasks for session summary generation.

Provides scheduled tasks for:
- Generating AI summaries of coding sessions
- Updating session metadata
"""

import json
import logging
import os
from datetime import datetime, timezone
from uuid import UUID

from memory.common import settings
from memory.common.celery_app import app, SUMMARIZE_SESSION, SUMMARIZE_STALE_SESSIONS
from memory.common.db.connection import make_session
from memory.common.db.models import Session
from memory.common.llms import create_provider, Message, MessageRole, LLMSettings

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
