"""Tests for the verification module."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from memory.common import settings
from memory.common.db.models import GithubItem, MailMessage
from memory.common.db.models.sources import EmailAccount, GithubAccount, GithubRepo
from memory.workers.verification import (
    BatchVerificationResult,
    VerificationResult,
    VERIFIERS,
    delete_orphaned_item,
    get_email_batch_key,
    get_github_batch_key,
    group_items_by_batch_key,
    process_verification_results,
    select_items_for_verification,
    verify_emails,
    verify_github_items,
    verify_items,
)


# =============================================================================
# Unit Tests for VerificationResult and BatchVerificationResult
# =============================================================================


def test_verification_result_exists():
    result = VerificationResult(item_id=1, exists=True)
    assert result.item_id == 1
    assert result.exists is True
    assert result.error is None


def test_verification_result_missing():
    result = VerificationResult(item_id=2, exists=False)
    assert result.item_id == 2
    assert result.exists is False
    assert result.error is None


def test_verification_result_error():
    result = VerificationResult(item_id=3, exists=True, error="API timeout")
    assert result.item_id == 3
    assert result.exists is True
    assert result.error == "API timeout"


def test_batch_verification_result_defaults():
    result = BatchVerificationResult()
    assert result.verified == 0
    assert result.orphaned == 0
    assert result.errors == 0
    assert result.deleted == 0


# =============================================================================
# Unit Tests for Batch Key Functions
# =============================================================================


def test_get_email_batch_key():
    msg = Mock(spec=MailMessage)
    msg.email_account_id = 42

    key = get_email_batch_key(msg)

    assert key == ("mail_message", 42)


def test_get_github_batch_key():
    item = Mock(spec=GithubItem)
    item.repo_id = 123

    key = get_github_batch_key(item)

    assert key == ("github_item", 123)


# =============================================================================
# Unit Tests for VERIFIERS Registry
# =============================================================================


def test_verifiers_registry_contains_mail_message():
    assert "mail_message" in VERIFIERS
    get_batch_key, verify_fn = VERIFIERS["mail_message"]
    assert get_batch_key == get_email_batch_key
    assert verify_fn == verify_emails


def test_verifiers_registry_contains_github_item():
    assert "github_item" in VERIFIERS
    get_batch_key, verify_fn = VERIFIERS["github_item"]
    assert get_batch_key == get_github_batch_key
    assert verify_fn == verify_github_items


# =============================================================================
# Unit Tests for group_items_by_batch_key
# =============================================================================


def test_group_items_by_batch_key_single_type():
    items = [
        Mock(spec=MailMessage, type="mail_message", email_account_id=1, id=10),
        Mock(spec=MailMessage, type="mail_message", email_account_id=1, id=11),
        Mock(spec=MailMessage, type="mail_message", email_account_id=2, id=12),
    ]

    groups = group_items_by_batch_key(items)

    assert len(groups) == 2
    assert ("mail_message", 1) in groups
    assert ("mail_message", 2) in groups
    assert len(groups[("mail_message", 1)]) == 2
    assert len(groups[("mail_message", 2)]) == 1


def test_group_items_by_batch_key_mixed_types():
    email1 = Mock(spec=MailMessage, type="mail_message", email_account_id=1, id=10)
    github1 = Mock(spec=GithubItem, type="github_item", repo_id=5, id=20)

    groups = group_items_by_batch_key([email1, github1])

    assert len(groups) == 2
    assert ("mail_message", 1) in groups
    assert ("github_item", 5) in groups


def test_group_items_by_batch_key_unknown_type():
    item = Mock(type="unknown_type", id=1)

    groups = group_items_by_batch_key([item])

    assert len(groups) == 0


# =============================================================================
# Unit Tests for process_verification_results
# =============================================================================


def test_process_verification_results_verified():
    session = Mock()
    item = Mock(id=1, verification_failures=2)

    results = {1: VerificationResult(item_id=1, exists=True)}

    stats = process_verification_results(session, [item], results)

    assert stats.verified == 1
    assert stats.orphaned == 0
    assert stats.deleted == 0
    assert item.verification_failures == 0
    assert item.last_verified_at is not None


def test_process_verification_results_orphaned_below_threshold():
    session = Mock()
    item = Mock(id=1, verification_failures=0, type="mail_message")

    results = {1: VerificationResult(item_id=1, exists=False)}

    with patch.object(settings, "MAX_VERIFICATION_FAILURES", 3):
        stats = process_verification_results(session, [item], results)

    assert stats.verified == 0
    assert stats.orphaned == 1
    assert stats.deleted == 0
    assert item.verification_failures == 1


def test_process_verification_results_orphaned_at_threshold():
    session = Mock()
    item = Mock(
        id=1, verification_failures=2, type="mail_message", chunks=[], attachments=[]
    )

    results = {1: VerificationResult(item_id=1, exists=False)}

    with patch.object(settings, "MAX_VERIFICATION_FAILURES", 3):
        stats = process_verification_results(session, [item], results)

    assert stats.orphaned == 1
    assert stats.deleted == 1
    session.delete.assert_called_once_with(item)


def test_process_verification_results_error():
    session = Mock()
    item = Mock(id=1, verification_failures=1)

    results = {1: VerificationResult(item_id=1, exists=True, error="API timeout")}

    stats = process_verification_results(session, [item], results)

    assert stats.verified == 0
    assert stats.orphaned == 0
    assert stats.errors == 1
    assert item.verification_failures == 1  # unchanged


def test_process_verification_results_no_result():
    session = Mock()
    item = Mock(id=1, verification_failures=0)

    results = {}  # No result for this item

    stats = process_verification_results(session, [item], results)

    assert stats.verified == 0
    assert stats.orphaned == 0
    assert stats.errors == 0


# =============================================================================
# Unit Tests for delete_orphaned_item
# =============================================================================


def test_delete_orphaned_item_no_chunks():
    session = Mock()
    item = Mock(id=1, type="mail_message", chunks=[], attachments=[])

    result = delete_orphaned_item(item, session)

    assert result is True
    session.delete.assert_called_once_with(item)


def test_delete_orphaned_item_with_chunks():
    session = Mock()
    chunk1 = Mock(id="chunk-1", collection_name="mail")
    chunk2 = Mock(id="chunk-2", collection_name="mail")
    item = Mock(id=1, type="mail_message", chunks=[chunk1, chunk2], attachments=[])

    with patch("memory.workers.verification.qdrant") as mock_qdrant:
        mock_client = Mock()
        mock_qdrant.get_qdrant_client.return_value = mock_client

        result = delete_orphaned_item(item, session)

        assert result is True
        mock_qdrant.delete_points.assert_called_once_with(
            mock_client, "mail", ["chunk-1", "chunk-2"]
        )
        session.delete.assert_called_once_with(item)


def test_delete_orphaned_item_qdrant_failure():
    session = Mock()
    chunk1 = Mock(id="chunk-1", collection_name="mail")
    item = Mock(id=1, type="mail_message", chunks=[chunk1], attachments=[])

    with patch("memory.workers.verification.qdrant") as mock_qdrant:
        mock_qdrant.get_qdrant_client.side_effect = Exception("Qdrant down")

        with pytest.raises(Exception, match="Qdrant down"):
            delete_orphaned_item(item, session)

        # PostgreSQL delete should NOT be called if Qdrant fails
        session.delete.assert_not_called()


def test_delete_orphaned_item_with_attachments():
    """Test that deleting a MailMessage also deletes attachment vectors."""
    session = Mock()

    # Parent email chunks
    email_chunk = Mock(id="email-chunk-1", collection_name="mail")

    # Attachment 1 with chunks
    attachment1_chunk = Mock(id="attach1-chunk-1", collection_name="mail")
    attachment1 = Mock(chunks=[attachment1_chunk])

    # Attachment 2 with chunks in different collection
    attachment2_chunk = Mock(id="attach2-chunk-1", collection_name="doc")
    attachment2 = Mock(chunks=[attachment2_chunk])

    # MailMessage with attachments
    item = Mock(
        id=1,
        type="mail_message",
        chunks=[email_chunk],
        attachments=[attachment1, attachment2],
    )

    with patch("memory.workers.verification.qdrant") as mock_qdrant:
        mock_client = Mock()
        mock_qdrant.get_qdrant_client.return_value = mock_client

        result = delete_orphaned_item(item, session)

        assert result is True

        # Should delete from both collections
        assert mock_qdrant.delete_points.call_count == 2

        # Verify calls (order may vary due to dict iteration)
        calls = mock_qdrant.delete_points.call_args_list
        call_args = {call[0][1]: call[0][2] for call in calls}

        assert "mail" in call_args
        assert set(call_args["mail"]) == {"email-chunk-1", "attach1-chunk-1"}
        assert "doc" in call_args
        assert call_args["doc"] == ["attach2-chunk-1"]

        session.delete.assert_called_once_with(item)


# =============================================================================
# Unit Tests for verify_items
# =============================================================================


def test_verify_items_unknown_source_type():
    session = Mock()

    result = verify_items(session, "unknown_type", 123, [1, 2, 3])

    assert result.verified == 0
    assert result.orphaned == 0


def test_verify_items_no_items_found():
    session = Mock()
    session.query.return_value.filter.return_value.all.return_value = []

    result = verify_items(session, "mail_message", 1, [999])

    assert result.verified == 0
    assert result.orphaned == 0


# =============================================================================
# Database Integration Tests
# =============================================================================


@pytest.fixture
def email_account(db_session, test_user):
    account = EmailAccount(
        user_id=test_user.id,
        name="Test Email Account",
        email_address="test@example.com",
        password="secret",
        imap_server="imap.example.com",
        folders=["INBOX"],
        active=True,
        account_type="imap",
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def github_account(db_session, test_user):
    account = GithubAccount(
        user_id=test_user.id,
        name="Test GitHub Account",
        auth_type="pat",
        access_token="ghp_test",
        active=True,
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture
def github_repo(db_session, github_account):
    repo = GithubRepo(
        account_id=github_account.id,
        owner="testorg",
        name="testrepo",
    )
    db_session.add(repo)
    db_session.commit()
    return repo


@pytest.fixture
def mail_messages(db_session, email_account):
    now = datetime.now(timezone.utc)
    messages = [
        MailMessage(
            modality="mail",
            sha256=f"hash{i}".encode(),
            email_account_id=email_account.id,
            imap_uid=str(100 + i),
            folder="INBOX",
            embed_status="STORED",
            last_verified_at=now - timedelta(hours=48) if i == 0 else None,
        )
        for i in range(3)
    ]
    for msg in messages:
        db_session.add(msg)
    db_session.commit()
    return messages


@pytest.fixture
def github_items(db_session, github_repo):
    items = [
        GithubItem(
            modality="github",
            sha256=f"ghash{i}".encode(),
            repo_id=github_repo.id,
            repo_path=f"{github_repo.owner}/{github_repo.name}",
            number=i + 1,
            kind="issue",
            embed_status="STORED",
        )
        for i in range(2)
    ]
    for item in items:
        db_session.add(item)
    db_session.commit()
    return items


def test_select_items_for_verification_empty(db_session):
    items = select_items_for_verification(db_session)
    assert items == []


def test_select_items_for_verification_with_emails(db_session, mail_messages):
    items = select_items_for_verification(db_session, batch_size=10)

    assert len(items) == 3
    # Items without last_verified_at should come first, then oldest
    # First item has old last_verified_at, others have None
    assert any(item.last_verified_at is None for item in items)


def test_select_items_for_verification_respects_batch_size(db_session, mail_messages):
    items = select_items_for_verification(db_session, batch_size=2)
    assert len(items) == 2


def test_select_items_for_verification_filters_by_source_type(
    db_session, mail_messages, github_items
):
    items = select_items_for_verification(
        db_session, batch_size=100, source_types=["mail_message"]
    )

    assert len(items) == 3
    assert all(item.type == "mail_message" for item in items)


def test_verify_emails_account_deleted(db_session):
    """When an email account is deleted, items should be preserved (not orphaned)."""
    msg = Mock(id=1, imap_uid="123")

    results = verify_emails(db_session, 999, [msg])

    # Account deleted - preserve items with error, don't mark as orphans
    assert results[1].exists is True
    assert results[1].error == "Account deleted"


def test_verify_github_items_repo_deleted(db_session):
    """When a GitHub repo is deleted, items should be preserved (not orphaned)."""
    item = Mock(id=1, number=1)

    results = verify_github_items(db_session, 999, [item])

    # Repo deleted - preserve items with error, don't mark as orphans
    assert results[1].exists is True
    assert results[1].error == "Repo deleted"


def test_verify_github_items_account_inactive(db_session, github_repo):
    github_repo.account.active = False
    db_session.commit()

    item = Mock(id=1, number=1)

    results = verify_github_items(db_session, github_repo.id, [item])

    assert results[1].exists is True
    assert results[1].error == "Account inactive"


def test_group_items_from_database(db_session, mail_messages, github_items):
    all_items = mail_messages + github_items

    groups = group_items_by_batch_key(all_items)

    assert len(groups) == 2
    mail_key = ("mail_message", mail_messages[0].email_account_id)
    github_key = ("github_item", github_items[0].repo_id)
    assert mail_key in groups
    assert github_key in groups
    assert len(groups[mail_key]) == 3
    assert len(groups[github_key]) == 2


# =============================================================================
# Unit Tests for verify_emails with mocked IMAP/Gmail
# =============================================================================


def test_verify_emails_imap_message_exists():
    session = Mock()
    account = Mock(account_type="imap", folders=["INBOX"])
    session.get.return_value = account

    msg = Mock(id=1, imap_uid="101", folder="INBOX")

    with patch("memory.workers.email.imap_connection") as mock_imap:
        mock_conn = Mock()
        mock_imap.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_imap.return_value.__exit__ = Mock(return_value=False)

        with patch("memory.workers.email.get_folder_uids") as mock_uids:
            mock_uids.return_value = {"101", "102", "103"}

            results = verify_emails(session, 1, [msg])

    assert results[1].exists is True
    assert results[1].error is None


def test_verify_emails_imap_message_missing():
    session = Mock()
    account = Mock(account_type="imap", folders=["INBOX"])
    session.get.return_value = account

    msg = Mock(id=1, imap_uid="999", folder="INBOX")

    with patch("memory.workers.email.imap_connection") as mock_imap:
        mock_conn = Mock()
        mock_imap.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_imap.return_value.__exit__ = Mock(return_value=False)

        with patch("memory.workers.email.get_folder_uids") as mock_uids:
            mock_uids.return_value = {"101", "102", "103"}

            results = verify_emails(session, 1, [msg])

    assert results[1].exists is False


def test_verify_emails_imap_no_uid():
    session = Mock()
    account = Mock(account_type="imap", folders=["INBOX"])
    session.get.return_value = account

    msg = Mock(id=1, imap_uid=None, folder="INBOX")

    with patch("memory.workers.email.imap_connection") as mock_imap:
        mock_conn = Mock()
        mock_imap.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_imap.return_value.__exit__ = Mock(return_value=False)

        with patch("memory.workers.email.get_folder_uids") as mock_uids:
            mock_uids.return_value = {"101"}

            results = verify_emails(session, 1, [msg])

    # Items without UID are assumed to exist
    assert results[1].exists is True


def test_verify_emails_gmail_message_exists():
    session = Mock()
    account = Mock(account_type="gmail")
    session.get.return_value = account

    msg = Mock(id=1, imap_uid="msg123")

    with patch("memory.workers.email.get_gmail_message_ids") as mock_gmail:
        mock_gmail.return_value = ({"msg123", "msg456"}, None)

        results = verify_emails(session, 1, [msg])

    assert results[1].exists is True


def test_verify_emails_gmail_message_missing():
    """Test that Gmail messages not in labels AND not found by direct lookup are marked missing."""
    session = Mock()
    account = Mock(account_type="gmail")
    session.get.return_value = account

    msg = Mock(id=1, imap_uid="msg999")

    with (
        patch("memory.workers.email.get_gmail_message_ids") as mock_gmail,
        patch("memory.workers.email.gmail_message_exists") as mock_exists,
    ):
        mock_service = Mock()
        mock_gmail.return_value = ({"msg123", "msg456"}, mock_service)
        # Message not in labels, and direct lookup confirms it's deleted
        mock_exists.return_value = False

        results = verify_emails(session, 1, [msg])

    assert results[1].exists is False
    # Verify fallback was called with the message ID
    mock_exists.assert_called_once_with(mock_service, "msg999")


def test_verify_emails_gmail_message_archived():
    """Test that archived Gmail messages (not in labels but still exist) are preserved."""
    session = Mock()
    account = Mock(account_type="gmail")
    session.get.return_value = account

    msg = Mock(id=1, imap_uid="msg999")

    with (
        patch("memory.workers.email.get_gmail_message_ids") as mock_gmail,
        patch("memory.workers.email.gmail_message_exists") as mock_exists,
    ):
        mock_service = Mock()
        mock_gmail.return_value = ({"msg123", "msg456"}, mock_service)
        # Message not in labels, but direct lookup shows it still exists (archived)
        mock_exists.return_value = True

        results = verify_emails(session, 1, [msg])

    assert results[1].exists is True
    mock_exists.assert_called_once_with(mock_service, "msg999")


def test_verify_emails_api_error():
    session = Mock()
    account = Mock(account_type="imap", folders=["INBOX"])
    session.get.return_value = account

    msg = Mock(id=1, imap_uid="101", folder="INBOX")

    with patch("memory.workers.email.imap_connection") as mock_imap:
        mock_imap.return_value.__enter__ = Mock(
            side_effect=Exception("Connection refused")
        )

        results = verify_emails(session, 1, [msg])

    # On error, items are marked as existing with error
    assert results[1].exists is True
    assert results[1].error is not None
    assert "Connection refused" in results[1].error


# =============================================================================
# Unit Tests for verify_github_items with mocked GitHub client
# =============================================================================


def test_verify_github_items_issue_exists():
    session = Mock()
    repo = Mock()
    repo.owner = "testorg"
    repo.name = "testrepo"
    repo.account = Mock(active=True, auth_type="token", access_token="ghp_test")
    session.get.return_value = repo

    item = Mock(id=1, number=42, kind="issue")

    with patch("memory.common.github.GithubClient") as mock_client_cls:
        mock_client = Mock()
        mock_client.items_exist.return_value = {(42, "issue"): True}
        mock_client_cls.return_value = mock_client

        results = verify_github_items(session, 1, [item])

    assert results[1].exists is True
    mock_client.items_exist.assert_called_once_with("testorg", "testrepo", [(42, "issue")])


def test_verify_github_items_issue_deleted():
    session = Mock()
    repo = Mock()
    repo.owner = "testorg"
    repo.name = "testrepo"
    repo.account = Mock(active=True, auth_type="token", access_token="ghp_test")
    session.get.return_value = repo

    item = Mock(id=1, number=999, kind="issue")

    with patch("memory.common.github.GithubClient") as mock_client_cls:
        mock_client = Mock()
        mock_client.items_exist.return_value = {(999, "issue"): False}
        mock_client_cls.return_value = mock_client

        results = verify_github_items(session, 1, [item])

    assert results[1].exists is False


def test_verify_github_items_api_error():
    """Batch API errors mark all items as existing with error."""
    session = Mock()
    repo = Mock()
    repo.owner = "testorg"
    repo.name = "testrepo"
    repo.account = Mock(active=True, auth_type="token", access_token="ghp_test")
    session.get.return_value = repo

    item = Mock(id=1, number=42, kind="issue")

    with patch("memory.common.github.GithubClient") as mock_client_cls:
        mock_client = Mock()
        mock_client.items_exist.side_effect = Exception("Rate limited")
        mock_client_cls.return_value = mock_client

        results = verify_github_items(session, 1, [item])

    assert results[1].exists is True
    assert results[1].error is not None
    assert "Rate limited" in results[1].error


def test_verify_github_items_client_setup_error():
    session = Mock()
    repo = Mock()
    repo.owner = "testorg"
    repo.name = "testrepo"
    repo.account = Mock(active=True, auth_type="token", access_token="ghp_test")
    session.get.return_value = repo

    item = Mock(id=1, number=42, kind="issue")

    with patch("memory.common.github.GithubClient") as mock_client_cls:
        mock_client_cls.side_effect = Exception("Invalid credentials")

        results = verify_github_items(session, 1, [item])

    # Setup errors mark all as existing with error
    assert results[1].exists is True
    assert results[1].error is not None
    assert "Invalid credentials" in results[1].error


def test_verify_github_items_pr_exists():
    """Verify that PRs are checked correctly using batch API."""
    session = Mock()
    repo = Mock()
    repo.owner = "testorg"
    repo.name = "testrepo"
    repo.account = Mock(active=True, auth_type="token", access_token="ghp_test")
    session.get.return_value = repo

    item = Mock(id=1, number=42, kind="pr")

    with patch("memory.common.github.GithubClient") as mock_client_cls:
        mock_client = Mock()
        mock_client.items_exist.return_value = {(42, "pr"): True}
        mock_client_cls.return_value = mock_client

        results = verify_github_items(session, 1, [item])

    assert results[1].exists is True
    mock_client.items_exist.assert_called_once_with("testorg", "testrepo", [(42, "pr")])


def test_verify_github_items_batch():
    """Verify that multiple items are batched in a single API call."""
    session = Mock()
    repo = Mock()
    repo.owner = "testorg"
    repo.name = "testrepo"
    repo.account = Mock(active=True, auth_type="token", access_token="ghp_test")
    session.get.return_value = repo

    items = [
        Mock(id=1, number=42, kind="issue"),
        Mock(id=2, number=43, kind="pr"),
        Mock(id=3, number=44, kind="issue"),
    ]

    with patch("memory.common.github.GithubClient") as mock_client_cls:
        mock_client = Mock()
        mock_client.items_exist.return_value = {
            (42, "issue"): True,
            (43, "pr"): False,
            (44, "issue"): True,
        }
        mock_client_cls.return_value = mock_client

        results = verify_github_items(session, 1, items)

    assert results[1].exists is True
    assert results[2].exists is False
    assert results[3].exists is True
    # Verify only one API call was made
    mock_client.items_exist.assert_called_once()


def test_verify_github_items_comment_skipped():
    """Comments and project_cards can't be verified individually, assume exists."""
    session = Mock()
    repo = Mock()
    repo.owner = "testorg"
    repo.name = "testrepo"
    repo.account = Mock(active=True, auth_type="token", access_token="ghp_test")
    session.get.return_value = repo

    item = Mock(id=1, number=None, kind="comment")

    with patch("memory.common.github.GithubClient") as mock_client_cls:
        mock_client = Mock()
        mock_client_cls.return_value = mock_client

        results = verify_github_items(session, 1, [item])

    assert results[1].exists is True
    # No API call made for comments
    mock_client.item_exists.assert_not_called()


