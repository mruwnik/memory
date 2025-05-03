from unittest import mock
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch
from memory.common.db.models import (
    EmailAccount,
    MailMessage,
    SourceItem,
    EmailAttachment,
)
from memory.common import embedding
from memory.workers.tasks.email import process_message


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


@pytest.fixture(autouse=True)
def mock_voyage_embed_text():
    with patch.object(embedding, "embed_text", return_value=[[0.1] * 1024]):
        yield


@pytest.fixture
def test_email_account(db_session):
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
    )
    db_session.add(account)
    db_session.commit()
    return account


def test_process_simple_email(db_session, test_email_account, qdrant):
    """Test processing a simple email message."""
    mail_message_id = process_message(
        account_id=test_email_account.id,
        message_id="101",
        folder="INBOX",
        raw_email=SIMPLE_EMAIL_RAW,
    )

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


def test_process_email_with_attachment(db_session, test_email_account, qdrant):
    """Test processing a message with an attachment."""
    mail_message_id = process_message(
        account_id=test_email_account.id,
        message_id="302",
        folder="Archive",
        raw_email=EMAIL_WITH_ATTACHMENT_RAW,
    )
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


def test_process_empty_message(db_session, test_email_account, qdrant):
    """Test processing an empty/invalid message."""
    source_id = process_message(
        account_id=test_email_account.id,
        message_id="999",
        folder="Archive",
        raw_email="",
    )

    assert source_id is None


def test_process_duplicate_message(db_session, test_email_account, qdrant):
    """Test that duplicate messages are detected and not stored again."""
    # First call should succeed and create records
    source_id_1 = process_message(
        account_id=test_email_account.id,
        message_id="101",
        folder="INBOX",
        raw_email=SIMPLE_EMAIL_RAW,
    )

    assert source_id_1 is not None, "First call should return a source_id"

    # Count records to verify state before second call
    source_count_before = db_session.query(SourceItem).count()
    message_count_before = db_session.query(MailMessage).count()

    # Second call with same email should detect duplicate and return None
    source_id_2 = process_message(
        account_id=test_email_account.id,
        message_id="101",
        folder="INBOX",
        raw_email=SIMPLE_EMAIL_RAW,
    )

    assert source_id_2 is None, "Second call should return None for duplicate message"

    # Verify no new records were created
    source_count_after = db_session.query(SourceItem).count()
    message_count_after = db_session.query(MailMessage).count()

    assert source_count_before == source_count_after, (
        "No new SourceItem should be created"
    )
    assert message_count_before == message_count_after, (
        "No new MailMessage should be created"
    )
