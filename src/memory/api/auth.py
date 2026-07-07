import logging
import re
from datetime import datetime, timedelta, timezone
from functools import cache
from typing import TypeVar, cast

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware

from memory.common import settings
from memory.common.db.connection import DBSession, get_session, make_session
from memory.common.db.models import (
    APIKey,
    BotUser,
    MCPServer,
    HumanUser,
    OAuthRefreshToken,
    User,
    UserSession,
)
from memory.common.access_control import get_user_project_roles, has_admin_scope
from memory.common.db.models.base import Base
from memory.common.mcp import mcp_tools_list
from memory.common.oauth import complete_oauth_flow
from memory.common.scopes import SCOPE_ADMIN

T = TypeVar("T", bound=Base)

logger = logging.getLogger(__name__)

# Flag to log DISABLE_AUTH warning only once (not per-request)
_auth_disabled_warning_logged = False

# Create router
router = APIRouter(prefix="/auth", tags=["auth"])

# Endpoints that don't require authentication (prefix matching).
# `/register` and `/revoke` are the OAuth Dynamic Client Registration
# (RFC 7591) and Token Revocation (RFC 7009) endpoints exposed by the
# MCP SDK at root. DCR clients have no credentials at registration time
# by definition, so the middleware cannot demand them; otherwise new
# MCP clients can't enroll and POST /register returns 401 before the
# OAuth provider's handler runs.
WHITELIST = {
    "/health",
    "/authorize",
    "/token",
    "/register",
    "/revoke",
    "/mcp",
    "/oauth/",
    "/.well-known/",
    "/ui",
    "/google-drive/callback",  # Google OAuth callback
    "/polls/respond",  # Public poll response endpoints
    # Cloud-claude file transfer: gated by HMAC-signed presigned tokens
    # (query string for pull, Bearer header for push) inside the endpoints
    # themselves — must bypass OAuth or curl can't reach them.
    "/claude/transfer/",
    # Generic content upload. The endpoint authenticates the request itself
    # by verifying the signed ingest token in the ``?token=`` query string
    # (ingest_tokens.verify_token: HMAC over a domain-tagged payload + exp +
    # payload-schema check). The signed payload carries the user_id that
    # scopes the write, so the token IS the authorization for this one
    # upload — the add_content MCP tool mints it and hands the upload_url to
    # a non-browser client that has no session cookie. Without this carve-out
    # the middleware 401s the PUT before the in-endpoint token check fires,
    # so the documented "PUT the bytes" step is impossible.
    "/ingest/upload",
    # Slack push-events webhook. Slack POSTs ``event_callback`` payloads
    # here and authenticates via x-slack-signature (HMAC-SHA256 over the
    # body using the per-app signing secret) — it cannot present a
    # session cookie or Bearer token. The endpoint enforces its own
    # crypto auth (slack.py:slack_events). Without this carve-out the
    # AuthenticationMiddleware 401s every Slack POST before the HMAC
    # check fires, leaving the Slack integration silently broken.
    # Trailing slash means the prefix match covers both
    # ``/slack/events/{slack_app_id}`` and any future query-string
    # variants the Slack API surface introduces. Sibling Slack admin
    # endpoints (``/slack/workspaces/...``, ``/slack/apps/...``, etc.)
    # are deliberately NOT under this prefix, so this does not weaken
    # auth on the operator-facing Slack APIs.
    "/slack/events/",
}


def is_whitelisted_path(path: str) -> bool:
    """Decide whether ``path`` should bypass the AuthenticationMiddleware.

    Trailing-slash whitelist entries (``/oauth/``, ``/.well-known/``)
    match anything below them. Bare entries (``/health``, ``/mcp``,
    ``/ui``, …) match the exact path or a child segment delimited by
    ``/`` — they deliberately do NOT match ``/healthcheck`` or
    ``/mcphidden``. The previous ``str.startswith`` everywhere version
    was a latent auth-bypass any time someone added a sibling route
    sharing a prefix with one of the bare entries.
    """
    if path == "/":
        return True
    for entry in WHITELIST:
        if entry.endswith("/"):
            if path.startswith(entry):
                return True
        elif path == entry or path.startswith(entry + "/"):
            return True
    return False

