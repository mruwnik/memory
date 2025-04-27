import email
import email.mime.multipart
import email.mime.text
import email.mime.base
import base64
import pathlib

from datetime import datetime
from email.utils import formatdate
from unittest.mock import ANY, MagicMock, patch
import pytest
from memory.common.db.models import SourceItem, MailMessage, EmailAttachment, EmailAccount
from memory.common import settings
from memory.workers.email import (
    compute_message_hash,
    create_source_item,
    extract_attachments,
    extract_body,
    extract_date,
    extract_email_uid,
    extract_recipients,
    parse_email_message,
    check_message_exists,
    create_mail_message,
    fetch_email,
    fetch_email_since,
    process_folder,
    process_attachment,
    process_attachments,
)



# Use a simple counter to generate unique message IDs without calling make_msgid
_msg_id_counter = 0


def _generate_test_message_id():
    """Generate a simple message ID for testing without expensive calls"""
    global _msg_id_counter
    _msg_id_counter += 1
    return f"<test-message-{_msg_id_counter}@example.com>"


def create_email_message(
    subject="Test Subject",
    from_addr="sender@example.com",
    to_addrs="recipient@example.com",
    cc_addrs=None,
    bcc_addrs=None,
    date=None,
    body="Test body content",
    attachments=None,
    multipart=True,
    message_id=None,
):
    """Helper function to create email.message.Message objects for testing"""
    if multipart:
        msg = email.mime.multipart.MIMEMultipart()
        msg.attach(email.mime.text.MIMEText(body))

        if attachments:
            for attachment in attachments:
                attachment_part = email.mime.base.MIMEBase(
                    "application", "octet-stream"
                )
                attachment_part.set_payload(attachment["content"])
                attachment_part.add_header(
                    "Content-Disposition",
                    f"attachment; filename={attachment['filename']}",
                )
                msg.attach(attachment_part)
    else:
        msg = email.mime.text.MIMEText(body)

    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addrs

    if cc_addrs:
        msg["Cc"] = cc_addrs
    if bcc_addrs:
        msg["Bcc"] = bcc_addrs
    if date:
        msg["Date"] = formatdate(float(date.timestamp()))
    if message_id:
        msg["Message-ID"] = message_id
    else:
        msg["Message-ID"] = _generate_test_message_id()

    return msg


@pytest.mark.parametrize(
    "to_addr, cc_addr, bcc_addr, expected",
    [
        # Single recipient in To field
        ("recipient@example.com", None, None, ["recipient@example.com"]),
        # Multiple recipients in To field
        (
            "recipient1@example.com, recipient2@example.com",
            None,
            None,
            ["recipient1@example.com", "recipient2@example.com"],
        ),
        # To, Cc fields
        (
            "recipient@example.com",
            "cc@example.com",
            None,
            ["recipient@example.com", "cc@example.com"],
        ),
        # To, Cc, Bcc fields
        (
            "recipient@example.com",
            "cc@example.com",
            "bcc@example.com",
            ["recipient@example.com", "cc@example.com", "bcc@example.com"],
        ),
        # Empty fields
        ("", "", "", []),
    ],
)
def test_extract_recipients(to_addr, cc_addr, bcc_addr, expected):
    msg = create_email_message(to_addrs=to_addr, cc_addrs=cc_addr, bcc_addrs=bcc_addr)
    assert sorted(extract_recipients(msg)) == sorted(expected)


def test_extract_date_missing():
    msg = create_email_message(date=None)
    assert extract_date(msg) is None


@pytest.mark.parametrize(
    "date_str",
    [
        "Invalid Date Format",
        "2023-01-01",  # ISO format but not RFC compliant
        "Monday, Jan 1, 2023",  # Descriptive but not RFC compliant
        "01/01/2023",  # Common format but not RFC compliant
        "",  # Empty string
    ],
)
def test_extract_date_invalid_formats(date_str):
    msg = create_email_message()
    msg["Date"] = date_str
    assert extract_date(msg) is None


@pytest.mark.parametrize(
    "date_str",
    [
        "Mon, 01 Jan 2023 12:00:00 +0000",  # RFC 5322 format
        "01 Jan 2023 12:00:00 +0000",  # RFC 822 format
        "Mon, 01 Jan 2023 12:00:00 GMT",  # With timezone name
    ],
)
def test_extract_date(date_str):
    msg = create_email_message()
    msg["Date"] = date_str
    result = extract_date(msg)

    assert result is not None
    assert result.year == 2023
    assert result.month == 1
    assert result.day == 1


