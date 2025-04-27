import email
import hashlib
import imaplib
import logging
import re
import uuid
import base64
from contextlib import contextmanager
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Generator, Callable, TypedDict, Literal
import pathlib
from sqlalchemy.orm import Session

from memory.common.db.models import EmailAccount, MailMessage, SourceItem
from memory.common import settings

logger = logging.getLogger(__name__)


class Attachment(TypedDict):
    filename: str
    content_type: str
    size: int
    content: bytes
    path: pathlib.Path


class EmailMessage(TypedDict):
    message_id: str
    subject: str
    sender: str
    recipients: list[str]
    sent_at: datetime | None
    body: str
    attachments: list[Attachment]


RawEmailResponse = tuple[Literal["OK", "ERROR"], bytes]


def extract_recipients(msg: email.message.Message) -> list[str]:
    """
    Extract email recipients from message headers.
    
    Args:
        msg: Email message object
        
    Returns:
        List of recipient email addresses
    """
    return [
        recipient
        for field in ["To", "Cc", "Bcc"]
        if (field_value := msg.get(field, ""))
        for r in field_value.split(",")
        if (recipient := r.strip())
    ]


def extract_date(msg: email.message.Message) -> datetime | None:
    """
    Parse date from email header.
    
    Args:
        msg: Email message object
        
    Returns:
        Parsed datetime or None if parsing failed
    """
    if date_str := msg.get("Date"):
        try:
            return parsedate_to_datetime(date_str)
        except Exception:
            logger.warning(f"Could not parse date: {date_str}")
    return None


def extract_body(msg: email.message.Message) -> str:
    """
    Extract plain text body from email message.
    
    Args:
        msg: Email message object
        
    Returns:
        Plain text body content
    """
    body = ""
    
    if not msg.is_multipart():
        try:
            return msg.get_payload(decode=True).decode(errors='replace')
        except Exception as e:
            logger.error(f"Error decoding message body: {str(e)}")
            return ""

    for part in msg.walk():
        content_type = part.get_content_type()
        content_disposition = str(part.get("Content-Disposition", ""))
        
        if content_type == "text/plain" and "attachment" not in content_disposition:
            try:
                body += part.get_payload(decode=True).decode(errors='replace') + "\n"
            except Exception as e:
                logger.error(f"Error decoding message part: {str(e)}")
    return body


def extract_attachments(msg: email.message.Message) -> list[Attachment]:
    """
    Extract attachment metadata and content from email.
    
    Args:
        msg: Email message object
        
    Returns:
        List of attachment dictionaries with metadata and content
    """
    if not msg.is_multipart():
        return []

    attachments = []
    for part in msg.walk():
        content_disposition = part.get("Content-Disposition", "")
        if "attachment" not in content_disposition:
            continue

        if filename := part.get_filename():
            try:
                content = part.get_payload(decode=True)
                attachments.append({
                    "filename": filename,
                    "content_type": part.get_content_type(),
                    "size": len(content),
                    "content": content
                })
            except Exception as e:
                logger.error(f"Error extracting attachment content for {filename}: {str(e)}")

    return attachments


def process_attachment(attachment: Attachment, message_id: str) -> Attachment | None:
    """Process an attachment, storing large files on disk and returning metadata.
    
    Args:
        attachment: Attachment dictionary with metadata and content
        message_id: Email message ID to use in file path generation
        
    Returns:
        Processed attachment dictionary with appropriate metadata
    """
    if not (content := attachment.get("content")):
        return attachment

    if attachment["size"] <= settings.MAX_INLINE_ATTACHMENT_SIZE:
        return {**attachment, "content": base64.b64encode(content).decode('utf-8')}

    safe_message_id = re.sub(r'[<>\s:/\\]', '_', message_id)
    unique_id = str(uuid.uuid4())[:8]
    safe_filename = re.sub(r'[/\\]', '_', attachment["filename"])
    
    # Create user subdirectory
    user_dir = settings.FILE_STORAGE_DIR / safe_message_id
    user_dir.mkdir(parents=True, exist_ok=True)
    
    # Final path for the attachment
    file_path = user_dir / f"{unique_id}_{safe_filename}"
    
    # Write the file
    try:
        file_path.write_bytes(content)
        return {**attachment, "path": file_path}
    except Exception as e:
        logger.error(f"Failed to save attachment {safe_filename} to disk: {str(e)}")
    return None


