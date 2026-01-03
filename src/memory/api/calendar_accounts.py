"""API endpoints for Calendar Account management."""

from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from memory.api.auth import get_current_user
from memory.common.calendar import get_events_in_range, parse_date_range
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.sources import CalendarAccount, GoogleAccount

router = APIRouter(prefix="/calendar-accounts", tags=["calendar-accounts"])


class CalendarAccountCreate(BaseModel):
    name: str
    calendar_type: Literal["caldav", "google"]
    # CalDAV fields
    caldav_url: str | None = None
    caldav_username: str | None = None
    caldav_password: str | None = None
    # Google Calendar fields
    google_account_id: int | None = None
    # Common fields
    calendar_ids: list[str] = []
    tags: list[str] = []
    check_interval: int = 15  # Minutes
    sync_past_days: int = 30
    sync_future_days: int = 90


class CalendarAccountUpdate(BaseModel):
    name: str | None = None
    caldav_url: str | None = None
    caldav_username: str | None = None
    caldav_password: str | None = None
    google_account_id: int | None = None
    calendar_ids: list[str] | None = None
    tags: list[str] | None = None
    check_interval: int | None = None
    sync_past_days: int | None = None
    sync_future_days: int | None = None
    active: bool | None = None


class GoogleAccountInfo(BaseModel):
    id: int
    name: str
    email: str


class CalendarEventResponse(BaseModel):
    id: int
    event_title: str
    start_time: str
    end_time: str | None
    all_day: bool
    location: str | None
    calendar_name: str | None
    recurrence_rule: str | None
    calendar_account_id: int | None
    attendees: list[str] | None = None
    meeting_link: str | None = None


class CalendarAccountResponse(BaseModel):
    id: int
    name: str
    calendar_type: str
    caldav_url: str | None
    caldav_username: str | None
    google_account_id: int | None
    google_account: GoogleAccountInfo | None
    calendar_ids: list[str]
    tags: list[str]
    check_interval: int
    sync_past_days: int
    sync_future_days: int
    last_sync_at: str | None
    sync_error: str | None
    active: bool
    created_at: str
    updated_at: str


def account_to_response(account: CalendarAccount) -> CalendarAccountResponse:
    """Convert a CalendarAccount model to a response model."""
    google_info = None
    if account.google_account:
        google_info = GoogleAccountInfo(
            id=cast(int, account.google_account.id),
            name=cast(str, account.google_account.name),
            email=cast(str, account.google_account.email),
        )

    return CalendarAccountResponse(
        id=cast(int, account.id),
        name=cast(str, account.name),
        calendar_type=cast(str, account.calendar_type),
        caldav_url=cast(str | None, account.caldav_url),
        caldav_username=cast(str | None, account.caldav_username),
        google_account_id=cast(int | None, account.google_account_id),
        google_account=google_info,
        calendar_ids=list(account.calendar_ids or []),
        tags=list(account.tags or []),
        check_interval=cast(int, account.check_interval),
        sync_past_days=cast(int, account.sync_past_days),
        sync_future_days=cast(int, account.sync_future_days),
        last_sync_at=account.last_sync_at.isoformat() if account.last_sync_at else None,
        sync_error=cast(str | None, account.sync_error),
        active=cast(bool, account.active),
        created_at=account.created_at.isoformat() if account.created_at else "",
        updated_at=account.updated_at.isoformat() if account.updated_at else "",
    )


