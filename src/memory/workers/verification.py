"""
Centralized orphan detection system.

Verifies that database items still exist at their remote source (email server,
GitHub, Google Drive, etc.). Items confirmed missing are eventually deleted.

Uses graduated deletion: items must fail verification multiple times before
removal, to handle transient API failures gracefully.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Any, Callable, Sequence, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from memory.common import qdrant, settings
from memory.common.db.models import GithubItem, MailMessage, SourceItem
from memory.common.db.models.sources import EmailAccount, GithubRepo

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """Result of verifying a single item's existence."""

    item_id: int
    exists: bool
    error: str | None = None


@dataclass
class BatchVerificationResult:
    """Aggregate result of verifying a batch of items."""

    verified: int = 0  # Items confirmed to exist
    orphaned: int = 0  # Items confirmed missing at source
    errors: int = 0  # Items where verification failed (API error)
    deleted: int = 0  # Items removed from DB


# =============================================================================
# Email Verification
# =============================================================================


def get_email_batch_key(item: SourceItem) -> tuple[str, Any]:
    """Return batch key for grouping email items by account."""
    msg = cast(MailMessage, item)
    return ("mail_message", msg.email_account_id)


def verify_gmail_messages(
    service: Any,
    gmail_ids: set[str],
    items: Sequence[SourceItem],
    gmail_message_exists: Callable[[Any, str], bool],
) -> dict[int, VerificationResult]:
    """Verify Gmail messages exist using message IDs.

    Checks against monitored labels first, then falls back to individual
    message existence checks for messages that may have been archived.
    """
    results: dict[int, VerificationResult] = {}

    for item in items:
        msg = cast(MailMessage, item)
        remote_id = cast(str | None, msg.imap_uid)

        if remote_id is None:
            results[item.id] = VerificationResult(item.id, exists=True)
        elif remote_id in gmail_ids:
            results[item.id] = VerificationResult(item.id, exists=True)
        else:
            # Not in monitored labels - check if it still exists (might be archived)
            try:
                exists = gmail_message_exists(service, remote_id)
                results[item.id] = VerificationResult(item.id, exists=exists)
            except Exception as e:
                # API error - assume exists to be safe
                results[item.id] = VerificationResult(
                    item.id, exists=True, error=str(e)
                )

    return results


def verify_imap_messages(
    imap_uids: dict[str, set[str]],
    items: Sequence[SourceItem],
) -> dict[int, VerificationResult]:
    """Verify IMAP messages exist by checking folder UIDs."""
    results: dict[int, VerificationResult] = {}

    for item in items:
        msg = cast(MailMessage, item)
        remote_id = cast(str | None, msg.imap_uid)
        folder = cast(str, msg.folder) or "INBOX"

        if remote_id is None:
            results[item.id] = VerificationResult(item.id, exists=True)
        elif folder not in imap_uids:
            # Unknown folder - preserve item
            results[item.id] = VerificationResult(item.id, exists=True)
        else:
            results[item.id] = VerificationResult(
                item.id, exists=remote_id in imap_uids[folder]
            )

    return results


def verify_emails(
    session: Session,
    account_id: int,
    items: Sequence[SourceItem],
) -> dict[int, VerificationResult]:
    """Verify email messages exist in IMAP or Gmail."""
    from memory.workers.email import (
        get_folder_uids,
        get_gmail_message_ids,
        gmail_message_exists,
        imap_connection,
    )

    account = session.get(EmailAccount, account_id)
    if not account:
        return {
            item.id: VerificationResult(item.id, exists=True, error="Account deleted")
            for item in items
        }

    account_type = cast(str, account.account_type) or "imap"

    try:
        if account_type == "gmail":
            gmail_ids, service = get_gmail_message_ids(account, session)
            return verify_gmail_messages(service, gmail_ids, items, gmail_message_exists)
        else:
            imap_uids: dict[str, set[str]] = {}
            with imap_connection(account) as conn:
                folders = cast(list[str], account.folders) or ["INBOX"]
                for folder in folders:
                    imap_uids[folder] = get_folder_uids(conn, folder)
            return verify_imap_messages(imap_uids, items)

    except Exception as e:
        logger.error(f"Email verification failed for account {account_id}: {e}")
        return {
            item.id: VerificationResult(item.id, exists=True, error=str(e))
            for item in items
        }


