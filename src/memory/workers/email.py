import base64
import hashlib
import imaplib
import logging
import re
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Generator, Sequence, cast

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session, scoped_session

from memory.common import collections, embedding, paths, qdrant, settings
from memory.common.db.models import EmailAccount, EmailAttachment, MailMessage
from memory.common.people import link_people
from memory.parsers.email import Attachment, EmailMessage, RawEmailResponse
from memory.parsers.google_drive import refresh_credentials

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

    # Auto-add source and sender tags for filtering
    auto_tags = ["email"]
    if message.sender:
        auto_tags.append(cast(str, message.sender))

    return EmailAttachment(
        modality=collections.get_modality(attachment["content_type"]),
        sha256=hashlib.sha256(
            real_content if real_content else str(attachment).encode()
        ).digest(),
        tags=auto_tags + (message.tags or []),
        size=attachment["size"],
        mime_type=attachment["content_type"],
        mail_message=message,
        content=content,
        filename=file_path and paths.to_db_filename(file_path),
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

    # Link people from sender and recipients
    email_addresses = set(parsed_email["recipients"] or [])
    if parsed_email["sender"]:  # sender can be None
        email_addresses.add(parsed_email["sender"])
    link_people(db_session, mail_message, email_addresses)

    return mail_message


UID_PATTERN = re.compile(r"UID (\d+)")


def extract_email_uid(
    msg_data: Sequence[bytes | tuple[bytes, bytes]],
) -> tuple[str | None, bytes]:
    """
    Extract the UID and raw email data from a FETCH response.

    ``msg_data`` is what ``imaplib``'s ``uid("FETCH", ...)`` returns: a list whose
    literal/header bytes come back as bare ``bytes`` and whose body parts come
    back as ``(header, payload)`` tuples. RFC 3501 does not guarantee the server
    echoes data items in request order, so the ``UID`` token may land in any of
    the string/tuple-header parts; scan them all rather than only the first.
    """
    uid: str | None = None
    raw_email = b""
    for part in msg_data:
        header, payload = part if isinstance(part, tuple) else (part, None)
        if uid is None and (match := UID_PATTERN.search(_as_text(header))):
            uid = match.group(1)
        if payload is not None and not raw_email:
            raw_email = payload

    if uid is None:
        logger.warning(f"Could not extract UID from FETCH response: {msg_data!r}")
    return uid, raw_email


def _as_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def fetch_email(conn: imaplib.IMAP4, uid: str) -> RawEmailResponse | None:
    try:
        status, msg_data = conn.uid("FETCH", uid, "(UID BODY.PEEK[])")
        if status != "OK" or not msg_data or not msg_data[0]:
            logger.error(f"Error fetching message {uid!r}")
            return None

        return extract_email_uid(msg_data)
    except Exception as e:
        logger.error(f"Error processing message {uid!r}: {str(e)}")
        return None


def fetch_email_since(
    conn: imaplib.IMAP4,
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

        status, data = conn.uid("SEARCH", f'(SINCE "{date_str}")')
        if status != "OK":
            logger.error(f"Error searching folder {folder}: {data}")
            return []
    except Exception as e:
        logger.error(f"Error in fetch_email_since for folder {folder}: {str(e)}")
        return []

    if not data or not data[0]:
        return []

    return [
        email
        for raw_uid in data[0].split()
        if (email := fetch_email(conn, raw_uid.decode()))
    ]


def process_folder(
    conn: imaplib.IMAP4,
    folder: str,
    account: EmailAccount,
    since_date: datetime,
    processor: Callable[[int, str, str, str], bool],
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
def imap_connection(account: EmailAccount) -> Generator[imaplib.IMAP4, None, None]:
    host = cast(str, account.imap_server)
    port = cast(int, account.imap_port)
    # use_ssl defaults to True when unset — SSL (993) is the norm and the
    # historical behaviour. Only an explicit False selects plain IMAP (143).
    if account.use_ssl is False:
        conn: imaplib.IMAP4 = imaplib.IMAP4(host=host, port=port)
    else:
        conn = imaplib.IMAP4_SSL(host=host, port=port)
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
        vector_ids = [str(c.id) for c in chunks]
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


class FolderUidsError(Exception):
    """Raised when a folder's UIDs cannot be read (SELECT/SEARCH failed).

    Distinct from a legitimately-empty folder, which returns an empty set, so
    callers can avoid treating a transient read failure as "the folder is empty"
    (which would mass-delete the local copies).
    """


def get_folder_uids(conn: imaplib.IMAP4, folder: str) -> set[str]:
    """
    Get all message UIDs in a folder.

    Args:
        conn: IMAP connection
        folder: Folder name to get UIDs from

    Returns:
        Set of UID strings. Empty set means the folder is genuinely empty.

    Raises:
        FolderUidsError: if SELECT or SEARCH fails, or the connection errors.
            A read failure is signalled by raising rather than returning an
            empty set so callers don't confuse it with an empty folder.
    """
    try:
        status, _ = conn.select(folder)
        if status != "OK":
            raise FolderUidsError(f"Error selecting folder {folder}")

        status, data = conn.uid("SEARCH", "ALL")
        if status != "OK":
            raise FolderUidsError(f"Error searching folder {folder}")
    except FolderUidsError:
        raise
    except Exception as e:
        raise FolderUidsError(
            f"Error getting UIDs from folder {folder}: {str(e)}"
        ) from e

    if not data or not data[0]:
        return set()

    return {uid.decode() for uid in data[0].split()}


@dataclass
class ImapFolder:
    """A mailbox returned by an IMAP ``LIST``."""

    name: str
    flags: list[str]
    selectable: bool


def decode_imap_utf7(value: str) -> str:
    """Decode an IMAP modified-UTF-7 folder name (RFC 3501 §5.1.3).

    ``&`` shifts into modified BASE64 (``,`` for ``/``, UTF-16BE payload) until
    ``-``; ``&-`` is a literal ``&``. Falls back to the raw value if a shifted
    run is malformed, since a best-effort name is better than dropping a folder.
    """
    if "&" not in value:
        return value

    result: list[str] = []
    i = 0
    while i < len(value):
        char = value[i]
        if char != "&":
            result.append(char)
            i += 1
            continue
        end = value.find("-", i + 1)
        if end == -1:
            return value  # unterminated shift — give up, keep the raw name
        chunk = value[i + 1 : end]
        if chunk == "":
            result.append("&")
        else:
            b64 = chunk.replace(",", "/")
            b64 += "=" * (-len(b64) % 4)
            try:
                result.append(base64.b64decode(b64).decode("utf-16-be"))
            except Exception:
                return value
        i = end + 1
    return "".join(result)


# (flags) "<delimiter>" <name>  — delimiter may be NIL; name may be quoted.
LIST_RESPONSE_RE = re.compile(
    rb'^\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.+)$'
)


def parse_folder_line(line: bytes) -> ImapFolder | None:
    """Parse one IMAP ``LIST`` response line into an ``ImapFolder``.

    Returns ``None`` for lines that don't match (e.g. continuation noise), so
    callers can filter them out.
    """
    match = LIST_RESPONSE_RE.match(line.strip())
    if not match:
        return None

    flags = match.group("flags").decode("ascii", "replace").split()
    name = match.group("name").decode("utf-8", "replace").strip()
    if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
        # IMAP quoted-string: strip the surrounding quotes and unescape the only
        # two characters a quoted-string may backslash-escape (RFC 3501 §4.3):
        # \" and \\. Embedded escapes are rare but otherwise leak a literal
        # backslash into the folder name.
        name = re.sub(r"\\(.)", r"\1", name[1:-1])
    name = decode_imap_utf7(name)
    # RFC 3501 \Noselect and RFC 5258 \NonExistent both mark a mailbox that
    # can't be SELECTed. Attribute names are case-insensitive per spec, so
    # compare case-folded (\NOSELECT is valid too).
    non_selectable = {"\\noselect", "\\nonexistent"}
    selectable = not any(f.lower() in non_selectable for f in flags)
    return ImapFolder(name=name, flags=flags, selectable=selectable)


def list_imap_folders(conn: imaplib.IMAP4) -> list[ImapFolder]:
    """Return all mailboxes on the server, parsed from ``LIST``.

    Raises if the ``LIST`` command itself fails; individual unparseable lines
    are skipped rather than aborting the whole listing.
    """
    status, data = conn.list()
    if status != "OK":
        raise RuntimeError(f"IMAP LIST failed: {status}")
    folders = [
        folder
        for raw in (data or [])
        if isinstance(raw, (bytes, bytearray))
        and (folder := parse_folder_line(bytes(raw))) is not None
    ]
    return folders


def should_delete_email(_email: MailMessage) -> bool:
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


def extract_vector_deletion_info(email: MailMessage) -> dict:
    """Extract info needed for vector deletion before session detachment.

    Call this before commit to capture chunk IDs and modalities while the
    session is still active.
    """
    info = {
        "email_chunk_ids": [str(c.id) for c in email.chunks] if email.chunks else [],
        "email_modality": cast(str, email.modality),
        "attachments": [],
    }
    for attachment in email.attachments:
        if attachment.chunks:
            info["attachments"].append(
                {
                    "chunk_ids": [str(c.id) for c in attachment.chunks],
                    "modality": cast(str, attachment.modality),
                }
            )
    return info


def delete_email_vectors_from_info(info: dict) -> None:
    """Delete vectors using pre-extracted info (safe to call after session commit)."""
    qdrant_client = qdrant.get_qdrant_client()

    # Delete email chunks
    if info["email_chunk_ids"]:
        try:
            qdrant.delete_points(
                client=qdrant_client,
                collection_name=info["email_modality"],
                ids=info["email_chunk_ids"],
            )
        except Exception as e:
            logger.warning(f"Error deleting email vectors: {e}")

    # Delete attachment chunks
    for att_info in info["attachments"]:
        try:
            qdrant.delete_points(
                client=qdrant_client,
                collection_name=att_info["modality"],
                ids=att_info["chunk_ids"],
            )
        except Exception as e:
            logger.warning(f"Error deleting attachment vectors: {e}")


def delete_email_vectors(email: MailMessage) -> None:
    """
    Delete vectors for an email and its attachments from Qdrant.

    Args:
        email: The MailMessage whose vectors should be deleted

    Note: This requires the email to still be attached to a session.
    For post-commit deletion, use extract_vector_deletion_info() before commit
    and delete_email_vectors_from_info() after.
    """
    info = extract_vector_deletion_info(email)
    delete_email_vectors_from_info(info)


def delete_removed_emails(
    conn: imaplib.IMAP4,
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
    try:
        server_uids = get_folder_uids(conn, folder)
    except FolderUidsError as e:
        # Could not read the folder. Skip reconciliation rather than risk
        # mass-deleting local mail on a transient SELECT/SEARCH failure.
        logger.error(f"Skipping deletion for folder {folder}: {str(e)}")
        return 0

    # Intentional safety asymmetry: a genuinely-empty folder (empty set) also
    # returns 0 without deleting. find_removed_emails treats an empty
    # server_uids as "delete nothing" (it can't tell empty-because-erased from
    # empty-because-unreadable), so emptying a watched folder on the server
    # leaves the local copies in the DB rather than wiping them.
    emails_to_delete = find_removed_emails(db_session, account_id, server_uids, folder)
    return delete_emails(emails_to_delete, db_session)


# -----------------------------------------------------------------------------
# Gmail API functions
# -----------------------------------------------------------------------------

# Standard Gmail labels that match by name
STANDARD_GMAIL_LABELS = {
    "INBOX",
    "SENT",
    "DRAFT",
    "SPAM",
    "TRASH",
    "STARRED",
    "IMPORTANT",
}


def get_gmail_service(
    account: EmailAccount,
    session: Session | scoped_session,
):
    """
    Get an authenticated Gmail API service for an account.

    Args:
        account: EmailAccount with linked GoogleAccount
        session: Database session for credential refresh

    Returns:
        Gmail API service object
    """
    google_account = account.google_account
    if not google_account:
        raise ValueError("Gmail account requires linked GoogleAccount")

    credentials = refresh_credentials(google_account, session)
    return build("gmail", "v1", credentials=credentials)


def get_gmail_label_ids(service, folder_names: list[str]) -> list[str]:
    """
    Map folder names to Gmail label IDs.

    Gmail uses label IDs internally. Common mappings:
    - INBOX -> INBOX
    - Sent -> SENT
    - Archive -> (no label, or custom)
    """
    requested = {folder.upper() for folder in folder_names}

    # Standard labels that map directly
    label_ids = requested & STANDARD_GMAIL_LABELS

    # Handle "Sent" folder mapping to "SENT" label
    if "SENT" in requested or any(f.lower() == "sent" for f in folder_names):
        label_ids.add("SENT")

    # Custom folders need API lookup
    custom_folders = (
        {f.lower() for f in folder_names}
        - {s.lower() for s in STANDARD_GMAIL_LABELS}
        - {"sent"}
    )

    if not custom_folders:
        return list(label_ids)

    # Fetch labels from API for custom folders
    try:
        labels_response = service.users().labels().list(userId="me").execute()
        api_labels = {
            label["name"].lower(): label["id"]
            for label in labels_response.get("labels", [])
        }
        return list(label_ids) + [
            label for folder in custom_folders if (label := api_labels.get(folder))
        ]
    except Exception as e:
        logger.warning(f"Error fetching labels: {e}")

    return list(label_ids)


def fetch_gmail_message(
    service, message_id: str, format: str = "raw"
) -> str | dict | None:
    """
    Fetch a single Gmail message.

    Args:
        service: Gmail API service
        message_id: Gmail message ID
        format: Message format - "raw" for RFC 2822, "full" for structured data

    Returns:
        For format="raw": Raw email content as string, or None on error
        For format="full": Message dict with payload, or None on error
    """
    try:
        message = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format=format)
            .execute()
        )
        if format == "raw":
            raw_data = message.get("raw", "")
            # Gmail returns base64url encoded data
            decoded = base64.urlsafe_b64decode(raw_data)
            return decoded.decode("utf-8", errors="replace")
        return message
    except Exception as e:
        logger.error(f"Error fetching Gmail message {message_id}: {e}")
        return None


def iterate_gmail_messages(
    service,
    label_ids: list[str] | None = None,
    query: str | None = None,
    fetch_content: bool = False,
) -> Generator[tuple[str, str | None], None, None]:
    """
    Iterate over Gmail messages matching the given criteria.

    Args:
        service: Gmail API service
        label_ids: Optional list of label IDs to filter by
        query: Optional Gmail search query (e.g., "after:2024/01/01")
        fetch_content: If True, fetch raw email content; if False, yield (id, None)

    Yields:
        Tuples of (message_id, raw_email_content or None)
    """
    try:
        request = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                labelIds=label_ids if label_ids else None,
                maxResults=500,
            )
        )

        while request:
            response = request.execute()

            for msg_info in response.get("messages", []):
                msg_id = msg_info["id"]
                if fetch_content:
                    try:
                        raw_email = fetch_gmail_message(service, msg_id, format="raw")
                        if raw_email and isinstance(raw_email, str):
                            yield (msg_id, raw_email)
                    except Exception as e:
                        logger.error(f"Error fetching Gmail message {msg_id}: {e}")
                else:
                    yield (msg_id, None)

            # Handle pagination
            request = service.users().messages().list_next(request, response)

    except Exception as e:
        logger.error(f"Error listing Gmail messages: {e}")


