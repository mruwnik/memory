import email
import hashlib
import imaplib
import logging
import re
from contextlib import contextmanager
from datetime import datetime
from email.utils import parsedate_to_datetime

from sqlalchemy.orm import Session

from memory.common.db.models import EmailAccount, MailMessage, SourceItem


logger = logging.getLogger(__name__)


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


def extract_attachments(msg: email.message.Message) -> list[dict]:
    """
    Extract attachment metadata from email.
    
    Args:
        msg: Email message object
        
    Returns:
        List of attachment metadata dicts
    """
    if not msg.is_multipart():
        return []

    attachments = []
    for part in msg.walk():
        content_disposition = part.get("Content-Disposition", "")
        if "attachment" not in content_disposition:
            continue

        if filename := part.get_filename():
            attachments.append({
                "filename": filename,
                "content_type": part.get_content_type(),
                "size": len(part.get_payload(decode=True))
            })

    return attachments


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


def parse_email_message(raw_email: str) -> dict:
    """
    Parse raw email into structured data.
    
    Args:
        raw_email: Raw email content as string
        
    Returns:
        Dict with parsed email data
    """
    msg = email.message_from_string(raw_email)
    
    return {
        "message_id": msg.get("Message-ID", ""),
        "subject": msg.get("Subject", ""),
        "sender": msg.get("From", ""),
        "recipients": extract_recipients(msg),
        "sent_at": extract_date(msg),
        "body": extract_body(msg),
        "attachments": extract_attachments(msg)
    }


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
    parsed_email: dict,
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
    mail_message = MailMessage(
        source_id=source_id,
        message_id=parsed_email["message_id"],
        subject=parsed_email["subject"],
        sender=parsed_email["sender"],
        recipients=parsed_email["recipients"],
        sent_at=parsed_email["sent_at"],
        body_raw=parsed_email["body"],
        attachments={"items": parsed_email["attachments"], "folder": folder}
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
    return (
        # Check by message_id first (faster)
        message_id and db_session.query(MailMessage).filter(MailMessage.message_id == message_id).first()
        # Then check by message_hash
        or db_session.query(SourceItem).filter(SourceItem.sha256 == message_hash).first() is not None
    )


def extract_email_uid(msg_data: bytes) -> tuple[str, str]:
    """
    Extract the UID and raw email data from the message data.
    """
    uid_pattern = re.compile(r'UID (\d+)')
    uid_match = uid_pattern.search(msg_data[0][0].decode('utf-8', errors='replace'))
    uid = uid_match.group(1) if uid_match else None
    raw_email = msg_data[0][1]
    return uid, raw_email


def fetch_email(conn: imaplib.IMAP4_SSL, uid: str) -> tuple[str, bytes] | None:
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
    since_date: datetime
) -> list[tuple[str, bytes]]:
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
    since_date: datetime
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

    try:
        emails = fetch_email_since(conn, folder, since_date)
        
        for uid, raw_email in emails:
            try:
                task = process_message.delay(
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
def imap_connection(account: EmailAccount) -> imaplib.IMAP4_SSL:
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
