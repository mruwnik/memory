import logging
from datetime import datetime
from typing import cast

from sqlalchemy.exc import IntegrityError

from memory.common.db.connection import make_session
from memory.common.db.models import EmailAccount, MailMessage
from memory.common.celery_app import app, PROCESS_EMAIL, SYNC_ACCOUNT, SYNC_ALL_ACCOUNTS
from memory.workers.email import (
    create_mail_message,
    delete_removed_emails,
    imap_connection,
    process_folder,
    vectorize_email,
)
from memory.parsers.email import parse_email_message
from memory.workers.tasks.content_processing import (
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

        folders_to_process: list[str] = cast(list[str], account.folders) or ["INBOX"]
        if since_date:
            cutoff_date = datetime.fromisoformat(since_date)
        else:
            cutoff_date: datetime = cast(datetime, account.last_sync_at) or datetime(
                1970, 1, 1
            )

        messages_found = 0
        new_messages = 0
        errors = 0
        deleted_messages = 0

        def process_message_wrapper(
            account_id: int, message_id: str, folder: str, raw_email: str
        ) -> int | None:
            parsed_email = parse_email_message(raw_email, message_id)
            if check_content_exists(
                db, MailMessage, message_id=message_id, sha256=parsed_email["hash"]
            ):
                return None
            return process_message.delay(account_id, message_id, folder, raw_email)  # type: ignore

        try:
            with imap_connection(account) as conn:
                for folder in folders_to_process:
                    folder_stats = process_folder(
                        conn, folder, account, cutoff_date, process_message_wrapper
                    )

                    messages_found += folder_stats["messages_found"]
                    new_messages += folder_stats["new_messages"]
                    errors += folder_stats["errors"]

                    # Delete emails that are no longer on the server
                    deleted_messages += delete_removed_emails(
                        conn, db, account_id, folder
                    )

                account.last_sync_at = datetime.now()  # type: ignore
                db.commit()
        except Exception as e:
            logger.error(f"Error connecting to server {account.imap_server}: {str(e)}")
            return {"status": "error", "error": str(e)}

        return {
            "status": "completed",
            "account": account.email_address,
            "since_date": cutoff_date.isoformat(),
            "folders_processed": len(folders_to_process),
            "messages_found": messages_found,
            "new_messages": new_messages,
            "deleted_messages": deleted_messages,
            "errors": errors,
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
