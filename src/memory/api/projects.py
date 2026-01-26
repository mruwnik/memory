"""API endpoints for Projects (access control).

Projects are backed by GitHub milestones and used for access control.
"""

from typing import cast

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from memory.api.auth import get_current_user
from memory.common.db.connection import get_session
from memory.common.db.models import User
from memory.common.db.models.sources import GithubMilestone, GithubRepo

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectResponse(BaseModel):
    id: int
    title: str
    description: str | None
    state: str
    repo_path: str


@router.get("")
def list_projects(
    state: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ProjectResponse]:
    """List all projects (GitHub milestones) for access control."""
    query = db.query(GithubMilestone).join(GithubRepo)

    if state:
        query = query.filter(GithubMilestone.state == state)

    query = query.order_by(GithubMilestone.title)
    milestones = query.all()

    return [
        ProjectResponse(
            id=cast(int, ms.id),
            title=cast(str, ms.title),
            description=ms.description,
            state=cast(str, ms.state),
            repo_path=f"{ms.repo.owner}/{ms.repo.name}",
        )
        for ms in milestones
    ]
