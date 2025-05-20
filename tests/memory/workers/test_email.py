import base64
import pathlib
from datetime import datetime
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from memory.common import embedding, settings
from memory.common.db.models import (
    EmailAccount,
    EmailAttachment,
    MailMessage,
)
from memory.common.parsers.email import Attachment
from memory.workers.email import (
    create_mail_message,
    extract_email_uid,
    fetch_email,
    fetch_email_since,
    process_attachment,
    process_attachments,
    process_folder,
    vectorize_email,
)


@pytest.fixture
def mock_uuid4():
    i = 0

    def uuid4():
        nonlocal i
        i += 1
        return f"00000000-0000-0000-0000-00000000000{i}"

    with patch("uuid.uuid4", side_effect=uuid4):
        yield


@pytest.mark.parametrize(
    "attachment_size, max_inline_size, message_id",
    [
        # Small attachment, should be base64 encoded and returned inline
        (100, 1000, "<test@example.com>"),
        # Edge case: exactly at max size, should be base64 encoded
        (100, 100, "<test@example.com>"),
    ],
)
def test_process_attachment_inline(
    attachment_size: int, max_inline_size: int, message_id: str
):
    attachment = {
        "filename": "test.txt",
        "content_type": "text/plain",
        "size": attachment_size,
        "content": b"a" * attachment_size,
    }
    message = MailMessage(
        id=1,
        message_id=message_id,
        sender="sender@example.com",
        folder="INBOX",
    )

    with patch.object(settings, "MAX_INLINE_ATTACHMENT_SIZE", max_inline_size):
        result = process_attachment(cast(Attachment, attachment), message)

    assert result is not None
    assert cast(str, result.content) == attachment["content"].decode(
        "utf-8", errors="replace"
    )
    assert result.filename is None


@pytest.mark.parametrize(
    "attachment_size, max_inline_size, message_id",
    [
        # Large attachment, should be saved to disk
        (1000, 100, "<test@example.com>"),
        # Message ID with special characters that need escaping
        (1000, 100, "<test/with:special\\chars>"),
    ],
)
def test_process_attachment_disk(attachment_size, max_inline_size, message_id):
    attachment = {
        "filename": "test/with:special\\chars.txt",
        "content_type": "text/plain",
        "size": attachment_size,
        "content": b"a" * attachment_size,
    }
    message = MailMessage(
        id=1,
        message_id=message_id,
        sender="sender@example.com",
        folder="INBOX",
    )
    with patch.object(settings, "MAX_INLINE_ATTACHMENT_SIZE", max_inline_size):
        result = process_attachment(cast(Attachment, attachment), message)

    assert result is not None
    assert not cast(str, result.content)
    assert cast(str, result.filename) == str(
        settings.FILE_STORAGE_DIR
        / "sender_example_com"
        / "INBOX"
        / "test_with_special_chars.txt"
    )


def test_process_attachment_write_error():
    # Create test attachment
    attachment = {
        "filename": "test_error.txt",
        "content_type": "text/plain",
        "size": 100,
        "content": b"a" * 100,
    }
    message = MailMessage(
        id=1,
        message_id="<test@example.com>",
        sender="sender@example.com",
        folder="INBOX",
    )

    # Mock write_bytes to raise an exception
    def mock_write_bytes(self, content):
        raise IOError("Test write error")

    with (
        patch.object(settings, "MAX_INLINE_ATTACHMENT_SIZE", 10),
        patch.object(pathlib.Path, "write_bytes", mock_write_bytes),
    ):
        assert process_attachment(cast(Attachment, attachment), message) is None


def test_process_attachments_empty():
    assert process_attachments([], MagicMock()) == []


