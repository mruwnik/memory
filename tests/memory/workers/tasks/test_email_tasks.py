import base64
import contextlib
import io
import logging
import pathlib
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
from typing import cast
from PIL import Image
from unittest.mock import patch, MagicMock
from memory.common.db.connection import DBSession
from memory.common.db.models import (
    Chunk,
    EmailAccount,
    GoogleAccount,
    JobStatus,
    MailMessage,
    PendingJob,
    Project,
    SourceItem,
    EmailAttachment,
    Event,
)
from memory.common import embedding, settings
from memory.common.jobs import LOST_JOB_ERROR, MAX_TASK_STARTS, WORKER_LOST_ERROR
from memory.common.email_sender import (
    PASSWORD_RESET_HEADER,
    PASSWORD_RESET_SUBJECT,
    _build_mime_message,
)
from memory.parsers.email import parse_email_message
from memory.workers.tasks.email import (
    FAILURE_HOLD_LIMIT,
    FIRST_SYNC_HOLD_LIMIT,
    SYNC_OVERLAP,
    QueueOutcome,
    get_cutoff_date,
    process_email_batch,
    process_message,
    queue_message,
    spool_path,
    store_email_message,
    spool_raw_email,
    sync_account,
    sync_gmail_messages,
    sync_imap_messages,
)
from tests.conftest import item_events
from tests.providers.email_provider import MockEmailProvider
from tests.providers.gmail_provider import FakeGmailService
from sqlalchemy.exc import OperationalError, PendingRollbackError
from memory.common.redis_lock import Lock


# Test email constants
SIMPLE_EMAIL_RAW = """From: alice@example.com
To: bob@example.com
Subject: Test Email 1
Message-ID: <test-101@example.com>
Date: Tue, 14 May 2024 10:00:00 +0000

This is test email 1"""

EMAIL_WITH_ATTACHMENT_RAW = """From: eve@example.com
To: bob@example.com
Subject: Email with Attachment
Message-ID: <test-302@example.com>
Date: Tue, 7 May 2024 10:00:00 +0000
Content-Type: multipart/mixed; boundary="boundary123"

--boundary123
Content-Type: text/plain

This email has an attachment

--boundary123
Content-Type: text/plain; name="test.txt"
Content-Disposition: attachment; filename="test.txt"
Content-Transfer-Encoding: base64

VGhpcyBpcyBhIHRlc3QgYXR0YWNobWVudA==

--boundary123--"""


def run_process_message(account_id, message_id, folder, raw_email, received_at=None):
    """Spool a raw email (as queue_message does) and run the worker task.

    process_message now receives a spool-file path rather than the raw body,
    so tests must put the email on disk first and pass its path.
    """
    parsed = parse_email_message(raw_email)
    spool_name = spool_raw_email(account_id, message_id, raw_email, parsed["hash"].hex())
    return process_message(
        account_id=account_id,
        message_id=message_id,
        folder=folder,
        spool_name=spool_name,
        received_at=received_at,
    )


@pytest.fixture(autouse=True)
def mock_voyage_embed_text():
    with patch.object(embedding, "embed_text", return_value=[[0.1] * 1024]):
        yield


@pytest.fixture
def test_email_account(db_session, test_user):
    """Create a test email account for integration testing."""
    account = EmailAccount(
        name="Test Account",
        email_address="bob@example.com",
        imap_server="imap.example.com",
        imap_port=993,
        username="bob@example.com",
        password="password123",
        use_ssl=True,
        folders=["INBOX", "Sent", "Archive"],
        tags=["test", "integration"],
        active=True,
        user_id=test_user.id,
    )
    db_session.add(account)
    db_session.commit()
    return account


def test_process_simple_email(db_session, test_email_account, qdrant):
    """Test processing a simple email message."""
    res = run_process_message(
        account_id=test_email_account.id,
        message_id="101",
        folder="INBOX",
        raw_email=SIMPLE_EMAIL_RAW,
    )

    mail_message_id = res["mail_message_id"]
    assert res == {
        "status": "processed",
        "mail_message_id": mail_message_id,
        "message_id": "101",
        "chunks_count": 1,
        "attachments_count": 0,
    }
    mail_message = (
        db_session.query(MailMessage).filter(MailMessage.id == mail_message_id).one()
    )
    assert mail_message is not None
    assert mail_message.modality == "mail"
    assert mail_message.tags == test_email_account.tags
    assert mail_message.mime_type == "message/rfc822"
    assert mail_message.embed_status == "STORED"
    assert mail_message.subject == "Test Email 1"
    assert mail_message.sender == "alice@example.com"
    assert "bob@example.com" in mail_message.recipients
    assert "This is test email 1" in mail_message.content
    assert mail_message.folder == "INBOX"


@pytest.mark.parametrize(
    "subject, body",
    [
        ("Test Email 1", "This is\x00 test email 1"),
        ("Test\x00 Email 1", "This is test email 1"),
    ],
)
def test_process_email_with_nul_characters(
    db_session, test_email_account, qdrant, subject, body
):
    raw_email = SIMPLE_EMAIL_RAW.replace("Test Email 1", subject).replace(
        "This is test email 1", body
    )

    res = run_process_message(
        account_id=test_email_account.id,
        message_id="101",
        folder="INBOX",
        raw_email=raw_email,
    )

    mail_message = db_session.get(MailMessage, res["mail_message_id"])
    assert mail_message.subject == "Test Email 1"
    assert "This is test email 1" in mail_message.content
    assert all("\x00" not in chunk.content for chunk in mail_message.chunks)


