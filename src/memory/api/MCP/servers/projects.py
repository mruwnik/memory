"""MCP subserver for project management.

Note on error response patterns:
Each MCP tool returns a specific response schema. When returning errors,
we include the expected fields with null/empty values to avoid breaking
clients that expect certain keys.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Literal, cast

from fastmcp import FastMCP
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from memory.common.github import GithubClient, GithubCredentials

from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common.access_control import (
    filter_projects_query,
    get_user_team_ids,
    has_admin_scope,
    user_can_access_project,
)
from memory.common.scopes import SCOPE_PROJECTS, SCOPE_PROJECTS_WRITE
from memory.common.db.connection import make_session
from memory.common.db.models import GithubAccount, GithubRepo, Project, Team
from memory.common.db.models.sources import Person
from memory.common.db.models.journal import JournalEntry, build_journal_access_filter
from memory.api.MCP.servers.github_helpers import (
    SyncResult,
    ensure_github_repo,
    get_github_client,
    sync_repo_teams_inbound,
    sync_repo_teams_outbound,
)

logger = logging.getLogger(__name__)

projects_mcp = FastMCP("memory-projects")


# ============== Validation Helpers ==============


def validate_teams_for_project(
    session: Any,
    user: Any,
    team_ids: list[int] | None,
    require_non_empty: bool = True,
) -> tuple[list[Team] | None, dict | None]:
    """Validate team_ids for project operations.

    Args:
        session: Database session
        user: Current user
        team_ids: List of team IDs to validate
        require_non_empty: If True, empty team_ids returns an error

    Returns:
        Tuple of (teams list or None, error dict or None).
        If error dict is returned, teams will be None.
    """
    if require_non_empty and not team_ids:
        return None, {
            "error": "team_ids must be a non-empty list for new projects",
            "project": None,
        }

    if not team_ids:
        return [], None

    # Validate all specified teams exist
    teams = session.query(Team).filter(Team.id.in_(team_ids)).all()
    found_ids = {t.id for t in teams}
    missing_ids = set(team_ids) - found_ids

    if missing_ids:
        return None, {
            "error": f"Invalid team_ids: teams {missing_ids} do not exist",
            "project": None,
        }

    # Non-admins must be a member of at least one specified team
    if not has_admin_scope(user):
        user_teams = get_user_team_ids(session, user)
        accessible_team_ids = set(team_ids) & user_teams
        if not accessible_team_ids:
            return None, {
                "error": "You do not have access to any of the specified teams",
                "project": None,
            }

    return teams, None


def validate_parent_project(
    session: Any,
    user: Any,
    parent_id: int | None,
) -> dict | None:
    """Validate that a parent project exists and user has access.

    Args:
        session: Database session
        user: Current user
        parent_id: Parent project ID to validate (None is valid)

    Returns:
        Error dict if validation fails, None if valid
    """
    if parent_id is None:
        return None

    parent = session.get(Project, parent_id)
    if not parent:
        return {"error": f"Parent project not found: {parent_id}", "project": None}

    if not has_admin_scope(user) and not user_can_access_project(
        session, user, parent_id
    ):
        return {"error": f"Parent project not found: {parent_id}", "project": None}

    return None


def generate_negative_project_id(
    session: Any, max_retries: int = 3
) -> tuple[int | None, dict | None]:
    """Generate a unique negative project ID with retry logic.

    Uses negative IDs to avoid collision with GitHub milestone IDs.
    Retries on collision to handle concurrent inserts.

    Args:
        session: Database session
        max_retries: Number of retries on collision

    Returns:
        Tuple of (generated ID or None, error dict or None)
    """
    for attempt in range(max_retries):
        max_negative_id = (
            session.query(func.min(Project.id)).filter(Project.id < 0).scalar()
        )
        new_id = (max_negative_id or 0) - 1

        # Test uniqueness via savepoint
        savepoint = session.begin_nested()
        try:
            # Just check if the ID exists - actual insert will be done by caller
            existing = session.query(Project.id).filter(Project.id == new_id).first()
            savepoint.rollback()  # We don't want to change anything
            if not existing:
                return new_id, None
        except IntegrityError:
            savepoint.rollback()

        if attempt == max_retries - 1:
            return None, {
                "error": "Failed to generate unique project ID after retries",
                "project": None,
            }

    return None, {"error": "Failed to generate unique project ID", "project": None}


def create_project_with_retry(
    session: Any,
    teams: list[Team],
    max_retries: int = 3,
    **project_kwargs: Any,
) -> tuple[Project | None, dict | None]:
    """Create a project with negative ID, retrying on collision.

    Args:
        session: Database session
        teams: Teams to assign to the project
        max_retries: Number of retries on ID collision
        **project_kwargs: Arguments to pass to Project constructor (except id)

    Returns:
        Tuple of (created Project or None, error dict or None)
    """
    for attempt in range(max_retries):
        max_negative_id = (
            session.query(func.min(Project.id)).filter(Project.id < 0).scalar()
        )
        new_id = (max_negative_id or 0) - 1

        project = Project(id=new_id, **project_kwargs)

        # Use savepoint to avoid rolling back the entire transaction
        savepoint = session.begin_nested()
        try:
            session.add(project)
            session.flush()

            # Assign teams
            for team in teams:
                project.teams.append(team)

            return project, None
        except IntegrityError:
            savepoint.rollback()
            if attempt == max_retries - 1:
                return None, {
                    "error": "Failed to generate unique project ID after retries",
                    "project": None,
                }
            continue

    return None, {"error": "Failed to create project", "project": None}


def sync_milestone_due_date(
    project: Project,
    new_due_on: datetime | None,
) -> dict[str, Any] | None:
    """Sync a project's due date to its GitHub milestone.

    This function syncs the due_on date to GitHub for milestone-backed projects.
    It should be called BEFORE updating the local database to ensure consistency
    (if GitHub fails, we don't leave the local database in an inconsistent state).

    Args:
        project: The project to sync (must have repo and milestone number)
        new_due_on: The new due date to set, or None to clear it

    Returns:
        Error dict if sync failed, None if successful or sync not needed
    """
    github_repo = project.repo
    if not github_repo:
        return None

    account = github_repo.account
    if not account:
        logger.warning(
            f"Cannot sync due_on to GitHub: repo {github_repo.owner}/{github_repo.name} "
            "has no associated account"
        )
        return None

    # Format for GitHub API (None clears the date)
    github_due_on: str | None = None
    if new_due_on is not None:
        utc_due_on = new_due_on.astimezone(timezone.utc)
        github_due_on = utc_due_on.strftime("%Y-%m-%dT%H:%M:%SZ")

    credentials = GithubCredentials(
        auth_type=account.auth_type,
        access_token=account.access_token,
        app_id=account.app_id,
        installation_id=account.installation_id,
        private_key=account.private_key,
    )
    client = GithubClient(credentials)
    try:
        result = client.update_milestone(
            owner=github_repo.owner,
            repo=github_repo.name,
            milestone_number=project.number,
            due_on=github_due_on,
        )
        if result is None:
            return {
                "error": "Failed to update GitHub milestone due date",
                "project": None,
            }
    except Exception as e:
        logger.exception("Failed to sync due date to GitHub")
        return {
            "error": f"Failed to sync due date to GitHub: {e}",
            "project": None,
        }

    return None


def find_existing_project_by_repo(
    session: Any,
    repo_path: str,
    milestone_title: str | None = None,
) -> Project | None:
    """Find an existing project by repo path and optional milestone title.

    Args:
        session: Database session
        repo_path: GitHub repo path (e.g., "owner/name")
        milestone_title: Optional milestone title to match. The match is
            case-sensitive and exact - if the local project title was
            modified after creation, it won't match the GitHub milestone.

    Returns:
        Existing Project if found, None otherwise
    """
    if "/" not in repo_path:
        return None

    owner, repo_name = repo_path.split("/", 1)

    # Look up repo in database
    repo_obj = (
        session.query(GithubRepo)
        .filter(GithubRepo.owner == owner, GithubRepo.name == repo_name)
        .first()
    )
    if not repo_obj:
        return None

    if milestone_title is not None:
        # Look for milestone project - match by title since we don't have the number yet
        # This is a best-effort match; if title was overridden, it won't match
        return (
            session.query(Project)
            .filter(
                Project.repo_id == repo_obj.id,
                Project.number.isnot(None),  # Has a milestone number
                Project.title == milestone_title,
            )
            .first()
        )
    else:
        # Look for repo-level project (no milestone)
        return (
            session.query(Project)
            .filter(Project.repo_id == repo_obj.id, Project.number.is_(None))
            .first()
        )


# ============== Response Helpers ==============


def project_to_dict(
    project: Project,
    include_teams: bool = False,
    include_owner: bool = False,
    children_count: int = 0,
) -> dict[str, Any]:
    """Convert a Project model to a dictionary for API responses."""
    repo_path = None
    if project.repo:
        repo_path = f"{project.repo.owner}/{project.repo.name}"

    result: dict[str, Any] = {
        "id": project.id,
        "title": project.title,
        "description": project.description,
        "state": project.state,
        "due_on": project.due_on.isoformat() if project.due_on else None,
        "repo_path": repo_path,
        "github_id": project.github_id,
        "number": project.number,
        "parent_id": project.parent_id,
        "owner_id": project.owner_id,
        "children_count": children_count,
    }

    if include_owner and project.owner:
        result["owner"] = {
            "id": project.owner.id,
            "identifier": project.owner.identifier,
            "display_name": project.owner.display_name,
        }

    if include_teams:
        result["teams"] = [
            {
                "id": t.id,
                "name": t.name,
                "slug": t.slug,
                "member_count": len(t.members) if t.members else None,
            }
            for t in project.teams
        ]

    return result


def _build_tree(projects: list[Project]) -> list[dict[str, Any]]:
    """Build a nested tree structure from a flat list of projects."""
    # Build a map of id -> project
    project_map: dict[int, Project] = {cast(int, p.id): p for p in projects}

    # Build a map of parent_id -> children
    # Projects with orphaned parent_id (parent not in project_map) are treated as top-level
    children_map: dict[int | None, list[Project]] = {}
    for p in projects:
        parent = p.parent_id
        # Treat orphaned projects (parent doesn't exist) as top-level
        if parent is not None and parent not in project_map:
            parent = None
        if parent not in children_map:
            children_map[parent] = []
        children_map[parent].append(p)

    def build_subtree(parent_id: int | None) -> list[dict[str, Any]]:
        children = children_map.get(parent_id, [])
        return [
            {
                "id": p.id,
                "title": p.title,
                "description": p.description,
                "state": p.state,
                "repo_path": f"{p.repo.owner}/{p.repo.name}" if p.repo else None,
                "parent_id": p.parent_id,
                "children": build_subtree(cast(int, p.id)),
            }
            for p in children
        ]

    return build_subtree(None)


# ============== Project CRUD ==============


@projects_mcp.tool()
@visible_when(require_scopes(SCOPE_PROJECTS))
async def list_all(
    state: Literal["open", "closed"] | None = None,
    parent_id: int | None = None,
    search: str | None = None,
    include_teams: bool = False,
    as_tree: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """
    List all projects the user can access.

    Access is determined by team membership:
    - Users see projects that have at least one team they belong to
    - Admins see all projects

    Args:
        state: Filter by state ('open' or 'closed')
        parent_id: Filter by parent (use 0 for root-level only)
        search: Filter by title (case-insensitive substring match)
        include_teams: If true, include team list for each project
        as_tree: If true, return projects as a nested tree structure
        limit: Maximum number of projects to return (default: 100)
        offset: Number of projects to skip (default: 0)

    Returns:
        List of projects with count and pagination info.
    """
    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated", "projects": [], "count": 0}

        query = session.query(Project)

        # Apply visibility filtering based on team membership
        query = filter_projects_query(session, user, query)

        if include_teams:
            query = query.options(
                selectinload(Project.teams).selectinload(Team.members)
            )

        query = query.options(selectinload(Project.owner))

        # Ensure no duplicates from joins
        query = query.distinct()

        if state:
            query = query.filter(Project.state == state)

        if search:
            query = query.filter(Project.title.ilike(f"%{search}%"))

        if parent_id is not None:
            if parent_id == 0:
                # Root level projects only
                query = query.filter(Project.parent_id.is_(None))
            else:
                query = query.filter(Project.parent_id == parent_id)

        # Get total count before pagination
        total_count = query.count()

        query = query.order_by(Project.title)

        # For tree view, we need all projects (no pagination at query level)
        # Pagination will be applied to root nodes
        if as_tree:
            all_projects = query.all()
            tree = _build_tree(all_projects)
            # Apply pagination to root nodes
            paginated_tree = tree[offset : offset + limit]
            return {
                "tree": paginated_tree,
                "count": len(all_projects),
                "total": total_count,
                "limit": limit,
                "offset": offset,
            }

        # Apply pagination
        projects = query.offset(offset).limit(limit).all()

        # Get children counts
        project_ids = [p.id for p in projects]
        children_counts: dict[int, int] = {}
        if project_ids:
            counts = (
                session.query(Project.parent_id, func.count(Project.id))
                .filter(Project.parent_id.in_(project_ids))
                .group_by(Project.parent_id)
                .all()
            )
            children_counts = {pid: count for pid, count in counts}

        return {
            "projects": [
                project_to_dict(
                    p,
                    include_teams=include_teams,
                    include_owner=True,
                    children_count=children_counts.get(cast(int, p.id), 0),
                )
                for p in projects
            ],
            "count": len(projects),
            "total": total_count,
            "limit": limit,
            "offset": offset,
        }


@projects_mcp.tool()
@visible_when(require_scopes(SCOPE_PROJECTS))
async def fetch(
    project_id: int,
    include_teams: bool = True,
    include_owner: bool = True,
    include_journal: bool = False,
) -> dict:
    """
    Get a single project by ID.

    Args:
        project_id: The project ID
        include_teams: Whether to include team list (default: true)
        include_owner: Whether to include owner details (default: true)
        include_journal: Whether to include journal entries (default: false)

    Returns:
        Project data with optional team list, or error if not found/accessible
    """
    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated", "project": None}

        # Build query with optional eager loading
        query = session.query(Project).filter(Project.id == project_id)
        if include_teams:
            query = query.options(
                selectinload(Project.teams).selectinload(Team.members)
            )
        if include_owner:
            query = query.options(selectinload(Project.owner))

        # Apply access filtering
        query = filter_projects_query(session, user, query)
        project = query.first()

        if not project:
            return {"error": f"Project not found: {project_id}", "project": None}

        # Count children
        children_count = (
            session.query(func.count(Project.id))
            .filter(Project.parent_id == project_id)
            .scalar()
        ) or 0

        result: dict[str, Any] = {
            "project": project_to_dict(
                project, include_teams, include_owner, children_count
            )
        }

        # Fetch journal entries if requested
        if include_journal:
            journal_query = session.query(JournalEntry).filter(
                JournalEntry.target_type == "project",
                JournalEntry.target_id == project_id,
            )
            access_filter = build_journal_access_filter(user, user.id)
            if access_filter is not True:
                journal_query = journal_query.filter(access_filter)
            entries = journal_query.order_by(JournalEntry.created_at.asc()).all()
            result["journal_entries"] = [e.as_payload() for e in entries]

        return result


@projects_mcp.tool()
@visible_when(require_scopes(SCOPE_PROJECTS_WRITE))
async def upsert(
    title: str | None = None,
    team_ids: list[int] | None = None,
    project_id: int | None = None,
    description: str | None = None,
    state: Literal["open", "closed"] | None = None,
    parent_id: int | None = None,
    clear_parent: bool = False,
    owner_id: int | None = None,
    clear_owner: bool = False,
    due_on: str | None = None,
    clear_due_on: bool = False,
    repo: str | None = None,
    milestone: str | None = None,
    create_repo: bool = False,
    private: bool = True,
) -> dict:
    """
    Create or update a project at various levels of the hierarchy.

    Project Hierarchy:
    - **Client level**: Standalone project (no repo) - top-level organizational unit
    - **Repo level**: Project linked to a GitHub repo - represents a product/codebase
    - **Milestone level**: Project linked to a specific milestone - represents a feature/sprint

    Usage patterns:

    1. **Client project** (standalone): Provide title and team_ids only
    2. **Repo project**: Provide repo (and optionally title). Creates tracking entry.
    3. **Milestone project**: Provide repo and milestone. Creates milestone if needed.
    4. **Update existing**: Provide project_id

    Args:
        title: Project title. Required for standalone projects.
               For repo projects, defaults to repo name. For milestone projects,
               defaults to milestone title.
        team_ids: List of team IDs to assign. Required for new projects.
                  On update, replaces all existing team assignments.
        project_id: ID of existing project to update (omit for create)
        description: Optional project description
        state: Project state ('open' or 'closed')
        parent_id: Optional parent project ID for hierarchy
        clear_parent: If true, removes the parent (sets to NULL)
        owner_id: Person ID to assign as project owner
        clear_owner: If true, removes the owner (sets to NULL)
        due_on: Project due date in ISO 8601 format (e.g., "2024-12-31T23:59:59Z")
        clear_due_on: If true, removes the due date (sets to NULL)
        repo: GitHub repo path (e.g., "owner/name").
        milestone: GitHub milestone title. Used with repo to create a milestone-level
                   project. Created on GitHub if it doesn't exist.
        create_repo: If True and repo doesn't exist on GitHub, creates it (default: False)
        private: Whether to create repo as private if creating (default: True)

    Returns:
        Created/updated project data, or error if validation fails

    Examples:
        # Create client-level project (standalone)
        upsert(title="Acme Corp", team_ids=[1])

        # Create repo-level project (tracks a GitHub repo)
        upsert(repo="acme/product-x", team_ids=[1], parent_id=-1)

        # Create milestone-level project (creates milestone if needed)
        upsert(repo="acme/product-x", milestone="v2.0", team_ids=[1])

        # Create repo AND milestone in one call
        upsert(
            repo="acme/new-product",
            milestone="Phase 1",
            team_ids=[1],
            create_repo=True,
            private=True,
        )
    """
    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated", "project": None}

        # If milestone provided without repo, require explicit project_id
        if project_id is None and milestone is not None and repo is None:
            return {
                "error": "project_id is required when specifying milestone without repo "
                "(milestones can have the same name across different repos)",
                "project": None,
            }

        # If no project_id but repo provided, try to find existing project
        if project_id is None and repo is not None:
            existing = find_existing_project_by_repo(session, repo, milestone)
            if existing:
                project_id = existing.id
                logger.info(
                    f"MCP: Found existing project {project_id} for repo={repo}, milestone={milestone}"
                )
            else:
                logger.debug(
                    f"MCP: No existing project found for repo={repo}, milestone={milestone}"
                )

        # UPDATE path
        if project_id is not None:
            return await update_project(
                session,
                user,
                project_id,
                title,
                team_ids,
                description,
                state,
                parent_id,
                clear_parent,
                owner_id,
                clear_owner,
                due_on,
                clear_due_on,
            )

        # Repo-level or milestone-level project path
        if repo is not None:
            if milestone is not None:
                # Milestone-level project
                return await create_milestone_project(
                    session,
                    user,
                    repo,
                    milestone,
                    team_ids,
                    description,
                    parent_id,
                    title,
                    owner_id=owner_id,
                    due_on=due_on,
                    create_repo=create_repo,
                    private=private,
                )
            else:
                # Repo-level project (no milestone)
                return await create_repo_project(
                    session,
                    user,
                    repo,
                    team_ids,
                    description,
                    state or "open",
                    parent_id,
                    title,
                    owner_id=owner_id,
                    due_on=due_on,
                    create_repo=create_repo,
                    private=private,
                )

        # Standalone project path - title is required
        if not title:
            return {
                "error": "title is required for standalone projects (or provide repo)",
                "project": None,
            }
        return await create_standalone_project(
            session,
            user,
            title,
            team_ids,
            description,
            state or "open",
            parent_id,
            owner_id=owner_id,
            due_on=due_on,
        )


def validate_owner(
    session: Any, owner_id: int | None
) -> tuple[Person | None, dict | None]:
    """Validate that an owner exists.

    Returns:
        Tuple of (Person or None, error dict or None)
    """
    if owner_id is None:
        return None, None
    owner = session.get(Person, owner_id)
    if not owner:
        return None, {"error": f"Owner not found: {owner_id}", "project": None}
    return owner, None


def parse_due_on(due_on_str: str | None) -> tuple[datetime | None, dict | None]:
    """Parse a due_on ISO string to datetime.

    Returns:
        Tuple of (datetime or None, error dict or None)
    """
    if due_on_str is None:
        return None, None
    try:
        return datetime.fromisoformat(due_on_str.replace("Z", "+00:00")), None
    except ValueError:
        return None, {"error": "Invalid due_on format. Use ISO 8601.", "project": None}


async def create_standalone_project(
    session: Any,
    user: Any,
    title: str,
    team_ids: list[int] | None,
    description: str | None,
    state: str,
    parent_id: int | None,
    owner_id: int | None = None,
    due_on: str | None = None,
) -> dict:
    """Create a new standalone project."""
    logger.info(f"MCP: Creating project: {title}")

    # Validate teams
    teams, error = validate_teams_for_project(session, user, team_ids)
    if error:
        return error

    # Validate parent
    error = validate_parent_project(session, user, parent_id)
    if error:
        return error

    # Validate owner
    _, error = validate_owner(session, owner_id)
    if error:
        return error

    # Parse due_on
    due_on_dt, error = parse_due_on(due_on)
    if error:
        return error

    # Create project with unique negative ID
    project, error = create_project_with_retry(
        session,
        teams,  # type: ignore[arg-type]
        repo_id=None,
        github_id=None,
        number=None,
        title=title,
        description=description,
        state=state,
        parent_id=parent_id,
        owner_id=owner_id,
        due_on=due_on_dt,
    )
    if error:
        return error
    assert project is not None  # error is None means project was created

    session.commit()
    session.refresh(project)

    return {
        "success": True,
        "created": True,
        "project": project_to_dict(project, include_teams=True),
    }


def get_inbound_teams(
    session: Session,
    client: GithubClient,
    owner: str,
    repo_name: str,
    existing_teams: list[Team],
    github_repo_created: bool,
) -> tuple[list[Team], list[Team]]:
    """Fetch teams from GitHub repo that should be added to a project.

    For existing repos (not newly created), fetches teams that have access
    on GitHub and returns any matching local teams that aren't already assigned.

    Args:
        session: Database session
        client: GitHub client
        owner: Repo owner
        repo_name: Repo name
        existing_teams: Teams already assigned to the project (not modified)
        github_repo_created: Whether the repo was just created (skip sync if so)

    Returns:
        Tuple of (teams_to_add, all_inbound_teams):
        - teams_to_add: Teams from GitHub not in existing_teams (to be added)
        - all_inbound_teams: All matching teams from GitHub (for reporting)
    """
    if github_repo_created:
        return [], []

    inbound_teams = sync_repo_teams_inbound(session, client, owner, repo_name)
    existing_team_ids = {t.id for t in existing_teams}
    teams_to_add = [t for t in inbound_teams if t.id not in existing_team_ids]
    return teams_to_add, inbound_teams


def perform_outbound_sync(
    client: GithubClient,
    owner: str,
    repo_name: str,
    teams: list[Team],
) -> SyncResult | None:
    """Grant teams access to repo on GitHub.

    Args:
        client: Authenticated GitHub client
        owner: Repository owner
        repo_name: Repository name
        teams: List of teams to sync

    Returns:
        SyncResult with synced/skipped/failed lists, or None if no teams provided.
        Logs warnings for any failed syncs.
    """
    if not teams:
        return None
    sync_result = sync_repo_teams_outbound(client, owner, repo_name, teams)
    if sync_result and sync_result.get("failed"):
        logger.warning(
            f"Some teams failed to sync to GitHub for {owner}/{repo_name}: "
            f"{sync_result['failed']}"
        )
    return sync_result


async def create_repo_project(
    session: Any,
    user: Any,
    repo_path: str,
    team_ids: list[int] | None,
    description: str | None,
    state: str,
    parent_id: int | None,
    title_override: str | None,
    owner_id: int | None = None,
    due_on: str | None = None,
    create_repo: bool = False,
    private: bool = True,
) -> dict:
    """Create a project at the repo level (linked to a GitHub repo, no milestone).

    Optionally creates the repo on GitHub if it doesn't exist.
    """
    logger.info(f"MCP: Creating repo-level project: {repo_path}")

    # Parse repo path
    if "/" not in repo_path:
        return {
            "error": f"Invalid repo path '{repo_path}'. Expected format: owner/name",
            "project": None,
        }
    owner, repo_name = repo_path.split("/", 1)

    # Validate teams
    teams, error = validate_teams_for_project(session, user, team_ids)
    if error:
        return error

    # Validate parent
    error = validate_parent_project(session, user, parent_id)
    if error:
        return error

    # Validate owner
    _, error = validate_owner(session, owner_id)
    if error:
        return error

    # Parse due_on
    due_on_dt, error = parse_due_on(due_on)
    if error:
        return error

    # Get GitHub client
    client, repo_obj = get_github_client(session, repo_path, user.id)
    if not client:
        return {
            "error": f"No GitHub access configured for '{repo_path}'",
            "project": None,
        }

    # If repo not tracked, ensure it exists (optionally creating on GitHub)
    github_repo_created = False
    tracking_created = False
    if not repo_obj:
        # Get user's account for tracking
        account = (
            session.query(GithubAccount)
            .filter(
                GithubAccount.user_id == user.id,
                GithubAccount.active.is_(True),
            )
            .first()
        )
        if not account:
            return {"error": "No GitHub account configured", "project": None}

        repo_obj, github_repo_created, tracking_created = ensure_github_repo(
            session,
            client,
            account.id,
            owner,
            repo_name,
            description=description,
            create_if_missing=create_repo,
            private=private,
        )
        if not repo_obj:
            if create_repo:
                return {
                    "error": f"Failed to create repository '{repo_path}' on GitHub. "
                    "Check that the GitHub account has permission to create repositories in the org.",
                    "project": None,
                }
            return {
                "error": f"Repository '{repo_path}' not found. Use create_repo=True to create it.",
                "project": None,
            }

    # Check if project already exists for this repo (without milestone)
    existing_project = (
        session.query(Project)
        .filter(Project.repo_id == repo_obj.id, Project.number.is_(None))
        .first()
    )
    if existing_project:
        # Update teams if provided
        existing_project.teams = teams  # type: ignore[assignment]
        if parent_id is not None:
            existing_project.parent_id = parent_id
        if description is not None:
            existing_project.description = description
        session.commit()
        session.refresh(existing_project)
        return {
            "success": True,
            "created": False,
            "github_repo_created": github_repo_created,
            "tracking_created": tracking_created,
            "project": project_to_dict(existing_project, include_teams=True),
        }

    # Inbound sync: add existing repo teams to project (for existing repos)
    teams_to_add, inbound_teams = get_inbound_teams(
        session, client, owner, repo_name, teams, github_repo_created  # type: ignore[arg-type]
    )
    all_teams = list(teams) + teams_to_add  # type: ignore[arg-type]

    # Create project with unique negative ID (with retry for race conditions)
    project, error = create_project_with_retry(
        session,
        all_teams,
        repo_id=repo_obj.id,
        github_id=repo_obj.github_id,
        number=None,
        title=title_override or repo_name,
        description=description,
        state=state,
        parent_id=parent_id,
        owner_id=owner_id,
        due_on=due_on_dt,
    )
    if error:
        return error
    assert project is not None  # error is None means project was created

    session.commit()
    session.refresh(project)

    # Outbound sync: grant teams access to repo on GitHub (after commit)
    sync_result = perform_outbound_sync(client, owner, repo_name, all_teams)

    result: dict[str, Any] = {
        "success": True,
        "created": True,
        "github_repo_created": github_repo_created,
        "tracking_created": tracking_created,
        "project": project_to_dict(project, include_teams=True),
    }
    if sync_result:
        result["github_team_sync"] = sync_result
    if inbound_teams:
        result["teams_from_github"] = [t.name for t in inbound_teams]
    return result


async def create_milestone_project(
    session: Any,
    user: Any,
    repo_path: str,
    milestone_title: str,
    team_ids: list[int] | None,
    description: str | None,
    parent_id: int | None,
    title_override: str | None,
    owner_id: int | None = None,
    due_on: str | None = None,
    create_repo: bool = False,
    private: bool = True,
) -> dict:
    """Create a project backed by a GitHub milestone.

    The milestone is created if it doesn't exist.
    Optionally creates the repo on GitHub if create_repo=True.
    """
    logger.info(
        f"MCP: Creating milestone-level project: {repo_path} / {milestone_title}"
    )

    # Parse repo path
    if "/" not in repo_path:
        return {
            "error": f"Invalid repo path '{repo_path}'. Expected format: owner/name",
            "project": None,
        }
    owner, repo_name = repo_path.split("/", 1)

    # Validate teams
    teams, error = validate_teams_for_project(session, user, team_ids)
    if error:
        return error

    # Validate parent
    error = validate_parent_project(session, user, parent_id)
    if error:
        return error

    # Validate owner
    _, error = validate_owner(session, owner_id)
    if error:
        return error

    # Parse due_on (may be overridden by milestone data)
    due_on_dt, error = parse_due_on(due_on)
    if error:
        return error

    # Get GitHub client
    client, repo_obj = get_github_client(session, repo_path, user.id)
    if not client:
        return {
            "error": f"No GitHub access configured for '{repo_path}'",
            "project": None,
        }

    # If repo not tracked, ensure it exists (optionally creating on GitHub)
    github_repo_created = False
    if not repo_obj:
        # Get user's account for tracking
        account = (
            session.query(GithubAccount)
            .filter(
                GithubAccount.user_id == user.id,
                GithubAccount.active.is_(True),
            )
            .first()
        )
        if not account:
            return {"error": "No GitHub account configured", "project": None}

        repo_obj, github_repo_created, _ = ensure_github_repo(
            session,
            client,
            account.id,
            owner,
            repo_name,
            description=description,
            create_if_missing=create_repo,
            private=private,
        )
        if not repo_obj:
            if create_repo:
                return {
                    "error": f"Failed to create repository '{repo_path}' on GitHub. "
                    "Check that the GitHub account has permission to create repositories in the org.",
                    "project": None,
                }
            return {
                "error": f"Repository '{repo_path}' not found. Use create_repo=True to create it.",
                "project": None,
            }

    # Ensure milestone exists (create if needed)
    milestone_data, was_created = client.ensure_milestone(
        owner, repo_name, milestone_title, description=description
    )
    if not milestone_data:
        return {
            "error": f"Failed to find or create milestone '{milestone_title}'",
            "project": None,
        }

    # Auto-parent to repo project if no parent specified
    if parent_id is None:
        repo_project = (
            session.query(Project)
            .filter(Project.repo_id == repo_obj.id, Project.number.is_(None))
            .first()
        )
        if repo_project:
            parent_id = repo_project.id

    # Check if project already exists for this milestone
    existing_project = (
        session.query(Project)
        .filter(
            Project.repo_id == repo_obj.id, Project.number == milestone_data["number"]
        )
        .first()
    )
    if existing_project:
        # Update teams if provided
        existing_project.teams = teams  # type: ignore[assignment]
        if parent_id is not None:
            existing_project.parent_id = parent_id
        session.commit()
        session.refresh(existing_project)
        return {
            "success": True,
            "created": False,
            "github_repo_created": github_repo_created,
            "milestone_created": False,
            "project": project_to_dict(existing_project, include_teams=True),
        }

    # Inbound sync: add existing repo teams to project (for existing repos)
    teams_to_add, inbound_teams = get_inbound_teams(
        session, client, owner, repo_name, teams, github_repo_created  # type: ignore[arg-type]
    )
    all_teams = list(teams) + teams_to_add  # type: ignore[arg-type]

    # Use provided due_on if set, otherwise use milestone's due_on from GitHub
    effective_due_on = (
        due_on_dt if due_on_dt is not None else milestone_data.get("due_on")
    )

    # Create project with unique negative ID (with retry for race conditions)
    project, error = create_project_with_retry(
        session,
        all_teams,
        repo_id=repo_obj.id,
        github_id=milestone_data["github_id"],
        number=milestone_data["number"],
        title=title_override or milestone_data["title"],
        description=milestone_data.get("description") or description,
        state=milestone_data.get("state", "open"),
        due_on=effective_due_on,
        parent_id=parent_id,
        owner_id=owner_id,
    )
    if error:
        return error
    assert project is not None  # error is None means project was created

    session.commit()
    session.refresh(project)

    # Outbound sync: grant teams access to repo on GitHub (after commit)
    sync_result = perform_outbound_sync(client, owner, repo_name, all_teams)

    result: dict[str, Any] = {
        "success": True,
        "created": True,
        "github_repo_created": github_repo_created,
        "milestone_created": was_created,
        "project": project_to_dict(project, include_teams=True),
    }
    if sync_result:
        result["github_team_sync"] = sync_result
    if inbound_teams:
        result["teams_from_github"] = [t.name for t in inbound_teams]
    return result


async def update_project(
    session: Any,
    user: Any,
    project_id: int,
    title: str | None,
    team_ids: list[int] | None,
    description: str | None,
    state: str | None,
    parent_id: int | None,
    clear_parent: bool,
    owner_id: int | None = None,
    clear_owner: bool = False,
    due_on: str | None = None,
    clear_due_on: bool = False,
) -> dict:
    """Update an existing project."""
    # Fetch project with access check
    query = filter_projects_query(
        session, user, session.query(Project).filter(Project.id == project_id)
    )
    query = query.options(selectinload(Project.owner))
    project = query.first()

    if not project:
        return {"error": f"Project not found: {project_id}", "project": None}

    is_standalone = project.repo_id is None

    # For GitHub-backed projects, only allow parent_id, owner_id, and due_on changes
    if not is_standalone:
        if title is not None or description is not None or state is not None:
            return {
                "error": "Cannot modify title/description/state of GitHub-backed projects. "
                "These are synced from GitHub.",
                "project": None,
            }

    # Validate parent if changing
    if parent_id is not None:
        if parent_id == project_id:
            return {"error": "Project cannot be its own parent", "project": None}

        parent = session.get(Project, parent_id)
        if not parent:
            return {"error": f"Parent project not found: {parent_id}", "project": None}

        # Check for circular reference
        current = parent
        while current.parent_id is not None:
            if current.parent_id == project_id:
                return {"error": "Circular parent reference detected", "project": None}
            current = session.get(Project, current.parent_id)
            if not current:
                break

    # Validate owner if changing
    if owner_id is not None:
        _, error = validate_owner(session, owner_id)
        if error:
            return error

    # Parse due_on if provided
    due_on_dt = None
    if due_on is not None:
        due_on_dt, error = parse_due_on(due_on)
        if error:
            return error

    # Handle team assignment changes
    teams = None
    old_team_ids: set[int] = set()
    if team_ids is not None:
        if not team_ids:
            return {
                "error": "team_ids cannot be empty - projects require at least one team",
                "project": None,
            }

        # Capture old teams before changes (for outbound sync)
        old_team_ids = {t.id for t in project.teams}

        # Validate all specified teams exist
        teams = session.query(Team).filter(Team.id.in_(team_ids)).all()
        found_ids = {t.id for t in teams}
        missing_ids = set(team_ids) - found_ids

        if missing_ids:
            return {
                "error": f"Invalid team_ids: teams {missing_ids} do not exist",
                "project": None,
            }

        # Non-admins must be a member of at least one specified team
        if not has_admin_scope(user):
            user_team_ids = get_user_team_ids(session, user)
            accessible_team_ids = set(team_ids) & user_team_ids
            if not accessible_team_ids:
                return {
                    "error": "You do not have access to any of the specified teams",
                    "project": None,
                }

    # Sync due_on to GitHub for milestone-backed projects BEFORE local changes
    # This ensures that if GitHub fails, we don't leave the local database inconsistent
    if not is_standalone and project.repo and project.number:
        # Determine if due_on is being changed
        new_due_on_value: datetime | None = None
        due_on_changed = False
        if clear_due_on:
            if project.due_on is not None:
                due_on_changed = True
                new_due_on_value = None
        elif due_on_dt is not None:
            if project.due_on != due_on_dt:
                due_on_changed = True
                new_due_on_value = due_on_dt

        if due_on_changed:
            sync_error = sync_milestone_due_date(project, new_due_on_value)
            if sync_error:
                return sync_error

    # Apply updates
    if clear_parent:
        project.parent_id = None
    elif parent_id is not None:
        project.parent_id = parent_id

    # Owner can be updated for both standalone and GitHub-backed projects
    if clear_owner:
        project.owner_id = None
    elif owner_id is not None:
        project.owner_id = owner_id

    # Due date can be updated for both standalone and GitHub-backed projects
    if clear_due_on:
        project.due_on = None
    elif due_on_dt is not None:
        project.due_on = due_on_dt

    if is_standalone:
        if title is not None:
            project.title = title
        if description is not None:
            project.description = description
        if state is not None:
            project.state = state

    # Update team assignments (works for both standalone and GitHub-backed)
    # NOTE: When teams are removed from a project, their GitHub repo access is NOT
    # automatically revoked. This is intentional for now - see GithubClient.remove_team_from_repo
    # for the capability. Revoking access should be implemented as a follow-up if needed.
    if teams is not None:
        project.teams.clear()
        for team in teams:
            project.teams.append(team)

    session.commit()
    session.refresh(project)

    # Outbound sync: grant newly added teams access to repo on GitHub (after commit)
    sync_result = None
    if teams is not None and project.repo:
        new_team_ids = {t.id for t in teams}
        added_team_ids = new_team_ids - old_team_ids
        if added_team_ids:
            added_teams = [t for t in teams if t.id in added_team_ids]
            try:
                client, _ = get_github_client(
                    session, f"{project.repo.owner}/{project.repo.name}", user.id
                )
                if client:
                    sync_result = perform_outbound_sync(
                        client, project.repo.owner, project.repo.name, added_teams
                    )
            except ValueError as e:
                # GitHub client unavailable or not configured
                logger.warning(
                    f"Could not sync teams to GitHub: {type(e).__name__}: {e}"
                )

    # Count children
    children_count = (
        session.query(func.count(Project.id))
        .filter(Project.parent_id == project_id)
        .scalar()
    ) or 0

    result: dict[str, Any] = {
        "success": True,
        "created": False,
        "project": project_to_dict(
            project, include_teams=team_ids is not None, children_count=children_count
        ),
    }
    if sync_result:
        result["github_team_sync"] = sync_result
    return result


@projects_mcp.tool()
@visible_when(require_scopes(SCOPE_PROJECTS_WRITE))
async def delete(
    project_id: int,
) -> dict:
    """
    Delete a standalone project.

    Requires access to the project via team membership.
    GitHub-backed projects cannot be deleted (they are synced from GitHub).
    Children of deleted projects will have their parent_id set to NULL.

    Args:
        project_id: The project ID to delete

    Returns:
        Deletion status, or error if not found/accessible/GitHub-backed
    """
    logger.info(f"MCP: Deleting project: {project_id}")

    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated"}

        # Fetch project with access check
        query = filter_projects_query(
            session, user, session.query(Project).filter(Project.id == project_id)
        )
        project = query.first()

        if not project:
            return {"error": f"Project not found: {project_id}"}

        if project.repo_id is not None:
            return {
                "error": "Cannot delete GitHub-backed projects. Close them in GitHub instead."
            }

        # Children will have parent_id set to NULL via ON DELETE SET NULL
        session.delete(project)
        session.commit()

        return {"success": True, "deleted_id": project_id}