def fetch_gmail_messages_by_ids(
    service,
    message_ids: set[str],
) -> Generator[tuple[str, str], None, None]:
    """
    Fetch email content for specific Gmail message IDs.

    Args:
        service: Gmail API service
        message_ids: Set of message IDs to fetch content for

    Yields:
        Tuples of (gmail_message_id, raw_email_content)
    """
    for msg_id in message_ids:
        raw_email = fetch_gmail_message(service, msg_id, format="raw")
        if raw_email:
            yield (msg_id, cast(str, raw_email))


def get_gmail_message_ids(
    account: EmailAccount,
    session: Session | scoped_session,
    service=None,
) -> tuple[set[str], object]:
    """
    Get all message IDs currently in Gmail for the configured labels.

    Used for deletion tracking - messages not in this set can be deleted.
    Returns the service object for reuse to avoid recreating it.

    Args:
        account: EmailAccount with linked GoogleAccount
        session: Database session for credential refresh
        service: Optional existing Gmail API service (to avoid recreating)

    Returns:
        Tuple of (set of Gmail message IDs, Gmail service object)
    """
    if service is None:
        service = get_gmail_service(account, session)

    folders = cast(list[str], account.folders) or ["INBOX"]
    label_ids = get_gmail_label_ids(service, folders)

    message_ids = {
        msg_id
        for msg_id, _ in iterate_gmail_messages(
            service, label_ids=label_ids, fetch_content=False
        )
    }

    return message_ids, service


