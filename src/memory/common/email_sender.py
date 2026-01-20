"""Email sending utilities supporting SMTP and Gmail API."""

import base64
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from googleapiclient.discovery import build

from memory.common.db.connection import DBSession
from memory.common.db.models import EmailAccount, GoogleAccount

logger = logging.getLogger(__name__)


# Common IMAP to SMTP server mappings
SMTP_SERVER_MAP = {
    "imap.gmail.com": "smtp.gmail.com",
    "imap.mail.yahoo.com": "smtp.mail.yahoo.com",
    "imap-mail.outlook.com": "smtp-mail.outlook.com",
    "outlook.office365.com": "smtp.office365.com",
    "imap.fastmail.com": "smtp.fastmail.com",
    "imap.zoho.com": "smtp.zoho.com",
    "imap.aol.com": "smtp.aol.com",
    "imap.protonmail.ch": "smtp.protonmail.ch",
}

DEFAULT_SMTP_PORT = 587  # STARTTLS


@dataclass
class EmailAttachmentData:
    """Attachment data for sending."""

    filename: str
    content: bytes
    content_type: str | None = None


@dataclass
class EmailResult:
    """Result of an email send operation."""

    success: bool
    message_id: str | None = None
    error: str | None = None


@dataclass
class SmtpConfig:
    """SMTP configuration extracted from EmailAccount for thread-safe sending."""

    email_address: str
    username: str | None
    password: str | None
    smtp_server: str | None
    smtp_port: int | None
    imap_server: str | None

    @classmethod
    def from_account(cls, account: EmailAccount) -> "SmtpConfig":
        """Create config from EmailAccount, eagerly loading all fields."""
        return cls(
            email_address=account.email_address,
            username=account.username,
            password=account.password,
            smtp_server=account.smtp_server,
            smtp_port=account.smtp_port,
            imap_server=account.imap_server,
        )


@dataclass
class GmailConfig:
    """Gmail configuration with pre-loaded credentials for thread-safe sending."""

    email_address: str
    credentials: object  # google.oauth2.credentials.Credentials

    @classmethod
    def from_account(
        cls, account: EmailAccount, google_account: GoogleAccount, session: DBSession
    ) -> "GmailConfig":
        """Create config from accounts, refreshing credentials while session is active.

        Raises:
            ValueError: If the Google account lacks gmail.send scope
        """
        from memory.parsers.google_drive import refresh_credentials

        # Validate send scope
        scopes = google_account.scopes or []
        has_send_scope = any(
            "gmail.send" in s or "mail.google.com" in s for s in scopes
        )
        if not has_send_scope:
            raise ValueError(
                "Gmail account does not have send permission. "
                "Required scope: https://www.googleapis.com/auth/gmail.send"
            )

        credentials = refresh_credentials(google_account, session)
        return cls(
            email_address=account.email_address,
            credentials=credentials,
        )


def _infer_smtp_server(imap_server: str) -> str | None:
    """Try to infer SMTP server from IMAP server hostname."""
    if not imap_server:
        return None

    # Direct mapping
    if imap_server in SMTP_SERVER_MAP:
        return SMTP_SERVER_MAP[imap_server]

    # Generic transformation: imap.example.com -> smtp.example.com
    if imap_server.startswith("imap."):
        return "smtp." + imap_server[5:]

    return None


def _get_smtp_server_port(config: SmtpConfig) -> tuple[str, int]:
    """Get SMTP server and port from config.

    Returns:
        Tuple of (smtp_server, smtp_port)

    Raises:
        ValueError: If SMTP configuration cannot be determined
    """
    smtp_server = config.smtp_server
    smtp_port = config.smtp_port or DEFAULT_SMTP_PORT

    if not smtp_server and config.imap_server:
        smtp_server = _infer_smtp_server(config.imap_server)

    if not smtp_server:
        raise ValueError(
            f"Cannot determine SMTP server for account {config.email_address}. "
            "Please configure smtp_server explicitly."
        )

    return smtp_server, smtp_port


