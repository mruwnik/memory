"""API endpoints for Slack workspace and channel management.

Multi-user design:
- Workspaces are shared resources identified by Slack team_id
- Multiple users can connect their OAuth credentials to the same workspace
- Each user's credentials are stored in SlackUserCredentials
- Message collection uses any valid credential (collection is user-agnostic)
- Sending messages uses the caller's own credentials
"""

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from memory.api.auth import (
    assert_project_membership,
    get_current_user,
    resolve_user_filter,
)
from memory.common import settings
from memory.common.celery_app import (
    ADD_SLACK_MESSAGE,
    MARK_SLACK_MESSAGE_DELETED,
    SYNC_SLACK_WORKSPACE,
    UPDATE_SLACK_CHANNEL,
    UPDATE_SLACK_REACTIONS,
    app as celery_app,
)
from memory.common.db.connection import get_session, make_session
from memory.common.db.models import User
from memory.common.db.models.slack import (
    SlackApp,
    SlackChannel,
    SlackUserCredentials,
    SlackWorkspace,
)
from memory.common.db.models.source_items import SlackMessage
from memory.common.oauth_client import (
    log_corr_id,
    validate_and_consume_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/slack", tags=["slack"])

# Slack team IDs are documented as "T" followed by 8-12 uppercase
# alphanumerics. We use this for trust-boundary validation on values
# that reach the OAuth-callback HTML template — see SECURITY/MED
# f2feda6d (XSS via team_id in JS string literals).
_SLACK_TEAM_ID_PATTERN = re.compile(r"^T[A-Z0-9]{8,12}$")
# Channel/DM/Group IDs: C=public channel, D=DM, G=private/group/MPIM.
# Slack docs leave the suffix length open; allow up to 20 to cover newer
# workspaces while still rejecting obvious garbage / injection payloads.
_SLACK_CHANNEL_ID_PATTERN = re.compile(r"^[CDG][A-Z0-9]{8,20}$")

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


# --- SlackApp request/response models (§3.2, §4 S4) ---


class SlackAppCreate(BaseModel):
    """Body for POST /slack/apps."""

    name: str
    client_id: str


class SlackAppUpdate(BaseModel):
    """Body for PATCH /slack/apps/{id}.

    `setup_state` is intentionally NOT here — state advances only via the
    wizard endpoints (paste-secret + url_verification + test-message).
    Direct mutation would let an owner bypass verification.
    """

    name: str | None = None
    is_active: bool | None = None


class SlackAppAuthorizedUserAdd(BaseModel):
    """Body for POST /slack/apps/{id}/authorized-users."""

    user_id: int


class SlackAppAuthorizedUser(BaseModel):
    """Slim user shape returned in the authorized_users list. Email kept
    so the owner can identify who they have authorized."""

    id: int
    email: str
    name: str | None


class SlackAppResponse(BaseModel):
    """Public-facing SlackApp serialization.

    NEVER includes the encrypted secret blobs or the decrypted secrets —
    only boolean configuration markers. The owner uses the wizard
    endpoints to (re)set secrets; nobody reads them back through the API.
    See §4 S4.
    """

    id: int
    client_id: str
    name: str
    setup_state: str
    is_active: bool
    is_owner: bool
    created_by_user_id: int | None
    created_at: str | None
    updated_at: str | None
    client_secret_configured: bool
    signing_secret_configured: bool
    authorized_users: list[SlackAppAuthorizedUser]


# --- Helper Functions ---


def get_user_credentials(
    db: Session,
    workspace_id: str,
    user: User,
    slack_app_id: int | None = None,
) -> SlackUserCredentials | None:
    """Get the current user's credentials for a workspace.

    When ``slack_app_id`` is given, scopes to that SlackApp (multi-tenant
    correctness — two SlackApps can legitimately share a workspace per
    design doc §7 decision 5). When ``None``, returns any credential the
    user has for the workspace (legacy single-app caller).
    """
    query = db.query(SlackUserCredentials).filter(
        SlackUserCredentials.workspace_id == workspace_id,
        SlackUserCredentials.user_id == user.id,
    )
    if slack_app_id is not None:
        query = query.filter(SlackUserCredentials.slack_app_id == slack_app_id)
    return query.first()


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


@router.get("/callback/{slack_app_id}")
async def slack_callback_for_app(
    slack_app_id: int,
    code: str = Query(...),
    state: str = Query(...),
    error: str | None = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Multi-tenant OAuth callback (slack-changes.md §3.2 / §3.4 row 4).

    Looks up the SlackApp from the URL path and uses ITS encrypted
    client_id/client_secret for the token exchange — not the env-var
    credentials. State is bound to the user's session via the same
    ``Depends(get_current_user)`` rule used by /slack/callback (CSRF fix
    a5c9746d). Stored credentials carry the path's ``slack_app_id``.
    """
    logger.info(
        f"Slack OAuth callback received (multi-app): slack_app_id={slack_app_id}, "
        f"code_corr={log_corr_id(code)}, "
        f"state_corr={log_corr_id(state)}, len={len(state)}, error={error}"
    )

    if error:
        logger.warning(f"Slack OAuth error from provider: {error}")
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")

    slack_app = db.get(SlackApp, slack_app_id)
    if slack_app is None or not slack_app.is_active:
        raise HTTPException(status_code=404, detail="Slack app not found")
    if not slack_app.is_authorized(user):
        # Authorized users (and the owner) are the only ones who may complete
        # OAuth into this app's tenant.
        raise HTTPException(status_code=403, detail="Not authorized for this Slack app")
    client_id = slack_app.client_id
    client_secret = slack_app.client_secret
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail="Slack app client credentials not configured",
        )

    state_user_id = validate_and_consume_state(db, state, "slack")
    if not state_user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")
    if user.id != state_user_id:
        raise HTTPException(
            status_code=403, detail="OAuth state was issued for a different session"
        )

    redirect_uri = _callback_url_for(slack_app_id)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        data = response.json()

    if not data.get("ok"):
        error_msg = data.get("error", "Unknown error")
        logger.error(f"Slack OAuth token exchange error: {error_msg}")
        raise HTTPException(status_code=400, detail=f"Slack OAuth failed: {error_msg}")

    authed_user = data.get("authed_user", {})
    access_token = authed_user.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No user access token received")

    team = data.get("team", {})
    team_id = team.get("id")
    team_name = team.get("name", "Unknown Workspace")
    if not team_id or not _SLACK_TEAM_ID_PATTERN.fullmatch(team_id):
        raise HTTPException(status_code=400, detail="Malformed team ID in response")

    workspace = db.get(SlackWorkspace, team_id)
    if not workspace:
        workspace = SlackWorkspace(id=team_id, name=team_name)
        db.add(workspace)
        db.flush()
    else:
        workspace.name = team_name

    token_expires_at = None
    expires_in = authed_user.get("expires_in")
    if expires_in and expires_in > 300:
        token_expires_at = datetime.now(timezone.utc).replace(
            microsecond=0
        ) + timedelta(seconds=expires_in - 300)

    existing_creds = (
        db.query(SlackUserCredentials)
        .filter(
            SlackUserCredentials.slack_app_id == slack_app.id,
            SlackUserCredentials.workspace_id == team_id,
            SlackUserCredentials.user_id == user.id,
        )
        .first()
    )
    if existing_creds:
        existing_creds.access_token = access_token
        existing_creds.refresh_token = authed_user.get("refresh_token")
        existing_creds.scopes = authed_user.get("scope", "").split()
        existing_creds.token_expires_at = token_expires_at
        existing_creds.slack_user_id = authed_user.get("id")
    else:
        credentials = SlackUserCredentials(
            slack_app_id=slack_app.id,
            workspace_id=team_id,
            user_id=user.id,
            scopes=authed_user.get("scope", "").split(),
            token_expires_at=token_expires_at,
            slack_user_id=authed_user.get("id"),
        )
        credentials.access_token = access_token
        credentials.refresh_token = authed_user.get("refresh_token")
        db.add(credentials)

    workspace.sync_error = None
    db.commit()

    # Trigger an immediate workspace sync (slack-changes.md §3.7 backfill).
    celery_app.send_task(
        SYNC_SLACK_WORKSPACE,
        kwargs={"workspace_id": team_id, "slack_app_id": slack_app.id},
    )

    frontend_url = (
        f"{settings.SERVER_URL}/ui/sources?tab=slack&connected={team_id}"
    )
    workspace_id_js = json.dumps(team_id)
    frontend_url_js = json.dumps(frontend_url)
    channel_name_js = json.dumps(f"slack-oauth-{user.id}")
    # Even though `slack_app.id` is currently an int (safe to interpolate raw),
    # use json.dumps so every interpolated value in this template inherits the
    # same defense-in-depth contract — a future field-type change won't
    # silently regress the safety story.
    slack_app_id_js = json.dumps(slack_app.id)
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Slack Connected</title></head>
    <body>
        <p>Slack workspace connected successfully. Redirecting...</p>
        <script>
            const channel = new BroadcastChannel({channel_name_js});
            channel.postMessage({{ type: 'oauth-complete', workspaceId: {workspace_id_js}, slackAppId: {slack_app_id_js} }});
            channel.close();
            if (window.opener) {{
                window.close();
            }} else {{
                window.location.href = {frontend_url_js};
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# --- Workspace Endpoints ---


@router.get("/workspaces")
def list_workspaces(
    user_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[SlackWorkspaceResponse]:
    """List Slack workspaces. Admins can view any user's workspaces."""
    resolved_user_id = resolve_user_filter(user_id, user, db)

    # If resolved_user_id is None (admin viewing all), show all workspaces
    if resolved_user_id is None:
        workspaces = db.query(SlackWorkspace).all()
    else:
        # Get workspace IDs where the resolved user has credentials
        user_workspace_ids = [
            cred.workspace_id
            for cred in db.query(SlackUserCredentials).filter(
                SlackUserCredentials.user_id == resolved_user_id
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
        assert_project_membership(db, user, updates.project_id)
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
        assert_project_membership(db, user, updates.project_id)
        channel.project_id = updates.project_id
    if updates.sensitivity is not None:
        channel.sensitivity = updates.sensitivity

    db.commit()
    db.refresh(channel)

    return channel_to_response(channel)


# ===========================================================================
# /slack/apps CRUD — slack-changes.md §3.2 + §4 S4 / S11 / S12
# ===========================================================================
#
# Lifecycle: a SlackApp is created in `setup_state='draft'` here. The wizard
# endpoints (POST /slack/apps/{id}/client-secret etc.) advance state to
# 'signing_verified' and finally 'live'. Direct setup_state mutation via
# PATCH is intentionally not allowed — verification is a sequence of real
# Slack API checks, not a flag a privileged user can flip.
#
# Owner vs authorized rule (§4 S4):
# * Owner = `created_by_user_id` of the row.
# * Authorized = users in the `slack_app_users` join table.
# * Owner-only:    PATCH, DELETE, authorized-users add/remove,
#                  client-secret + signing-secret rotation (wizard).
# * Authorized:    OAuth flow (insert SlackUserCredentials), GET /apps,
#                  GET /apps/{id}.
# * Secrets are NEVER returned in any response — only the `*_configured`
#   booleans. The encrypted bytes never leave the server.


def slack_app_to_response(app: SlackApp, current_user: User) -> SlackAppResponse:
    """Public serialization of a SlackApp row.

    Drops both encrypted blobs and any decrypted secret values. Only the
    `*_configured` booleans signal whether the owner has supplied each
    secret yet (used by the wizard UI to decide which step to show next).
    """
    return SlackAppResponse(
        id=app.id,
        client_id=app.client_id,
        name=app.name,
        setup_state=app.setup_state,
        is_active=app.is_active,
        is_owner=app.is_owner(current_user),
        created_by_user_id=app.created_by_user_id,
        created_at=app.created_at.isoformat() if app.created_at else None,
        updated_at=app.updated_at.isoformat() if app.updated_at else None,
        client_secret_configured=app.client_secret_encrypted is not None,
        signing_secret_configured=app.signing_secret_encrypted is not None,
        authorized_users=[
            SlackAppAuthorizedUser(id=u.id, email=u.email, name=u.name)
            for u in app.authorized_users
        ],
    )


def get_slack_app_for_authorized_user(
    db: Session, app_id: int, user: User
) -> SlackApp:
    """Fetch a SlackApp the user is authorized to see.

    Authorized = owner OR in the authorized_users list. Anyone else gets
    a 404 (don't leak app existence — same response whether the row
    doesn't exist OR the user isn't authorized; §4 S11).
    """
    app = db.get(SlackApp, app_id)
    if app is None or not app.is_authorized(user):
        raise HTTPException(status_code=404, detail="Slack app not found")
    return app


def get_slack_app_for_owner(db: Session, app_id: int, user: User) -> SlackApp:
    """Fetch a SlackApp owned by the user.

    Authorized-but-not-owner gets 403 (we DO want to signal that the row
    exists and they're authorized, just not for this action). Non-authorized
    users continue to get 404 to avoid existence leakage.
    """
    app = db.get(SlackApp, app_id)
    if app is None or not app.is_authorized(user):
        raise HTTPException(status_code=404, detail="Slack app not found")
    if not app.is_owner(user):
        raise HTTPException(
            status_code=403, detail="Only the app's owner can perform this action"
        )
    return app


def list_slack_apps_for_user(db: Session, user: User) -> list[SlackApp]:
    """All SlackApps the user owns or has been authorized for.

    Implemented as a single union query rather than two queries + dedup
    so callers always get a stable, deduplicated result set.
    """
    owned = SlackApp.created_by_user_id == user.id
    authorized = SlackApp.id.in_(
        db.query(SlackApp.id)
        .join(SlackApp.authorized_users)
        .filter(User.id == user.id)
    )
    return db.query(SlackApp).filter(or_(owned, authorized)).all()


@router.post("/apps", response_model=SlackAppResponse, status_code=201)
def create_slack_app(
    body: SlackAppCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackAppResponse:
    """Create a draft SlackApp claim for a (new) Slack client_id.

    Returns 409 if another user has already claimed the same client_id.
    Per §3.1 squatting mitigation: the legitimate client_id owner can
    wait 24h for the stale-draft cleanup task to free the slot, or
    contact support.
    """
    app = SlackApp(
        name=body.name,
        client_id=body.client_id,
        created_by_user_id=user.id,
        setup_state="draft",
        is_active=True,
    )
    db.add(app)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Don't reveal whether the existing row is owned by this user vs
        # someone else — uniform 409 either way (§4 S11 information
        # discipline).
        raise HTTPException(
            status_code=409,
            detail=(
                "A Slack app with this client_id already exists. If you own "
                "this client_id, wait up to 24 hours for the squatting "
                "cleanup or contact support."
            ),
        )
    db.refresh(app)
    return slack_app_to_response(app, user)


@router.get("/apps", response_model=list[SlackAppResponse])
def list_slack_apps(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[SlackAppResponse]:
    """List apps the current user owns or has been authorized for."""
    apps = list_slack_apps_for_user(db, user)
    return [slack_app_to_response(a, user) for a in apps]


@router.get("/apps/{app_id}", response_model=SlackAppResponse)
def get_slack_app(
    app_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackAppResponse:
    """Get a single SlackApp by id (owner or authorized user only)."""
    app = get_slack_app_for_authorized_user(db, app_id, user)
    return slack_app_to_response(app, user)


@router.patch("/apps/{app_id}", response_model=SlackAppResponse)
def update_slack_app(
    app_id: int,
    body: SlackAppUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackAppResponse:
    """Update display name / is_active. Owner only."""
    app = get_slack_app_for_owner(db, app_id, user)

    if body.name is not None:
        app.name = body.name
    if body.is_active is not None:
        app.is_active = body.is_active

    db.commit()
    db.refresh(app)
    return slack_app_to_response(app, user)


@router.delete("/apps/{app_id}", status_code=204)
def delete_slack_app(
    app_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> None:
    """Delete a SlackApp. Owner only.

    Cascades to SlackUserCredentials (per the model's relationship
    cascade='all, delete-orphan'), so deleting the app also disconnects
    every workspace the app was used to authenticate.
    """
    app = get_slack_app_for_owner(db, app_id, user)
    db.delete(app)
    db.commit()


@router.post(
    "/apps/{app_id}/authorized-users",
    response_model=SlackAppResponse,
    status_code=201,
)
def add_authorized_user(
    app_id: int,
    body: SlackAppAuthorizedUserAdd,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackAppResponse:
    """Add a user to authorized_users. Owner only."""
    app = get_slack_app_for_owner(db, app_id, user)

    target = db.get(User, body.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    if target not in app.authorized_users:
        app.authorized_users.append(target)
        db.commit()
        db.refresh(app)

    return slack_app_to_response(app, user)


@router.delete(
    "/apps/{app_id}/authorized-users/{user_id}",
    response_model=SlackAppResponse,
)
def remove_authorized_user(
    app_id: int,
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackAppResponse:
    """Remove a user from authorized_users. Owner only.

    Removing the owner from authorized_users is a no-op (they're owner
    by virtue of created_by_user_id, not the join table — `is_authorized`
    checks both).
    """
    app = get_slack_app_for_owner(db, app_id, user)

    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    if target in app.authorized_users:
        app.authorized_users.remove(target)
        db.commit()
        db.refresh(app)

    return slack_app_to_response(app, user)


# ---------------------------------------------------------------------------
# Events endpoint (slack-changes.md §3.3) — push delivery from Slack
# ---------------------------------------------------------------------------

# Body cap is well above any realistic Slack event (Slack max is ~3MB but real
# events are small) and bounds the surface for pre-decrypt malicious traffic.
SLACK_EVENT_MAX_BODY_BYTES = 1_048_576  # 1 MiB
SLACK_EVENT_MAX_TS_SKEW_SECONDS = 5 * 60  # 5 min — Slack's documented window
SLACK_EVENT_REPLAY_TTL_SECONDS = 6 * 60  # 6 min — slightly larger than skew

# Per-app rate limit (per slack-changes.md §3.3 step 4).
SLACK_EVENT_PER_APP_BURST = 50

UNIFORM_REJECT_BODY = b"invalid request"
"""All security-failure responses use this identical body (security M5 — no
oracle: header issues, signature failures, replay hits all look the same to
the caller)."""


class _InMemorySlackStore:
    """Process-local TTL key/value store backing the events endpoint and
    wizard. Mimics the subset of the redis-py API used here so callers stay
    unchanged.

    Scope: this is a deliberate single-process design. Memory's API container
    runs a single uvicorn worker (see docker/api/Dockerfile), so the replay
    cache, token buckets, wizard nonces, and event counters all live inside
    one process. Multi-worker deployments would lose dedup correctness across
    workers — call out before adding ``--workers N`` to the API service.

    Implementation notes:
    - Lazy expiry on access (no background scrubber).
    - Single ``RLock`` for thread safety; uvicorn's threadpool path can call
      sync routes from worker threads.
    - Methods return bytes for ``get`` to match redis-py's behavior (callers
      already handle the bytes/str ambiguity).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # value: stored bytes; expires_at: monotonic seconds (or None for no TTL).
        self._kv: dict[str, tuple[bytes, float | None]] = {}
        # Hash storage for token-bucket state. Same expiry semantics.
        self._hashes: dict[str, tuple[dict[str, str], float | None]] = {}

    @staticmethod
    def _coerce_bytes(value: Any) -> bytes:
        if isinstance(value, bytes):
            return value
        if isinstance(value, (int, float)):
            return str(value).encode()
        return str(value).encode()

    def _get_unexpired(self, store: dict, key: str, now: float):
        entry = store.get(key)
        if entry is None:
            return None
        _, expires_at = entry
        if expires_at is not None and expires_at <= now:
            store.pop(key, None)
            return None
        return entry

    def get(self, key: str):
        with self._lock:
            entry = self._get_unexpired(self._kv, key, time.monotonic())
            return entry[0] if entry is not None else None

    def set(
        self,
        key: str,
        value: Any,
        ex: int | None = None,
        nx: bool = False,
    ):
        with self._lock:
            now = time.monotonic()
            existing = self._get_unexpired(self._kv, key, now)
            if nx and existing is not None:
                return None
            expires_at = now + ex if ex is not None else None
            self._kv[key] = (self._coerce_bytes(value), expires_at)
            return True

    def delete(self, key: str) -> int:
        with self._lock:
            return 1 if self._kv.pop(key, None) is not None else 0

    def incr(self, key: str) -> int:
        with self._lock:
            now = time.monotonic()
            entry = self._get_unexpired(self._kv, key, now)
            current = int(entry[0]) if entry is not None else 0
            new_value = current + 1
            expires_at = entry[1] if entry is not None else None
            self._kv[key] = (str(new_value).encode(), expires_at)
            return new_value

    def expire(self, key: str, seconds: int) -> int:
        with self._lock:
            now = time.monotonic()
            entry = self._get_unexpired(self._kv, key, now)
            if entry is not None:
                self._kv[key] = (entry[0], now + seconds)
                return 1
            hentry = self._get_unexpired(self._hashes, key, now)
            if hentry is not None:
                self._hashes[key] = (hentry[0], now + seconds)
                return 1
            return 0

    def hgetall(self, key: str) -> dict[bytes, bytes]:
        with self._lock:
            entry = self._get_unexpired(self._hashes, key, time.monotonic())
            if entry is None:
                return {}
            mapping, _ = entry
            return {k.encode(): v.encode() for k, v in mapping.items()}

    def hset(self, key: str, mapping: dict[str, str]) -> int:
        with self._lock:
            now = time.monotonic()
            entry = self._get_unexpired(self._hashes, key, now)
            current_mapping = dict(entry[0]) if entry is not None else {}
            expires_at = entry[1] if entry is not None else None
            current_mapping.update({k: str(v) for k, v in mapping.items()})
            self._hashes[key] = (current_mapping, expires_at)
            return len(mapping)

    def pipeline(self):
        return _InMemoryPipeline(self)


class _InMemoryPipeline:
    """Tiny shim mimicking redis-py's pipeline API for the only call shape
    used here: queue ``hgetall`` + ``expire``, then ``execute`` returns the
    list of results in order."""

    def __init__(self, store: "_InMemorySlackStore") -> None:
        self._store = store
        self._ops: list[tuple[str, tuple, dict]] = []

    def hgetall(self, key: str):
        self._ops.append(("hgetall", (key,), {}))
        return self

    def expire(self, key: str, seconds: int):
        self._ops.append(("expire", (key, seconds), {}))
        return self

    def incr(self, key: str):
        self._ops.append(("incr", (key,), {}))
        return self

    def hset(self, key: str, mapping: dict[str, str]):
        self._ops.append(("hset", (key,), {"mapping": mapping}))
        return self

    def execute(self) -> list[Any]:
        results: list[Any] = []
        for name, args, kwargs in self._ops:
            method = getattr(self._store, name)
            results.append(method(*args, **kwargs))
        self._ops.clear()
        return results


_slack_inmem_store = _InMemorySlackStore()


def _slack_store() -> _InMemorySlackStore:
    """Process-local in-memory store used for replay cache, token buckets,
    wizard nonces, and the rolling event counter. See ``_InMemorySlackStore``
    for the deployment scope this assumes.
    """
    return _slack_inmem_store


def _events_logger() -> logging.Logger:
    """Dedicated logger for event ingestion — keeps the whitelist discipline
    (security S9): only ``slack_app_id``, ``event_type``, ``hmac_ok``,
    ``ts_skew_seconds``, ``body_sha256`` may appear."""
    return logging.getLogger("memory.api.slack.events")


def _uniform_401() -> Response:
    return Response(
        content=UNIFORM_REJECT_BODY,
        status_code=401,
        media_type="application/octet-stream",
    )


def _check_token_bucket(
    client: "_InMemorySlackStore", key: str, capacity: int, refill_per_sec: float
) -> bool:
    """Atomic-ish token-bucket check via Redis.

    Returns True if a token was consumed (request allowed). Uses a
    millisecond-resolution clock and lazy refill; non-strict (a small
    burst above ``capacity`` is possible under contention but bounded).
    Acceptable for DoS mitigation — we don't need exact fairness.
    """
    now_ms = int(time.time() * 1000)
    pipe = client.pipeline()
    pipe.hgetall(key)
    pipe.expire(key, 120)
    raw, _ = pipe.execute()
    state = {k.decode(): v.decode() for k, v in raw.items()} if raw else {}

    last_ms = int(state.get("ts", now_ms))
    tokens = float(state.get("tokens", capacity))
    elapsed = max(now_ms - last_ms, 0) / 1000.0
    tokens = min(capacity, tokens + elapsed * refill_per_sec)

    if tokens < 1.0:
        client.hset(key, mapping={"tokens": f"{tokens:.4f}", "ts": str(now_ms)})
        return False
    tokens -= 1.0
    client.hset(key, mapping={"tokens": f"{tokens:.4f}", "ts": str(now_ms)})
    return True


def _verify_slack_signature(
    signing_secret: str, request_ts: str, body: bytes, signature_header: str
) -> bool:
    """HMAC-SHA256 verification of the Slack request envelope.

    Per Slack's docs the basestring is ``v0:{timestamp}:{body}``. The
    signature header is ``v0=<hex>``. ``hmac.compare_digest`` is used to
    avoid timing leaks; the ``v0=`` prefix is verified explicitly.
    """
    if not signature_header.startswith("v0="):
        return False
    expected_hex = signature_header[len("v0="):]
    basestring = f"v0:{request_ts}:".encode() + body
    digest = hmac.new(
        signing_secret.encode("utf-8"),
        basestring,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(digest, expected_hex)


def _bump_event_counter(client: "_InMemorySlackStore", slack_app_id: int) -> None:
    """Bump the rolling-24h counter the watchdog reads.

    Implementation: a single counter with a 24h TTL — refreshed on each
    write. So a fully idle app's counter expires after 24h, which is the
    exact signal slack_token_health_check looks for.
    """
    key = f"slack_events_count:{slack_app_id}"
    pipe = client.pipeline()
    pipe.incr(key)
    pipe.expire(key, 24 * 3600)
    pipe.execute()


def _wizard_nonce_redis_key(slack_app_id: int) -> str:
    """Wizard nonce is stored as a hash keyed on slack_app_id, with the
    initiating user_id as the value. We accept any active nonce for the
    app — user-binding lives in the OAuth state for /slack/callback,
    not here."""
    return f"slack_wizard_nonce:{slack_app_id}"


def _slack_app_for_event(db: Session, slack_app_id: int) -> SlackApp | None:
    """Look up the SlackApp; return None on miss so callers fail closed."""
    if slack_app_id <= 0:
        return None
    return db.get(SlackApp, slack_app_id)


def _dispatch_event_callback(
    body: dict, slack_app_id: int
) -> dict[str, str]:
    """Branch on event.type and enqueue the right celery task.

    Returns a small status dict for logging — Slack only needs the 200.

    Defense-in-depth: HMAC signature proves the body came from Slack with the
    correct signing secret, but it does not prove the *content* is well-formed.
    A Slack-side compromise, a replay against a stale signing secret in a
    different format, or a malformed event from a buggy Slack app could put
    arbitrary strings into our Celery task kwargs (workspace_id flows through
    to a SlackMessage Text column with no DB-level shape constraint). We
    validate workspace_id and channel_id here so garbage gets rejected at the
    dispatcher rather than wasting Celery cycles + log spam in add_slack_message.
    """
    event = body.get("event") or {}
    event_type = event.get("type") or "unknown"
    subtype = event.get("subtype")
    workspace_id = body.get("team_id")

    # `event.channel` is sometimes a dict (channel_* events deliver a full
    # channel object), sometimes a string (message events). Reactions carry
    # their channel under `event.item.channel` instead. Normalize per source.
    raw_channel = event.get("channel")
    if isinstance(raw_channel, dict):
        primary_channel_id = raw_channel.get("id")
    else:
        primary_channel_id = raw_channel
    reaction_item = event.get("item") or {}
    reaction_channel_id = (
        reaction_item.get("channel") if isinstance(reaction_item, dict) else None
    )
    # Pick the right channel_id for this event type so the validation below
    # rejects events that lack a usable channel_id at the right source.
    if event_type in ("reaction_added", "reaction_removed"):
        channel_id = reaction_channel_id
    else:
        channel_id = primary_channel_id

    if not workspace_id or not _SLACK_TEAM_ID_PATTERN.fullmatch(workspace_id):
        logger.warning(
            f"Rejecting event with malformed team_id "
            f"slack_app_id={slack_app_id} event_type={event_type}"
        )
        return {"dispatch": "rejected_team_id"}
    # channel_id is required for every dispatched task type below.
    if not channel_id or not _SLACK_CHANNEL_ID_PATTERN.fullmatch(channel_id):
        logger.warning(
            f"Rejecting event with malformed channel_id "
            f"slack_app_id={slack_app_id} event_type={event_type}"
        )
        return {"dispatch": "rejected_channel_id"}

    if event_type == "message":
        if subtype == "message_deleted":
            celery_app.send_task(
                MARK_SLACK_MESSAGE_DELETED,
                kwargs={
                    "workspace_id": workspace_id,
                    "channel_id": channel_id,
                    "message_ts": (event.get("deleted_ts") or event.get("ts")),
                    "slack_app_id": slack_app_id,
                },
            )
            return {"dispatch": "mark_deleted"}
        # Both "message" and "message_changed" go through ADD_SLACK_MESSAGE,
        # which carries the merge logic from B-pre-1/B-pre-2.
        message = event if subtype != "message_changed" else event.get("message", {})
        celery_app.send_task(
            ADD_SLACK_MESSAGE,
            kwargs={
                "workspace_id": workspace_id,
                "channel_id": channel_id,
                "message_ts": message.get("ts"),
                "author_id": message.get("user"),
                "content": message.get("text", ""),
                "thread_ts": message.get("thread_ts"),
                "reply_count": message.get("reply_count"),
                "subtype": subtype,
                "edited_ts": message.get("edited", {}).get("ts"),
                "reactions": message.get("reactions"),
                "files": message.get("files"),
                "slack_app_id": slack_app_id,
            },
        )
        return {"dispatch": "add_message"}

    if event_type in ("reaction_added", "reaction_removed"):
        celery_app.send_task(
            UPDATE_SLACK_REACTIONS,
            kwargs={
                "workspace_id": workspace_id,
                "channel_id": channel_id,
                "message_ts": reaction_item.get("ts"),
                "reactions": event.get("reactions"),
                "slack_app_id": slack_app_id,
            },
        )
        return {"dispatch": "update_reactions"}

    if event_type.startswith("channel_"):
        # channel_id is already normalized at the top regardless of whether
        # `event.channel` was a string or a dict. The full channel object
        # (when present) still ships as channel_payload.
        channel_payload = raw_channel if isinstance(raw_channel, dict) else {}
        celery_app.send_task(
            UPDATE_SLACK_CHANNEL,
            kwargs={
                "workspace_id": workspace_id,
                "channel_id": channel_id,
                "channel_payload": channel_payload,
                "slack_app_id": slack_app_id,
            },
        )
        return {"dispatch": "update_channel"}

    return {"dispatch": "ignored"}


def _check_test_message_token(
    client: "_InMemorySlackStore", slack_app_id: int, body: dict
) -> None:
    """Wizard step-6 hook: if there's an active test-message token in Redis
    AND a `message` event for this app contains it, advance the SlackApp
    state to ``live`` (slack-changes.md §3.4 row 6).

    Idempotent: removes the token on match so duplicate events don't keep
    re-flipping state. No-ops when there's no token waiting.
    """
    event = body.get("event") or {}
    if event.get("type") != "message":
        return
    text = event.get("text") or ""
    if not text:
        return
    key = f"slack_wizard_test_token:{slack_app_id}"
    raw = client.get(key)
    if raw is None:
        return
    token = raw.decode() if isinstance(raw, bytes) else str(raw)
    if token not in text:
        return
    # Atomically claim the match so a duplicate event doesn't trigger twice.
    if client.delete(key) == 0:
        return
    with make_session() as session:
        app = session.get(SlackApp, slack_app_id)
        if app is None:
            return
        app.setup_state = "live"
        session.commit()


@router.post("/events/{slack_app_id}")
async def slack_events(
    slack_app_id: int,
    request: Request,
    db: Session = Depends(get_session),
) -> Response:
    """Slack push events endpoint (slack-changes.md §3.3).

    Layered defense:
      1. Pre-decrypt cheap rejects — body cap, ts skew, per-IP rate limit,
         per-app rate limit. None of these touch the SlackApp's signing
         secret, so the OS scheduler sheds DoS traffic before crypto.
      2. Crypto layer — HMAC verify + replay-cache.
      3. Dispatch — url_verification or event_callback fan-out to celery.
    """
    log = _events_logger()
    redis_client = _slack_store()

    # 1. Body size cap (must read body to count, but we cap the read).
    body = await request.body()
    if len(body) > SLACK_EVENT_MAX_BODY_BYTES:
        return _uniform_401()

    # 2. Timestamp skew check (header-only, no body parse).
    ts_header = request.headers.get("x-slack-request-timestamp", "")
    try:
        ts_int = int(ts_header)
    except (TypeError, ValueError):
        return _uniform_401()
    skew = abs(int(time.time()) - ts_int)
    if skew > SLACK_EVENT_MAX_TS_SKEW_SECONDS:
        return _uniform_401()

    # 3. Per-app token bucket (well above Slack's normal delivery rate).
    app_ok = _check_token_bucket(
        redis_client,
        f"slack_event_app_bucket:{slack_app_id}",
        SLACK_EVENT_PER_APP_BURST,
        SLACK_EVENT_PER_APP_BURST,
    )
    if not app_ok:
        return _uniform_401()

    # 5. SlackApp lookup + signing-secret decrypt + HMAC verify.
    app = _slack_app_for_event(db, slack_app_id)
    if app is None or not app.is_active:
        return _uniform_401()
    signing_secret = app.signing_secret  # decrypts under the hood
    if not signing_secret:
        return _uniform_401()
    sig_header = request.headers.get("x-slack-signature", "")
    if not _verify_slack_signature(signing_secret, ts_header, body, sig_header):
        body_sha = hashlib.sha256(body).hexdigest()[:16]
        log.info(
            "slack event rejected",
            extra={
                "slack_app_id": slack_app_id,
                "hmac_ok": False,
                "ts_skew_seconds": skew,
                "body_sha256": body_sha,
            },
        )
        return _uniform_401()

    # 6. Replay protection via SETNX on the body hash.
    body_sha = hashlib.sha256(body).hexdigest()
    replay_key = f"slack_event_seen:{body_sha}"
    if not redis_client.set(
        replay_key, "1", nx=True, ex=SLACK_EVENT_REPLAY_TTL_SECONDS
    ):
        # Slack expects 200 on duplicates so its retry loop stops.
        return PlainTextResponse(content="", status_code=200)

    _bump_event_counter(redis_client, slack_app_id)

    # 7. Parse and dispatch.
    try:
        envelope = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _uniform_401()

    envelope_type = envelope.get("type")

    if envelope_type == "url_verification":
        # Wizard nonce binding (slack-changes.md §3.4 step 5b, security H1).
        nonce_qs = request.query_params.get("wizard_nonce")
        if not nonce_qs:
            return _uniform_401()
        stored = redis_client.get(_wizard_nonce_redis_key(slack_app_id))
        if stored is None:
            return _uniform_401()
        stored_nonce = stored.decode() if isinstance(stored, bytes) else str(stored)
        if not hmac.compare_digest(stored_nonce, nonce_qs):
            return _uniform_401()

        # Advance SlackApp.setup_state from 'draft' → 'signing_verified'.
        # Failed verification doesn't get here; nonce mismatch above bails.
        if app.setup_state == "draft":
            app.setup_state = "signing_verified"
            db.commit()

        challenge = envelope.get("challenge", "")
        return PlainTextResponse(content=challenge, status_code=200)

    if envelope_type == "event_callback":
        log.info(
            "slack event received",
            extra={
                "slack_app_id": slack_app_id,
                "event_type": (envelope.get("event") or {}).get("type"),
                "hmac_ok": True,
                "ts_skew_seconds": skew,
                "body_sha256": body_sha[:16],
            },
        )
        # Test-message wizard hook (step 6).
        _check_test_message_token(redis_client, slack_app_id, envelope)
        _dispatch_event_callback(envelope, slack_app_id)
        return PlainTextResponse(content="", status_code=200)

    # Unknown envelope type — accept (Slack expects 200) but don't dispatch.
    return PlainTextResponse(content="", status_code=200)


# ---------------------------------------------------------------------------
# Wizard endpoints (slack-changes.md §3.4) — owner-only secret + nonce flow
# ---------------------------------------------------------------------------


WIZARD_NONCE_TTL_SECONDS = 30 * 60  # 30 min — long enough for the user to
# paste the URL into Slack and click Save.

WIZARD_TEST_MESSAGE_TTL_SECONDS = 60  # 60s window — slack-changes.md §3.4
# row 6.


class SlackAppSecretBody(BaseModel):
    secret: str


class SlackAppNonceResponse(BaseModel):
    nonce: str
    callback_url: str
    events_url: str


class SlackWizardStatus(BaseModel):
    setup_state: str
    has_credentials: bool
    test_message_pending: bool


class SlackTestMessageStart(BaseModel):
    token: str


class SlackTestMessageStatus(BaseModel):
    status: Literal["waiting", "matched", "expired"]


def _events_url_for(slack_app_id: int, nonce: str) -> str:
    return f"{settings.SERVER_URL}/slack/events/{slack_app_id}?wizard_nonce={nonce}"


def _callback_url_for(slack_app_id: int) -> str:
    return f"{settings.SERVER_URL}/slack/callback/{slack_app_id}"


@router.post("/apps/{app_id}/client-secret", response_model=SlackAppResponse)
def set_client_secret(
    app_id: int,
    body: SlackAppSecretBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackAppResponse:
    """Store the SlackApp client_secret encrypted. Owner only.

    Validation gate is the OAuth callback — Slack will reject mismatched
    client_secret/redirect_uri combos there.
    """
    app = get_slack_app_for_owner(db, app_id, user)
    if not body.secret.strip():
        raise HTTPException(status_code=400, detail="client_secret cannot be empty")
    app.client_secret = body.secret.strip()
    db.commit()
    db.refresh(app)
    return slack_app_to_response(app, user)


@router.post("/apps/{app_id}/signing-secret", response_model=SlackAppResponse)
def set_signing_secret(
    app_id: int,
    body: SlackAppSecretBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackAppResponse:
    """Store the SlackApp signing_secret encrypted. Owner only.

    State stays 'draft' until url_verification succeeds in /slack/events.
    Failed verification doesn't roll state back — user can re-paste and retry.
    """
    app = get_slack_app_for_owner(db, app_id, user)
    if not body.secret.strip():
        raise HTTPException(status_code=400, detail="signing_secret cannot be empty")
    app.signing_secret = body.secret.strip()
    db.commit()
    db.refresh(app)
    return slack_app_to_response(app, user)


@router.post("/apps/{app_id}/wizard-nonce", response_model=SlackAppNonceResponse)
def issue_wizard_nonce(
    app_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackAppNonceResponse:
    """Issue a fresh wizard_nonce bound to (slack_app_id, user_id).

    Stored in Redis with TTL 30 min, scoped per (app, user). The frontend
    embeds this nonce in the Events URL it tells the user to paste into
    Slack's Event Subscriptions config; Slack's url_verification ping
    must echo it back, otherwise we won't advance setup_state.
    """
    app = get_slack_app_for_owner(db, app_id, user)
    # 128 bits from the OS CSPRNG. Do NOT derive from time/app.id/user.id —
    # those are public/predictable and an attacker who can narrow time.time_ns()
    # to ~10⁶ candidates can exhaust SHA-256 in well under a second, defeating
    # the H1 binding between this wizard session and Slack's url_verification ping.
    nonce = secrets.token_hex(16)
    redis_client = _slack_store()
    redis_client.set(
        _wizard_nonce_redis_key(app.id),
        nonce,
        ex=WIZARD_NONCE_TTL_SECONDS,
    )
    return SlackAppNonceResponse(
        nonce=nonce,
        callback_url=_callback_url_for(app.id),
        events_url=_events_url_for(app.id, nonce),
    )


@router.get("/apps/{app_id}/wizard-status", response_model=SlackWizardStatus)
def wizard_status(
    app_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackWizardStatus:
    """Return wizard progress for the frontend's polling loop.

    Available to any authorized user (not just the owner) — they need
    progress visibility when the owner ran the wizard.
    """
    app = get_slack_app_for_authorized_user(db, app_id, user)
    has_credentials = (
        db.query(SlackUserCredentials)
        .filter(SlackUserCredentials.slack_app_id == app.id)
        .first()
        is not None
    )
    test_pending = _slack_store().get(f"slack_wizard_test_token:{app.id}") is not None
    return SlackWizardStatus(
        setup_state=app.setup_state,
        has_credentials=has_credentials,
        test_message_pending=test_pending,
    )


@router.post("/apps/{app_id}/test-message", response_model=SlackTestMessageStatus)
def begin_test_message(
    app_id: int,
    body: SlackTestMessageStart,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackTestMessageStatus:
    """Begin the 60s test-message window. Owner only.

    Stores the user-supplied token in Redis; the events handler matches
    incoming `message` events against it and advances setup_state to
    'live' when a match arrives. Token requirement (slack-changes.md §3.4
    B4 fix) prevents false-positives from chatty workspaces.
    """
    app = get_slack_app_for_owner(db, app_id, user)
    if app.setup_state not in ("signing_verified", "live", "degraded"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"App must be in signing_verified/live state to begin "
                f"test-message; current: {app.setup_state}"
            ),
        )
    token = body.token.strip()
    if not token or len(token) < 8:
        raise HTTPException(
            status_code=400, detail="token must be at least 8 chars"
        )
    redis_client = _slack_store()
    redis_client.set(
        f"slack_wizard_test_token:{app.id}",
        token,
        ex=WIZARD_TEST_MESSAGE_TTL_SECONDS,
    )
    return SlackTestMessageStatus(status="waiting")


@router.get("/apps/{app_id}/test-message", response_model=SlackTestMessageStatus)
def poll_test_message(
    app_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> SlackTestMessageStatus:
    """Poll for the test-message advance.

    The events handler clears the Redis token and advances setup_state on
    match. The frontend polls this endpoint to know when to advance UI.
    """
    app = get_slack_app_for_authorized_user(db, app_id, user)
    if app.setup_state == "live":
        return SlackTestMessageStatus(status="matched")
    if _slack_store().get(f"slack_wizard_test_token:{app.id}") is not None:
        return SlackTestMessageStatus(status="waiting")
    return SlackTestMessageStatus(status="expired")