def test_process_email_with_attachment(db_session, test_email_account, qdrant):
    """Test processing a message with an attachment."""
    mail_message_id = run_process_message(
        account_id=test_email_account.id,
        message_id="302",
        folder="Archive",
        raw_email=EMAIL_WITH_ATTACHMENT_RAW,
    )["mail_message_id"]
    # Check mail message specifics
    mail_message = (
        db_session.query(MailMessage).filter(MailMessage.id == mail_message_id).one()
    )
    assert mail_message is not None
    assert mail_message.subject == "Email with Attachment"
    assert mail_message.sender == "eve@example.com"
    assert "This email has an attachment" in mail_message.content
    assert mail_message.folder == "Archive"

    # Check attachments were processed and stored in the EmailAttachment table
    attachments = (
        db_session.query(
            EmailAttachment.filename,
            EmailAttachment.content,
            EmailAttachment.mime_type,
        )
        .filter(EmailAttachment.mail_message_id == mail_message.id)
        .all()
    )

    assert attachments == [(None, "This is a test attachment", "text/plain")]


def test_stored_email_inherits_account_access_before_embedding(
    db_session, test_email_account, test_user, qdrant
):
    project = Project(title="Inbox", state="open")
    db_session.add(project)
    db_session.flush()
    test_email_account.project_id = project.id
    test_email_account.sensitivity = "internal"
    db_session.commit()

    mail_message_id = run_process_message(
        account_id=test_email_account.id,
        message_id="303",
        folder="INBOX",
        raw_email=EMAIL_WITH_ATTACHMENT_RAW,
    )["mail_message_id"]

    message = db_session.get(MailMessage, mail_message_id)
    items = [message, *message.attachments]
    chunks = [chunk for item in items for chunk in item.chunks]
    payloads = [
        point.payload
        for chunk in chunks
        for point in qdrant.retrieve(chunk.collection_name, [str(chunk.id)])
    ]
    assert len(items) == 2
    assert {(i.project_id, i.sensitivity, i.creator_id) for i in items} == {
        (project.id, "internal", test_user.id)
    }
    assert len(payloads) == len(chunks) > 0
    assert {(p["project_id"], p["sensitivity"], p["creator_id"]) for p in payloads} == {
        (project.id, "internal", test_user.id)
    }


def test_process_empty_message(db_session, test_email_account, qdrant):
    """Test processing an empty/invalid message."""
    res = run_process_message(
        account_id=test_email_account.id,
        message_id="999",
        folder="Archive",
        raw_email="",
    )
    assert res == {"reason": "empty_content", "status": "skipped"}


# GitHub re-sends notifications under the same Message-ID with a different body.
REDELIVERED_EMAIL_RAW = SIMPLE_EMAIL_RAW.replace(
    "This is test email 1", "This is test email 1, edited"
)
REDELIVERED_ATTACHMENT_RAW = EMAIL_WITH_ATTACHMENT_RAW.replace(
    "This email has an attachment", "This email has an attachment, edited"
)


@pytest.mark.parametrize(
    "original, uid, raw_email",
    [
        (SIMPLE_EMAIL_RAW, "101", SIMPLE_EMAIL_RAW),
        (SIMPLE_EMAIL_RAW, "102", REDELIVERED_EMAIL_RAW),
        (EMAIL_WITH_ATTACHMENT_RAW, "102", REDELIVERED_ATTACHMENT_RAW),
    ],
)
def test_process_duplicate_message(
    db_session, test_email_account, qdrant, original, uid, raw_email
):
    run_process_message(
        account_id=test_email_account.id,
        message_id="101",
        folder="INBOX",
        raw_email=original,
    )
    source_count_before = db_session.query(SourceItem).count()

    res = run_process_message(
        account_id=test_email_account.id,
        message_id=uid,
        folder="INBOX",
        raw_email=raw_email,
    )

    assert res == {"status": "already_exists", "message_id": uid}
    assert db_session.query(SourceItem).count() == source_count_before


@pytest.mark.parametrize(
    "uid, raw_email",
    [("101", SIMPLE_EMAIL_RAW), ("102", REDELIVERED_EMAIL_RAW)],
)
def test_queue_message_skips_stored_message_id(
    db_session, test_email_account, qdrant, uid, raw_email
):
    run_process_message(
        account_id=test_email_account.id,
        message_id="101",
        folder="INBOX",
        raw_email=SIMPLE_EMAIL_RAW,
    )

    with patch("memory.workers.tasks.email.process_message") as mock_proc:
        queued = queue_message(
            db_session, test_email_account.id, uid, "INBOX", raw_email
        )

    assert queued is QueueOutcome.STORED_HERE
    mock_proc.delay.assert_not_called()


def test_queue_message_tells_apart_a_copy_stored_by_another_account(
    db_session, test_email_account, test_user
):
    other = EmailAccount(
        name="Other", email_address="other@example.com", imap_server="imap.example.com",
        imap_port=993, username="other", password="pw", user_id=test_user.id,
    )
    db_session.add(other)
    db_session.commit()
    with patch("memory.workers.tasks.email.vectorize_email"):
        store_email_message(other.id, "101", "INBOX", SIMPLE_EMAIL_RAW)

    with patch("memory.workers.tasks.email.process_message") as mock_proc:
        outcome = queue_message(
            db_session, test_email_account.id, "101", "INBOX", SIMPLE_EMAIL_RAW
        )

    assert outcome is QueueOutcome.STORED_ELSEWHERE
    mock_proc.delay.assert_not_called()


