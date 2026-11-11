"""Celery tasks for GitHub issue/PR syncing."""

import hashlib
import logging
from collections.abc import Collection
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple, cast

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from memory.common.celery_app import (
    app,
    ENSURE_ALL_GITHUB_WEBHOOKS,
    ENSURE_GITHUB_WEBHOOKS,
    REFETCH_GITHUB_ITEM,
    REMOVE_GITHUB_WEBHOOKS,
    SYNC_GITHUB_REPO,
    SYNC_GITHUB_ITEM,
    SYNC_GITHUB_PROJECTS,
)
from memory.common.db.connection import make_session
from memory.common.db.models import (
    GithubIssue,
    GithubItem,
    GithubPR,
    GithubPRData,
    GithubProject,
    Project,
    is_backfill,
    item_event,
)
from memory.common.db.models.sources import (
    GithubAccount,
    GithubRepo,
    GithubUser,
    Person,
)
from memory.common.people import find_person_by_github, link_github_user_to_person
from memory.common.github import (
    GITHUB_API_URL,
    GithubClient,
    GithubIssueData,
    GithubMilestoneData,
    GithubProjectData,
    GithubPRDataDict,
    GithubQueryError,
    extract_status_priority,
    hook_config,
    not_found_message,
    org_hooks,
    parse_github_date,
    parse_required_github_date,
    repo_hooks,
    serialize_issue_data,
    webhook_url,
)
from memory.common.github.refetch import refetch_started
from memory.common.project.client import account_client
from memory.common.content_processing import (
    create_content_hash,
    delete_chunk_vectors,
    create_task_result,
    process_content_item,
)
from memory.common.jobs import tracked_task
from memory.common.push_health import GITHUB_PUSH_STATUSES, push_overdue, record_push_miss

logger = logging.getLogger(__name__)

# The order item_changes reports changes in.
CHANGES = (
    "edited",
    "labels",
    "assignees",
    "draft",
    "commits",
    "comments",
    "review",
    "checks",
    "checks_complete",
    "state",
)
# Head commit check rollups that mean every check, including ones not created yet, has finished.
FINISHED_ROLLUPS = frozenset({"SUCCESS", "FAILURE", "ERROR"})
# Items are looked up by kind, and GitHub numbers a repo's issues and PRs from
# one sequence, so a stored item never changes class.
CLASSES: dict[str, type[GithubItem]] = {"pr": GithubPR, "issue": GithubIssue}
# The incomplete_since of a sync that fetched every item: a catch-up re-fetches them all.
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def earliest(*times: datetime | None) -> datetime | None:
    return min((t for t in times if t is not None), default=None)


def own_logins(account: GithubAccount, client: GithubClient) -> set[str]:
    """Logins the account itself posts as: its comments and reviews are not changes,
    so an agent answering a PR doesn't trigger itself."""
    logins = {account.verified_login} if account.verified_login else set()
    if account.auth_type != "app":
        return logins
    try:
        slug = client.get_app_installation().get("app_slug")
    except requests.RequestException:
        logger.warning("could not read the app slug of github account %s", account.id)
        return logins
    return logins | {f"{slug}[bot]"} if slug else logins


def has_body(existing: GithubItem, body: str) -> bool:
    """Whether the stored content, as _build_content laid it out, starts with this body."""
    head = f"# {existing.title}\n\n{body}"
    content = existing.content or ""
    return content == head or content.startswith(head + "\n\n\n---\n")


def item_body_hash(data: GithubIssueData) -> str:
    """SHA-256 of the item's body, kept by metadata_only when it drops the body."""
    return data.get("body_hash") or hashlib.sha256(data["body"].encode()).hexdigest()


def body_changed(existing: GithubItem, new: GithubIssueData) -> bool:
    """Whether ``new``'s body differs from the stored one: by hash, or for items
    stored before bodies were hashed, by the stored content."""
    if existing.body_hash:
        return existing.body_hash != item_body_hash(new)
    return not has_body(existing, new["body"])


def metadata_only(data: GithubIssueData) -> GithubIssueData:
    """``data`` without bodies, comment and review text, diffs or file lists:
    what a repo whose content_mode is "metadata" stores. Ids, authors and
    timestamps stay, as item_changes and contributors detect changes by them."""
    pr = data.get("pr_data")
    stripped_pr = pr and cast(
        GithubPRDataDict,
        {
            **pr,
            "diff": None,
            "files": [],
            "reviews": [{**r, "body": None} for r in pr["reviews"]],
            "review_comments": [
                {**c, "body": "", "path": "", "diff_hunk": ""} for c in pr["review_comments"]
            ],
        },
    )
    return cast(
        GithubIssueData,
        {
            **data,
            "body_hash": item_body_hash(data),
            "body": "",
            "comments": [{**c, "body": ""} for c in data["comments"]],
            "diff_summary": None,
            "pr_data": stripped_pr,
        },
    )


