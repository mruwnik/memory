"""API endpoints for Projects (access control).

Projects can be:
- GitHub-backed: Synced from GitHub milestones
- Standalone: Created directly in Memory for access control

Projects support hierarchical organization via parent_id.
Team-based access control is managed via the teams MCP server.
"""

from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from memory.api.auth import get_current_user
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.sources import Project, Team

router = APIRouter(prefix="/projects", tags=["projects"])


class TeamSummary(BaseModel):
    id: int
    name: str
    slug: str
    member_count: int | None = None


class ProjectResponse(BaseModel):
    id: int
    title: str
    description: str | None
    state: str
    # GitHub info (null for standalone projects)
    repo_path: str | None
    github_id: int | None
    number: int | None
    # Hierarchy
    parent_id: int | None
    children_count: int
    # Teams (optional)
    teams: list[TeamSummary] | None = None


class ProjectCreate(BaseModel):
    title: str
    description: str | None = None
    state: Literal["open", "closed"] = "open"
    parent_id: int | None = None


class ProjectUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    state: Literal["open", "closed"] | None = None
    parent_id: int | None = None


class ProjectTreeNode(BaseModel):
    """Project with nested children for tree view."""
    id: int
    title: str
    description: str | None
    state: str
    repo_path: str | None
    parent_id: int | None
    children: list["ProjectTreeNode"]


def project_to_response(
    project: Project, children_count: int = 0, include_teams: bool = False
) -> ProjectResponse:
    """Convert a project model to response."""
    repo_path = None
    if project.repo:
        repo_path = f"{project.repo.owner}/{project.repo.name}"

    teams = None
    if include_teams:
        teams = [
            TeamSummary(
                id=cast(int, t.id),
                name=t.name,
                slug=t.slug,
                member_count=len(t.members) if t.members else None,
            )
            for t in project.teams
        ]

    return ProjectResponse(
        id=cast(int, project.id),
        title=cast(str, project.title),
        description=project.description,
        state=cast(str, project.state),
        repo_path=repo_path,
        github_id=project.github_id,
        number=project.number,
        parent_id=project.parent_id,
        children_count=children_count,
        teams=teams,
    )


@router.get("")
def list_projects(
    state: str | None = None,
    parent_id: int | None = None,
    include_children: bool = False,
    include_teams: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ProjectResponse]:
    """List all projects for access control.

    Args:
        state: Filter by state ('open' or 'closed')
        parent_id: Filter by parent (use 0 for root-level only)
        include_children: If true, include child count for each project
        include_teams: If true, include team list for each project
    """
    query = db.query(Project)

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

    query = query.order_by(Project.title)
    projects = query.all()

    # Get children counts if requested
    children_counts: dict[int, int] = {}
    if include_children:
        project_ids = [p.id for p in projects]
        if project_ids:
            counts = (
                db.query(Project.parent_id, func.count(Project.id))
                .filter(Project.parent_id.in_(project_ids))
                .group_by(Project.parent_id)
                .all()
            )
            children_counts = {pid: count for pid, count in counts}

    return [
        project_to_response(p, children_counts.get(cast(int, p.id), 0), include_teams)
        for p in projects
    ]


@router.get("/tree")
def get_project_tree(
    state: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ProjectTreeNode]:
    """Get projects as a nested tree structure."""
    query = db.query(Project)

    if state:
        query = query.filter(Project.state == state)

    query = query.order_by(Project.title)
    all_projects = query.all()

    # Build a map of id -> project
    project_map: dict[int, Project] = {
        cast(int, p.id): p for p in all_projects
    }

    # Build a map of parent_id -> children
    # Projects with orphaned parent_id (parent not in project_map) are treated as top-level
    children_map: dict[int | None, list[Project]] = {}
    for p in all_projects:
        parent = p.parent_id
        # Treat orphaned projects (parent doesn't exist) as top-level
        if parent is not None and parent not in project_map:
            parent = None
        if parent not in children_map:
            children_map[parent] = []
        children_map[parent].append(p)

    def build_tree(parent_id: int | None) -> list[ProjectTreeNode]:
        children = children_map.get(parent_id, [])
        return [
            ProjectTreeNode(
                id=cast(int, p.id),
                title=cast(str, p.title),
                description=p.description,
                state=cast(str, p.state),
                repo_path=f"{p.repo.owner}/{p.repo.name}" if p.repo else None,
                parent_id=p.parent_id,
                children=build_tree(cast(int, p.id)),
            )
            for p in children
        ]

    return build_tree(None)


