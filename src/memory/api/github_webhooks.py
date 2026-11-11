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
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from memory.api.request_body import read_request_body_with_cap
from memory.common import settings
from memory.common.db.connection import get_session
from memory.common.db.models import GithubItem
from memory.common.db.models.events import NO_ITEM_TYPE, NOT_COLLECTED, STORED, record_raw_event
from memory.common.db.models.sources import GithubAccount, GithubRepo
from memory.common.github.refetch import queue_refetch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["github"])

SIGNATURE_FORMAT = re.compile(r"sha256=[0-9a-f]{64}")


def signature_matches(secret: str, body: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.encode())


def accepted(status: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"status": status, **extra}, status_code=202)


class Numbered(BaseModel):
    number: int


class Issue(Numbered):
    # Present on the issue side of a PR.
    pull_request: dict | None = None


class ListedPR(BaseModel):
    number: int | None = None


class Check(BaseModel):
    head_sha: str | None = None
    pull_requests: list[ListedPR] | None = None


class Repository(BaseModel):
    id: int


class Payload(BaseModel):
    """The parts of a delivery the endpoint reads; a signed body shaped otherwise is a 400."""

    action: Any = None
    repository: Repository | None = None
    pull_request: Numbered | None = None
    issue: Issue | None = None
    check_run: Check | None = None
    check_suite: Check | None = None
    sha: str | None = None


def numbers_at_head(db: Session, repo: GithubRepo, sha: str | None, listed: list[ListedPR]) -> list[int]:
    """The PRs a commit's checks belong to: those the event lists (same-repo PRs)
    and the stored PRs whose head it is (which covers forks)."""
    if not sha:
        return []
    stored = db.query(GithubItem.number).filter(
        GithubItem.repo_id == repo.id, GithubItem.kind == "pr", GithubItem.head_sha == sha
    )
    numbers = {pr.number for pr in listed if pr.number} | {n for (n,) in stored}
    return sorted(numbers)


def event_targets(db: Session, repo: GithubRepo, event: str, payload: Payload) -> list[tuple[int, str]]:
    """The (number, kind) of each issue or PR the event changed."""
    pr, issue = payload.pull_request, payload.issue
    if event in ("pull_request", "pull_request_review", "pull_request_review_comment") and pr:
        return [(pr.number, "pr")]
    # A deleted or transferred issue is no longer in the repo to fetch.
    if event == "issues" and payload.action in ("deleted", "transferred"):
        return []
    if event in ("issues", "issue_comment") and issue:
        return [(issue.number, "pr" if issue.pull_request is not None else "issue")]
    check = {"check_run": payload.check_run, "check_suite": payload.check_suite}.get(event)
    if check and payload.action == "completed":
        return [(n, "pr") for n in numbers_at_head(db, repo, check.head_sha, check.pull_requests or [])]
    if event == "status":
        return [(n, "pr") for n in numbers_at_head(db, repo, payload.sha, [])]
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
    # Refuse what can't be a delivery before reading the body.
    signature = request.headers.get("x-hub-signature-256", "")
    has_secret = await run_in_threadpool(account_has_secret, db, account_id)
    if not has_secret or not SIGNATURE_FORMAT.fullmatch(signature):
        logger.warning("github webhook rejected account=%s", account_id)
        raise HTTPException(status_code=401, detail="Invalid signature")
    body = await read_request_body_with_cap(request, settings.GITHUB_WEBHOOK_MAX_BODY_BYTES)
    # The DB work and the broker publish are blocking.
    return await run_in_threadpool(handle_delivery, db, account_id, body, request)


def account_has_secret(db: Session, account_id: int) -> bool:
    account = db.get(GithubAccount, account_id)
    return bool(account and account.webhook_secret)


def event_type(event: str, payload: Payload) -> str:
    action = payload.action
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
        payload: dict = json.loads(body)
        shape = Payload.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValidationError):
        logger.warning("github webhook unparseable account=%s delivery=%s", account_id, delivery)
        raise HTTPException(status_code=400, detail="Body must be a JSON object shaped as GitHub sends it")

    event = request.headers.get("x-github-event", "")
    repo_github_id = shape.repository.id if shape.repository else None
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
    changed = event_targets(db, repo, event, shape) if repo else []
    targets = [(n, kind) for n, kind in changed if repo and tracks(repo, kind)]
    if event == "ping":
        # A ping for a repo that isn't collected is as untracked as any other event.
        status, outcome = "pong", NO_ITEM_TYPE if repo is not None else NOT_COLLECTED
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
        event_type=event_type(event, shape),
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