def checks_complete_key(data: GithubIssueData) -> str | None:
    """"<head sha>:<rollup state>" once the head commit's checks have all finished.
    A PR with no checks has no rollup, so it never has one."""
    head_sha, state = data.get("head_sha"), data.get("checks_state")
    if not head_sha or state not in FINISHED_ROLLUPS:
        return None
    return f"{head_sha}:{state}"


class Changes(NamedTuple):
    tags: list[str]
    # Who made the comments, review and commits changes, where GitHub says.
    actors: dict[str, list[str]]


def item_changes(
    existing: GithubItem | None, new: GithubIssueData, logins: Collection[str]
) -> Changes:
    """What a sync of ``new`` changes about ``existing``, as change tags,
    and who made them.

    A head SHA, check summary or draft flag seen for the first time is not a
    change, so items stored before they were recorded don't all fire on their
    next sync. ``checks_complete`` fires when the head commit's checks finish with a
    result other than the last one recorded: a new push, or a re-run that turns
    FAILURE into SUCCESS, fires it again; a re-run with the same result doesn't.
    """
    if existing is None:
        return Changes(["new"], {})

    actors = contributors(existing, new, logins)
    head_sha, checks, draft = new.get("head_sha"), new.get("checks"), new.get("draft")
    complete = checks_complete_key(new)
    changed = {
        "edited": existing.title != new["title"] or body_changed(existing, new),
        "labels": set(existing.labels or []) != set(new["labels"]),
        "assignees": set(existing.assignees or []) != set(new["assignees"]),
        "draft": existing.draft is not None and draft is not None and draft != existing.draft,
        "commits": existing.head_sha is not None
        and head_sha is not None
        and head_sha != existing.head_sha,
        "comments": bool(actors.get("comments")),
        "review": bool(actors.get("review")),
        "checks": existing.checks is not None
        and checks is not None
        and checks != existing.checks,
        "checks_complete": existing.checks is not None
        and complete not in (None, existing.checks_complete_key),
        "state": (existing.state, existing.merged_at, existing.closed_at)
        != (new["state"], new["merged_at"], new["closed_at"]),
    }
    head_author = new.get("head_author")
    if changed["commits"] and head_author:
        actors["commits"] = [head_author]
    return Changes([c for c in CHANGES if changed[c]], actors)


def contributors(
    existing: GithubItem, new: GithubIssueData, logins: Collection[str]
) -> dict[str, list[str]]:
    """Who, other than the account's own ``logins``, wrote the comments and
    reviews that ``new`` has and ``existing`` hadn't."""
    own = {login.removesuffix("[bot]") for login in logins}
    since = cast(datetime | None, existing.github_updated_at)
    comment_authors = [
        c["author"]
        for c in new["comments"]
        if since is None or (parse_github_date(c["created_at"]) or since) > since
    ]
    review_authors: list[str] = []
    pr = new.get("pr_data")
    if pr and existing.pr_data:
        known_reviews = {r["id"] for r in existing.pr_data.reviews or []}
        known_comments = {c["id"] for c in existing.pr_data.review_comments or []}
        review_authors = [r["user"] for r in pr["reviews"] if r["id"] not in known_reviews]
        comment_authors += [
            c["user"] for c in pr["review_comments"] if c["id"] not in known_comments
        ]
    by = {"comments": comment_authors, "review": review_authors}
    others = {
        change: sorted({a for a in authors if a.removesuffix("[bot]") not in own})
        for change, authors in by.items()
    }
    return {change: authors for change, authors in others.items() if authors}


def close_open_repo_projects(session: Any, repo: GithubRepo) -> int:
    """Close all open Project rows backed by this repo (repo-level and
    milestone-level), e.g. when the repo is archived or removed on GitHub.
    Returns the count closed.
    """
    now = datetime.now(timezone.utc)
    open_children = (
        session.query(Project)
        .filter(Project.repo_id == repo.id, Project.state == "open")
        .all()
    )
    for child in open_children:
        child.state = "closed"
        child.closed_at = now
    return len(open_children)


