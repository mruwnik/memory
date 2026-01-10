"""API endpoints for availability polls (LettuceMeet-style scheduling).

Public endpoints for poll responses. Authenticated poll management is done
via MCP tools in memory.api.MCP.servers.polling.
"""

import html
import secrets
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from memory.common.db.connection import get_session
from memory.common.db.models import (
    AvailabilityPoll,
    AvailabilityPollPayload,
    AvailabilityPollDetailPayload,
    PollResponse,
    PollResponsePayload,
    PollAvailability,
    PollAvailabilityPayload,
    AvailabilityLevel,
    SlotAggregation,
)

router = APIRouter(prefix="/polls", tags=["polls"])


# Request schemas


class AvailabilitySlot(BaseModel):
    """A single time slot selection (all times in UTC)."""

    slot_start: datetime
    slot_end: datetime
    availability_level: int = AvailabilityLevel.AVAILABLE.value


class PollResponseRequest(BaseModel):
    """Request to submit or update availability."""

    respondent_name: str | None = None
    respondent_email: str | None = None
    availabilities: list[AvailabilitySlot]


# Helper functions


def get_poll_by_slug_or_404(slug: str, db: DBSession) -> AvailabilityPoll:
    """Get a poll by its public slug, or raise 404."""
    poll = db.query(AvailabilityPoll).filter(AvailabilityPoll.slug == slug).first()
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    return poll


def add_availabilities(
    response_id: int, 
    slots: list[AvailabilitySlot], 
    db: DBSession,
) -> None:
    """Add availability records for a response."""
    for slot in slots:
        db.add(PollAvailability(
            response_id=response_id,
            slot_start=slot.slot_start,
            slot_end=slot.slot_end,
            availability_level=slot.availability_level,
        ))


def poll_to_payload(poll: AvailabilityPoll) -> AvailabilityPollPayload:
    """Convert poll model to API payload."""
    return AvailabilityPollPayload(
        id=poll.id,
        slug=poll.slug,
        title=poll.title,
        description=poll.description,
        status=poll.status,
        datetime_start=poll.datetime_start,
        datetime_end=poll.datetime_end,
        slot_duration_minutes=poll.slot_duration_minutes,
        response_count=poll.response_count,
        created_at=poll.created_at,
        closes_at=poll.closes_at,
        finalized_at=poll.finalized_at,
        finalized_time=poll.finalized_time,
    )


def response_to_payload(response: PollResponse) -> PollResponsePayload:
    """Convert response model to API payload."""
    return PollResponsePayload(
        id=response.id,
        respondent_name=response.respondent_name,
        respondent_email=response.respondent_email,
        person_id=response.person_id,
        availabilities=[
            PollAvailabilityPayload(
                slot_start=a.slot_start,
                slot_end=a.slot_end,
                availability_level=a.availability_level,
            )
            for a in response.availabilities
        ],
        created_at=response.created_at,
        updated_at=response.updated_at,
    )


def poll_to_detail_payload(poll: AvailabilityPoll) -> AvailabilityPollDetailPayload:
    """Convert poll model to detailed API payload including responses."""
    base = poll_to_payload(poll)
    return AvailabilityPollDetailPayload(
        **base.model_dump(),
        responses=[response_to_payload(r) for r in poll.responses],
    )


