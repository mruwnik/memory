import logging
from datetime import datetime

from memory.common.db.connection import make_session
from memory.common.db.models import EmailAccount
from memory.workers.celery_app import app
from memory.workers.email import (
    check_message_exists,
    compute_message_hash,
    create_mail_message,
    create_source_item,
    imap_connection,
    parse_email_message,
    process_folder,
)


logger = logging.getLogger(__name__)


@app.task(name="memory.email.process_message")
def process_message(
    account_id: int, message_id: str, folder: str, raw_email: str,
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
    with make_session() as db:
        account = db.query(EmailAccount).get(account_id)
        if not account:
            logger.error(f"Account {account_id} not found")
            return None
        
        parsed_email = parse_email_message(raw_email)
        
        # Use server-provided message ID if missing
        if not parsed_email["message_id"]:
            parsed_email["message_id"] = f"generated-{message_id}"
        
        message_hash = compute_message_hash(
            parsed_email["message_id"], 
            parsed_email["subject"], 
            parsed_email["sender"], 
            parsed_email["body"]
        )
        
        if check_message_exists(db, parsed_email["message_id"], message_hash):
            logger.debug(f"Message {parsed_email['message_id']} already exists in database")
            return None
        
        source_item = create_source_item(db, message_hash, account.tags, len(raw_email))
        
        create_mail_message(db, source_item.id, parsed_email, folder)
        
        db.commit()
        
        # TODO: Queue for embedding once that's implemented
        
        return source_item.id


@app.task(name="memory.email.sync_account")
def sync_account(account_id: int) -> dict:
    """
    Synchronize emails from a specific account.
    
    Args:
        account_id: ID of the EmailAccount to sync
        
    Returns:
        dict with stats about the sync operation
    """
    with make_session() as db:
        account = db.query(EmailAccount).filter(EmailAccount.id == account_id).first()
        if not account or not account.active:
            logger.warning(f"Account {account_id} not found or inactive")
            return {"error": "Account not found or inactive"}
        
        folders_to_process = account.folders or ["INBOX"]
        since_date = account.last_sync_at or datetime(1970, 1, 1)

        messages_found = 0
        new_messages = 0
        errors = 0
        
        try:
            with imap_connection(account) as conn:
                for folder in folders_to_process:
                    folder_stats = process_folder(conn, folder, account, since_date)
                    
                    messages_found += folder_stats["messages_found"]
                    new_messages += folder_stats["new_messages"]
                    errors += folder_stats["errors"]
                
                account.last_sync_at = datetime.now()
                db.commit()
        except Exception as e:
            logger.error(f"Error connecting to server {account.imap_server}: {str(e)}")
            return {"error": str(e)}
        
        return {
            "account": account.email_address,
            "folders_processed": len(folders_to_process),
            "messages_found": messages_found,
            "new_messages": new_messages,
            "errors": errors
        }


@app.task(name="memory.email.sync_all_accounts")
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
                "task_id": sync_account.delay(account.id).id
            }
            for account in active_accounts
        ]