def test_process_attachments_mixed():
    # Create test attachments
    attachments = [
        # Small attachment - should be kept inline
        {
            "filename": "small.txt",
            "content_type": "text/plain",
            "size": 20,
            "content": b"a" * 20,
        },
        # Large attachment - should be stored on disk
        {
            "filename": "large.txt",
            "content_type": "text/plain",
            "size": 100,
            "content": b"b" * 100,
        },
        # Another small attachment
        {
            "filename": "another_small.txt",
            "content_type": "text/plain",
            "size": 30,
            "content": b"c" * 30,
        },
    ]
    message = MailMessage(
        id=1,
        tags=["test"],
        message_id="<test@example.com>",
        sender="sender@example.com",
        folder="INBOX",
    )

    with patch.object(settings, "MAX_INLINE_ATTACHMENT_SIZE", 50):
        # Process attachments
        results = process_attachments(cast(list[Attachment], attachments), message)

    # Verify we have all attachments processed
    assert len(results) == 3

    assert cast(str, results[0].content) == "a" * 20
    assert cast(str, results[2].content) == "c" * 30

    # Verify large attachment has a path
    assert cast(str, results[1].filename) == str(
        settings.FILE_STORAGE_DIR / "sender_example_com" / "INBOX" / "large.txt"
    )


def test_extract_email_uid_valid():
    msg_data = [(b"1 (UID 12345 RFC822 {1234}", b"raw email content")]
    uid, raw_email = extract_email_uid(msg_data)

    assert uid == "12345"
    assert raw_email == b"raw email content"


def test_extract_email_uid_no_match():
    msg_data = [(b"1 (RFC822 {1234}", b"raw email content")]
    uid, raw_email = extract_email_uid(msg_data)

    assert uid is None
    assert raw_email == b"raw email content"


def test_create_mail_message(db_session):
    raw_email = (
        "From: sender@example.com\n"
        "To: recipient@example.com\n"
        "Subject: Test Subject\n"
        "Date: Sun, 1 Jan 2023 12:00:00 +0000\n"
        "Message-ID: 321\n"
        "MIME-Version: 1.0\n"
        'Content-Type: multipart/mixed; boundary="boundary"\n'
        "\n"
        "--boundary\n"
        "Content-Type: text/plain\n"
        "\n"
        "Test body content\n"
        "--boundary\n"
        'Content-Disposition: attachment; filename="test.txt"\n'
        "Content-Type: text/plain\n"
        "Content-Transfer-Encoding: base64\n"
        "\n"
        "YXR0YWNobWVudCBjb250ZW50\n"
        "--boundary--"
    )
    folder = "INBOX"

    # Call function
    mail_message = create_mail_message(
        db_session=db_session,
        raw_email=raw_email,
        folder=folder,
        tags=["test"],
        message_id="123",
    )
    db_session.commit()

    attachments = (
        db_session.query(EmailAttachment)
        .filter(EmailAttachment.mail_message_id == mail_message.id)
        .all()
    )

    # Verify the mail message was created correctly
    assert isinstance(mail_message, MailMessage)
    assert cast(str, mail_message.message_id) == "321"
    assert cast(str, mail_message.subject) == "Test Subject"
    assert cast(str, mail_message.sender) == "sender@example.com"
    assert cast(list[str], mail_message.recipients) == ["recipient@example.com"]
    assert mail_message.sent_at.isoformat()[:-6] == "2023-01-01T12:00:00"
    assert cast(str, mail_message.content) == raw_email
    assert mail_message.body == "Test body content\n"
    assert mail_message.attachments == attachments


def test_fetch_email(email_provider):
    # Configure the provider with sample emails
    email_provider.select("INBOX")

    # Test fetching an existing email
    result = fetch_email(email_provider, "101")

    # Verify result contains the expected UID and content
    assert result is not None
    uid, content = result
    assert uid == "101"
    assert b"This is test email 1" in content

    # Test fetching a non-existent email
    result = fetch_email(email_provider, "999")
    assert result is None


def test_fetch_email_since(email_provider):
    # Fetch emails from INBOX folder
    result = fetch_email_since(email_provider, "INBOX", datetime(1970, 1, 1))

    # Verify we got the expected number of emails
    assert len(result) == 2

    # Verify content of fetched emails
    uids = sorted([uid or "" for uid, _ in result])
    assert uids == ["101", "102"]

    # Test with a folder that doesn't exist
    result = fetch_email_since(
        email_provider, "NonExistentFolder", datetime(1970, 1, 1)
    )
    assert result == []


