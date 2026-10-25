import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import cache
from typing import Any, TypeVar, cast

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware

from memory.common import instance_settings, settings
from memory.common.instance_settings import VOYAGE_API_KEY
from memory.common.module_settings import enabled_modules
from memory.common.db.connection import DBSession, get_session, make_session
from memory.common.db.models import (
    APIKey,
    HumanUser,
    OAuthRefreshToken,
    User,
    UserSession,
)
from memory.common.access_control import (
    credential_scopes,
    get_user_project_roles,
    has_admin_scope,
)
from memory.common.db.models.base import Base
from memory.common.scopes import has_scope

T = TypeVar("T", bound=Base)

logger = logging.getLogger(__name__)

# Flag to log DISABLE_AUTH warning only once (not per-request)
_auth_disabled_warning_logged = False

# Create router
router = APIRouter(prefix="/auth", tags=["auth"])

AUTH_REQUIRED_BODY = "Authentication required"

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
    # External MCP server OAuth callback: authenticates the session itself
    # so a logged-out user gets a page to act on instead of a bare 401.
    "/auth/mcp/callback",
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
    # Transcript push ingest. Senders authenticate per account with the
    # webhook secret (HMAC of the body or a bearer token), checked inside
    # transcript_ingest.ingest_transcript; they have no session.
    "/transcripts/ingest/",
    # Forgotten-password request and confirm: the caller is by definition
    # logged out; confirm is authorized by the signed reset token.
    "/auth/password-reset",
    # First-run setup: status is what a logged-out browser checks before
    # login, and claiming is authorized by the setup token from the log.
    "/setup",
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


def authenticate_by_api_key(
    api_key: str, db: DBSession, allowed_key_types: list[str] | None = None
) -> tuple[User | None, APIKey | None]:
    """Authenticate any user by API key.

    Uses constant-time comparison to prevent timing attacks.

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

    # Eagerly load user before handle_api_key_use, which commits. This
    # prevents DetachedInstanceError on lazy load.
    user = matched_key.user
    handle_api_key_use(matched_key, db)
    return user, matched_key


def handle_api_key_use(key_record: APIKey, db: DBSession) -> None:
    """Update last_used_at. Commits the session."""
    key_record.last_used_at = datetime.now(timezone.utc)
    db.commit()


@dataclass
class TokenLookup:
    """Result of looking up a bearer token.

    Exactly one of ``user_session`` / ``api_key_record`` is non-None — they
    encode which credential matched. Both ``verify_token`` and
    ``load_access_token`` route on this distinction to project the right
    scope dimension; the lookup, expiry/validity check, and last-used bump
    are all shared via :func:`lookup_principal` so the two
    methods can never drift on those concerns.
    """

    user: User
    user_session: UserSession | None = None
    api_key_record: APIKey | None = None


def lookup_principal(
    token: str, session: DBSession, record_use: bool = True
) -> TokenLookup | None:
    """Resolve a bearer token to its (user, credential) pair.

    Tries the token as a ``UserSession`` row first (OAuth access tokens and
    frontend cookie sessions are both keyed on ``UserSession.id``); on miss,
    falls back to ``lookup_api_key`` for bot / discord / mcp
    keys.

    Side effects on success:

    * For UserSession matches: none — the caller decides whether to refresh
      anything based on its own scope/return-type contract.
    * For APIKey matches: ``handle_api_key_use`` is called *here* so the
      last_used_at bump happens with the lookup. Centralising it means both callers run it exactly once, and
      a future revocation column / audit hook / key-was-used webhook
      added to ``handle_api_key_use`` reaches both call sites for free.
      ``record_use=False`` skips it, for re-resolving a token that already
      authenticated this request.

    Returns ``None`` on:
    * unknown token
    * expired UserSession (uses :func:`is_expired` so a tz-aware
      ``expires_at`` from a non-UTC pool doesn't crash, addressing the
      naive-vs-aware comparison bug noted in audit 7affa983)
    * UserSession with no attached User (fail-closed)
    * APIKey not in valid state per ``is_valid``
    * APIKey with no attached User (fail-closed; symmetric with the
      session-path guard above. Schema-level FK constraints make this
      shape unreachable today, but pinning it here keeps a stale-FK or
      cascade-delete race from turning into a 500 — and lets downstream
      ``principal.user.id`` be a non-Optional access without comment.)
    """
    user_session = session.get(UserSession, token)
    if user_session is not None:
        if is_expired(user_session.expires_at):
            return None
        if user_session.user is None:
            return None
        return TokenLookup(user=user_session.user, user_session=user_session)

    api_key_record = lookup_api_key(token, session)
    if api_key_record is not None and api_key_record.is_valid():
        if api_key_record.user is None:
            # Match the session-path fail-closed above. Today's FK
            # constraints rule this out, but a future cascade race or
            # detached-row bug shouldn't escalate to 500.
            return None
        # Doing it here means every caller of lookup_principal bumps
        # last_used_at exactly once — never twice, never zero.
        if record_use:
            handle_api_key_use(api_key_record, session)
        return TokenLookup(user=api_key_record.user, api_key_record=api_key_record)

    return None


def system_scopes(principal: TokenLookup) -> list[str]:
    """The system scopes that gate a principal's MCP tools; see ``verify_token``."""
    return credential_scopes(principal.user, principal.api_key_record)


