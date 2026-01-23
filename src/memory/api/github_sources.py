"""API endpoints for GitHub Account and Repo management."""

from datetime import datetime, timezone
from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from memory.api.auth import get_current_user, get_user_account, has_admin_scope
from memory.common.celery_app import app as celery_app, SYNC_GITHUB_REPO, SYNC_GITHUB_PROJECTS
from memory.common.db.connection import get_session
from memory.common.db.models import User, GithubProject
from memory.common.db.models.sources import GithubAccount, GithubRepo
from memory.common.github import GithubClient, GithubCredentials

router = APIRouter(prefix="/github", tags=["github"])


# --- Account Models ---


class GithubAccountCreate(BaseModel):
    name: str
    auth_type: Literal["pat", "app"]
    # For PAT auth
    access_token: str | None = None
    # For App auth
    app_id: int | None = None
    installation_id: int | None = None
    private_key: str | None = None


class GithubAccountUpdate(BaseModel):
    name: str | None = None
    access_token: str | None = None
    app_id: int | None = None
    installation_id: int | None = None
    private_key: str | None = None
    active: bool | None = None


class GithubRepoResponse(BaseModel):
    id: int
    account_id: int
    owner: str
    name: str
    repo_path: str
    track_issues: bool
    track_prs: bool
    track_comments: bool
    track_project_fields: bool
    labels_filter: list[str]
    state_filter: str | None
    tags: list[str]
    check_interval: int
    full_sync_interval: int
    last_sync_at: str | None
    last_full_sync_at: str | None
    active: bool
    created_at: str


class GithubAccountResponse(BaseModel):
    id: int
    name: str
    auth_type: str
    has_access_token: bool
    has_private_key: bool
    app_id: int | None
    installation_id: int | None
    active: bool
    last_sync_at: str | None
    created_at: str
    updated_at: str
    repos: list[GithubRepoResponse]


# --- Repo Models ---


class GithubRepoCreate(BaseModel):
    owner: str
    name: str
    track_issues: bool = True
    track_prs: bool = True
    track_comments: bool = True
    track_project_fields: bool = False
    labels_filter: list[str] = []
    state_filter: str | None = None
    tags: list[str] = []
    check_interval: int = 60
    full_sync_interval: int = 1440


class GithubRepoUpdate(BaseModel):
    track_issues: bool | None = None
    track_prs: bool | None = None
    track_comments: bool | None = None
    track_project_fields: bool | None = None
    labels_filter: list[str] | None = None
    state_filter: str | None = None
    tags: list[str] | None = None
    check_interval: int | None = None
    full_sync_interval: int | None = None
    active: bool | None = None


# --- Helper Functions ---


def repo_to_response(repo: GithubRepo) -> GithubRepoResponse:
    """Convert a GithubRepo model to a response model."""
    return GithubRepoResponse(
        id=cast(int, repo.id),
        account_id=cast(int, repo.account_id),
        owner=cast(str, repo.owner),
        name=cast(str, repo.name),
        repo_path=repo.repo_path,
        track_issues=cast(bool, repo.track_issues),
        track_prs=cast(bool, repo.track_prs),
        track_comments=cast(bool, repo.track_comments),
        track_project_fields=cast(bool, repo.track_project_fields),
        labels_filter=list(repo.labels_filter or []),
        state_filter=cast(str | None, repo.state_filter),
        tags=list(repo.tags or []),
        check_interval=cast(int, repo.check_interval),
        full_sync_interval=cast(int, repo.full_sync_interval),
        last_sync_at=repo.last_sync_at.isoformat() if repo.last_sync_at else None,
        last_full_sync_at=repo.last_full_sync_at.isoformat()
        if repo.last_full_sync_at
        else None,
        active=cast(bool, repo.active),
        created_at=repo.created_at.isoformat() if repo.created_at else "",
    )


