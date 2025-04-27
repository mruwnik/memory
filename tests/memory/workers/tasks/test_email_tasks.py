import pytest
from datetime import datetime, timedelta

from memory.common.db.models import EmailAccount
from memory.workers.tasks.email import process_message, sync_account, sync_all_accounts
# from ..email_provider import MockEmailProvider


@pytest.fixture
def sample_emails():
    """Fixture providing a sample set of test emails across different folders."""
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    last_week = now - timedelta(days=7)
    
    return {
        "INBOX": [
            {
                "uid": 101,
                "flags": "\\Seen",
                "date": now.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                "date_internal": now.strftime("%d-%b-%Y %H:%M:%S +0000"),
                "from": "alice@example.com",
                "to": "bob@example.com",
                "subject": "Recent Test Email",
                "message_id": "<test-101@example.com>",
                "body": "This is a recent test email"
            },
            {
                "uid": 102,
                "flags": "",
                "date": yesterday.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                "date_internal": yesterday.strftime("%d-%b-%Y %H:%M:%S +0000"),
                "from": "charlie@example.com",
                "to": "bob@example.com",
                "subject": "Yesterday's Email",
                "message_id": "<test-102@example.com>",
                "body": "This email was sent yesterday"
            }
        ],
        "Sent": [
            {
                "uid": 201,
                "flags": "\\Seen",
                "date": yesterday.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                "date_internal": yesterday.strftime("%d-%b-%Y %H:%M:%S +0000"),
                "from": "bob@example.com",
                "to": "alice@example.com",
                "subject": "Re: Test Email",
                "message_id": "<test-201@example.com>",
                "body": "This is a reply to the test email"
            }
        ],
        "Archive": [
            {
                "uid": 301,
                "flags": "\\Seen",
                "date": last_week.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                "date_internal": last_week.strftime("%d-%b-%Y %H:%M:%S +0000"),
                "from": "david@example.com",
                "to": "bob@example.com",
                "subject": "Old Email",
                "message_id": "<test-301@example.com>",
                "body": "This is an old email from last week"
            },
            {
                "uid": 302,
                "flags": "\\Seen",
                "date": last_week.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                "date_internal": last_week.strftime("%d-%b-%Y %H:%M:%S +0000"),
                "from": "eve@example.com",
                "to": "bob@example.com",
                "subject": "Email with Attachment",
                "message_id": "<test-302@example.com>",
                "body": "This email has an attachment",
                "attachments": [
                    {
                        "filename": "test.txt",
                        "maintype": "text",
                        "subtype": "plain",
                        "content": b"This is a test attachment"
                    }
                ]
            }
        ]
    }


@pytest.fixture
def test_email_account(db_session):
    """Create a test email account for integration testing."""
    account = EmailAccount(
        name="Test Account",
        email_address="bob@example.com",
        imap_server="imap.example.com",
        imap_port=993,
        username="bob@example.com",
        password="password123",
        use_ssl=True,
        folders=["INBOX", "Sent", "Archive"],
        tags=["test", "integration"],
        active=True
    )
    db_session.add(account)
    db_session.commit()
    return account
