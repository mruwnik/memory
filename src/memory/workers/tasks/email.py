import contextlib
import logging
import pathlib
from datetime import datetime
from typing import Generator, cast

from sqlalchemy.exc import IntegrityError

from memory.common import settings
from memory.common.celery_app import PROCESS_EMAIL, SYNC_ACCOUNT, SYNC_ALL_ACCOUNTS, app
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import EmailAccount, MailMessage
from memory.common.db.models.source_item import clean_filename
from memory.common.redis_lock import Lock, distributed_lock
from memory.parsers.email import parse_email_message
from memory.workers.email import (
    create_mail_message,
    delete_emails,
    delete_removed_emails,
    fetch_gmail_messages_by_ids,
    find_removed_emails,
    get_gmail_message_ids,
    imap_connection,
    process_folder,
    vectorize_email,
)
from memory.common.content_processing import check_content_exists
from memory.common.jobs import tracked_task

logger = logging.getLogger(__name__)

# Lock timeout for email sync (15 minutes - email sync can take a while)
EMAIL_SYNC_LOCK_TIMEOUT = 900


@contextlib.contextmanager
def email_sync_lock(account_id: int) -> Generator[Lock | None, None, None]:
    """Distributed lock for email account sync to prevent duplicate processing.

    Yields a :class:`memory.common.redis_lock.Lock` if acquired (with
    ``extend()`` for renewal during long syncs), or ``None`` if another
    sync is in progress. Atomic check-and-delete release is handled by
    the underlying helper so we never clobber another worker's lock.
    """
    lock_key = f"memory:lock:email_sync:{account_id}"
    with distributed_lock(lock_key, EMAIL_SYNC_LOCK_TIMEOUT) as lock:
        if lock is None:
            logger.info(
                f"Email sync lock already held for account {account_id}, skipping"
            )
        yield lock


def spool_filename(account_id: int, message_id: str, sha256_hex: str) -> str:
    """Deterministic spool filename for a raw email awaiting processing.

    Keyed by account, server message id and content hash so re-enqueueing the
    same message overwrites its own file instead of leaking a new one. Only this
    bare filename (never an absolute path) crosses the Celery broker and the
    PendingJob params, so its length stays bounded regardless of
    ``FILE_STORAGE_DIR`` and it can't be truncated by the params snapshot's
    200-char cap — which a long absolute path could, silently breaking retry.
    """
    return f"{account_id}-{clean_filename(message_id)}-{sha256_hex[:16]}.eml"


def spool_path(filename: str) -> pathlib.Path:
    """Resolve a spool filename to its path under EMAIL_SPOOL_DIR.

    Uses only the basename so a crafted/garbled filename from job params can't
    escape the spool directory via ``..`` segments.
    """
    return settings.EMAIL_SPOOL_DIR / pathlib.Path(filename).name


