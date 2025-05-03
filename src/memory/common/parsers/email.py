import email
import hashlib
import logging
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import TypedDict, Literal
import pathlib

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


def parse_email_message(raw_email: str, message_id: str) -> EmailMessage:
    """
    Parse raw email into structured data.
    
    Args:
        raw_email: Raw email content as string
        
    Returns:
        Dict with parsed email data
    """
    msg = email.message_from_string(raw_email)
    message_id = msg.get("Message-ID") or f"generated-{message_id}"
    subject = msg.get("Subject", "")
    from_ = msg.get("From", "")
    body = extract_body(msg)
    
    return EmailMessage(
        message_id=message_id,
        subject=subject,
        sender=from_,
        recipients=extract_recipients(msg),
        sent_at=extract_date(msg),
        body=body,
        attachments=extract_attachments(msg),
        hash=compute_message_hash(message_id, subject, from_, body)
    )