def _sync_milestone(
    session: Any,
    repo: GithubRepo,
    milestone_data: GithubMilestoneData,
) -> Project:
    """Sync a milestone, creating or updating as needed."""
    milestone = (
        session.query(Project)
        .filter(
            Project.repo_id == repo.id,
            Project.number == milestone_data["number"],
        )
        .first()
    )

    # Find repo-level project to use as parent (for new or orphaned milestones)
    repo_project = (
        session.query(Project)
        .filter(
            Project.repo_id == repo.id,
            Project.number.is_(None),
        )
        .first()
    )

    if not milestone:
        milestone = Project(
            repo_id=repo.id,
            github_id=milestone_data["github_id"],
            number=milestone_data["number"],
            parent_id=repo_project.id if repo_project else None,
        )
        session.add(milestone)
    elif milestone.parent_id is None and repo_project:
        # Backfill parent for existing milestones that don't have one
        milestone.parent_id = repo_project.id

    # Update fields
    milestone.title = milestone_data["title"]
    milestone.description = milestone_data["description"]
    milestone.state = milestone_data["state"]
    milestone.due_on = milestone_data["due_on"]
    milestone.github_created_at = milestone_data["github_created_at"]
    milestone.github_updated_at = milestone_data["github_updated_at"]
    milestone.closed_at = milestone_data["closed_at"]

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
    """Create a GithubItem from parsed issue/PR data. An item from a repo that
    doesn't store content has none, and a sha256 of its identity instead."""
    content = _build_content(issue_data) if repo.stores_content else ""
    sha256 = create_content_hash(content) if content else create_content_hash(
        "", "github-metadata", repo.repo_path, issue_data["kind"], str(issue_data["number"])
    )

    # Extract project status/priority if available. Use the shared
    # helper so the worker path matches the MCP fetch path's
    # behaviour — in particular, both must skip non-scalar values
    # (some Projects v2 field types ship structured payloads, and
    # str()-ing those leaves dict/list reprs in the DB column).
    project_fields = issue_data.get("project_fields") or {}
    project_status, project_priority = extract_status_priority(project_fields)

    repo_tags = cast(list[str], repo.tags) or []

    github_item = CLASSES.get(issue_data["kind"], GithubItem)(
        modality="github",
        sha256=sha256,
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
        content_hash=issue_data["content_hash"] if content else None,
        body_hash=item_body_hash(issue_data),
        project_status=project_status,
        project_priority=project_priority,
        project_fields=project_fields if project_fields else None,
        tags=repo_tags + issue_data["labels"],
        size=len(content.encode("utf-8")),
        mime_type="text/markdown",
        head_sha=issue_data.get("head_sha"),
        checks=issue_data.get("checks"),
        base_sha=issue_data.get("base_sha"),
        head_ref=issue_data.get("head_ref"),
        head_repo=issue_data.get("head_repo"),
        draft=issue_data.get("draft"),
        checks_complete_key=checks_complete_key(issue_data),
    )

    # Create PR data if this is a PR
    if issue_data["kind"] == "pr":
        github_item.pr_data = _create_pr_data(issue_data)

    return github_item


