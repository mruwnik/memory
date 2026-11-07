"""GitHub webhook ingest: POST /github/webhook/{account_id}.

GitHub signs each delivery with the account's webhook secret
(``X-Hub-Signature-256``). A delivery only names what changed: each issue or
PR it touches gets a debounced refetch, so the stored item always comes from
the GitHub API, never from the payload. Check and status events name a
commit, and route to the tracked PRs whose head it is. Each signed delivery
is a raw event, deduped by its body: the signature doesn't cover the
X-GitHub-Delivery header, so a replayed body under a new id is still a repeat.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from memory.api.request_body import read_request_body_with_cap
from memory.common.db.connection import get_session
from memory.common.db.models import GithubItem
from memory.common.db.models.events import NO_ITEM_TYPE, NOT_COLLECTED, STORED, record_raw_event
from memory.common.db.models.sources import GithubAccount, GithubRepo
from memory.common.github.refetch import queue_refetch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["github"])

# GitHub caps payloads at 25 MB.
MAX_BODY_BYTES = 25 * 1024 * 1024


def signature_matches(secret: str, body: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.encode())


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


def event_type(event: str, payload: dict) -> str:
    action = payload.get("action")
    return f"{event}.{action}" if isinstance(action, str) else event


def handle_delivery(db: Session, account_id: int, body: bytes, request: Request) -> JSONResponse:
    account = db.get(GithubAccount, account_id)
    secret = account.webhook_secret if account else None
    signature = request.headers.get("x-hub-signature-256", "")
    if account is None or not secret or not signature_matches(secret, body, signature):
        logger.warning("github webhook rejected account=%s", account_id)
        raise HTTPException(status_code=401, detail="Invalid signature")

    delivery = request.headers.get("x-github-delivery", "")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        payload = None
    if not isinstance(payload, dict):
        logger.warning("github webhook unparseable account=%s delivery=%s", account_id, delivery)
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    event = request.headers.get("x-github-event", "")
    repo_github_id = (payload.get("repository") or {}).get("id")
    repo = (
        db.query(GithubRepo)
        .filter(
            GithubRepo.account_id == account.id,
            GithubRepo.github_id == repo_github_id,
            GithubRepo.active.is_(True),
        )
        .first()
        if repo_github_id and account.active
        else None
    )
    changed = event_targets(db, repo, event, payload) if repo else []
    targets = [(n, kind) for n, kind in changed if repo and tracks(repo, kind)]
    if event == "ping":
        status, outcome = "pong", NO_ITEM_TYPE
    elif not account.active:
        status, outcome = "ignored", NOT_COLLECTED
    elif repo is None:
        status, outcome = "untracked", NOT_COLLECTED
    else:
        status = "queued"
        outcome = STORED if targets else NOT_COLLECTED if changed else NO_ITEM_TYPE

    raw_id = record_raw_event(
        db,
        source="github",
        event_type=event_type(event, payload),
        account_id=account.id,
        owner_id=account.user_id,
        delivery_id=hashlib.sha256(body).hexdigest(),
        outcome=outcome,
        container=str(repo_github_id) if repo_github_id else None,
        payload=payload,
    )
    if raw_id is None:
        db.rollback()
        return accepted("duplicate")
    if repo is None:
        db.commit()
        return accepted(status)

    repo.last_event_at = datetime.now(timezone.utc)
    repo.push_missing_since = None
    try:
        # Refetches are debounced per item, so the item events they write don't link this delivery.
        queued = [n for n, kind in targets if queue_refetch(repo.id, n, kind)]
    except Exception:
        # GitHub retries a failed delivery, which must not be a duplicate.
        db.rollback()
        raise
    db.commit()
    logger.info(
        "github webhook account=%s repo=%s event=%s delivery=%s queued=%s",
        account_id, repo.id, event, delivery, queued,
    )
    return accepted(status, numbers=[n for n, _ in targets])