def test_process_message_stores_account_and_uid(db_session, test_email_account, qdrant):
    """Test that process_message stores account_id and imap_uid."""
    res = run_process_message(
        account_id=test_email_account.id,
        message_id="12345",  # This is the IMAP UID
        folder="INBOX",
        raw_email=SIMPLE_EMAIL_RAW,
    )

    mail_message_id = res["mail_message_id"]
    mail_message = (
        db_session.query(MailMessage).filter(MailMessage.id == mail_message_id).one()
    )

    # Verify the new sync tracking fields are stored
    assert mail_message.email_account_id == test_email_account.id
    assert mail_message.imap_uid == "12345"


def test_stored_mail_is_offered_to_subscriptions(db_session, test_email_account, qdrant):
    res = run_process_message(
        account_id=test_email_account.id,
        message_id="777",
        folder="INBOX",
        raw_email=SIMPLE_EMAIL_RAW,
    )

    assert item_events(db_session) == [(res["mail_message_id"], "added", None)]


def mail_sent(date_header: str) -> str:
    return SIMPLE_EMAIL_RAW.replace("Date: Tue, 14 May 2024 10:00:00 +0000", date_header)


def days_ago(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


@pytest.mark.parametrize(
    "date_header, received_days_ago, backfill",
    [
        (f"Date: {format_datetime(days_ago(0))}", None, False),
        (f"Date: {format_datetime(days_ago(1.9))}", None, False),
        (f"Date: {format_datetime(days_ago(2.1))}", None, True),
        ("Date: Tue, 14 May 2024 10:00:00 +0000", None, True),
        # -0000 parses to a naive datetime
        (f"Date: {format_datetime(days_ago(5).replace(tzinfo=None))}", None, True),
        ("X-No-Date: none", None, False),
        # the receiving server's time beats a sender's Date, which can be forged
        ("Date: Tue, 14 May 2024 10:00:00 +0000", 0, False),
        (f"Date: {format_datetime(days_ago(0))}", 5, True),
    ],
    # The headers hold the time of collection, which xdist workers must agree on.
    ids=["sent-now", "sent-1.9d", "sent-2.1d", "sent-2024", "sent-5d-naive", "no-date", "received-now", "received-5d"],
)
def test_mail_received_long_before_it_is_stored_is_backfill(
    db_session, test_email_account, qdrant, date_header, received_days_ago, backfill
):
    received_at = None if received_days_ago is None else days_ago(received_days_ago).isoformat()
    run_process_message(
        account_id=test_email_account.id,
        message_id="778",
        folder="INBOX",
        raw_email=mail_sent(date_header),
        received_at=received_at,
    )

    assert [e.backfill for e in db_session.query(Event).filter(Event.kind == "item")] == [backfill]


def test_spool_file_removed_after_processing(db_session, test_email_account, qdrant):
    """The spool file is deleted once the message is handled."""
    parsed = parse_email_message(SIMPLE_EMAIL_RAW)
    name = spool_raw_email(
        test_email_account.id, "555", SIMPLE_EMAIL_RAW, parsed["hash"].hex()
    )
    assert spool_path(name).exists()

    process_message(
        account_id=test_email_account.id,
        message_id="555",
        folder="INBOX",
        spool_name=name,
    )

    assert not spool_path(name).exists()


def test_process_message_missing_spool_file_is_noop(db_session, test_email_account):
    """A vanished spool file is a safe skip, not an error or a crash."""
    res = process_message(
        account_id=test_email_account.id,
        message_id="404",
        folder="INBOX",
        spool_name="does-not-exist.eml",
    )
    assert res == {"status": "skipped", "reason": "spool_missing"}


def test_queue_message_spools_body_and_passes_only_filename(
    db_session, test_email_account
):
    """queue_message must keep the raw RFC822 body out of the broker message,
    passing only the bare spool filename."""
    with patch("memory.workers.tasks.email.process_message") as mock_proc:
        queued = queue_message(
            db_session, test_email_account.id, "777", "INBOX", SIMPLE_EMAIL_RAW,
            datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
        )

    assert queued is QueueOutcome.QUEUED
    mock_proc.delay.assert_called_once()
    # Enqueued by keyword so the args land in the PendingJob params (retry).
    call = mock_proc.delay.call_args
    assert call.args == ()
    assert call.kwargs["account_id"] == test_email_account.id
    assert call.kwargs["message_id"] == "777"
    assert call.kwargs["folder"] == "INBOX"
    assert call.kwargs["received_at"] == "2026-01-02T03:04:00+00:00"
    name = call.kwargs["spool_name"]
    # The broker carries a bare filename, never the body or an absolute path.
    assert name.endswith(".eml")
    assert "/" not in name
    assert SIMPLE_EMAIL_RAW not in call.kwargs.values()
    # And the body was actually written there for the worker to read.
    assert spool_path(name).read_text(encoding="utf-8") == SIMPLE_EMAIL_RAW


@pytest.mark.parametrize(
    "previous_jobs, expected",
    [
        ([("777", WORKER_LOST_ERROR, MAX_TASK_STARTS)], QueueOutcome.KILLS_WORKER),
        ([("777", LOST_JOB_ERROR, 1)] * MAX_TASK_STARTS, QueueOutcome.KILLS_WORKER),
        # One lost start is as likely a deploy as a poison message
        ([("777", LOST_JOB_ERROR, 1)], QueueOutcome.QUEUED),
        ([("777", "boom", MAX_TASK_STARTS)], QueueOutcome.QUEUED),
        ([("778", WORKER_LOST_ERROR, MAX_TASK_STARTS)], QueueOutcome.QUEUED),
    ],
)
def test_queue_message_skips_message_that_keeps_killing_its_worker(
    db_session, test_email_account, previous_jobs, expected
):
    db_session.add_all(
        PendingJob(
            job_type="process_message",
            params={"account_id": test_email_account.id, "message_id": message_id},
            status=JobStatus.FAILED.value,
            error_message=error,
            attempts=attempts,
        )
        for message_id, error, attempts in previous_jobs
    )
    db_session.commit()

    with patch("memory.workers.tasks.email.process_message") as mock_proc:
        result = queue_message(
            db_session, test_email_account.id, "777", "INBOX", SIMPLE_EMAIL_RAW
        )

    assert result is expected
    assert mock_proc.delay.called is (expected is QueueOutcome.QUEUED)


RESET_SENDER = "noreply@example.com"


@pytest.mark.parametrize(
    "from_addr, subject, headers, expected",
    [
        (RESET_SENDER, PASSWORD_RESET_SUBJECT, None, QueueOutcome.PASSWORD_RESET),
        (f"Memory <{RESET_SENDER}>", PASSWORD_RESET_SUBJECT, None, QueueOutcome.PASSWORD_RESET),
        ("someone@example.com", "Anything", {PASSWORD_RESET_HEADER: "1"}, QueueOutcome.PASSWORD_RESET),
        (RESET_SENDER, "Weekly digest", None, QueueOutcome.QUEUED),
        ("someone@example.com", PASSWORD_RESET_SUBJECT, None, QueueOutcome.QUEUED),
    ],
)
def test_queue_message_never_ingests_password_reset_mail(
    db_session, test_email_account, monkeypatch, from_addr, subject, headers, expected
):
    monkeypatch.setattr(settings, "PASSWORD_RESET_FROM_EMAIL", RESET_SENDER)
    raw = _build_mime_message(
        from_addr=from_addr,
        to=["victim@example.com"],
        subject=subject,
        body="https://memory.example.com/reset-password#token=secret",
        headers=headers,
    ).as_string()

    with patch("memory.workers.tasks.email.process_message") as mock_proc:
        outcome = queue_message(db_session, test_email_account.id, "888", "Sent", raw)

    assert outcome is expected
    assert mock_proc.delay.called is (expected is QueueOutcome.QUEUED)


def test_spool_file_kept_when_processing_raises(db_session, test_email_account):
    """On an unexpected processing error the spool file must survive so the
    PendingJob's retry (which re-dispatches the same filename) can re-read it.
    Only success and skip outcomes delete it."""
    parsed = parse_email_message(SIMPLE_EMAIL_RAW)
    name = spool_raw_email(
        test_email_account.id, "666", SIMPLE_EMAIL_RAW, parsed["hash"].hex()
    )
    assert spool_path(name).exists()

    with (
        patch(
            "memory.workers.tasks.email.store_email_message",
            side_effect=RuntimeError("boom"),
        ),
        patch("memory.common.content_processing.notify_task_failure"),
        pytest.raises(RuntimeError),
    ):
        process_message(
            account_id=test_email_account.id,
            message_id="666",
            folder="INBOX",
            spool_name=name,
        )

    assert spool_path(name).exists()


def test_failed_email_job_can_be_reprocessed_from_captured_params(
    db_session, test_email_account, qdrant
):
    """End-to-end: a failed process_message keeps its spool file AND captures
    its call args in the job params, so re-dispatching from those params (the
    way retry_failed_job does) re-reads the surviving file and reprocesses.

    Covers the whole retry chain — param capture, keep-on-error, re-read — that
    the per-step tests only exercise in isolation.
    """
    parsed = parse_email_message(SIMPLE_EMAIL_RAW)
    name = spool_raw_email(
        test_email_account.id, "888", SIMPLE_EMAIL_RAW, parsed["hash"].hex()
    )

    # First attempt fails mid-store → job FAILED, spool file kept.
    with (
        patch(
            "memory.workers.tasks.email.store_email_message",
            side_effect=RuntimeError("boom"),
        ),
        patch("memory.common.content_processing.notify_task_failure"),
        pytest.raises(RuntimeError),
    ):
        process_message(
            account_id=test_email_account.id,
            message_id="888",
            folder="INBOX",
            spool_name=name,
        )
    assert spool_path(name).exists()

    # tracked_task captured the call into the FAILED job's params.
    job = (
        db_session.query(PendingJob)
        .filter(PendingJob.status == JobStatus.FAILED.value)
        .order_by(PendingJob.created_at.desc())
        .first()
    )
    assert job is not None
    assert job.params["account_id"] == test_email_account.id
    assert job.params["message_id"] == "888"
    assert job.params["folder"] == "INBOX"
    assert job.params["spool_name"] == name

    # retry_failed_job reconstructs task_kwargs as the non-underscore params;
    # re-dispatching those now succeeds and consumes the surviving spool file.
    task_kwargs = {k: v for k, v in job.params.items() if not k.startswith("_")}
    res = process_message(**task_kwargs)
    assert res["status"] == "processed"
    assert not spool_path(name).exists()
    assert (
        db_session.query(MailMessage)
        .filter(MailMessage.imap_uid == "888")
        .count()
        == 1
    )


# -----------------------------------------------------------------------------
# Gmail sync task tests
# -----------------------------------------------------------------------------


@pytest.fixture
def gmail_email_account(db_session, test_user):
    """Create a test Gmail email account with linked GoogleAccount."""
    google_account = GoogleAccount(
        name="Test Google Account",
        email="test@gmail.com",
        access_token="test_access_token",
        refresh_token="test_refresh_token",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        active=True,
        user_id=test_user.id,
    )
    db_session.add(google_account)
    db_session.flush()

    account = EmailAccount(
        name="Test Gmail Account",
        email_address="test@gmail.com",
        account_type="gmail",
        google_account_id=google_account.id,
        folders=["INBOX"],
        tags=["test", "gmail"],
        active=True,
        user_id=test_user.id,
    )
    db_session.add(account)
    db_session.commit()
    return account


def test_sync_account_routes_to_gmail(db_session, gmail_email_account, qdrant):
    """Test that sync_account routes Gmail accounts to sync_gmail_messages."""
    with patch(
        "memory.workers.tasks.email.get_gmail_service",
        return_value=FakeGmailService({}),
    ) as mock_service:
        result = sync_account(gmail_email_account.id)

    assert result["status"] == "completed"
    assert result["account_type"] == "gmail"
    assert result["account"] == "test@gmail.com"
    mock_service.assert_called_once()


def test_sync_account_routes_to_imap(db_session, test_email_account, qdrant):
    """Test that sync_account routes IMAP accounts to sync_imap_messages."""
    with patch("memory.workers.tasks.email.imap_connection") as mock_conn:
        mock_imap = MagicMock()
        mock_conn.return_value.__enter__.return_value = mock_imap

        with patch("memory.workers.tasks.email.process_folder") as mock_process:
            mock_process.return_value = {
                "messages_found": 0,
                "new_messages": 0,
                "errors": 0,
                "unavailable": False,
            }

            with patch(
                "memory.workers.tasks.email.delete_removed_emails"
            ) as mock_delete:
                mock_delete.return_value = 0

                result = sync_account(test_email_account.id)

    assert result["status"] == "completed"
    assert result["account_type"] == "imap"
    assert result["account"] == "bob@example.com"


def test_sync_account_inactive_account(db_session, gmail_email_account):
    """Test that sync_account rejects inactive accounts."""
    gmail_email_account.active = False
    db_session.commit()

    result = sync_account(gmail_email_account.id)

    assert result["status"] == "error"
    assert "inactive" in result["error"]


def test_sync_account_nonexistent_account(db_session):
    """Test that sync_account handles nonexistent accounts."""
    result = sync_account(99999)

    assert result["status"] == "error"
    assert "not found" in result["error"]


def test_sync_failure_keeps_the_mailbox_out_of_error_log(db_session, test_email_account, caplog):
    """ERROR records reach the appliance operator, so a failed sync logs the account id."""
    error = RuntimeError(f"NO [AUTHENTICATIONFAILED] {test_email_account.email_address}")
    with (
        patch("memory.workers.tasks.email.sync_imap_messages", side_effect=error),
        caplog.at_level(logging.ERROR),
    ):
        result = sync_account(test_email_account.id)

    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert result["status"] == "error"
    assert messages
    assert not [m for m in messages if test_email_account.email_address in m]


def test_sync_gmail_handles_api_error(db_session, gmail_email_account, qdrant):
    """Test that Gmail sync handles API errors gracefully."""
    with patch("memory.workers.tasks.email.get_gmail_service") as mock_service:
        mock_service.side_effect = Exception("Gmail API error")

        result = sync_account(gmail_email_account.id)

    assert result["status"] == "error"
    assert "Gmail API error" in result["error"]

    # Verify sync_error was recorded
    db_session.refresh(gmail_email_account)
    assert gmail_email_account.sync_error == "Gmail API error"


def add_gmail_mail(db_session, account: EmailAccount, gmail_id: str) -> None:
    db_session.add(
        MailMessage(
            sha256=gmail_id.encode().ljust(32, b"\0"),
            tags=["test"],
            size=100,
            mime_type="message/rfc822",
            embed_status="STORED",
            message_id=f"<{gmail_id}@x>",
            subject=gmail_id,
            sender="alice@example.com",
            recipients=["test@gmail.com"],
            content=gmail_id,
            folder="INBOX",
            modality="mail",
            email_account_id=account.id,
            imap_uid=gmail_id,
        )
    )
    db_session.commit()


def run_gmail_sync(db_session, account: EmailAccount, service: FakeGmailService):
    with (
        patch("memory.workers.tasks.email.get_gmail_service", return_value=service),
        patch("memory.workers.tasks.email.process_message") as mock_process,
    ):
        stats = sync_gmail_messages(account, db_session, datetime(1970, 1, 1))
    return stats, mock_process


@pytest.mark.parametrize(
    "folders,mailbox,kept",
    [
        (["INBOX"], {"stored": ["INBOX"]}, True),
        # Archived: no longer under the synced label, still in the mailbox
        (["INBOX"], {"stored": ["CATEGORY_UPDATES"]}, True),
        (["INBOX"], {"stored": []}, True),
        ([], {"stored": []}, True),
        ([], {"stored": ["SENT"]}, True),
        (["SPAM"], {"stored": ["SPAM"]}, True),
        (["INBOX"], {"stored": ["TRASH"]}, False),
        ([], {"stored": ["SPAM"]}, False),
        ([], {}, False),
        (["INBOX"], {}, False),
    ],
)
def test_sync_gmail_deletes_only_mail_gone_from_the_mailbox(
    db_session, gmail_email_account, qdrant, folders, mailbox, kept
):
    gmail_email_account.folders = folders
    add_gmail_mail(db_session, gmail_email_account, "stored")
    service = FakeGmailService(mailbox)

    stats, _ = run_gmail_sync(db_session, gmail_email_account, service)

    remaining = db_session.query(MailMessage).filter_by(imap_uid="stored").count()
    assert remaining == int(kept)
    assert stats["deleted_messages"] == int(not kept)
    assert service.raw_fetches == []


@pytest.mark.parametrize(
    "folders,fetched",
    [
        ([], {"archived", "sent", "new"}),
        (["INBOX"], {"new"}),
    ],
)
def test_sync_gmail_fetches_only_unstored_mail_in_the_synced_labels(
    db_session, gmail_email_account, qdrant, folders, fetched
):
    gmail_email_account.folders = folders
    add_gmail_mail(db_session, gmail_email_account, "stored")
    service = FakeGmailService(
        {
            "stored": ["INBOX"],
            "new": ["INBOX", "UNREAD"],
            "archived": ["CATEGORY_UPDATES"],
            "sent": ["SENT"],
            "trashed": ["TRASH"],
            "spam": ["SPAM"],
        }
    )

    stats, mock_process = run_gmail_sync(db_session, gmail_email_account, service)

    assert set(service.raw_fetches) == fetched
    assert stats["new_messages"] == len(fetched)
    assert {c.kwargs["message_id"] for c in mock_process.delay.call_args_list} == fetched


def test_sync_gmail_remembers_and_skips_declined_mail(
    db_session, gmail_email_account
):
    # A copy of "dup" is already stored under another Gmail id.
    add_gmail_mail(db_session, gmail_email_account, "dup")
    db_session.query(MailMessage).filter_by(imap_uid="dup").update(
        {"imap_uid": "earlier-copy"}
    )
    db_session.commit()
    mailbox = {"earlier-copy": ["INBOX"], "dup": ["INBOX"]}

    first = FakeGmailService(mailbox)
    run_gmail_sync(db_session, gmail_email_account, first)
    second = FakeGmailService(mailbox)
    run_gmail_sync(db_session, gmail_email_account, second)

    assert (first.raw_fetches, second.raw_fetches) == (["dup"], [])
    assert gmail_email_account.gmail_declined_ids == ["dup"]


@pytest.mark.parametrize(
    "mailbox, remembered",
    [
        ({"a": ["INBOX"], "b": ["INBOX"]}, ["a", "b"]),
        # Out of the synced label but still in the mailbox
        ({"a": ["SENT"]}, ["a"]),
        ({"a": ["TRASH"]}, []),
        ({}, []),
    ],
)
def test_sync_gmail_forgets_declined_mail_that_left_the_mailbox(
    db_session, gmail_email_account, mailbox, remembered
):
    gmail_email_account.gmail_declined_ids = ["a", "b"]
    db_session.commit()
    service = FakeGmailService(mailbox)

    run_gmail_sync(db_session, gmail_email_account, service)

    assert service.raw_fetches == []
    assert gmail_email_account.gmail_declined_ids == remembered


def test_sync_gmail_stores_mail_once_another_accounts_copy_is_gone(
    db_session, gmail_email_account, test_user
):
    other = EmailAccount(
        name="Other Gmail", email_address="other@gmail.com", account_type="gmail",
        imap_server="imap.gmail.com", imap_port=993, username="other", password="pw",
        user_id=test_user.id,
    )
    db_session.add(other)
    db_session.commit()
    add_gmail_mail(db_session, other, "dup")
    mailbox = {"dup": ["INBOX"]}

    _, first_queue = run_gmail_sync(db_session, gmail_email_account, FakeGmailService(mailbox))
    remembered = list(gmail_email_account.gmail_declined_ids)
    for copy in db_session.query(MailMessage).filter_by(email_account_id=other.id):
        db_session.delete(copy)
    db_session.commit()
    second = FakeGmailService(mailbox)
    _, second_queue = run_gmail_sync(db_session, gmail_email_account, second)

    assert remembered == []
    assert first_queue.delay.call_count == 0
    assert second.raw_fetches == ["dup"]
    assert [c.kwargs["message_id"] for c in second_queue.delay.call_args_list] == ["dup"]


def test_sync_gmail_does_not_remember_failed_mail(
    db_session, gmail_email_account
):
    service = FakeGmailService({"new": ["INBOX"]})

    with (
        patch("memory.workers.tasks.email.get_gmail_service", return_value=service),
        patch("memory.workers.tasks.email.process_message") as mock_process,
    ):
        mock_process.delay.side_effect = RuntimeError("broker down")
        stats = sync_gmail_messages(gmail_email_account, db_session, datetime(1970, 1, 1))

    assert stats["errors"] == 1
    assert gmail_email_account.gmail_declined_ids == []


def test_sync_account_keeps_declined_mail_when_other_mail_fails(
    db_session, gmail_email_account
):
    # "dup" is a copy of stored mail, so it is declined; "new" fails to queue.
    add_gmail_mail(db_session, gmail_email_account, "dup")
    db_session.query(MailMessage).filter_by(imap_uid="dup").update(
        {"imap_uid": "earlier-copy"}
    )
    db_session.commit()
    service = FakeGmailService(
        {"earlier-copy": ["INBOX"], "dup": ["INBOX"], "new": ["INBOX"]}
    )

    with (
        patch("memory.workers.tasks.email.get_gmail_service", return_value=service),
        patch("memory.workers.tasks.email.process_message") as mock_process,
    ):
        mock_process.delay.side_effect = RuntimeError("broker down")
        sync_account(gmail_email_account.id)

    db_session.refresh(gmail_email_account)
    assert gmail_email_account.gmail_declined_ids == ["dup"]
    assert gmail_email_account.sync_error is not None
    assert gmail_email_account.last_sync_at is None


class PoisonableSession:
    """Minimal fake session that reproduces SQLAlchemy's poisoned-transaction
    behaviour: once a statement fails, every later statement raises
    PendingRollbackError until rollback() is called."""

    def __init__(self):
        self.poisoned = False
        self.rollback_calls = 0

    def rollback(self):
        self.rollback_calls += 1
        self.poisoned = False


@patch("memory.workers.tasks.email.process_message")
@patch("memory.workers.tasks.email.killed_its_worker", return_value=False)
@patch("memory.workers.tasks.email.check_content_exists")
def test_process_email_batch_recovers_after_db_error(
    mock_exists, _killed_its_worker, mock_process_message
):
    """One dropped connection must not poison the whole batch.

    The first message's existence check fails (connection dropped); without
    a rollback the shared session stays poisoned and every subsequent message
    raises PendingRollbackError. The handler must rollback so the rest of the
    batch still gets queued.
    """
    db = PoisonableSession()
    call_count = {"n": 0}

    def fake_check(session, model, **kwargs):
        call_count["n"] += 1
        if session.poisoned:
            raise PendingRollbackError(
                "Can't reconnect until invalid transaction is rolled back."
            )
        if call_count["n"] == 1:
            session.poisoned = True
            raise OperationalError(
                "server closed the connection unexpectedly", None, Exception()
            )
        return False

    mock_exists.side_effect = fake_check

    account = MagicMock()
    account.id = 1
    messages = (
        (uid, SIMPLE_EMAIL_RAW, None) for uid in ("1", "2", "3")
    )

    # PoisonableSession is a structural stand-in for a real session (it only
    # needs rollback()/poisoned for this test); cast so the type checker
    # accepts it for the DBSession parameter.
    stats = process_email_batch(account, cast(DBSession, db), messages)

    # The poisoned session was rolled back, so messages 2 and 3 survived.
    assert db.rollback_calls >= 1
    assert stats["errors"] == 1
    assert stats["new_messages"] == 2
    assert mock_process_message.delay.call_count == 2


def test_finalize_sync_error_rolls_back_before_commit():
    """On the error path, the (possibly poisoned) session must be rolled
    back before committing the sync_error, or the commit cascades into
    PendingRollbackError and the status is lost."""
    from memory.workers.tasks.email import finalize_sync

    order = []
    db = MagicMock()
    db.rollback.side_effect = lambda: order.append("rollback")
    db.commit.side_effect = lambda: order.append("commit")
    account = EmailAccount()

    finalize_sync(account, db, datetime.now(timezone.utc), error=Exception("boom"))

    assert order == ["rollback", "commit"]
    assert account.sync_error == "boom"


def email_with_image(n: int, image_b64: str) -> str:
    return f"""From: eve@example.com
To: bob@example.com
Subject: Logo {n}
Message-ID: <logo-{n}@example.com>
Date: Tue, 7 May 2024 10:00:0{n} +0000
Content-Type: multipart/mixed; boundary="b"

--b
Content-Type: text/plain

Email number {n} with the same signature logo as the others

--b
Content-Type: image/png; name="logo.png"
Content-Disposition: attachment; filename="logo.png"
Content-Transfer-Encoding: base64

{image_b64}
--b--"""


def test_repeated_attachment_leaves_no_unreferenced_chunk_images(
    db_session, test_email_account, qdrant
):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, format="PNG")
    logo = base64.b64encode(buf.getvalue()).decode()

    for n in range(3):
        run_process_message(test_email_account.id, str(900 + n), "INBOX", email_with_image(n, logo))

    referenced = {
        pathlib.Path(p).name
        for (paths,) in db_session.query(Chunk.file_paths).filter(Chunk.file_paths.isnot(None))
        for p in paths
    }
    on_disk = {p.name for p in settings.CHUNK_STORAGE_DIR.iterdir()}
    assert len(on_disk) == 1
    assert on_disk == referenced