# =============================================================================
# GitHub Verification
# =============================================================================


def get_github_batch_key(item: SourceItem) -> tuple[str, Any]:
    """Return batch key for grouping GitHub items by repo."""
    gh_item = cast(GithubItem, item)
    return ("github_item", gh_item.repo_id)


def verify_github_items(
    session: Session,
    repo_id: int,
    items: Sequence[SourceItem],
) -> dict[int, VerificationResult]:
    """Verify GitHub issues/PRs exist using batch API."""
    from memory.common.github import GithubClient, GithubCredentials

    repo = session.get(GithubRepo, repo_id)
    if not repo:
        return {
            item.id: VerificationResult(item.id, exists=True, error="Repo deleted")
            for item in items
        }

    account = repo.account
    if not account or not cast(bool, account.active):
        return {
            item.id: VerificationResult(item.id, exists=True, error="Account inactive")
            for item in items
        }

    results: dict[int, VerificationResult] = {}

    # Separate verifiable items (issues/PRs with numbers) from non-verifiable ones
    verifiable: list[tuple[SourceItem, int, str]] = []  # (item, number, kind)
    for item in items:
        gh_item = cast(GithubItem, item)
        kind = cast(str, gh_item.kind)
        number = cast(int | None, gh_item.number)

        if kind not in ("issue", "pr") or number is None:
            # Non-verifiable items (comments, project_cards) - preserve
            results[item.id] = VerificationResult(item.id, exists=True)
        else:
            verifiable.append((item, number, kind))

    if not verifiable:
        return results

    try:
        credentials = GithubCredentials(
            auth_type=cast(str, account.auth_type),
            access_token=cast(str | None, account.access_token),
            app_id=cast(int | None, account.app_id),
            installation_id=cast(int | None, account.installation_id),
            private_key=cast(str | None, account.private_key),
        )
        client = GithubClient(credentials)

        owner = cast(str, repo.owner)
        name = cast(str, repo.name)

        # Batch check all verifiable items in a single GraphQL query
        batch_items = [(number, kind) for _, number, kind in verifiable]
        existence_map = client.items_exist(owner, name, batch_items)

        for item, number, kind in verifiable:
            exists = existence_map.get((number, kind), False)
            results[item.id] = VerificationResult(item.id, exists=exists)

    except Exception as e:
        logger.error(f"GitHub verification failed for repo {repo_id}: {e}")
        # On error, preserve all verifiable items (don't mark as missing)
        for item, _, _ in verifiable:
            results[item.id] = VerificationResult(item.id, exists=True, error=str(e))

    return results


# =============================================================================
# Verifier Registry
# =============================================================================

# Maps source type to (batch_key_fn, verify_fn)
BatchKeyFn = Callable[[SourceItem], tuple[str, Any]]
VerifyFn = Callable[[Session, int, Sequence[SourceItem]], dict[int, VerificationResult]]

VERIFIERS: dict[str, tuple[BatchKeyFn, VerifyFn]] = {
    "mail_message": (get_email_batch_key, verify_emails),
    "github_item": (get_github_batch_key, verify_github_items),
}


# =============================================================================
# Core Verification Logic
# =============================================================================


def select_items_for_verification(
    session: Session,
    batch_size: int = settings.VERIFICATION_BATCH_SIZE,
    source_types: list[str] | None = None,
) -> list[SourceItem]:
    """
    Select items needing verification, prioritizing:
    1. Items never verified (last_verified_at IS NULL)
    2. Items with oldest last_verified_at

    Only selects from source types with registered verifiers.
    """
    verifiable_types = list(VERIFIERS.keys())
    if source_types:
        verifiable_types = [t for t in source_types if t in verifiable_types]

    if not verifiable_types:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=settings.VERIFICATION_INTERVAL_HOURS
    )

    stmt = (
        select(SourceItem)
        .where(
            SourceItem.type.in_(verifiable_types),
            SourceItem.embed_status == "STORED",  # Only indexed items
            (SourceItem.last_verified_at.is_(None))
            | (SourceItem.last_verified_at < cutoff),
        )
        .order_by(SourceItem.last_verified_at.asc().nullsfirst())
        .limit(batch_size)
    )

    return list(session.execute(stmt).scalars().all())


