import email
import email.mime.multipart
import email.mime.text
import email.mime.base

from datetime import datetime
from email.utils import formatdate
from unittest.mock import ANY, patch
import pytest
from memory.common.parsers.email import (
    compute_message_hash,
    extract_attachments,
    extract_body,
    extract_date,
    extract_recipients,
    parse_email_message,
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

    result = parse_email_message(msg.as_string(), msg_id)

    assert result == {
        "message_id": msg_id,
        "subject": "Test Subject",
        "sender": "sender@example.com",
        "recipients": ["recipient@example.com"],
        "body": "Test body content\n",
        "attachments": [],
        "sent_at": ANY,
        "hash": b'\xed\xa0\x9b\xd4\t4\x06\xb9l\xa4\xb3*\xe4NpZ\x19\xc2\x9b\x87'
              + b'\xa6\x12\r\x7fS\xb6\xf1\xbe\x95\x9c\x99\xf1',
    }
    assert abs(result["sent_at"].timestamp() - test_date.timestamp()) < 86400


def test_parse_email_with_attachments():
    attachments = [{"filename": "test.txt", "content": b"attachment content"}]
    msg = create_email_message(attachments=attachments)

    result = parse_email_message(msg.as_string(), "123")

    assert len(result["attachments"]) == 1
    assert result["attachments"][0]["filename"] == "test.txt"
