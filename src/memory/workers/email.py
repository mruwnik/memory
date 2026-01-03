import hashlib
import imaplib
import logging
import re
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from typing import Callable, Generator, Sequence, cast

from sqlalchemy.orm import Session, scoped_session

from memory.common import embedding, qdrant, settings, collections
from memory.common.db.models import (
    EmailAccount,
    EmailAttachment,
    MailMessage,
)
from memory.parsers.email import (
    Attachment,
    EmailMessage,
    RawEmailResponse,
)

logger = logging.getLogger(__name__)


def process_attachment(
    attachment: Attachment, message: MailMessage
) -> EmailAttachment | None:
    """Process an attachment, storing large files on disk and returning metadata.

    Args:
        attachment: Attachment dictionary with metadata and content
        message: MailMessage instance to use for file path generation

    Returns:
        Processed attachment dictionary with appropriate metadata
    """
    content, file_path = None, None
    if not (real_content := attachment.get("content")):
        pass  # No content, so just save the metadata
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
        modality=collections.get_modality(attachment["content_type"]),
        sha256=hashlib.sha256(
            real_content if real_content else str(attachment).encode()
        ).digest(),
        tags=message.tags,
        size=attachment["size"],
        mime_type=attachment["content_type"],
        mail_message=message,
        content=content,
        filename=file_path and str(file_path.relative_to(settings.FILE_STORAGE_DIR)),
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
    parsed_email: EmailMessage,
    email_account_id: int | None = None,
    imap_uid: str | None = None,
) -> MailMessage:
    """
    Create a new mail message record and associated attachments.

    Args:
        db_session: Database session
        tags: Tags to apply to the message
        folder: IMAP folder name
        parsed_email: Parsed email data
        email_account_id: ID of the EmailAccount this message belongs to
        imap_uid: IMAP UID for deletion tracking

    Returns:
        Newly created MailMessage
    """
    raw_email = parsed_email["raw_email"]
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
        email_account_id=email_account_id,
        imap_uid=imap_uid,
    )

    db_session.add(mail_message)

    if parsed_email["attachments"]:
        attachments = process_attachments(parsed_email["attachments"], mail_message)
        db_session.add_all(attachments)
        mail_message.attachments = attachments

    return mail_message


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
        status, msg_data = conn.fetch(uid, "(UID BODY.PEEK[])")
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
            # If logout fails, explicitly close the socket to prevent resource leak
            try:
                conn.shutdown()
            except Exception:
                pass  # Socket may already be closed


def vectorize_email(email: MailMessage):
    qdrant_client = qdrant.get_qdrant_client()

    chunks = embedding.embed_source_item(email)
    email.chunks = chunks
    if chunks:
        vector_ids = [cast(str, c.id) for c in chunks]
        vectors = [c.vector for c in chunks]
        metadata = [c.item_metadata for c in chunks]
        qdrant.upsert_vectors(
            client=qdrant_client,
            collection_name=cast(str, email.modality),
            ids=vector_ids,
            vectors=vectors,  # type: ignore
            payloads=metadata,  # type: ignore
        )

    embeds = defaultdict(list)
    for attachment in email.attachments:
        chunks = embedding.embed_source_item(attachment)
        if not chunks:
            continue

        attachment.chunks = chunks
        embeds[attachment.modality].extend(chunks)

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


def get_folder_uids(conn: imaplib.IMAP4_SSL, folder: str) -> set[str]:
    """
    Get all message UIDs in a folder.

    Args:
        conn: IMAP connection
        folder: Folder name to get UIDs from

    Returns:
        Set of UID strings
    """
    try:
        status, _ = conn.select(folder)
        if status != "OK":
            logger.error(f"Error selecting folder {folder}")
            return set()

        status, data = conn.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            return set()

        return {uid.decode() for uid in data[0].split()}
    except Exception as e:
        logger.error(f"Error getting UIDs from folder {folder}: {str(e)}")
        return set()


def should_delete_email(email: MailMessage) -> bool:
    """
    Determine if an email should be deleted when it's no longer on the server.

    This function can be customized to preserve certain emails based on:
    - Sender (keep emails from important contacts)
    - Age (keep emails older than X days)
    - Attachments (keep emails with attachments)
    - Tags (keep emails with certain tags)
    - etc.

    Args:
        email: The MailMessage to check

    Returns:
        True if the email should be deleted, False to keep it
    """
    # For now, always delete. Customize this function as needed.
    return True


def delete_email_vectors(email: MailMessage) -> None:
    """
    Delete vectors for an email and its attachments from Qdrant.

    Args:
        email: The MailMessage whose vectors should be deleted
    """
    qdrant_client = qdrant.get_qdrant_client()

    # Delete email chunks
    if email.chunks:
        chunk_ids = [cast(str, c.id) for c in email.chunks]
        try:
            qdrant.delete_points(
                client=qdrant_client,
                collection_name=cast(str, email.modality),
                ids=chunk_ids,
            )
        except Exception as e:
            logger.warning(f"Error deleting email vectors: {e}")

    # Delete attachment chunks
    for attachment in email.attachments:
        if attachment.chunks:
            chunk_ids = [cast(str, c.id) for c in attachment.chunks]
            try:
                qdrant.delete_points(
                    client=qdrant_client,
                    collection_name=cast(str, attachment.modality),
                    ids=chunk_ids,
                )
            except Exception as e:
                logger.warning(f"Error deleting attachment vectors: {e}")


def delete_removed_emails(
    conn: imaplib.IMAP4_SSL,
    db_session: Session | scoped_session,
    account_id: int,
    folder: str,
) -> int:
    """
    Delete emails from the database that are no longer on the IMAP server.

    Compares UIDs on the server with UIDs in the database for the given
    account and folder, and deletes any emails that are no longer present
    on the server.

    Args:
        conn: IMAP connection
        db_session: Database session
        account_id: ID of the EmailAccount
        folder: IMAP folder to check

    Returns:
        Number of emails deleted
    """
    server_uids = get_folder_uids(conn, folder)
    if not server_uids:
        return 0

    # Get emails in DB that are no longer on the server
    db_emails = (
        db_session.query(MailMessage)
        .filter(
            MailMessage.email_account_id == account_id,
            MailMessage.folder == folder,
            MailMessage.imap_uid.isnot(None),
            MailMessage.imap_uid.notin_(server_uids),
        )
        .all()
    )

    deleted_count = 0
    for email in db_emails:
        if should_delete_email(email):
            logger.info(
                f"Deleting email {email.message_id} "
                f"(UID {email.imap_uid}) - no longer on server"
            )
            delete_email_vectors(email)
            db_session.delete(email)
            deleted_count += 1

    return deleted_count
