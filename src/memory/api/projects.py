"""API endpoints for Projects (access control).

Projects can be:
- GitHub-backed: Synced from GitHub milestones
- Standalone: Created directly in Memory for access control

Projects support hierarchical organization via parent_id.
"""

from typing import Literal, Self, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from memory.api.auth import get_current_user
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.people import Person
from memory.common.db.models.sources import Project, project_collaborators
from memory.common.people import find_person

router = APIRouter(prefix="/projects", tags=["projects"])


class CollaboratorInput(BaseModel):
    """Input for setting a project collaborator."""

    person_id: int | None = None
    person_identifier: str | None = None
    role: Literal["contributor", "manager", "admin"] = "contributor"

    @model_validator(mode="after")
    def require_one_identifier(self) -> Self:
        if not self.person_id and not self.person_identifier:
            raise ValueError("Either person_id or person_identifier required")
        return self


class CollaboratorResponse(BaseModel):
    """A project collaborator with their role."""

    person_id: int
    person_identifier: str
    display_name: str
    role: str


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
    # Collaborators
    collaborators: list[CollaboratorResponse]


class ProjectCreate(BaseModel):
    title: str
    description: str | None = None
    state: Literal["open", "closed"] = "open"
    parent_id: int | None = None
    collaborators: list[CollaboratorInput] | None = None


class ProjectUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    state: Literal["open", "closed"] | None = None
    parent_id: int | None = None
    collaborators: list[CollaboratorInput] | None = None


class ProjectTreeNode(BaseModel):
    """Project with nested children for tree view."""
    id: int
    title: str
    description: str | None
    state: str
    repo_path: str | None
    parent_id: int | None
    children: list["ProjectTreeNode"]


def get_collaborators(db: Session, project_id: int) -> list[CollaboratorResponse]:
    """Get collaborators for a project."""
    # Use a JOIN to fetch collaborators with person data in a single query
    rows = db.execute(
        project_collaborators.select()
        .add_columns(
            Person.id.label("person_id"),
            Person.identifier.label("person_identifier"),
            Person.display_name.label("person_display_name"),
        )
        .join(Person, Person.id == project_collaborators.c.person_id)
        .where(project_collaborators.c.project_id == project_id)
    ).fetchall()

    return [
        CollaboratorResponse(
            person_id=row.person_id,
            person_identifier=row.person_identifier,
            display_name=row.person_display_name,
            role=row.role,
        )
        for row in rows
    ]


def resolve_collaborator_person(db: Session, collab: CollaboratorInput) -> Person:
    """Resolve a CollaboratorInput to a Person, raising HTTPException if not found."""
    if collab.person_id:
        person = db.get(Person, collab.person_id)
        if not person:
            raise HTTPException(
                status_code=400, detail=f"Person with id {collab.person_id} not found"
            )
        return person

    person = find_person(db, collab.person_identifier)
    if not person:
        raise HTTPException(
            status_code=400,
            detail=f"Person with identifier '{collab.person_identifier}' not found",
        )
    return person


def sync_collaborators(
    db: Session, project_id: int, collaborators: list[CollaboratorInput]
) -> None:
    """Replace all collaborators for a project with the given list.

    Deduplicates by person, using the last role if a person appears multiple times.
    """
    # Resolve all people and dedupe (last one wins)
    resolved: dict[int, str] = {}
    for collab in collaborators:
        person = resolve_collaborator_person(db, collab)
        resolved[cast(int, person.id)] = collab.role

    # Delete existing collaborators
    db.execute(
        project_collaborators.delete().where(
            project_collaborators.c.project_id == project_id
        )
    )

    # Insert new collaborators
    for person_id, role in resolved.items():
        db.execute(
            project_collaborators.insert().values(
                project_id=project_id,
                person_id=person_id,
                role=role,
            )
        )


def project_to_response(
    db: Session, project: Project, children_count: int = 0
) -> ProjectResponse:
    """Convert a project model to response."""
    repo_path = None
    if project.repo:
        repo_path = f"{project.repo.owner}/{project.repo.name}"

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
        collaborators=get_collaborators(db, cast(int, project.id)),
    )


@router.get("")
def list_projects(
    state: str | None = None,
    parent_id: int | None = None,
    include_children: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ProjectResponse]:
    """List all projects for access control.

    Args:
        state: Filter by state ('open' or 'closed')
        parent_id: Filter by parent (use 0 for root-level only)
        include_children: If true, include child count for each project
    """
    query = db.query(Project)

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
            children_counts = {parent_id: count for parent_id, count in counts}

    return [
        project_to_response(db, p, children_counts.get(cast(int, p.id), 0))
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
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ProjectResponse:
    """Get a single project by ID."""
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Count children
    children_count = (
        db.query(func.count(Project.id))
        .filter(Project.parent_id == project_id)
        .scalar()
    ) or 0

    return project_to_response(db, project, children_count)


@router.post("")
def create_project(
    data: ProjectCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ProjectResponse:
    """Create a new standalone project (not GitHub-backed)."""
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

    # Sync collaborators if provided
    if data.collaborators:
        sync_collaborators(db, new_id, data.collaborators)
        db.commit()

    return project_to_response(db, project)


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

    # Sync collaborators if explicitly provided (even if empty list)
    if "collaborators" in (data.model_fields_set or set()):
        sync_collaborators(db, project_id, data.collaborators or [])

    db.commit()
    db.refresh(project)

    # Count children
    children_count = (
        db.query(func.count(Project.id))
        .filter(Project.parent_id == project_id)
        .scalar()
    ) or 0

    return project_to_response(db, project, children_count)


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