def spool_raw_email(
    account_id: int, message_id: str, raw_email: str, sha256_hex: str
) -> str:
    """Persist a raw email to the spool dir; return its (bare) filename.

    A single RFC822 message carries its attachments inline as base64, so
    passing ``raw_email`` straight to the broker put hundreds of KB per task
    into Redis — a large backfill ballooned it to gigabytes and tripped
    ``stop-writes-on-bgsave-error``. Spooling the body to disk and sending only
    the filename keeps the broker message tiny.
    """
    name = spool_filename(account_id, message_id, sha256_hex)
    path = spool_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Pin UTF-8 on both write and read: raw_email is a Unicode str (decoded
    # upstream with errors="replace") that can hold arbitrary codepoints, and
    # the enqueuing and worker processes may run under different/locale-default
    # encodings (e.g. C/POSIX in a minimal container). An unpinned codec could
    # raise UnicodeEncodeError or silently corrupt the body across a mismatch.
    path.write_text(raw_email, encoding="utf-8")
    return name


def read_spooled_email(filename: str) -> str | None:
    """Read a spooled raw email by filename, or None if it is no longer present."""
    try:
        return spool_path(filename).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def store_email_message(
    account_id: int,
    message_id: str,
    folder: str,
    raw_email: str,
) -> dict:
    """Parse a raw email and persist it (with attachments and embeddings)."""
    try:
        with make_session() as db:
            account = db.get(EmailAccount, account_id)
            if not account:
                logger.error(f"Account {account_id} not found")
                return {"status": "error", "error": "Account not found"}

            parsed_email = parse_email_message(raw_email, message_id)
            if check_content_exists(
                db, MailMessage, message_id=message_id, sha256=parsed_email["hash"]
            ):
                return {"status": "already_exists", "message_id": message_id}

            mail_message = create_mail_message(
                db,
                account.tags,
                folder,
                parsed_email,
                email_account_id=account_id,
                imap_uid=message_id,
            )

            db.flush()
            vectorize_email(mail_message)

            db.commit()

            logger.info(f"Stored embedding for message {mail_message.message_id}")
            logger.info("Chunks:")
            for chunk in mail_message.chunks:
                logger.info(f" - {chunk.id}")
            for attachment in mail_message.attachments:
                logger.info(f" - Attachment {attachment.id}")
                for chunk in attachment.chunks:
                    logger.info(f"   - {chunk.id}")

            return {
                "status": "processed",
                "mail_message_id": cast(int, mail_message.id),
                "message_id": message_id,
                "chunks_count": len(mail_message.chunks),
                "attachments_count": len(mail_message.attachments),
            }
    except IntegrityError:
        # Another worker already processed this message (race condition)
        logger.info(f"Message {message_id} already exists (concurrent insert)")
        return {"status": "already_exists", "message_id": message_id}


@app.task(name=PROCESS_EMAIL)
@tracked_task
def process_message(
    account_id: int,
    message_id: str,
    folder: str,
    spool_name: str,
) -> dict:
    """
    Process a single email message and store it in the database.

    Args:
        account_id: ID of the EmailAccount
        message_id: UID of the message on the server
        folder: Folder name where the message is stored
        spool_name: Filename of the spooled raw email under EMAIL_SPOOL_DIR
            (written by ``queue_message``); read here and deleted once handled.

    Returns:
        dict with processing result
    """
    logger.info(f"Processing message {message_id} for account {account_id}")
    raw_email = read_spooled_email(spool_name)
    if raw_email is None:
        # Spool file gone (e.g. a previous run already consumed it, or a hard
        # crash between enqueue and processing). The next account sync re-fetches
        # and re-spools the message, so this is a safe no-op rather than an error.
        logger.warning(
            f"Spool file missing for message {message_id} "
            f"(account {account_id}): {spool_name}"
        )
        return {"status": "skipped", "reason": "spool_missing"}

    if not raw_email.strip():
        logger.warning(f"Empty email message received for account {account_id}")
        spool_path(spool_name).unlink(missing_ok=True)
        return {"status": "skipped", "reason": "empty_content"}

    result = store_email_message(account_id, message_id, folder, raw_email)
    # Delete only on a handled outcome. On an uncaught error this line is
    # skipped so the spool file survives and recovery can re-read it two ways:
    # the next account re-sync re-spools to the same deterministic filename (and
    # deletes on success), and a manual retry of the FAILED job re-dispatches
    # with the same spool_name — queue_message enqueues by keyword, so it's
    # captured in the job params. A repeatedly-failing message thus leaves at
    # most one stale file, so no separate orphan sweeper is needed.
    spool_path(spool_name).unlink(missing_ok=True)
    return result


def queue_message(
    db: DBSession,
    account_id: int,
    message_id: str,
    folder: str,
    raw_email: str,
) -> bool:
    """Parse, dedup, spool, and queue a single message for async processing.

    Returns True if the message was newly queued, False if it already
    exists (dedup hit). On any failure the shared session is rolled back
    before the exception propagates: a dropped connection (e.g. Postgres'
    idle-in-transaction timeout firing mid-sync) otherwise poisons the
    transaction and every subsequent statement cascades into
    PendingRollbackError. pool_pre_ping then hands out a live connection
    on the next statement.
    """
    spooled: str | None = None
    try:
        parsed_email = parse_email_message(raw_email, message_id)
        if check_content_exists(
            db, MailMessage, message_id=message_id, sha256=parsed_email["hash"]
        ):
            return False
        spooled = spool_raw_email(
            account_id, message_id, raw_email, parsed_email["hash"].hex()
        )
        # Enqueue by keyword (not positionally) so tracked_task's _build_job_params
        # captures these in the PendingJob params — which is what lets a manual
        # retry of a failed job reconstruct the call (including spool_name).
        process_message.delay(  # type: ignore[attr-defined]
            account_id=account_id,
            message_id=message_id,
            folder=folder,
            spool_name=spooled,
        )
        return True
    except Exception:
        # If we spooled the body but never got it onto the queue (e.g. the
        # broker is unreachable — the exact failure this change guards against),
        # drop the orphaned file so a failing backfill can't litter the spool
        # dir with one stale .eml per message.
        if spooled is not None:
            spool_path(spooled).unlink(missing_ok=True)
        db.rollback()
        raise


def get_cutoff_date(account: EmailAccount, since_date: str | None) -> datetime:
    """Get the cutoff date for syncing emails."""
    if since_date:
        return datetime.fromisoformat(since_date)
    return cast(datetime, account.last_sync_at) or datetime(1970, 1, 1)


# Extend lock every N messages during batch processing
LOCK_EXTEND_INTERVAL = 100


def process_email_batch(
    account: EmailAccount,
    db: DBSession,
    messages: Generator[tuple[str, str], None, None],
    folder: str = "INBOX",
    lock: Lock | None = None,
) -> dict:
    """
    Process a batch of emails, queuing them for async processing.

    Args:
        account: EmailAccount being synced
        db: Database session
        messages: Generator yielding (message_id, raw_email) tuples
        folder: Folder name for the messages
        lock: Optional lock to extend during long-running operations

    Returns:
        Stats dict with messages_found, new_messages, errors
    """
    messages_found = 0
    new_messages = 0
    errors = 0

    for message_id, raw_email in messages:
        messages_found += 1

        # Extend lock periodically to prevent expiry during long syncs
        if lock and messages_found % LOCK_EXTEND_INTERVAL == 0:
            if not lock.extend():
                logger.warning(
                    "Failed to extend lock, another sync may have started - aborting"
                )
                # Caller should check the returned `aborted` field. The
                # outer email_sync_lock context manager will run its
                # ownership-checked release in finally — which is now a
                # no-op (correctly) because some other worker holds the
                # lock.
                return {
                    "messages_found": messages_found,
                    "new_messages": new_messages,
                    "errors": errors,
                    "aborted": True,
                    "abort_reason": "lock_extension_failed",
                }

        try:
            if queue_message(
                db, cast(int, account.id), message_id, folder, raw_email
            ):
                new_messages += 1
        except Exception as e:
            logger.error(f"Error queuing message {message_id}: {e}")
            errors += 1

    return {
        "messages_found": messages_found,
        "new_messages": new_messages,
        "errors": errors,
    }


def finalize_sync(
    account: EmailAccount,
    db: DBSession,
    error: Exception | None = None,
) -> None:
    """Update account sync status after sync completes."""
    if error:
        # The failing sync may have left the session in a poisoned
        # transaction (e.g. a dropped connection mid-query). Roll back
        # first so the sync_error write can actually be committed instead
        # of cascading into PendingRollbackError and losing the status.
        db.rollback()
        account.sync_error = str(error)  # type: ignore
    else:
        account.last_sync_at = datetime.now()  # type: ignore
        account.sync_error = None  # type: ignore
    db.commit()


def sync_imap_messages(
    account: EmailAccount,
    db: DBSession,
    cutoff_date: datetime,
    lock: Lock | None = None,
) -> dict:
    """Sync emails from an IMAP account."""
    folders_to_process: list[str] = cast(list[str], account.folders) or ["INBOX"]
    messages_found = 0
    new_messages = 0
    errors = 0
    deleted_messages = 0

    def message_processor(
        account_id: int, message_id: str, folder: str, raw_email: str
    ) -> bool:
        return queue_message(db, account_id, message_id, folder, raw_email)

    with imap_connection(account) as conn:
        for folder in folders_to_process:
            # Extend lock before each folder (folders can be large)
            if lock:
                lock.extend()

            # Close any transaction left open by the previous folder's
            # delete pass before fetching this folder. fetch_email_since
            # can take a long time on large folders, and a session left
            # idle-in-transaction during it gets killed by Postgres.
            db.commit()

            folder_stats = process_folder(
                conn, folder, account, cutoff_date, message_processor
            )

            messages_found += folder_stats["messages_found"]
            new_messages += folder_stats["new_messages"]
            errors += folder_stats["errors"]

            deleted_messages += delete_removed_emails(
                conn, db, cast(int, account.id), folder
            )

    return {
        "messages_found": messages_found,
        "new_messages": new_messages,
        "deleted_messages": deleted_messages,
        "errors": errors,
        "folders_processed": len(folders_to_process),
    }


def sync_gmail_messages(
    account: EmailAccount,
    db: DBSession,
    cutoff_date: datetime,
    lock: Lock | None = None,
) -> dict:
    """Sync emails from a Gmail account using the Gmail API."""
    # Get all message IDs from Gmail (single API call)
    server_message_ids, service = get_gmail_message_ids(account, db)

    # Find which messages we don't have in the DB yet
    existing_uids = {
        uid
        for (uid,) in db.query(MailMessage.imap_uid)
        .filter(
            MailMessage.email_account_id == account.id,
            MailMessage.imap_uid.in_(server_message_ids),
        )
        .all()
    }
    new_message_ids = server_message_ids - existing_uids

    # Release the read transaction from the existing-UID query before the
    # Gmail body fetch, which is network-bound and can exceed the 60s
    # idle_in_transaction_session_timeout if a transaction is held open.
    db.commit()

    # Fetch content only for new messages
    messages = fetch_gmail_messages_by_ids(service, new_message_ids)
    stats = process_email_batch(account, db, messages, folder="INBOX", lock=lock)

    # Delete emails that are no longer in Gmail (reuse server_message_ids)
    emails_to_delete = find_removed_emails(
        db, cast(int, account.id), server_message_ids
    )
    deleted_count = delete_emails(emails_to_delete, db)

    return {
        "messages_found": len(server_message_ids),
        "new_messages": stats["new_messages"],
        "deleted_messages": deleted_count,
        "errors": stats["errors"],
    }


@app.task(name=SYNC_ACCOUNT)
@tracked_task
def sync_account(account_id: int, since_date: str | None = None) -> dict:
    """
    Synchronize emails from a specific account.

    Args:
        account_id: ID of the EmailAccount to sync
        since_date: ISO format date string to sync since

    Returns:
        dict with stats about the sync operation
    """
    logger.info(f"Syncing account {account_id} since {since_date}")

    # Use distributed lock to prevent concurrent syncs of the same account
    with email_sync_lock(account_id) as lock:
        if lock is None:
            return {"status": "skipped", "reason": "sync already in progress"}

        with make_session() as db:
            account = db.query(EmailAccount).filter(EmailAccount.id == account_id).first()
            if not account or not cast(bool, account.active):
                logger.warning(f"Account {account_id} not found or inactive")
                return {"status": "error", "error": "Account not found or inactive"}

            account_type = cast(str, account.account_type) or "imap"
            cutoff_date = get_cutoff_date(account, since_date)

            # Release the read transaction opened by loading the account
            # before the sync does network I/O (IMAP connect/fetch, Gmail
            # API calls). Otherwise the session sits "idle in transaction"
            # for the duration of the fetch and Postgres' 60s
            # idle_in_transaction_session_timeout kills the connection
            # mid-sync. expire_on_commit=False keeps `account` usable here.
            db.commit()

            try:
                if account_type == "gmail":
                    stats = sync_gmail_messages(account, db, cutoff_date, lock)
                else:
                    stats = sync_imap_messages(account, db, cutoff_date, lock)

                # Don't finalize if sync was aborted (e.g., lock extension failed)
                if stats.get("aborted"):
                    return {"status": "aborted", **stats}

                finalize_sync(account, db)

            except Exception as e:
                logger.error(f"Error syncing account {account.email_address}: {e}")
                finalize_sync(account, db, error=e)
                return {"status": "error", "error": str(e)}

            return {
                "status": "completed",
                "account_type": account_type,
                "account": account.email_address,
                "since_date": cutoff_date.isoformat(),
                **stats,
            }


@app.task(name=SYNC_ALL_ACCOUNTS)
@tracked_task
def sync_all_accounts() -> list[dict]:
    """
    Synchronize all active email accounts.

    Returns:
        List of task IDs that were scheduled
    """
    with make_session() as db:
        active_accounts = db.query(EmailAccount).filter(EmailAccount.active).all()

        return [
            {
                "account_id": account.id,
                "email": account.email_address,
                "task_id": sync_account.delay(account.id).id,  # type: ignore
            }
            for account in active_accounts
        ]