# Claude WebSocket session path pattern - more specific than prefix matching.
# Session IDs have format: u{user_id}-{source}-{hex} where source is:
#   e{env_id}  for environment-based sessions   (e.g. u123-e456-abcdef012345)
#   s{snap_id} for snapshot-based sessions      (e.g. u123-s789-abcdef012345)
#   x          for sessions without snapshot/environment (e.g. u123-x-abcdef012345)
# Whitelisted here so WebSocket clients can authenticate via ?token= query param.
#
# The hex suffix is generated by ``cloud_claude.make_session_id`` via
# ``secrets.token_hex(16)`` -> 32 hex chars (128 bits). The canonical
# session-id regex lives in ``cloud_claude._SESSION_ID_RE``; if those
# token-hex semantics ever change, both regexes must stay in sync.
#
# Two anchoring properties matter for security here:
#
# 1. **Path-segment boundary** (``(?:/|$)``). Without it, the prefix-only
#    regex would match e.g. ``/claude/u1-e2-abcdef012345-admin-shell`` —
#    *any* path that just happens to start with a valid session-id prefix
#    would inherit the auth-bypass. Today every route under
#    ``/claude/{session_id}/...`` does its own auth, so this is a latent
#    defense-in-depth issue rather than an active bypass; but a future
#    contributor adding a route that forgets ``Depends(get_current_user)``
#    would inherit unauthenticated access without a single test failure.
#    The ``(?:/|$)`` anchor restricts the match to the bare session-id or
#    a proper subpath under it — never adjacent characters.
#
# 2. **Hex length floor** (``{32,}``). Without it, ``[a-fA-F0-9]+`` accepts
#    a single hex character. An attacker enumerating short hex prefixes
#    has trivially fewer values to brute-force; pinning the floor to the
#    actual generator length closes the search-space-shrinking footgun
#    even though the WS routes still gate on a stronger session-ownership
#    check downstream. 32 chars = 128 bits, the modern bar for
#    unguessable session tokens.
_CLAUDE_SESSION_PATTERN = re.compile(
    r"^/claude/u\d+-(?:e\d+|s\d+|x)-[a-fA-F0-9]{32,}(?:/|$)",
    re.IGNORECASE,
)

# Prefixes that identify a token as an API key (vs a session token)
API_KEY_PREFIXES = (
    "user_",  # Legacy prefix for migrated user keys
    "internal_",
    "discord_",
    "google_",
    "github_",
    "mcp_",
    "ot_",  # Key type prefixes
)


def get_bearer_token(request: Request) -> str | None:
    """Get bearer token from request"""
    bearer_token = request.headers.get("Authorization", "").split(" ")
    if len(bearer_token) != 2:
        return None
    return bearer_token[1]


def get_token(request: Request) -> str | None:
    """Get token from request.

    Checks in order: Authorization Bearer header, session cookie, access_token cookie.
    The access_token cookie fallback supports iframe embeds (e.g. report viewer)
    which can't set Authorization headers.
    """
    return (
        get_bearer_token(request)
        or request.cookies.get(settings.SESSION_COOKIE_NAME)
        or request.cookies.get("access_token")
    )


def create_user_session(
    user_id: int, db: DBSession, valid_for: int = settings.SESSION_VALID_FOR
) -> str:
    """Create a new session for a user"""
    expires_at = datetime.now(timezone.utc) + timedelta(days=valid_for)

    session = UserSession(user_id=user_id, expires_at=expires_at)
    db.add(session)
    db.commit()

    return str(session.id)


def is_expired(expires_at: datetime | None) -> bool:
    """Return True if ``expires_at`` is in the past (UTC-correct).

    PostgreSQL stores session expiry as naive UTC; some drivers / pool
    configs return tz-aware datetimes. ``.replace(tzinfo=UTC)`` only
    works for the naive case — for an already-aware datetime in a
    non-UTC zone it relabels rather than converting and silently shifts
    the wall clock. This helper handles both:

    - naive → assume UTC (matches how the column is written)
    - aware → astimezone(UTC) so the comparison is wall-clock-correct

    A NULL expires_at is treated as expired (fail-closed).
    """
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    else:
        expires_at = expires_at.astimezone(timezone.utc)
    return expires_at < datetime.now(timezone.utc)


