import email
import email.mime.multipart
import email.mime.text
import email.mime.base
from datetime import datetime
from email.utils import formatdate
from unittest.mock import ANY, MagicMock, patch
import pytest
import imaplib
from memory.common.db.models import SourceItem
from memory.common.db.models import MailMessage, EmailAccount
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
    imap_connection,
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
                attachment_part = email.mime.base.MIMEBase("application", "octet-stream")
                attachment_part.set_payload(attachment["content"])
                attachment_part.add_header(
                    "Content-Disposition", 
                    f"attachment; filename={attachment['filename']}"
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
        (
            "recipient@example.com", 
            None, 
            None, 
            ["recipient@example.com"]
        ),
        # Multiple recipients in To field
        (
            "recipient1@example.com, recipient2@example.com", 
            None, 
            None, 
            ["recipient1@example.com", "recipient2@example.com"]
        ),
        # To, Cc fields
        (
            "recipient@example.com", 
            "cc@example.com", 
            None, 
            ["recipient@example.com", "cc@example.com"]
        ),
        # To, Cc, Bcc fields
        (
            "recipient@example.com", 
            "cc@example.com", 
            "bcc@example.com", 
            ["recipient@example.com", "cc@example.com", "bcc@example.com"]
        ),
        # Empty fields
        (
            "", 
            "", 
            "", 
            []
        ),
    ]
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
    ]
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
    ]
)
def test_extract_date(date_str):
    msg = create_email_message()
    msg["Date"] = date_str
    result = extract_date(msg)
    
    assert result is not None
    assert result.year == 2023
    assert result.month == 1
    assert result.day == 1


@pytest.mark.parametrize('multipart', [True, False])
def test_extract_body_text_plain(multipart):
    body_content = "This is a test email body"
    msg = create_email_message(body=body_content, multipart=multipart)
    extracted = extract_body(msg)
    
    # Strip newlines for comparison since multipart emails often add them
    assert extracted.strip() == body_content.strip()


def test_extract_body_with_attachments():
    body_content = "This is a test email body"
    attachments = [
        {"filename": "test.txt", "content": b"attachment content"}
    ]
    msg = create_email_message(body=body_content, attachments=attachments)
    assert body_content in extract_body(msg)


def test_extract_attachments_none():
    msg = create_email_message(multipart=True)
    assert extract_attachments(msg) == []


def test_extract_attachments_with_files():
    attachments = [
        {"filename": "test1.txt", "content": b"content1"},
        {"filename": "test2.pdf", "content": b"content2"}
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
            b"\xf2\xbd"  # First two bytes of the actual hash
        ),
        (
            "<different@example.com>", 
            "Test Subject", 
            "sender@example.com", 
            "Test body",
            b"\xa4\x15"  # Will be different from the first hash
        ),
    ]
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
        message_id=msg_id
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
    attachments = [
        {"filename": "test.txt", "content": b"attachment content"}
    ]
    msg = create_email_message(attachments=attachments)
    
    result = parse_email_message(msg.as_string())
    
    assert len(result["attachments"]) == 1
    assert result["attachments"][0]["filename"] == "test.txt"


def test_extract_email_uid_valid():
    msg_data = [(b'1 (UID 12345 RFC822 {1234}', b'raw email content')]
    uid, raw_email = extract_email_uid(msg_data)
    
    assert uid == "12345"
    assert raw_email == b'raw email content'


def test_extract_email_uid_no_match():
    msg_data = [(b'1 (RFC822 {1234}', b'raw email content')]
    uid, raw_email = extract_email_uid(msg_data)
    
    assert uid is None
    assert raw_email == b'raw email content'


def test_create_source_item(db_session):
    # Mock data
    message_hash = b'test_hash_bytes' + bytes(28)  # 32 bytes for SHA-256
    account_tags = ["work", "important"]
    raw_email_size = 1024
    
    # Call function
    source_item = create_source_item(
        db_session=db_session,
        message_hash=message_hash,
        account_tags=account_tags,
        raw_email_size=raw_email_size
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
                db.add(SourceItem(
                    id=1,
                    modality="mail",
                    sha256=b'some_hash_bytes' + bytes(28),
                    tags=["test"],
                    byte_length=100,
                    mime_type="message/rfc822",
                    embed_status="RAW"
                )),
                db.flush(),
                # Then create mail_message
                db.add(MailMessage(
                    source_id=1,
                    message_id="<test@example.com>",
                    subject="Test",
                    sender="test@example.com",
                    recipients=["recipient@example.com"],
                    body_raw="Test body"
                ))
            ),
            "<test@example.com>",
            b"unmatched_hash",
            True
        ),
        # Test by non-existent message ID
        (
            lambda db: None,
            "<nonexistent@example.com>",
            b"unmatched_hash",
            False
        ),
        # Test by hash
        (
            lambda db: db.add(SourceItem(
                modality="mail",
                sha256=b'test_hash_bytes' + bytes(28),
                tags=["test"],
                byte_length=100,
                mime_type="message/rfc822",
                embed_status="RAW"
            )),
            "",
            b'test_hash_bytes' + bytes(28),
            True
        ),
        # Test by non-existent hash
        (
            lambda db: None,
            "",
            b'different_hash_' + bytes(28),
            False
        ),
    ]
)
def test_check_message_exists(db_session, setup_db, message_id, message_hash, expected_exists):
    # Setup test data
    if setup_db:
        setup_db(db_session)
        db_session.flush()
    
    # Test the function
    assert check_message_exists(db_session, message_id, message_hash) == expected_exists


