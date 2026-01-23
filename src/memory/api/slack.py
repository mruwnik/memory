"""API endpoints for Slack workspace, channel, and user management."""

import logging
import secrets
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from memory.api.auth import get_current_user
from memory.common import settings
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.slack import (
    SlackChannel,
    SlackOAuthState,
    SlackUser,
    SlackWorkspace,
)
from memory.common.db.models.people import Person

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
    user_count: int


class SlackWorkspaceUpdate(BaseModel):
    collect_messages: bool | None = None
    sync_interval_seconds: int | None = None


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


class SlackChannelUpdate(BaseModel):
    collect_messages: bool | None = None


class SlackUserResponse(BaseModel):
    id: str
    workspace_id: str
    username: str
    display_name: str | None
    real_name: str | None
    email: str | None
    is_bot: bool
    system_user_id: int | None
    person_id: int | None
    person_identifier: str | None


class SlackUserLinkRequest(BaseModel):
    system_user_id: int | None = None
    person_id: int | None = None


# --- Helper Functions ---


def get_user_workspace(
    db: Session, workspace_id: str, user: User
) -> SlackWorkspace:
    """Get a workspace, ensuring the user owns it."""
    workspace = db.get(SlackWorkspace, workspace_id)
    if not workspace or workspace.user_id != user.id:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


def workspace_to_response(ws: SlackWorkspace) -> SlackWorkspaceResponse:
    return SlackWorkspaceResponse(
        id=ws.id,
        name=ws.name,
        domain=ws.domain,
        collect_messages=ws.collect_messages,
        sync_interval_seconds=ws.sync_interval_seconds,
        last_sync_at=ws.last_sync_at.isoformat() if ws.last_sync_at else None,
        sync_error=ws.sync_error,
        channel_count=len(ws.channels),
        user_count=len(ws.users),
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
    )


def slack_user_to_response(user: SlackUser) -> SlackUserResponse:
    return SlackUserResponse(
        id=user.id,
        workspace_id=user.workspace_id,
        username=user.username,
        display_name=user.display_name,
        real_name=user.real_name,
        email=user.email,
        is_bot=user.is_bot,
        system_user_id=user.system_user_id,
        person_id=user.person_id,
        person_identifier=user.person.identifier if user.person else None,
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

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)

    # Clean up expired states for this user
    db.query(SlackOAuthState).filter(
        SlackOAuthState.user_id == user.id,
        SlackOAuthState.expires_at < datetime.now(timezone.utc),
    ).delete()

    # Store state in database with 10-minute expiration
    oauth_state = SlackOAuthState(
        state=state,
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.add(oauth_state)
    db.commit()

    # Build authorization URL
    params = {
        "client_id": settings.SLACK_CLIENT_ID,
        "scope": " ".join(SLACK_SCOPES),
        "redirect_uri": settings.SLACK_REDIRECT_URI,
        "state": state,
        "user_scope": " ".join(SLACK_SCOPES),  # Request user token scopes
    }

    auth_url = f"https://slack.com/oauth/v2/authorize?{urlencode(params)}"

    return {"authorization_url": auth_url, "state": state}


@router.get("/callback")
async def slack_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str | None = Query(None),
    db: Session = Depends(get_session),
):
    """Handle Slack OAuth2 callback.

    Exchanges the authorization code for tokens and creates/updates the workspace.
    """
    require_slack_configured()

    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")

    # Validate state from database (CSRF protection)
    oauth_state = db.query(SlackOAuthState).filter(
        SlackOAuthState.state == state
    ).first()

    if not oauth_state:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")

    # Check expiration
    if oauth_state.expires_at < datetime.now(timezone.utc):
        db.delete(oauth_state)
        db.commit()
        raise HTTPException(status_code=400, detail="State parameter expired")

    # Get user from the stored state
    user_id = oauth_state.user_id
    user = db.get(User, user_id)
    if not user:
        db.delete(oauth_state)
        db.commit()
        raise HTTPException(status_code=400, detail="User not found")

    # Delete the state (one-time use)
    db.delete(oauth_state)
    db.commit()

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

    if not data.get("ok"):
        error_msg = data.get("error", "Unknown error")
        logger.error(f"Slack OAuth error: {error_msg}")
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

    # Check if workspace already exists for this user
    existing = db.query(SlackWorkspace).filter(
        SlackWorkspace.id == team_id,
        SlackWorkspace.user_id == user_id,
    ).first()

    if existing:
        # Update existing workspace
        existing.access_token = access_token
        existing.refresh_token = authed_user.get("refresh_token")
        existing.scopes = authed_user.get("scope", "").split()
        existing.name = team_name
        existing.sync_error = None
        # Update token expiration if present
        if expires_in := authed_user.get("expires_in"):
            existing.token_expires_at = datetime.now(timezone.utc).replace(
                microsecond=0
            ) + timedelta(seconds=expires_in)
        else:
            existing.token_expires_at = None
        workspace = existing
    else:
        # Create new workspace
        workspace = SlackWorkspace(
            id=team_id,
            name=team_name,
            user_id=user_id,
            scopes=authed_user.get("scope", "").split(),
        )
        workspace.access_token = access_token
        workspace.refresh_token = authed_user.get("refresh_token")

        # Handle token expiration if present
        if expires_in := authed_user.get("expires_in"):
            workspace.token_expires_at = datetime.now(timezone.utc).replace(
                microsecond=0
            ) + timedelta(seconds=expires_in)

        db.add(workspace)

    db.commit()

    # Redirect to frontend with success
    frontend_url = f"{settings.SERVER_URL}/ui/sources?tab=slack&connected={team_id}"
    return RedirectResponse(url=frontend_url)