# =============================================================================
# Unit Tests for Celery Tasks
# =============================================================================


def test_verify_orphans_task_no_items():
    from memory.workers.tasks.verification import verify_orphans

    with patch("memory.workers.tasks.verification.make_session") as mock_session:
        mock_db = Mock()
        mock_session.return_value.__enter__ = Mock(return_value=mock_db)
        mock_session.return_value.__exit__ = Mock(return_value=False)

        with patch(
            "memory.workers.tasks.verification.select_items_for_verification"
        ) as mock_select:
            mock_select.return_value = []

            result = verify_orphans()

    assert result["status"] == "no_items"
    assert result["checked"] == 0


def test_verify_orphans_task_dispatches_batches():
    from memory.workers.tasks.verification import verify_orphans, verify_source_batch

    mock_items = [
        Mock(type="mail_message", email_account_id=1, id=10),
        Mock(type="mail_message", email_account_id=1, id=11),
        Mock(type="mail_message", email_account_id=2, id=12),
    ]

    with patch("memory.workers.tasks.verification.make_session") as mock_session:
        mock_db = Mock()
        mock_session.return_value.__enter__ = Mock(return_value=mock_db)
        mock_session.return_value.__exit__ = Mock(return_value=False)

        with patch(
            "memory.workers.tasks.verification.select_items_for_verification"
        ) as mock_select:
            mock_select.return_value = mock_items

            with patch.object(verify_source_batch, "delay") as mock_delay:
                mock_delay.return_value = Mock(id="task-123")

                result = verify_orphans()

    assert result["status"] == "dispatched"
    assert result["total_items"] == 3
    assert result["groups"] == 2
    assert len(result["tasks"]) == 2


def test_verify_source_batch_task():
    from memory.workers.tasks.verification import verify_source_batch

    with patch("memory.workers.tasks.verification.make_session") as mock_session:
        mock_db = Mock()
        mock_session.return_value.__enter__ = Mock(return_value=mock_db)
        mock_session.return_value.__exit__ = Mock(return_value=False)

        with patch("memory.workers.tasks.verification.verify_items") as mock_verify:
            mock_verify.return_value = BatchVerificationResult(
                verified=2, orphaned=1, errors=0, deleted=1
            )

            result = verify_source_batch("mail_message", 1, [10, 11, 12])

    assert result["status"] == "completed"
    assert result["verified"] == 2
    assert result["orphaned"] == 1
    assert result["deleted"] == 1
    mock_db.commit.assert_called_once()


def test_verify_source_batch_unknown_type():
    from memory.workers.tasks.verification import verify_source_batch

    result = verify_source_batch("unknown_type", 1, [1, 2, 3])

    assert result["status"] == "error"
    assert "No verifier" in result["error"]
