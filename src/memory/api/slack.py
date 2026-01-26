"""API endpoints for Slack workspace and channel management.

Multi-user design:
- Workspaces are shared resources identified by Slack team_id
- Multiple users can connect their OAuth credentials to the same workspace
- Each user's credentials are stored in SlackUserCredentials
- Message collection uses any valid credential (collection is user-agnostic)
- Sending messages uses the caller's own credentials
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from memory.api.auth import get_current_user
from memory.common import settings
from memory.common.celery_app import app as celery_app, SYNC_SLACK_WORKSPACE
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.slack import (
    SlackChannel,
    SlackUserCredentials,
    SlackWorkspace,
)
from memory.common.db.models.source_items import SlackMessage
from memory.common.oauth_client import (
    generate_state,
    sign_state,
    validate_and_consume_state,
    store_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/slack", tags=["slack"])

# OAuth scopes for Slack user tokens
SLACK_SCOPES = [
    # Read messages from channels
    "channels:history",
    "groups:history",
    "mpim:history",
    "im:history",
    # List and read channel info
    "channels:read",
    "groups:read",
    "mpim:read",
    "im:read",
    # Read user info for mention resolution
    "users:read",
    "users:read.email",
    # Read reactions and files
    "reactions:read",
    "files:read",
    # Send messages and reactions (for MCP tools)
    "chat:write",
    "reactions:write",
]


# --- Response Models ---


class SlackWorkspaceResponse(BaseModel):
    id: str
    name: str
    domain: str | None
    collect_messages: bool
    sync_interval_seconds: int
    last_sync_at: str | None
    sync_error: str | None
    channel_count: int
    connected_users: int  # Number of users with credentials for this workspace
    user_connected: bool  # Whether the current user has credentials
    # Access control
    project_id: int | None
    sensitivity: str


class SlackWorkspaceUpdate(BaseModel):
    collect_messages: bool | None = None
    sync_interval_seconds: int | None = None
    # Access control
    project_id: int | None = None
    sensitivity: Literal["public", "basic", "internal", "confidential"] | None = None


class SlackChannelResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    channel_type: str
    is_private: bool
    is_archived: bool
    collect_messages: bool | None
    effective_collect: bool
    last_message_ts: str | None
    # Access control
    project_id: int | None
    sensitivity: str


class SlackChannelUpdate(BaseModel):
    collect_messages: bool | None = None
    # Access control
    project_id: int | None = None
    sensitivity: Literal["public", "basic", "internal", "confidential"] | None = None


# --- Helper Functions ---


def get_user_credentials(
    db: Session, workspace_id: str, user: User
) -> SlackUserCredentials | None:
    """Get the current user's credentials for a workspace."""
    return db.query(SlackUserCredentials).filter(
        SlackUserCredentials.workspace_id == workspace_id,
        SlackUserCredentials.user_id == user.id,
    ).first()


