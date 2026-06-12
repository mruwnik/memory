"""MCP subserver for metadata and utility tools."""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, TypedDict, get_args, get_type_hints

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import qdrant
from memory.common.dates import parse_iso_datetime
from memory.common.celery_app import SEND_NOTIFICATION
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import DBSession, make_session
from memory.common.scopes import SCOPE_READ, SCOPE_WRITE
from memory.common.db.models import (
    APIKey,
    APIKeyType,
    EmailAccount,
    GithubAccount,
    ScheduledTask,
    SlackUserCredentials,
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
    # ``name`` is self-attested and must not be used for identity
    # decisions; ``verified_login`` is what GitHub reports for the
    # stored credentials and is None until verified (see issue #84).
    user_info["github_accounts"] = [
        {
            "name": a.name,
            "auth_type": a.auth_type,
            "verified_login": a.verified_login,
        }
        for a in github_accounts
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
                # str keys to match User.serialize() and the JSON-RPC wire shape.
                str(acct.id): acct.username for acct in person.discord_accounts
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
        if (size := sizes.get(collection))
    }


@meta_mcp.tool()
@visible_when()  # genuinely public utility — no PII, no side effects
async def get_current_time() -> dict:
    """Get the current time in UTC."""
    logger.info("get_current_time tool called")
    return {"current_time": datetime.now(timezone.utc).isoformat()}


@meta_mcp.tool()
@visible_when(require_scopes(SCOPE_READ))
async def get_user(generate_one_time_key: bool = False) -> dict:
    """Get information about the authenticated user.

    Args:
        generate_one_time_key: If True, generates a one-time API key for
            client operations. The key will be deleted after first use.
    """
    with make_session() as session:
        result = _get_current_user(session)

        if generate_one_time_key and result.get("authenticated"):
            # FIXME(security): non-admin callers can mint a one-time key here.
            # `_create_one_time_key` still caps the issued scopes to
            # `user.scopes ∩ granted_scopes`, so the minted key never carries
            # more scope than the caller already had — but minting key
            # material from a non-admin path is a confused-deputy shape that
            # should be gated on admin (on either the user or the OAuth
            # grant). Deliberately left open for now to unblock client
            # workflows; reinstate the admin gate when those are migrated.
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
            # Discord ids are int snowflakes in serialize(); coerce to str so the
            # identifier survives the celery JSON boundary as the str the
            # downstream .isdigit() classification expects (slack/email are
            # already strings here).
            channels.append(("discord", str(discord_id), {"discord_bot_id": discord_bots[0]}))

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


def format_notification_message(subject: str, message: str, details_url: str | None) -> str:
    """Render the on-wire notification body (bold subject + optional link)."""
    full_message = f"**{subject}**\n\n{message}"
    if details_url:
        full_message += f"\n\n[View details]({details_url})"
    return full_message


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