@pytest.mark.parametrize("multipart", [True, False])
def test_extract_body_text_plain(multipart):
    body_content = "This is a test email body"
    msg = create_email_message(body=body_content, multipart=multipart)
    extracted = extract_body(msg)

    # Strip newlines for comparison since multipart emails often add them
    assert extracted.strip() == body_content.strip()


def test_extract_body_with_attachments():
    body_content = "This is a test email body"
    attachments = [{"filename": "test.txt", "content": b"attachment content"}]
    msg = create_email_message(body=body_content, attachments=attachments)
    assert body_content in extract_body(msg)


def test_extract_attachments_none():
    msg = create_email_message(multipart=True)
    assert extract_attachments(msg) == []


def test_extract_attachments_with_files():
    attachments = [
        {"filename": "test1.txt", "content": b"content1"},
        {"filename": "test2.pdf", "content": b"content2"},
    ]
    msg = create_email_message(attachments=attachments)

    result = extract_attachments(msg)
    assert len(result) == 2
    assert result[0]["filename"] == "test1.txt"
    assert result[1]["filename"] == "test2.pdf"


def test_extract_attachments_non_multipart():
    msg = create_email_message(multipart=False)
    assert extract_attachments(msg) == []


@pytest.mark.parametrize(
    "attachment_size, max_inline_size, message_id",
    [
        # Small attachment, should be base64 encoded and returned inline
        (100, 1000, "<test@example.com>"),
        # Edge case: exactly at max size, should be base64 encoded
        (100, 100, "<test@example.com>"),
    ],
)
def test_process_attachment_inline(attachment_size, max_inline_size, message_id):
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
        result = process_attachment(attachment, message)

    assert result is not None
    # For inline attachments, content should be base64 encoded string
    assert isinstance(result.content, bytes)
    # Decode the base64 string and compare with the original content
    decoded_content = base64.b64decode(result.content)
    assert decoded_content == attachment["content"]
    assert result.file_path is None


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
        result = process_attachment(attachment, message)

    assert result is not None
    assert not result.content
    assert result.file_path == str(settings.FILE_STORAGE_DIR / "sender@example.com" / "INBOX" / "test.txt")


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
        assert process_attachment(attachment, message) is None


def test_process_attachments_empty():
    assert process_attachments([], "<test@example.com>") == []


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
        message_id="<test@example.com>",
        sender="sender@example.com",
        folder="INBOX",
    )

    with patch.object(settings, "MAX_INLINE_ATTACHMENT_SIZE", 50):
        # Process attachments
        results = process_attachments(attachments, message)

    # Verify we have all attachments processed
    assert len(results) == 3

    # Verify small attachments are base64 encoded
    assert isinstance(results[0].content, bytes)
    assert isinstance(results[2].content, bytes)

    # Verify large attachment has a path
    assert results[1].file_path is not None


@pytest.mark.parametrize(
    "msg_id, subject, sender, body, expected",
    [
        (
            "<test@example.com>",
            "Test Subject",
            "sender@example.com",
            "Test body",
            b"\xf2\xbd",  # First two bytes of the actual hash
        ),
        (
            "<different@example.com>",
            "Test Subject",
            "sender@example.com",
            "Test body",
            b"\xa4\x15",  # Will be different from the first hash
        ),
    ],
)
def test_compute_message_hash(msg_id, subject, sender, body, expected):
    result = compute_message_hash(msg_id, subject, sender, body)

    # Verify it's bytes and correct length for SHA-256 (32 bytes)
    assert isinstance(result, bytes)
    assert len(result) == 32

    # Verify first two bytes match expected
    assert result[:2] == expected


def test_hash_consistency():
    args = ("<test@example.com>", "Test Subject", "sender@example.com", "Test body")
    assert compute_message_hash(*args) == compute_message_hash(*args)


def test_parse_simple_email():
    test_date = datetime(2023, 1, 1, 12, 0, 0)
    msg_id = "<test123@example.com>"
    msg = create_email_message(
        subject="Test Subject",
        from_addr="sender@example.com",
        to_addrs="recipient@example.com",
        date=test_date,
        body="Test body content",
        message_id=msg_id,
    )

    result = parse_email_message(msg.as_string())

    assert result == {
        "message_id": msg_id,
        "subject": "Test Subject",
        "sender": "sender@example.com",
        "recipients": ["recipient@example.com"],
        "body": "Test body content\n",
        "attachments": [],
        "sent_at": ANY,
    }
    assert abs(result["sent_at"].timestamp() - test_date.timestamp()) < 86400