def account_to_response(account: GithubAccount) -> GithubAccountResponse:
    """Convert a GithubAccount model to a response model."""
    return GithubAccountResponse(
        id=cast(int, account.id),
        name=cast(str, account.name),
        auth_type=cast(str, account.auth_type),
        has_access_token=bool(account.access_token),
        has_private_key=bool(account.private_key),
        app_id=cast(int | None, account.app_id),
        installation_id=cast(int | None, account.installation_id),
        active=cast(bool, account.active),
        last_sync_at=account.last_sync_at.isoformat() if account.last_sync_at else None,
        created_at=account.created_at.isoformat() if account.created_at else "",
        updated_at=account.updated_at.isoformat() if account.updated_at else "",
        repos=[repo_to_response(repo) for repo in account.repos],
    )


# --- Account Endpoints ---


@router.get("/accounts")
def list_accounts(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[GithubAccountResponse]:
    """List GitHub accounts for the current user."""
    accounts = db.query(GithubAccount).filter(GithubAccount.user_id == user.id).all()
    return [account_to_response(account) for account in accounts]


class GithubRepoBasic(BaseModel):
    """Minimal repo info for selection dropdowns."""

    id: int
    owner: str
    name: str
    repo_path: str


@router.get("/repos")
def list_all_repos(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[GithubRepoBasic]:
    """List all tracked repos. Admins see all repos, others see only their own."""
    query = db.query(GithubRepo).filter(GithubRepo.active == True)  # noqa: E712

    if not has_admin_scope(user):
        # Non-admins only see repos from their own accounts
        user_account_ids = [
            acc.id
            for acc in db.query(GithubAccount.id)
            .filter(GithubAccount.user_id == user.id)
            .all()
        ]
        query = query.filter(GithubRepo.account_id.in_(user_account_ids))

    return [
        GithubRepoBasic(
            id=cast(int, repo.id),
            owner=cast(str, repo.owner),
            name=cast(str, repo.name),
            repo_path=repo.repo_path,
        )
        for repo in query.all()
    ]


@router.post("/accounts")
def create_account(
    data: GithubAccountCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> GithubAccountResponse:
    """Create a new GitHub account."""
    # Validate auth configuration
    if data.auth_type == "pat":
        if not data.access_token:
            raise HTTPException(
                status_code=400,
                detail="access_token is required for PAT authentication",
            )
    elif data.auth_type == "app":
        if not all([data.app_id, data.installation_id, data.private_key]):
            raise HTTPException(
                status_code=400,
                detail="app_id, installation_id, and private_key are required for App authentication",
            )

    account = GithubAccount(
        user_id=user.id,
        name=data.name,
        auth_type=data.auth_type,
        access_token=data.access_token,
        app_id=data.app_id,
        installation_id=data.installation_id,
        private_key=data.private_key,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    return account_to_response(account)


@router.get("/accounts/{account_id}")
def get_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> GithubAccountResponse:
    """Get a single GitHub account."""
    account = get_user_account(db, GithubAccount, account_id, user)
    return account_to_response(account)


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: int,
    updates: GithubAccountUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> GithubAccountResponse:
    """Update a GitHub account."""
    account = get_user_account(db, GithubAccount, account_id, user)

    if updates.name is not None:
        account.name = updates.name
    if updates.access_token is not None:
        account.access_token = updates.access_token
    if updates.app_id is not None:
        account.app_id = updates.app_id
    if updates.installation_id is not None:
        account.installation_id = updates.installation_id
    if updates.private_key is not None:
        account.private_key = updates.private_key
    if updates.active is not None:
        account.active = updates.active

    db.commit()
    db.refresh(account)

    return account_to_response(account)


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Delete a GitHub account and all its repos."""
    account = get_user_account(db, GithubAccount, account_id, user)

    db.delete(account)
    db.commit()

    return {"status": "deleted"}


@router.post("/accounts/{account_id}/validate")
def validate_account(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Validate GitHub API access for an account."""
    account = get_user_account(db, GithubAccount, account_id, user)

    try:
        credentials = GithubCredentials(
            auth_type=cast(str, account.auth_type),
            access_token=cast(str | None, account.access_token),
            app_id=cast(int | None, account.app_id),
            installation_id=cast(int | None, account.installation_id),
            private_key=cast(str | None, account.private_key),
        )
        client = GithubClient(credentials)

        # Test by getting authenticated user info
        user_info = client.get_authenticated_user()

        return {
            "status": "success",
            "message": f"Authenticated as {user_info.get('login', 'unknown')}",
            "user": user_info.get("login"),
            "scopes": user_info.get("scopes", []),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


class AvailableRepoResponse(BaseModel):
    owner: str
    name: str
    full_name: str
    description: str | None
    private: bool
    html_url: str | None


class AvailableProjectResponse(BaseModel):
    number: int
    title: str
    short_description: str | None
    url: str
    public: bool
    closed: bool
    items_total_count: int


@router.get("/accounts/{account_id}/available-repos")
def list_available_repos(
    account_id: int,
    limit: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[AvailableRepoResponse]:
    """List repositories available from GitHub for this account.

    Args:
        account_id: GitHub account ID.
        limit: Maximum number of repos to return. None or 0 means fetch all available repos.
    """
    account = get_user_account(db, GithubAccount, account_id, user)

    # Treat 0 as "no limit" for API convenience
    max_repos = limit if limit else None

    try:
        credentials = GithubCredentials(
            auth_type=cast(str, account.auth_type),
            access_token=cast(str | None, account.access_token),
            app_id=cast(int | None, account.app_id),
            installation_id=cast(int | None, account.installation_id),
            private_key=cast(str | None, account.private_key),
        )
        client = GithubClient(credentials)

        repos = []
        for repo in client.list_repos(max_repos=max_repos):
            repos.append(AvailableRepoResponse(**repo))
        return repos
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/accounts/{account_id}/available-projects")
def list_available_projects(
    account_id: int,
    owner: str,
    is_org: bool = True,
    include_closed: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[AvailableProjectResponse]:
    """List projects available from GitHub for this account.

    Args:
        account_id: GitHub account ID.
        owner: Organization or user login that owns the projects.
        is_org: True if owner is an organization, False if user.
        include_closed: Whether to include closed projects.
    """
    account = get_user_account(db, GithubAccount, account_id, user)

    try:
        credentials = GithubCredentials(
            auth_type=cast(str, account.auth_type),
            access_token=cast(str | None, account.access_token),
            app_id=cast(int | None, account.app_id),
            installation_id=cast(int | None, account.installation_id),
            private_key=cast(str | None, account.private_key),
        )
        client = GithubClient(credentials)

        projects = []
        for project in client.list_projects(owner, is_org, include_closed):
            projects.append(
                AvailableProjectResponse(
                    number=project["number"],
                    title=project["title"],
                    short_description=project["short_description"],
                    url=project["url"],
                    public=project["public"],
                    closed=project["closed"],
                    items_total_count=project["items_total_count"],
                )
            )
        return projects
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Repo Endpoints ---


@router.get("/accounts/{account_id}/repos")
def list_repos(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[GithubRepoResponse]:
    """List all repos for a GitHub account."""
    account = get_user_account(db, GithubAccount, account_id, user)

    return [repo_to_response(repo) for repo in account.repos]


@router.post("/accounts/{account_id}/repos")
def add_repo(
    account_id: int,
    data: GithubRepoCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> GithubRepoResponse:
    """Add a repo to track for a GitHub account."""
    get_user_account(db, GithubAccount, account_id, user)  # Verify ownership

    # Check for duplicate
    existing = (
        db.query(GithubRepo)
        .filter(
            GithubRepo.account_id == account_id,
            GithubRepo.owner == data.owner,
            GithubRepo.name == data.name,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Repo already tracked")

    repo = GithubRepo(
        account_id=account_id,
        owner=data.owner,
        name=data.name,
        track_issues=data.track_issues,
        track_prs=data.track_prs,
        track_comments=data.track_comments,
        track_project_fields=data.track_project_fields,
        labels_filter=data.labels_filter,
        state_filter=data.state_filter,
        tags=data.tags,
        check_interval=data.check_interval,
        full_sync_interval=data.full_sync_interval,
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)

    return repo_to_response(repo)


@router.patch("/accounts/{account_id}/repos/{repo_id}")
def update_repo(
    account_id: int,
    repo_id: int,
    updates: GithubRepoUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> GithubRepoResponse:
    """Update a repo's tracking configuration."""
    get_user_account(db, GithubAccount, account_id, user)  # Verify ownership
    repo = (
        db.query(GithubRepo)
        .filter(GithubRepo.account_id == account_id, GithubRepo.id == repo_id)
        .first()
    )
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    if updates.track_issues is not None:
        repo.track_issues = updates.track_issues
    if updates.track_prs is not None:
        repo.track_prs = updates.track_prs
    if updates.track_comments is not None:
        repo.track_comments = updates.track_comments
    if updates.track_project_fields is not None:
        repo.track_project_fields = updates.track_project_fields
    if updates.labels_filter is not None:
        repo.labels_filter = updates.labels_filter
    if updates.state_filter is not None:
        repo.state_filter = updates.state_filter
    if updates.tags is not None:
        repo.tags = updates.tags
    if updates.check_interval is not None:
        repo.check_interval = updates.check_interval
    if updates.full_sync_interval is not None:
        repo.full_sync_interval = updates.full_sync_interval
    if updates.active is not None:
        repo.active = updates.active

    db.commit()
    db.refresh(repo)

    return repo_to_response(repo)


@router.delete("/accounts/{account_id}/repos/{repo_id}")
def remove_repo(
    account_id: int,
    repo_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Remove a repo from tracking."""
    get_user_account(db, GithubAccount, account_id, user)  # Verify ownership
    repo = (
        db.query(GithubRepo)
        .filter(GithubRepo.account_id == account_id, GithubRepo.id == repo_id)
        .first()
    )
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    db.delete(repo)
    db.commit()

    return {"status": "deleted"}


@router.post("/accounts/{account_id}/repos/{repo_id}/sync")
def trigger_repo_sync(
    account_id: int,
    repo_id: int,
    force_full: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Manually trigger a sync for a repo."""
    get_user_account(db, GithubAccount, account_id, user)  # Verify ownership
    repo = (
        db.query(GithubRepo)
        .filter(GithubRepo.account_id == account_id, GithubRepo.id == repo_id)
        .first()
    )
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    task = celery_app.send_task(
        SYNC_GITHUB_REPO,
        args=[repo_id],
        kwargs={"force_full": force_full},
    )

    return {"task_id": task.id, "status": "scheduled"}


# --- Project Models ---


class GithubProjectResponse(BaseModel):
    id: int
    account_id: int
    node_id: str
    number: int
    owner_type: str
    owner_login: str
    title: str
    short_description: str | None
    readme: str | None
    url: str
    public: bool
    closed: bool
    fields: list[dict]
    items_total_count: int
    github_created_at: str | None
    github_updated_at: str | None
    last_sync_at: str | None
    created_at: str


class GithubProjectCreate(BaseModel):
    owner: str
    project_number: int
    is_org: bool = True


def project_to_response(project: GithubProject) -> GithubProjectResponse:
    """Convert a GithubProject model to a response model."""
    return GithubProjectResponse(
        id=cast(int, project.id),
        account_id=cast(int, project.account_id),
        node_id=cast(str, project.node_id),
        number=cast(int, project.number),
        owner_type=cast(str, project.owner_type),
        owner_login=cast(str, project.owner_login),
        title=cast(str, project.title),
        short_description=cast(str | None, project.short_description),
        readme=cast(str | None, project.readme),
        url=cast(str, project.url),
        public=cast(bool, project.public),
        closed=cast(bool, project.closed),
        fields=list(project.fields or []),
        items_total_count=cast(int, project.items_total_count),
        github_created_at=project.github_created_at.isoformat()
        if project.github_created_at
        else None,
        github_updated_at=project.github_updated_at.isoformat()
        if project.github_updated_at
        else None,
        last_sync_at=project.last_sync_at.isoformat() if project.last_sync_at else None,
        created_at=project.created_at.isoformat() if project.created_at else "",
    )


# --- Project Endpoints (Account-scoped) ---


@router.get("/accounts/{account_id}/projects")
def list_account_projects(
    account_id: int,
    include_closed: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[GithubProjectResponse]:
    """List all synced GitHub projects for a specific account."""
    account = get_user_account(db, GithubAccount, account_id, user)

    query = db.query(GithubProject).filter(GithubProject.account_id == account.id)
    if not include_closed:
        query = query.filter(GithubProject.closed == False)  # noqa: E712

    query = query.order_by(GithubProject.title)
    projects = query.all()

    return [project_to_response(project) for project in projects]


@router.post("/accounts/{account_id}/projects")
def add_project(
    account_id: int,
    data: GithubProjectCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> GithubProjectResponse:
    """Add a single GitHub project to track for an account.

    Fetches the project from GitHub and stores it locally.
    """
    account = get_user_account(db, GithubAccount, account_id, user)

    # Check for duplicate
    existing = (
        db.query(GithubProject)
        .filter(
            GithubProject.account_id == account_id,
            GithubProject.owner_login == data.owner,
            GithubProject.number == data.project_number,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Project already tracked")

    # Fetch project from GitHub
    try:
        credentials = GithubCredentials(
            auth_type=cast(str, account.auth_type),
            access_token=cast(str | None, account.access_token),
            app_id=cast(int | None, account.app_id),
            installation_id=cast(int | None, account.installation_id),
            private_key=cast(str | None, account.private_key),
        )
        client = GithubClient(credentials)

        # Fetch the specific project directly via GraphQL
        project_data = client.fetch_project(data.owner, data.project_number, data.is_org)
        if not project_data:
            raise HTTPException(
                status_code=404,
                detail=f"Project #{data.project_number} not found for {data.owner}",
            )

        # Create the project record
        project = GithubProject(
            account_id=account_id,
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
        db.add(project)
        db.commit()
        db.refresh(project)

        return project_to_response(project)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Project Endpoints (Global) ---


@router.get("/projects")
def list_projects(
    owner: str | None = None,
    include_closed: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[GithubProjectResponse]:
    """List all synced GitHub projects."""
    query = db.query(GithubProject)

    if owner:
        query = query.filter(GithubProject.owner_login == owner)
    if not include_closed:
        query = query.filter(GithubProject.closed == False)  # noqa: E712

    query = query.order_by(GithubProject.title)
    projects = query.all()

    return [project_to_response(project) for project in projects]


@router.get("/projects/{project_id}")
def get_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> GithubProjectResponse:
    """Get a single GitHub project."""
    project = db.get(GithubProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project_to_response(project)


@router.post("/projects/sync")
def trigger_projects_sync(
    owner: str,
    is_org: bool = True,
    include_closed: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Trigger a sync of all GitHub Projects for an owner."""
    # Find an active account for the current user
    account = (
        db.query(GithubAccount)
        .filter(
            GithubAccount.user_id == user.id,
            GithubAccount.active == True,  # noqa: E712
        )
        .first()
    )
    if not account:
        raise HTTPException(status_code=400, detail="No active GitHub account found")

    task = celery_app.send_task(
        SYNC_GITHUB_PROJECTS,
        args=[account.id, owner, is_org, include_closed],
    )

    return {"task_id": task.id, "status": "scheduled", "owner": owner}


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Delete a synced GitHub project."""
    project = db.get(GithubProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    db.delete(project)
    db.commit()

    return {"status": "deleted"}
