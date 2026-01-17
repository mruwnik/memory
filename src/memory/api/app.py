"""
FastAPI application for the knowledge base.
"""

import os
import logging
import mimetypes
import pathlib

from fastapi import FastAPI, UploadFile, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqladmin import Admin

from memory.common import extract, paths, settings
from memory.common.db.connection import get_engine
from memory.api.admin import setup_admin
from memory.api.auth import (
    AuthenticationMiddleware,
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
from memory.api.cloud_claude import router as cloud_claude_router
from memory.api.secrets import router as secrets_router
from memory.api.users import router as users_router
from memory.api.MCP.base import mcp

logger = logging.getLogger(__name__)

# Rate limiter setup
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.API_RATE_LIMIT_DEFAULT] if settings.API_RATE_LIMIT_ENABLED else [],
    enabled=settings.API_RATE_LIMIT_ENABLED,
)

# Create the MCP http app to get its lifespan
mcp_http_app = mcp.http_app(stateless_http=True)

app = FastAPI(title="Knowledge Base API", lifespan=mcp_http_app.lifespan)
app.state.limiter = limiter

# Rate limit exception handler
@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "detail": str(exc.detail),
            "retry_after": exc.retry_after,
        },
        headers={"Retry-After": str(exc.retry_after)} if exc.retry_after else {},
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


def validate_path_within_directory(base_dir: pathlib.Path, requested_path: str) -> pathlib.Path:
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


# SQLAdmin setup with OAuth protection
engine = get_engine()
admin = Admin(app, engine)

# Setup admin with OAuth protection using existing OAuth provider
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
app.include_router(cloud_claude_router)
app.include_router(secrets_router)
app.include_router(users_router)


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
