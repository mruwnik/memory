"""Best-effort callback delivery for completed check jobs.

Delivery is fire-and-forget: a few quick retries, then we give up and rely on
the caller polling ``GET /check/{id}`` (results live for CHECK_JOB_TTL_SEC).
Callbacks go through ``safe_http``, which checks every address at dial time, so
the server can't be coerced into POSTing to internal or metadata endpoints.
"""

import asyncio
import logging
from dataclasses import replace
from urllib.parse import urlparse

import httpx

from memory.common import settings
from memory.common.check.schemas import CallbackPayload, JobRecord
from memory.common.safe_http import (
    BlockedDestination,
    NetworkPolicy,
    PinnedBackend,
    default_policy,
    public_http_client,
)

logger = logging.getLogger(__name__)

_BACKOFF_SECONDS = [0, 5, 30]  # attempt 1 immediate, then 5s, 30s


def callback_policy() -> NetworkPolicy:
    """Public addresses only, unless the dev flag also opens private ranges.
    Loopback, link-local and metadata stay refused either way."""
    if settings.CHECK_ALLOW_PRIVATE_CALLBACKS:
        return default_policy()
    return replace(default_policy(), public_only=True)


def callback_backend() -> PinnedBackend:
    return PinnedBackend(policy=callback_policy())


def build_callback_payload(job: JobRecord) -> CallbackPayload:
    return CallbackPayload.from_record(job)


async def deliver_callback(job: JobRecord) -> bool:
    """POST the callback with bounded retries. Returns True on a 2xx.

    Never raises: this runs as a fire-and-forget task, so any unexpected error
    is logged here rather than surfacing as an unretrieved-task exception.
    """
    try:
        return await _deliver_callback(job)
    except Exception:
        logger.exception("check: callback delivery crashed for %s", job.get("job_id"))
        return False


async def _deliver_callback(job: JobRecord) -> bool:
    url = job["callback_url"]
    if urlparse(url).scheme not in ("http", "https"):
        return False

    payload = build_callback_payload(job)
    max_attempts = max(1, settings.CHECK_CALLBACK_MAX_ATTEMPTS)
    async with public_http_client(
        timeout=settings.CHECK_CALLBACK_TIMEOUT_SEC, backend=callback_backend()
    ) as client:
        for attempt in range(max_attempts):
            backoff = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
            if backoff:
                await asyncio.sleep(backoff)
            try:
                resp = await client.post(url, json=payload.model_dump())
            except BlockedDestination:
                logger.warning("check: refusing unsafe callback_url for %s", job.get("job_id"))
                return False
            except httpx.HTTPError as exc:
                logger.info("check callback attempt %d failed: %s", attempt + 1, exc)
                continue
            if 200 <= resp.status_code < 300:
                return True
            if 400 <= resp.status_code < 500:
                logger.info("check callback got %d (4xx); one retry then stop",
                            resp.status_code)
                if attempt >= 1:
                    return False
    return False
