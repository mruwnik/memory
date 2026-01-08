"""Tests for email sending functionality."""

from email.mime.multipart import MIMEMultipart
from unittest.mock import MagicMock, Mock, patch

import pytest

from memory.common.email_sender import (
    EmailAttachmentData,
    EmailResult,
    GmailConfig,
    SmtpConfig,
    _build_mime_message,
    _get_smtp_server_port,
    _infer_smtp_server,
    get_account_by_address,
    get_user_email_accounts,
    send_email,
    send_via_gmail_api,
    send_via_smtp,
)


@pytest.mark.parametrize(
    "imap_server,expected_smtp",
    [
        ("imap.gmail.com", "smtp.gmail.com"),
        ("imap.mail.yahoo.com", "smtp.mail.yahoo.com"),
        ("imap-mail.outlook.com", "smtp-mail.outlook.com"),
        ("outlook.office365.com", "smtp.office365.com"),
        ("imap.fastmail.com", "smtp.fastmail.com"),
        ("imap.zoho.com", "smtp.zoho.com"),
        ("imap.example.com", "smtp.example.com"),  # Generic imap. prefix
        ("mail.example.com", None),  # No imap. prefix, no mapping
        ("", None),
        (None, None),
    ],
)
def test_infer_smtp_server(imap_server, expected_smtp):
    result = _infer_smtp_server(imap_server)
    assert result == expected_smtp


@pytest.mark.parametrize(
    "smtp_server,smtp_port,imap_server,expected_server,expected_port",
    [
        # Explicit SMTP config takes precedence
        ("smtp.explicit.com", 465, "imap.gmail.com", "smtp.explicit.com", 465),
        # Only explicit server, default port
        ("smtp.explicit.com", None, "imap.gmail.com", "smtp.explicit.com", 587),
        # Inferred from IMAP
        (None, None, "imap.gmail.com", "smtp.gmail.com", 587),
        # Inferred with explicit port
        (None, 465, "imap.gmail.com", "smtp.gmail.com", 465),
    ],
)
def test_get_smtp_server_port_success(
    smtp_server, smtp_port, imap_server, expected_server, expected_port
):
    config = SmtpConfig(
        email_address="test@example.com",
        username="test@example.com",
        password="password",
        smtp_server=smtp_server,
        smtp_port=smtp_port,
        imap_server=imap_server,
    )

    server, port = _get_smtp_server_port(config)
    assert server == expected_server
    assert port == expected_port


def test_get_smtp_server_port_failure():
    config = SmtpConfig(
        email_address="test@example.com",
        username="test@example.com",
        password="password",
        smtp_server=None,
        smtp_port=None,
        imap_server="mail.example.com",  # Can't infer SMTP from this
    )

    with pytest.raises(ValueError, match="Cannot determine SMTP server"):
        _get_smtp_server_port(config)


@pytest.mark.parametrize(
    "include_bcc,bcc_list,expect_bcc_header",
    [
        (False, ["bcc@example.com"], False),
        (True, ["bcc@example.com"], True),
        (True, None, False),
        (False, None, False),
    ],
)
def test_build_mime_message_bcc_handling(include_bcc, bcc_list, expect_bcc_header):
    msg = _build_mime_message(
        from_addr="from@example.com",
        to=["to@example.com"],
        subject="Test",
        body="Body",
        bcc=bcc_list,
        include_bcc_header=include_bcc,
    )

    assert isinstance(msg, MIMEMultipart)
    assert msg["From"] == "from@example.com"
    assert msg["To"] == "to@example.com"
    assert msg["Subject"] == "Test"

    if expect_bcc_header:
        assert msg["Bcc"] == "bcc@example.com"
    else:
        assert msg["Bcc"] is None


@pytest.mark.parametrize(
    "cc,reply_to",
    [
        (["cc1@example.com", "cc2@example.com"], "reply@example.com"),
        (None, None),
        (["cc@example.com"], None),
        (None, "reply@example.com"),
    ],
)
def test_build_mime_message_optional_headers(cc, reply_to):
    msg = _build_mime_message(
        from_addr="from@example.com",
        to=["to@example.com"],
        subject="Test",
        body="Body",
        cc=cc,
        reply_to=reply_to,
    )

    if cc:
        assert msg["Cc"] == ", ".join(cc)
    else:
        assert msg["Cc"] is None

    if reply_to:
        assert msg["Reply-To"] == reply_to
    else:
        assert msg["Reply-To"] is None