def get_user_session(request: Request, db: DBSession) -> UserSession | None:
    """Get session ID from request"""
    session_id = get_token(request)

    if not session_id:
        return None

    session = db.get(UserSession, session_id)
    if not session:
        return None

    if is_expired(session.expires_at):
        return None
    return session


def lookup_api_key(api_key: str, db: DBSession) -> APIKey | None:
    """Look up an API key in the database using indexed lookup.

    Uses direct database query with unique index on APIKey.key for O(1) lookup
    instead of loading all keys. This prevents memory exhaustion with many keys.

    Note: This has a minor timing side-channel (attacker could learn if
    a key exists by response time), but the DoS risk from loading all
    keys is more significant for this use case.

    Args:
        api_key: The API key string to look up.
        db: Database session.

    Returns:
        The matching APIKey record, or None if not found.
    """
    return db.query(APIKey).filter(APIKey.key == api_key).first()


def authenticate_bot(api_key: str, db: DBSession) -> BotUser | None:
    """Authenticate a bot by API key.

    This is a convenience wrapper around authenticate_by_api_key() for bot-only auth.

    Args:
        api_key: The API key to authenticate.
        db: Database session.

    Returns:
        The BotUser if authenticated and is a bot, None otherwise.
    """
    user, _ = authenticate_by_api_key(api_key, db)
    if user is not None and user.user_type == "bot":
        return cast(BotUser, user)
    return None


def authenticate_by_api_key(
    api_key: str, db: DBSession, allowed_key_types: list[str] | None = None
) -> tuple[User | None, APIKey | None]:
    """Authenticate any user by API key.

    Uses constant-time comparison to prevent timing attacks.

    For one-time keys, consumes the key atomically (UPDATE ... RETURNING,
    marking it revoked): if two concurrent requests present the same ot_*
    token, only one wins. The race-loser gets ``(None, None)`` instead of
    double-authenticating.

    Args:
        api_key: The API key to authenticate.
        db: Database session.
        allowed_key_types: Optional list of allowed key types. If None, all types allowed.

    Returns:
        Tuple of (user, api_key_record).
    """
    matched_key = lookup_api_key(api_key, db)
    if matched_key is None or not matched_key.is_valid():
        return None, None

    # Check key type restriction if specified
    if allowed_key_types and matched_key.key_type not in allowed_key_types:
        return None, None

    # Eagerly load user before handle_api_key_use, which commits (and for
    # one-time keys revokes). This prevents DetachedInstanceError on lazy load.
    user = matched_key.user
    if not handle_api_key_use(matched_key, db):
        # Concurrent request consumed the one-time key first. Debug-level
        # so it stays out of normal production logs unless an operator is
        # chasing a "401 from a fresh key" report and bumps verbosity.
        logger.debug(
            "One-time API key %s consumed by concurrent request; this request loses race",
            matched_key.id,
        )
        return None, None
    return user, matched_key


def handle_api_key_use(key_record: APIKey, db: DBSession) -> bool:
    """Handle API key usage: update last_used_at and consume one-time keys.

    Warning: This function commits the database session. This is intentional
    to ensure one-time keys are consumed before request processing begins,
    preventing replay attacks. For regular keys, this updates the last_used_at
    timestamp immediately.

    For one-time keys: soft-deletes via an atomic
    ``UPDATE ... SET revoked WHERE id=:id AND NOT revoked RETURNING id`` so
    concurrent requests cannot both succeed. Returns False if another request
    already consumed the key (race-loser); True otherwise. The row is kept
    (rather than hard-deleted) so the mint rate limit in
    ``MCP.servers.meta.create_one_time_key`` can count keys issued in its
    rolling window; a maintenance task purges rows once they age out.

    For regular keys: updates last_used_at and returns True.
    """
    if key_record.is_one_time:
        result = db.execute(
            update(APIKey)
            .where(APIKey.id == key_record.id, APIKey.revoked.is_(False))
            .values(revoked=True, last_used_at=datetime.now(timezone.utc))
            .returning(APIKey.id)
        )
        won = result.scalar_one_or_none() is not None
        db.commit()
        return won

    key_record.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return True