# -----------------------------------------------------------------------------
# IMAP sync cutoff
# -----------------------------------------------------------------------------

PREVIOUS_SYNC = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)


def run_imap_sync(account_id, provider, queue_error=None):
    with (
        patch(
            "memory.workers.tasks.email.imap_connection",
            new=lambda account: contextlib.nullcontext(provider),
        ),
        patch("memory.workers.tasks.email.process_message") as task,
    ):
        task.delay.side_effect = queue_error
        return sync_account(account_id)


def test_imap_sync_moves_cutoff_to_its_start(db_session, test_email_account):
    provider = MockEmailProvider({"INBOX": [{"uid": 1}]})
    before = datetime.now(timezone.utc)

    result = run_imap_sync(test_email_account.id, provider)

    db_session.refresh(test_email_account)
    assert result["status"] == "completed"
    assert test_email_account.sync_error is None
    assert before <= test_email_account.last_sync_at <= cast(datetime, provider.searched_at)


@pytest.mark.parametrize(
    "provider_kwargs, queue_error",
    [
        # The server refuses a folder LIST still shows
        ({"unselectable": {"Sent"}}, None),
        ({"unfetchable": {1}}, None),
        ({}, RuntimeError("broker down")),
    ],
)
def test_imap_sync_with_failures_keeps_cutoff(
    db_session, test_email_account, provider_kwargs, queue_error
):
    previous_sync = datetime.now(timezone.utc) - timedelta(days=1)
    test_email_account.last_sync_at = previous_sync
    db_session.commit()
    provider = MockEmailProvider({"INBOX": [{"uid": 1}], "Sent": []}, **provider_kwargs)

    run_imap_sync(test_email_account.id, provider, queue_error)

    db_session.refresh(test_email_account)
    assert test_email_account.last_sync_at == previous_sync
    assert test_email_account.sync_error == (
        "1 messages or folders could not be fetched or queued; "
        "the next sync tries them again"
    )