def test_process_folder(email_provider):
    account = MagicMock(spec=EmailAccount)
    account.id = 123
    account.tags = ["test"]

    results = process_folder(
        email_provider, "INBOX", account, datetime(1970, 1, 1), MagicMock()
    )

    assert results == {"messages_found": 2, "new_messages": 2, "errors": 0}


def test_process_folder_no_emails(email_provider):
    account = MagicMock(spec=EmailAccount)
    account.id = 123
    email_provider.search = MagicMock(return_value=("OK", [b""]))

    result = process_folder(
        email_provider, "Empty", account, datetime(1970, 1, 1), MagicMock()
    )
    assert result == {"messages_found": 0, "new_messages": 0, "errors": 0}


def test_process_folder_error(email_provider):
    account = MagicMock(spec=EmailAccount)
    account.id = 123

    mock_processor = MagicMock()

    def raise_exception(*args):
        raise Exception("Test error")

    email_provider.search = raise_exception

    result = process_folder(
        email_provider, "INBOX", account, datetime(1970, 1, 1), mock_processor
    )
    assert result == {"messages_found": 0, "new_messages": 0, "errors": 0}


def test_vectorize_email_basic(db_session, qdrant, mock_uuid4):
    mail_message = MailMessage(
        sha256=b"test_hash" + bytes(24),
        tags=["test"],
        size=100,
        mime_type="message/rfc822",
        embed_status="RAW",
        message_id="<test-vector@example.com>",
        subject="Test Vectorization",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        content="This is a test email for vectorization",
        folder="INBOX",
    )
    db_session.add(mail_message)
    db_session.flush()

    assert cast(str, mail_message.embed_status) == "RAW"

    with patch.object(embedding, "embed_text", return_value=[[0.1] * 1024]):
        vectorize_email(mail_message)
        assert [c.id for c in mail_message.chunks] == [
            "00000000-0000-0000-0000-000000000001"
        ]

    db_session.commit()
    assert cast(str, mail_message.embed_status) == "STORED"


def test_vectorize_email_with_attachments(db_session, qdrant, mock_uuid4):
    mail_message = MailMessage(
        sha256=b"test_hash" + bytes(24),
        tags=["test"],
        size=100,
        mime_type="message/rfc822",
        embed_status="RAW",
        message_id="<test-vector-attach@example.com>",
        subject="Test Vectorization with Attachments",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        content="This is a test email with attachments",
        folder="INBOX",
    )
    db_session.add(mail_message)
    db_session.flush()

    # Add two attachments - one with content and one with file_path
    attachment1 = EmailAttachment(
        mail_message_id=mail_message.id,
        size=100,
        content=base64.b64encode(b"This is inline content"),
        filename=None,
        modality="doc",
        sha256=b"test_hash1" + bytes(24),
        tags=["test"],
        mime_type="text/plain",
        embed_status="RAW",
    )

    file_path = mail_message.attachments_path / "stored.txt"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"This is stored content")
    attachment2 = EmailAttachment(
        mail_message_id=mail_message.id,
        size=200,
        content=None,
        filename=str(file_path),
        modality="doc",
        sha256=b"test_hash2" + bytes(24),
        tags=["test"],
        mime_type="text/plain",
        embed_status="RAW",
    )

    db_session.add_all([attachment1, attachment2])
    db_session.flush()

    # Mock embedding functions but use real qdrant
    with patch.object(embedding, "embed_text", return_value=[[0.1] * 1024]):
        # Call the function
        vectorize_email(mail_message)

        # Verify results
        vector_ids = [
            c.id for c in mail_message.chunks + attachment1.chunks + attachment2.chunks
        ]
        assert vector_ids == [
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            "00000000-0000-0000-0000-000000000003",
        ]

    db_session.commit()
    assert cast(str, mail_message.embed_status) == "STORED"
    assert cast(str, attachment1.embed_status) == "STORED"
    assert cast(str, attachment2.embed_status) == "STORED"
