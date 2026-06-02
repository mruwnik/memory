"""Best-effort callback delivery for completed check jobs.

Delivery is fire-and-forget: a few quick retries, then we give up and rely on
the caller polling ``GET /check/{id}`` (results live for CHECK_JOB_TTL_SEC).
Callback URLs are SSRF-guarded — the server must not be coerced into POSTing to
internal/metadata endpoints.
"""

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx

from memory.common.check.schemas import CallbackPayload, JobRecord
from memory.common import settings

logger = logging.getLogger(__name__)

_BACKOFF_SECONDS = [0, 5, 30]  # attempt 1 immediate, then 5s, 30s


async def _resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


async def is_safe_callback_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    # NOTE (v2): we validate the resolved IP, but httpx re-resolves the hostname
    # at connect time, so an active DNS-rebinding attacker can still bypass this.
    # Full fix = pin the validated IP via a custom transport while preserving TLS
    # SNI. Accepted for now: callbacks are check-scope-gated and private ranges
    # are denied by default.
    try:
        addrs = await _resolve(parsed.hostname)
    except (socket.gaierror, OSError):
        return False
    if not addrs:
        return False
    if settings.CHECK_ALLOW_PRIVATE_CALLBACKS:
        return True  # dev escape hatch: permit private/loopback targets (scheme + resolvability still enforced)
    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


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
    if not url:
        return False
    if not await is_safe_callback_url(url):
        logger.warning("check: refusing unsafe callback_url for %s", job.get("job_id"))
        return False

    payload = build_callback_payload(job)
    max_attempts = max(1, settings.CHECK_CALLBACK_MAX_ATTEMPTS)
    async with httpx.AsyncClient(timeout=settings.CHECK_CALLBACK_TIMEOUT_SEC) as client:
        for attempt in range(max_attempts):
            backoff = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
            if backoff:
                await asyncio.sleep(backoff)
            try:
                resp = await client.post(url, json=payload.model_dump())
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