@pytest.mark.parametrize(
    "last_sync_age, created_age, span",
    [
        (FAILURE_HOLD_LIMIT + timedelta(hours=1), timedelta(days=30), "3 days"),
        # Never synced cleanly: a shorter hold, from when the account was added
        (None, FIRST_SYNC_HOLD_LIMIT + timedelta(minutes=10), "6 hours"),
    ],
)
def test_imap_sync_gives_up_on_failures_held_past_the_limit(
    db_session, test_email_account, last_sync_age, created_age, span
):
    now = datetime.now(timezone.utc)
    test_email_account.last_sync_at = last_sync_age and now - last_sync_age
    test_email_account.created_at = now - created_age
    db_session.commit()
    provider = MockEmailProvider({"INBOX": [{"uid": 1}]}, unfetchable={1})

    run_imap_sync(test_email_account.id, provider)

    db_session.refresh(test_email_account)
    assert now <= test_email_account.last_sync_at <= cast(datetime, provider.searched_at)
    assert test_email_account.sync_error == (
        "1 messages or folders could not be fetched or queued for over "
        f"{span}; the sync moved on without them"
    )


def test_imap_sync_failures_of_a_never_synced_account_hold_cutoff(
    db_session, test_email_account
):
    provider = MockEmailProvider({"INBOX": [{"uid": 1}]}, unfetchable={1})

    run_imap_sync(test_email_account.id, provider)

    db_session.refresh(test_email_account)
    assert test_email_account.last_sync_at is None


