"""MCP subserver: add arbitrary content (file/page/image) by URL or upload."""

import base64
import binascii
import logging

from fastmcp import FastMCP

from memory.api import ingest_tokens
from memory.api.MCP.access import get_mcp_current_user, require_project_membership
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.api.ingest import land_and_dispatch
from memory.common import ingest_routing, settings
from memory.common.db.connection import make_session
from memory.common.downloads import stream_download_to_bytes
from memory.common.scopes import SCOPE_INGEST
from memory.common.ssrf import validate_public_url

logger = logging.getLogger(__name__)

ingest_mcp = FastMCP("memory-ingest")


def upload_url_response(intent: ingest_tokens.IngestTokenPayload) -> dict:
    token = ingest_tokens.mint_token(intent)
    return {
        "status": "awaiting_upload",
        "upload_url": f"{settings.SERVER_URL}/ingest/upload?token={token}",
        "expires_in": settings.INGEST_TOKEN_TTL_SECONDS,
    }


@ingest_mcp.tool()
@visible_when(require_scopes(SCOPE_INGEST))
async def add_content(
    type: str,
    name: str,
    url: str | None = None,
    data: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, str | int | float] | None = None,
    project_id: int | None = None,
) -> dict:
    """Add a file, web page, or image to the knowledge base.

    `type` is the expected MIME type of the bytes (e.g. "application/pdf",
    "image/png", "application/epub+zip"). The destination is chosen by
    inspecting the actual bytes, not by trusting `type`: ebooks (epub/mobi/fb2)
    become books, images become photos, and everything else (PDF, office docs,
    text, ...) a generic document. Generic documents whose content has no text
    extractor are stored but produce no embeddings, so they won't appear in
    semantic search (they remain fetchable by listing).

    Provide the content one of three ways:
      - `url`: the server fetches it. The fetched Content-Type must match
        `type` or the call fails.
      - `data`: base64-encoded bytes, inlined (only up to a size limit).
      - neither, or data over the limit: you receive a short-lived
        `upload_url` to PUT the bytes to.

    Args:
        type: Expected MIME type of the content.
        name: File/display name.
        url: Optional source URL to fetch.
        data: Optional base64-encoded content.
        tags: Organization tags.
        metadata: Arbitrary string/number key-values. Stored and made
            searchable on generic documents; a "title"/"author" key is used
            for books.
        project_id: Optional project to scope visibility. Requires membership
            in that project. If omitted, content is visible to its creator
            (and admins).
    """
    tags = tags or []
    metadata = metadata or {}
    if "/" not in ingest_routing.normalize_mime(type):
        raise ValueError(f"`type` must be a MIME type (e.g. 'application/pdf'), got {type!r}")

    if url and data:
        raise ValueError("Provide either `url` or `data`, not both.")

    user = get_mcp_current_user()
    user_id = user and user.id

    if project_id is not None:
        # Authorize the write: a non-admin must be a member of the project, or
        # they could plant content into any project they don't belong to.
        require_project_membership(user, project_id)

    intent = ingest_tokens.IngestTokenPayload(
        user_id=user_id,
        type=type,
        filename=name,
        tags=tags,
        doc_metadata=dict(metadata),
        project_id=project_id,
        exp=None,
    )

    if url:
        validate_public_url(url)
        fetched_type, raw = stream_download_to_bytes(
            url, max_bytes=ingest_routing.max_ingest_bytes(), return_content_type=True
        )
        if raw is None:
            # (content_type, None) means the request succeeded but the body was
            # rejected for size; (None, None) means the fetch itself failed.
            if fetched_type is not None:
                raise ValueError("URL content exceeds the maximum ingest size.")
            raise ValueError(f"Could not fetch url: {url}")
        norm_fetched = ingest_routing.normalize_mime(fetched_type or "")
        norm_declared = ingest_routing.normalize_mime(type)
        if norm_fetched and norm_fetched != norm_declared:
            raise ValueError(
                f"URL content-type {norm_fetched!r} does not match declared type "
                f"{norm_declared!r}"
            )
        with make_session() as db:
            return land_and_dispatch(db, content=raw, intent=intent).model_dump()

    if data:
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("`data` is not valid base64.")
        if len(raw) > settings.INGEST_INLINE_MAX_BYTES:
            return upload_url_response(intent)
        with make_session() as db:
            return land_and_dispatch(db, content=raw, intent=intent).model_dump()

    return upload_url_response(intent)
