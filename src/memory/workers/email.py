import base64
import hashlib
import imaplib
import ssl
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Generator, Iterable, Sequence, cast

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session, object_session, scoped_session

from memory.common import collections, embedding, mail_connect, paths, settings
from memory.common.content_processing import (
    chunk_ids_by_collection,
    delete_vectors,
    push_chunks_to_qdrant,
)
from memory.common.db.models import EmailAccount, EmailAttachment, MailMessage
from memory.common.people import link_people
from memory.parsers.email import Attachment, EmailMessage, RawEmailResponse
from memory.parsers.google_drive import refresh_credentials

logger = logging.getLogger(__name__)


def process_attachment(
    attachment: Attachment, message: MailMessage, tag: str = ""
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
        file_path = message.safe_filename(attachment["filename"], tag)
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(real_content)
        except Exception:
            logger.exception("Failed to save an attachment to disk")
            return None

    return EmailAttachment(
        modality=collections.get_modality(attachment["content_type"]),
        sha256=hashlib.sha256(
            real_content if real_content else str(attachment).encode()
        ).digest(),
        tags=["email"] + (message.tags or []),
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
    names = [message.safe_filename(a["filename"]) for a in attachments]
    # A repeated name gets a content-hash tag: stable across reingests, and two
    # copies of the same file may share a path.
    tags = [
        hashlib.sha256(a.get("content") or b"").hexdigest()[:8] if name in names[:i] else ""
        for i, (a, name) in enumerate(zip(attachments, names))
    ]
    return [
        attachment
        for a, tag in zip(attachments, tags)
        if (attachment := process_attachment(a, message, tag))
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
INTERNALDATE_PATTERN = re.compile(r'INTERNALDATE "([^"]+)"')


def extract_email_uid(
    msg_data: Sequence[bytes | tuple[bytes, bytes]],
) -> RawEmailResponse:
    """
    Extract the UID and raw email data from a FETCH response.

    ``msg_data`` is what ``imaplib``'s ``uid("FETCH", ...)`` returns: a list whose
    literal/header bytes come back as bare ``bytes`` and whose body parts come
    back as ``(header, payload)`` tuples. RFC 3501 does not guarantee the server
    echoes data items in request order, so the ``UID`` token may land in any of
    the string/tuple-header parts; scan them all rather than only the first.
    """
    uid: str | None = None
    received_at: datetime | None = None
    raw_email = b""
    for part in msg_data:
        header, payload = part if isinstance(part, tuple) else (part, None)
        text = _as_text(header)
        if uid is None and (match := UID_PATTERN.search(text)):
            uid = match.group(1)
        if received_at is None and (match := INTERNALDATE_PATTERN.search(text)):
            received_at = parse_internaldate(match.group(1))
        if payload is not None and not raw_email:
            raw_email = payload

    if uid is None:
        logger.warning(f"Could not extract UID from FETCH response: {msg_data!r}")
    return uid, raw_email, received_at


def parse_internaldate(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.strip(), "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        return None


def _as_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def fetch_email(conn: imaplib.IMAP4, uid: str) -> RawEmailResponse | None:
    try:
        status, msg_data = conn.uid("FETCH", uid, "(UID INTERNALDATE BODY.PEEK[])")
        if status != "OK" or not msg_data or not msg_data[0]:
            logger.error(f"Error fetching message {uid!r}")
            return None

        return extract_email_uid(msg_data)
    except Exception:
        logger.exception(f"Error processing message {uid!r}")
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
        List of (uid, raw_email, received_at) tuples
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
    except Exception:
        logger.exception("Error in fetch_email_since for a folder")
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
    processor: Callable[[int, str, str, str, datetime | None], bool],
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

        for uid, raw_email, received_at in emails:
            try:
                task = processor(
                    account_id=account.id,  # type: ignore
                    message_id=uid,
                    folder=folder,
                    raw_email=raw_email.decode("utf-8", errors="replace"),
                    received_at=received_at,
                )
                if task:
                    new_messages += 1
            except Exception:
                logger.exception(f"Error queuing message {uid}")
                errors += 1

    except Exception:
        logger.exception("Error processing a folder")
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
    context = ssl.create_default_context()
    # Port 143 requires STARTTLS before any credentials are sent.
    if account.use_ssl is False:
        conn: imaplib.IMAP4 = mail_connect.PublicIMAP4(host=host, port=port)
    else:
        conn = mail_connect.PublicIMAP4SSL(host=host, port=port, ssl_context=context)
    try:
        if account.use_ssl is False:
            conn.starttls(ssl_context=context)
        conn.login(cast(str, account.username), cast(str, account.password))
        yield conn
    finally:
        # Always try to logout and close the connection
        try:
            conn.logout()
        except Exception:
            logger.exception("Error logging out of IMAP")
            # If logout fails, explicitly close the socket to prevent resource leak
            try:
                conn.shutdown()
            except Exception:
                pass  # Socket may already be closed


def vectorize_email(email: MailMessage):
    if not embedding.configured():
        for item in [email, *email.attachments]:
            item.embed_status = "FAILED"  # type: ignore
        embedding.warn_not_configured()
        return

    email.chunks = embedding.embed_source_item(email)
    chunks = list(email.chunks)

    # The flush drops attachments whose content is already stored
    # (handle_duplicate_sha256); chunking those only writes chunk images
    # that no row will ever reference.
    stored = [a for a in email.attachments if object_session(a) is not None]
    for attachment in stored:
        if not attachment.should_embed:
            attachment.embed_status = "SKIPPED"  # type: ignore
            continue
        try:
            attachment_chunks = embedding.embed_source_item(attachment)
        except Exception:
            # One undecodable attachment (e.g. a decompression-bomb image) must
            # not abort the whole email: marking it FAILED and continuing lets
            # the message commit, so it isn't re-spooled into an endless retry.
            logger.exception(
                f"Failed to embed attachment {attachment.id}; marking FAILED"
            )
            attachment.embed_status = "FAILED"  # type: ignore
            continue

        attachment.chunks = attachment_chunks
        # Page and image rasters are on disk by now; holding every
        # attachment's at once is what can take a worker past its memory cap.
        for chunk in attachment_chunks:
            chunk.images = []
        chunks.extend(attachment_chunks)

    push_chunks_to_qdrant(chunks)

    email.embed_status = "STORED"  # type: ignore
    for attachment in email.attachments:
        if cast(str, attachment.embed_status) not in ("FAILED", "SKIPPED"):
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
    # The name exactly as LIST printed it (quoted, modified UTF-7), which is
    # what SELECT must be sent; ``name`` is the decoded form shown to users.
    mailbox: str


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
    mailbox = match.group("name").decode("utf-8", "replace").strip()
    name = mailbox
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
    return ImapFolder(name=name, flags=flags, selectable=selectable, mailbox=mailbox)


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


def extract_vector_deletion_info(email: MailMessage) -> dict[str, list[str]]:
    """Chunk ids of the email and its attachments, by collection.

    Call this before commit, while the session can still load the chunks.
    """
    chunks = [*(email.chunks or [])]
    for attachment in email.attachments:
        chunks.extend(attachment.chunks or [])
    return chunk_ids_by_collection(chunks)


def delete_removed_emails(
    conn: imaplib.IMAP4,
    db_session: Session | scoped_session,
    account_id: int,
    folder: str,
) -> int:
    """
    Delete emails from the database that are no longer on the IMAP server.

    Compares UIDs on the server with UIDs in the database for the given
    account and folder. A stored email whose UID left the folder but whose
    Message-ID turns up in another folder (other than Trash/Junk) is re-pointed
    there; the rest are deleted.

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
    except FolderUidsError:
        # Could not read the folder. Skip reconciliation rather than risk
        # mass-deleting local mail on a transient SELECT/SEARCH failure.
        logger.exception("Skipping deletion for a folder that could not be read")
        return 0

    # Intentional safety asymmetry: a genuinely-empty folder (empty set) also
    # returns 0 without deleting. find_removed_emails treats an empty
    # server_uids as "delete nothing" (it can't tell empty-because-erased from
    # empty-because-unreadable), so emptying a watched folder on the server
    # leaves the local copies in the DB rather than wiping them.
    departed = find_removed_emails(db_session, account_id, server_uids, folder)
    if not departed:
        return 0

    # A message that left the folder was usually moved (archived, filed), not
    # deleted; only mail found nowhere outside Trash/Junk is gone.
    try:
        located = locate_imap_messages(
            conn, {cast(str, email.message_id) for email in departed}, folder
        )
    except Exception:
        logger.exception(
            "Skipping deletion for account %s: could not search its other folders",
            account_id,
        )
        return 0

    gone = []
    for email in departed:
        if location := located.get(cast(str, email.message_id)):
            email.folder, email.imap_uid = location
        else:
            gone.append(email)
    db_session.commit()
    return delete_emails(gone, db_session)


IMAP_DISCARD_FLAGS = {"\\trash", "\\junk"}
IMAP_DISCARD_NAMES = {"trash", "junk", "spam", "bin", "deleted items", "deleted messages"}


def is_discard_folder(folder: ImapFolder) -> bool:
    """Trash or Junk, by RFC 6154 special-use flag or, for servers without it, by name."""
    leaf = re.split(r"[/.]", folder.name)[-1].casefold()
    return leaf in IMAP_DISCARD_NAMES or any(
        flag.casefold() in IMAP_DISCARD_FLAGS for flag in folder.flags
    )


def imap_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def locate_imap_messages(
    conn: imaplib.IMAP4, message_ids: set[str], exclude_folder: str
) -> dict[str, tuple[str, str]]:
    """Find where each Message-ID lives now: ``{message_id: (folder, uid)}``.

    Searches every selectable folder except ``exclude_folder`` and Trash/Junk.
    Raises FolderUidsError if a folder can't be searched, so an unsearched
    folder is never mistaken for one that lacks the message.
    """
    found: dict[str, tuple[str, str]] = {}
    for folder in list_imap_folders(conn):
        remaining = message_ids - found.keys()
        if not remaining:
            break
        if not folder.selectable or folder.name == exclude_folder or is_discard_folder(folder):
            continue
        status, _ = conn.select(folder.mailbox, readonly=True)
        if status != "OK":
            raise FolderUidsError(f"Error selecting folder {folder.name}")
        for message_id in remaining:
            status, data = conn.uid(
                "SEARCH", "UNDELETED", "HEADER", "Message-ID", imap_quote(message_id)
            )
            if status != "OK":
                raise FolderUidsError(f"Error searching folder {folder.name}")
            uids = (data[0] or b"").split() if data else []
            if uids:
                found[message_id] = (folder.name, uids[-1].decode())
    return found


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

    # Fail closed: a missing label or API failure must never expand the sync
    # to the entire mailbox or make deletion tracking use a partial result.
    labels_response = service.users().labels().list(userId="me").execute()
    api_labels = {
        label["name"].lower(): label["id"]
        for label in labels_response.get("labels", [])
    }
    if custom_folders - api_labels.keys():
        raise ValueError("A selected Gmail folder no longer exists; refresh the folder selection")
    return list(label_ids) + [api_labels[folder] for folder in custom_folders]


def fetch_gmail_message(service, message_id: str) -> tuple[str, datetime | None] | None:
    """A Gmail message's raw RFC 2822 text and when Gmail received it, or None on error."""
    try:
        message = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="raw")
            .execute()
        )
        raw_email = base64.urlsafe_b64decode(message.get("raw", "")).decode("utf-8", errors="replace")
        internal_date = message.get("internalDate")
        received_at = internal_date and datetime.fromtimestamp(int(internal_date) / 1000, timezone.utc)
    except Exception:
        logger.exception(f"Error fetching Gmail message {message_id}")
        return None
    return raw_email, received_at or None


def iterate_gmail_messages(
    service,
    label_ids: list[str] | None = None,
    query: str | None = None,
) -> Generator[str, None, None]:
    """Ids of the Gmail messages carrying `label_ids` and matching `query`."""
    request = (
        service.users()
        .messages()
        .list(
            userId="me",
            q=query,
            labelIds=label_ids if label_ids else None,
            maxResults=500,
            includeSpamTrash=bool(set(label_ids or []) & {"SPAM", "TRASH"}),
        )
    )

    while request:
        response = request.execute()
        for msg_info in response.get("messages", []):
            yield msg_info["id"]
        request = service.users().messages().list_next(request, response)


def fetch_gmail_messages_by_ids(
    service,
    message_ids: set[str],
) -> Generator[tuple[str, str, datetime | None], None, None]:
    """(gmail message id, raw email, received at) of each message that could be fetched."""
    for msg_id in message_ids:
        fetched = fetch_gmail_message(service, msg_id)
        if fetched and fetched[0]:
            yield (msg_id, *fetched)


GMAIL_MAILBOX_QUERY = "-in:drafts -in:chats"


def list_gmail_message_ids(service, label_ids: list[str] | None = None) -> set[str]:
    """IDs of the messages carrying any of ``label_ids``, or with ``None`` of
    every mail outside Spam and Trash (Gmail's own default for a listing)."""
    if label_ids is None:
        # An unfiltered listing also returns unsent drafts and Hangouts chats.
        return {
            msg_id
            for msg_id in iterate_gmail_messages(service, query=GMAIL_MAILBOX_QUERY)
        }
    # labelIds on one listing means "carries all of these", so union per label
    return {
        msg_id
        for label_id in label_ids
        for msg_id in iterate_gmail_messages(service, label_ids=[label_id])
    }


GMAIL_GONE_LABELS = {"TRASH", "SPAM"}
# Google advises at most 50 calls per batch; larger ones get rate limited
GMAIL_BATCH_SIZE = 50


def gmail_gone_message_ids(service, message_ids: Iterable[str]) -> set[str]:
    """The messages that are deleted (404) or sit in Trash or Spam.

    A message whose status can't be read (rate limit, server error) is not
    reported gone, so it is kept and asked about again next time.
    """
    gone: set[str] = set()

    def record(message_id: str, response: dict | None, exception: Exception | None):
        if exception is None:
            if GMAIL_GONE_LABELS & set((response or {}).get("labelIds", [])):
                gone.add(message_id)
        elif isinstance(exception, HttpError) and exception.resp.status == 404:
            gone.add(message_id)
        else:
            logger.warning(f"Could not check Gmail message {message_id}: {exception}")

    ids = sorted(message_ids)
    for start in range(0, len(ids), GMAIL_BATCH_SIZE):
        batch = service.new_batch_http_request(callback=record)
        for message_id in ids[start : start + GMAIL_BATCH_SIZE]:
            batch.add(
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="minimal", fields="id,labelIds"),
                request_id=message_id,
            )
        batch.execute()
    return gone


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
        try:
            delete_vectors(info)
        except IOError:
            logger.warning("Error deleting vectors of a removed email", exc_info=True)

    return len(deletion_info)
