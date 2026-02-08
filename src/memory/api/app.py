"""
FastAPI application for the knowledge base.
"""

import os
import logging
import mimetypes
import pathlib

from fastapi import Depends, FastAPI, UploadFile, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqladmin import Admin

from memory.common import extract, paths, settings
from memory.common.access_control import get_user_project_roles, has_admin_scope, user_can_access
from memory.common.db.connection import get_engine, get_session
from memory.common.db.models import User
from memory.common.db.models.source_items import Report
from memory.api.admin import AdminAuth, setup_admin
from memory.api.auth import (
    AuthenticationMiddleware,
    get_current_user,
    router as auth_router,
)
from memory.api.google_drive import router as google_drive_router
from memory.api.email_accounts import router as email_accounts_router
from memory.api.article_feeds import router as article_feeds_router
from memory.api.github_sources import router as github_sources_router
from memory.api.calendar_accounts import router as calendar_accounts_router
from memory.api.meetings import router as meetings_router
from memory.api.content_sources import router as content_sources_router
from memory.api.metrics import router as metrics_router
from memory.api.telemetry import router as telemetry_router
from memory.api.jobs import router as jobs_router
from memory.api.polls import router as polls_router
from memory.api.source_items import router as source_items_router
from memory.api.sessions import router as sessions_router
from memory.api.docker_logs import router as docker_logs_router
from memory.api.claude_snapshots import router as claude_snapshots_router
from memory.api.claude_environments import router as claude_environments_router
from memory.api.cloud_claude import router as cloud_claude_router
from memory.api.secrets import router as secrets_router
from memory.api.users import router as users_router
from memory.api.discord import router as discord_router
from memory.api.slack import router as slack_router
from memory.api.celery_overview import router as celery_overview_router
from memory.api.MCP.base import mcp

logger = logging.getLogger(__name__)

# Rate limiter setup
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.API_RATE_LIMIT_DEFAULT]
    if settings.API_RATE_LIMIT_ENABLED
    else [],
    enabled=settings.API_RATE_LIMIT_ENABLED,
)

# Create the MCP http app to get its lifespan
mcp_http_app = mcp.http_app(stateless_http=True)

# OpenAPI tag descriptions for better documentation organization
tags_metadata = [
    {"name": "auth", "description": "Authentication and authorization endpoints"},
    {"name": "users", "description": "User management"},
    {"name": "source-items", "description": "Manage ingested content items"},
    {"name": "jobs", "description": "Background job monitoring and management"},
    {"name": "sessions", "description": "User session management"},
    {
        "name": "google-drive",
        "description": "Google Drive integration for content ingestion",
    },
    {
        "name": "email-accounts",
        "description": "Email account configuration for ingestion",
    },
    {"name": "article-feeds", "description": "RSS/Atom feed subscriptions"},
    {"name": "github-sources", "description": "GitHub repository ingestion"},
    {"name": "calendar-accounts", "description": "Calendar integration"},
    {"name": "meetings", "description": "Meeting transcripts and summaries"},
    {"name": "content-sources", "description": "Content source configuration"},
    {"name": "metrics", "description": "System metrics and statistics"},
    {"name": "telemetry", "description": "Usage telemetry"},
    {"name": "polls", "description": "Create and manage polls"},
    {"name": "docker-logs", "description": "Docker container log access"},
    {"name": "claude-snapshots", "description": "Claude conversation snapshots"},
    {"name": "claude-environments", "description": "Claude environment management"},
    {"name": "cloud-claude", "description": "Cloud Claude integration"},
    {"name": "secrets", "description": "Secret management"},
    {"name": "discord", "description": "Discord integration"},
    {"name": "slack", "description": "Slack integration"},
    {"name": "celery", "description": "Celery task overview and ingestion summary"},
]

app = FastAPI(
    title="Memory Knowledge Base API",
    description="""
Personal knowledge base API for ingesting, indexing, and searching across your digital content.

## Features

- **Semantic Search**: Vector-based similarity search across all content
- **Content Ingestion**: Ingest emails, documents, web pages, ebooks, and more
- **MCP Integration**: Model Context Protocol support for AI assistants
- **Observations**: AI assistants can record and recall user preferences

## Authentication

Most endpoints require authentication via:
- **Session cookie**: For browser-based access
- **Bearer token**: API key in `Authorization: Bearer <token>` header
    """,
    version="1.0.0",
    openapi_tags=tags_metadata,
    lifespan=mcp_http_app.lifespan,
)
app.state.limiter = limiter


# Rate limit exception handler
@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    retry_after = getattr(exc, "retry_after", None)
    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "detail": str(exc.detail),
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)} if retry_after else {},
    )


# Validation error handler with detailed logging
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Log detailed validation error info
    logger.warning(
        "Validation error on %s %s: %s",
        request.method,
        request.url.path,
        exc.errors(),
    )

    # Log query parameters if present
    if request.query_params:
        logger.warning("Query params: %s", dict(request.query_params))

    # Try to log the request body for debugging (may fail for large bodies)
    try:
        body = await request.body()
        if body:
            # Truncate large bodies
            body_preview = body[:2000].decode("utf-8", errors="replace")
            if len(body) > 2000:
                body_preview += f"... ({len(body)} bytes total)"
            logger.warning("Request body: %s", body_preview)
    except Exception as e:
        logger.warning("Could not read request body: %s", e)

    # Return detailed error response
    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.errors(),
            "body": exc.body if hasattr(exc, "body") else None,
        },
    )


