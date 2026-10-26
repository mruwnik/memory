"""Tests for the push ingest endpoint POST /transcripts/ingest/{account_id}."""

import hashlib
import hmac
import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from memory.api.transcript_ingest import ACCOUNT_RATE_LIMIT, MAX_BODY_BYTES
from memory.common.celery_app import INGEST_TRANSCRIPT_MEETING, PROCESS_MEETING
from memory.common.celery_app import app as celery_app
from memory.common.db.models import PendingJob
from memory.common.db.models.sources import TranscriptAccount
from memory.common.rate_limit import parse_limit

SECRET = "s3cret-for-tests"

GENERIC_BODY = {
    "external_id": "rec-42",
    "title": "Weekly sync",
    "started_at": "2026-10-20T15:00:00Z",
    "duration_minutes": 25,
    "participants": [
        {"name": "Ann", "email": "ann@example.com"},
        {"name": "Bob"},
    ],
    "segments": [
        {"speaker": "Ann", "text": "Hello."},
        {"speaker": "Ann", "text": "Agenda."},
        {"speaker": "Bob", "text": "Hi."},
    ],
}


def make_account(db_session, user_id, *, provider="generic", secret: str | None = SECRET, active=True, tags=None):
    account = TranscriptAccount(
        user_id=user_id,
        name=f"push-{provider}",
        provider=provider,
        active=active,
        tags=tags or [],
    )
    if provider == "fireflies":
        account.api_key = "ff-key"
    account.webhook_secret = secret
    db_session.add(account)
    db_session.commit()
    return account


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post(client, account_id, body: bytes, headers: dict):
    return client.post(
        f"/transcripts/ingest/{account_id}",
        content=body,
        headers={"content-type": "application/json", **headers},
    )


@pytest.fixture
def sent():
    calls: list[dict] = []
    with patch.object(celery_app, "send_task") as send:
        send.side_effect = lambda name, args=None, kwargs=None, task_id=None: (
            calls.append({"name": name, "args": args, "kwargs": kwargs, "task_id": task_id})
            or MagicMock(id=task_id or f"task-{len(calls)}")
        )
        yield calls


@pytest.mark.parametrize(
    "header_fn",
    [
        lambda body: {"X-Hub-Signature": f"sha256={sign(body)}"},
        lambda body: {"X-Hub-Signature": sign(body)},
        lambda body: {"X-Hub-Signature": f"sha256={sign(body).upper()}"},
        lambda body: {"Authorization": f"Bearer {SECRET}"},
    ],
)
def test_generic_body_queues_process_meeting(client, user, db_session, sent, header_fn):
    account = make_account(db_session, user.id, tags=["team"])
    body = json.dumps(GENERIC_BODY).encode()

    response = post(client, account.id, body, header_fn(body))

    assert response.status_code == 202
    assert [c["name"] for c in sent] == [PROCESS_MEETING]
    kwargs = sent[0]["kwargs"]
    assert kwargs["transcript"] == "Ann: Hello. Agenda.\nBob: Hi."
    assert kwargs["title"] == "Weekly sync"
    assert kwargs["meeting_date"] == "2026-10-20T15:00:00+00:00"
    assert kwargs["duration_minutes"] == 25
    assert kwargs["attendee_pairs"] == [["ann@example.com", "Ann"]]
    assert kwargs["attendee_names"] == ["Bob"]
    assert kwargs["external_id"] == f"push:{account.id}:rec-42"
    assert kwargs["transcript_account_id"] == account.id
    assert kwargs["source_tool"] == "generic"
    assert kwargs["tags"] == ["team"]
    job = db_session.get(PendingJob, response.json()["job_id"])
    assert job.user_id == user.id
    assert job.celery_task_id == sent[0]["task_id"]
    db_session.refresh(account)
    assert account.last_sync_at is not None


def test_generic_text_body_passes_text_through(client, user, db_session, sent):
    account = make_account(db_session, user.id)
    body = json.dumps({"external_id": "x", "text": "Raw transcript"}).encode()

    response = post(client, account.id, body, {"X-Hub-Signature": sign(body)})

    assert response.status_code == 202
    assert sent[0]["kwargs"]["transcript"] == "Raw transcript"


def test_repeat_external_id_returns_existing_job(client, user, db_session, sent):
    account = make_account(db_session, user.id)
    body = json.dumps(GENERIC_BODY).encode()
    headers = {"X-Hub-Signature": sign(body)}

    first = post(client, account.id, body, headers)
    account.last_sync_at = None
    db_session.commit()
    second = post(client, account.id, body, headers)

    assert second.status_code == 202
    assert second.json()["job_id"] == first.json()["job_id"]
    assert len(sent) == 1
    db_session.refresh(account)
    assert account.last_sync_at is not None


@pytest.mark.parametrize(
    "payload",
    [
        {"event": "meeting.transcribed", "meeting_id": "FF123"},
        {"eventType": "Transcription completed", "meetingId": "FF123"},
    ],
)
def test_fireflies_notification_queues_one_meeting_fetch(client, user, db_session, sent, payload):
    account = make_account(db_session, user.id, provider="fireflies")
    body = json.dumps(payload).encode()

    response = post(client, account.id, body, {"X-Hub-Signature": f"sha256={sign(body)}"})

    assert response.status_code == 202
    assert sent == [{"name": INGEST_TRANSCRIPT_MEETING, "args": [account.id, "FF123"], "kwargs": None, "task_id": None}]