def test_imap_sync_moves_cutoff_past_a_folder_the_server_wont_open(
    db_session, test_email_account
):
    test_email_account.folders = ["INBOX", "Gone"]
    test_email_account.last_sync_at = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()
    cutoffs = []

    for _ in range(2):
        provider = MockEmailProvider({"INBOX": [{"uid": 1}]}, unselectable={"Gone"})
        before = datetime.now(timezone.utc)
        run_imap_sync(test_email_account.id, provider)
        db_session.refresh(test_email_account)
        cutoffs.append((before, test_email_account.last_sync_at, provider.searched_at))

    assert all(before <= cutoff <= searched for before, cutoff, searched in cutoffs)
    assert test_email_account.sync_error == (
        "the server won't open folder(s) Gone; check the account's folder list"
    )


@pytest.mark.parametrize(
    "last_sync_at, since_date, expected",
    [
        (None, None, datetime(1970, 1, 1)),
        (PREVIOUS_SYNC, None, PREVIOUS_SYNC - SYNC_OVERLAP),
        (PREVIOUS_SYNC, "2024-04-01T00:00:00", datetime(2024, 4, 1)),
    ],
)
def test_get_cutoff_date(last_sync_at, since_date, expected):
    account = EmailAccount(last_sync_at=last_sync_at)

    assert get_cutoff_date(account, since_date) == expected