def test_build_mime_message_with_html():
    msg = _build_mime_message(
        from_addr="from@example.com",
        to=["to@example.com"],
        subject="Test",
        body="Plain text body",
        html_body="<html><body>HTML body</body></html>",
    )

    # Should have multipart/alternative for body
    payload = msg.get_payload()
    assert len(payload) == 1
    alt_part = payload[0]
    assert alt_part.get_content_type() == "multipart/alternative"


def test_build_mime_message_with_attachments():
    attachments = [
        EmailAttachmentData(
            filename="test.txt",
            content=b"Hello, World!",
            content_type="text/plain",
        ),
        EmailAttachmentData(
            filename="data.bin",
            content=b"\x00\x01\x02",
            content_type="application/octet-stream",
        ),
    ]

    msg = _build_mime_message(
        from_addr="from@example.com",
        to=["to@example.com"],
        subject="Test",
        body="Body",
        attachments=attachments,
    )

    payload = msg.get_payload()
    # Body + 2 attachments
    assert len(payload) == 3


@patch("memory.common.email_sender.smtplib.SMTP")
def test_send_via_smtp_success(mock_smtp_class):
    mock_server = MagicMock()
    mock_smtp_class.return_value.__enter__ = Mock(return_value=mock_server)
    mock_smtp_class.return_value.__exit__ = Mock(return_value=False)

    config = SmtpConfig(
        email_address="sender@example.com",
        username="sender@example.com",
        password="password123",
        smtp_server="smtp.example.com",
        smtp_port=587,
        imap_server=None,
    )

    result = send_via_smtp(
        config=config,
        to=["recipient@example.com"],
        subject="Test Subject",
        body="Test body",
    )

    assert result.success is True
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("sender@example.com", "password123")
    mock_server.send_message.assert_called_once()


@patch("memory.common.email_sender.smtplib.SMTP")
def test_send_via_smtp_with_bcc(mock_smtp_class):
    mock_server = MagicMock()
    mock_smtp_class.return_value.__enter__ = Mock(return_value=mock_server)
    mock_smtp_class.return_value.__exit__ = Mock(return_value=False)

    config = SmtpConfig(
        email_address="sender@example.com",
        username="sender@example.com",
        password="password123",
        smtp_server="smtp.example.com",
        smtp_port=587,
        imap_server=None,
    )

    result = send_via_smtp(
        config=config,
        to=["to@example.com"],
        subject="Test",
        body="Body",
        cc=["cc@example.com"],
        bcc=["bcc@example.com"],
    )

    assert result.success is True
    # Verify all recipients were passed to send_message
    call_args = mock_server.send_message.call_args
    to_addrs = call_args.kwargs.get("to_addrs") or call_args[1].get("to_addrs")
    assert "to@example.com" in to_addrs
    assert "cc@example.com" in to_addrs
    assert "bcc@example.com" in to_addrs


@patch("memory.common.email_sender.smtplib.SMTP")
def test_send_via_smtp_auth_failure(mock_smtp_class):
    import smtplib

    mock_server = MagicMock()
    mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Auth failed")
    mock_smtp_class.return_value.__enter__ = Mock(return_value=mock_server)
    mock_smtp_class.return_value.__exit__ = Mock(return_value=False)

    config = SmtpConfig(
        email_address="sender@example.com",
        username="sender@example.com",
        password="wrong_password",
        smtp_server="smtp.example.com",
        smtp_port=587,
        imap_server=None,
    )

    result = send_via_smtp(
        config=config,
        to=["recipient@example.com"],
        subject="Test",
        body="Body",
    )

    assert result.success is False
    assert "Authentication failed" in result.error


@patch("memory.common.email_sender.smtplib.SMTP")
def test_send_via_smtp_timeout(mock_smtp_class):
    import socket

    mock_smtp_class.side_effect = socket.timeout("Connection timed out")

    config = SmtpConfig(
        email_address="sender@example.com",
        username="sender@example.com",
        password="password123",
        smtp_server="smtp.example.com",
        smtp_port=587,
        imap_server=None,
    )

    result = send_via_smtp(
        config=config,
        to=["recipient@example.com"],
        subject="Test",
        body="Body",
    )

    assert result.success is False
    assert "timed out" in result.error.lower()


@patch("memory.common.email_sender.smtplib.SMTP")
def test_send_via_smtp_no_password(mock_smtp_class):
    """Test that missing password returns clear error before attempting connection."""
    config = SmtpConfig(
        email_address="sender@example.com",
        username="sender@example.com",
        password=None,
        smtp_server="smtp.example.com",
        smtp_port=587,
        imap_server=None,
    )

    result = send_via_smtp(
        config=config,
        to=["recipient@example.com"],
        subject="Test",
        body="Body",
    )

    assert result.success is False
    assert "password" in result.error.lower()
    # Should not have attempted to connect
    mock_smtp_class.assert_not_called()


