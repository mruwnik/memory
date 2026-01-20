import logging
from datetime import datetime
from typing import Generator, cast

from sqlalchemy.exc import IntegrityError

from memory.common.celery_app import PROCESS_EMAIL, SYNC_ACCOUNT, SYNC_ALL_ACCOUNTS, app
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import EmailAccount, MailMessage
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
from memory.common.content_processing import (
    check_content_exists,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


@app.task(name=PROCESS_EMAIL)
@safe_task_execution
def process_message(
    account_id: int,
    message_id: str,
    folder: str,
    raw_email: str,
) -> dict:
    """
    Process a single email message and store it in the database.

    Args:
        account_id: ID of the EmailAccount
        message_id: UID of the message on the server
        folder: Folder name where the message is stored
        raw_email: Raw email content as string

    Returns:
        dict with processing result
    """
    logger.info(f"Processing message {message_id} for account {account_id}")
    if not raw_email.strip():
        logger.warning(f"Empty email message received for account {account_id}")
        return {"status": "skipped", "reason": "empty_content"}

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


def get_cutoff_date(account: EmailAccount, since_date: str | None) -> datetime:
    """Get the cutoff date for syncing emails."""
    if since_date:
        return datetime.fromisoformat(since_date)
    return cast(datetime, account.last_sync_at) or datetime(1970, 1, 1)


def process_email_batch(
    account: EmailAccount,
    db: DBSession,
    messages: Generator[tuple[str, str], None, None],
    folder: str = "INBOX",
) -> dict:
    """
    Process a batch of emails, queuing them for async processing.

    Args:
        account: EmailAccount being synced
        db: Database session
        messages: Generator yielding (message_id, raw_email) tuples
        folder: Folder name for the messages

    Returns:
        Stats dict with messages_found, new_messages, errors
    """
    messages_found = 0
    new_messages = 0
    errors = 0

    for message_id, raw_email in messages:
        messages_found += 1
        try:
            parsed_email = parse_email_message(raw_email, message_id)
            if check_content_exists(
                db, MailMessage, message_id=message_id, sha256=parsed_email["hash"]
            ):
                continue

            process_message.delay(
                cast(int, account.id),
                message_id,
                folder,
                raw_email,
            )
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
        account.sync_error = str(error)  # type: ignore
    else:
        account.last_sync_at = datetime.now()  # type: ignore
        account.sync_error = None  # type: ignore
    db.commit()


def sync_imap_messages(
    account: EmailAccount,
    db: DBSession,
    cutoff_date: datetime,
) -> dict:
    """Sync emails from an IMAP account."""
    folders_to_process: list[str] = cast(list[str], account.folders) or ["INBOX"]
    messages_found = 0
    new_messages = 0
    errors = 0
    deleted_messages = 0

    def message_processor(
        account_id: int, message_id: str, folder: str, raw_email: str
    ) -> int | None:
        parsed_email = parse_email_message(raw_email, message_id)
        if check_content_exists(
            db, MailMessage, message_id=message_id, sha256=parsed_email["hash"]
        ):
            return None
        return process_message.delay(account_id, message_id, folder, raw_email)  # type: ignore

    with imap_connection(account) as conn:
        for folder in folders_to_process:
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

    # Fetch content only for new messages
    messages = fetch_gmail_messages_by_ids(service, new_message_ids)
    stats = process_email_batch(account, db, messages, folder="INBOX")

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
@safe_task_execution
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

    with make_session() as db:
        account = db.query(EmailAccount).filter(EmailAccount.id == account_id).first()
        if not account or not cast(bool, account.active):
            logger.warning(f"Account {account_id} not found or inactive")
            return {"status": "error", "error": "Account not found or inactive"}

        account_type = cast(str, account.account_type) or "imap"
        cutoff_date = get_cutoff_date(account, since_date)

        try:
            if account_type == "gmail":
                stats = sync_gmail_messages(account, db, cutoff_date)
            else:
                stats = sync_imap_messages(account, db, cutoff_date)

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
