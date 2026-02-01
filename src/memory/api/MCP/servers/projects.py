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
from memory.common.db.models import Project, Team

logger = logging.getLogger(__name__)

projects_mcp = FastMCP("memory-projects")


# ============== Response Helpers ==============


def _project_to_dict(
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
                _project_to_dict(p, include_teams, children_counts.get(cast(int, p.id), 0))
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

        return {"project": _project_to_dict(project, include_teams, children_count)}


@projects_mcp.tool()
@visible_when(require_scopes("projects"))
async def upsert(
    title: str,
    team_ids: list[int] | None = None,
    project_id: int | None = None,
    description: str | None = None,
    state: Literal["open", "closed"] = "open",
    parent_id: int | None = None,
    clear_parent: bool = False,
) -> dict:
    """
    Create or update a standalone project.

    If project_id is provided, updates the existing project.
    Otherwise, creates a new project (requires team_ids).

    Projects must be assigned to at least one team for access control.
    Multiple teams can be assigned at creation for shared access.

    Note: GitHub-backed projects can only have parent_id updated locally.
    Title, description, and state are synced from GitHub for those projects.

    Args:
        title: Project title
        team_ids: List of team IDs to assign (required for new projects)
        project_id: ID of existing project to update (omit for create)
        description: Optional project description
        state: Project state ('open' or 'closed', default: 'open')
        parent_id: Optional parent project ID for hierarchy
        clear_parent: If true, removes the parent (sets to NULL)

    Returns:
        Created/updated project data, or error if validation fails

    Example:
        # Create new project
        upsert(
            title="Q1 2026 Sprint",
            team_ids=[1, 3],  # Engineering and Design teams
            description="First quarter development sprint",
        )

        # Update existing project
        upsert(
            title="Q1 2026 Sprint - Updated",
            project_id=-1,
            state="closed",
        )
    """
    with make_session() as session:
        user = get_mcp_current_user(session, full=True)
        if not user:
            return {"error": "Not authenticated", "project": None}

        # UPDATE path
        if project_id is not None:
            return await _update_project(
                session, user, project_id, title, description, state, parent_id, clear_parent
            )

        # CREATE path
        return await _create_project(
            session, user, title, team_ids, description, state, parent_id
        )


async def _create_project(
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

    # Validate team_ids is non-empty for creation
    if not team_ids:
        return {"error": "team_ids must be a non-empty list for new projects", "project": None}

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

    # Validate parent exists if specified
    if parent_id is not None:
        parent = session.get(Project, parent_id)
        if not parent:
            return {"error": f"Parent project not found: {parent_id}", "project": None}
        # Non-admins must have access to the parent
        if not has_admin_scope(user) and not user_can_access_project(session, user, parent_id):
            return {"error": f"Parent project not found: {parent_id}", "project": None}

    # Generate a unique ID for standalone projects
    # Use negative IDs to avoid collision with GitHub milestone IDs
    # Retry on collision to handle concurrent inserts
    max_retries = 3
    project = None
    for attempt in range(max_retries):
        max_negative_id = (
            session.query(func.min(Project.id))
            .filter(Project.id < 0)
            .scalar()
        )
        new_id = (max_negative_id or 0) - 1

        project = Project(
            id=new_id,
            repo_id=None,  # Standalone project
            github_id=None,
            number=None,
            title=title,
            description=description,
            state=state,
            parent_id=parent_id,
        )
        try:
            session.add(project)
            session.flush()  # Get the ID assigned
            break  # Success
        except IntegrityError:
            session.rollback()
            if attempt == max_retries - 1:
                return {
                    "error": "Failed to generate unique project ID after retries",
                    "project": None,
                }
            # Retry with fresh ID
            continue

    # Assign to all specified teams
    for team in teams:
        project.teams.append(team)

    session.commit()
    session.refresh(project)

    return {
        "success": True,
        "created": True,
        "project": _project_to_dict(project, include_teams=True),
    }


async def _update_project(
    session: Any,
    user: Any,
    project_id: int,
    title: str | None,
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
        "project": _project_to_dict(project, include_teams=False, children_count=children_count),
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