def aggregate_availability(poll: AvailabilityPoll) -> list[SlotAggregation]:
    """Aggregate availability across all responses for each time slot.

    Only aggregates slots that match the poll's configured duration to exclude
    invalid historical data from before validation was added.
    """
    slot_data: dict[tuple[datetime, datetime], dict[str, Any]] = defaultdict(
        lambda: {
            "available": 0,
            "if_needed": 0,
            "respondents": [],
        }
    )

    expected_duration_seconds = poll.slot_duration_minutes * 60

    for response in poll.responses:
        name = response.respondent_name or "Anonymous"
        for avail in response.availabilities:
            # Skip slots that don't match the poll's configured duration
            slot_duration = (avail.slot_end - avail.slot_start).total_seconds()
            if slot_duration != expected_duration_seconds:
                continue

            # Skip misaligned slots (must start on valid boundary from poll start)
            offset_seconds = (avail.slot_start - poll.datetime_start).total_seconds()
            if offset_seconds % expected_duration_seconds != 0:
                continue

            key = (avail.slot_start, avail.slot_end)
            if avail.availability_level == AvailabilityLevel.AVAILABLE.value:
                slot_data[key]["available"] += 1
                slot_data[key]["respondents"].append(name)
            else:
                slot_data[key]["if_needed"] += 1

    result = []
    for (start, end), data in sorted(slot_data.items()):
        result.append(
            SlotAggregation(
                slot_start=start,
                slot_end=end,
                available_count=data["available"],
                if_needed_count=data["if_needed"],
                total_count=data["available"] + data["if_needed"],
                respondents=data["respondents"],
            )
        )

    return result


def validate_slot(slot: AvailabilitySlot, poll: AvailabilityPoll) -> None:
    """Validate that a slot is within the poll's time window and has valid values."""
    # Ensure slot_start < slot_end
    if slot.slot_start >= slot.slot_end:
        raise HTTPException(
            status_code=400,
            detail="Slot start must be before slot end",
        )

    # Ensure slot is within poll's datetime range
    if slot.slot_start < poll.datetime_start or slot.slot_end > poll.datetime_end:
        raise HTTPException(
            status_code=400,
            detail=f"Slot {slot.slot_start.isoformat()} is outside poll time range",
        )

    # Validate slot duration matches poll configuration
    slot_duration = (slot.slot_end - slot.slot_start).total_seconds() / 60
    if slot_duration != poll.slot_duration_minutes:
        raise HTTPException(
            status_code=400,
            detail=f"Slot duration must be {poll.slot_duration_minutes} minutes, got {int(slot_duration)}",
        )

    # Validate slot starts on a valid boundary (aligned to slot duration from poll start)
    offset_from_start = (slot.slot_start - poll.datetime_start).total_seconds() / 60
    if offset_from_start % poll.slot_duration_minutes != 0:
        raise HTTPException(
            status_code=400,
            detail=f"Slot must start on a {poll.slot_duration_minutes}-minute boundary from poll start",
        )

    # Validate availability level
    if slot.availability_level not in [AvailabilityLevel.AVAILABLE.value, AvailabilityLevel.IF_NEEDED.value]:
        raise HTTPException(
            status_code=400,
            detail="Invalid availability_level: must be 1 (available) or 2 (if needed)",
        )


MAX_NAME_LENGTH = 255


def sanitize_name(name: str | None) -> str | None:
    """Sanitize respondent name: trim, limit length, escape HTML."""
    if name is None:
        return None
    name = name.strip()
    if not name:
        return None
    if len(name) > MAX_NAME_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Name must be {MAX_NAME_LENGTH} characters or less",
        )
    # Escape HTML entities to prevent XSS
    return html.escape(name)


def deduplicate_slots(slots: list[AvailabilitySlot]) -> list[AvailabilitySlot]:
    """Remove duplicate slots, keeping the first occurrence of each time range."""
    seen: set[tuple[datetime, datetime]] = set()
    result = []
    for slot in slots:
        key = (slot.slot_start, slot.slot_end)
        if key not in seen:
            seen.add(key)
            result.append(slot)
    return result


# Public endpoints (no auth required)


@router.get("/respond/{slug}")
def get_poll_for_response(
    slug: str,
    db: DBSession = Depends(get_session),
) -> AvailabilityPollPayload:
    """Get poll details for responding (public, no auth)."""
    poll = get_poll_by_slug_or_404(slug, db)
    return poll_to_payload(poll)


