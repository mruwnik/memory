"""OpenTelemetry ingest. Clients point OTEL_EXPORTER_OTLP_ENDPOINT here; the data is read through /usage."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from memory.api.auth import require_rest_access, request_api_key
from memory.common import settings
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.telemetry import (
    parse_otlp_json,
    write_events_to_db,
)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

# Maximum events extracted from a single OTLP payload. Even within the
# byte cap an OTLP "metrics" payload can fan out to thousands of
# datapoints; bound the in-memory list before it's handed off to a
# background task so a burst of valid-but-fat payloads can't pile up
# write_events_to_db invocations on the asyncio loop.
_MAX_TELEMETRY_EVENTS_PER_REQUEST = 10000


class IngestResponse(BaseModel):
    """Response from the ingest endpoint."""

    status: str
    events_received: int
    events_stored: int


@router.post("/ingest", response_model=IngestResponse)
@router.post("/v1/metrics", response_model=IngestResponse)
@router.post("/v1/logs", response_model=IngestResponse)
@router.post("/v1/traces", response_model=IngestResponse)
async def ingest_telemetry(
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_rest_access),
    db: DBSession = Depends(get_session),
) -> IngestResponse:
    """
    Accept OpenTelemetry data.

    Supports OTLP/HTTP JSON format. Configure your telemetry source with:
    ```
    export OTEL_EXPORTER_OTLP_ENDPOINT=https://your-server/telemetry/ingest
    export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer <your-token>"
    ```

    Authentication: Requires Bearer token (same auth as other endpoints).

    Bounded by ``settings.MAX_TELEMETRY_PAYLOAD_BYTES`` (default 5 MiB)
    so a misbehaving collector — or an authenticated attacker — can't
    POST gigabytes of OTLP and OOM the API process while the body is
    buffered and parsed before any rate limit fires.
    """
    max_bytes = settings.MAX_TELEMETRY_PAYLOAD_BYTES
    # Trust the declared Content-Length when present; bail before reading.
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_int = int(declared)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared_int > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Telemetry payload exceeds {max_bytes} bytes; "
                    "split into smaller batches or raise MAX_TELEMETRY_PAYLOAD_BYTES."
                ),
            )

    # Even with a small/missing Content-Length, defend against chunked or
    # lying clients by reading in chunks and bailing if the cumulative
    # size crosses the cap.
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        received += len(chunk)
        if received > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Telemetry payload exceeds {max_bytes} bytes; "
                    "split into smaller batches or raise MAX_TELEMETRY_PAYLOAD_BYTES."
                ),
            )
        chunks.append(chunk)
    body = b"".join(chunks)

    if not body:
        return IngestResponse(status="accepted", events_received=0, events_stored=0)

    # Parse OTLP JSON
    events = parse_otlp_json(body)

    if not events:
        return IngestResponse(status="accepted", events_received=0, events_stored=0)

    # Cap event count per request — a small payload of nested OTLP can
    # still expand to a large datapoint list. Truncate rather than fail
    # so a chatty collector doesn't lose the whole batch.
    if len(events) > _MAX_TELEMETRY_EVENTS_PER_REQUEST:
        events = events[:_MAX_TELEMETRY_EVENTS_PER_REQUEST]

    # Process in background to avoid blocking the response
    api_key = request_api_key(request, db)
    background_tasks.add_task(
        write_events_to_db, events, user.id, api_key.id if api_key else None
    )

    return IngestResponse(
        status="accepted",
        events_received=len(events),
        events_stored=len(events),  # Optimistic - actual count may differ
    )
