"""MCP subserver for metadata and utility tools."""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, TypedDict, get_args, get_type_hints

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token

from memory.common import qdrant
from memory.common.celery_app import EXECUTE_SCHEDULED_CALL
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    APIKey,
    APIKeyType,
    EmailAccount,
    ScheduledLLMCall,
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


def _create_one_time_key(session: DBSession, user_session: UserSession) -> str:
    """Create a one-time API key for the user.

    Returns the key string (only available at creation time).
    The key includes OAuth scopes (read, write) plus the user's MCP tool scopes.
    """
    # Combine OAuth scopes with user's MCP tool scopes
    user_scopes = list(user_session.user.scopes or [])
    scopes = list(set(user_scopes) | {"read", "write"})

    one_time_key = APIKey.create(
        user_id=user_session.user.id,
        key_type=APIKeyType.ONE_TIME,
        name="MCP Client Operation",
        scopes=scopes,
    )
    session.add(one_time_key)
    session.commit()
    return one_time_key.key


def _get_current_user(session: DBSession) -> dict:
    """Get the current authenticated user from the access token."""
    user_session = _get_user_session_from_token(session)
    if not user_session:
        access_token = get_access_token()
        if not access_token:
            return {"authenticated": False}
        return {"authenticated": False, "error": "User not found"}

    user_info = user_session.user.serialize()

    # Add email accounts
    email_accounts = (
        session.query(EmailAccount)
        .filter(
            EmailAccount.user_id == user_session.user.id, EmailAccount.active.is_(True)
        )
        .all()
    )
    user_info["email_accounts"] = [
        {
            "email_address": a.email_address,
            "name": a.name,
            "account_type": a.account_type,
        }
        for a in email_accounts
    ]

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
        type_str = str(type_)
        if type_str.startswith("typing."):
            type_str = type_str[7:]
        elif len((parts := type_str.split("'"))) > 1:
            type_str = parts[1]
        return SchemaArg(type=type_str, description=description)
    except IndexError:
        logger.error(f"Error from annotation: {annotation}")
        return None


def get_schema(klass: type[SourceItem]) -> dict[str, SchemaArg]:
    if not hasattr(klass, "as_payload"):
        return {}

    if not (payload_type := get_type_hints(klass.as_payload).get("return")):
        return {}

    return {
        name: schema
        for name, arg in payload_type.__annotations__.items()
        if (schema := from_annotation(arg))
    }


@meta_mcp.tool()
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
        if (size := sizes.get(collection))
    }


@meta_mcp.tool()
async def get_current_time() -> dict:
    """Get the current time in UTC."""
    logger.info("get_current_time tool called")
    return {"current_time": datetime.now(timezone.utc).isoformat()}


@meta_mcp.tool()
async def get_user(generate_one_time_key: bool = False) -> dict:
    """Get information about the authenticated user.

    Args:
        generate_one_time_key: If True, generates a one-time API key for client operations.
                               The key will be deleted after first use.
    """
    with make_session() as session:
        result = _get_current_user(session)

        if generate_one_time_key and result.get("authenticated"):
            if user_session := _get_user_session_from_token(session):
                result["one_time_key"] = _create_one_time_key(session, user_session)

        return result


# --- Notification tools ---


def get_notification_channel(user_info: dict, preferred: str | None) -> tuple[str, str, dict[str, Any]] | None:
    """
    Determine which notification channel to use and the identifier for it.

    Priority order (unless overridden): Discord -> Slack -> Email

    Returns (channel_type, channel_identifier) or None if no channel available.
    """
    user = user_info.get("user", {})

    # Build available channels
    channels: list[tuple[str, str, dict[str, Any]]] = []  # (type, identifier, extra_data)

    # Discord
    discord_accounts = user.get("discord_accounts", {})
    discord_bots = user.get("discord_bots", [])
    if discord_accounts and discord_bots:
        discord_id = next(iter(discord_accounts.keys()), None)
        if discord_id:
            channels.append(("discord", discord_id, {"discord_bot_id": discord_bots[0]}))

    # Slack
    slack_accounts = user.get("slack_accounts", {})
    if slack_accounts:
        slack_id = next(iter(slack_accounts.keys()), None)
        if slack_id:
            channels.append(("slack", slack_id, {}))

    # Email
    email_accounts = user.get("email_accounts", [])
    if email_accounts:
        email_addr = email_accounts[0].get("email_address")
        if email_addr:
            channels.append(("email", email_addr, {"from_address": email_addr}))

    if not channels:
        return None

    # If preferred channel specified, try to find it
    if preferred:
        for ch_type, ch_id, extra in channels:
            if ch_type == preferred:
                return (ch_type, ch_id, extra)
        # Preferred not available
        return None

    # Return first available (priority order)
    return channels[0]


def create_notification(
    session,
    user_id: int,
    channel_type: str,
    channel_identifier: str,
    subject: str,
    message: str,
    details_url: str | None,
    scheduled_time: datetime,
    extra_data: dict[str, Any] | None = None,
) -> ScheduledLLMCall:
    """Create a notification record in the database."""
    # Format message with subject and details URL
    full_message = f"**{subject}**\n\n{message}"
    if details_url:
        full_message += f"\n\n[View details]({details_url})"

    data = {
        "notification_type": "notify_user",
        "subject": subject,
        "details_url": details_url,
    }
    if extra_data:
        data.update(extra_data)

    scheduled_call = ScheduledLLMCall(
        user_id=user_id,
        scheduled_time=scheduled_time,
        message=full_message,
        topic=subject,
        model=None,  # No LLM processing, send as-is
        channel_type=channel_type,
        channel_identifier=channel_identifier,
        data=data,
    )

    session.add(scheduled_call)
    return scheduled_call


@meta_mcp.tool()
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
        Dict with success status and delivery details
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

        # Parse scheduled time or use now for immediate
        if scheduled_time:
            try:
                scheduled_dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
                if scheduled_dt.tzinfo is not None:
                    scheduled_dt = scheduled_dt.astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError:
                raise ValueError("Invalid datetime format for scheduled_time")

            current_time_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            if scheduled_dt <= current_time_naive:
                raise ValueError("Scheduled time must be in the future")
        else:
            # Immediate: set scheduled_time to now
            scheduled_dt = datetime.now(timezone.utc).replace(tzinfo=None)

        # Create the notification record
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

        notification_id = notification.id

    # For immediate notifications, dispatch Celery task now
    if not scheduled_time:
        celery_app.send_task(EXECUTE_SCHEDULED_CALL, args=[notification_id])

    return {
        "success": True,
        "scheduled": bool(scheduled_time),
        "notification_id": notification_id,
        "channel_type": channel_type,
        "scheduled_time": scheduled_dt.isoformat() if scheduled_time else None,
    }
