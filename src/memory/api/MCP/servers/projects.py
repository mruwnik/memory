"""MCP subserver for project management.

Note on error response patterns:
Each MCP tool returns a specific response schema. When returning errors,
we include the expected fields with null/empty values to avoid breaking
clients that expect certain keys.
"""

import logging
from typing import Any, Literal, cast

from fastmcp import FastMCP
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common.access_control import (
    filter_projects_query,
    get_user_team_ids,
    has_admin_scope,
    user_can_access_project,
)
from memory.common.db.connection import make_session
from memory.common.db.models import GithubAccount, GithubRepo, Project, Team
from memory.api.MCP.servers.github_helpers import ensure_github_repo, get_github_client

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
        return None, {"error": "team_ids must be a non-empty list for new projects", "project": None}

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

    if not has_admin_scope(user) and not user_can_access_project(session, user, parent_id):
        return {"error": f"Parent project not found: {parent_id}", "project": None}

    return None


def generate_negative_project_id(session: Any, max_retries: int = 3) -> tuple[int | None, dict | None]:
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
            session.query(func.min(Project.id))
            .filter(Project.id < 0)
            .scalar()
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
            session.query(func.min(Project.id))
            .filter(Project.id < 0)
            .scalar()
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


# ============== Response Helpers ==============


