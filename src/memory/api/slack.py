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
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from memory.api.auth import (
    assert_project_membership,
    get_interactive_user,
    resolve_user_filter,
)
from memory.api.https_url import HttpsUrl, require_chat_url
from memory.api.request_body import read_request_body_with_cap
from memory.api.webhook_events import record_event_arrival
from memory.api.llm_keys import LLMKeyInput, LLMKeyView, apply_llm_key, llm_key_view
from memory.api.sync_status import PushHealthResponse, SyncStatusResponse, push_health_fields, sync_status_fields
from memory.common import settings
from memory.common.access_control import has_admin_scope
from memory.common.celery_app import (
    ADD_SLACK_MESSAGE,
    MARK_SLACK_MESSAGE_DELETED,
    SYNC_SLACK_WORKSPACE,
    UPDATE_SLACK_CHANNEL,
    UPDATE_SLACK_REACTIONS,
)
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.events import NO_ITEM_TYPE, NOT_COLLECTED, REJECTED, STORED, record_raw_event
from memory.common.db.models.slack import (
    DEFAULT_SLACK_API_BASE_URL,
    DEFAULT_SLACK_AUTHORIZE_URL,
    DEFAULT_SLACK_TOKEN_URL,
    SlackApp,
    SlackChannel,
    SlackUserCredentials,
    SlackWorkspace,
)
from memory.common.db.models.source_items import SlackMessage
from memory.common.encryption import decrypt_value
from memory.common.oauth_client import (
    generate_state,
    log_corr_id,
    sign_state,
    store_state,
    validate_and_consume_state,
)
from memory.common.push_health import push_stale
from memory.common.safe_http import public_http_client
from memory.common.slack import (
    is_public_channel,
    is_slack_ts,
    user_has_workspace_membership,
    user_manages_workspace,
    visible_channels,
    workspace_poll_plan,
    workspace_sync_interval,
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

# A collecting workspace with no successful sync for this many polling
# intervals is reported as stale.
SLACK_STALE_SYNC_INTERVALS = 3


# --- Response Models ---


class SlackWorkspaceApp(PushHealthResponse):
    """A SlackApp the current user is connected to this workspace through."""

    id: int
    name: str
    setup_state: str
    is_owner: bool
    last_event_at: str | None
    push_stale: bool


class SlackWorkspaceResponse(SyncStatusResponse):
    id: str
    name: str
    domain: str | None
    collect_messages: bool
    sync_interval_seconds: int
    event_reconcile_interval_seconds: int
    effective_sync_interval_seconds: int
    last_sync_at: str | None
    channel_count: int
    connected_users: int  # Number of users with credentials for this workspace
    user_connected: bool  # Whether the current user has credentials
    # Why a collecting workspace won't be polled; None when it will be (or collection is off)
    poll_blocker: str | None
    # Collecting, but no successful sync for several polling intervals
    sync_stale: bool
    apps: list[SlackWorkspaceApp]
    # Whether the current user may change this workspace's and its channels' settings
    can_manage: bool
    # Access control
    project_id: int | None
    sensitivity: str
    llm_key: LLMKeyView


class SlackWorkspaceUpdate(BaseModel):
    collect_messages: bool | None = None
    sync_interval_seconds: int | None = Field(default=None, ge=60)
    event_reconcile_interval_seconds: int | None = Field(default=None, ge=3600)
    # Access control
    project_id: int | None = None
    sensitivity: Literal["public", "basic", "internal", "confidential"] | None = None
    llm_key: LLMKeyInput | None = None


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


ClientId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SlackAppCreate(BaseModel):
    """Body for POST /slack/apps."""

    name: str
    client_id: ClientId
    authorize_url: HttpsUrl | None = None
    token_url: HttpsUrl | None = None
    api_base_url: HttpsUrl | None = None


class SlackAppUpdate(BaseModel):
    """Body for PATCH /slack/apps/{id}.

    `setup_state` is intentionally NOT here — state advances only via the
    wizard endpoints (paste-secret + url_verification) and ingested events.
    Direct mutation would let an owner bypass verification.
    """

    name: str | None = None
    is_active: bool | None = None
    client_id: ClientId | None = None
    authorize_url: HttpsUrl | None = None
    token_url: HttpsUrl | None = None
    api_base_url: HttpsUrl | None = None


SLACK_APP_URL_DEFAULTS = {
    "authorize_url": DEFAULT_SLACK_AUTHORIZE_URL,
    "token_url": DEFAULT_SLACK_TOKEN_URL,
    "api_base_url": DEFAULT_SLACK_API_BASE_URL,
}
DUPLICATE_CLIENT_ID = "You already have a Slack app with this client ID. Reconnect or delete it on the Slack tab."


class SlackAppResponse(PushHealthResponse):
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
    authorize_url: str
    token_url: str
    api_base_url: str
    last_event_at: str | None


# --- Helper Functions ---


def get_user_credentials_for_app(
    db: Session,
    workspace_id: str,
    user: User,
    slack_app_id: int,
) -> SlackUserCredentials | None:
    """Get the current user's credentials for a (SlackApp, workspace) pair.

    Strict per-tenant lookup — two SlackApps can legitimately share a
    workspace (design doc §7 decision 5), so callers MUST identify the
    app. For "does this user have ANY credential for this workspace?"
    membership checks (e.g. workspace-level access gating), use
    :func:`user_has_workspace_membership` instead.
    """
    return (
        db.query(SlackUserCredentials)
        .filter(
            SlackUserCredentials.workspace_id == workspace_id,
            SlackUserCredentials.user_id == user.id,
            SlackUserCredentials.slack_app_id == slack_app_id,
        )
        .first()
    )


def get_workspace_with_access(
    db: Session, workspace_id: str, user: User
) -> SlackWorkspace:
    """Get a workspace, ensuring the user has at least one credential for it."""
    workspace = db.get(SlackWorkspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    if not user_has_workspace_membership(db, workspace_id, user):
        raise HTTPException(status_code=404, detail="Workspace not found")

    return workspace


def poll_blocker(db: Session, ws: SlackWorkspace) -> str | None:
    if not ws.collect_messages:
        return None
    app_ids, blocked = workspace_poll_plan(db, ws.id)
    return None if app_ids else "; ".join(blocked)


def is_sync_stale(ws: SlackWorkspace, interval: int | None = None) -> bool:
    if not ws.collect_messages or ws.last_sync_at is None:
        return False
    # The beat task only runs every SLACK_SYNC_INTERVAL, so a shorter
    # per-workspace interval can't make syncs more frequent than that.
    cadence = max(interval or ws.sync_interval_seconds, settings.SLACK_SYNC_INTERVAL)
    age = datetime.now(timezone.utc) - ws.last_sync_at
    return age.total_seconds() > SLACK_STALE_SYNC_INTERVALS * cadence


def workspace_apps(db: Session, ws: SlackWorkspace, user: User) -> list[SlackWorkspaceApp]:
    apps = (
        db.query(SlackApp)
        .join(SlackUserCredentials, SlackUserCredentials.slack_app_id == SlackApp.id)
        .filter(
            SlackUserCredentials.workspace_id == ws.id,
            SlackUserCredentials.user_id == user.id,
        )
        .order_by(SlackApp.id)
        .distinct()
        .all()
    )
    return [
        SlackWorkspaceApp(
            id=app.id,
            name=app.name,
            setup_state=app.setup_state,
            is_owner=app.is_owner(user),
            last_event_at=app.last_event_at.isoformat() if app.last_event_at else None,
            push_stale=app.setup_state in ("live", "degraded") and push_stale(app.last_event_at),
            **push_health_fields(app),
        )
        for app in apps
    ]


NOT_WORKSPACE_OWNER = "Only the workspace's owner (the first user to connect it) or an admin can change its settings"


def get_visible_workspace(db: Session, workspace_id: str, user: User) -> SlackWorkspace:
    """A workspace `user` is connected to, or any workspace for admins, who see them all
    in the list without a credential."""
    if not has_admin_scope(user):
        return get_workspace_with_access(db, workspace_id, user)
    workspace = db.get(SlackWorkspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


def get_workspace_for_settings(db: Session, workspace_id: str, user: User) -> SlackWorkspace:
    """A workspace whose settings `user` may change; 403 for its other members."""
    workspace = get_visible_workspace(db, workspace_id, user)
    if not user_manages_workspace(workspace, user):
        raise HTTPException(status_code=403, detail=NOT_WORKSPACE_OWNER)
    return workspace


def get_channel_for_settings(db: Session, channel_id: str, user: User) -> SlackChannel:
    """A channel whose settings `user` may change. A channel the caller doesn't see in
    the listing is a 404, one they see but don't manage is a 403."""
    channel = db.get(SlackChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    workspace = get_visible_workspace(db, channel.workspace_id, user)
    if user_manages_workspace(workspace, user):
        return channel
    if not is_public_channel(channel):
        raise HTTPException(status_code=404, detail="Channel not found")
    raise HTTPException(status_code=403, detail=NOT_WORKSPACE_OWNER)


def workspace_to_response(
    ws: SlackWorkspace, db: Session, current_user: User
) -> SlackWorkspaceResponse:
    """Convert workspace to response model."""
    channels = db.query(func.count(SlackChannel.id)).filter(SlackChannel.workspace_id == ws.id)
    channel_count = visible_channels(channels, ws, current_user).scalar() or 0

    connected_users = (
        db.query(func.count(SlackUserCredentials.id))
        .filter(SlackUserCredentials.workspace_id == ws.id)
        .scalar()
        or 0
    )

    user_connected = (
        db.query(SlackUserCredentials)
        .filter(
            SlackUserCredentials.workspace_id == ws.id,
            SlackUserCredentials.user_id == current_user.id,
        )
        .first()
        is not None
    )

    pollable_apps, _ = workspace_poll_plan(db, ws.id)
    interval = workspace_sync_interval(db, ws, pollable_apps)
    return SlackWorkspaceResponse(
        id=ws.id,
        name=ws.name,
        domain=ws.domain,
        collect_messages=ws.collect_messages,
        sync_interval_seconds=ws.sync_interval_seconds,
        event_reconcile_interval_seconds=ws.event_reconcile_interval_seconds,
        effective_sync_interval_seconds=max(settings.SLACK_SYNC_INTERVAL, interval),
        last_sync_at=ws.last_sync_at.isoformat() if ws.last_sync_at else None,
        **sync_status_fields(ws),
        channel_count=channel_count,
        connected_users=connected_users,
        user_connected=user_connected,
        poll_blocker=poll_blocker(db, ws),
        sync_stale=is_sync_stale(ws, interval),
        apps=workspace_apps(db, ws, current_user),
        can_manage=user_manages_workspace(ws, current_user),
        project_id=ws.project_id,
        sensitivity=ws.sensitivity or "basic",
        llm_key=llm_key_view(db, current_user, ws),
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


def _resolve_callback_app(db: Session, slack_app_id: int, user: User) -> SlackApp:
    """Look up the SlackApp and verify the caller is allowed to OAuth into it.

    Raises 404 (missing/inactive — same status to avoid existence probing),
    403 (caller not the owner), or 503 (the app exists
    but its wizard hasn't finished — no client credentials to exchange with).
    """
    slack_app = db.get(SlackApp, slack_app_id)
    if slack_app is None or not slack_app.is_active:
        raise HTTPException(status_code=404, detail="Slack app not found")
    if not slack_app.is_owner(user):
        raise HTTPException(status_code=403, detail="Not authorized for this Slack app")
    if not slack_app.client_id or not slack_app.client_secret:
        raise HTTPException(
            status_code=503,
            detail="Slack app client credentials not configured",
        )
    return slack_app


def _consume_oauth_state(db: Session, state: str, user: User) -> None:
    """Validate the OAuth state row + signature + one-time consumption, and
    confirm the authenticated session matches the user the state was issued
    for (CSRF binding fix a5c9746d). Raises 400 / 403 on mismatch."""
    state_user_id = validate_and_consume_state(db, state, "slack")
    if not state_user_id:
        raise HTTPException(
            status_code=400, detail="Invalid or expired state parameter"
        )
    if user.id != state_user_id:
        raise HTTPException(
            status_code=403, detail="OAuth state was issued for a different session"
        )


OAUTH_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


def oauth_error_code(error: object) -> str | None:
    """An OAuth error as Slack spells them (``invalid_code``); anything else,
    which the token server or redirect controls, as ``unknown_error``."""
    if error is None:
        return None
    return error if isinstance(error, str) and OAUTH_ERROR_CODE.fullmatch(error) else "unknown_error"


async def _exchange_slack_oauth_code(
    slack_app: SlackApp, code: str, redirect_uri: str
) -> dict:
    """POST to Slack's oauth.v2.access. Returns the parsed authed_user payload,
    the app's bot user id and the validated team. Raises 400 on Slack-reported errors, missing
    user token, or malformed team_id."""
    async with public_http_client(timeout=30.0) as client:
        response = await client.post(
            slack_app.token_url,
            data={
                "client_id": slack_app.client_id,
                "client_secret": slack_app.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
    try:
        data = response.json()
    except ValueError:
        logger.warning(f"Slack app {slack_app.id}: token exchange reply was not JSON")
        raise HTTPException(status_code=400, detail="Slack OAuth failed: invalid response")

    if not isinstance(data, dict) or not data.get("ok"):
        error = oauth_error_code(data.get("error") if isinstance(data, dict) else None)
        logger.warning(f"Slack app {slack_app.id}: token exchange failed: {error}")
        raise HTTPException(status_code=400, detail=f"Slack OAuth failed: {error}")

    authed_user = data.get("authed_user", {})
    if not authed_user.get("access_token"):
        raise HTTPException(status_code=400, detail="No user access token received")

    team = data.get("team", {})
    team_id = team.get("id")
    if not team_id or not _SLACK_TEAM_ID_PATTERN.fullmatch(team_id):
        raise HTTPException(status_code=400, detail="Malformed team ID in response")

    return {
        "authed_user": authed_user,
        "team_id": team_id,
        "team_name": team.get("name", "Unknown Workspace"),
        "bot_user_id": data.get("bot_user_id"),
    }


def _persist_oauth_credentials(
    db: Session,
    slack_app: SlackApp,
    user: User,
    team_id: str,
    team_name: str,
    authed_user: dict,
    bot_user_id: str | None = None,
) -> None:
    """Upsert the SlackWorkspace + SlackUserCredentials row from a successful
    Slack token-exchange response. Computes token expiry with a 5-minute
    buffer, splits scopes, stores encrypted access/refresh tokens."""
    workspace = db.get(SlackWorkspace, team_id)
    if workspace is None:
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

    creds = (
        db.query(SlackUserCredentials)
        .filter(
            SlackUserCredentials.slack_app_id == slack_app.id,
            SlackUserCredentials.workspace_id == team_id,
            SlackUserCredentials.user_id == user.id,
        )
        .first()
    )
    if creds is None:
        creds = SlackUserCredentials(
            slack_app_id=slack_app.id,
            workspace_id=team_id,
            user_id=user.id,
        )
        db.add(creds)

    creds.access_token = authed_user["access_token"]
    creds.refresh_token = authed_user.get("refresh_token")
    creds.scopes = authed_user.get("scope", "").replace(",", " ").split()
    creds.token_expires_at = token_expires_at
    creds.slack_user_id = authed_user.get("id")
    creds.bot_user_id = bot_user_id

    workspace.record_sync(None)
    db.commit()


def _render_oauth_complete_html(
    user_id: int, slack_app_id: int, team_id: str
) -> HTMLResponse:
    """Render the post-callback HTML that posts to the wizard's per-user
    BroadcastChannel and either closes the popup or redirects.

    Every interpolated value goes through ``json.dumps`` so the JS string
    literals stay safely quoted regardless of the field's runtime type —
    ``team_id`` already passed the regex check upstream, but the encoding
    is the contract a future caller inherits.
    """
    frontend_url = f"{settings.SERVER_URL}/sources?tab=slack&connected={team_id}"
    workspace_id_js = json.dumps(team_id)
    frontend_url_js = json.dumps(frontend_url)
    channel_name_js = json.dumps(f"slack-oauth-{user_id}")
    slack_app_id_js = json.dumps(slack_app_id)
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


@router.get("/callback/{slack_app_id}")
async def slack_callback_for_app(
    slack_app_id: int,
    code: str = Query(...),
    state: str = Query(...),
    error: str | None = Query(None),
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
):
    """Multi-tenant OAuth callback (slack-changes.md §3.2 / §3.4 row 4).

    Looks up the SlackApp from the URL path and uses ITS encrypted
    client_id/client_secret for the token exchange — not the env-var
    credentials. State is bound to the user's session (CSRF fix
    a5c9746d). Stored credentials carry the path's ``slack_app_id``.
    """
    logger.info(
        f"Slack OAuth callback received (multi-app): slack_app_id={slack_app_id}, "
        f"code_corr={log_corr_id(code)}, "
        f"state_corr={log_corr_id(state)}, len={len(state)}, error={oauth_error_code(error)}"
    )

    if error:
        error_code = oauth_error_code(error)
        logger.warning(f"Slack OAuth error from provider: {error_code}")
        raise HTTPException(status_code=400, detail=f"OAuth error: {error_code}")

    slack_app = _resolve_callback_app(db, slack_app_id, user)
    require_chat_url(slack_app.token_url, user)
    _consume_oauth_state(db, state, user)
    exchange = await _exchange_slack_oauth_code(
        slack_app, code, _callback_url_for(slack_app_id)
    )
    _persist_oauth_credentials(
        db,
        slack_app,
        user,
        team_id=exchange["team_id"],
        team_name=exchange["team_name"],
        authed_user=exchange["authed_user"],
        bot_user_id=exchange["bot_user_id"],
    )

    # Trigger an immediate workspace sync (slack-changes.md §3.7 backfill).
    celery_app.send_task(
        SYNC_SLACK_WORKSPACE,
        kwargs={"workspace_id": exchange["team_id"], "slack_app_id": slack_app.id},
    )

    return _render_oauth_complete_html(user.id, slack_app.id, exchange["team_id"])


# --- Workspace Endpoints ---


@router.get("/workspaces")
def list_workspaces(
    user_id: int | None = None,
    user: User = Depends(get_interactive_user),
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
            for cred in db.query(SlackUserCredentials)
            .filter(SlackUserCredentials.user_id == resolved_user_id)
            .all()
        ]

        if not user_workspace_ids:
            return []

        workspaces = (
            db.query(SlackWorkspace)
            .filter(SlackWorkspace.id.in_(user_workspace_ids))
            .all()
        )

    return [workspace_to_response(w, db, user) for w in workspaces]


@router.get("/workspaces/{workspace_id}")
def get_workspace(
    workspace_id: str,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SlackWorkspaceResponse:
    """Get details of a specific workspace."""
    workspace = get_workspace_with_access(db, workspace_id, user)
    return workspace_to_response(workspace, db, user)


@router.patch("/workspaces/{workspace_id}")
def update_workspace(
    workspace_id: str,
    updates: SlackWorkspaceUpdate,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SlackWorkspaceResponse:
    """Update workspace settings. Only the workspace's owner or an admin may."""
    workspace = get_workspace_for_settings(db, workspace_id, user)

    if updates.collect_messages is not None:
        workspace.collect_messages = updates.collect_messages
    if updates.event_reconcile_interval_seconds is not None:
        workspace.event_reconcile_interval_seconds = updates.event_reconcile_interval_seconds
    if updates.sync_interval_seconds is not None:
        workspace.sync_interval_seconds = updates.sync_interval_seconds
    if "project_id" in updates.model_fields_set:
        if updates.project_id is not None:
            assert_project_membership(db, user, updates.project_id)
        workspace.project_id = updates.project_id
    if updates.sensitivity is not None:
        workspace.sensitivity = updates.sensitivity
    apply_llm_key(db, user, workspace, updates.llm_key)

    db.commit()
    db.refresh(workspace)

    return workspace_to_response(workspace, db, user)


@router.delete("/workspaces/{workspace_id}")
def disconnect_workspace(
    workspace_id: str,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
):
    """Disconnect user's credentials from a Slack workspace.

    This removes ALL of the user's credentials for this workspace
    (across every SlackApp they connected through). The workspace and
    its messages are preserved if there are ingested messages (to
    avoid data loss) or if other users are still connected.
    """
    user_creds = (
        db.query(SlackUserCredentials)
        .filter(
            SlackUserCredentials.workspace_id == workspace_id,
            SlackUserCredentials.user_id == user.id,
        )
        .all()
    )
    if not user_creds:
        raise HTTPException(status_code=404, detail="Workspace not found")

    for cred in user_creds:
        db.delete(cred)
    db.commit()

    # Check remaining users and messages
    remaining_users = (
        db.query(func.count(SlackUserCredentials.id))
        .filter(SlackUserCredentials.workspace_id == workspace_id)
        .scalar()
        or 0
    )

    message_count = (
        db.query(func.count(SlackMessage.id))
        .filter(SlackMessage.workspace_id == workspace_id)
        .scalar()
        or 0
    )

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
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
):
    """Trigger a manual sync for a workspace.

    Runs the periodic sync early: one ``sync_slack_workspace`` task per
    SlackApp that can poll the workspace, 409 with the reasons if none can.
    """
    get_workspace_with_access(db, workspace_id, user)

    app_ids, blocked = workspace_poll_plan(db, workspace_id)
    if not app_ids:
        raise HTTPException(
            status_code=409,
            detail="Nothing can poll this workspace: " + "; ".join(blocked),
        )
    for slack_app_id in app_ids:
        celery_app.send_task(
            SYNC_SLACK_WORKSPACE,
            kwargs={"workspace_id": workspace_id, "slack_app_id": slack_app_id},
        )

    return {
        "status": "sync_triggered",
        "workspace_id": workspace_id,
        "apps_dispatched": len(app_ids),
    }


# --- Channel Endpoints ---


@router.get("/workspaces/{workspace_id}/channels")
def list_channels(
    workspace_id: str,
    channel_type: str | None = None,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> list[SlackChannelResponse]:
    """List a workspace's channels: all of them for its owner and admins, the public
    ones for its other members."""
    workspace = get_visible_workspace(db, workspace_id, user)

    query = visible_channels(
        db.query(SlackChannel).filter(SlackChannel.workspace_id == workspace_id), workspace, user
    )

    if channel_type:
        query = query.filter(SlackChannel.channel_type == channel_type)

    channels = query.order_by(SlackChannel.name).all()
    return [channel_to_response(c) for c in channels]


@router.patch("/channels/{channel_id}")
def update_channel(
    channel_id: str,
    updates: SlackChannelUpdate,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SlackChannelResponse:
    """Update channel settings. Only the workspace's owner or an admin may."""
    channel = get_channel_for_settings(db, channel_id, user)

    if "collect_messages" in updates.model_fields_set:
        channel.collect_messages = updates.collect_messages
    if "project_id" in updates.model_fields_set:
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
# Only the owner (`created_by_user_id`) can see or use an app; anyone else
# gets a 404, so app existence doesn't leak (§4 S11).
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
        authorize_url=app.authorize_url,
        token_url=app.token_url,
        api_base_url=app.api_base_url,
        last_event_at=app.last_event_at.isoformat() if app.last_event_at else None,
        **push_health_fields(app),
    )


def get_slack_app_for_owner(db: Session, app_id: int, user: User, lock: bool = False) -> SlackApp:
    """Fetch a SlackApp owned by the user; 404 otherwise, whether or not it exists."""
    app = db.get(SlackApp, app_id, with_for_update=lock or None)
    if app is None or not app.is_owner(user):
        raise HTTPException(status_code=404, detail="Slack app not found")
    return app


def app_connected(db: Session, app_id: int) -> bool:
    return db.query(SlackUserCredentials).filter(SlackUserCredentials.slack_app_id == app_id).first() is not None


def list_slack_apps_for_user(db: Session, user: User) -> list[SlackApp]:
    return db.query(SlackApp).filter(SlackApp.created_by_user_id == user.id).all()


@router.post("/apps", response_model=SlackAppResponse, status_code=201)
def create_slack_app(
    body: SlackAppCreate,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SlackAppResponse:
    """Register a draft SlackApp for the caller.

    Several users can register the same client_id (a shared Slack app);
    one user registering it twice gets a 409.
    """
    for field in SLACK_APP_URL_DEFAULTS:
        if value := getattr(body, field):
            require_chat_url(value, user)
    app = SlackApp(
        name=body.name,
        client_id=body.client_id,
        created_by_user_id=user.id,
        setup_state="draft",
        is_active=True,
        **{f: getattr(body, f) for f in SLACK_APP_URL_DEFAULTS if getattr(body, f)},
    )
    db.add(app)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_CLIENT_ID)
    db.refresh(app)
    return slack_app_to_response(app, user)


@router.get("/apps", response_model=list[SlackAppResponse])
def list_slack_apps(
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> list[SlackAppResponse]:
    """List the apps the current user owns."""
    apps = list_slack_apps_for_user(db, user)
    return [slack_app_to_response(a, user) for a in apps]


@router.get("/apps/{app_id}", response_model=SlackAppResponse)
def get_slack_app(
    app_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SlackAppResponse:
    """Get a single SlackApp by id (owner only)."""
    app = get_slack_app_for_owner(db, app_id, user)
    return slack_app_to_response(app, user)


@router.patch("/apps/{app_id}", response_model=SlackAppResponse)
def update_slack_app(
    app_id: int,
    body: SlackAppUpdate,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SlackAppResponse:
    """Update display name, is_active, the OAuth / API URLs and, until a workspace is connected, client_id. Owner only."""
    # Locked so an event's state advance can't land between this read and the reset to 'draft'.
    app = get_slack_app_for_owner(db, app_id, user, lock=True)

    if body.client_id and body.client_id != app.client_id:
        if app_connected(db, app.id):
            raise HTTPException(
                status_code=409,
                detail="A workspace is connected through this app, so its client ID can't change. Delete the app and register it again.",
            )
        # The secrets and verification belonged to the Slack app the old client_id named.
        app.client_id = body.client_id
        app.client_secret = None
        app.signing_secret = None
        app.setup_state = "draft"
    if body.name is not None:
        app.name = body.name
    if body.is_active is not None:
        app.is_active = body.is_active
    for field in SLACK_APP_URL_DEFAULTS.keys() & body.model_fields_set:
        # An explicit null resets the URL to Slack's.
        value = getattr(body, field) or SLACK_APP_URL_DEFAULTS[field]
        require_chat_url(value, user)
        setattr(app, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_CLIENT_ID)
    db.refresh(app)
    return slack_app_to_response(app, user)


@router.delete("/apps/{app_id}", status_code=204)
def delete_slack_app(
    app_id: int,
    user: User = Depends(get_interactive_user),
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


# ---------------------------------------------------------------------------
# Events endpoint (slack-changes.md §3.3) — push delivery from Slack
# ---------------------------------------------------------------------------

# Body cap is well above any realistic Slack event (Slack max is ~3MB but real
# events are small) and bounds the surface for pre-decrypt malicious traffic.
SLACK_EVENT_MAX_BODY_BYTES = 1_048_576  # 1 MiB
SLACK_EVENT_MAX_TS_SKEW_SECONDS = 5 * 60  # 5 min — Slack's documented window

UNIFORM_REJECT_BODY = b"invalid request"
"""All security-failure responses use this identical body (security M5 — no
oracle: header issues, signature failures, replay hits all look the same to
the caller)."""


# Process-local TTL state for the slack events endpoint and wizard. Single
# uvicorn worker (see docker/api/Dockerfile), so plain dicts + a lock are
# fine; multi-worker deployments would lose this state across workers.
_slack_state_lock = threading.Lock()
_wizard_nonces: dict[int, tuple[str, float]] = {}  # app_id → (nonce, expiry)


def _set_wizard_nonce(slack_app_id: int, nonce: str, ttl_seconds: int) -> None:
    with _slack_state_lock:
        _wizard_nonces[slack_app_id] = (nonce, time.monotonic() + ttl_seconds)


def _get_wizard_nonce(slack_app_id: int) -> str | None:
    now = time.monotonic()
    with _slack_state_lock:
        entry = _wizard_nonces.get(slack_app_id)
        if entry is None:
            return None
        nonce, expiry = entry
        if expiry <= now:
            _wizard_nonces.pop(slack_app_id, None)
            return None
        return nonce


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
    expected_hex = signature_header[len("v0=") :]
    basestring = f"v0:{request_ts}:".encode() + body
    digest = hmac.new(
        signing_secret.encode("utf-8"),
        basestring,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(digest, expected_hex)


def app_has_workspace(db: Session, slack_app_id: int, team_id: str | None) -> bool:
    return (
        team_id is not None
        and db.query(SlackUserCredentials.id)
        .filter(
            SlackUserCredentials.slack_app_id == slack_app_id,
            SlackUserCredentials.workspace_id == team_id,
        )
        .first()
        is not None
    )


def _slack_app_for_event(db: Session, slack_app_id: int) -> SlackApp | None:
    """Look up the SlackApp; return None on miss so callers fail closed."""
    if slack_app_id <= 0:
        return None
    return db.get(SlackApp, slack_app_id)


# A planned dispatch: a label for logs, and the task to send with its kwargs (none for a dropped event).
SlackDispatch = tuple[str, str | None, dict]


class PushedEdit(BaseModel):
    ts: str | None = None


class PushedMessage(BaseModel):
    ts: str | None = None
    user: str | None = None
    text: str | None = ""
    thread_ts: str | None = None
    reply_count: int | None = None
    edited: PushedEdit | None = None
    reactions: list | None = None
    files: list | None = None


class PushedItem(BaseModel):
    channel: str | None = None
    ts: str | None = None


class PushedChannel(BaseModel):
    # channel_created and channel_rename ship the whole object on to the task.
    model_config = ConfigDict(extra="allow")

    id: str | None = None


class PushedEvent(PushedMessage):
    type: str = "unknown"
    subtype: str | None = None
    # A user object on team_join and user_change.
    user: Any = None
    channel: str | PushedChannel | None = None
    # pin_*, file_shared and the like name their channel here instead.
    channel_id: str | None = None
    item: PushedItem | None = None
    message: PushedMessage | None = None
    deleted_ts: str | None = None
    reaction: str | None = None


class SlackEnvelope(BaseModel):
    """The parts of a pushed body the endpoint reads; a signed body shaped otherwise is rejected."""

    type: str | None = None
    team_id: str | None = None
    event_id: str | None = None
    challenge: str = ""
    event: PushedEvent = Field(default_factory=PushedEvent)


def slack_event_type(envelope: SlackEnvelope) -> str:
    event = envelope.event
    return f"{event.type}.{event.subtype}" if event.subtype else event.type


def channel_collected(db: Session, workspace_id: str, channel_id: str) -> bool:
    """Whether the channel's messages are collected; a channel not stored yet
    follows its workspace."""
    if channel := db.get(SlackChannel, channel_id):
        return channel.should_collect
    workspace = db.get(SlackWorkspace, workspace_id)
    return bool(workspace and workspace.collect_messages)


def channel_event_payload(event_type: str, channel: str | PushedChannel | None) -> dict | None:
    """The channel fields a channel_* event changes, or None if it changes none."""
    # Archive events name the channel by id only; created/rename carry the object.
    if event_type in ("channel_created", "channel_rename") and isinstance(channel, PushedChannel):
        return channel.model_dump(exclude_unset=True)
    if event_type == "channel_archive":
        return {"is_archived": True}
    if event_type == "channel_unarchive":
        return {"is_archived": False}
    return None


def malformed_ts(required: str | None, *optional: str | None) -> bool:
    """Whether the required timestamp is missing or malformed, or an optional one is malformed."""
    return not is_slack_ts(required) or any(ts is not None and not is_slack_ts(ts) for ts in optional)


def rejected_ts(slack_app_id: int, event_type: str) -> SlackDispatch:
    logger.warning(f"Rejecting event with malformed ts slack_app_id={slack_app_id} event_type={event_type}")
    return "rejected_ts", None, {}


def plan_event_callback(envelope: SlackEnvelope, slack_app_id: int) -> SlackDispatch:
    """Branch on event.type to the celery task that applies it.

    Defense-in-depth: HMAC signature proves the body came from Slack with the
    correct signing secret, but it does not prove the *content* is well-formed.
    A Slack-side compromise, a replay against a stale signing secret in a
    different format, or a malformed event from a buggy Slack app could put
    arbitrary strings into our Celery task kwargs (workspace_id flows through
    to a SlackMessage Text column with no DB-level shape constraint). We
    validate workspace_id, channel_id and message timestamps here so garbage
    gets rejected at the dispatcher rather than wasting Celery cycles + log
    spam in add_slack_message. A timestamp also names a directory on disk.
    """
    event = envelope.event
    event_type, subtype = event.type, event.subtype
    workspace_id = envelope.team_id

    # channel_* events deliver a channel object, message events its id, and
    # reactions carry it under `event.item.channel` instead.
    reacted = event_type in ("reaction_added", "reaction_removed")
    if reacted:
        channel_id = event.item and event.item.channel
    else:
        channel_id = (event.channel.id if isinstance(event.channel, PushedChannel) else event.channel) or event.channel_id
    # Workspace-level events (team_join, user_change, ...) name no channel.
    needs_channel = reacted or event_type == "message" or event_type.startswith("channel_")

    if not workspace_id or not _SLACK_TEAM_ID_PATTERN.fullmatch(workspace_id):
        logger.warning(
            f"Rejecting event with malformed team_id "
            f"slack_app_id={slack_app_id} event_type={event_type}"
        )
        return "rejected_team_id", None, {}
    if channel_id is None and not needs_channel:
        return "ignored", None, {"workspace_id": workspace_id, "channel_id": None}
    if not channel_id or not _SLACK_CHANNEL_ID_PATTERN.fullmatch(channel_id):
        logger.warning(
            f"Rejecting event with malformed channel_id "
            f"slack_app_id={slack_app_id} event_type={event_type}"
        )
        return "rejected_channel_id", None, {}

    if event_type == "message":
        if subtype == "message_deleted":
            if malformed_ts(event.deleted_ts or event.ts):
                return rejected_ts(slack_app_id, event_type)
            return "mark_deleted", MARK_SLACK_MESSAGE_DELETED, {
                "workspace_id": workspace_id,
                "channel_id": channel_id,
                "message_ts": event.deleted_ts or event.ts,
                "slack_app_id": slack_app_id,
            }
        # Both "message" and "message_changed" go through ADD_SLACK_MESSAGE,
        # which carries the merge logic from B-pre-1/B-pre-2.
        message = event if subtype != "message_changed" else event.message or PushedMessage()
        if malformed_ts(message.ts, message.thread_ts, message.edited and message.edited.ts):
            return rejected_ts(slack_app_id, event_type)
        return "add_message", ADD_SLACK_MESSAGE, {
            "workspace_id": workspace_id,
            "channel_id": channel_id,
            "message_ts": message.ts,
            "author_id": message.user,
            "content": message.text,
            "thread_ts": message.thread_ts,
            "reply_count": message.reply_count,
            "subtype": subtype,
            "edited_ts": message.edited and message.edited.ts,
            "reactions": message.reactions,
            "files": message.files,
            "slack_app_id": slack_app_id,
        }

    if reacted:
        if malformed_ts(event.item and event.item.ts):
            return rejected_ts(slack_app_id, event_type)
        return "update_reactions", UPDATE_SLACK_REACTIONS, {
            "workspace_id": workspace_id,
            "channel_id": channel_id,
            "message_ts": event.item and event.item.ts,
            "action": "add" if event_type == "reaction_added" else "remove",
            "emoji": event.reaction,
            "user_id": event.user,
            "slack_app_id": slack_app_id,
        }

    channel_payload = channel_event_payload(event_type, event.channel)
    if channel_payload is not None:
        return "update_channel", UPDATE_SLACK_CHANNEL, {
            "workspace_id": workspace_id,
            "channel_id": channel_id,
            "channel_payload": channel_payload,
            "slack_app_id": slack_app_id,
        }

    return "ignored", None, {"workspace_id": workspace_id, "channel_id": channel_id}


def advance_setup_state(db: Session, app: SlackApp, verified_secret: bytes, from_state: str, to_state: str) -> None:
    # The loaded state gates the write so busy live apps cost no UPDATE; a
    # stale read can only miss a reset to 'draft', which follows a secret
    # change this request couldn't vouch for anyway.
    if app.setup_state != from_state:
        return
    # Matching on the secret the request was verified with keeps a secret
    # pasted mid-request (which resets to 'draft') from being marked verified.
    updated = (
        db.query(SlackApp)
        .filter(
            SlackApp.id == app.id,
            SlackApp.setup_state == from_state,
            SlackApp.signing_secret_encrypted == verified_secret,
        )
        .update({SlackApp.setup_state: to_state}, synchronize_session=False)
    )
    db.commit()
    if updated:
        set_committed_value(app, "setup_state", to_state)


@router.post("/events/{slack_app_id}")
async def slack_events(
    slack_app_id: int,
    request: Request,
    db: Session = Depends(get_session),
) -> Response:
    """Slack push events endpoint (slack-changes.md §3.3).

    Layered defense:
      1. Pre-decrypt cheap rejects — body cap + ts skew. Neither touches the
         SlackApp's signing secret, so the OS scheduler sheds malformed
         traffic before crypto. (IP-level rate limiting is handled by the
         reverse proxy fronting this endpoint.)
      2. Crypto layer — HMAC verify.
      3. Dispatch — url_verification or event_callback fan-out to celery,
         deduped by the raw event each request records.
    """
    log = _events_logger()

    # 1. Body size cap, enforced while reading.
    try:
        body = await read_request_body_with_cap(request, SLACK_EVENT_MAX_BODY_BYTES)
    except HTTPException:
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

    # 3. SlackApp lookup + signing-secret decrypt + HMAC verify.
    app = _slack_app_for_event(db, slack_app_id)
    if app is None or not app.is_active or app.signing_secret_encrypted is None:
        log.warning("slack event for an unknown or inactive app", extra={"slack_app_id": slack_app_id})
        return _uniform_401()
    verified_secret = app.signing_secret_encrypted
    signing_secret = decrypt_value(verified_secret)
    sig_header = request.headers.get("x-slack-signature", "")
    if not _verify_slack_signature(signing_secret, ts_header, body, sig_header):
        body_sha = hashlib.sha256(body).hexdigest()[:16]
        log.warning(
            "slack event rejected",
            extra={
                "slack_app_id": slack_app_id,
                "hmac_ok": False,
                "ts_skew_seconds": skew,
                "body_sha256": body_sha,
            },
        )
        return _uniform_401()

    # 4. Parse and dispatch. Repeats are caught by the raw event's dedupe.
    body_sha = hashlib.sha256(body).hexdigest()
    try:
        envelope = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        envelope = None
    if not isinstance(envelope, dict):
        log.warning("slack event unparseable", extra={"slack_app_id": slack_app_id})
        return _uniform_401()
    try:
        shape = SlackEnvelope.model_validate(envelope)
    except ValidationError:
        shape = None

    # The verification token is a credential.
    logged = {k: v for k, v in envelope.items() if k != "token"}

    def record(event_type: str, outcome: str, container: str | None = None, links_items: bool = False) -> int | None:
        return record_raw_event(
            db,
            source="slack",
            event_type=event_type,
            account_id=app.id,
            owner_id=app.created_by_user_id,
            delivery_id=(shape and shape.event_id) or body_sha,
            outcome=outcome,
            container=container,
            payload=logged,
            links_items=links_items,
        )

    if shape is None:
        log.warning("slack event of an unexpected shape", extra={"slack_app_id": slack_app_id})
        record(str(envelope.get("type")), REJECTED)
        db.commit()
        return PlainTextResponse(content="", status_code=200)

    if shape.type == "url_verification":
        nonce_qs = request.query_params.get("wizard_nonce")
        challenge = shape.challenge
        # An events URL registered without the wizard's nonce is verified
        # by the signature alone: it proves the sender holds the signing
        # secret the owner pasted, which is all 'signing_verified' claims.
        # A nonce that is present must still match (security H1).
        if nonce_qs:
            stored_nonce = _get_wizard_nonce(slack_app_id)
            if stored_nonce is None:
                return _uniform_401()
            if not hmac.compare_digest(stored_nonce, nonce_qs):
                return _uniform_401()

        record("url_verification", NO_ITEM_TYPE)
        db.commit()
        advance_setup_state(db, app, verified_secret, "draft", "signing_verified")
        return PlainTextResponse(content=challenge, status_code=200)

    if shape.type != "event_callback":
        # Slack expects 200 for envelope types we don't act on.
        record(str(shape.type), NO_ITEM_TYPE)
        db.commit()
        return PlainTextResponse(content="", status_code=200)

    # A secret rotated outside the wizard gets no new url_verification;
    # a signed event proves the same thing.
    advance_setup_state(db, app, verified_secret, "draft", "signing_verified")
    record_event_arrival(db, app)
    # An event shows push reaches some channel, not the one that missed,
    # so the outage ends only once polls stop finding misses.
    if app.push_missing_since and push_stale(app.push_last_miss_at):
        app.push_missing_since = None
        db.commit()
    event_type = slack_event_type(shape)
    # The signature only proves the app's owner sent this; an event counts
    # only for a workspace the app was authorized into.
    if not app_has_workspace(db, app.id, shape.team_id):
        log.info("slack event for an unconnected workspace dropped", extra={"slack_app_id": slack_app_id})
        record(event_type, REJECTED)
        db.commit()
        return PlainTextResponse(content="", status_code=200)

    label, task, kwargs = plan_event_callback(shape, slack_app_id)
    # A channel a push may not reach Memory from leaves only a stub; a delete
    # still drops whatever copy Memory has.
    if label.startswith("rejected"):
        outcome = REJECTED
    elif kwargs["channel_id"] and not channel_collected(db, kwargs["workspace_id"], kwargs["channel_id"]):
        outcome = NOT_COLLECTED
    else:
        # A mention's message arrives as its own event and is stored from that.
        outcome = STORED if task or event_type == "app_mention" else NO_ITEM_TYPE
    untracked_delete = label == "mark_deleted" and outcome == NOT_COLLECTED
    # Only these tasks write item events; reactions and channel updates change what's stored.
    links_items = outcome == STORED and label in ("add_message", "mark_deleted")
    raw_id = record(event_type, outcome, kwargs.get("channel_id"), links_items=links_items)
    if raw_id is None:
        # A retry of an event already taken.
        db.rollback()
        return PlainTextResponse(content="", status_code=200)
    log.info(
        "slack event received",
        extra={
            "slack_app_id": slack_app_id,
            "event_type": event_type,
            "hmac_ok": True,
            "ts_skew_seconds": skew,
            "body_sha256": body_sha[:16],
        },
    )
    if task and (outcome == STORED or untracked_delete):
        try:
            celery_app.send_task(task, kwargs={**kwargs, "caused_by": raw_id})
        except Exception:
            # Slack retries a failed event, which must not be a duplicate.
            db.rollback()
            raise
    db.commit()
    # A content event shows push works, which is what 'live' claims, even from
    # a channel not collected; lifecycle, ignored and malformed events don't.
    if task:
        advance_setup_state(db, app, verified_secret, "signing_verified", "live")
    return PlainTextResponse(content="", status_code=200)


# ---------------------------------------------------------------------------
# Wizard endpoints (slack-changes.md §3.4) — owner-only secret + nonce flow
# ---------------------------------------------------------------------------


WIZARD_NONCE_TTL_SECONDS = 30 * 60  # 30 min — long enough for the user to
# paste the URL into Slack and click Save.


class SlackAppSecretBody(BaseModel):
    secret: str


class SlackAppNonceResponse(BaseModel):
    nonce: str
    callback_url: str
    events_url: str


class SlackOAuthStateResponse(BaseModel):
    """Signed OAuth state issued for the wizard's "Open Slack OAuth" step.

    The frontend includes ``state`` in the Slack authorize URL; Slack
    echoes it back to ``/slack/callback/{slack_app_id}`` where
    ``validate_and_consume_state`` checks the signature and one-time
    use, and the authenticated user must match the state's ``user_id``.
    """

    state: str


class SlackWizardStatus(BaseModel):
    setup_state: str
    has_credentials: bool


def _events_url_for(slack_app_id: int, nonce: str) -> str:
    return f"{settings.SERVER_URL}/slack/events/{slack_app_id}?wizard_nonce={nonce}"


def _callback_url_for(slack_app_id: int) -> str:
    return f"{settings.SERVER_URL}/slack/callback/{slack_app_id}"


@router.post("/apps/{app_id}/client-secret", response_model=SlackAppResponse)
def set_client_secret(
    app_id: int,
    body: SlackAppSecretBody,
    user: User = Depends(get_interactive_user),
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
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SlackAppResponse:
    """Store the SlackApp signing_secret encrypted. Owner only.

    State stays 'draft' until /slack/events receives a request signed with
    it, and a different secret sends a verified app back to 'draft'.
    """
    # Locked so an event's state advance can't land between this read and the reset to 'draft'.
    app = get_slack_app_for_owner(db, app_id, user, lock=True)
    secret = body.secret.strip()
    if not secret:
        raise HTTPException(status_code=400, detail="signing_secret cannot be empty")
    if secret != app.signing_secret:
        app.signing_secret = secret
        app.setup_state = "draft"
    db.commit()
    db.refresh(app)
    return slack_app_to_response(app, user)


@router.post("/apps/{app_id}/wizard-nonce", response_model=SlackAppNonceResponse)
def issue_wizard_nonce(
    app_id: int,
    user: User = Depends(get_interactive_user),
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
    _set_wizard_nonce(app.id, nonce, WIZARD_NONCE_TTL_SECONDS)
    return SlackAppNonceResponse(
        nonce=nonce,
        callback_url=_callback_url_for(app.id),
        events_url=_events_url_for(app.id, nonce),
    )


@router.post("/apps/{app_id}/oauth-state", response_model=SlackOAuthStateResponse)
def issue_oauth_state(
    app_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SlackOAuthStateResponse:
    """Mint a fresh OAuth state for the wizard's "Open Slack OAuth" step.

    The frontend includes the returned ``state`` as the ``state=`` query
    parameter on the Slack authorize URL. Slack echoes it back to
    ``/slack/callback/{slack_app_id}`` where it's validated against the
    stored row, signed-state HMAC, and the authenticated user.

    Owner-only. The state is bound to ``user.id`` so the callback's
    session-vs-state user check (CSRF binding) succeeds.
    """
    app = get_slack_app_for_owner(db, app_id, user)
    require_chat_url(app.authorize_url, user)
    raw_state = generate_state()
    store_state(db, raw_state, "slack", user.id)
    return SlackOAuthStateResponse(state=sign_state(raw_state, user.id))


@router.get("/apps/{app_id}/wizard-status", response_model=SlackWizardStatus)
def wizard_status(
    app_id: int,
    user: User = Depends(get_interactive_user),
    db: Session = Depends(get_session),
) -> SlackWizardStatus:
    """Return wizard progress for the frontend's polling loop."""
    app = get_slack_app_for_owner(db, app_id, user)
    return SlackWizardStatus(setup_state=app.setup_state, has_credentials=app_connected(db, app.id))