def test_create_mail_message(db_session):
    source_id = 1
    parsed_email = {
        "message_id": "<test@example.com>",
        "subject": "Test Subject",
        "sender": "sender@example.com",
        "recipients": ["recipient@example.com"],
        "sent_at": datetime(2023, 1, 1, 12, 0, 0),
        "body": "Test body content",
        "attachments": [{"filename": "test.txt", "content_type": "text/plain", "size": 100}]
    }
    folder = "INBOX"
    
    # Call function
    mail_message = create_mail_message(
        db_session=db_session,
        source_id=source_id,
        parsed_email=parsed_email,
        folder=folder
    )
    
    # Verify the mail message was created correctly
    assert isinstance(mail_message, MailMessage)
    assert mail_message.source_id == source_id
    assert mail_message.message_id == parsed_email["message_id"]
    assert mail_message.subject == parsed_email["subject"]
    assert mail_message.sender == parsed_email["sender"]
    assert mail_message.recipients == parsed_email["recipients"]
    assert mail_message.sent_at == parsed_email["sent_at"]
    assert mail_message.body_raw == parsed_email["body"]
    assert mail_message.attachments == {"items": parsed_email["attachments"], "folder": folder}


@pytest.mark.parametrize(
    "fetch_return, fetch_side_effect, extract_uid_return, expected_result",
    [
        # Success case
        (('OK', ['mock_data']), None, ("12345", b'raw email content'), ("12345", b'raw email content')),
        # IMAP error
        (('NO', []), None, None, None),
        # Exception case
        (None, Exception("Test error"), None, None),
    ]
)
@patch('memory.workers.email.extract_email_uid')
def test_fetch_email(
    mock_extract_email_uid, fetch_return, fetch_side_effect, extract_uid_return, expected_result
):
    conn = MagicMock(spec=imaplib.IMAP4_SSL)
    
    # Configure mocks
    if fetch_side_effect:
        conn.fetch.side_effect = fetch_side_effect
    else:
        conn.fetch.return_value = fetch_return
        
    if extract_uid_return:
        mock_extract_email_uid.return_value = extract_uid_return
    
    uid = "12345"
    
    # Call function
    result = fetch_email(conn, uid)
    
    # Verify expectations
    assert result == expected_result
    
    # Verify fetch was called if no exception
    if not fetch_side_effect:
        conn.fetch.assert_called_once_with(uid, '(UID RFC822)')


@pytest.mark.parametrize(
    "select_return, search_return, select_side_effect, expected_calls, expected_result",
    [
        # Successful case with multiple messages
        (
            ('OK', [b'1']), 
            ('OK', [b'1 2 3']), 
            None, 
            3, 
            [("1", b'email1'), ("2", b'email2'), ("3", b'email3')]
        ),
        # No messages found case
        (
            ('OK', [b'0']), 
            ('OK', [b'']), 
            None, 
            0, 
            []
        ),
        # Error in select
        (
            ('NO', [b'Error']), 
            None, 
            None, 
            0, 
            []
        ),
        # Error in search
        (
            ('OK', [b'1']), 
            ('NO', [b'Error']), 
            None, 
            0, 
            []
        ),
        # Exception in select
        (
            None, 
            None, 
            Exception("Test error"), 
            0, 
            []
        ),
    ]
)
@patch('memory.workers.email.fetch_email')
def test_fetch_email_since(
    mock_fetch_email, select_return, search_return, select_side_effect, expected_calls, expected_result
):
    conn = MagicMock(spec=imaplib.IMAP4_SSL)
    
    # Configure mocks based on parameters
    if select_side_effect:
        conn.select.side_effect = select_side_effect
    else:
        conn.select.return_value = select_return
        
    if search_return:
        conn.search.return_value = search_return
    
    # Configure fetch_email mock if needed
    if expected_calls > 0:
        mock_fetch_email.side_effect = [
            (f"{i+1}", f"email{i+1}".encode()) for i in range(expected_calls)
        ]
    
    folder = "INBOX"
    since_date = datetime(2023, 1, 1)
    
    result = fetch_email_since(conn, folder, since_date)
    
    assert mock_fetch_email.call_count == expected_calls
    assert result == expected_result


@patch('memory.workers.email.fetch_email_since')
def test_process_folder_error(mock_fetch_email_since):
    # Setup
    conn = MagicMock(spec=imaplib.IMAP4_SSL)
    folder = "INBOX"
    account = MagicMock(spec=EmailAccount)
    since_date = datetime(2023, 1, 1)
    
    # Test exception in fetch_email_since
    mock_fetch_email_since.side_effect = Exception("Test error")
    
    # Call function
    result = process_folder(conn, folder, account, since_date)
    
    # Verify
    assert result["messages_found"] == 0
    assert result["new_messages"] == 0
    assert result["errors"] == 1


@patch('memory.workers.tasks.email.process_message.delay')
@patch('memory.workers.email.fetch_email_since')
def test_process_folder(mock_fetch_email_since, mock_process_message_delay):
    conn = MagicMock(spec=imaplib.IMAP4_SSL)
    folder = "INBOX"
    account = MagicMock(spec=EmailAccount)
    account.id = 123
    since_date = datetime(2023, 1, 1)
    
    mock_fetch_email_since.return_value = [
        ("1", b'email1'),
        ("2", b'email2'),
    ]
    
    mock_process_message_delay.return_value = MagicMock()
    
    with patch('builtins.__import__', side_effect=__import__):
        result = process_folder(conn, folder, account, since_date)
    
    mock_fetch_email_since.assert_called_once_with(conn, folder, since_date)
    assert mock_process_message_delay.call_count == 2
    
    mock_process_message_delay.assert_any_call(
        account_id=account.id,
        message_id="1",
        folder=folder,
        raw_email='email1'
    )
    
    assert result["messages_found"] == 2
    assert result["new_messages"] == 2
    assert result["errors"] == 0