@router.get("/{project_id}")
def get_project(
    project_id: int,
    include_teams: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ProjectResponse:
    """Get a single project by ID."""
    query = db.query(Project).filter(Project.id == project_id)
    if include_teams:
        query = query.options(selectinload(Project.teams).selectinload(Team.members))

    project = query.first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Count children
    children_count = (
        db.query(func.count(Project.id))
        .filter(Project.parent_id == project_id)
        .scalar()
    ) or 0

    return project_to_response(project, children_count, include_teams)


@router.post("")
def create_project(
    data: ProjectCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ProjectResponse:
    """Create a new standalone project (not GitHub-backed).

    Use the teams MCP server to assign teams to this project for access control.
    """
    # Validate parent exists if specified
    if data.parent_id is not None:
        parent = db.get(Project, data.parent_id)
        if not parent:
            raise HTTPException(status_code=400, detail="Parent project not found")

    # Generate a unique ID for standalone projects
    # Use negative IDs to avoid collision with GitHub milestone IDs
    max_negative_id = (
        db.query(func.min(Project.id))
        .filter(Project.id < 0)
        .scalar()
    )
    new_id = (max_negative_id or 0) - 1

    project = Project(
        id=new_id,
        repo_id=None,  # Standalone project
        github_id=None,
        number=None,
        title=data.title,
        description=data.description,
        state=data.state,
        parent_id=data.parent_id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    return project_to_response(project)


@router.patch("/{project_id}")
def update_project(
    project_id: int,
    data: ProjectUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ProjectResponse:
    """Update a project.

    Note: GitHub-backed projects can only have parent_id updated locally.
    Title, description, and state are synced from GitHub.
    Use the teams MCP server to manage team assignments for access control.
    """
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    is_standalone = project.repo_id is None

    # For GitHub-backed projects, only allow parent_id changes
    if not is_standalone:
        if data.title is not None or data.description is not None or data.state is not None:
            raise HTTPException(
                status_code=400,
                detail="Cannot modify title/description/state of GitHub-backed projects. These are synced from GitHub."
            )

    # Validate parent if changing
    if data.parent_id is not None:
        if data.parent_id == project_id:
            raise HTTPException(status_code=400, detail="Project cannot be its own parent")
        parent = db.get(Project, data.parent_id)
        if not parent:
            raise HTTPException(status_code=400, detail="Parent project not found")
        # Check for circular reference
        current = parent
        while current.parent_id is not None:
            if current.parent_id == project_id:
                raise HTTPException(status_code=400, detail="Circular parent reference detected")
            current = db.get(Project, current.parent_id)
            if not current:
                break

    # Apply updates
    if data.parent_id is not None:
        project.parent_id = data.parent_id
    elif "parent_id" in (data.model_fields_set or set()):
        # Explicitly set to None (unset parent)
        project.parent_id = None

    if is_standalone:
        if data.title is not None:
            project.title = data.title
        if data.description is not None:
            project.description = data.description
        elif "description" in (data.model_fields_set or set()):
            project.description = None
        if data.state is not None:
            project.state = data.state

    db.commit()
    db.refresh(project)

    # Count children
    children_count = (
        db.query(func.count(Project.id))
        .filter(Project.parent_id == project_id)
        .scalar()
    ) or 0

    return project_to_response(project, children_count)


@router.delete("/{project_id}")
def delete_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    """Delete a standalone project.

    GitHub-backed projects cannot be deleted (they are synced from GitHub).
    Children of deleted projects will have their parent_id set to NULL.
    """
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.repo_id is not None:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete GitHub-backed projects. Close them in GitHub instead."
        )

    # Children will have parent_id set to NULL via ON DELETE SET NULL
    db.delete(project)
    db.commit()

    return {"status": "deleted", "id": project_id}