def authenticate_request(
    request: Request, db: DBSession, allowed_key_types: list[str] | None = None
) -> tuple[User | None, str | None]:
    """Authenticate a request and return user with auth method info.

    Returns:
        Tuple of (user, key_type) where:
        - user: The authenticated User or None
        - key_type: The API key type if authenticated via API key, None for session tokens
    """
    token = get_token(request)
    if not token:
        return None, None

    # Check if this looks like an API key (various prefixes)
    if any(token.startswith(prefix) for prefix in API_KEY_PREFIXES):
        user, api_key = authenticate_by_api_key(token, db, allowed_key_types)
        key_type = api_key.key_type if api_key else None
        return user, key_type

    # Otherwise treat as session token
    if session := get_user_session(request, db):
        return session.user, None
    return None, None


def get_session_user(
    request: Request, db: DBSession, allowed_key_types: list[str] | None = None
) -> User | None:
    """Get user from session ID or API key if valid.

    Supports two authentication methods:
    - Session tokens (UUIDs from UserSession table)
    - API keys (from api_keys table)

    Args:
        request: The HTTP request.
        db: Database session.
        allowed_key_types: Optional list of allowed API key types. If None, all types allowed.
    """
    user, _ = authenticate_request(request, db, allowed_key_types)
    return user


def get_current_user(request: Request, db: DBSession = Depends(get_session)) -> User:
    """FastAPI dependency to get current authenticated user.

    First checks if user was already authenticated by middleware (stored in
    request.state.authenticated_user_id). This is important for one-time keys,
    which are consumed on first use - the middleware authenticates and revokes
    the key, then stores the user_id so endpoints don't try to re-authenticate.
    """
    # Check if middleware already authenticated this request
    if hasattr(request.state, "authenticated_user_id"):
        user = db.get(User, request.state.authenticated_user_id)
        if user:
            return user
        # User was deleted after middleware authenticated - fall through to re-auth
        # which will also fail, returning appropriate 401

    # Fall back to normal authentication. This handles:
    # 1. Whitelisted paths where middleware doesn't run
    # 2. User deleted after middleware auth (db.get returned None above)
    user = get_session_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return user


def get_user_from_token(
    token: str, db: DBSession, allowed_key_types: list[str] | None = None
) -> User | None:
    """Authenticate user from a token string.

    Supports both session tokens (UUIDs) and API keys.
    Useful for WebSocket authentication where tokens are passed via query params.

    Args:
        token: The token to authenticate.
        db: Database session.
        allowed_key_types: Optional list of allowed API key types. If None, all types allowed.
    """
    if not token:
        return None

    # Check if this looks like an API key
    if any(token.startswith(prefix) for prefix in API_KEY_PREFIXES):
        user, _ = authenticate_by_api_key(token, db, allowed_key_types)
        return user

    # Otherwise treat as session token
    session = db.get(UserSession, token)
    if not session:
        return None

    if is_expired(session.expires_at):
        return None
    return session.user


def require_scope(scope: str):
    """Dependency that checks if user has required scope.

    Usage:
        @router.get("/admin")
        def admin_endpoint(user: User = require_scope("admin:users")):
            ...
    """

    def checker(user: User = Depends(get_current_user)) -> User:
        user_scopes = user.scopes or []
        if SCOPE_ADMIN in user_scopes or scope in user_scopes:
            return user
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    return Depends(checker)


