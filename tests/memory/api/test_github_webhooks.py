"""Tests for POST /github/webhook/{account_id}."""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone

import pytest

from memory.common.celery_app import REFETCH_GITHUB_ITEM
from memory.common.db.models import GithubItem
from memory.common.db.models.sources import GithubAccount, GithubRepo
from memory.common.github.refetch import REFETCH_DELAY_SECONDS, refetch_started
from tests.conftest import unique_sha256

SECRET = "gh-webhook-secret"
REPO_GITHUB_ID = 555


@pytest.fixture
def account(db_session, user):
    account = GithubAccount(user_id=user.id, name="acct", auth_type="pat", access_token="ghp_x", active=True)
    account.webhook_secret = SECRET
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def repo(db_session, account):
    repo = GithubRepo(
        account_id=account.id,
        github_id=REPO_GITHUB_ID,
        owner="testorg",
        name="testrepo",
        track_issues=True,
        track_prs=True,
        active=True,
    )
    db_session.add(repo)
    db_session.commit()
    return repo


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def deliver(client, account_id, event, payload, *, delivery=None, signature=None):
    body = json.dumps(payload).encode()
    headers = {
        "content-type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
    }
    signature = sign(body) if signature is None else signature
    if signature:
        headers["X-Hub-Signature-256"] = signature
    return client.post(f"/github/webhook/{account_id}", content=body, headers=headers)


def refetches(no_celery_dispatch) -> list[list]:
    return [
        c.kwargs["args"]
        for c in no_celery_dispatch.call_args_list
        if c.args and c.args[0] == REFETCH_GITHUB_ITEM
    ]


def pr_event(action="synchronize", number=7, repo_id=REPO_GITHUB_ID) -> dict:
    return {"action": action, "number": number, "pull_request": {"number": number}, "repository": {"id": repo_id}}


def test_signed_synchronize_queues_one_debounced_refetch(client, db_session, repo, no_celery_dispatch):
    first = deliver(client, repo.account_id, "pull_request", pr_event())
    second = deliver(client, repo.account_id, "pull_request", pr_event("edited"))

    assert (first.status_code, second.status_code) == (202, 202)
    assert refetches(no_celery_dispatch) == [[repo.id, 7, "pr"]]
    (call,) = [c for c in no_celery_dispatch.call_args_list if c.args[0] == REFETCH_GITHUB_ITEM]
    assert call.kwargs["countdown"] == REFETCH_DELAY_SECONDS
    db_session.refresh(repo)
    assert repo.last_event_at is not None


@pytest.mark.parametrize("signature", ["", "sha256=" + "0" * 64, sign(b"other body"), "deadbeef"])
def test_bad_signature_is_401(client, db_session, repo, no_celery_dispatch, signature, caplog):
    with caplog.at_level(logging.INFO):
        response = deliver(client, repo.account_id, "pull_request", pr_event(), signature=signature)

    assert response.status_code == 401
    assert refetches(no_celery_dispatch) == []
    assert "synchronize" not in caplog.text
    db_session.refresh(repo)
    assert repo.last_event_at is None


def test_account_without_secret_or_unknown_account_is_401(client, db_session, repo, account):
    account.webhook_secret = None
    db_session.commit()

    no_secret = deliver(client, account.id, "pull_request", pr_event())
    unknown = deliver(client, account.id + 1000, "pull_request", pr_event())

    assert (no_secret.status_code, unknown.status_code) == (401, 401)
    assert no_secret.json() == unknown.json()


def test_redelivered_delivery_is_a_no_op(client, repo, no_celery_dispatch):
    deliver(client, repo.account_id, "pull_request", pr_event(), delivery="d-1")
    refetch_started(repo.id, 7)

    response = deliver(client, repo.account_id, "pull_request", pr_event(), delivery="d-1")

    assert response.status_code == 202
    assert response.json()["status"] == "duplicate"
    assert refetches(no_celery_dispatch) == [[repo.id, 7, "pr"]]


def test_a_delivery_that_could_not_be_queued_is_taken_on_redelivery(client, repo, no_celery_dispatch):
    no_celery_dispatch.side_effect = [ConnectionError("broker down"), None]

    failed = deliver(client, repo.account_id, "pull_request", pr_event(), delivery="d-1")
    retried = deliver(client, repo.account_id, "pull_request", pr_event(), delivery="d-1")

    assert (failed.status_code, retried.status_code) == (500, 202)
    assert retried.json()["status"] == "queued"
    assert refetches(no_celery_dispatch) == [[repo.id, 7, "pr"]] * 2


def test_ping_answers_without_work(client, repo, no_celery_dispatch):
    response = deliver(client, repo.account_id, "ping", {"zen": "Keep it simple.", "hook_id": 1})

    assert response.status_code == 202
    assert response.json()["status"] == "pong"
    assert refetches(no_celery_dispatch) == []


