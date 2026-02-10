"""MCP subserver for metadata and utility tools."""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, TypedDict, get_args, get_type_hints

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from memory.common import qdrant
from memory.common.scopes import SCOPE_READ, SCOPE_WRITE
from memory.common.celery_app import EXECUTE_SCHEDULED_TASK
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    APIKey,
    APIKeyType,
    EmailAccount,
    GithubAccount,
    ScheduledTask,
    SlackUserCredentials,
    SourceItem,
    TaskExecution,
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
    scopes = list(set(user_scopes) | {SCOPE_READ, SCOPE_WRITE})

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

    # Add Slack accounts from credentials table
    slack_credentials = (
        session.query(SlackUserCredentials)
        .filter(SlackUserCredentials.user_id == user_session.user.id)
        .all()
    )
    user_info["slack_accounts"] = {
        cred.slack_user_id: cred.workspace_id for cred in slack_credentials
    }

    # Add GitHub accounts
    github_accounts = (
        session.query(GithubAccount)
        .filter(GithubAccount.user_id == user_session.user.id)
        .all()
    )
    user_info["github_accounts"] = [
        {"name": a.name, "auth_type": a.auth_type} for a in github_accounts
    ]

    # Enrich from linked Person record (via User.person or discord_accounts)
    person = user_session.user.person
    if not person:
        for discord_acct in user_session.user.discord_accounts:
            if not discord_acct.person:
                continue
            if not person:
                person = discord_acct.person
            elif discord_acct.person.id != person.id:
                logger.warning(
                    "User %s has discord accounts linked to multiple Person records; using first found",
                    user_session.user.id,
                )
                break

    if person:
        # Add Discord from Person if not already on User
        if not user_info.get("discord_accounts") and person.discord_accounts:
            user_info["discord_accounts"] = {
                acct.id: acct.username for acct in person.discord_accounts
            }

        # Add Slack from Person contact_info if no credentials found
        if not slack_credentials and person.contact_info:
            slack_info = person.contact_info.get("slack", {})
            for workspace_id, workspace_data in slack_info.items():
                if slack_user_id := workspace_data.get("user_id"):
                    user_info["slack_accounts"][slack_user_id] = workspace_id

        # Add GitHub from Person if no GithubAccount credentials found
        if not github_accounts and person.github_accounts:
            user_info["github_accounts"] = [
                {"username": acct.username, "id": acct.id} for acct in person.github_accounts
            ]
        elif not github_accounts and person.contact_info:
            github_username = person.contact_info.get("github") or person.contact_info.get("github_username")
            if github_username:
                user_info["github_accounts"] = [{"username": github_username}]

    # Fall back to user's own email if no dedicated email accounts
    if not email_accounts and user_session.user.email:
        user_info["email_accounts"] = [
            {
                "email_address": user_session.user.email,
                "name": user_session.user.name,
                "account_type": "primary",
            }
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
    scheduled_time: datetime | None,
    extra_data: dict[str, Any] | None = None,
) -> ScheduledTask:
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

    scheduled_task = ScheduledTask(
        user_id=user_id,
        task_type="notification",
        enabled=True,
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
            scheduled_dt = None

        # Create the notification record
        # For immediate sends, next_scheduled_time is None so the beat
        # scheduler won't also pick it up.
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

        # For immediate notifications, create execution in the same transaction
        # to avoid orphaned ScheduledTask records if execution creation fails
        execution_id = None
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if not scheduled_time:
            session.flush()  # Assign notification.id before referencing it
            execution = TaskExecution(
                task_id=notification.id,
                scheduled_time=now,
                status="pending",
            )
            session.add(execution)
            session.flush()  # Get the execution ID before commit
            execution_id = execution.id

        session.commit()
        notification_id = notification.id

    # Dispatch Celery task after successful commit
    if execution_id:
        celery_app.send_task(EXECUTE_SCHEDULED_TASK, args=[execution_id])

    return {
        "success": True,
        "scheduled": bool(scheduled_time),
        "notification_id": notification_id,
        "channel_type": channel_type,
        "scheduled_time": scheduled_dt.isoformat() if scheduled_dt else None,
    }


