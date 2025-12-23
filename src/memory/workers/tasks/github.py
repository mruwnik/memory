"""Celery tasks for GitHub issue/PR syncing."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from memory.common import qdrant
from memory.common.celery_app import (
    app,
    SYNC_GITHUB_REPO,
    SYNC_ALL_GITHUB_REPOS,
    SYNC_GITHUB_ITEM,
)
from memory.common.db.connection import make_session
from memory.common.db.models import GithubItem
from memory.common.db.models.sources import GithubAccount, GithubRepo
from memory.parsers.github import (
    GithubClient,
    GithubCredentials,
    GithubIssueData,
)
from memory.workers.tasks.content_processing import (
    create_content_hash,
    create_task_result,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


def _build_content(issue_data: GithubIssueData) -> str:
    """Build searchable content from issue/PR data."""
    content_parts = [f"# {issue_data['title']}", issue_data["body"]]
    for comment in issue_data["comments"]:
        content_parts.append(f"\n---\n**{comment['author']}**: {comment['body']}")
    return "\n\n".join(content_parts)


def _create_github_item(
    repo: GithubRepo,
    issue_data: GithubIssueData,
) -> GithubItem:
    """Create a GithubItem from parsed issue/PR data."""
    content = _build_content(issue_data)

    # Extract project status/priority if available
    project_fields = issue_data.get("project_fields") or {}
    project_status = None
    project_priority = None
    for key, value in project_fields.items():
        key_lower = key.lower()
        if "status" in key_lower and project_status is None:
            project_status = str(value)
        elif "priority" in key_lower and project_priority is None:
            project_priority = str(value)

    repo_tags = cast(list[str], repo.tags) or []

    return GithubItem(
        modality="text",
        sha256=create_content_hash(content),
        content=content,
        kind=issue_data["kind"],
        repo_path=repo.repo_path,
        repo_id=repo.id,
        number=issue_data["number"],
        title=issue_data["title"],
        state=issue_data["state"],
        author=issue_data["author"],
        labels=issue_data["labels"],
        assignees=issue_data["assignees"],
        milestone=issue_data["milestone"],
        created_at=issue_data["created_at"],
        closed_at=issue_data["closed_at"],
        merged_at=issue_data["merged_at"],
        github_updated_at=issue_data["github_updated_at"],
        comment_count=issue_data["comment_count"],
        diff_summary=issue_data["diff_summary"],
        content_hash=issue_data["content_hash"],
        project_status=project_status,
        project_priority=project_priority,
        project_fields=project_fields if project_fields else None,
        tags=repo_tags + issue_data["labels"],
        size=len(content.encode("utf-8")),
        mime_type="text/markdown",
    )


def _needs_reindex(existing: GithubItem, new_data: GithubIssueData) -> bool:
    """Check if an existing item needs reindexing based on content changes."""
    # Compare content hash
    if existing.content_hash != new_data["content_hash"]:
        return True

    # Check if github_updated_at is newer
    existing_updated = cast(datetime | None, existing.github_updated_at)
    if existing_updated and new_data["github_updated_at"] > existing_updated:
        return True

    # Check project fields changes
    existing_fields = cast(dict | None, existing.project_fields) or {}
    new_fields = new_data.get("project_fields") or {}
    if existing_fields != new_fields:
        return True

    return False


def _update_existing_item(
    session: Any,
    existing: GithubItem,
    repo: GithubRepo,
    issue_data: GithubIssueData,
) -> dict[str, Any]:
    """Update an existing GithubItem and reindex if content changed."""
    if not _needs_reindex(existing, issue_data):
        return create_task_result(existing, "unchanged")

    logger.info(
        f"Content changed for {repo.repo_path}#{issue_data['number']}, reindexing"
    )

    # Delete old chunks from Qdrant
    chunk_ids = [str(c.id) for c in existing.chunks if c.id]
    if chunk_ids:
        try:
            client = qdrant.get_qdrant_client()
            qdrant.delete_points(client, cast(str, existing.modality), chunk_ids)
        except IOError as e:
            logger.error(f"Error deleting chunks: {e}")

    # Delete chunks from database
    for chunk in existing.chunks:
        session.delete(chunk)

    # Update the existing item with new data
    content = _build_content(issue_data)
    existing.content = content  # type: ignore
    existing.sha256 = create_content_hash(content)  # type: ignore
    existing.title = issue_data["title"]  # type: ignore
    existing.state = issue_data["state"]  # type: ignore
    existing.labels = issue_data["labels"]  # type: ignore
    existing.assignees = issue_data["assignees"]  # type: ignore
    existing.milestone = issue_data["milestone"]  # type: ignore
    existing.closed_at = issue_data["closed_at"]  # type: ignore
    existing.merged_at = issue_data["merged_at"]  # type: ignore
    existing.github_updated_at = issue_data["github_updated_at"]  # type: ignore
    existing.comment_count = issue_data["comment_count"]  # type: ignore
    existing.diff_summary = issue_data["diff_summary"]  # type: ignore
    existing.content_hash = issue_data["content_hash"]  # type: ignore
    existing.size = len(content.encode("utf-8"))  # type: ignore

    # Update project fields
    project_fields = issue_data.get("project_fields") or {}
    existing.project_fields = project_fields if project_fields else None  # type: ignore
    for key, value in project_fields.items():
        key_lower = key.lower()
        if "status" in key_lower:
            existing.project_status = str(value)  # type: ignore
        elif "priority" in key_lower:
            existing.project_priority = str(value)  # type: ignore

    # Update tags
    repo_tags = cast(list[str], repo.tags) or []
    existing.tags = repo_tags + issue_data["labels"]  # type: ignore

    session.flush()

    # Re-embed and push to Qdrant
    return process_content_item(existing, session)


def _serialize_issue_data(data: GithubIssueData) -> dict[str, Any]:
    """Serialize GithubIssueData for Celery task passing."""
    return {
        **data,
        "created_at": data["created_at"].isoformat() if data["created_at"] else None,
        "closed_at": data["closed_at"].isoformat() if data["closed_at"] else None,
        "merged_at": data["merged_at"].isoformat() if data["merged_at"] else None,
        "github_updated_at": (
            data["github_updated_at"].isoformat()
            if data["github_updated_at"]
            else None
        ),
        "comments": [
            {
                "id": c["id"],
                "author": c["author"],
                "body": c["body"],
                "created_at": c["created_at"],
                "updated_at": c["updated_at"],
            }
            for c in data["comments"]
        ],
    }


def _deserialize_issue_data(data: dict[str, Any]) -> GithubIssueData:
    """Deserialize issue data from Celery task."""
    from memory.parsers.github import parse_github_date

    return GithubIssueData(
        kind=data["kind"],
        number=data["number"],
        title=data["title"],
        body=data["body"],
        state=data["state"],
        author=data["author"],
        labels=data["labels"],
        assignees=data["assignees"],
        milestone=data["milestone"],
        created_at=parse_github_date(data["created_at"]),  # type: ignore
        closed_at=parse_github_date(data.get("closed_at")),
        merged_at=parse_github_date(data.get("merged_at")),
        github_updated_at=parse_github_date(data["github_updated_at"]),  # type: ignore
        comment_count=data["comment_count"],
        comments=data["comments"],
        diff_summary=data.get("diff_summary"),
        project_fields=data.get("project_fields"),
        content_hash=data["content_hash"],
    )


@app.task(name=SYNC_GITHUB_ITEM)
@safe_task_execution
def sync_github_item(
    repo_id: int,
    issue_data_serialized: dict[str, Any],
) -> dict[str, Any]:
    """Sync a single GitHub issue or PR."""
    issue_data = _deserialize_issue_data(issue_data_serialized)
    logger.info(f"Syncing {issue_data['kind']} from repo {repo_id}: #{issue_data['number']}")

    with make_session() as session:
        repo = session.get(GithubRepo, repo_id)
        if not repo:
            return {"status": "error", "error": "Repo not found"}

        # Check for existing item
        existing = (
            session.query(GithubItem)
            .filter(
                GithubItem.repo_path == repo.repo_path,
                GithubItem.number == issue_data["number"],
                GithubItem.kind == issue_data["kind"],
            )
            .first()
        )

        if existing:
            return _update_existing_item(session, existing, repo, issue_data)

        # Create new item
        github_item = _create_github_item(repo, issue_data)
        return process_content_item(github_item, session)


@app.task(name=SYNC_GITHUB_REPO)
@safe_task_execution
def sync_github_repo(repo_id: int, force_full: bool = False) -> dict[str, Any]:
    """Sync all issues and PRs for a repository."""
    logger.info(f"Syncing GitHub repo {repo_id}")

    with make_session() as session:
        repo = session.get(GithubRepo, repo_id)
        if not repo or not cast(bool, repo.active):
            return {"status": "error", "error": "Repo not found or inactive"}

        account = repo.account
        if not account or not cast(bool, account.active):
            return {"status": "error", "error": "Account not found or inactive"}

        now = datetime.now(timezone.utc)
        last_sync = cast(datetime | None, repo.last_sync_at)
        last_full_sync = cast(datetime | None, repo.last_full_sync_at)
        full_sync_interval = cast(int, repo.full_sync_interval)
        track_project_fields = cast(bool, repo.track_project_fields)

        # Determine if we need a full sync for project fields
        # Only do this if track_project_fields is enabled and interval > 0
        needs_full_sync = False
        if track_project_fields and full_sync_interval > 0:
            if last_full_sync is None:
                needs_full_sync = True
            elif now - last_full_sync >= timedelta(minutes=full_sync_interval):
                needs_full_sync = True

        # Check if sync is needed based on interval
        if last_sync and not force_full and not needs_full_sync:
            check_interval = cast(int, repo.check_interval)
            if now - last_sync < timedelta(minutes=check_interval):
                return {"status": "skipped_recent_check", "repo_id": repo_id}

        # Create GitHub client
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
        labels = cast(list[str], repo.labels_filter) or None
        state = cast(str | None, repo.state_filter) or "all"

        # For full syncs triggered by full_sync_interval, only sync open issues
        # (closed issues rarely have project field changes that matter)
        if needs_full_sync and not force_full:
            since = None
            state = "open"
            logger.info(f"Performing full sync of open issues for {repo.repo_path}")
        elif force_full:
            since = None
        else:
            since = last_sync

        issues_synced = 0
        prs_synced = 0
        task_ids = []

        # Sync issues
        if cast(bool, repo.track_issues):
            for issue_data in client.fetch_issues(owner, name, since, state, labels):
                # Fetch project fields if enabled
                if track_project_fields:
                    issue_data["project_fields"] = client.fetch_project_fields(
                        owner, name, issue_data["number"]
                    )

                serialized = _serialize_issue_data(issue_data)
                task_id = sync_github_item.delay(repo.id, serialized)
                task_ids.append(task_id.id)
                issues_synced += 1

        # Sync PRs
        if cast(bool, repo.track_prs):
            for pr_data in client.fetch_prs(owner, name, since, state):
                # Fetch project fields if enabled
                if track_project_fields:
                    pr_data["project_fields"] = client.fetch_pr_project_fields(
                        owner, name, pr_data["number"]
                    )

                serialized = _serialize_issue_data(pr_data)
                task_id = sync_github_item.delay(repo.id, serialized)
                task_ids.append(task_id.id)
                prs_synced += 1

        # Update sync timestamps
        repo.last_sync_at = now  # type: ignore
        if needs_full_sync or force_full:
            repo.last_full_sync_at = now  # type: ignore
        session.commit()

        return {
            "status": "completed",
            "sync_type": "full" if (needs_full_sync or force_full) else "incremental",
            "repo_id": repo_id,
            "repo_path": repo.repo_path,
            "issues_synced": issues_synced,
            "prs_synced": prs_synced,
            "task_ids": task_ids,
        }


@app.task(name=SYNC_ALL_GITHUB_REPOS)
def sync_all_github_repos() -> list[dict[str, Any]]:
    """Trigger sync for all active GitHub repos."""
    with make_session() as session:
        active_repos = (
            session.query(GithubRepo)
            .join(GithubAccount)
            .filter(GithubRepo.active, GithubAccount.active)
            .all()
        )

        results = [
            {
                "repo_id": repo.id,
                "repo_path": repo.repo_path,
                "task_id": sync_github_repo.delay(repo.id).id,
            }
            for repo in active_repos
        ]
        logger.info(f"Scheduled sync for {len(results)} active GitHub repos")
        return results