def _needs_reindex(existing: GithubItem, new_data: GithubIssueData) -> bool:
    """Check if an existing item needs reindexing based on content changes."""
    # Re-syncs are the only retry path for an item whose embed failed.
    if existing.embed_status != "STORED":
        return True

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
    changes: list[str] | None = None,
    actors: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Update an existing GithubItem and reindex if content changed."""
    changes = changes or []
    existing.head_sha = issue_data.get("head_sha") or existing.head_sha  # type: ignore
    existing.base_sha = issue_data.get("base_sha") or existing.base_sha
    existing.head_ref = issue_data.get("head_ref") or existing.head_ref
    # Null once the fork is deleted, so it isn't kept like the others.
    existing.head_repo = issue_data.get("head_repo")
    new_checks = issue_data.get("checks")
    if new_checks is not None:
        existing.checks = new_checks  # type: ignore
    existing.checks_complete_key = checks_complete_key(issue_data) or existing.checks_complete_key
    draft = issue_data.get("draft")
    existing.draft = existing.draft if draft is None else draft
    # Stored before the reindex check, so a change that isn't re-embedded doesn't report again.
    existing.labels = issue_data["labels"]  # type: ignore
    existing.assignees = issue_data["assignees"]  # type: ignore
    existing.tags = (cast(list[str], repo.tags) or []) + issue_data["labels"]  # type: ignore
    existing.body_hash = item_body_hash(issue_data)

    if not repo.stores_content:
        return update_metadata(session, existing, issue_data, milestone_id, project_id, changes, actors)

    if not _needs_reindex(existing, issue_data):
        if changes:
            session.add(item_event(session, existing, changes, actors=actors))
        session.commit()
        return create_task_result(existing, "updated" if changes else "unchanged")

    logger.debug(
        f"Content changed for {repo.repo_path}#{issue_data['number']}, reindexing"
    )

    # Delete old chunks from Qdrant
    # Note: chunks relationship can be None if not loaded, vs empty list if loaded but empty
    existing_chunks = existing.chunks or []
    if existing_chunks:
        try:
            delete_chunk_vectors(existing_chunks)
        except IOError:
            # Re-raise to fail the task - leaving stale vectors causes duplicate results
            logger.exception("Error deleting chunks from Qdrant")
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
    existing.diff_summary = issue_data["diff_summary"]  # type: ignore
    existing.content_hash = issue_data["content_hash"]  # type: ignore
    existing.size = len(content.encode("utf-8"))  # type: ignore
    apply_metadata(existing, issue_data, milestone_id, project_id)

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
                existing.pr_data.files = pr_data_dict.get("files")
                existing.pr_data.reviews = pr_data_dict.get("reviews")
                existing.pr_data.review_comments = pr_data_dict.get("review_comments")
                existing.pr_data.diff = pr_data_dict.get("diff")
            else:
                # Create new pr_data
                existing.pr_data = _create_pr_data(issue_data)

    session.flush()

    # Re-embed and push to Qdrant
    return process_content_item(existing, session, changes=changes, actors=actors)


def apply_metadata(
    existing: GithubItem,
    issue_data: GithubIssueData,
    milestone_id: int | None,
    project_id: int | None,
) -> None:
    """Store the fields both content modes keep that change with the item."""
    existing.title = issue_data["title"]  # type: ignore
    existing.state = issue_data["state"]  # type: ignore
    existing.milestone_id = milestone_id  # type: ignore
    existing.project_id = project_id  # type: ignore
    existing.closed_at = issue_data["closed_at"]  # type: ignore
    existing.merged_at = issue_data["merged_at"]  # type: ignore
    existing.github_updated_at = issue_data["github_updated_at"]  # type: ignore
    existing.comment_count = issue_data["comment_count"]  # type: ignore

    project_fields = issue_data.get("project_fields") or {}
    existing.project_fields = project_fields if project_fields else None  # type: ignore
    for key, value in project_fields.items():
        key_lower = key.lower()
        if "status" in key_lower:
            existing.project_status = str(value)  # type: ignore
        elif "priority" in key_lower:
            existing.project_priority = str(value)  # type: ignore


def update_metadata(
    session: Any,
    existing: GithubItem,
    issue_data: GithubIssueData,
    milestone_id: int | None,
    project_id: int | None,
    changes: list[str],
    actors: dict[str, list[str]] | None,
) -> dict[str, Any]:
    """Update an item of a repo that stores only metadata, from metadata_only
    data. Content stored before the repo switched modes, and its vectors, stay
    as they were; the review and review comment lists are replaced, as the next
    sync tells new reviews by their ids."""
    apply_metadata(existing, issue_data, milestone_id, project_id)
    pr = issue_data.get("pr_data")
    if pr and existing.pr_data:
        existing.pr_data.additions = pr["additions"]
        existing.pr_data.deletions = pr["deletions"]
        existing.pr_data.changed_files_count = pr["changed_files_count"]
        existing.pr_data.reviews = pr["reviews"]
        existing.pr_data.review_comments = pr["review_comments"]
    elif pr:
        existing.pr_data = _create_pr_data(issue_data)
    if changes:
        session.add(item_event(session, existing, changes, actors=actors))
    session.commit()
    return create_task_result(existing, "updated" if changes else "unchanged")


def _deserialize_issue_data(data: dict[str, Any]) -> GithubIssueData:
    """Deserialize issue data from Celery task."""
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
        author_id=data.get("author_id"),
        labels=data["labels"],
        assignees=data["assignees"],
        milestone_number=data.get("milestone_number"),
        created_at=parse_required_github_date(data["created_at"]),
        closed_at=parse_github_date(data.get("closed_at")),
        merged_at=parse_github_date(data.get("merged_at")),
        github_updated_at=parse_required_github_date(data["github_updated_at"]),
        comment_count=data["comment_count"],
        comments=data["comments"],
        diff_summary=data.get("diff_summary"),
        project_fields=data.get("project_fields"),
        content_hash=data["content_hash"],
        pr_data=pr_data,
        head_sha=data.get("head_sha"),
        checks=data.get("checks"),
        base_sha=data.get("base_sha"),
        head_ref=data.get("head_ref"),
        head_repo=data.get("head_repo"),
        draft=data.get("draft"),
        checks_state=data.get("checks_state"),
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
    except Exception:
        logger.exception(f"Failed to fetch milestone #{milestone_number} from GitHub")
        return None


def _resolve_author_person(
    session: Any, login: str | None, github_id: int | None
) -> int | None:
    """Resolve a GitHub author login to a Person id, creating records as needed.

    Returns the linked Person id, or None for bot accounts / unresolvable
    logins. On first sighting of a login we find-or-create a Person and, when
    the numeric id is known (carried from the GraphQL sync — no extra API
    call), record the relational ``GithubUser`` link. Duplicate People are
    expected to be consolidated later via the people-merge tooling.
    """
    # "ghost" is GitHub's sentinel login for deleted accounts (the parser's
    # default when author is null); bots aren't real people. Neither should
    # collapse every such item onto one shared Person.
    if not login or login == "ghost" or login.endswith("[bot]"):
        return None

    # The numeric id is the stable identity; a login can be renamed or
    # recycled to a different account.
    by_id = session.get(GithubUser, github_id) if github_id else None
    if by_id is not None and by_id.person is not None:
        if by_id.username != login:
            link_github_user_to_person(session, by_id.person, by_id.id, login)
        return by_id.person_id

    github_user = (
        session.query(GithubUser).filter(GithubUser.username == login).one_or_none()
    )
    recycled = (
        github_user is not None and github_id is not None and github_user.id != github_id
    )
    if github_user is not None and github_user.person_id is not None and not recycled:
        return github_user.person_id

    # A recycled login is a different human, so nothing keyed on the login
    # (the stale link or a contact_info handle) identifies them.
    person = None if recycled else find_person_by_github(session, login)
    if person is None:
        # Two concurrent first-sighting syncs for the same login can both reach
        # here and both insert; the unique constraints on people.identifier and
        # github_users.username make the second one fail the task rather than
        # create a duplicate. That's intentional — a later sync (or retry) finds
        # the now-existing row. The constraints protect integrity; the race only
        # costs one task a re-run.
        person = Person(
            identifier=f"github:{login}:{github_id}" if recycled else f"github:{login}",
            display_name=login,
            aliases=[login],
            contact_info={"github": login},
        )
        session.add(person)
        session.flush()

    link_id = github_id or (github_user.id if github_user else None)
    if link_id:
        link_github_user_to_person(session, person, link_id, login)
    return person.id


def _apply_author_person(
    session: Any, item: GithubItem, issue_data: GithubIssueData
) -> None:
    """Set ``item.author_person_id`` when not already resolved."""
    if item.author_person_id is not None:
        return
    item.author_person_id = _resolve_author_person(
        session, issue_data.get("author"), issue_data.get("author_id")
    )


@app.task(name=SYNC_GITHUB_ITEM)
@tracked_task
def sync_github_item(
    repo_id: int,
    issue_data_serialized: dict[str, Any],
    own_logins: list[str] | None = None,
) -> dict[str, Any]:
    """Sync a single GitHub issue or PR found by polling."""
    return sync_item(
        repo_id, _deserialize_issue_data(issue_data_serialized), own_logins or [], polled=True
    )


def matches_repo_filters(repo: GithubRepo, data: GithubIssueData) -> bool:
    """Whether polling would have fetched this item: the state filter, and for issues the labels."""
    labels = cast(list[str], repo.labels_filter) or []
    if data["kind"] == "issue" and labels and not set(labels) & set(data["labels"]):
        return False
    return repo.state_filter in (None, "", "all", data["state"])


@app.task(name=REFETCH_GITHUB_ITEM)
@tracked_task
def refetch_github_item(repo_id: int, number: int, kind: str) -> dict[str, Any]:
    """Fetch and sync the one issue or PR a webhook event named."""
    refetch_started(repo_id, number)
    with make_session() as session:
        repo = session.get(GithubRepo, repo_id)
        account = repo.account if repo else None
        if not repo or not repo.active or not account or not account.active:
            return {"status": "error", "error": "Repo not found or inactive"}
        if not (repo.track_prs if kind == "pr" else repo.track_issues):
            return {"status": "skipped", "repo_id": repo_id, "number": number}

        client = account_client(account)
        owner, name = cast(str, repo.owner), cast(str, repo.name)
        try:
            data = (
                client.fetch_pr(owner, name, number, with_diff=repo.stores_content)
                if kind == "pr"
                else client.fetch_issue(owner, name, number)
            )
        except GithubQueryError as e:
            # The repo is where the user looks; a later sync clears it once GitHub answers.
            repo.record_sync(e)
            session.commit()
            raise
        if data is None:
            # Deleted or transferred after its event was sent.
            return {"status": "not_found", "repo_id": repo_id, "number": number}
        if unreadable := client.unreadable_message(repo.repo_path):
            repo.record_sync(unreadable, partial=True)
            repo.incomplete_since = earliest(repo.incomplete_since, data["github_updated_at"])
        if not matches_repo_filters(repo, data):
            return {"status": "skipped", "repo_id": repo_id, "number": number}
        if repo.track_project_fields:
            fetch_fields = (
                client.fetch_pr_project_fields if kind == "pr" else client.fetch_project_fields
            )
            data["project_fields"] = fetch_fields(owner, name, number)
        logins = own_logins(account, client)
    return sync_item(repo_id, data, logins)


def push_missed(repo: GithubRepo, data: GithubIssueData, changes: list[str]) -> bool:
    """Whether a poll found a change the repo's webhook should have delivered:
    one made since the hook was last confirmed installed. Check results don't
    move an item's updated_at, so their age can't be told and they don't count."""
    if repo.webhook_status not in GITHUB_PUSH_STATUSES:
        return False
    if not set(changes) - {"checks", "checks_complete"}:
        return False
    return push_overdue(data["github_updated_at"], repo.webhook_checked_at)