@patch("memory.common.email_sender.build")
def test_send_via_gmail_api_success(mock_build):
    mock_credentials = Mock()

    mock_service = Mock()
    mock_build.return_value = mock_service
    mock_service.users().messages().send().execute.return_value = {
        "id": "msg123",
        "threadId": "thread456",
    }

    config = GmailConfig(
        email_address="sender@gmail.com",
        credentials=mock_credentials,
    )

    result = send_via_gmail_api(
        config=config,
        to=["recipient@example.com"],
        subject="Test Subject",
        body="Test body",
    )

    assert result.success is True
    assert result.message_id == "msg123"
    mock_build.assert_called_once_with("gmail", "v1", credentials=mock_credentials)


@patch("memory.parsers.google_drive.refresh_credentials")
def test_gmail_config_missing_scope(mock_refresh):
    """Test that GmailConfig.from_account raises ValueError if missing scope."""
    account = Mock()
    account.email_address = "sender@gmail.com"

    google_account = Mock()
    google_account.scopes = ["https://www.googleapis.com/auth/drive"]  # Wrong scope

    session = Mock()

    with pytest.raises(ValueError, match="gmail.send"):
        GmailConfig.from_account(account, google_account, session)


@patch("memory.common.email_sender.send_via_smtp")
def test_send_email_routes_to_smtp(mock_smtp):
    mock_smtp.return_value = EmailResult(success=True, message_id="smtp123")

    config = SmtpConfig(
        email_address="sender@example.com",
        username="sender@example.com",
        password="password123",
        smtp_server="smtp.example.com",
        smtp_port=587,
        imap_server=None,
    )

    result = send_email(
        config=config,
        to=["recipient@example.com"],
        subject="Test",
        body="Body",
    )

    assert result.success is True
    assert result.message_id == "smtp123"
    mock_smtp.assert_called_once()


@patch("memory.common.email_sender.send_via_gmail_api")
def test_send_email_routes_to_gmail(mock_gmail):
    mock_gmail.return_value = EmailResult(success=True, message_id="gmail123")

    mock_credentials = Mock()
    config = GmailConfig(
        email_address="sender@gmail.com",
        credentials=mock_credentials,
    )

    result = send_email(
        config=config,
        to=["recipient@example.com"],
        subject="Test",
        body="Body",
    )

    assert result.success is True
    assert result.message_id == "gmail123"
    mock_gmail.assert_called_once()


def test_get_user_email_accounts(db_session):
    from memory.common.db.models import EmailAccount
    from memory.common.db.models.users import HumanUser

    # Create a test user
    user = HumanUser.create_with_password(
        email="testuser@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.flush()

    # Create email accounts
    send_enabled_account = EmailAccount(
        user_id=user.id,
        name="Send Enabled Account",
        email_address="enabled@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        send_enabled=True,
    )
    send_disabled_account = EmailAccount(
        user_id=user.id,
        name="Send Disabled Account",
        email_address="disabled@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        send_enabled=False,
    )
    db_session.add_all([send_enabled_account, send_disabled_account])
    db_session.commit()

    # Test send_enabled_only=True (default)
    accounts = get_user_email_accounts(db_session, user.id)
    assert len(accounts) == 1
    assert accounts[0].email_address == "enabled@example.com"

    # Test send_enabled_only=False
    all_accounts = get_user_email_accounts(db_session, user.id, send_enabled_only=False)
    assert len(all_accounts) == 2


def test_get_account_by_address(db_session):
    from memory.common.db.models import EmailAccount
    from memory.common.db.models.users import HumanUser

    user = HumanUser.create_with_password(
        email="testuser@example.com", name="Test User", password="password123"
    )
    db_session.add(user)
    db_session.flush()

    account = EmailAccount(
        user_id=user.id,
        name="Test Account",
        email_address="test@example.com",
        account_type="imap",
        imap_server="imap.example.com",
        send_enabled=True,
    )
    db_session.add(account)
    db_session.commit()

    # Found
    result = get_account_by_address(db_session, user.id, "test@example.com")
    assert result is not None
    assert result.email_address == "test@example.com"

    # Not found - wrong address
    result = get_account_by_address(db_session, user.id, "other@example.com")
    assert result is None

    # Not found - wrong user
    result = get_account_by_address(db_session, user.id + 999, "test@example.com")
    assert result is None
