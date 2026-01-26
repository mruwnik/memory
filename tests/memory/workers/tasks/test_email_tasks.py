import pytest
from unittest.mock import patch, MagicMock
from memory.common.db.models import (
    EmailAccount,
    GoogleAccount,
    MailMessage,
    SourceItem,
    EmailAttachment,
)
from memory.common import embedding
from memory.workers.tasks.email import process_message, sync_account


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
    res = process_message(
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


def test_process_email_with_attachment(db_session, test_email_account, qdrant):
    """Test processing a message with an attachment."""
    mail_message_id = process_message(
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


def test_process_empty_message(db_session, test_email_account, qdrant):
    """Test processing an empty/invalid message."""
    res = process_message(
        account_id=test_email_account.id,
        message_id="999",
        folder="Archive",
        raw_email="",
    )
    assert res == {"reason": "empty_content", "status": "skipped"}


def test_process_duplicate_message(db_session, test_email_account, qdrant):
    """Test that duplicate messages are detected and not stored again."""
    # First call should succeed and create records
    res = process_message(
        account_id=test_email_account.id,
        message_id="101",
        folder="INBOX",
        raw_email=SIMPLE_EMAIL_RAW,
    )
    source_id_1 = res.get("mail_message_id")

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
    ).get("mail_message_id")

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


def test_process_message_stores_account_and_uid(db_session, test_email_account, qdrant):
    """Test that process_message stores account_id and imap_uid."""
    res = process_message(
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
    mock_service = MagicMock()

    with patch("memory.workers.tasks.email.get_gmail_message_ids") as mock_ids:
        mock_ids.return_value = (set(), mock_service)

        with patch("memory.workers.tasks.email.fetch_gmail_messages_by_ids") as mock_fetch:
            mock_fetch.return_value = iter([])  # Generator

            with patch("memory.workers.tasks.email.find_removed_emails") as mock_find:
                mock_find.return_value = []

                result = sync_account(gmail_email_account.id)

    assert result["status"] == "completed"
    assert result["account_type"] == "gmail"
    assert result["account"] == "test@gmail.com"
    mock_ids.assert_called_once()


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


def test_sync_gmail_processes_messages(db_session, gmail_email_account, qdrant):
    """Test that Gmail sync processes new messages."""
    raw_email = (
        "From: sender@example.com\n"
        "To: test@gmail.com\n"
        "Subject: Test Gmail\n"
        "Message-ID: <gmail-test@example.com>\n"
        "Date: Tue, 14 May 2024 10:00:00 +0000\n\n"
        "Test body"
    )
    mock_service = MagicMock()

    def mock_generator(service, ids):
        for msg_id in ids:
            yield (msg_id, raw_email)

    with patch("memory.workers.tasks.email.get_gmail_message_ids") as mock_ids:
        mock_ids.return_value = ({"gmail_msg_1"}, mock_service)

        with patch("memory.workers.tasks.email.fetch_gmail_messages_by_ids") as mock_fetch:
            mock_fetch.side_effect = mock_generator

            with patch("memory.workers.tasks.email.find_removed_emails") as mock_find:
                mock_find.return_value = []

                with patch(
                    "memory.workers.tasks.email.process_message"
                ) as mock_process:
                    # Mock the delay method
                    mock_process.delay = MagicMock()

                    result = sync_account(gmail_email_account.id)

    assert result["status"] == "completed"
    assert result["account_type"] == "gmail"
    assert result["messages_found"] == 1
    assert result["new_messages"] == 1
    mock_process.delay.assert_called_once()


def test_sync_gmail_handles_api_error(db_session, gmail_email_account, qdrant):
    """Test that Gmail sync handles API errors gracefully."""
    with patch("memory.workers.tasks.email.get_gmail_message_ids") as mock_ids:
        mock_ids.side_effect = Exception("Gmail API error")

        result = sync_account(gmail_email_account.id)

    assert result["status"] == "error"
    assert "Gmail API error" in result["error"]

    # Verify sync_error was recorded
    db_session.refresh(gmail_email_account)
    assert gmail_email_account.sync_error == "Gmail API error"