def lock_github_item(session: DBSession, repo_path: str, kind: str, number: int) -> None:
    """Hold this item's lock until the transaction ends, so a poll and a webhook
    syncing it at once can't both find it missing and insert it twice."""
    key = func.hashtextextended(f"github_item:{repo_path}:{kind}:{number}", 0)
    session.execute(select(func.pg_advisory_xact_lock(key)))


def sync_item(
    repo_id: int, issue_data: GithubIssueData, logins: Collection[str], polled: bool = False
) -> dict[str, Any]:
    logger.debug(
        f"Syncing {issue_data['kind']} from repo {repo_id}: #{issue_data['number']}"
    )

    with make_session() as session:
        repo = session.get(GithubRepo, repo_id)
        if not repo:
            return {"status": "error", "error": "Repo not found"}

        # Create GitHub client for milestone lookup if needed
        account = repo.account
        client = account_client(account) if account and account.active else None

        # Look up milestone by number (fetches from GitHub if not in DB)
        milestone_id = _lookup_milestone_id(
            session, repo, issue_data.get("milestone_number"), client
        )

        # Determine project_id: use milestone if set, otherwise use repo project
        if milestone_id:
            project_id = milestone_id
        else:
            project_id = _lookup_repo_project_id(session, repo)

        lock_github_item(session, repo.repo_path, issue_data["kind"], issue_data["number"])
        existing = (
            session.query(GithubItem)
            .filter(
                GithubItem.repo_path == repo.repo_path,
                GithubItem.number == issue_data["number"],
                GithubItem.kind == issue_data["kind"],
            )
            .first()
        )

        changes, actors = item_changes(existing, issue_data, logins)
        if not repo.stores_content:
            issue_data = metadata_only(issue_data)
        if polled and push_missed(repo, issue_data, changes):
            record_push_miss(GithubRepo, repo.id, repo.account.user_id, f"GitHub repo {repo.repo_path}")
        if existing:
            _apply_author_person(session, existing, issue_data)
            return _update_existing_item(
                session, existing, repo, issue_data, milestone_id, project_id, changes, actors
            )

        # Create new item
        github_item = _create_github_item(repo, issue_data, milestone_id, project_id)
        _apply_author_person(session, github_item, issue_data)
        backfill = is_backfill(issue_data["github_updated_at"])
        return process_content_item(github_item, session, changes=changes, backfill=backfill)