def _build_mime_message(
    from_addr: str,
    to: list[str],
    subject: str,
    body: str,
    html_body: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    attachments: list[EmailAttachmentData] | None = None,
    reply_to: str | None = None,
    include_bcc_header: bool = False,
) -> MIMEMultipart:
    """Build a MIME message for sending.

    Args:
        include_bcc_header: If True, include Bcc in headers (needed for Gmail API).
            For SMTP, should be False - Bcc recipients are added to envelope only.
    """
    msg = MIMEMultipart("mixed")
    msg["From"] = from_addr
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject

    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc and include_bcc_header:
        msg["Bcc"] = ", ".join(bcc)
    if reply_to:
        msg["Reply-To"] = reply_to

    # Create body part
    if html_body:
        body_part = MIMEMultipart("alternative")
        body_part.attach(MIMEText(body, "plain", "utf-8"))
        body_part.attach(MIMEText(html_body, "html", "utf-8"))
        msg.attach(body_part)
    else:
        msg.attach(MIMEText(body, "plain", "utf-8"))

    # Add attachments
    if attachments:
        for att in attachments:
            content_type = att.content_type or "application/octet-stream"
            maintype, subtype = content_type.split("/", 1)

            part = MIMEApplication(att.content, _subtype=subtype)
            part.add_header("Content-Disposition", "attachment", filename=att.filename)
            msg.attach(part)

    return msg


def send_via_smtp(
    config: SmtpConfig,
    to: list[str],
    subject: str,
    body: str,
    html_body: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    attachments: list[EmailAttachmentData] | None = None,
    reply_to: str | None = None,
) -> EmailResult:
    """Send email via SMTP.

    Args:
        config: SMTP configuration with credentials
        to: List of recipient addresses
        subject: Email subject
        body: Plain text body
        html_body: Optional HTML body
        cc: Optional CC recipients
        bcc: Optional BCC recipients
        attachments: Optional attachments
        reply_to: Optional reply-to address

    Returns:
        EmailResult with success status and message_id or error
    """
    try:
        smtp_server, smtp_port = _get_smtp_server_port(config)

        msg = _build_mime_message(
            from_addr=config.email_address,
            to=to,
            subject=subject,
            body=body,
            html_body=html_body,
            cc=cc,
            bcc=bcc,
            attachments=attachments,
            reply_to=reply_to,
        )

        # All recipients for SMTP envelope
        all_recipients = list(to)
        if cc:
            all_recipients.extend(cc)
        if bcc:
            all_recipients.extend(bcc)

        # Validate password is set
        if not config.password:
            return EmailResult(
                success=False,
                error=f"No password configured for account {config.email_address}. "
                "SMTP authentication requires a password.",
            )

        # Connect and send
        context = ssl.create_default_context()

        # Port 465 uses implicit TLS (SMTP_SSL), port 587 uses STARTTLS
        if smtp_port == 465:
            with smtplib.SMTP_SSL(
                smtp_server, smtp_port, timeout=30, context=context
            ) as server:
                server.login(config.username or config.email_address, config.password)
                server.send_message(msg, to_addrs=all_recipients)
        else:
            with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
                server.starttls(context=context)
                server.login(config.username or config.email_address, config.password)
                server.send_message(msg, to_addrs=all_recipients)

        message_id = msg.get("Message-ID")
        logger.info(f"Email sent via SMTP to {to}, message_id={message_id}")

        return EmailResult(success=True, message_id=message_id)

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP authentication failed for {config.email_address}: {e}")
        return EmailResult(success=False, error=f"Authentication failed: {e}")
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending email: {e}")
        return EmailResult(success=False, error=f"SMTP error: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error sending email via SMTP: {e}")
        return EmailResult(success=False, error=str(e))


