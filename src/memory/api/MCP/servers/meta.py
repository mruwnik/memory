"""MCP subserver for metadata and utility tools."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, TypedDict, get_args, get_type_hints

import sqlalchemy as sa
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import qdrant, rate_limit, settings
from memory.common.collections import SESSION_COLLECTIONS
from memory.common.dates import parse_iso_datetime
from memory.common.celery_app import SEND_NOTIFICATION
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import DBSession, make_session
from memory.common.scopes import SCOPE_READ, SCOPE_WRITE
from memory.common.notify import (
    format_notification_message,
    get_notification_channel,
    user_info_for,
)
from memory.common.version import db_revision, version_info
from memory.common.db.models import (
    APIKey,
    APIKeyType,
    ScheduledTask,
    SourceItem,
    UserSession,
)

logger = logging.getLogger(__name__)

meta_mcp = FastMCP("memory-meta")


def _get_user_session_from_token(session: DBSession) -> UserSession | None:
    """Get the UserSession from the current access token.

    Returns None if no token or user session found.
    """
    access_token = get_access_token()
    if not access_token:
        return None

    user_session = session.get(UserSession, access_token.token)
    if not user_session or not user_session.user:
        return None
    return user_session


def _create_one_time_key(session: DBSession, user_session: UserSession) -> APIKey:
    """Create a one-time API key for the user.

    Returns the committed APIKey row; ``.key`` holds the secret (only
    available at creation time).

    The issued key carries ``user.scopes ∩ access_token.scopes`` (with a
    short-circuit for either side carrying admin — see the conditional
    below). In production today ``access_token.scopes`` from
    ``verify_token`` equals ``user.scopes ∪ {read, write}``, so the
    intersection collapses to ``user.scopes`` and the cap is a no-op for
    OAuth callers — but the math is here so a future ``verify_token``
    change that DOES emit narrower access-token scopes flows through
    correctly without a parallel edit. For direct-API-key callers
    (where ``access_token.scopes`` carries the API key's resolved
    scopes) the intersection is the live cap.
    """
    user_scopes = set(user_session.user.scopes or [])
    access_token = get_access_token()
    granted_scopes = set(access_token.scopes or []) if access_token else set()

    # Intersection (or union with user.scopes when the token holds admin)
    # rather than blind user.scopes assignment, so a future verify_token
    # change that DOES narrow access_token.scopes will be reflected here
    # without needing a parallel edit. Today both sides supply the same
    # information; this just keeps them coupled.
    if "*" in granted_scopes:
        effective = user_scopes
    elif "*" in user_scopes:
        effective = granted_scopes
    else:
        effective = user_scopes & granted_scopes

    scopes = sorted(effective)

    one_time_key = APIKey.create(
        user_id=user_session.user.id,
        key_type=APIKeyType.ONE_TIME,
        name="MCP Client Operation",
        scopes=scopes,
        # Unused keys must not become permanent credentials: is_valid()
        # rejects the key once the TTL elapses, and the maintenance task
        # purges it once it is also outside the mint rate window.
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=settings.ONE_TIME_KEY_TTL_SECONDS),
    )
    session.add(one_time_key)
    session.commit()
    return one_time_key


def _get_current_user(session: DBSession) -> dict:
    """Get the current authenticated user from the access token."""
    user_session = _get_user_session_from_token(session)
    if not user_session:
        access_token = get_access_token()
        if not access_token:
            return {"authenticated": False}
        return {"authenticated": False, "error": "User not found"}

    user_info = user_info_for(session, user_session.user)
    access_token = get_access_token()
    return {
        "authenticated": True,
        "token_type": "Bearer",
        "scopes": access_token.scopes if access_token else [],
        "client_id": access_token.client_id if access_token else None,
        "user": user_info,
        "public_key": user_session.user.ssh_public_key,
    }


# --- Metadata tools ---


class SchemaArg(TypedDict):
    type: str | None
    description: str | None


class CollectionMetadata(TypedDict):
    schema: dict[str, SchemaArg]
    size: int


def from_annotation(annotation: Annotated) -> SchemaArg | None:
    try:
        type_, description = get_args(annotation)
    except (IndexError, ValueError):
        logger.error(f"Error from annotation: {annotation}")
        return None
    type_str = str(type_)
    if type_str.startswith("typing."):
        type_str = type_str[7:]
    elif len((parts := type_str.split("'"))) > 1:
        type_str = parts[1]
    return SchemaArg(type=type_str, description=description)


def get_schema(klass: type[SourceItem]) -> dict[str, SchemaArg]:
    if not hasattr(klass, "as_payload"):
        return {}

    if not (payload_type := get_type_hints(klass.as_payload).get("return")):
        return {}

    # include_extras=True preserves Annotated[T, "desc"]; without it (or with raw
    # __annotations__) the model files' `from __future__ import annotations`
    # leaves us with ForwardRef strings whose get_args() returns ().
    return {
        name: schema
        for name, arg in get_type_hints(payload_type, include_extras=True).items()
        if (schema := from_annotation(arg))
    }


@meta_mcp.tool()
@visible_when(require_scopes(SCOPE_READ))
async def get_metadata_schemas() -> dict[str, CollectionMetadata]:
    """Get the metadata schema for each collection used in the knowledge base.

    These schemas can be used to filter the knowledge base.

    Returns: A mapping of collection names to their metadata schemas with field types and descriptions.

    Example:
    ```
    {
        "mail": {"subject": {"type": "str", "description": "The subject of the email."}},
        "chat": {"subject": {"type": "str", "description": "The subject of the chat message."}}
    }
    """
    client = qdrant.get_qdrant_client()
    sizes = qdrant.get_collection_sizes(client)
    schemas = defaultdict(dict)
    for klass in SourceItem.__subclasses__():
        for collection in klass.get_collections():
            schemas[collection].update(get_schema(klass))

    return {
        collection: CollectionMetadata(schema=schema, size=size)
        for collection, schema in schemas.items()
        # Session transcripts are owner-only-even-for-admins and only
        # reachable via claude_session_search; don't advertise their
        # schema or the cross-user point count here.
        if collection not in SESSION_COLLECTIONS and (size := sizes.get(collection))
    }


@meta_mcp.tool()
@visible_when()  # genuinely public utility — no PII, no side effects
async def get_current_time() -> dict:
    """Get the current time in UTC."""
    logger.info("get_current_time tool called")
    return {"current_time": datetime.now(timezone.utc).isoformat()}


@meta_mcp.tool()
@visible_when(require_scopes(SCOPE_READ))
async def get_user() -> dict:
    """Get information about the authenticated user and the server's version."""
    with make_session() as session:
        user = _get_current_user(session)
        db_rev = db_revision(session)
    if user["authenticated"]:
        user["server"] = await version_info(db_rev)
    return user


# Advisory-lock class for one-time key minting; pairs with the user id in
# pg_advisory_xact_lock(int, int) so unrelated lockers can't collide.
ONE_TIME_KEY_LOCK_CLASS = 0x07E1


def count_one_time_keys_in_window(
    session: DBSession, user_id: int, window_seconds: int
) -> int:
    """Count one-time keys minted for a user within the rolling window.

    Consumed keys are soft-deleted (``revoked=True``) rather than removed
    precisely so they still count here; a maintenance task purges them
    once ``created_at`` falls out of the window.

    The cutoff is computed from ``func.now()`` in-query: ``created_at`` is
    stamped by the Postgres clock (``server_default=func.now()``), so both
    sides of the comparison must share that clock — an app-side
    ``datetime.now()`` cutoff would shift the window under app/DB clock
    skew or a non-UTC DB ``TimeZone``.
    """
    return (
        session.query(APIKey)
        .filter(
            APIKey.user_id == user_id,
            APIKey.key_type == APIKeyType.ONE_TIME,
            APIKey.created_at > sa.func.now() - timedelta(seconds=window_seconds),
        )
        .count()
    )


# Tool descriptions are all MCP callers ever see — bake the deployment's
# actual configured limits in (settings are import-time constants), never
# setting *names* the caller can't resolve.
@meta_mcp.tool(
    description=(
        "Mint a one-time API key for client operations.\n\n"
        "The key stops working after its first use, and expires unused after "
        f"{settings.ONE_TIME_KEY_TTL_SECONDS} seconds. It carries the caller's "
        "own scopes capped to the OAuth grant — it can never hold more scope "
        "than the caller already had.\n\n"
        f"Minting is rate limited per user to {settings.ONE_TIME_KEY_RATE_LIMIT} "
        "over a rolling window; the response reports how many mints remain."
    )
)
@visible_when(require_scopes(SCOPE_WRITE))
async def create_one_time_key() -> dict:
    with make_session() as session:
        # FIXME(security): non-admin callers can mint a one-time key here.
        # `_create_one_time_key` still caps the issued scopes to
        # `user.scopes ∩ granted_scopes`, so the minted key never carries
        # more scope than the caller already had — but minting key
        # material from a non-admin path is a confused-deputy shape that
        # should be gated on admin (on either the user or the OAuth
        # grant). Deliberately left open for now to unblock client
        # workflows; reinstate the admin gate when those are migrated.
        user_session = _get_user_session_from_token(session)
        if not user_session:
            raise ValueError("Not authenticated")

        user_id = user_session.user.id
        limit, window_seconds = rate_limit.parse_limit(
            settings.ONE_TIME_KEY_RATE_LIMIT
        )
        # Serialize count-then-insert per user so concurrent mints can't
        # both pass the check; released automatically at COMMIT/ROLLBACK.
        session.execute(
            sa.select(sa.func.pg_advisory_xact_lock(ONE_TIME_KEY_LOCK_CLASS, user_id))
        )
        minted = count_one_time_keys_in_window(session, user_id, window_seconds)
        if minted >= limit:
            raise ValueError(
                "One-time key rate limit exceeded "
                f"({settings.ONE_TIME_KEY_RATE_LIMIT}, rolling window); try again later"
            )

        key = _create_one_time_key(session, user_session)
        return {
            "one_time_key": key.key,
            "expires_at": key.expires_at.isoformat() if key.expires_at else None,
            "mints_remaining": max(0, limit - minted - 1),
            "rate_limit": settings.ONE_TIME_KEY_RATE_LIMIT,
        }


# --- Notification tools ---


def create_notification(
    session,
    user_id: int,
    channel_type: str,
    channel_identifier: str,
    subject: str,
    message: str,
    details_url: str | None,
    scheduled_time: datetime | None,
    extra_data: dict[str, Any] | None = None,
) -> ScheduledTask:
    """Create a notification record in the database."""
    full_message = format_notification_message(subject, message, details_url)

    data = {
        "notification_type": "notify_user",
        "subject": subject,
        "details_url": details_url,
    }
    if extra_data:
        data.update(extra_data)

    scheduled_task = ScheduledTask(
        user_id=user_id,
        task_type="notification",
        next_scheduled_time=scheduled_time,
        message=full_message,
        topic=subject,
        notification_channel=channel_type,
        notification_target=channel_identifier,
        data=data,
    )

    session.add(scheduled_task)
    return scheduled_task


@meta_mcp.tool()
@visible_when(require_scopes(SCOPE_WRITE))
async def notify_user(
    subject: str,
    message: str,
    details_url: str | None = None,
    channel: Literal["discord", "slack", "email"] | None = None,
    scheduled_time: str | None = None,
) -> dict[str, Any]:
    """
    Send a notification to the current user.

    Delivery priority (unless overridden by channel parameter):
    1. Discord DM (if user has Discord account linked)
    2. Slack DM (if user has Slack account linked)
    3. Email (if user has email account configured)

    Args:
        subject: Notification subject/title (shown as bold header)
        message: Main message content
        details_url: Optional URL to full details (e.g., link to a note)
        channel: Override notification channel. If None, uses priority order above.
        scheduled_time: ISO datetime string (e.g., "2024-12-20T15:30:00Z").
                       If None, sends immediately. If set, schedules for later.

    Returns:
        For a scheduled send: ``{"success": True, "scheduled": True, ...}`` — a
        notification row is durably committed.
        For an immediate send: ``{"queued": True, "scheduled": False, ...}`` —
        the delivery task is enqueued but NOT confirmed delivered (fire-and-forget,
        no row/metric to read back).
    """
    if not subject:
        raise ValueError("Subject is required")
    if not message:
        raise ValueError("Message is required")

    with make_session() as session:
        current_user = _get_current_user(session)

        if not current_user.get("authenticated"):
            raise ValueError("Not authenticated")

        user_id = current_user.get("user", {}).get("user_id")
        if not user_id:
            raise ValueError("User not found")

        # Determine channel
        channel_info = get_notification_channel(current_user, channel)
        if not channel_info:
            if channel:
                raise ValueError(f"{channel.title()} account not configured")
            raise ValueError("No notification channel available (Discord, Slack, or Email)")

        channel_type, channel_identifier, extra_data = channel_info
        full_message = format_notification_message(subject, message, details_url)

        if scheduled_time:
            scheduled_dt = parse_iso_datetime(scheduled_time)
            if scheduled_dt is None:
                raise ValueError("Invalid datetime format for scheduled_time")
            if scheduled_dt.tzinfo is not None:
                scheduled_dt = scheduled_dt.astimezone(timezone.utc).replace(tzinfo=None)

            current_time_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            if scheduled_dt <= current_time_naive:
                raise ValueError("Scheduled time must be in the future")

            notification = create_notification(
                session=session,
                user_id=user_id,
                channel_type=channel_type,
                channel_identifier=channel_identifier,
                subject=subject,
                message=message,
                details_url=details_url,
                scheduled_time=scheduled_dt,
                extra_data=extra_data,
            )
            session.commit()
            return {
                "success": True,
                "scheduled": True,
                "notification_id": notification.id,
                "channel_type": channel_type,
                "scheduled_time": scheduled_dt.isoformat() + "Z",
            }

    # Immediate send: fire-and-forget, no ScheduledTask / TaskExecution rows.
    celery_app.send_task(
        SEND_NOTIFICATION,
        args=[channel_type, channel_identifier, full_message, user_id, subject, extra_data],
    )
    # "queued", not "success": the celery task is enqueued but delivery is not
    # confirmed here. There is no row/metric to read back, so the caller can't
    # learn whether the send actually landed — don't imply it did.
    return {
        "queued": True,
        "scheduled": False,
        "notification_id": None,
        "channel_type": channel_type,
        "scheduled_time": None,
    }


