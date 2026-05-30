"""Read a raw request body under a hard size cap.

Shared by the cloud-claude transfer-push endpoint and the add_content
/ingest/upload endpoint: both stream an unframed body and must bound peak
allocation, differing only in the cap and where the bytes ultimately go.
"""

from fastapi import HTTPException
from starlette.requests import Request


def too_large_detail(cap_bytes: int) -> str:
    return f"Upload too large. Maximum size is {cap_bytes // (1024 * 1024)} MB"


async def read_request_body_with_cap(request: Request, cap_bytes: int) -> bytes:
    """Buffer the full request body, refusing to exceed ``cap_bytes``.

    Cheap pre-check on the declared Content-Length rejects an honestly-too-large
    client before any bytes are read; the running total then catches chunked
    uploads and Content-Length-lying clients. Peak memory is bounded by
    ``cap_bytes`` (plus a transient join), not by the chunk size.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap_bytes:
        raise HTTPException(status_code=413, detail=too_large_detail(cap_bytes))

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap_bytes:
            raise HTTPException(status_code=413, detail=too_large_detail(cap_bytes))
        chunks.append(chunk)
    return b"".join(chunks)