def require_key_types(*allowed_types: str):
    """Dependency that restricts authentication to specific API key types.

    By default, all key types are allowed. Use this to restrict endpoints
    to only accept certain key types.

    Usage:
        @router.get("/discord-only")
        def discord_endpoint(user: User = require_key_types("discord", "internal")):
            # Only accepts discord or internal API keys (and session tokens)
            ...

    Args:
        allowed_types: Key types to allow (e.g., "discord", "internal", "mcp").
                      Session tokens (non-API-key auth) are always allowed since
                      they represent interactive user sessions (logged in via
                      username/password), which should have full access.
    """
    allowed_list = list(allowed_types)

    def checker(request: Request, db: DBSession = Depends(get_session)) -> User:
        # Check if middleware already authenticated this request
        if hasattr(request.state, "authenticated_user_id"):
            key_type = getattr(request.state, "authenticated_key_type", None)
            # Session tokens (key_type=None) always bypass this check - they represent
            # interactive user sessions with full access, not programmatic API keys
            if key_type is None or key_type in allowed_list:
                user = db.get(User, request.state.authenticated_user_id)
                if user:
                    return user
            # Key type not in allowed list
            raise HTTPException(
                status_code=401,
                detail=f"Authentication required with key type: {', '.join(allowed_list)}",
            )

        # Fall back to normal authentication (for whitelisted paths, etc.)
        user = get_session_user(request, db, allowed_key_types=allowed_list)
        if not user:
            raise HTTPException(
                status_code=401,
                detail=f"Authentication required with key type: {', '.join(allowed_list)}",
            )
        return user

    return Depends(checker)


def get_user_account(db: DBSession, model: type[T], account_id: int, user: User) -> T:
    """Get an account by ID, ensuring it belongs to the user or user is admin.

    Generic helper for verifying ownership of user-scoped resources.
    Returns 404 for both "not found" and "not yours" to avoid leaking info.
    Admins can access any account.

    Args:
        db: Database session
        model: SQLAlchemy model class (must have user_id column)
        account_id: ID of the account to retrieve
        user: Current authenticated user

    Returns:
        The account if found and owned by user (or user is admin)

    Raises:
        HTTPException: 404 if account not found or not owned by user (and not admin)
    """
    account = db.get(model, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.user_id != user.id and not has_admin_scope(user):  # type: ignore[attr-defined]
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def assert_project_membership(
    db: DBSession, user: User, project_id: int | None
) -> None:
    """Raise 403 unless `user` is admin or a member of `project_id`.

    Mirrors the membership check PR #74 added to tidbit_add/tidbit_update.
    Non-members assigning content to a project violates the access invariant
    in both directions: (a) attacker plants content into a confidential
    project, (b) the access-filter pipeline then surfaces it to legitimate
    members. ``project_id=None`` is a no-op (NULL → admin-only by convention,
    handled at read time).
    """
    if project_id is None:
        return
    if has_admin_scope(user):
        return
    roles = get_user_project_roles(db, user)
    if project_id not in roles:
        raise HTTPException(
            status_code=403, detail="Not a member of target project"
        )


def resolve_user_filter(
    user_id: int | None, current_user: User, db: DBSession
) -> int | None:
    """Resolve user_id filter for admin queries.

    Used by endpoints that support admin viewing of other users' data.
    Non-admin users always see only their own data regardless of the
    user_id parameter.

    Args:
        user_id: Requested user ID filter (None for all users)
        current_user: The authenticated user making the request
        db: Database session for user validation

    Returns:
        - None if admin requests all users (user_id omitted)
        - Specific user_id if admin requests specific user
        - current_user.id if non-admin (ignores user_id param)

    Raises:
        HTTPException 404 if requested user doesn't exist
    """
    if not has_admin_scope(current_user):
        # Non-admins can only see their own data
        return current_user.id

    if user_id is None:
        # Admin with no filter - return all users
        return None

    # Admin requesting specific user - verify they exist
    target_user = db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    return user_id


def create_user(email: str, password: str, name: str, db: DBSession) -> HumanUser:
    """Create a new human user.

    The select-then-insert is a TOCTOU race: two concurrent registrations
    with the same email both pass the existence check, then one
    db.commit() lands and the other raises IntegrityError. Catching
    IntegrityError on commit converts that into a clean 400 instead of a
    500, matching the UX of the early-existence path. The unique
    constraint on users.email is the actual safety net.
    """
    # Best-effort early check so the common "duplicate email" path
    # returns a friendly 400 without churning a transaction. Real
    # serialization happens on commit.
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User already exists")

    user = HumanUser.create_with_password(email, name, password)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400, detail="User with this email already exists"
        )
    db.refresh(user)

    return user