@pytest.mark.parametrize(
    "payload",
    [
        {"event": "meeting.summarized", "meeting_id": "FF123"},
        {"eventType": "Something else", "meetingId": "FF123"},
    ],
)
def test_fireflies_other_events_are_ignored(client, user, db_session, sent, payload):
    account = make_account(db_session, user.id, provider="fireflies")
    body = json.dumps(payload).encode()

    response = post(client, account.id, body, {"X-Hub-Signature": sign(body)})

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert sent == []


def test_fireflies_notification_rejected_on_generic_source(client, user, db_session, sent):
    account = make_account(db_session, user.id)
    body = json.dumps({"event": "meeting.transcribed", "meeting_id": "FF1"}).encode()

    response = post(client, account.id, body, {"X-Hub-Signature": sign(body)})

    assert response.status_code == 422
    assert sent == []


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Hub-Signature": "sha256=" + "0" * 64},
        {"X-Hub-Signature": "sha256="},
        {"Authorization": "Bearer wrong"},
        {"Authorization": SECRET},
    ],
)
def test_bad_auth_is_401(client, user, db_session, sent, headers):
    account = make_account(db_session, user.id)

    response = post(client, account.id, json.dumps(GENERIC_BODY).encode(), headers)

    assert response.status_code == 401
    assert sent == []


def test_signature_over_different_body_is_401(client, user, db_session, sent):
    account = make_account(db_session, user.id)
    body = json.dumps(GENERIC_BODY).encode()

    response = post(client, account.id, body + b" ", {"X-Hub-Signature": sign(body)})

    assert response.status_code == 401


def test_unknown_account_and_missing_secret_look_identical(client, user, db_session, sent):
    account = make_account(db_session, user.id, secret=None)
    body = json.dumps(GENERIC_BODY).encode()
    headers = {"Authorization": f"Bearer {SECRET}"}

    no_secret = post(client, account.id, body, headers)
    unknown = post(client, account.id + 1000, body, headers)

    assert (no_secret.status_code, no_secret.content) == (unknown.status_code, unknown.content)
    assert no_secret.status_code == 401


def test_inactive_source_is_403(client, user, db_session, sent):
    account = make_account(db_session, user.id, active=False)
    body = json.dumps(GENERIC_BODY).encode()

    response = post(client, account.id, body, {"X-Hub-Signature": sign(body)})

    assert response.status_code == 403
    assert sent == []


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "no external id"},
        {"external_id": "x"},
        {"external_id": "x", "text": "a", "segments": [{"text": "b"}]},
        {"external_id": "x" * 201, "text": "a"},
        {"external_id": "x", "text": "a", "started_at": "not a date"},
        {"external_id": "x", "text": "a" * 500_001},
    ],
)
def test_invalid_generic_body_is_422_without_echoing_input(client, user, db_session, sent, payload):
    account = make_account(db_session, user.id)
    body = json.dumps(payload).encode()

    response = post(client, account.id, body, {"X-Hub-Signature": sign(body)})

    assert response.status_code == 422
    assert all("input" not in error for error in response.json()["detail"])
    assert sent == []


@pytest.mark.parametrize(
    "body", [b"{not json", b"[" * 100_000 + b"]" * 100_000], ids=["malformed", "too_deep"]
)
def test_invalid_json_is_400(client, user, db_session, sent, body):
    account = make_account(db_session, user.id)

    response = post(client, account.id, body, {"X-Hub-Signature": sign(body)})

    assert response.status_code == 400


def test_oversized_body_is_413(client, user, db_session, sent):
    account = make_account(db_session, user.id)
    body = b"x" * (MAX_BODY_BYTES + 1)

    response = post(client, account.id, body, {"X-Hub-Signature": sign(body)})

    assert response.status_code == 413
    assert sent == []


def test_per_account_rate_limit_is_429(client, user, db_session, sent):
    account = make_account(db_session, user.id)
    limit, _ = parse_limit(ACCOUNT_RATE_LIMIT)
    statuses = []
    for i in range(limit + 1):
        body = json.dumps({"external_id": f"r{i}", "text": "t"}).encode()
        statuses.append(post(client, account.id, body, {"X-Hub-Signature": sign(body)}).status_code)

    assert statuses == [202] * limit + [429]


def test_never_logs_body_or_secret(client, user, db_session, sent, caplog):
    account = make_account(db_session, user.id)
    body = json.dumps({"external_id": "x", "text": "very private words"}).encode()

    with caplog.at_level(logging.DEBUG):
        post(client, account.id, body, {"Authorization": f"Bearer {SECRET}"})
        post(client, account.id, body, {"Authorization": "Bearer guess"})

    assert "very private words" not in caplog.text
    assert SECRET not in caplog.text
    assert "guess" not in caplog.text