@router.get("")
def list_accounts(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[CalendarAccountResponse]:
    """List all calendar accounts."""
    accounts = db.query(CalendarAccount).all()
    return [account_to_response(account) for account in accounts]


@router.post("")
def create_account(
    data: CalendarAccountCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> CalendarAccountResponse:
    """Create a new calendar account."""
    # Validate based on type
    if data.calendar_type == "caldav":
        if not data.caldav_url or not data.caldav_username or not data.caldav_password:
            raise HTTPException(
                status_code=400,
                detail="CalDAV accounts require caldav_url, caldav_username, and caldav_password",
            )
    elif data.calendar_type == "google":
        if not data.google_account_id:
            raise HTTPException(
                status_code=400,
                detail="Google Calendar accounts require google_account_id",
            )
        # Verify the Google account exists
        google_account = db.get(GoogleAccount, data.google_account_id)
        if not google_account:
            raise HTTPException(status_code=400, detail="Google account not found")

    account = CalendarAccount(
        name=data.name,
        calendar_type=data.calendar_type,
        caldav_url=data.caldav_url,
        caldav_username=data.caldav_username,
        caldav_password=data.caldav_password,
        google_account_id=data.google_account_id,
        calendar_ids=data.calendar_ids,
        tags=data.tags,
        check_interval=data.check_interval,
        sync_past_days=data.sync_past_days,
        sync_future_days=data.sync_future_days,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    return account_to_response(account)


@router.get("/{account_id}")
def get_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> CalendarAccountResponse:
    """Get a single calendar account."""
    account = db.get(CalendarAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account_to_response(account)


@router.patch("/{account_id}")
def update_account(
    account_id: int,
    updates: CalendarAccountUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> CalendarAccountResponse:
    """Update a calendar account."""
    account = db.get(CalendarAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if updates.name is not None:
        account.name = updates.name
    if updates.caldav_url is not None:
        account.caldav_url = updates.caldav_url
    if updates.caldav_username is not None:
        account.caldav_username = updates.caldav_username
    if updates.caldav_password is not None:
        account.caldav_password = updates.caldav_password
    if updates.google_account_id is not None:
        # Verify the Google account exists
        google_account = db.get(GoogleAccount, updates.google_account_id)
        if not google_account:
            raise HTTPException(status_code=400, detail="Google account not found")
        account.google_account_id = updates.google_account_id
    if updates.calendar_ids is not None:
        account.calendar_ids = updates.calendar_ids
    if updates.tags is not None:
        account.tags = updates.tags
    if updates.check_interval is not None:
        account.check_interval = updates.check_interval
    if updates.sync_past_days is not None:
        account.sync_past_days = updates.sync_past_days
    if updates.sync_future_days is not None:
        account.sync_future_days = updates.sync_future_days
    if updates.active is not None:
        account.active = updates.active

    db.commit()
    db.refresh(account)

    return account_to_response(account)


@router.delete("/{account_id}")
def delete_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Delete a calendar account."""
    account = db.get(CalendarAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    db.delete(account)
    db.commit()

    return {"status": "deleted"}


@router.post("/{account_id}/sync")
def trigger_sync(
    account_id: int,
    force_full: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Manually trigger a sync for a calendar account."""
    from memory.common.celery_app import app, SYNC_CALENDAR_ACCOUNT

    account = db.get(CalendarAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    task = app.send_task(
        SYNC_CALENDAR_ACCOUNT,
        args=[account_id],
        kwargs={"force_full": force_full},
    )

    return {"task_id": task.id, "status": "scheduled"}


@router.get("/events/upcoming")
def get_upcoming_events(
    days: int = Query(default=7, ge=1, le=365),
    limit: int = Query(default=10, ge=1, le=200),
    start_date: str | None = Query(default=None, description="ISO format start date"),
    end_date: str | None = Query(default=None, description="ISO format end date"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[CalendarEventResponse]:
    """Get calendar events within a date range.

    If start_date/end_date provided, uses those. Otherwise uses days from now.
    Expands recurring events to show future occurrences.
    """
    try:
        range_start, range_end = parse_date_range(start_date, end_date, days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    events = get_events_in_range(db, range_start, range_end, limit)

    return [
        CalendarEventResponse(
            id=e["id"],
            event_title=e["event_title"],
            start_time=e["start_time"],
            end_time=e["end_time"],
            all_day=e["all_day"],
            location=e["location"],
            calendar_name=e["calendar_name"],
            recurrence_rule=e["recurrence_rule"],
            calendar_account_id=e["calendar_account_id"],
            attendees=e.get("attendees"),
            meeting_link=e.get("meeting_link"),
        )
        for e in events
    ]