@cache
def dummy_password_hash() -> str:
    """Return a real bcrypt hash used as a constant-time dummy.

    The cached value is generated on first call (paying the ~250ms bcrypt cost
    once per process) and reused thereafter. We deliberately use the same cost
    factor (12) as :func:`hash_password` so that ``verify_password`` against
    this hash performs the *same amount of work* as a real-user check —
    that's the whole point of the dummy in :func:`authenticate_user`.

    The previous implementation used a hard-coded 46-character string that was
    not a valid bcrypt hash. ``bcrypt.checkpw`` raised ``ValueError("Invalid
    salt")`` almost immediately, defeating the timing-attack mitigation and
    letting attackers enumerate accounts by response latency.
    """
    return bcrypt.hashpw(b"dummy", bcrypt.gensalt(rounds=12)).decode("utf-8")


def authenticate_user(email: str, password: str, db: DBSession) -> HumanUser | None:
    """Authenticate a human user by email and password.

    Uses constant-time comparison to prevent timing-based user enumeration.
    """
    user = db.query(HumanUser).filter(HumanUser.email == email).first()

    # Always perform password check to prevent timing attacks
    # Even if user doesn't exist, we do a dummy check
    if user:
        if user.is_valid_password(password):
            return user
    else:
        # Dummy password check to prevent timing-based user enumeration.
        # This ensures the function takes similar time whether user exists or
        # not. The dummy hash is a real bcrypt hash so checkpw runs the full
        # 12-round computation rather than failing fast on a malformed string.
        from memory.common.db.models.users import verify_password

        verify_password(password, dummy_password_hash())

    return None


@router.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request, db: DBSession = Depends(get_session)):
    """Logout and clear session.

    Deleting the ``UserSession`` row alone is not enough: any
    ``OAuthRefreshToken`` minted alongside this session via
    ``make_token_from_data`` is keyed on
    ``access_token_session_id == session.id`` and can be replayed by
    anyone who already exfiltrated the refresh token (browser leak,
    malicious extension, stolen device). Without revoking those, "logout"
    silently leaves the OAuth refresh-token family alive and there is no
    user-facing way to kill it.

    Fix: in the same transaction as the session delete, mark every
    refresh token paired with this exact session ``revoked=True``. We
    intentionally do NOT call ``revoke_refresh_token_family`` (which
    would nuke every refresh token for the (user, client) pair) because
    that tears down sessions on the user's *other* devices too — not
    what a single-device logout should do.
    """
    user_session = get_user_session(request, db)
    if user_session:
        # Revoke every active refresh token paired with this access-token
        # session. Use a bulk UPDATE so the operation is one statement
        # regardless of how many refresh-token rotations have happened.
        db.execute(
            update(OAuthRefreshToken)
            .where(
                OAuthRefreshToken.access_token_session_id == user_session.id,
                OAuthRefreshToken.revoked == False,  # noqa: E712
            )
            .values(revoked=True)
        )
        db.delete(user_session)
        db.commit()
    return {"message": "Logged out successfully"}


@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    """Get current user info"""
    return user.serialize()


