"""
FastAPI application for the knowledge base.
"""

import contextlib
import pathlib
import logging
from typing import Annotated, Optional

from fastapi import FastAPI, HTTPException, File, UploadFile, Query, Form
from fastapi.responses import FileResponse
from sqladmin import Admin

from memory.common import settings
from memory.common import extract
from memory.common.db.connection import get_engine
from memory.api.admin import setup_admin
from memory.api.search import search, SearchResult
from memory.api.MCP.tools import mcp

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(mcp.session_manager.run())
        yield


app = FastAPI(title="Knowledge Base API", lifespan=lifespan)

# SQLAdmin setup
engine = get_engine()
admin = Admin(app, engine)
setup_admin(admin)
app.mount("/", mcp.streamable_http_app())


@app.get("/health")
def health_check():
    """Simple health check endpoint"""
    return {"status": "healthy"}


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
        modalities=modalities,
        limit=limit,
        min_text_score=min_text_score,
        min_multimodal_score=min_multimodal_score,
    )


@app.get("/files/{path:path}")
def get_file_by_path(path: str):
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


def main():
    """Run the FastAPI server in debug mode with auto-reloading."""
    import uvicorn

    uvicorn.run(
        "memory.api.app:app", host="0.0.0.0", port=8000, reload=True, log_level="debug"
    )


if __name__ == "__main__":
    from memory.common.qdrant import setup_qdrant

    setup_qdrant()
    main()