class LostLock:
    def extend(self) -> bool:
        return False


def test_imap_sync_aborts_when_lock_is_lost(db_session, test_email_account):
    provider = MockEmailProvider({"INBOX": [{"uid": 1}]})

    with (
        patch(
            "memory.workers.tasks.email.imap_connection",
            new=lambda account: contextlib.nullcontext(provider),
        ),
        patch("memory.workers.tasks.email.process_message") as task,
    ):
        stats = sync_imap_messages(
            test_email_account, db_session, datetime(1970, 1, 1), cast(Lock, LostLock())
        )

    assert stats["aborted"] is True
    task.delay.assert_not_called()


# -----------------------------------------------------------------------------
# Mail without a Message-ID header
# -----------------------------------------------------------------------------

NO_MESSAGE_ID_RAW = """From: alice@example.com
To: bob@example.com
Subject: {subject}
Date: Tue, 14 May 2024 10:00:00 +0000

No Message-ID header here"""


@pytest.mark.parametrize(
    "folder, subject, expected",
    [
        # Another message with the same UID in another folder is new mail
        ("Sent", "A different message", QueueOutcome.QUEUED),
        # The same message fetched again is not
        ("INBOX", "Stored first", QueueOutcome.STORED_HERE),
    ],
)
def test_mail_without_message_id_is_told_apart_by_content(
    db_session, test_email_account, folder, subject, expected
):
    with patch("memory.workers.tasks.email.vectorize_email"):
        store_email_message(
            test_email_account.id, "5", "INBOX", NO_MESSAGE_ID_RAW.format(subject="Stored first")
        )

    with patch("memory.workers.tasks.email.process_message"):
        result = queue_message(
            db_session,
            test_email_account.id,
            "5",
            folder,
            NO_MESSAGE_ID_RAW.format(subject=subject),
        )

    assert result is expected
