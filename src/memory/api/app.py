"""
FastAPI application for the knowledge base.
"""

import contextlib
import os
import pathlib
import logging
from typing import Annotated, Optional

from fastapi import (
    FastAPI,
    HTTPException,
    File,
    UploadFile,
    Query,
    Form,
    Depends,
    Request,
)
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqladmin import Admin

from memory.common import extract, settings
from memory.common.db.connection import get_engine
from memory.common.db.models import User
from memory.api.admin import setup_admin
from memory.api.search import search, SearchResult
from memory.api.auth import (
    get_current_user,
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# SQLAdmin setup with OAuth protection
engine = get_engine()
admin = Admin(app, engine)

# Setup admin with OAuth protection using existing OAuth provider
setup_admin(admin)
app.include_router(auth_router)
app.add_middleware(AuthenticationMiddleware)


# Add health check to MCP server instead of main app
@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request):
    """Simple health check endpoint on MCP server"""
    from fastapi.responses import JSONResponse

    return JSONResponse({"status": "healthy", "mcp_oauth": "enabled"})


# Mount MCP server at root - OAuth endpoints need to be at root level
app.mount("/", mcp.streamable_http_app())


async def input_type(item: str | UploadFile) -> list[extract.DataChunk]:
    if not item:
        return []

    if isinstance(item, str):
        return extract.extract_text(item)
    content_type = item.content_type or "application/octet-stream"
    return extract.extract_data_chunks(content_type, await item.read())


@app.post("/search", response_model=list[SearchResult])
async def search_endpoint(
    query: Optional[str] = Form(None),
    previews: Optional[bool] = Form(False),
    modalities: Annotated[list[str], Query()] = [],
    files: list[UploadFile] = File([]),
    limit: int = Query(10, ge=1, le=100),
    min_text_score: float = Query(0.3, ge=0.0, le=1.0),
    min_multimodal_score: float = Query(0.3, ge=0.0, le=1.0),
    current_user: User = Depends(get_current_user),
):
    """Search endpoint - delegates to search module"""
    upload_data = [
        chunk for item in [query, *files] for chunk in await input_type(item)
    ]
    logger.error(
        f"Querying chunks for {modalities}, query: {query}, previews: {previews}, upload_data: {upload_data}"
    )
    return await search(
        upload_data,
        previews=previews,
        modalities=set(modalities),
        limit=limit,
        min_text_score=min_text_score,
        min_multimodal_score=min_multimodal_score,
    )


@app.get("/files/{path:path}")
def get_file_by_path(path: str, current_user: User = Depends(get_current_user)):
    """
    Fetch a file by its path

    Parameters:
    - path: Path of the file to fetch (relative to FILE_STORAGE_DIR)

    Returns:
    - The file as a download
    """
    # Sanitize the path to prevent directory traversal
    sanitized_path = path.lstrip("/")
    if ".." in sanitized_path:
        raise HTTPException(status_code=400, detail="Invalid path")

    file_path = pathlib.Path(settings.FILE_STORAGE_DIR) / sanitized_path

    # Check if the file exists on disk
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found at path: {path}")

    return FileResponse(path=file_path, filename=file_path.name)


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
