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
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqladmin import Admin

from memory.common import extract, settings
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

    Prevents path traversal attacks using ../, symlinks, or similar techniques.

    Args:
        base_dir: The allowed base directory
        requested_path: The user-provided path

    Returns:
        The resolved absolute path if valid

    Raises:
        HTTPException: If the path would escape the base directory
    """
    # Resolve base directory to absolute path
    base_resolved = base_dir.resolve(strict=True)

    # Build the target path and resolve it
    # Use strict=False first to check the path before it exists
    target = base_dir / requested_path

    # Resolve the path (follows symlinks)
    try:
        resolved = target.resolve(strict=True)
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="File not found")

    # Use pathlib's is_relative_to for proper path containment check
    # This is safer than string comparison as it handles edge cases
    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        # Path is not relative to base - access denied
        raise HTTPException(status_code=403, detail="Access denied")

    return resolved


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
async def serve_file(path: str):
    file_path = validate_path_within_directory(settings.FILE_STORAGE_DIR, path)

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None:
        mime_type = "application/octet-stream"

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