@app.task(name=SYNC_GITHUB_REPO)
@tracked_task
def sync_github_repo(repo_id: int, force_full: bool = False) -> dict[str, Any]:
    """Sync all issues and PRs for a repository.

    Note: every invocation makes a `client.get_repo(owner, name)` REST call
    up front to detect archived/missing repos. That's one extra REST hit
    per sync cycle on top of the per-issue/PR/milestone fetches. For a
    fleet of N tracked repos that's N extra calls per cycle. Acceptable
    for current scale; consider plumbing the fetched repo metadata down
    to avoid re-fetching, or doing the archival check on a slower cadence,
    if API rate limits become a concern.
    """
    try:
        return sync_repo(repo_id, force_full)
    except Exception as e:
        # Own session, so the failure survives the failed sync's rollback.
        with make_session() as session:
            if repo := session.get(GithubRepo, repo_id):
                repo.record_sync(e)
                session.commit()
        raise


def sync_repo(repo_id: int, force_full: bool) -> dict[str, Any]:
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

        client = account_client(account)

        owner = cast(str, repo.owner)
        name = cast(str, repo.name)

        # Check if repo is archived or missing on GitHub before doing any sync work
        repo_data = client.get_repo(owner, name)
        if repo_data is None:
            # Not deactivated: a repo the App or token lost access to looks just like a deleted one.
            raise GithubQueryError(not_found_message(cast(str, account.auth_type), repo.repo_path))
        if repo_data.get("archived", False):
            logger.warning(f"Repo {repo.repo_path} is archived on GitHub")
            repo.active = False  # type: ignore
            close_open_repo_projects(session, repo)
            session.commit()
            return {
                "status": "skipped_repo_archived",
                "repo_id": repo_id,
                "repo_path": repo.repo_path,
            }

        labels = cast(list[str], repo.labels_filter) or None
        state = cast(str | None, repo.state_filter) or "all"

        milestones_synced = 0
        milestones_error = None
        try:
            for ms_data in client.fetch_milestones(owner, name):
                _sync_milestone(session, repo, ms_data)
                milestones_synced += 1
            session.commit()
            logger.info(f"Synced {milestones_synced} milestones for {repo.repo_path}")
        except GithubQueryError as e:
            # Issues and PRs don't need milestones to sync; the repo says what to grant.
            milestones_error = str(e)
            session.rollback()
        except Exception as e:
            logger.warning(
                f"Failed to sync milestones for {repo.repo_path}: "
                f"{type(e).__name__}: {e}"
            )
            session.rollback()

        # For full syncs triggered by full_sync_interval, only sync open issues
        # (closed issues rarely have project field changes that matter)
        open_sweep = needs_full_sync and not force_full
        if open_sweep:
            since = None
            state = "open"
            logger.info(f"Performing full sync of open issues for {repo.repo_path}")
        elif force_full:
            since = None
        else:
            since = last_sync

        logins = sorted(own_logins(account, client))
        task_ids: list[str] = []

        def dispatch(since: datetime | None, state: str) -> tuple[int, int]:
            """Queue a sync of each issue and PR updated since then; how many of each."""
            issues = prs = 0
            if cast(bool, repo.track_issues):
                for issue_data in client.fetch_issues(owner, name, since, state, labels):
                    if track_project_fields:
                        issue_data["project_fields"] = client.fetch_project_fields(
                            owner, name, issue_data["number"]
                        )
                    task_ids.append(sync_github_item.delay(repo.id, serialize_issue_data(issue_data), logins).id)
                    issues += 1
            if cast(bool, repo.track_prs):
                for pr_data in client.fetch_prs(owner, name, since, state, with_diff=repo.stores_content):
                    if track_project_fields:
                        pr_data["project_fields"] = client.fetch_pr_project_fields(
                            owner, name, pr_data["number"]
                        )
                    task_ids.append(sync_github_item.delay(repo.id, serialize_issue_data(pr_data), logins).id)
                    prs += 1
            return issues, prs

        issues_synced, prs_synced = dispatch(since, state)
        if open_sweep and last_sync:
            # The sweep skips what closed since the last sync, and the next sync starts from now.
            issues, prs = dispatch(last_sync, cast(str | None, repo.state_filter) or "all")
            issues_synced, prs_synced = issues_synced + issues, prs_synced + prs
        unreadable = client.unreadable_message(repo.repo_path)
        catch_up = cast(datetime | None, repo.incomplete_since)
        # Items stored while fields were withheld; a forced full sync already re-fetched them.
        if catch_up and not unreadable and not force_full:
            logger.info(f"Re-fetching {repo.repo_path} since {catch_up.isoformat()}: earlier syncs missed fields")
            issues, prs = dispatch(catch_up, cast(str | None, repo.state_filter) or "all")
            issues_synced, prs_synced = issues_synced + issues, prs_synced + prs
            unreadable = client.unreadable_message(repo.repo_path)

        # The cursors move on regardless: holding them back would re-fetch
        # everything every sync until access is granted.
        repo.last_sync_at = now  # type: ignore
        if needs_full_sync or force_full:
            repo.last_full_sync_at = now  # type: ignore
        # The next open-only sweep re-fetches what this one did; only what an
        # incremental or forced sync fetched needs catching up.
        fetched_from = since or ((last_sync or now) if open_sweep else EPOCH)
        # A refetch may have marked items incomplete while this sync ran.
        session.refresh(repo, ["incomplete_since"])
        marked = repo.incomplete_since if repo.incomplete_since != catch_up else None
        repo.incomplete_since = earliest(repo.incomplete_since, fetched_from) if unreadable else marked
        error = "; ".join(filter(None, [milestones_error, unreadable])) or None
        repo.record_sync(error, partial=True)
        session.commit()

        return {
            "status": "incomplete" if error else "completed",
            "error": error,
            "sync_type": "full" if (needs_full_sync or force_full) else "incremental",
            "repo_id": repo_id,
            "repo_path": repo.repo_path,
            "issues_synced": issues_synced,
            "prs_synced": prs_synced,
            "task_ids": task_ids,
        }


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
@tracked_task
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

        client = account_client(account)

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