def test_parse_email_with_attachments():
    attachments = [{"filename": "test.txt", "content": b"attachment content"}]
    msg = create_email_message(attachments=attachments)

    result = parse_email_message(msg.as_string())

    assert len(result["attachments"]) == 1
    assert result["attachments"][0]["filename"] == "test.txt"


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


def test_create_source_item(db_session):
    # Mock data
    message_hash = b"test_hash_bytes" + bytes(28)  # 32 bytes for SHA-256
    account_tags = ["work", "important"]
    raw_email_size = 1024

    # Call function
    source_item = create_source_item(
        db_session=db_session,
        message_hash=message_hash,
        account_tags=account_tags,
        raw_email_size=raw_email_size,
    )

    # Verify the source item was created correctly
    assert isinstance(source_item, SourceItem)
    assert source_item.id is not None
    assert source_item.modality == "mail"
    assert source_item.sha256 == message_hash
    assert source_item.tags == account_tags
    assert source_item.byte_length == raw_email_size
    assert source_item.mime_type == "message/rfc822"
    assert source_item.embed_status == "RAW"

    # Verify it was added to the session
    db_session.flush()
    fetched_item = db_session.query(SourceItem).filter_by(id=source_item.id).one()
    assert fetched_item is not None
    assert fetched_item.sha256 == message_hash


@pytest.mark.parametrize(
    "setup_db, message_id, message_hash, expected_exists",
    [
        # Test by message ID
        (
            lambda db: (
                # First create source_item to satisfy foreign key constraint
                db.add(
                    SourceItem(
                        id=1,
                        modality="mail",
                        sha256=b"some_hash_bytes" + bytes(28),
                        tags=["test"],
                        byte_length=100,
                        mime_type="message/rfc822",
                        embed_status="RAW",
                    )
                ),
                db.flush(),
                # Then create mail_message
                db.add(
                    MailMessage(
                        source_id=1,
                        message_id="<test@example.com>",
                        subject="Test",
                        sender="test@example.com",
                        recipients=["recipient@example.com"],
                        body_raw="Test body",
                    )
                ),
            ),
            "<test@example.com>",
            b"unmatched_hash",
            True,
        ),
        # Test by non-existent message ID
        (lambda db: None, "<nonexistent@example.com>", b"unmatched_hash", False),
        # Test by hash
        (
            lambda db: db.add(
                SourceItem(
                    modality="mail",
                    sha256=b"test_hash_bytes" + bytes(28),
                    tags=["test"],
                    byte_length=100,
                    mime_type="message/rfc822",
                    embed_status="RAW",
                )
            ),
            "",
            b"test_hash_bytes" + bytes(28),
            True,
        ),
        # Test by non-existent hash
        (lambda db: None, "", b"different_hash_" + bytes(28), False),
    ],
)
def test_check_message_exists(
    db_session, setup_db, message_id, message_hash, expected_exists
):
    # Setup test data
    if setup_db:
        setup_db(db_session)
        db_session.flush()

    # Test the function
    assert check_message_exists(db_session, message_id, message_hash) == expected_exists


def test_create_mail_message(db_session):
    source_item = SourceItem(
        id=1,
        modality="mail",
        sha256=b"test_hash_bytes" + bytes(28),
        tags=["test"],
        byte_length=100,
    )
    db_session.add(source_item)
    db_session.flush()
    source_id = source_item.id
    parsed_email = {
        "message_id": "<test@example.com>",
        "subject": "Test Subject",
        "sender": "sender@example.com",
        "recipients": ["recipient@example.com"],
        "sent_at": datetime(2023, 1, 1, 12, 0, 0),
        "body": "Test body content",
        "attachments": [
            {"filename": "test.txt", "content_type": "text/plain", "size": 100}
        ],
    }
    folder = "INBOX"

    # Call function
    mail_message = create_mail_message(
        db_session=db_session,
        source_id=source_id,
        parsed_email=parsed_email,
        folder=folder,
    )

    attachments = db_session.query(EmailAttachment).filter(EmailAttachment.mail_message_id == mail_message.id).all()

    # Verify the mail message was created correctly
    assert isinstance(mail_message, MailMessage)
    assert mail_message.source_id == source_id
    assert mail_message.message_id == parsed_email["message_id"]
    assert mail_message.subject == parsed_email["subject"]
    assert mail_message.sender == parsed_email["sender"]
    assert mail_message.recipients == parsed_email["recipients"]
    assert mail_message.sent_at == parsed_email["sent_at"]
    assert mail_message.body_raw == parsed_email["body"]
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
    uids = sorted([uid for uid, _ in result])
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
