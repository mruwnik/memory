"""
FastAPI application for the knowledge base.
"""

import contextlib
import os
import logging
import mimetypes
import pathlib

from fastapi import FastAPI, UploadFile, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqladmin import Admin

from memory.common import extract, settings
from memory.common.db.connection import get_engine
from memory.api.admin import setup_admin
from memory.api.auth import (
    AuthenticationMiddleware,
    router as auth_router,
)
from memory.api.MCP.base import mcp

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(mcp.session_manager.run())
        yield


app = FastAPI(title="Knowledge Base API", lifespan=lifespan)
app.add_middleware(AuthenticationMiddleware)
# Configure CORS with specific origin to prevent CSRF attacks.
# allow_credentials=True requires specific origins, not wildcards.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.SERVER_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def validate_path_within_directory(base_dir: pathlib.Path, requested_path: str) -> pathlib.Path:
    """Validate that a requested path resolves within the base directory.

    Prevents path traversal attacks using ../ or similar techniques.

    Args:
        base_dir: The allowed base directory
        requested_path: The user-provided path

    Returns:
        The resolved absolute path if valid

    Raises:
        HTTPException: If the path would escape the base directory
    """
    # Resolve to absolute path and ensure it's within base_dir
    resolved = (base_dir / requested_path).resolve()
    base_resolved = base_dir.resolve()

    if not str(resolved).startswith(str(base_resolved) + "/") and resolved != base_resolved:
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


# Add health check to MCP server instead of main app
@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request):
    """Health check endpoint that verifies all dependencies are accessible."""
    from fastapi.responses import JSONResponse
    from sqlalchemy import text

    checks = {"mcp_oauth": "enabled"}
    all_healthy = True

    # Check database connection
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "healthy"
    except Exception as e:
        checks["database"] = f"unhealthy: {str(e)[:100]}"
        all_healthy = False

    # Check Qdrant connection
    try:
        from memory.common.qdrant import get_qdrant_client

        client = get_qdrant_client()
        client.get_collections()
        checks["qdrant"] = "healthy"
    except Exception as e:
        checks["qdrant"] = f"unhealthy: {str(e)[:100]}"
        all_healthy = False

    checks["status"] = "healthy" if all_healthy else "degraded"
    status_code = 200 if all_healthy else 503
    return JSONResponse(checks, status_code=status_code)


# Mount MCP server at root - OAuth endpoints need to be at root level
app.mount("/", mcp.streamable_http_app())


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