def get_workspace_with_access(
    db: Session, workspace_id: str, user: User
) -> SlackWorkspace:
    """Get a workspace, ensuring the user has credentials for it."""
    workspace = db.get(SlackWorkspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # User must have credentials to access workspace
    credentials = get_user_credentials(db, workspace_id, user)
    if not credentials:
        raise HTTPException(status_code=404, detail="Workspace not found")

    return workspace


def workspace_to_response(
    ws: SlackWorkspace, db: Session, current_user: User
) -> SlackWorkspaceResponse:
    """Convert workspace to response model."""
    channel_count = db.query(func.count(SlackChannel.id)).filter(
        SlackChannel.workspace_id == ws.id
    ).scalar() or 0

    connected_users = db.query(func.count(SlackUserCredentials.id)).filter(
        SlackUserCredentials.workspace_id == ws.id
    ).scalar() or 0

    user_connected = db.query(SlackUserCredentials).filter(
        SlackUserCredentials.workspace_id == ws.id,
        SlackUserCredentials.user_id == current_user.id,
    ).first() is not None

    return SlackWorkspaceResponse(
        id=ws.id,
        name=ws.name,
        domain=ws.domain,
        collect_messages=ws.collect_messages,
        sync_interval_seconds=ws.sync_interval_seconds,
        last_sync_at=ws.last_sync_at.isoformat() if ws.last_sync_at else None,
        sync_error=ws.sync_error,
        channel_count=channel_count,
        connected_users=connected_users,
        user_connected=user_connected,
        project_id=ws.project_id,
        sensitivity=ws.sensitivity or "basic",
    )


def channel_to_response(channel: SlackChannel) -> SlackChannelResponse:
    return SlackChannelResponse(
        id=channel.id,
        workspace_id=channel.workspace_id,
        name=channel.name,
        channel_type=channel.channel_type,
        is_private=channel.is_private,
        is_archived=channel.is_archived,
        collect_messages=channel.collect_messages,
        effective_collect=channel.should_collect,
        last_message_ts=channel.last_message_ts,
        project_id=channel.project_id,
        sensitivity=channel.sensitivity or "basic",
    )


def require_slack_configured() -> None:
    """Check that Slack OAuth is configured."""
    if not settings.SLACK_CLIENT_ID or not settings.SLACK_CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Slack integration not configured. Set SLACK_CLIENT_ID and SLACK_CLIENT_SECRET.",
        )


# --- OAuth Endpoints ---


