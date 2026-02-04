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
    SYNC_GITHUB_PROJECTS,
)
from memory.common.db.connection import make_session
from memory.common.db.models import GithubItem, GithubPRData, Project, GithubProject
from memory.common.db.models.sources import GithubAccount, GithubRepo
from memory.common.github import (
    GithubClient,
    GithubCredentials,
    GithubIssueData,
    GithubMilestoneData,
    GithubProjectData,
    GithubPRDataDict,
    serialize_issue_data,
)
from memory.common.content_processing import (
    create_content_hash,
    create_task_result,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


def _sync_milestone(
    session: Any,
    repo: GithubRepo,
    milestone_data: GithubMilestoneData,
) -> Project:
    """Sync a milestone, creating or updating as needed."""
    existing = (
        session.query(Project)
        .filter(
            Project.repo_id == repo.id,
            Project.number == milestone_data["number"],
        )
        .first()
    )

    if existing:
        # Update existing milestone
        existing.title = milestone_data["title"]
        existing.description = milestone_data["description"]
        existing.state = milestone_data["state"]
        existing.due_on = milestone_data["due_on"]
        existing.github_updated_at = milestone_data["github_updated_at"]
        existing.closed_at = milestone_data["closed_at"]
        return existing

    # Create new milestone
    milestone = Project(
        repo_id=repo.id,
        github_id=milestone_data["github_id"],
        number=milestone_data["number"],
        title=milestone_data["title"],
        description=milestone_data["description"],
        state=milestone_data["state"],
        due_on=milestone_data["due_on"],
        github_created_at=milestone_data["github_created_at"],
        github_updated_at=milestone_data["github_updated_at"],
        closed_at=milestone_data["closed_at"],
    )
    session.add(milestone)
    session.flush()
    return milestone


def _build_content(issue_data: GithubIssueData) -> str:
    """Build searchable content from issue/PR data."""
    content_parts = [f"# {issue_data['title']}", issue_data["body"]]

    # Add regular comments
    for comment in issue_data["comments"]:
        content_parts.append(f"\n---\n**{comment['author']}**: {comment['body']}")

    # Add review comments for PRs (makes them searchable)
    pr_data = issue_data.get("pr_data")
    if pr_data and pr_data.get("review_comments"):
        content_parts.append("\n---\n## Code Review Comments\n")
        for rc in pr_data["review_comments"]:
            content_parts.append(f"**{rc['user']}** on `{rc['path']}`: {rc['body']}")

    return "\n\n".join(content_parts)


def _create_pr_data(issue_data: GithubIssueData) -> GithubPRData | None:
    """Create GithubPRData from PR-specific data if available."""
    pr_data_dict = issue_data.get("pr_data")
    if not pr_data_dict:
        return None

    pr_data = GithubPRData(
        additions=pr_data_dict.get("additions"),
        deletions=pr_data_dict.get("deletions"),
        changed_files_count=pr_data_dict.get("changed_files_count"),
        files=pr_data_dict.get("files"),
        reviews=pr_data_dict.get("reviews"),
        review_comments=pr_data_dict.get("review_comments"),
    )
    # Use the setter to compress the diff
    pr_data.diff = pr_data_dict.get("diff")
    return pr_data


def _lookup_repo_project_id(session: Any, repo: GithubRepo) -> int | None:
    """Look up the project ID for a repo (not a milestone).

    Returns the project that has repo_id set but no milestone number,
    which represents the repo-level project for access control.
    """
    project = (
        session.query(Project)
        .filter(
            Project.repo_id == repo.id,
            Project.number.is_(None),
        )
        .first()
    )
    if project:
        return cast(int, project.id)
    return None


def _create_github_item(
    repo: GithubRepo,
    issue_data: GithubIssueData,
    milestone_id: int | None = None,
    project_id: int | None = None,
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

    github_item = GithubItem(
        modality="github",
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
        milestone_id=milestone_id,
        project_id=project_id,
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

    # Create PR data if this is a PR
    if issue_data["kind"] == "pr":
        github_item.pr_data = _create_pr_data(issue_data)

    return github_item


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

    # Check if PR is missing pr_data (needs backfill)
    if new_data["kind"] == "pr" and new_data.get("pr_data") and not existing.pr_data:
        return True

    return False


def _update_existing_item(
    session: Any,
    existing: GithubItem,
    repo: GithubRepo,
    issue_data: GithubIssueData,
    milestone_id: int | None = None,
    project_id: int | None = None,
) -> dict[str, Any]:
    """Update an existing GithubItem and reindex if content changed."""
    if not _needs_reindex(existing, issue_data):
        return create_task_result(existing, "unchanged")

    logger.info(
        f"Content changed for {repo.repo_path}#{issue_data['number']}, reindexing"
    )

    # Delete old chunks from Qdrant
    # Note: chunks relationship can be None if not loaded, vs empty list if loaded but empty
    existing_chunks = existing.chunks or []
    chunk_ids = [str(c.id) for c in existing_chunks if c.id]
    if chunk_ids:
        try:
            client = qdrant.get_qdrant_client()
            qdrant.delete_points(client, cast(str, existing.modality), chunk_ids)
        except IOError as e:
            # Re-raise to fail the task - leaving stale vectors causes duplicate results
            logger.error(f"Error deleting chunks from Qdrant: {e}")
            raise

    # Delete chunks from database and clear the collection
    # (must clear before flush to avoid SQLAlchemy referencing deleted objects)
    chunks_to_delete = list(existing_chunks)
    if existing.chunks is not None:
        existing.chunks.clear()
    for chunk in chunks_to_delete:
        session.delete(chunk)

    # Update the existing item with new data
    content = _build_content(issue_data)
    existing.content = content  # type: ignore
    existing.sha256 = create_content_hash(content)  # type: ignore
    existing.title = issue_data["title"]  # type: ignore
    existing.state = issue_data["state"]  # type: ignore
    existing.labels = issue_data["labels"]  # type: ignore
    existing.assignees = issue_data["assignees"]  # type: ignore
    existing.milestone_id = milestone_id  # type: ignore
    existing.project_id = project_id  # type: ignore
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

    # Update PR data if this is a PR
    if issue_data["kind"] == "pr":
        pr_data_dict = issue_data.get("pr_data")
        if pr_data_dict:
            if existing.pr_data:
                # Update existing pr_data
                existing.pr_data.additions = pr_data_dict.get("additions")
                existing.pr_data.deletions = pr_data_dict.get("deletions")
                existing.pr_data.changed_files_count = pr_data_dict.get(
                    "changed_files_count"
                )
                existing.pr_data.files = pr_data_dict.get("files")  # type: ignore[assignment]
                existing.pr_data.reviews = pr_data_dict.get("reviews")  # type: ignore[assignment]
                existing.pr_data.review_comments = pr_data_dict.get("review_comments")  # type: ignore[assignment]
                existing.pr_data.diff = pr_data_dict.get("diff")
            else:
                # Create new pr_data
                existing.pr_data = _create_pr_data(issue_data)

    session.flush()

    # Re-embed and push to Qdrant
    return process_content_item(existing, session)


def _deserialize_issue_data(data: dict[str, Any]) -> GithubIssueData:
    """Deserialize issue data from Celery task."""
    from memory.common.github import parse_github_date

    # Reconstruct pr_data if present
    pr_data: GithubPRDataDict | None = None
    if data.get("pr_data"):
        pr_data = cast(GithubPRDataDict, data["pr_data"])

    return GithubIssueData(
        kind=data["kind"],
        number=data["number"],
        title=data["title"],
        body=data["body"],
        state=data["state"],
        author=data["author"],
        labels=data["labels"],
        assignees=data["assignees"],
        milestone_number=data.get("milestone_number"),
        created_at=parse_github_date(data["created_at"]),  # type: ignore
        closed_at=parse_github_date(data.get("closed_at")),
        merged_at=parse_github_date(data.get("merged_at")),
        github_updated_at=parse_github_date(data["github_updated_at"]),  # type: ignore
        comment_count=data["comment_count"],
        comments=data["comments"],
        diff_summary=data.get("diff_summary"),
        project_fields=data.get("project_fields"),
        content_hash=data["content_hash"],
        pr_data=pr_data,
    )


def _lookup_milestone_id(
    session: Any,
    repo: GithubRepo,
    milestone_number: int | None,
    client: "GithubClient | None" = None,
) -> int | None:
    """Look up milestone ID from the database by number, fetching from GitHub if needed.

    This is more robust than passing milestone_id directly, as it handles
    cases where the milestone might not have been committed yet or was created
    after the sync started.
    """
    if milestone_number is None:
        return None

    # Try to find in database first
    milestone = (
        session.query(Project)
        .filter(
            Project.repo_id == repo.id,
            Project.number == milestone_number,
        )
        .first()
    )
    if milestone:
        return cast(int, milestone.id)

    # Not found locally - try to fetch from GitHub
    if client is None:
        logger.warning(
            f"Milestone #{milestone_number} not found for repo {repo.id} and no client provided"
        )
        return None

    logger.info(
        f"Milestone #{milestone_number} not found locally, fetching from GitHub"
    )
    try:
        owner = cast(str, repo.owner)
        name = cast(str, repo.name)
        ms_data = client.fetch_milestone(owner, name, milestone_number)
        if ms_data:
            milestone = _sync_milestone(session, repo, ms_data)
            session.commit()
            return cast(int, milestone.id)
        else:
            logger.warning(
                f"Milestone #{milestone_number} not found on GitHub for {repo.repo_path}"
            )
            return None
    except Exception as e:
        logger.error(f"Failed to fetch milestone #{milestone_number} from GitHub: {e}")
        return None


@app.task(name=SYNC_GITHUB_ITEM)
@safe_task_execution
def sync_github_item(
    repo_id: int,
    issue_data_serialized: dict[str, Any],
) -> dict[str, Any]:
    """Sync a single GitHub issue or PR."""
    issue_data = _deserialize_issue_data(issue_data_serialized)
    logger.info(
        f"Syncing {issue_data['kind']} from repo {repo_id}: #{issue_data['number']}"
    )

    with make_session() as session:
        repo = session.get(GithubRepo, repo_id)
        if not repo:
            return {"status": "error", "error": "Repo not found"}

        # Create GitHub client for milestone lookup if needed
        client: GithubClient | None = None
        account = repo.account
        if account and cast(bool, account.active):
            credentials = GithubCredentials(
                auth_type=cast(str, account.auth_type),
                access_token=cast(str | None, account.access_token),
                app_id=cast(int | None, account.app_id),
                installation_id=cast(int | None, account.installation_id),
                private_key=cast(str | None, account.private_key),
            )
            client = GithubClient(credentials)

        # Look up milestone by number (fetches from GitHub if not in DB)
        milestone_id = _lookup_milestone_id(
            session, repo, issue_data.get("milestone_number"), client
        )

        # Determine project_id: use milestone if set, otherwise use repo project
        if milestone_id:
            project_id = milestone_id
        else:
            project_id = _lookup_repo_project_id(session, repo)

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
            return _update_existing_item(
                session, existing, repo, issue_data, milestone_id, project_id
            )

        # Create new item
        github_item = _create_github_item(repo, issue_data, milestone_id, project_id)
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

        milestones_synced = 0
        try:
            for ms_data in client.fetch_milestones(owner, name):
                _sync_milestone(session, repo, ms_data)
                milestones_synced += 1
            session.commit()
            logger.info(f"Synced {milestones_synced} milestones for {repo.repo_path}")
        except Exception as e:
            logger.warning(
                f"Failed to sync milestones for {repo.repo_path}: "
                f"{type(e).__name__}: {e}"
            )
            session.rollback()

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

                serialized = serialize_issue_data(issue_data)
                task_id = sync_github_item.delay(repo.id, serialized)  # type: ignore[attr-defined]
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

                serialized = serialize_issue_data(pr_data)
                task_id = sync_github_item.delay(repo.id, serialized)  # type: ignore[attr-defined]
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
def sync_all_github_repos(force_full: bool = False) -> list[dict[str, Any]]:
    """Trigger sync for all active GitHub repos.

    Args:
        force_full: If True, re-sync all items instead of incremental sync.
    """
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
                "task_id": sync_github_repo.delay(repo.id, force_full=force_full).id,  # type: ignore[attr-defined]
            }
            for repo in active_repos
        ]
        logger.info(
            f"Scheduled {'full' if force_full else 'incremental'} sync for {len(results)} active GitHub repos"
        )
        return results


def _sync_project(
    session: Any,
    account: GithubAccount,
    project_data: GithubProjectData,
) -> GithubProject:
    """Sync a GitHub project, creating or updating as needed."""
    existing = (
        session.query(GithubProject)
        .filter(
            GithubProject.account_id == account.id,
            GithubProject.owner_login == project_data["owner_login"],
            GithubProject.number == project_data["number"],
        )
        .first()
    )

    if existing:
        # Update existing project
        existing.node_id = project_data["node_id"]
        existing.title = project_data["title"]
        existing.short_description = project_data["short_description"]
        existing.readme = project_data["readme"]
        existing.url = project_data["url"]
        existing.public = project_data["public"]
        existing.closed = project_data["closed"]
        existing.fields = project_data["fields"]
        existing.items_total_count = project_data["items_total_count"]
        existing.github_updated_at = project_data["github_updated_at"]
        existing.last_sync_at = datetime.now(timezone.utc)
        return existing

    # Create new project
    project = GithubProject(
        account_id=account.id,
        node_id=project_data["node_id"],
        number=project_data["number"],
        owner_type=project_data["owner_type"],
        owner_login=project_data["owner_login"],
        title=project_data["title"],
        short_description=project_data["short_description"],
        readme=project_data["readme"],
        url=project_data["url"],
        public=project_data["public"],
        closed=project_data["closed"],
        fields=project_data["fields"],
        items_total_count=project_data["items_total_count"],
        github_created_at=project_data["github_created_at"],
        github_updated_at=project_data["github_updated_at"],
        last_sync_at=datetime.now(timezone.utc),
    )
    session.add(project)
    session.flush()
    return project


@app.task(name=SYNC_GITHUB_PROJECTS)
@safe_task_execution
def sync_github_projects(
    account_id: int,
    owner: str,
    is_org: bool = True,
    include_closed: bool = False,
) -> dict[str, Any]:
    """Sync all GitHub Projects for an owner (org or user).

    Args:
        account_id: ID of the GithubAccount to use for authentication
        owner: Organization or user login that owns the projects
        is_org: True if owner is an organization, False if user
        include_closed: Whether to include closed projects

    Returns:
        Dict with sync results including projects synced count
    """
    logger.info(f"Syncing GitHub projects for {owner} (is_org={is_org})")

    with make_session() as session:
        account = session.get(GithubAccount, account_id)
        if not account or not cast(bool, account.active):
            return {"status": "error", "error": "Account not found or inactive"}

        # Create GitHub client
        credentials = GithubCredentials(
            auth_type=cast(str, account.auth_type),
            access_token=cast(str | None, account.access_token),
            app_id=cast(int | None, account.app_id),
            installation_id=cast(int | None, account.installation_id),
            private_key=cast(str | None, account.private_key),
        )
        client = GithubClient(credentials)

        projects_synced = 0
        project_ids = []

        for project_data in client.list_projects(owner, is_org, include_closed):
            project = _sync_project(session, account, project_data)
            projects_synced += 1
            project_ids.append(cast(int, project.id))
            logger.info(f"Synced project: {project_data['title']} (#{project_data['number']})")

        session.commit()

        return {
            "status": "completed",
            "owner": owner,
            "is_org": is_org,
            "projects_synced": projects_synced,
            "project_ids": project_ids,
        }