@router.get("/callback/discord")
async def oauth_callback_discord(request: Request):
    """Get current user info"""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    # Log OAuth callback without sensitive data (code/state could be intercepted)
    logger.info("Received OAuth callback")

    message, title, close, status_code = "", "", "", 200
    if error:
        logger.error(f"OAuth error: {error}")
        message = f"Error: {error}"
        title = "❌ Authorization Failed"
        status_code = 400
    elif not code or not state:
        message = "Missing authorization code or state parameter."
        title = "❌ Invalid Request"
        status_code = 400
    else:
        # The OAuth flow does two HTTPS round-trips (token exchange and MCP
        # tools list). Holding a sync DB session across those `await`s pinned
        # a connection from the pool for the duration of the upstream calls
        # — under concurrent OAuth callbacks that's a clean route to pool
        # starvation. Split into short DB scopes around the network I/O.

        # Phase 1: locate the row and detach it from the session so its
        # column attributes survive past session close. We pass the detached
        # object to complete_oauth_flow which mutates its in-memory
        # attributes; we then write those back in a fresh session.
        with make_session() as session:
            mcp_server = (
                session.query(MCPServer).filter(MCPServer.state == state).first()
            )
            if not mcp_server:
                return Response(
                    content="MCP server not found",
                    status_code=404,
                )
            server_id = cast(int, mcp_server.id)
            # Force-load all columns we'll need post-detach. SQLAlchemy
            # already loaded them via the SELECT above; the explicit access
            # documents the dependency and fails fast if the schema changes.
            _ = (
                mcp_server.mcp_server_url,
                mcp_server.client_id,
                mcp_server.code_verifier,
            )
            session.expunge(mcp_server)

        # Phase 2: network I/O — no DB connection held.
        status_code, message = await complete_oauth_flow(mcp_server, code, state)

        # Phase 3: persist the mutations complete_oauth_flow wrote onto the
        # detached object. Re-fetch by primary key so a concurrent admin
        # update on the same row isn't clobbered (only the OAuth-token
        # fields and the temporary state/code_verifier are written).
        with make_session() as session:
            server = session.get(MCPServer, server_id)
            if server is not None:
                server.access_token = mcp_server.access_token  # type: ignore
                server.refresh_token = mcp_server.refresh_token  # type: ignore
                server.token_expires_at = mcp_server.token_expires_at  # type: ignore
                server.state = mcp_server.state  # type: ignore  # cleared on success
                server.code_verifier = mcp_server.code_verifier  # type: ignore  # cleared on success
                session.commit()

        # Phase 4: second network I/O — only on success.
        if 200 <= status_code < 300:
            tools = await mcp_tools_list(
                cast(str, mcp_server.mcp_server_url),
                cast(str, mcp_server.access_token),
            )
            available_tools = [
                name for tool in tools if (name := tool.get("name"))
            ]
            logger.info(f"MCP server tools: {tools}")

            # Phase 5: persist the tool list in a fresh short session.
            with make_session() as session:
                server = session.get(MCPServer, server_id)
                if server is not None:
                    server.available_tools = available_tools  # type: ignore
                    session.commit()

        if 200 <= status_code < 300:
            title = "✅ Authorization Successful!"
            close = "You can close this window and return to the MCP server."
        else:
            title = "❌ Authorization Failed"

    from html import escape

    return Response(
        content=f"""
        <html>
            <body>
                <h1>{escape(title)}</h1>
                <p>{escape(message)}</p>
                <p>{close}</p>
            </body>
        </html>
        """,
        status_code=status_code,
    )


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Middleware to require authentication for all endpoints except whitelisted ones."""

    async def dispatch(self, request: Request, call_next):
        global _auth_disabled_warning_logged
        if settings.DISABLE_AUTH:
            # Log warning only once to avoid flooding logs during development
            if not _auth_disabled_warning_logged:
                logger.warning(
                    "DISABLE_AUTH is enabled - all endpoints are publicly accessible. "
                    "This should ONLY be used for local development."
                )
                _auth_disabled_warning_logged = True
            return await call_next(request)

        path = request.url.path

        # Skip authentication for whitelisted endpoints. Use the explicit
        # exact-or-child-segment matcher rather than a raw `startswith` so
        # adding a future route like `/mcphost` doesn't accidentally
        # whitelist itself.
        if is_whitelisted_path(path) or _CLAUDE_SESSION_PATTERN.match(path):
            return await call_next(request)

        # Check for session ID in header or cookie
        session_id = get_token(request)
        if not session_id:
            return Response(
                content="Authentication required",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Validate session and get user
        with make_session() as session:
            user, key_type = authenticate_request(request, session)
            if not user:
                return Response(
                    content="Invalid or expired session",
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Store auth info in request state so endpoints don't re-authenticate
            # (important for one-time keys which are consumed on first use)
            request.state.authenticated_user_id = user.id
            request.state.authenticated_key_type = key_type  # None for session tokens

            # Log user ID instead of email for privacy
            logger.debug(f"Authenticated request from user_id={user.id} to {path}")

        return await call_next(request)