def gmail_message_exists(service, message_id: str) -> bool:
    """
    Check if a Gmail message exists by its ID.

    Uses the messages.get API with minimal fields to check existence efficiently.
    This is useful for verifying individual messages that might have been archived
    (removed from monitored labels) but still exist in Gmail.

    Args:
        service: Gmail API service object
        message_id: Gmail message ID to check

    Returns:
        True if the message exists, False if deleted or not found
    """
    try:
        # Use fields parameter to minimize data transfer - we only need to know it exists
        service.users().messages().get(
            userId="me", id=message_id, format="minimal", fields="id"
        ).execute()
        return True
    except HttpError as e:
        if e.resp.status == 404:
            return False
        # For other errors (rate limiting, auth), we can't confirm non-existence
        raise


def find_removed_emails(
    db_session: Session | scoped_session,
    account_id: int,
    server_message_ids: set[str],
    folder: str | None = None,
) -> list[MailMessage]:
    """
    Find emails in the database that are no longer on the server.

    Args:
        db_session: Database session
        account_id: ID of the EmailAccount
        server_message_ids: Set of message IDs currently on server
        folder: Optional folder to filter by (for IMAP)

    Returns:
        List of MailMessage objects that should be deleted
    """
    if not server_message_ids:
        return []

    query = db_session.query(MailMessage).filter(
        MailMessage.email_account_id == account_id,
        MailMessage.imap_uid.isnot(None),
        MailMessage.imap_uid.notin_(server_message_ids),
    )

    if folder:
        query = query.filter(MailMessage.folder == folder)

    return query.all()


def delete_emails(
    emails: list[MailMessage], db_session: Session | scoped_session
) -> int:
    """
    Delete emails and their vectors from the database.

    Deletes from DB first, commits, then deletes vectors. This ensures
    that if commit fails, we don't have orphaned DB records without vectors.

    Note: This function commits internally to ensure DB deletion completes before
    vector deletion. Callers should NOT wrap this in their own transaction that
    expects to control commit timing.

    Args:
        emails: List of MailMessage objects to delete
        db_session: Database session

    Returns:
        Number of emails deleted
    """
    # Collect emails to delete and extract vector info BEFORE commit
    # (after commit, the ORM objects are detached and relationships inaccessible)
    deletion_info = []
    for email in emails:
        if should_delete_email(email):
            logger.info(
                f"Deleting email {email.message_id} "
                f"(UID {email.imap_uid}) - no longer on server"
            )
            # Extract vector info while session is still active
            deletion_info.append(extract_vector_deletion_info(email))
            db_session.delete(email)

    if not deletion_info:
        return 0

    # Commit DB deletions first
    db_session.commit()

    # Now safe to delete vectors using pre-extracted info
    for info in deletion_info:
        delete_email_vectors_from_info(info)

    return len(deletion_info)