def authenticate_request(
    request: Request, db: DBSession, allowed_key_types: list[str] | None = None
) -> tuple[User | None, APIKey | None]:
    """Authenticate a request.

    Returns:
        Tuple of (user, api_key) where:
        - user: The authenticated User or None
        - api_key: The key that authenticated, None for session tokens
    """
    token = get_token(request)
    if not token:
        return None, None

    # Check if this looks like an API key (various prefixes)
    if any(token.startswith(prefix) for prefix in API_KEY_PREFIXES):
        return authenticate_by_api_key(token, db, allowed_key_types)

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


class CredentialUser:
    """Read-through account view with credential scopes, never ORM scope mutation."""

    def __init__(self, user: User, scopes: list[str]):
        self.account = user
        self.scopes = scopes

    def __getattr__(self, name: str) -> Any:
        return getattr(self.account, name)


def credential_user(user: User, key: APIKey | None) -> User:
    if key is None:
        return user
    # Endpoint annotations describe the account interface; this view forwards
    # its fields while keeping authorization scopes separate from the ORM row.
    return cast(User, CredentialUser(user, credential_scopes(user, key)))


def get_current_user(request: Request, db: DBSession = Depends(get_session)) -> User:
    """FastAPI dependency to get current authenticated user.

    First checks if user was already authenticated by middleware (stored in
    request.state.authenticated_user_id), so endpoints don't re-authenticate.
    """
    # Check if middleware already authenticated this request
    if hasattr(request.state, "authenticated_user_id"):
        user = db.get(User, request.state.authenticated_user_id)
        if user:
            return credential_user(user, request_api_key(request, db))
        # User was deleted after middleware authenticated - fall through to re-auth
        # which will also fail, returning appropriate 401

    # Fall back to normal authentication. This handles:
    # 1. Whitelisted paths where middleware doesn't run
    # 2. User deleted after middleware auth (db.get returned None above)
    user, key = authenticate_request(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return credential_user(user, key)


def get_interactive_user(
    request: Request,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> User:
    """Account recovery and stored credentials require a login session."""
    if request_api_key(request, db) is not None:
        raise HTTPException(status_code=403, detail="Interactive session required")
    return user


def require_rest_access(
    request: Request,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> User:
    """General REST reads require read; mutations require write."""
    scope = "read" if request.method in {"GET", "HEAD", "OPTIONS"} else "write"
    if not has_scope(credential_scopes(user, request_api_key(request, db)), scope):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return user


def get_user_from_token(token: str, db: DBSession, scope: str) -> User | None:
    """The user a session token or API key authenticates as, if that credential
    holds ``scope``. For WebSockets, which pass the token as a query param."""
    principal = lookup_principal(token, db) if token else None
    if principal is None or not has_scope(system_scopes(principal), scope):
        return None
    return principal.user


def request_api_key(request: Request, db: DBSession) -> APIKey | None:
    """The API key that authenticated ``request``; None for a session token."""
    if hasattr(request.state, "authenticated_user_id"):
        key_id = getattr(request.state, "authenticated_api_key_id", None)
        if key_id is None:
            return None
        key = db.get(APIKey, key_id)
        if key is None or not key.is_valid() or key.user_id != request.state.authenticated_user_id:
            raise HTTPException(status_code=401, detail="Invalid or expired API key")
        return key
    token = get_token(request)
    if token and any(token.startswith(prefix) for prefix in API_KEY_PREFIXES):
        return lookup_api_key(token, db)
    return None


def require_scope(scope: str):
    """Dependency that checks if user has required scope.

    Usage:
        @router.get("/admin")
        def admin_endpoint(user: User = require_scope("admin:users")):
            ...
    """

    def checker(
        request: Request,
        user: User = Depends(get_current_user),
        db: DBSession = Depends(get_session),
    ) -> User:
        # An API key's own scopes apply, so a narrow key can't borrow its
        # owner's wider grant.
        principal = TokenLookup(user=user, api_key_record=request_api_key(request, db))
        if has_scope(system_scopes(principal), scope):
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


def create_user(
    email: str, password: str, name: str, db: DBSession, scopes: list[str] | None = None
) -> HumanUser:
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
    if scopes is not None:
        user.scopes = scopes
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


def setup_warnings(user: User) -> list[str]:
    """Settings an admin still has to set for the instance to work fully."""
    if not has_admin_scope(user):
        return []
    return [] if instance_settings.value(VOYAGE_API_KEY) else [VOYAGE_API_KEY.key]


@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    """Get current user info, with the modules this instance runs."""
    return user.serialize() | {
        "modules": sorted(enabled_modules()),
        "setup_warnings": setup_warnings(user),
    }


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
                content=AUTH_REQUIRED_BODY,
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Validate session and get user
        with make_session() as session:
            user, api_key = authenticate_request(request, session)
            if not user:
                return Response(
                    content="Invalid or expired session",
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Store auth info in request state so endpoints don't re-authenticate
            request.state.authenticated_user_id = user.id
            # Both None for session tokens
            request.state.authenticated_key_type = api_key and api_key.key_type
            request.state.authenticated_api_key_id = api_key and api_key.id

            # Log user ID instead of email for privacy
            logger.debug(f"Authenticated request from user_id={user.id} to {path}")

        return await call_next(request)