@router.post("/respond/{slug}")
def submit_response(
    slug: str,
    data: PollResponseRequest,
    db: DBSession = Depends(get_session),
) -> dict:
    """Submit availability for a poll (public, no auth)."""
    poll = get_poll_by_slug_or_404(slug, db)

    if not poll.is_open:
        raise HTTPException(status_code=400, detail="Poll is closed")

    # Sanitize name
    sanitized_name = sanitize_name(data.respondent_name)

    # Deduplicate and validate all slots
    unique_slots = deduplicate_slots(data.availabilities)
    for slot in unique_slots:
        validate_slot(slot, poll)

    response = PollResponse(
        poll_id=poll.id,
        respondent_name=sanitized_name,
        respondent_email=data.respondent_email,
    )
    db.add(response)
    db.flush()

    add_availabilities(response.id, unique_slots, db)

    db.commit()
    db.refresh(response)

    return {
        "response_id": response.id,
        "edit_token": response.edit_token,
        "status": "created",
    }


@router.get("/respond/{slug}/response")
def get_response_by_token(
    slug: str,
    x_edit_token: str = Header(..., alias="X-Edit-Token"),
    db: DBSession = Depends(get_session),
) -> dict:
    """Get a response by its edit token (for pre-populating edit form)."""
    poll = get_poll_by_slug_or_404(slug, db)
    
    response = (
        db.query(PollResponse)
        .filter(PollResponse.poll_id == poll.id, PollResponse.edit_token == x_edit_token)
        .first()
    )
    
    if not response:
        raise HTTPException(status_code=404, detail="Response not found")
    
    return {
        "response_id": response.id,
        "respondent_name": response.respondent_name,
        "respondent_email": response.respondent_email,
        "availabilities": [
            {
                "slot_start": a.slot_start.isoformat(),
                "slot_end": a.slot_end.isoformat(),
                "availability_level": a.availability_level,
            }
            for a in response.availabilities
        ],
    }


@router.put("/respond/{slug}/{response_id}")
def update_response(
    slug: str,
    response_id: int,
    data: PollResponseRequest,
    x_edit_token: str = Header(..., alias="X-Edit-Token"),
    db: DBSession = Depends(get_session),
) -> dict:
    """Update an existing response (requires X-Edit-Token header)."""
    poll = get_poll_by_slug_or_404(slug, db)

    response = db.get(PollResponse, response_id)

    if not response or response.poll_id != poll.id:
        raise HTTPException(status_code=404, detail="Response not found")

    if not secrets.compare_digest(response.edit_token, x_edit_token):
        raise HTTPException(status_code=403, detail="Invalid edit token")

    if not poll.is_open:
        raise HTTPException(status_code=400, detail="Poll is closed")

    # Deduplicate and validate all slots
    unique_slots = deduplicate_slots(data.availabilities)
    for slot in unique_slots:
        validate_slot(slot, poll)

    # Update respondent info with sanitization
    if data.respondent_name is not None:
        response.respondent_name = sanitize_name(data.respondent_name)
    if data.respondent_email is not None:
        response.respondent_email = data.respondent_email

    # Replace availabilities atomically using bulk delete
    db.query(PollAvailability).filter(PollAvailability.response_id == response.id).delete()
    add_availabilities(response.id, unique_slots, db)

    response.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {"status": "updated"}


@router.get("/respond/{slug}/results")
def get_poll_results(
    slug: str,
    db: DBSession = Depends(get_session),
) -> dict:
    """Get aggregated poll results (public)."""
    poll = get_poll_by_slug_or_404(slug, db)
    aggregated = aggregate_availability(poll)

    # Find best slots (most available respondents)
    if aggregated:
        max_available = max(s.available_count for s in aggregated)
        best_slots = [s for s in aggregated if s.available_count == max_available]
    else:
        best_slots = []

    return {
        "poll": poll_to_payload(poll),
        "response_count": poll.response_count,
        "aggregated": [s.model_dump() for s in aggregated],
        "best_slots": [s.model_dump() for s in best_slots],
    }
