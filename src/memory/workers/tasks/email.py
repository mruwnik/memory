import logging
from datetime import datetime
from typing import cast
from memory.common.db.connection import make_session
from memory.common.db.models import EmailAccount
from memory.workers.celery_app import app
from memory.workers.email import (
    check_message_exists,
    create_mail_message,
    imap_connection,
    process_folder,
    vectorize_email,
)


logger = logging.getLogger(__name__)

PROCESS_EMAIL = "memory.workers.tasks.email.process_message"
SYNC_ACCOUNT = "memory.workers.tasks.email.sync_account"
SYNC_ALL_ACCOUNTS = "memory.workers.tasks.email.sync_all_accounts"


@app.task(name=PROCESS_EMAIL)
def process_message(
    account_id: int,
    message_id: str,
    folder: str,
    raw_email: str,
) -> int | None:
    """
    Process a single email message and store it in the database.

    Args:
        account_id: ID of the EmailAccount
        message_id: UID of the message on the server
        folder: Folder name where the message is stored
        raw_email: Raw email content as string

    Returns:
        source_id if successful, None otherwise
    """
    logger.info(f"Processing message {message_id} for account {account_id}")
    if not raw_email.strip():
        logger.warning(f"Empty email message received for account {account_id}")
        return None

    with make_session() as db:
        if check_message_exists(db, account_id, message_id, raw_email):
            logger.debug(f"Message {message_id} already exists in database")
            return None

        account = db.query(EmailAccount).get(account_id)
        if not account:
            logger.error(f"Account {account_id} not found")
            return None

        mail_message = create_mail_message(
            db, account.tags, folder, raw_email, message_id
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

        return cast(int, mail_message.id)


@app.task(name=SYNC_ACCOUNT)
def sync_account(account_id: int, since_date: str | None = None) -> dict:
    """
    Synchronize emails from a specific account.

    Args:
        account_id: ID of the EmailAccount to sync

    Returns:
        dict with stats about the sync operation
    """
    logger.info(f"Syncing account {account_id} since {since_date}")

    with make_session() as db:
        account = db.query(EmailAccount).filter(EmailAccount.id == account_id).first()
        if not account or not cast(bool, account.active):
            logger.warning(f"Account {account_id} not found or inactive")
            return {"error": "Account not found or inactive"}

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

        def process_message_wrapper(
            account_id: int, message_id: str, folder: str, raw_email: str
        ) -> int | None:
            if check_message_exists(db, account_id, message_id, raw_email):  # type: ignore
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

                account.last_sync_at = datetime.now()  # type: ignore
                db.commit()
        except Exception as e:
            logger.error(f"Error connecting to server {account.imap_server}: {str(e)}")
            return {"error": str(e)}

        return {
            "account": account.email_address,
            "since_date": cutoff_date.isoformat(),
            "folders_processed": len(folders_to_process),
            "messages_found": messages_found,
            "new_messages": new_messages,
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
