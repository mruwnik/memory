"""GitHub webhook ingest: POST /github/webhook/{account_id}.

GitHub signs each delivery with the account's webhook secret
(``X-Hub-Signature-256``). A delivery only names what changed: each issue or
PR it touches gets a debounced refetch, so the stored item always comes from
the GitHub API, never from the payload. Check and status events name a
commit, and route to the tracked PRs whose head it is.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

import redis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from memory.api.request_body import read_request_body_with_cap
from memory.common import settings
from memory.common.db.connection import get_session
from memory.common.db.models import GithubItem
from memory.common.db.models.sources import GithubAccount, GithubRepo
from memory.common.github.refetch import queue_refetch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["github"])

# GitHub caps payloads at 25 MB.
MAX_BODY_BYTES = 25 * 1024 * 1024
# GitHub redelivers on request for up to three days; two cover automatic retries.
DELIVERY_TTL_SECONDS = 48 * 3600


def signature_matches(secret: str, body: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.encode())


def first_delivery(delivery_id: str) -> bool:
    key = f"memory:github:delivery:{delivery_id}"
    return bool(redis.from_url(settings.REDIS_URL).set(key, 1, nx=True, ex=DELIVERY_TTL_SECONDS))


def accepted(status: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"status": status, **extra}, status_code=202)


def numbers_at_head(db: Session, repo: GithubRepo, sha: str | None, listed: list[dict]) -> list[int]:
    """The PRs a commit's checks belong to: those the event lists (same-repo PRs)
    and the stored PRs whose head it is (which covers forks)."""
    if not sha:
        return []
    stored = db.query(GithubItem.number).filter(
        GithubItem.repo_id == repo.id, GithubItem.kind == "pr", GithubItem.head_sha == sha
    )
    numbers = {pr["number"] for pr in listed if pr.get("number")} | {n for (n,) in stored}
    return sorted(numbers)


def event_targets(db: Session, repo: GithubRepo, event: str, payload: dict) -> list[tuple[int, str]]:
    """The (number, kind) of each issue or PR the event changed."""
    action = payload.get("action")
    if event in ("pull_request", "pull_request_review", "pull_request_review_comment"):
        return [(payload["pull_request"]["number"], "pr")]
    if event in ("issues", "issue_comment"):
        issue = payload["issue"]
        return [(issue["number"], "pr" if "pull_request" in issue else "issue")]
    if event in ("check_run", "check_suite") and action == "completed":
        check = payload[event]
        listed = check.get("pull_requests") or []
        return [(n, "pr") for n in numbers_at_head(db, repo, check.get("head_sha"), listed)]
    if event == "status":
        return [(n, "pr") for n in numbers_at_head(db, repo, payload.get("sha"), [])]
    return []


def tracks(repo: GithubRepo, kind: str) -> bool:
    return bool(repo.track_prs if kind == "pr" else repo.track_issues)


@router.post("/webhook/{account_id}")
async def github_webhook(
    account_id: int,
    request: Request,
    db: Session = Depends(get_session),
) -> JSONResponse:
    """Accept one signed GitHub webhook delivery for an account."""
    body = await read_request_body_with_cap(request, MAX_BODY_BYTES)
    # The DB work and the broker publish are blocking.
    return await run_in_threadpool(handle_delivery, db, account_id, body, request)


def handle_delivery(db: Session, account_id: int, body: bytes, request: Request) -> JSONResponse:
    account = db.get(GithubAccount, account_id)
    secret = account.webhook_secret if account else None
    signature = request.headers.get("x-hub-signature-256", "")
    if account is None or not secret or not signature_matches(secret, body, signature):
        logger.info("github webhook rejected account=%s", account_id)
        raise HTTPException(status_code=401, detail="Invalid signature")

    delivery = request.headers.get("x-github-delivery", "")
    if not delivery:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Delivery")
    if not first_delivery(delivery):
        return accepted("duplicate")

    event = request.headers.get("x-github-event", "")
    if event == "ping":
        return accepted("pong")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise HTTPException(status_code=400, detail="Body must be JSON")
    if not isinstance(payload, dict) or not account.active:
        return accepted("ignored")

    repo_github_id = (payload.get("repository") or {}).get("id")
    repo = (
        db.query(GithubRepo)
        .filter(
            GithubRepo.account_id == account.id,
            GithubRepo.github_id == repo_github_id,
            GithubRepo.active.is_(True),
        )
        .first()
        if repo_github_id
        else None
    )
    if repo is None:
        return accepted("untracked")

    repo.last_event_at = datetime.now(timezone.utc)
    db.commit()

    targets = [(n, kind) for n, kind in event_targets(db, repo, event, payload) if tracks(repo, kind)]
    queued = [n for n, kind in targets if queue_refetch(repo.id, n, kind)]
    logger.info(
        "github webhook account=%s repo=%s event=%s delivery=%s queued=%s",
        account_id, repo.id, event, delivery, queued,
    )
    return accepted("queued", numbers=[n for n, _ in targets])