@router.get("/authorize")
def authorize_slack(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Initiate Slack OAuth2 flow.

    Returns the URL to redirect the user to for Slack authorization.
    """
    require_slack_configured()
    logger.info(f"Starting Slack OAuth flow for user_id={user.id}")

    # Generate and store state for CSRF protection
    state = generate_state()
    logger.info(f"Generated state: {state[:16]}...")
    store_state(db, state, "slack", user.id)

    # Sign state with user-specific data to prevent interception attacks
    signed_state = sign_state(state, user.id)
    logger.info(f"Signed state: {signed_state[:20]}... (len={len(signed_state)})")

    # Build authorization URL
    params = {
        "client_id": settings.SLACK_CLIENT_ID,
        "scope": " ".join(SLACK_SCOPES),
        "redirect_uri": settings.SLACK_REDIRECT_URI,
        "state": signed_state,
        "user_scope": " ".join(SLACK_SCOPES),  # Request user token scopes
    }
    logger.info(f"OAuth redirect_uri: {settings.SLACK_REDIRECT_URI}")

    auth_url = f"https://slack.com/oauth/v2/authorize?{urlencode(params)}"
    logger.info(f"Redirecting to Slack OAuth: {auth_url[:100]}...")

    return {"authorization_url": auth_url, "state": signed_state}


@router.get("/callback")
async def slack_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str | None = Query(None),
    db: Session = Depends(get_session),
):
    """Handle Slack OAuth2 callback.

    Exchanges the authorization code for tokens and creates/updates credentials.
    If the workspace doesn't exist, creates it. If it does, just adds user's credentials.
    """
    logger.info(
        f"Slack OAuth callback received: code={code[:10]}..., "
        f"state={state[:20]}... (len={len(state)}), error={error}"
    )
    require_slack_configured()

    if error:
        logger.warning(f"Slack OAuth error from provider: {error}")
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")

    # Validate and consume state (handles expiration, signature verification, one-time use)
    logger.info("Validating OAuth state...")
    user_id = validate_and_consume_state(db, state, "slack")
    if not user_id:
        logger.error(f"State validation failed for state={state[:20]}...")
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")

    logger.info(f"State validated successfully, user_id={user_id}")

    # Get user from the stored state
    user = db.get(User, user_id)
    if not user:
        logger.error(f"User not found for user_id={user_id}")
        raise HTTPException(status_code=400, detail="User not found")

    logger.info(f"Exchanging code for tokens with redirect_uri={settings.SLACK_REDIRECT_URI}")
    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": settings.SLACK_CLIENT_ID,
                "client_secret": settings.SLACK_CLIENT_SECRET,
                "code": code,
                "redirect_uri": settings.SLACK_REDIRECT_URI,
            },
        )
        data = response.json()

    logger.info(f"Token exchange response ok={data.get('ok')}")
    if not data.get("ok"):
        error_msg = data.get("error", "Unknown error")
        logger.error(f"Slack OAuth token exchange error: {error_msg}, full_response={data}")
        raise HTTPException(status_code=400, detail=f"Slack OAuth failed: {error_msg}")

    # Extract user token (not bot token)
    authed_user = data.get("authed_user", {})
    access_token = authed_user.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No user access token received")

    # Extract workspace info
    team = data.get("team", {})
    team_id = team.get("id")
    team_name = team.get("name", "Unknown Workspace")

    if not team_id:
        raise HTTPException(status_code=400, detail="No team ID in response")

    # Get or create workspace (workspaces are shared across users)
    workspace = db.get(SlackWorkspace, team_id)
    if not workspace:
        workspace = SlackWorkspace(
            id=team_id,
            name=team_name,
        )
        db.add(workspace)
        db.flush()
    else:
        # Update workspace name if changed
        workspace.name = team_name

    # Calculate token expiration with 5-minute buffer to prevent race conditions
    token_expires_at = None
    expires_in = authed_user.get("expires_in")
    if expires_in and expires_in > 300:  # Only set if > 5 minutes
        token_expires_at = datetime.now(timezone.utc).replace(
            microsecond=0
        ) + timedelta(seconds=expires_in - 300)  # 5-minute buffer

    # Get or create user credentials for this workspace
    existing_creds = db.query(SlackUserCredentials).filter(
        SlackUserCredentials.workspace_id == team_id,
        SlackUserCredentials.user_id == user_id,
    ).first()

    if existing_creds:
        # Update existing credentials
        existing_creds.access_token = access_token
        existing_creds.refresh_token = authed_user.get("refresh_token")
        existing_creds.scopes = authed_user.get("scope", "").split()
        existing_creds.token_expires_at = token_expires_at
        existing_creds.slack_user_id = authed_user.get("id")
    else:
        # Create new credentials
        credentials = SlackUserCredentials(
            workspace_id=team_id,
            user_id=user_id,
            scopes=authed_user.get("scope", "").split(),
            token_expires_at=token_expires_at,
            slack_user_id=authed_user.get("id"),
        )
        credentials.access_token = access_token
        credentials.refresh_token = authed_user.get("refresh_token")
        db.add(credentials)

    # Clear any sync errors since we have fresh credentials
    workspace.sync_error = None

    db.commit()
    logger.info(
        f"Slack OAuth completed successfully: user_id={user_id}, "
        f"workspace_id={team_id}, workspace_name={team_name}"
    )

    # Return HTML that notifies opener via BroadcastChannel and redirects
    frontend_url = f"{settings.SERVER_URL}/ui/sources?tab=slack&connected={team_id}"
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Slack Connected</title></head>
    <body>
        <p>Slack workspace connected successfully. Redirecting...</p>
        <script>
            // Notify any listening windows via BroadcastChannel
            const channel = new BroadcastChannel('slack-oauth');
            channel.postMessage({{ type: 'oauth-complete', workspaceId: '{team_id}' }});
            channel.close();

            // If opened as popup, try to close; otherwise redirect
            if (window.opener) {{
                window.close();
            }} else {{
                window.location.href = '{frontend_url}';
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# --- Workspace Endpoints ---


@router.get("/workspaces")
def list_workspaces(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[SlackWorkspaceResponse]:
    """List Slack workspaces the current user has credentials for."""
    # Get workspace IDs where user has credentials
    user_workspace_ids = [
        cred.workspace_id
        for cred in db.query(SlackUserCredentials).filter(
            SlackUserCredentials.user_id == user.id
        ).all()
    ]

    if not user_workspace_ids:
        return []

    workspaces = db.query(SlackWorkspace).filter(
        SlackWorkspace.id.in_(user_workspace_ids)
    ).all()

    return [workspace_to_response(w, db, user) for w in workspaces]


@router.get("/workspaces/{workspace_id}")
def get_workspace(
    workspace_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackWorkspaceResponse:
    """Get details of a specific workspace."""
    workspace = get_workspace_with_access(db, workspace_id, user)
    return workspace_to_response(workspace, db, user)


@router.patch("/workspaces/{workspace_id}")
def update_workspace(
    workspace_id: str,
    updates: SlackWorkspaceUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackWorkspaceResponse:
    """Update workspace settings.

    Any connected user can update workspace settings (settings are shared).
    """
    workspace = get_workspace_with_access(db, workspace_id, user)

    if updates.collect_messages is not None:
        workspace.collect_messages = updates.collect_messages
    if updates.sync_interval_seconds is not None:
        workspace.sync_interval_seconds = updates.sync_interval_seconds
    if updates.project_id is not None:
        workspace.project_id = updates.project_id
    if updates.sensitivity is not None:
        workspace.sensitivity = updates.sensitivity

    db.commit()
    db.refresh(workspace)

    return workspace_to_response(workspace, db, user)


@router.delete("/workspaces/{workspace_id}")
def disconnect_workspace(
    workspace_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Disconnect user's credentials from a Slack workspace.

    This removes the user's OAuth credentials. The workspace and its messages
    are preserved if there are ingested messages (to avoid data loss) or if
    other users are still connected.
    """
    # Get user's credentials
    credentials = get_user_credentials(db, workspace_id, user)
    if not credentials:
        raise HTTPException(status_code=404, detail="Workspace not found")

    db.delete(credentials)
    db.commit()

    # Check remaining users and messages
    remaining_users = db.query(func.count(SlackUserCredentials.id)).filter(
        SlackUserCredentials.workspace_id == workspace_id
    ).scalar() or 0

    message_count = db.query(func.count(SlackMessage.id)).filter(
        SlackMessage.workspace_id == workspace_id
    ).scalar() or 0

    workspace_deleted = False
    if remaining_users == 0 and message_count == 0:
        # No users and no messages - safe to delete workspace
        workspace = db.get(SlackWorkspace, workspace_id)
        if workspace:
            db.delete(workspace)
            db.commit()
            workspace_deleted = True

    return {
        "status": "disconnected",
        "workspace_id": workspace_id,
        "workspace_deleted": workspace_deleted,
        "messages_preserved": message_count,
    }


@router.post("/workspaces/{workspace_id}/sync")
def trigger_sync(
    workspace_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Trigger a manual sync for a workspace."""
    # Verify user has access
    get_workspace_with_access(db, workspace_id, user)

    celery_app.send_task(SYNC_SLACK_WORKSPACE, args=[workspace_id])

    return {"status": "sync_triggered", "workspace_id": workspace_id}


# --- Channel Endpoints ---


@router.get("/workspaces/{workspace_id}/channels")
def list_channels(
    workspace_id: str,
    channel_type: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[SlackChannelResponse]:
    """List channels in a workspace."""
    # Verify user has access
    get_workspace_with_access(db, workspace_id, user)

    query = db.query(SlackChannel).filter(SlackChannel.workspace_id == workspace_id)

    if channel_type:
        query = query.filter(SlackChannel.channel_type == channel_type)

    channels = query.order_by(SlackChannel.name).all()
    return [channel_to_response(c) for c in channels]


@router.patch("/channels/{channel_id}")
def update_channel(
    channel_id: str,
    updates: SlackChannelUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackChannelResponse:
    """Update channel collection settings."""
    channel = db.get(SlackChannel, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Verify user has access to the workspace
    get_workspace_with_access(db, channel.workspace_id, user)

    channel.collect_messages = updates.collect_messages
    if updates.project_id is not None:
        channel.project_id = updates.project_id
    if updates.sensitivity is not None:
        channel.sensitivity = updates.sensitivity

    db.commit()
    db.refresh(channel)

    return channel_to_response(channel)
