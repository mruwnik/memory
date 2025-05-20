import hashlib
import imaplib
import logging
import pathlib
import re
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from typing import Callable, Generator, Sequence, cast

from sqlalchemy.orm import Session, scoped_session

from memory.common import embedding, qdrant, settings
from memory.common.db.models import (
    EmailAccount,
    EmailAttachment,
    MailMessage,
    SourceItem,
)
from memory.common.parsers.email import (
    Attachment,
    RawEmailResponse,
    parse_email_message,
)

logger = logging.getLogger(__name__)


def process_attachment(
    attachment: Attachment, message: MailMessage
) -> EmailAttachment | None:
    """Process an attachment, storing large files on disk and returning metadata.

    Args:
        attachment: Attachment dictionary with metadata and content
        message_id: Email message ID to use in file path generation

    Returns:
        Processed attachment dictionary with appropriate metadata
    """
    content, file_path = None, None
    if not (real_content := attachment.get("content")):
        "No content, so just save the metadata"
    elif attachment["size"] <= settings.MAX_INLINE_ATTACHMENT_SIZE and attachment[
        "content_type"
    ].startswith("text/"):
        content = real_content.decode("utf-8", errors="replace")
    else:
        file_path = message.safe_filename(attachment["filename"])
        try:
            file_path.write_bytes(real_content)
        except Exception as e:
            logger.error(f"Failed to save attachment {file_path} to disk: {str(e)}")
            return None

    return EmailAttachment(
        modality=embedding.get_modality(attachment["content_type"]),
        sha256=hashlib.sha256(
            real_content if real_content else str(attachment).encode()
        ).digest(),
        tags=message.tags,
        size=attachment["size"],
        mime_type=attachment["content_type"],
        mail_message=message,
        content=content,
        filename=file_path and str(file_path),
    )


def process_attachments(
    attachments: list[Attachment], message: MailMessage
) -> list[EmailAttachment]:
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
        for a in attachments
        if (attachment := process_attachment(a, message))
    ]


def create_mail_message(
    db_session: Session | scoped_session,
    tags: list[str],
    folder: str,
    raw_email: str,
    message_id: str,
) -> MailMessage:
    """
    Create a new mail message record and associated attachments.

    Args:
        db_session: Database session
        source_id: ID of the SourceItem
        parsed_email: Parsed email data
        folder: IMAP folder name

    Returns:
        Newly created MailMessage
    """
    parsed_email = parse_email_message(raw_email, message_id)
    mail_message = MailMessage(
        modality="mail",
        sha256=parsed_email["hash"],
        tags=tags,
        size=len(raw_email),
        mime_type="message/rfc822",
        embed_status="RAW",
        message_id=parsed_email["message_id"],
        subject=parsed_email["subject"],
        sender=parsed_email["sender"],
        recipients=parsed_email["recipients"],
        sent_at=parsed_email["sent_at"],
        content=raw_email,
        folder=folder,
    )

    db_session.add(mail_message)

    if parsed_email["attachments"]:
        attachments = process_attachments(parsed_email["attachments"], mail_message)
        db_session.add_all(attachments)
        mail_message.attachments = attachments

    db_session.add(mail_message)
    return mail_message


def does_message_exist(
    db_session: Session | scoped_session, message_id: str, message_hash: bytes
) -> bool:
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
        mail_message = (
            db_session.query(MailMessage)
            .filter(MailMessage.message_id == message_id)
            .first()
        )
        if mail_message is not None:
            return True

    # Then check by message_hash
    source_item = (
        db_session.query(SourceItem).filter(SourceItem.sha256 == message_hash).first()
    )
    return source_item is not None


def check_message_exists(
    db: Session | scoped_session, account_id: int, message_id: str, raw_email: str
) -> bool:
    account = db.query(EmailAccount).get(account_id)
    if not account:
        logger.error(f"Account {account_id} not found")
        return False

    parsed_email = parse_email_message(raw_email, message_id)
    if "szczepalins" in raw_email.lower():
        print(parsed_email["message_id"])

    return does_message_exist(db, parsed_email["message_id"], parsed_email["hash"])