def group_items_by_batch_key(
    items: Sequence[SourceItem],
) -> dict[tuple[str, Any], list[SourceItem]]:
    """Group items by their batch key for efficient verification."""
    groups: dict[tuple[str, Any], list[SourceItem]] = {}

    for item in items:
        source_type = cast(str, item.type)
        verifier = VERIFIERS.get(source_type)
        if verifier:
            get_batch_key, _ = verifier
            key = get_batch_key(item)
            groups.setdefault(key, []).append(item)

    return groups


def collect_chunks_by_collection(
    items: Sequence[SourceItem],
) -> dict[str, list[str]]:
    """Collect all chunk IDs grouped by collection from a list of items."""
    chunks_by_collection: dict[str, list[str]] = defaultdict(list)
    for item in items:
        if item.chunks:
            for chunk in item.chunks:
                if chunk.id and chunk.collection_name:
                    chunks_by_collection[chunk.collection_name].append(str(chunk.id))
    return chunks_by_collection


def delete_orphaned_item(item: SourceItem, session: Session) -> bool:
    """
    Delete an orphaned item and its vectors from Qdrant.

    For MailMessage items, also deletes vectors from any attachments
    (which will be cascade-deleted from the database).

    Returns True if deleted, False if preserved.
    Raises exception if Qdrant deletion fails (to prevent orphaned vectors).
    """
    # Collect all items whose vectors need deletion
    items_to_delete: list[SourceItem] = [item]

    # Include attachments for email messages (cascade will delete them from DB)
    attachments = getattr(item, "attachments", None)
    if attachments:
        items_to_delete.extend(attachments)

    # Delete vectors from Qdrant first - if this fails, don't delete from DB
    chunks_by_collection = collect_chunks_by_collection(items_to_delete)

    if chunks_by_collection:
        client = qdrant.get_qdrant_client()
        for collection, chunk_ids in chunks_by_collection.items():
            qdrant.delete_points(client, collection, chunk_ids)

    session.delete(item)
    logger.info(f"Deleted orphaned {item.type} {item.id}")
    return True


def process_verification_results(
    session: Session,
    items: Sequence[SourceItem],
    results: dict[int, VerificationResult],
) -> BatchVerificationResult:
    """
    Process verification results: update timestamps, handle failures,
    delete orphans exceeding failure threshold.
    """
    now = datetime.now(timezone.utc)
    stats = BatchVerificationResult()

    for item in items:
        result = results.get(item.id)
        if result is None:
            continue

        if result.error:
            # API error - don't change failure count
            stats.errors += 1
            continue

        if result.exists:
            # Item confirmed to exist
            item.last_verified_at = now
            item.verification_failures = 0
            stats.verified += 1
        else:
            # Item not found at source
            stats.orphaned += 1
            # The `or 0` handles pre-migration rows where verification_failures is NULL
            item.verification_failures = (item.verification_failures or 0) + 1

            if item.verification_failures >= settings.MAX_VERIFICATION_FAILURES:
                try:
                    if delete_orphaned_item(item, session):
                        stats.deleted += 1
                except Exception as e:
                    logger.error(f"Failed to delete orphaned item {item.id}: {e}")
                    stats.errors += 1
            else:
                item.last_verified_at = now
                logger.info(
                    f"{item.type} {item.id} not found at source "
                    f"(failure {item.verification_failures}/{settings.MAX_VERIFICATION_FAILURES})"
                )

    return stats


def verify_items(
    session: Session,
    source_type: str,
    batch_key: Any,
    item_ids: list[int],
) -> BatchVerificationResult:
    """
    Verify a batch of items from a single source/account.

    This is the main entry point for the celery task.
    """
    verifier = VERIFIERS.get(source_type)
    if not verifier:
        logger.error(f"No verifier for {source_type}")
        return BatchVerificationResult()

    _, verify_fn = verifier

    # Use SQLAlchemy polymorphism - query SourceItem and it returns the right subclass
    items = (
        session.query(SourceItem).filter(SourceItem.id.in_(item_ids)).all()
    )

    if not items:
        return BatchVerificationResult()

    results = verify_fn(session, batch_key, items)

    return process_verification_results(session, items, results)