# --- Workspace Endpoints ---


@router.get("/workspaces")
def list_workspaces(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[SlackWorkspaceResponse]:
    """List Slack workspaces connected by the current user."""
    workspaces = db.query(SlackWorkspace).filter(
        SlackWorkspace.user_id == user.id
    ).all()
    return [workspace_to_response(w) for w in workspaces]


@router.get("/workspaces/{workspace_id}")
def get_workspace(
    workspace_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackWorkspaceResponse:
    """Get details of a specific workspace."""
    workspace = get_user_workspace(db, workspace_id, user)
    return workspace_to_response(workspace)


@router.patch("/workspaces/{workspace_id}")
def update_workspace(
    workspace_id: str,
    updates: SlackWorkspaceUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackWorkspaceResponse:
    """Update workspace settings."""
    workspace = get_user_workspace(db, workspace_id, user)

    if updates.collect_messages is not None:
        workspace.collect_messages = updates.collect_messages
    if updates.sync_interval_seconds is not None:
        workspace.sync_interval_seconds = updates.sync_interval_seconds

    db.commit()
    db.refresh(workspace)

    return workspace_to_response(workspace)


@router.delete("/workspaces/{workspace_id}")
def delete_workspace(
    workspace_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Disconnect a Slack workspace."""
    workspace = get_user_workspace(db, workspace_id, user)
    db.delete(workspace)
    db.commit()
    return {"status": "deleted", "workspace_id": workspace_id}


@router.post("/workspaces/{workspace_id}/sync")
def trigger_sync(
    workspace_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Trigger a manual sync for a workspace."""
    # Verify ownership
    get_user_workspace(db, workspace_id, user)

    # Import here to avoid circular imports
    from memory.common.celery_app import SYNC_SLACK_WORKSPACE
    from memory.common.celery_app import app as celery_app

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
    # Verify ownership
    get_user_workspace(db, workspace_id, user)

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

    # Verify user owns the workspace
    workspace = db.get(SlackWorkspace, channel.workspace_id)
    if not workspace or workspace.user_id != user.id:
        raise HTTPException(status_code=404, detail="Channel not found")

    channel.collect_messages = updates.collect_messages

    db.commit()
    db.refresh(channel)

    return channel_to_response(channel)


# --- User Endpoints ---


@router.get("/workspaces/{workspace_id}/users")
def list_slack_users(
    workspace_id: str,
    search: str | None = None,
    linked_only: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[SlackUserResponse]:
    """List Slack users in a workspace."""
    # Verify ownership
    get_user_workspace(db, workspace_id, user)

    query = db.query(SlackUser).filter(SlackUser.workspace_id == workspace_id)

    if search:
        search_term = f"%{search.lower()}%"
        query = query.filter(
            or_(
                SlackUser.username.ilike(search_term),
                SlackUser.display_name.ilike(search_term),
                SlackUser.real_name.ilike(search_term),
            )
        )

    if linked_only:
        query = query.filter(
            or_(
                SlackUser.system_user_id.isnot(None),
                SlackUser.person_id.isnot(None),
            )
        )

    users = query.order_by(SlackUser.display_name, SlackUser.username).limit(100).all()
    return [slack_user_to_response(u) for u in users]


@router.patch("/users/{slack_user_id}")
def link_slack_user(
    slack_user_id: str,
    data: SlackUserLinkRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackUserResponse:
    """Link a Slack user to a system user and/or person."""
    slack_user = db.get(SlackUser, slack_user_id)
    if not slack_user:
        raise HTTPException(status_code=404, detail="Slack user not found")

    # Verify user owns the workspace
    workspace = db.get(SlackWorkspace, slack_user.workspace_id)
    if not workspace or workspace.user_id != user.id:
        raise HTTPException(status_code=404, detail="Slack user not found")

    # Validate and link system user
    if data.system_user_id is not None:
        system_user = db.get(User, data.system_user_id)
        if not system_user:
            raise HTTPException(status_code=404, detail="System user not found")
        slack_user.system_user_id = data.system_user_id
    elif data.system_user_id is None and "system_user_id" in (data.model_fields_set or set()):
        slack_user.system_user_id = None

    # Validate and link person
    if data.person_id is not None:
        person = db.get(Person, data.person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")
        slack_user.person_id = data.person_id
    elif data.person_id is None and "person_id" in (data.model_fields_set or set()):
        slack_user.person_id = None

    db.commit()
    db.refresh(slack_user)

    return slack_user_to_response(slack_user)