def test_untracked_repo_is_a_no_op(client, db_session, repo, no_celery_dispatch):
    response = deliver(client, repo.account_id, "pull_request", pr_event(repo_id=999))

    assert response.status_code == 202
    assert response.json()["status"] == "untracked"
    assert refetches(no_celery_dispatch) == []
    db_session.refresh(repo)
    assert repo.last_event_at is None


def store_pr(db_session, repo, number, head_sha):
    item = GithubItem(
        modality="github",
        sha256=unique_sha256(f"pr-{number}"),
        content="x",
        kind="pr",
        repo_path=repo.repo_path,
        repo_id=repo.id,
        number=number,
        head_sha=head_sha,
    )
    db_session.add(item)
    db_session.commit()


@pytest.mark.parametrize(
    "event, payload",
    [
        ("check_run", {"action": "completed", "check_run": {"head_sha": "f00", "pull_requests": []}}),
        ("check_suite", {"action": "completed", "check_suite": {"head_sha": "f00", "pull_requests": []}}),
        ("status", {"sha": "f00", "state": "failure"}),
    ],
)
def test_check_events_route_to_the_pr_with_that_head(client, db_session, repo, no_celery_dispatch, event, payload):
    store_pr(db_session, repo, 9, "f00")
    store_pr(db_session, repo, 10, "other")

    response = deliver(client, repo.account_id, event, {**payload, "repository": {"id": REPO_GITHUB_ID}})

    assert response.status_code == 202
    assert refetches(no_celery_dispatch) == [[repo.id, 9, "pr"]]


def test_check_run_routes_by_its_listed_pull_requests(client, repo, no_celery_dispatch):
    payload = {
        "action": "completed",
        "check_run": {"head_sha": "new", "pull_requests": [{"number": 12}]},
        "repository": {"id": REPO_GITHUB_ID},
    }

    deliver(client, repo.account_id, "check_run", payload)

    assert refetches(no_celery_dispatch) == [[repo.id, 12, "pr"]]


@pytest.mark.parametrize(
    "event, payload, expected",
    [
        *[
            ("pull_request", pr_event(action, number=3), [3, "pr"])
            for action in ("labeled", "assigned", "converted_to_draft", "milestoned", "review_requested")
        ],
        ("pull_request_review", {"action": "submitted", "pull_request": {"number": 3}}, [3, "pr"]),
        ("pull_request_review_comment", {"action": "created", "pull_request": {"number": 3}}, [3, "pr"]),
        ("issue_comment", {"action": "created", "issue": {"number": 4, "pull_request": {}}}, [4, "pr"]),
        ("issue_comment", {"action": "created", "issue": {"number": 5}}, [5, "issue"]),
        ("issues", {"action": "opened", "issue": {"number": 6}}, [6, "issue"]),
    ],
)
def test_events_queue_a_refetch_of_their_item(client, repo, no_celery_dispatch, event, payload, expected):
    deliver(client, repo.account_id, event, {**payload, "repository": {"id": REPO_GITHUB_ID}})

    assert refetches(no_celery_dispatch) == [[repo.id, *expected]]


@pytest.mark.parametrize(
    "event, payload",
    [
        ("check_run", {"action": "created", "check_run": {"head_sha": "f00", "pull_requests": [{"number": 1}]}}),
        ("push", {"ref": "refs/heads/main"}),
    ],
)
def test_other_events_queue_nothing_but_count_as_events(client, db_session, repo, no_celery_dispatch, event, payload):
    repo.push_missing_since = repo.push_last_miss_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db_session.commit()

    response = deliver(client, repo.account_id, event, {**payload, "repository": {"id": REPO_GITHUB_ID}})

    assert response.status_code == 202
    assert refetches(no_celery_dispatch) == []
    db_session.refresh(repo)
    assert repo.last_event_at is not None
    assert repo.push_missing_since is None  # push delivers again: the outage is over


def test_untracked_kind_queues_nothing(client, db_session, repo, no_celery_dispatch):
    repo.track_issues = False
    db_session.commit()

    deliver(client, repo.account_id, "issues", {"action": "opened", "issue": {"number": 6}, "repository": {"id": REPO_GITHUB_ID}})

    assert refetches(no_celery_dispatch) == []


def test_webhook_secret_is_generated_write_only(client, db_session, account):
    response = client.post(f"/github/accounts/{account.id}/webhook-secret")

    assert response.status_code == 200
    data = response.json()
    assert data["webhook_url"].endswith(f"/github/webhook/{account.id}")
    assert data["webhook_secret_hint"] == data["secret"][-4:]
    db_session.refresh(account)
    assert account.webhook_secret == data["secret"]
    listed = client.get(f"/github/accounts/{account.id}").json()
    assert listed["webhook_secret_hint"] == data["secret"][-4:]
    assert data["secret"] not in json.dumps(listed)

    cleared = client.delete(f"/github/accounts/{account.id}/webhook-secret")

    assert cleared.json()["webhook_secret_hint"] is None
    db_session.refresh(account)
    assert account.webhook_secret is None