def hook_failure(error: requests.RequestException) -> str:
    """The webhook_status for a failed hook call. GitHub answers 404 to a token
    that can see the repo but lacks the hooks permission."""
    code = status_code(error)
    if code in (403, 404):
        return "missing_permission"
    return "rejected" if code == 422 else "error"


def our_hook(hooks: list[dict[str, Any]], url: str) -> dict[str, Any] | None:
    # A hook added by hand may carry a trailing slash.
    return next((h for h in hooks if (h.get("config") or {}).get("url", "").rstrip("/") == url), None)


def status_code(error: requests.RequestException) -> int | None:
    return error.response.status_code if error.response is not None else None


def ensure_hook(client: GithubClient, hooks_url: str, config: dict[str, str]) -> None:
    """Update the hook pointing at us, or add one. Hooks on other URLs are never touched."""
    ours = our_hook(client.list_hooks(hooks_url), config["url"])
    if ours:
        client.update_hook(hooks_url, ours["id"], config)
    else:
        client.create_hook(hooks_url, config)


def ensure_repo_hook(client: GithubClient, repo: GithubRepo, config: dict[str, str]) -> str:
    try:
        ensure_hook(client, repo_hooks(repo.owner, repo.name), config)
    except requests.RequestException as e:
        status = hook_failure(e)
        logger.warning("github webhook install failed repo=%s status=%s", repo.id, status)
        return status
    return "installed"