def project_to_dict(
    project: Project,
    include_teams: bool = False,
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
        "repo_path": repo_path,
        "github_id": project.github_id,
        "number": project.number,
        "parent_id": project.parent_id,
        "children_count": children_count,
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
    project_map: dict[int, Project] = {
        cast(int, p.id): p for p in projects
    }

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
@visible_when(require_scopes("projects"))
async def list_all(
    state: Literal["open", "closed"] | None = None,
    parent_id: int | None = None,
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
        include_teams: If true, include team list for each project
        as_tree: If true, return projects as a nested tree structure
        limit: Maximum number of projects to return (default: 100)
        offset: Number of projects to skip (default: 0)

    Returns:
        List of projects with count and pagination info
    """
    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated", "projects": [], "count": 0}

        query = session.query(Project)

        # Apply visibility filtering based on team membership
        query = filter_projects_query(session, user, query)

        if include_teams:
            query = query.options(selectinload(Project.teams).selectinload(Team.members))

        if state:
            query = query.filter(Project.state == state)

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
            paginated_tree = tree[offset:offset + limit]
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
                project_to_dict(p, include_teams, children_counts.get(cast(int, p.id), 0))
                for p in projects
            ],
            "count": len(projects),
            "total": total_count,
            "limit": limit,
            "offset": offset,
        }


@projects_mcp.tool()
@visible_when(require_scopes("projects"))
async def fetch(
    project_id: int,
    include_teams: bool = True,
) -> dict:
    """
    Get a single project by ID.

    Args:
        project_id: The project ID
        include_teams: Whether to include team list (default: true)

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
            query = query.options(selectinload(Project.teams).selectinload(Team.members))

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

        return {"project": project_to_dict(project, include_teams, children_count)}


@projects_mcp.tool()
@visible_when(require_scopes("projects"))
async def upsert(
    title: str | None = None,
    team_ids: list[int] | None = None,
    project_id: int | None = None,
    description: str | None = None,
    state: Literal["open", "closed"] | None = None,
    parent_id: int | None = None,
    clear_parent: bool = False,
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

        # UPDATE path
        if project_id is not None:
            return await update_project(
                session, user, project_id, title, team_ids, description, state, parent_id, clear_parent
            )

        # Repo-level or milestone-level project path
        if repo is not None:
            if milestone is not None:
                # Milestone-level project
                return await create_milestone_project(
                    session, user, repo, milestone, team_ids, description, parent_id, title,
                    create_repo=create_repo, private=private
                )
            else:
                # Repo-level project (no milestone)
                return await create_repo_project(
                    session, user, repo, team_ids, description, state or "open", parent_id, title,
                    create_repo=create_repo, private=private
                )

        # Standalone project path - title is required
        if not title:
            return {"error": "title is required for standalone projects (or provide repo)", "project": None}
        return await create_standalone_project(
            session, user, title, team_ids, description, state or "open", parent_id
        )


async def create_standalone_project(
    session: Any,
    user: Any,
    title: str,
    team_ids: list[int] | None,
    description: str | None,
    state: str,
    parent_id: int | None,
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
    )
    if error:
        return error

    session.commit()
    session.refresh(project)

    return {
        "success": True,
        "created": True,
        "project": project_to_dict(project, include_teams=True),
    }


async def create_repo_project(
    session: Any,
    user: Any,
    repo_path: str,
    team_ids: list[int] | None,
    description: str | None,
    state: str,
    parent_id: int | None,
    title_override: str | None,
    create_repo: bool = False,
    private: bool = True,
) -> dict:
    """Create a project at the repo level (linked to a GitHub repo, no milestone).

    Optionally creates the repo on GitHub if it doesn't exist.
    """
    logger.info(f"MCP: Creating repo-level project: {repo_path}")

    # Parse repo path
    if "/" not in repo_path:
        return {"error": f"Invalid repo path '{repo_path}'. Expected format: owner/name", "project": None}
    owner, repo_name = repo_path.split("/", 1)

    # Validate teams
    teams, error = validate_teams_for_project(session, user, team_ids)
    if error:
        return error

    # Validate parent
    error = validate_parent_project(session, user, parent_id)
    if error:
        return error

    # Get GitHub client
    client, repo_obj = get_github_client(session, repo_path, user.id)
    if not client:
        return {"error": f"No GitHub access configured for '{repo_path}'", "project": None}

    # If repo not tracked, ensure it exists (optionally creating on GitHub)
    github_repo_created = False
    tracking_created = False
    if not repo_obj:
        # Get user's account for tracking
        account = (
            session.query(GithubAccount)
            .filter(GithubAccount.user_id == user.id, GithubAccount.active == True)  # noqa: E712
            .first()
        )
        if not account:
            return {"error": "No GitHub account configured", "project": None}

        repo_obj, github_repo_created, tracking_created = ensure_github_repo(
            session, client, account.id, owner, repo_name,
            description=description, create_if_missing=create_repo, private=private
        )
        if not repo_obj:
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

    # Create project with unique negative ID (with retry for race conditions)
    project, error = create_project_with_retry(
        session,
        teams,  # type: ignore[arg-type]
        repo_id=repo_obj.id,
        github_id=repo_obj.github_id,
        number=None,
        title=title_override or repo_name,
        description=description,
        state=state,
        parent_id=parent_id,
    )
    if error:
        return error

    session.commit()
    session.refresh(project)

    return {
        "success": True,
        "created": True,
        "github_repo_created": github_repo_created,
        "tracking_created": tracking_created,
        "project": project_to_dict(project, include_teams=True),
    }


async def create_milestone_project(
    session: Any,
    user: Any,
    repo_path: str,
    milestone_title: str,
    team_ids: list[int] | None,
    description: str | None,
    parent_id: int | None,
    title_override: str | None,
    create_repo: bool = False,
    private: bool = True,
) -> dict:
    """Create a project backed by a GitHub milestone.

    The milestone is created if it doesn't exist.
    Optionally creates the repo on GitHub if create_repo=True.
    """
    logger.info(f"MCP: Creating milestone-level project: {repo_path} / {milestone_title}")

    # Parse repo path
    if "/" not in repo_path:
        return {"error": f"Invalid repo path '{repo_path}'. Expected format: owner/name", "project": None}
    owner, repo_name = repo_path.split("/", 1)

    # Validate teams
    teams, error = validate_teams_for_project(session, user, team_ids)
    if error:
        return error

    # Validate parent
    error = validate_parent_project(session, user, parent_id)
    if error:
        return error

    # Get GitHub client
    client, repo_obj = get_github_client(session, repo_path, user.id)
    if not client:
        return {"error": f"No GitHub access configured for '{repo_path}'", "project": None}

    # If repo not tracked, ensure it exists (optionally creating on GitHub)
    github_repo_created = False
    if not repo_obj:
        # Get user's account for tracking
        account = (
            session.query(GithubAccount)
            .filter(GithubAccount.user_id == user.id, GithubAccount.active == True)  # noqa: E712
            .first()
        )
        if not account:
            return {"error": "No GitHub account configured", "project": None}

        repo_obj, github_repo_created, _ = ensure_github_repo(
            session, client, account.id, owner, repo_name,
            description=description, create_if_missing=create_repo, private=private
        )
        if not repo_obj:
            return {
                "error": f"Repository '{repo_path}' not found. Use create_repo=True to create it.",
                "project": None,
            }

    # Ensure milestone exists (create if needed)
    milestone_data, was_created = client.ensure_milestone(
        owner, repo_name, milestone_title, description=description
    )
    if not milestone_data:
        return {"error": f"Failed to find or create milestone '{milestone_title}'", "project": None}

    # Check if project already exists for this milestone
    existing_project = (
        session.query(Project)
        .filter(Project.repo_id == repo_obj.id, Project.number == milestone_data["number"])
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

    # Create project with unique negative ID (with retry for race conditions)
    project, error = create_project_with_retry(
        session,
        teams,  # type: ignore[arg-type]
        repo_id=repo_obj.id,
        github_id=milestone_data["github_id"],
        number=milestone_data["number"],
        title=title_override or milestone_data["title"],
        description=milestone_data.get("description") or description,
        state=milestone_data.get("state", "open"),
        due_on=milestone_data.get("due_on"),
        parent_id=parent_id,
    )
    if error:
        return error

    session.commit()
    session.refresh(project)

    return {
        "success": True,
        "created": True,
        "github_repo_created": github_repo_created,
        "milestone_created": was_created,
        "project": project_to_dict(project, include_teams=True),
    }


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
) -> dict:
    """Update an existing project."""
    # Fetch project with access check
    query = filter_projects_query(
        session, user,
        session.query(Project).filter(Project.id == project_id)
    )
    project = query.first()

    if not project:
        return {"error": f"Project not found: {project_id}", "project": None}

    is_standalone = project.repo_id is None

    # For GitHub-backed projects, only allow parent_id changes
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

    # Handle team assignment changes
    teams = None
    if team_ids is not None:
        if not team_ids:
            return {"error": "team_ids cannot be empty - projects require at least one team", "project": None}

        # Validate all specified teams exist
        teams = session.query(Team).filter(Team.id.in_(team_ids)).all()
        found_ids = {t.id for t in teams}
        missing_ids = set(team_ids) - found_ids

        if missing_ids:
            return {"error": f"Invalid team_ids: teams {missing_ids} do not exist", "project": None}

        # Non-admins must be a member of at least one specified team
        if not has_admin_scope(user):
            user_team_ids = get_user_team_ids(session, user)
            accessible_team_ids = set(team_ids) & user_team_ids
            if not accessible_team_ids:
                return {"error": "You do not have access to any of the specified teams", "project": None}

    # Apply updates
    if clear_parent:
        project.parent_id = None
    elif parent_id is not None:
        project.parent_id = parent_id

    if is_standalone:
        if title is not None:
            project.title = title
        if description is not None:
            project.description = description
        if state is not None:
            project.state = state

    # Update team assignments (works for both standalone and GitHub-backed)
    if teams is not None:
        project.teams.clear()
        for team in teams:
            project.teams.append(team)

    session.commit()
    session.refresh(project)

    # Count children
    children_count = (
        session.query(func.count(Project.id))
        .filter(Project.parent_id == project_id)
        .scalar()
    ) or 0

    return {
        "success": True,
        "created": False,
        "project": project_to_dict(project, include_teams=team_ids is not None, children_count=children_count),
    }


@projects_mcp.tool()
@visible_when(require_scopes("projects"))
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
            session, user,
            session.query(Project).filter(Project.id == project_id)
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