def process_attachments(attachments: list[Attachment], message_id: str) -> list[Attachment]:
    """
    Process email attachments, storing large files on disk and returning metadata.
    
    Args:
        attachments: List of attachment dictionaries with metadata and content
        message_id: Email message ID to use in file path generation
        
    Returns:
        List of processed attachment dictionaries with appropriate metadata
    """
    if not attachments:
        return []

    return [
        attachment
        for a in attachments if (attachment := process_attachment(a, message_id))
    ]


def compute_message_hash(msg_id: str, subject: str, sender: str, body: str) -> bytes:
    """
    Compute a SHA-256 hash of message content.
    
    Args:
        msg_id: Message ID
        subject: Email subject
        sender: Sender email
        body: Message body
        
    Returns:
        SHA-256 hash as bytes
    """
    hash_content = (msg_id + subject + sender + body).encode()
    return hashlib.sha256(hash_content).digest()


def parse_email_message(raw_email: str) -> EmailMessage:
    """
    Parse raw email into structured data.
    
    Args:
        raw_email: Raw email content as string
        
    Returns:
        Dict with parsed email data
    """
    msg = email.message_from_string(raw_email)
    
    return EmailMessage(
        message_id=msg.get("Message-ID", ""),
        subject=msg.get("Subject", ""),
        sender=msg.get("From", ""),
        recipients=extract_recipients(msg),
        sent_at=extract_date(msg),
        body=extract_body(msg),
        attachments=extract_attachments(msg)
    )


def create_source_item(
    db_session: Session,
    message_hash: bytes,
    account_tags: list[str],
    raw_email_size: int,
) -> SourceItem:
    """
    Create a new source item record.
    
    Args:
        db_session: Database session
        message_hash: SHA-256 hash of message
        account_tags: Tags from the email account
        raw_email_size: Size of raw email in bytes
        
    Returns:
        Newly created SourceItem
    """
    source_item = SourceItem(
        modality="mail",
        sha256=message_hash,
        tags=account_tags,
        byte_length=raw_email_size,
        mime_type="message/rfc822",
        embed_status="RAW"
    )
    db_session.add(source_item)
    db_session.flush()
    return source_item


def create_mail_message(
    db_session: Session,
    source_id: int,
    parsed_email: EmailMessage,
    folder: str,
) -> MailMessage:
    """
    Create a new mail message record.
    
    Args:
        db_session: Database session
        source_id: ID of the SourceItem
        parsed_email: Parsed email data
        folder: IMAP folder name
        
    Returns:
        Newly created MailMessage
    """
    processed_attachments = process_attachments(
        parsed_email["attachments"], 
        parsed_email["message_id"]
    )
    print("processed_attachments", processed_attachments)
    
    mail_message = MailMessage(
        source_id=source_id,
        message_id=parsed_email["message_id"],
        subject=parsed_email["subject"],
        sender=parsed_email["sender"],
        recipients=parsed_email["recipients"],
        sent_at=parsed_email["sent_at"],
        body_raw=parsed_email["body"],
        attachments={"items": processed_attachments, "folder": folder}
    )
    db_session.add(mail_message)
    return mail_message


