"""Celery tasks for calendar syncing (CalDAV, Google Calendar)."""

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict, cast

import caldav
from sqlalchemy.orm import Session
from googleapiclient.discovery import build

from memory.common.celery_app import (
    SYNC_ALL_CALENDARS,
    SYNC_CALENDAR_ACCOUNT,
    SYNC_CALENDAR_EVENT,
    app,
)
from memory.common.db.connection import make_session
from memory.common.db.models import CalendarEvent
from memory.common.db.models.sources import CalendarAccount
from memory.parsers.google_drive import refresh_credentials
from memory.common.content_processing import (
    create_task_result,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


class EventData(TypedDict, total=False):
    """Structured event data for calendar sync.

    Required fields: title, start_time
    """

    title: str  # Required
    start_time: datetime  # Required
    end_time: datetime | None
    all_day: bool
    description: str
    location: str | None
    external_id: str | None
    calendar_name: str
    recurrence_rule: str | None
    attendees: list[str]
    meeting_link: str | None


# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------


def _get_ical_component(event: Any) -> Any:
    """Get the VEVENT component from a caldav event using icalendar."""
    ical = event.icalendar_instance
    for component in ical.walk():
        if component.name == "VEVENT":
            return component
    return None


def _get_vevent_attr(vevent: Any, attr: str, default: Any = None) -> Any:
    """Safely get an attribute from an icalendar VEVENT component.

    For rrule attributes, converts the rrule object to its string representation.
    """
    component = _get_ical_component(vevent)
    if component is None:
        return default

    value = component.get(attr)
    if value is None:
        return default

    # For date/datetime properties, extract the actual value
    if hasattr(value, "dt"):
        return value.dt

    # rrule is a special case - it's an object that needs string conversion
    if attr == "rrule" and value is not None:
        return str(value.to_ical().decode("utf-8"))

    return value


def _create_event_hash(event_data: EventData) -> bytes:
    """Create a hash for deduplication based on event content."""
    content = (
        f"{event_data.get('title', '')}"
        f"{event_data.get('start_time', '')}"
        f"{event_data.get('description', '')}"
    )
    return hashlib.sha256(content.encode()).digest()


def _serialize_event_data(event_data: EventData) -> dict[str, Any]:
    """Serialize event data for Celery task passing (datetime -> ISO string)."""
    serialized: dict[str, Any] = dict(event_data)
    if isinstance(serialized.get("start_time"), datetime):
        serialized["start_time"] = serialized["start_time"].isoformat()
    if isinstance(serialized.get("end_time"), datetime):
        serialized["end_time"] = serialized["end_time"].isoformat()
    return serialized


def _deserialize_event_data(data: dict[str, Any]) -> EventData:
    """Deserialize event data from Celery task (ISO string -> datetime)."""
    result = dict(data)
    if isinstance(result.get("start_time"), str):
        result["start_time"] = datetime.fromisoformat(result["start_time"])
    if isinstance(result.get("end_time"), str):
        result["end_time"] = datetime.fromisoformat(result["end_time"])
    return cast(EventData, result)


def _ensure_timezone(dt: datetime | None) -> datetime | None:
    """Ensure datetime has timezone info, defaulting to UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _create_calendar_event(
    account: CalendarAccount, event_data: EventData
) -> CalendarEvent:
    """Create a CalendarEvent model from parsed event data."""
    account_tags = cast(list[str], account.tags) or []

    metadata: dict[str, Any] = {}
    if event_data.get("attendees"):
        metadata["attendees"] = event_data["attendees"]
    if event_data.get("meeting_link"):
        metadata["meeting_link"] = event_data["meeting_link"]

    return CalendarEvent(
        modality="calendar",
        sha256=_create_event_hash(event_data),
        content=event_data.get("description", ""),
        event_title=event_data["title"],
        start_time=event_data["start_time"],
        end_time=event_data.get("end_time"),
        all_day=event_data.get("all_day", False),
        location=event_data.get("location"),
        recurrence_rule=event_data.get("recurrence_rule"),
        calendar_account_id=account.id,
        calendar_name=event_data.get("calendar_name"),
        external_id=event_data.get("external_id"),
        event_metadata=metadata,
        tags=account_tags,
    )


def _update_existing_event(existing: CalendarEvent, event_data: EventData) -> None:
    """Update an existing CalendarEvent with new data."""
    existing.event_title = event_data["title"]
    existing.start_time = event_data["start_time"]
    existing.end_time = event_data.get("end_time")
    existing.all_day = event_data.get("all_day", False)
    existing.location = event_data.get("location")
    existing.content = event_data.get("description", "")
    existing.recurrence_rule = event_data.get("recurrence_rule")

    metadata = existing.event_metadata or {}
    if event_data.get("attendees"):
        metadata["attendees"] = event_data["attendees"]
    if event_data.get("meeting_link"):
        metadata["meeting_link"] = event_data["meeting_link"]
    existing.event_metadata = metadata


# -----------------------------------------------------------------------------
# CalDAV parsing
# -----------------------------------------------------------------------------


def parse_caldav_event(vevent: Any, calendar_name: str) -> EventData:
    """Parse a CalDAV VEVENT into EventData format."""
    summary = _get_vevent_attr(vevent, "summary", "Untitled Event")
    dtstart = _get_vevent_attr(vevent, "dtstart")
    dtend = _get_vevent_attr(vevent, "dtend")

    if dtstart is None:
        raise ValueError("Calendar event missing required start time (dtstart)")

    # All-day events use date objects, timed events use datetime
    all_day = not hasattr(dtstart, "hour")

    if all_day:
        start_time = datetime.combine(dtstart, datetime.min.time()).replace(
            tzinfo=timezone.utc
        )
        end_time = (
            datetime.combine(dtend, datetime.min.time()).replace(tzinfo=timezone.utc)
            if dtend
            else None
        )
    else:
        start_time = dtstart if dtstart.tzinfo else dtstart.replace(tzinfo=timezone.utc)
        end_time = _ensure_timezone(dtend)

    # Parse attendees
    attendees: list[str] = []
    raw_attendees = _get_vevent_attr(vevent, "attendee")
    if raw_attendees:
        attendee_list = (
            raw_attendees if isinstance(raw_attendees, list) else [raw_attendees]
        )
        attendees = [str(a).replace("mailto:", "") for a in attendee_list]

    return EventData(
        title=str(summary),
        start_time=start_time,
        end_time=end_time,
        all_day=all_day,
        description=str(_get_vevent_attr(vevent, "description", "")),
        location=_get_vevent_attr(vevent, "location"),
        external_id=_get_vevent_attr(vevent, "uid"),
        calendar_name=calendar_name,
        recurrence_rule=_get_vevent_attr(vevent, "rrule"),
        attendees=attendees,
    )


def fetch_caldav_events(
    url: str,
    username: str,
    password: str,
    calendar_ids: list[str],
    since: datetime,
    until: datetime,
) -> list[EventData]:
    """Fetch events from a CalDAV server.

    Fetches ALL events (not date-filtered) to preserve recurring events with RRULE.
    Recurring events are expanded at query time, not sync time.
    """
    client = caldav.DAVClient(url=url, username=username, password=password)
    principal = client.principal()
    events: list[EventData] = []

    for calendar in principal.calendars():
        calendar_name = calendar.name or "Unknown"

        if calendar_ids and calendar.id not in calendar_ids:
            continue

        try:
            # Fetch ALL events to get recurring events with RRULE intact
            # We expand recurring events at query time, not sync time
            vevents = calendar.events()
            for vevent in vevents:
                try:
                    events.append(parse_caldav_event(vevent, calendar_name))
                except Exception as e:
                    logger.error(
                        f"Error parsing CalDAV event from {calendar_name}: {e}"
                    )
        except Exception as e:
            logger.error(f"Error fetching events from calendar {calendar_name}: {e}")

    return events


# -----------------------------------------------------------------------------
# Google Calendar parsing
# -----------------------------------------------------------------------------


def _parse_google_event(event: dict[str, Any], calendar_name: str) -> EventData:
    """Parse a Google Calendar event into EventData format."""
    start = event.get("start", {})
    end = event.get("end", {})
    all_day = "date" in start

    if all_day:
        start_time = datetime.fromisoformat(start["date"]).replace(tzinfo=timezone.utc)
        end_time = (
            datetime.fromisoformat(end["date"]).replace(tzinfo=timezone.utc)
            if end.get("date")
            else None
        )
    else:
        start_time = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
        end_time = (
            datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
            if end.get("dateTime")
            else None
        )

    # Extract attendee emails
    attendees = [a["email"] for a in event.get("attendees", []) if a.get("email")]

    # Extract meeting link from hangoutLink or conferenceData
    meeting_link = event.get("hangoutLink")
    if not meeting_link and "conferenceData" in event:
        for ep in event["conferenceData"].get("entryPoints", []):
            if ep.get("entryPointType") == "video":
                meeting_link = ep.get("uri")
                break

    # Extract recurrence rule (first one if multiple)
    recurrence = event.get("recurrence", [])
    recurrence_rule = recurrence[0] if recurrence else None

    return EventData(
        title=event.get("summary", "Untitled Event"),
        start_time=start_time,
        end_time=end_time,
        all_day=all_day,
        description=event.get("description", ""),
        location=event.get("location"),
        external_id=event.get("id"),
        calendar_name=calendar_name,
        recurrence_rule=recurrence_rule,
        attendees=attendees,
        meeting_link=meeting_link,
    )


def _fetch_google_calendar_events(
    account: CalendarAccount,
    calendar_ids: list[str],
    since: datetime,
    until: datetime,
    session: Session,
) -> list[EventData]:
    """Fetch events from Google Calendar using existing GoogleAccount."""
    google_account = account.google_account
    if not google_account:
        raise ValueError("Google Calendar account requires linked GoogleAccount")

    credentials = refresh_credentials(google_account, session)

    service = build("calendar", "v3", credentials=credentials)
    events: list[EventData] = []

    time_min = since.isoformat()
    time_max = until.isoformat()

    # Determine which calendars to sync
    calendars_to_sync = calendar_ids
    if not calendars_to_sync:
        try:
            calendar_list = service.calendarList().list().execute()
            calendars_to_sync = [cal["id"] for cal in calendar_list.get("items", [])]
        except Exception as e:
            logger.error(f"Error fetching calendar list, falling back to primary: {e}")
            calendars_to_sync = ["primary"]

    for calendar_id in calendars_to_sync:
        try:
            # Get calendar display name
            try:
                cal_info = service.calendars().get(calendarId=calendar_id).execute()
                calendar_name = cal_info.get("summary", calendar_id)
            except Exception:
                calendar_name = calendar_id

            events_result = (
                service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )

            for event in events_result.get("items", []):
                try:
                    events.append(_parse_google_event(event, calendar_name))
                except Exception as e:
                    logger.error(f"Error parsing Google event: {e}")

        except Exception as e:
            logger.error(f"Error fetching events from calendar {calendar_id}: {e}")

    return events


# -----------------------------------------------------------------------------
# Celery tasks
# -----------------------------------------------------------------------------


@app.task(name=SYNC_CALENDAR_EVENT)
@safe_task_execution
def sync_calendar_event(
    account_id: int, event_data_raw: dict[str, Any]
) -> dict[str, Any]:
    """Sync a single calendar event."""
    event_data = _deserialize_event_data(event_data_raw)
    logger.info(f"Syncing calendar event: {event_data.get('title')}")

    with make_session() as session:
        account = session.get(CalendarAccount, account_id)
        if not account:
            return {"status": "error", "error": "Account not found"}

        # Check for existing event by external_id
        external_id = event_data.get("external_id")
        existing = None
        if external_id:
            existing = (
                session.query(CalendarEvent)
                .filter(
                    CalendarEvent.calendar_account_id == account_id,
                    CalendarEvent.external_id == external_id,
                )
                .first()
            )

        if existing:
            _update_existing_event(existing, event_data)
            session.commit()
            return create_task_result(existing, "updated")

        calendar_event = _create_calendar_event(account, event_data)
        return process_content_item(calendar_event, session)


@app.task(name=SYNC_CALENDAR_ACCOUNT)
@safe_task_execution
def sync_calendar_account(account_id: int, force_full: bool = False) -> dict[str, Any]:
    """Sync all events from a calendar account."""
    logger.info(f"Syncing calendar account {account_id}")

    with make_session() as session:
        account = session.get(CalendarAccount, account_id)
        if not account or not cast(bool, account.active):
            return {"status": "error", "error": "Account not found or inactive"}

        now = datetime.now(timezone.utc)
        last_sync = cast(datetime | None, account.last_sync_at)

        # Skip if recently synced (unless force_full)
        if last_sync and not force_full:
            check_interval = cast(int, account.check_interval)
            if now - last_sync < timedelta(minutes=check_interval):
                return {"status": "skipped_recent_check", "account_id": account_id}

        # Calculate sync window
        sync_past = cast(int, account.sync_past_days)
        sync_future = cast(int, account.sync_future_days)
        since = now - timedelta(days=sync_past)
        until = now + timedelta(days=sync_future)

        calendar_type = cast(str, account.calendar_type)
        calendar_ids = cast(list[str], account.calendar_ids) or []

        try:
            if calendar_type == "caldav":
                caldav_url = cast(str, account.caldav_url)
                caldav_username = cast(str, account.caldav_username)
                caldav_password = cast(str, account.caldav_password)

                if not all([caldav_url, caldav_username, caldav_password]):
                    return {"status": "error", "error": "CalDAV credentials incomplete"}

                events = fetch_caldav_events(
                    caldav_url,
                    caldav_username,
                    caldav_password,
                    calendar_ids,
                    since,
                    until,
                )
            elif calendar_type == "google":
                events = _fetch_google_calendar_events(
                    account, calendar_ids, since, until, session
                )
            else:
                return {
                    "status": "error",
                    "error": f"Unknown calendar type: {calendar_type}",
                }

            # Queue sync tasks for each event
            task_ids = []
            for event_data in events:
                try:
                    serialized = _serialize_event_data(event_data)
                    task = sync_calendar_event.delay(account.id, serialized)
                    task_ids.append(task.id)
                except Exception as e:
                    logger.error(f"Error queuing event {event_data.get('title')}: {e}")

            account.last_sync_at = now
            account.sync_error = None
            session.commit()

        except Exception as e:
            account.sync_error = str(e)
            session.commit()
            raise

        return {
            "status": "completed",
            "sync_type": "full" if force_full else "incremental",
            "account_id": account_id,
            "account_name": account.name,
            "calendar_type": calendar_type,
            "events_synced": len(task_ids),
            "task_ids": task_ids,
        }


@app.task(name=SYNC_ALL_CALENDARS)
def sync_all_calendars(force_full: bool = False) -> list[dict[str, Any]]:
    """Trigger sync for all active calendar accounts."""
    with make_session() as session:
        active_accounts = (
            session.query(CalendarAccount).filter(CalendarAccount.active).all()
        )

        results = [
            {
                "account_id": account.id,
                "account_name": account.name,
                "calendar_type": account.calendar_type,
                "task_id": sync_calendar_account.delay(
                    account.id, force_full=force_full
                ).id,
            }
            for account in active_accounts
        ]

        logger.info(
            f"Scheduled {'full' if force_full else 'incremental'} sync "
            f"for {len(results)} active calendar accounts"
        )
        return results
