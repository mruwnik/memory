"""MCP subserver for sending emails."""

import asyncio
import logging
import mimetypes
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token

from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common import settings
from memory.common.scopes import SCOPE_EMAIL_WRITE
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import UserSession
from memory.common.email_sender import (
    EmailAttachmentData,
    get_account_by_address,
    get_user_email_accounts,
    prepare_send_config,
    send_email,
)

logger = logging.getLogger(__name__)

email_mcp = FastMCP("memory-email")


async def has_send_accounts(user_info: dict, session: DBSession | None) -> bool:
    """Visibility checker: only show email tools if user has send-enabled accounts."""
    token = user_info.get("token")
    if not token:
        return False

    def _check() -> bool:
        # Create our own session to avoid threading issues with passed session
        with make_session() as local_session:
            user_session = local_session.get(UserSession, token)
            if not user_session or not user_session.user:
                return False
            accounts = get_user_email_accounts(
                local_session, user_session.user.id, send_enabled_only=True
            )
            return len(accounts) > 0

    return await asyncio.to_thread(_check)


def _get_user_id(session: DBSession) -> int:
    """Get the current user ID from the access token or raise ValueError."""
    access_token = get_access_token()
    if not access_token:
        raise ValueError("Not authenticated")

    user_session = session.get(UserSession, access_token.token)
    if not user_session or not user_session.user:
        raise ValueError("User not found")

    return user_session.user.id


def _load_attachment(path: str) -> EmailAttachmentData | None:
    """Load an attachment from the file storage directory.

    Only allows files within FILE_STORAGE_DIR for security.
    """
    try:
        file_path = Path(settings.FILE_STORAGE_DIR) / path
        # Security: ensure path is within storage dir
        file_path = file_path.resolve()
        storage_dir = Path(settings.FILE_STORAGE_DIR).resolve()

        try:
            file_path.relative_to(storage_dir)
        except ValueError:
            logger.warning(f"Attempted path traversal: {path}")
            return None

        if not file_path.exists():
            logger.warning(f"Attachment not found: {path}")
            return None

        content = file_path.read_bytes()

        # Guess content type from extension
        content_type, _ = mimetypes.guess_type(str(file_path))

        return EmailAttachmentData(
            filename=file_path.name,
            content=content,
            content_type=content_type,
        )
    except Exception as e:
        logger.error(f"Failed to load attachment {path}: {e}")
        return None


@email_mcp.tool()
@visible_when(require_scopes(SCOPE_EMAIL_WRITE), has_send_accounts)
async def send(
    to: list[str],
    subject: str,
    body: str,
    from_address: str,
    html_body: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: str | None = None,
    attachment_paths: list[str] | None = None,
) -> dict[str, Any]:
    """
    Send an email from one of the user's configured email accounts.

    The from_address must match an active email account configured for the user.
    For Gmail accounts, the account must have the gmail.send OAuth scope.
    For IMAP accounts, SMTP credentials will be used (inferred from IMAP if not explicit).

    Args:
        to: List of recipient email addresses
        subject: Email subject line
        body: Plain text email body
        from_address: Sender email address (must match a configured account for the current user)
        html_body: Optional HTML version of the body
        cc: Optional list of CC recipients
        bcc: Optional list of BCC recipients
        reply_to: Optional reply-to address
        attachment_paths: Optional list of file paths (relative to storage dir) to attach

    Returns:
        Dict with success status, message_id on success, or error message on failure
    """
    logger.info(f"send_email_message called: to={to}, subject={subject[:50]}...")

    # Validate recipients
    if not to:
        raise ValueError("At least one recipient is required")

    with make_session() as session:
        user_id = _get_user_id(session)
        # Find the email account
        account = get_account_by_address(session, user_id, from_address)

        if not account:
            # List available accounts for helpful error
            available = get_user_email_accounts(session, user_id)
            available_addrs = [a.email_address for a in available]
            raise ValueError(
                f"No active email account found for '{from_address}'. "
                f"Available accounts: {available_addrs}"
            )

        # Prepare config while session is active (preloads all needed data)
        config = prepare_send_config(session, account)

    # Load attachments if provided
    attachments = None
    if attachment_paths:
        attachments = []
        for path in attachment_paths:
            att = _load_attachment(path)
            if att:
                attachments.append(att)
            else:
                logger.warning(f"Skipping invalid attachment: {path}")

    # Send the email in a thread to avoid blocking the event loop
    result = await asyncio.to_thread(
        send_email,
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

    if result.success:
        return {
            "success": True,
            "message_id": result.message_id,
            "from": from_address,
            "to": to,
            "subject": subject,
        }
    return {
        "success": False,
        "error": result.error,
        "from": from_address,
        "to": to,
    }