def ensure_org_hook(client: GithubClient, org: str, config: dict[str, str]) -> str:
    """'org_hook' once a hook on the org delivers to us, else why not."""
    try:
        ensure_hook(client, org_hooks(org), config)
    except requests.RequestException as e:
        status = hook_failure(e)
        logger.warning("github org webhook install failed status=%s", status)
        return status
    return "org_hook"


def drop_hook(client: GithubClient, hooks_url: str, url: str) -> bool:
    """Delete our hook at ``hooks_url``, if there is one, best effort."""
    try:
        ours = our_hook(client.list_hooks(hooks_url), url)
        if ours:
            client.delete_hook(hooks_url, ours["id"])
    except requests.RequestException:
        logger.warning("github webhook removal failed")
        return False
    return ours is not None


def install_owner_hooks(client: GithubClient, repos: list[GithubRepo], config: dict[str, str]) -> dict[int, str]:
    """One hook on an org covers all its repos; a personal account has no account-level
    hooks, so each of its repos gets one. An org's repos never get their own, which
    would duplicate an org hook added by hand."""
    try:
        owner_type = client.owner_type(repos[0].owner)
    except requests.RequestException:
        logger.warning("github owner type lookup failed repo=%s", repos[0].id)
        return {r.id: "error" for r in repos}
    if owner_type == "User":
        return {r.id: ensure_repo_hook(client, r, config) for r in repos}
    status = ensure_org_hook(client, repos[0].owner, config)
    if status == "org_hook":
        for repo in repos:
            drop_hook(client, repo_hooks(repo.owner, repo.name), config["url"])
    return {r.id: status for r in repos}


def by_owner(repos: list[GithubRepo]) -> dict[str, list[GithubRepo]]:
    owners: dict[str, list[GithubRepo]] = {}
    for repo in repos:
        owners.setdefault(repo.owner.lower(), []).append(repo)
    return owners


def install_hooks(
    account: GithubAccount, repos: list[GithubRepo], config: dict[str, str]
) -> dict[int, str]:
    try:
        client = account_client(account)
    except requests.RequestException:
        logger.warning("github client unavailable for webhook install account=%s", account.id)
        return {r.id: "error" for r in repos}
    return {
        repo_id: status
        for owned in by_owner(repos).values()
        for repo_id, status in install_owner_hooks(client, owned, config).items()
    }


@app.task(name=ENSURE_GITHUB_WEBHOOKS)
@tracked_task
def ensure_github_webhooks(account_id: int) -> dict[str, Any]:
    """Make every active repo of the account deliver its events to us, signed with the current secret."""
    with make_session() as session:
        account = session.get(GithubAccount, account_id)
        secret = account.webhook_secret if account and account.active else None
        if not account or not secret:
            return {"status": "skipped", "account_id": account_id}
        repos = [r for r in account.repos if r.active]
        statuses = install_hooks(account, repos, hook_config(account.id, secret))
        now = datetime.now(timezone.utc)
        for repo in repos:
            repo.webhook_status = statuses[repo.id]
            repo.webhook_checked_at = now
        session.commit()
    return {"status": "completed", "account_id": account_id, "repos": statuses}


@app.task(name=ENSURE_ALL_GITHUB_WEBHOOKS)
def ensure_all_github_webhooks() -> dict[str, Any]:
    with make_session() as session:
        ids = [
            id
            for (id,) in session.query(GithubAccount.id).filter(
                GithubAccount.active.is_(True),
                GithubAccount.webhook_secret_encrypted.isnot(None),
            )
        ]
    for id in ids:
        app.send_task(ENSURE_GITHUB_WEBHOOKS, args=[id])
    return {"status": "queued", "accounts": ids}


@app.task(name=REMOVE_GITHUB_WEBHOOKS)
@tracked_task
def remove_github_webhooks(account_id: int) -> dict[str, Any]:
    """Delete the org and repo hooks pointing at the account, best effort. Hooks on other URLs stay."""
    with make_session() as session:
        account = session.get(GithubAccount, account_id)
        # A secret generated since the removal was queued wants the hooks kept.
        if not account or account.webhook_secret:
            return {"status": "skipped", "account_id": account_id}
        url = webhook_url(account.id)
        owners = {r.owner.lower(): r.owner for r in account.repos}.values()
        targets = [org_hooks(o) for o in owners] + [repo_hooks(r.owner, r.name) for r in account.repos]
        try:
            client = account_client(account)
        except requests.RequestException:
            logger.warning("github client unavailable for webhook removal account=%s", account.id)
            client = None
        removed = [t.removeprefix(GITHUB_API_URL) for t in targets if client and drop_hook(client, t, url)]
        now = datetime.now(timezone.utc)
        for repo in account.repos:
            repo.webhook_status = None
            repo.webhook_checked_at = now
        session.commit()
    return {"status": "completed", "account_id": account_id, "removed": removed}