# Add rate limiting middleware
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(AuthenticationMiddleware)
# Configure CORS with specific origin to prevent CSRF attacks.
# allow_credentials=True requires specific origins, not wildcards.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.SERVER_URL, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def validate_path_within_directory(
    base_dir: pathlib.Path, requested_path: str
) -> pathlib.Path:
    """Validate that a requested path resolves within the base directory.

    Wraps the shared utility and converts ValueError to HTTPException.

    Args:
        base_dir: The allowed base directory
        requested_path: The user-provided path

    Returns:
        The resolved absolute path if valid

    Raises:
        HTTPException: If the path would escape the base directory or doesn't exist
    """
    try:
        return paths.validate_path_within_directory(
            base_dir, requested_path, require_exists=True
        )
    except ValueError as e:
        if "does not exist" in str(e):
            raise HTTPException(status_code=404, detail="File not found")
        raise HTTPException(status_code=403, detail="Access denied")


@app.get("/ui{full_path:path}")
async def serve_react_app(full_path: str):
    full_path = full_path.lstrip("/")
    try:
        index_file = validate_path_within_directory(settings.STATIC_DIR, full_path)
        if index_file.is_file():
            return FileResponse(index_file)
    except HTTPException:
        pass  # Fall through to index.html for SPA routing
    return FileResponse(settings.STATIC_DIR / "index.html")


@app.get("/reports/{path:path}")
async def serve_report(
    path: str,
    download: bool = False,
    user: User = Depends(get_current_user),
    db=Depends(get_session),
):
    """Serve a report file with per-report access control and CSP sandboxing for HTML."""
    file_path = validate_path_within_directory(settings.REPORT_STORAGE_DIR, path)

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Report not found")

    # Per-report access control - require a DB record for every served file
    try:
        filename = file_path.relative_to(settings.REPORT_STORAGE_DIR.resolve()).as_posix()
    except ValueError:
        raise HTTPException(status_code=404, detail="Report not found")
    report = db.query(Report).filter(Report.filename == filename).one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    project_roles = get_user_project_roles(db, user) if not has_admin_scope(user) else {}
    if not user_can_access(user, report, project_roles):
        raise HTTPException(status_code=403, detail="Access denied")

    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None:
        mime_type = "application/octet-stream"

    if download:
        return FileResponse(
            file_path,
            media_type=mime_type,
            filename=file_path.name,
            content_disposition_type="attachment",
        )

    response = FileResponse(file_path, media_type=mime_type)

    # CSP sandbox for HTML - prevents script execution in uploaded reports
    if mime_type in ("text/html", "application/xhtml+xml"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; sandbox"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"

    return response


@app.get("/files/{path:path}")
async def serve_file(path: str, download: bool = False):
    file_path = validate_path_within_directory(settings.FILE_STORAGE_DIR, path)

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None:
        mime_type = "application/octet-stream"

    if download:
        return FileResponse(
            file_path,
            media_type=mime_type,
            filename=file_path.name,
            content_disposition_type="attachment",
        )
    return FileResponse(file_path, media_type=mime_type)


async def input_type(item: str | UploadFile) -> list[extract.DataChunk]:
    if not item:
        return []

    if isinstance(item, str):
        return extract.extract_text(item)
    content_type = item.content_type or "application/octet-stream"
    return extract.extract_data_chunks(content_type, await item.read())


# SQLAdmin setup with authentication requiring admin scope
engine = get_engine()
_admin_secret_key = settings.SECRETS_ENCRYPTION_KEY
if not _admin_secret_key:
    import logging as _logging

    _logging.getLogger(__name__).warning(
        "SECRETS_ENCRYPTION_KEY not set - using insecure dev key for admin auth. "
        "Set SECRETS_ENCRYPTION_KEY in production!"
    )
    _admin_secret_key = "dev-secret-key"
admin = Admin(
    app, engine, authentication_backend=AdminAuth(secret_key=_admin_secret_key)
)

# Setup admin views
setup_admin(admin)
app.include_router(auth_router)
app.include_router(google_drive_router)
app.include_router(email_accounts_router)
app.include_router(article_feeds_router)
app.include_router(github_sources_router)
app.include_router(calendar_accounts_router)
app.include_router(meetings_router)
app.include_router(content_sources_router)
app.include_router(metrics_router)
app.include_router(telemetry_router)
app.include_router(jobs_router)
app.include_router(polls_router)
app.include_router(source_items_router)
app.include_router(sessions_router)
app.include_router(docker_logs_router)
app.include_router(claude_snapshots_router)
app.include_router(claude_environments_router)
app.include_router(cloud_claude_router)
app.include_router(secrets_router)
app.include_router(users_router)
app.include_router(discord_router)
app.include_router(slack_router)
app.include_router(celery_overview_router)


# Add health check to MCP server instead of main app
# Mount MCP server at root - OAuth endpoints need to be at root level
# Health check is defined in MCP/base.py
app.mount("/", mcp_http_app)


def main(reload: bool = False):
    """Run the FastAPI server in debug mode with auto-reloading."""
    import uvicorn

    uvicorn.run(
        "memory.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=reload,
        log_level="debug",
    )


if __name__ == "__main__":
    from memory.common.qdrant import setup_qdrant

    setup_qdrant()
    main(os.getenv("RELOAD", "false") == "true")