def extract_email_uid(
    msg_data: Sequence[tuple[bytes, bytes]],
) -> tuple[str | None, bytes]:
    """
    Extract the UID and raw email data from the message data.
    """
    uid_pattern = re.compile(r"UID (\d+)")
    uid_match = uid_pattern.search(msg_data[0][0].decode("utf-8", errors="replace"))
    uid = uid_match.group(1) if uid_match else None
    raw_email = msg_data[0][1]
    return uid, raw_email


def fetch_email(conn: imaplib.IMAP4_SSL, uid: str) -> RawEmailResponse | None:
    try:
        status, msg_data = conn.fetch(uid, "(UID RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            logger.error(f"Error fetching message {uid}")
            return None

        return extract_email_uid(msg_data)  # type: ignore
    except Exception as e:
        logger.error(f"Error processing message {uid}: {str(e)}")
        return None


def fetch_email_since(
    conn: imaplib.IMAP4_SSL,
    folder: str,
    since_date: datetime = datetime(1970, 1, 1),
) -> list[RawEmailResponse]:
    """
    Fetch emails from a folder since a given date and time.

    Args:
        conn: IMAP connection
        folder: Folder name to select
        since_date: Fetch emails since this date and time

    Returns:
        List of tuples with (uid, raw_email)
    """
    try:
        status, counts = conn.select(folder)
        if status != "OK":
            logger.error(f"Error selecting folder {folder}: {counts}")
            return []

        date_str = since_date.strftime("%d-%b-%Y")

        status, data = conn.search(None, f'(SINCE "{date_str}")')
        if status != "OK":
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
    processor: Callable[[int, str, str, str], int | None],
) -> dict:
    """
    Process a single folder from an email account.

    Args:
        conn: Active IMAP connection
        folder: Folder name to process
        account: Email account configuration
        since_date: Only fetch messages newer than this date
        processor: Function to process each message

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
                    account_id=account.id,  # type: ignore
                    message_id=uid,
                    folder=folder,
                    raw_email=raw_email.decode("utf-8", errors="replace"),
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
        "errors": errors,
    }


@contextmanager
def imap_connection(account: EmailAccount) -> Generator[imaplib.IMAP4_SSL, None, None]:
    conn = imaplib.IMAP4_SSL(
        host=cast(str, account.imap_server), port=cast(int, account.imap_port)
    )
    try:
        conn.login(cast(str, account.username), cast(str, account.password))
        yield conn
    finally:
        # Always try to logout and close the connection
        try:
            conn.logout()
        except Exception as e:
            logger.error(f"Error logging out from {account.imap_server}: {str(e)}")


def vectorize_email(email: MailMessage):
    qdrant_client = qdrant.get_qdrant_client()

    _, chunks = embedding.embed(
        "text/plain",
        email.body,
        metadata=email.as_payload(),
    )
    email.chunks = chunks
    if chunks:
        vector_ids = [cast(str, c.id) for c in chunks]
        vectors = [c.vector for c in chunks]
        metadata = [c.item_metadata for c in chunks]
        qdrant.upsert_vectors(
            client=qdrant_client,
            collection_name="mail",
            ids=vector_ids,
            vectors=vectors,  # type: ignore
            payloads=metadata,  # type: ignore
        )

    embeds = defaultdict(list)
    for attachment in email.attachments:
        if attachment.filename:
            content = pathlib.Path(attachment.filename).read_bytes()
        else:
            content = attachment.content
        collection, chunks = embedding.embed(
            attachment.mime_type, content, metadata=attachment.as_payload()
        )
        if not chunks:
            continue

        attachment.chunks = chunks
        embeds[collection].extend(chunks)

    for collection, chunks in embeds.items():
        ids = [c.id for c in chunks]
        vectors = [c.vector for c in chunks]
        metadata = [c.item_metadata for c in chunks]
        qdrant.upsert_vectors(
            client=qdrant_client,
            collection_name=collection,
            ids=ids,
            vectors=vectors,
            payloads=metadata,
        )

    email.embed_status = "STORED"  # type: ignore
    for attachment in email.attachments:
        attachment.embed_status = "STORED"

    logger.info(f"Stored embedding for message {email.message_id}")