def send_via_gmail_api(
    config: GmailConfig,
    to: list[str],
    subject: str,
    body: str,
    html_body: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    attachments: list[EmailAttachmentData] | None = None,
    reply_to: str | None = None,
) -> EmailResult:
    """Send email via Gmail API.

    Args:
        config: Gmail configuration with pre-loaded credentials
        to: List of recipient addresses
        subject: Email subject
        body: Plain text body
        html_body: Optional HTML body
        cc: Optional CC recipients
        bcc: Optional BCC recipients
        attachments: Optional attachments
        reply_to: Optional reply-to address

    Returns:
        EmailResult with success status and message_id or error
    """
    try:
        # Build Gmail service
        service = build("gmail", "v1", credentials=config.credentials)

        # Build message (include Bcc header - Gmail API reads it then strips before delivery)
        msg = _build_mime_message(
            from_addr=config.email_address,
            to=to,
            subject=subject,
            body=body,
            html_body=html_body,
            cc=cc,
            bcc=bcc,
            attachments=attachments,
            reply_to=reply_to,
            include_bcc_header=True,
        )

        # Encode for Gmail API
        raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

        # Send
        result = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": raw_message})
            .execute()
        )

        message_id = result.get("id")
        logger.info(f"Email sent via Gmail API to {to}, message_id={message_id}")

        return EmailResult(success=True, message_id=message_id)

    except Exception as e:
        logger.exception(f"Error sending email via Gmail API: {e}")
        return EmailResult(success=False, error=str(e))


def send_email(
    config: SmtpConfig | GmailConfig,
    to: list[str],
    subject: str,
    body: str,
    html_body: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    attachments: list[EmailAttachmentData] | None = None,
    reply_to: str | None = None,
) -> EmailResult:
    """Send an email using the appropriate method based on config type.

    Args:
        config: SmtpConfig or GmailConfig with pre-loaded credentials
        to: List of recipient addresses
        subject: Email subject
        body: Plain text body
        html_body: Optional HTML body
        cc: Optional CC recipients
        bcc: Optional BCC recipients
        attachments: Optional attachments
        reply_to: Optional reply-to address

    Returns:
        EmailResult with success status and message_id or error
    """
    if isinstance(config, GmailConfig):
        return send_via_gmail_api(
            config=config,
            to=to,
            subject=subject,
            body=body,
            html_body=html_body,
            cc=cc,
            bcc=bcc,
            attachments=attachments,
            reply_to=reply_to,
        )
    return send_via_smtp(
        config=config,
        to=to,
        subject=subject,
        body=body,
        html_body=html_body,
        cc=cc,
        bcc=bcc,
        attachments=attachments,
        reply_to=reply_to,
    )


def prepare_send_config(
    session: DBSession, account: EmailAccount
) -> SmtpConfig | GmailConfig:
    """Prepare a send config from an EmailAccount, preloading all needed data.

    This should be called while the session is active. The returned config
    can safely be used in another thread.

    Args:
        session: Database session
        account: EmailAccount to prepare config for

    Returns:
        SmtpConfig or GmailConfig ready for sending

    Raises:
        ValueError: If Gmail account is not properly linked or lacks permissions
    """
    if account.account_type != "gmail":
        return SmtpConfig.from_account(account)

    if not account.google_account_id:
        raise ValueError("Gmail account not linked to a Google account")

    google_account = session.get(GoogleAccount, account.google_account_id)
    if not google_account:
        raise ValueError("Associated Google account not found")

    return GmailConfig.from_account(account, google_account, session)


def get_user_email_accounts(
    session: DBSession, user_id: int, send_enabled_only: bool = True
) -> list[EmailAccount]:
    """Get all email accounts for a user that can be used for sending.

    Args:
        session: Database session
        user_id: User ID
        send_enabled_only: Only return accounts with send_enabled=True

    Returns:
        List of EmailAccount instances
    """
    query = session.query(EmailAccount).filter(EmailAccount.user_id == user_id)
    if send_enabled_only:
        query = query.filter(EmailAccount.send_enabled.is_(True))
    return query.all()


def get_account_by_address(
    session: DBSession, user_id: int, email_address: str
) -> EmailAccount | None:
    """Get a specific email account by address for a user.

    Only returns accounts that have send_enabled=True.

    Args:
        session: Database session
        user_id: User ID
        email_address: Email address to look up

    Returns:
        EmailAccount if found and send-enabled, None otherwise
    """
    return (
        session.query(EmailAccount)
        .filter(
            EmailAccount.user_id == user_id,
            EmailAccount.email_address == email_address,
            EmailAccount.send_enabled.is_(True),
        )
        .first()
    )