def check_message_exists(db_session: Session, message_id: str, message_hash: bytes) -> bool:
    """
    Check if a message already exists in the database.
    
    Args:
        db_session: Database session
        message_id: Email message ID
        message_hash: SHA-256 hash of message
        
    Returns:
        True if message exists, False otherwise
    """
    # Check by message_id first (faster)
    if message_id:
        mail_message = db_session.query(MailMessage).filter(MailMessage.message_id == message_id).first()
        if mail_message is not None:
            return True
    
    # Then check by message_hash
    source_item = db_session.query(SourceItem).filter(SourceItem.sha256 == message_hash).first()
    return source_item is not None


def extract_email_uid(msg_data: bytes) -> tuple[str, str]:
    """
    Extract the UID and raw email data from the message data.
    """
    uid_pattern = re.compile(r'UID (\d+)')
    uid_match = uid_pattern.search(msg_data[0][0].decode('utf-8', errors='replace'))
    uid = uid_match.group(1) if uid_match else None
    raw_email = msg_data[0][1]
    return uid, raw_email


def fetch_email(conn: imaplib.IMAP4_SSL, uid: str) -> RawEmailResponse | None:
    try:
        status, msg_data = conn.fetch(uid, '(UID RFC822)')
        if status != 'OK' or not msg_data or not msg_data[0]:
            logger.error(f"Error fetching message {uid}")
            return None
            
        return extract_email_uid(msg_data)
    except Exception as e:
        logger.error(f"Error processing message {uid}: {str(e)}")
        return None


def fetch_email_since(
    conn: imaplib.IMAP4_SSL,
    folder: str,
    since_date: datetime = datetime(1970, 1, 1)
) -> list[RawEmailResponse]:
    """
    Fetch emails from a folder since a given date.
    
    Args:
        conn: IMAP connection
        folder: Folder name to select
        since_date: Fetch emails since this date
        
    Returns:
        List of tuples with (uid, raw_email)
    """
    try:
        status, counts = conn.select(folder)
        if status != 'OK':
            logger.error(f"Error selecting folder {folder}: {counts}")
            return []
        
        date_str = since_date.strftime("%d-%b-%Y")
        
        status, data = conn.search(None, f'(SINCE "{date_str}")')
        if status != 'OK':
            logger.error(f"Error searching folder {folder}: {data}")
            return []
    except Exception as e:
        logger.error(f"Error in fetch_email_since for folder {folder}: {str(e)}")
        return []
        
    if not data or not data[0]:
        return []
    
    return [email for uid in data[0].split() if (email := fetch_email(conn, uid))]


def process_folder(
    conn: imaplib.IMAP4_SSL,
    folder: str,
    account: EmailAccount,
    since_date: datetime,
    processor: Callable[[int, str, str, bytes], int | None],
) -> dict:
    """
    Process a single folder from an email account.
    
    Args:
        conn: Active IMAP connection
        folder: Folder name to process
        account: Email account configuration
        since_date: Only fetch messages newer than this date
        
    Returns:
        Stats dictionary for the folder
    """
    new_messages, errors = 0, 0
    emails = []

    try:
        emails = fetch_email_since(conn, folder, since_date)
        
        for uid, raw_email in emails:
            try:
                task = processor(
                    account_id=account.id,
                    message_id=uid,
                    folder=folder,
                    raw_email=raw_email.decode('utf-8', errors='replace')
                )
                if task:
                    new_messages += 1
            except Exception as e:
                logger.error(f"Error queuing message {uid}: {str(e)}")
                errors += 1
                
    except Exception as e:
        logger.error(f"Error processing folder {folder}: {str(e)}")
        errors += 1

    return {
        "messages_found": len(emails),
        "new_messages": new_messages,
        "errors": errors
    }


@contextmanager
def imap_connection(account: EmailAccount) -> Generator[imaplib.IMAP4_SSL, None, None]:
    conn = imaplib.IMAP4_SSL(
        host=account.imap_server,
        port=account.imap_port
    )
    try:
        conn.login(account.username, account.password)
        yield conn
    finally:
        # Always try to logout and close the connection
        try:
            conn.logout()
        except Exception as e:
            logger.error(f"Error logging out from {account.imap_server}: {str(e)}")
