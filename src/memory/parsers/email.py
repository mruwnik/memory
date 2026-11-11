import email
import email.message
import email.policy
import hashlib
import logging
import pathlib
from datetime import datetime
from email.utils import getaddresses, parsedate_to_datetime
from typing import TypedDict, cast

from memory.common import settings
from memory.common.html_text import html_to_markdown

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
    hash: bytes
    raw_email: str


# (uid, raw email, when the mail server received it)
RawEmailResponse = tuple[str | None, bytes, datetime | None]


def message_ref(msg: email.message.Message) -> str:  # type: ignore
    """The message's Message-ID, shortened, for log lines."""
    return repr(str(msg.get("Message-ID") or "")[:100])


def kept(items: list, limit: int, kind: str, msg: email.message.Message) -> list:  # type: ignore
    """The first ``limit`` items, logging when some are dropped."""
    if len(items) > limit:
        logger.warning(
            "Mail %s: keeping %d of its %d %s", message_ref(msg), limit, len(items), kind
        )
    return items[:limit]


def extract_recipients(msg: email.message.Message) -> list[str]:  # type: ignore
    """
    Extract email recipient addresses from message headers.

    Uses ``email.utils.getaddresses`` for RFC 5322-correct parsing: a naive
    ``split(",")`` corrupts addresses whose display name contains a comma
    (``"Doe, John" <j@x>`` becomes two bogus recipients). Returns the bare
    address (``j@x``) so a mailbox is stored under one canonical form rather
    than smeared across display-name variants. At most MAX_EMAIL_RECIPIENTS
    are returned, as each one is looked up among the people.

    Args:
        msg: Email message object

    Returns:
        List of recipient email addresses
    """
    headers = [value for field in ["To", "Cc", "Bcc"] if (value := msg.get(field))]
    addresses = [addr for _name, addr in getaddresses(headers) if addr]
    return kept(addresses, settings.MAX_EMAIL_RECIPIENTS, "recipients", msg)


def header_addresses(value: str) -> list[str]:
    return [addr.lower() for _name, addr in getaddresses([value]) if "@" in addr]


def decode_mime_header(name: str, value: str) -> str:
    """Header text with MIME encoded-words decoded; address headers come back
    re-quoted (``"Doe, John" <j@x>``) so they still parse as addresses. A value
    that doesn't decode cleanly is kept raw rather than lost. Only the first
    MAX_EMAIL_HEADER_CHARS characters are kept, as decoding is superlinear."""
    value = value[: settings.MAX_EMAIL_HEADER_CHARS]
    try:
        decoded = str(email.policy.default.header_fetch_parse(name, value))
    # The stdlib header parser has crash bugs on malformed input (AttributeError on "a@[1.2.3").
    except Exception:
        return value
    if not decoded:
        return value
    if name.lower() != "from":
        return decoded
    # The parser can silently drop a mailbox it can't make sense of ("@" -> "<>").
    if header_addresses(decoded) != header_addresses(value) or decoded.count("@") < value.count("@"):
        return value
    return decoded


def extract_date(msg: email.message.Message) -> datetime | None:  # type: ignore
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


def extract_body(msg: email.message.Message) -> str:  # type: ignore
    """
    Extract body from email message, preferring HTML converted to markdown.

    Args:
        msg: Email message object

    Returns:
        Body content as markdown (if HTML found) or plain text
    """
    html_body = ""
    plain_body = ""

    if not msg.is_multipart():
        try:
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                content = payload.decode(errors="replace")
            else:
                content = str(payload)
            content_type = msg.get_content_type()
            if content_type == "text/html":
                return html_to_markdown(content)
            else:
                return content
        except Exception:
            logger.exception("Error decoding message body")
            return ""

    # Extract both HTML and plain text parts
    for part in msg.walk():
        content_type = part.get_content_type()
        content_disposition = str(part.get("Content-Disposition", ""))

        if "attachment" in content_disposition or content_type not in (
            "text/plain",
            "text/html",
        ):
            continue

        try:
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                content = payload.decode(errors="replace")
            else:
                content = str(payload)

            if content_type == "text/html":
                html_body += content + "\n"
            else:
                plain_body += content + "\n"
        except Exception:
            logger.exception("Error decoding message part")

    # Prefer HTML (converted to markdown) over plain text
    if html_body.strip():
        return html_to_markdown(html_body)
    else:
        return plain_body.strip()


def attachment_parts(msg: email.message.Message) -> list[email.message.Message]:  # type: ignore
    """The parts of a multipart message that are named attachments."""
    if not msg.is_multipart():
        return []
    return [
        part
        for part in msg.walk()
        if "attachment" in part.get("Content-Disposition", "") and part.get_filename()
    ]


def decode_attachment(part: email.message.Message) -> Attachment | None:  # type: ignore
    """An attachment part's metadata and decoded content; None when it has no content."""
    filename = part.get_filename()
    try:
        content = part.get_payload(decode=True)
    except Exception:
        logger.exception("Error extracting attachment content")
        return None
    if not isinstance(content, bytes):
        logger.warning(f"Attachment {filename} has no content, skipping")
        return None
    return cast(
        Attachment,
        {
            "filename": filename,
            "content_type": part.get_content_type(),
            "size": len(content),
            "content": content,
        },
    )


def extract_attachments(msg: email.message.Message) -> list[Attachment]:  # type: ignore
    """
    Extract attachment metadata and content from email. Only the first
    MAX_EMAIL_ATTACHMENTS are decoded, since each becomes its own item.

    Args:
        msg: Email message object

    Returns:
        List of attachment dictionaries with metadata and content
    """
    parts = kept(attachment_parts(msg), settings.MAX_EMAIL_ATTACHMENTS, "attachments", msg)
    return [attachment for part in parts if (attachment := decode_attachment(part))]


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


def generated_message_id(raw_email: str) -> str:
    """An id for mail without a Message-ID header, derived from its content so
    different messages never share one and refetching a message reuses it."""
    return f"generated-{hashlib.sha256(raw_email.encode()).hexdigest()}"


def parse_email_message(raw_email: str, with_attachments: bool = True) -> EmailMessage:
    """
    Parse raw email into structured data.

    Args:
        raw_email: Raw email content as string
        with_attachments: Decode attachment bytes; readers of the body alone
            skip it, since a large attachment costs its full size again in RAM.

    Returns:
        Dict with parsed email data
    """
    msg = email.message_from_string(raw_email)
    message_id = msg.get("Message-ID") or generated_message_id(raw_email)
    subject = msg.get("Subject", "")
    from_ = msg.get("From", "")
    body = extract_body(msg)

    return EmailMessage(
        raw_email=raw_email,
        message_id=message_id,
        subject=decode_mime_header("Subject", subject),
        sender=decode_mime_header("From", from_),
        recipients=extract_recipients(msg),
        sent_at=extract_date(msg),
        body=body,
        attachments=extract_attachments(msg) if with_attachments else [],
        hash=compute_message_hash(message_id, subject, from_, body),
    )
