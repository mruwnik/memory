"""MCP subserver for project management.

Note on error response patterns:
Each MCP tool returns a specific response schema. When returning errors,
we include the expected fields with null/empty values to avoid breaking
clients that expect certain keys.

The orchestration layer in `memory.common.project` raises typed
`ProjectError` subclasses. The MCP tools here translate those exceptions
back into the legacy `{"error": str(e), "project": None}` dict shape.
"""

import logging
from datetime import datetime
from typing import Any, Literal, cast

from fastmcp import FastMCP
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.visibility import require_scopes, visible_when
from memory.common.access_control import (
    filter_projects_query,
    get_user_team_ids,
    has_admin_scope,
)
from memory.common.scopes import SCOPE_PROJECTS, SCOPE_PROJECTS_WRITE
from memory.common.db.connection import make_session
from memory.common.db.models import Project, Team
from memory.common.db.models.journal import JournalEntry, build_journal_access_filter
from memory.common.project import (
    ProjectCreationResult,
    ProjectError,
    create_milestone_project as _create_milestone_project,
    create_repo_project as _create_repo_project,
    create_standalone_project as _create_standalone_project,
    find_existing_project_by_repo,
    get_github_client,
    handle_attach,
    handle_clear_milestone as _handle_clear_milestone,
    handle_clear_repo as _handle_clear_repo,
    handle_promote_to_milestone,
    mark_repo_inactive,
    perform_outbound_sync,
    refresh_from_github,
    sync_milestone_due_date,
)
from memory.common.project.errors import (
    RepoArchivedError,
    RepoMissingError,
)
from memory.api.MCP.servers.serializers import build_tree, project_to_dict
from memory.api.MCP.servers.validation import (
    parse_due_on,
    validate_doc_url,
    validate_owner,
    validate_parent_project,
    validate_teams_for_project,
)

logger = logging.getLogger(__name__)

projects_mcp = FastMCP("memory-projects")

# Sentinel for "not provided" on string fields (doc_url). We use a string sentinel
# here because MCP tool parameters are JSON -- object sentinels aren't viable.
# Numeric fields (owner_id) use 0 as their sentinel instead because their domain is
# positive integers, and 0 is unambiguous. The two conventions coexist intentionally;
# unifying them would add complexity without benefit.
_UNSET = "__UNSET__"


def _project_error_response(exc: ProjectError) -> dict:
    """Translate a ProjectError to the legacy MCP error-dict shape."""
    return {"error": str(exc), "project": None}


def _creation_result_to_dict(
    result: ProjectCreationResult,
    *,
    kind: str = "standalone",
) -> dict:
    """Convert a `ProjectCreationResult` to the MCP response dict.

    Args:
        kind: One of "standalone", "repo", "milestone". Determines which
            extra keys (`github_repo_created`, `tracking_created`,
            `milestone_created`) are always present in the response.
    """
    response: dict[str, Any] = {
        "success": True,
        "created": result.created,
        "project": project_to_dict(result.project, include_teams=True),
    }
    if kind == "repo":
        response["github_repo_created"] = result.github_repo_created
        response["tracking_created"] = result.tracking_created
    elif kind == "milestone":
        response["github_repo_created"] = result.github_repo_created
        response["milestone_created"] = result.milestone_created
    if result.sync_result:
        response["github_team_sync"] = result.sync_result
    if result.inbound_teams:
        response["teams_from_github"] = [t.name for t in result.inbound_teams]
    return response


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
            tree = build_tree(all_projects)
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


# ----- MCP wrappers around the orchestration creators -----
#
# Each `create_*_project` MCP wrapper does the same five things:
#   1. validate `team_ids`            -> Team[]
#   2. validate `parent_id`           -> error dict or pass
#   3. validate `owner_id`            -> error dict or pass
#   4. parse `due_on` (ISO8601)       -> datetime | None
#   5. call the orchestration-layer creator
#   6. translate `ProjectError` -> legacy `{"error": ..., "project": None}`
#   7. translate the result via `_creation_result_to_dict(kind=...)`
#
# The wrappers differ only in:
#   - which orchestration-layer creator they call,
#   - which extra kwargs that creator takes (repo_path, milestone_title, etc.),
#   - the `kind` label on the response dict.
#
# `_run_create` factors out steps 1-4, 6-7. Each public wrapper supplies a
# `create_call` closure for step 5 that captures its own extra args.


def _validate_create_inputs(
    session: Any,
    user: Any,
    team_ids: list[int] | None,
    parent_id: int | None,
    owner_id: int | None,
    due_on: str | None,
) -> tuple[list[Team] | None, datetime | None, dict | None]:
    """Run the four MCP-shaped validations every create path shares.

    Returns `(teams, due_on_dt, error_dict)`. If `error_dict` is non-None,
    the caller should short-circuit and return it; `teams` and `due_on_dt`
    are undefined in that case.
    """
    teams, error = validate_teams_for_project(session, user, team_ids)
    if error:
        return None, None, error

    error = validate_parent_project(session, user, parent_id)
    if error:
        return None, None, error

    _, error = validate_owner(session, owner_id)
    if error:
        return None, None, error

    due_on_dt, error = parse_due_on(due_on)
    if error:
        return None, None, error

    return teams, due_on_dt, None


async def _run_create(
    *,
    session: Any,
    user: Any,
    kind: Literal["standalone", "repo", "milestone"],
    team_ids: list[int] | None,
    parent_id: int | None,
    owner_id: int | None,
    due_on: str | None,
    create_call: Any,
) -> dict:
    """Shared dispatcher for the three MCP creation wrappers.

    Validates the common inputs, calls `create_call(teams, due_on_dt)`,
    translates `ProjectError` → error dict, and packages the
    `ProjectCreationResult` with the right `kind` flag.
    """
    teams, due_on_dt, error = _validate_create_inputs(
        session, user, team_ids, parent_id, owner_id, due_on
    )
    if error:
        return error

    try:
        result = create_call(teams or [], due_on_dt)
    except ProjectError as e:
        return _project_error_response(e)

    return _creation_result_to_dict(result, kind=kind)


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
    doc_url: str | None = None,
) -> dict:
    """MCP wrapper around `memory.common.project.create_standalone_project`.

    Performs MCP-shaped validation (teams, parent, owner, due_on) up-front
    and translates any orchestration-layer ProjectError to the legacy
    error-dict shape.
    """
    return await _run_create(
        session=session,
        user=user,
        kind="standalone",
        team_ids=team_ids,
        parent_id=parent_id,
        owner_id=owner_id,
        due_on=due_on,
        create_call=lambda teams, due_on_dt: _create_standalone_project(
            session,
            teams,
            title,
            description,
            state,
            parent_id,
            owner_id,
            due_on_dt,
            doc_url,
        ),
    )


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
    doc_url: str | None = None,
    create_repo: bool = False,
    private: bool = True,
) -> dict:
    """MCP wrapper around `memory.common.project.create_repo_project`."""
    return await _run_create(
        session=session,
        user=user,
        kind="repo",
        team_ids=team_ids,
        parent_id=parent_id,
        owner_id=owner_id,
        due_on=due_on,
        create_call=lambda teams, due_on_dt: _create_repo_project(
            session,
            user,
            teams,
            repo_path,
            description,
            state,
            parent_id,
            title_override,
            owner_id,
            due_on_dt,
            doc_url,
            create_repo=create_repo,
            private=private,
        ),
    )


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
    doc_url: str | None = None,
    create_repo: bool = False,
    private: bool = True,
) -> dict:
    """MCP wrapper around `memory.common.project.create_milestone_project`."""
    return await _run_create(
        session=session,
        user=user,
        kind="milestone",
        team_ids=team_ids,
        parent_id=parent_id,
        owner_id=owner_id,
        due_on=due_on,
        create_call=lambda teams, due_on_dt: _create_milestone_project(
            session,
            user,
            teams,
            repo_path,
            milestone_title,
            description,
            parent_id,
            title_override,
            owner_id,
            due_on_dt,
            doc_url,
            create_repo=create_repo,
            private=private,
        ),
    )


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
    owner_id: int | str | None = _UNSET,
    due_on: str | None = None,
    clear_due_on: bool = False,
    doc_url: str | None = _UNSET,
    repo: str | None = None,
    milestone: str | None = None,
    clear_repo: bool = False,
    clear_milestone: bool = False,
    force: bool = False,
    create_repo: bool = False,
    create_milestone: bool = False,
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
        owner_id: Person ID to assign as project owner. Set to null to clear.
        due_on: Project due date in ISO 8601 format (e.g., "2024-12-31T23:59:59Z")
        clear_due_on: If true, removes the due date (sets to NULL)
        doc_url: URL to the project's documentation (e.g., Google Doc, Notion page, wiki).
                 Set to null to clear.
        repo: GitHub repo path (e.g., "owner/name").
        milestone: GitHub milestone title. Used with repo to create a milestone-level
                   project. Created on GitHub if it doesn't exist.
        create_repo: If True and repo doesn't exist on GitHub, creates it (default: False)
        private: Whether to create repo as private if creating (default: True)
        clear_repo: If True, detach this project from its GitHub repo. Project
                    becomes standalone. Refused if linked GithubItems exist
                    unless force=True. Cannot combine with repo.
        clear_milestone: If True, demote a milestone-level project to repo-level.
                         Refused if linked GithubItems exist unless force=True.
                         Cannot combine with milestone.
        force: When set with clear_repo or clear_milestone, allow detach even
               if linked GithubItems exist. Items will keep their project_id
               but the project will no longer be GitHub-backed.
        create_milestone: If True and milestone doesn't exist on GitHub, create it.
                          Default False.

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

        # Resolve owner_id: _UNSET = skip, None = clear, int = set
        resolved_owner_id: int | None = 0  # 0 = skip (don't change)
        if owner_id is None:
            resolved_owner_id = None  # Explicitly clear
        elif owner_id is not _UNSET:
            try:
                resolved_owner_id = int(owner_id)  # type: ignore[arg-type]
            except (ValueError, TypeError):
                return {"error": f"owner_id must be an integer, got: {owner_id!r}", "project": None}

        # Resolve doc_url: _UNSET = skip, None = clear, str = set
        resolved_doc_url: str | None = _UNSET  # type: ignore[assignment]
        if doc_url is not _UNSET:
            if doc_url is not None:
                error = validate_doc_url(doc_url)
                if error:
                    return {"error": error, "project": None}
            resolved_doc_url = doc_url

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
                resolved_owner_id,
                due_on,
                clear_due_on,
                resolved_doc_url,
                repo=repo,
                milestone=milestone,
                clear_repo=clear_repo,
                clear_milestone=clear_milestone,
                force=force,
                create_repo=create_repo,
                create_milestone=create_milestone,
                private=private,
            )

        # For create paths, convert sentinel to None (new projects don't need "skip")
        create_owner_id = None if resolved_owner_id == 0 else resolved_owner_id
        create_doc_url = None if resolved_doc_url is _UNSET else resolved_doc_url

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
                    owner_id=create_owner_id,
                    due_on=due_on,
                    doc_url=create_doc_url,
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
                    owner_id=create_owner_id,
                    due_on=due_on,
                    doc_url=create_doc_url,
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
            owner_id=create_owner_id,
            due_on=due_on,
            doc_url=create_doc_url,
        )


def handle_clear_repo(session: Any, project: Project, force: bool) -> dict | None:
    """MCP-shaped wrapper for `memory.common.project.handle_clear_repo`.

    Returns an error dict to short-circuit `update_project`, or None to
    indicate the detach was applied (caller should commit).
    """
    try:
        _handle_clear_repo(session, project, force)
    except ProjectError as e:
        return _project_error_response(e)
    return None


def handle_clear_milestone(
    session: Any, project: Project, force: bool
) -> dict | None:
    """MCP-shaped wrapper for `memory.common.project.handle_clear_milestone`.

    Returns an error dict to short-circuit `update_project`, or None to
    indicate the demotion was applied (caller should commit).
    """
    try:
        _handle_clear_milestone(session, project, force)
    except ProjectError as e:
        return _project_error_response(e)
    return None


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
    owner_id: int | None = 0,
    due_on: str | None = None,
    clear_due_on: bool = False,
    doc_url: str | None = _UNSET,  # type: ignore[assignment]
    *,
    repo: str | None = None,
    milestone: str | None = None,
    clear_repo: bool = False,
    clear_milestone: bool = False,
    force: bool = False,
    create_repo: bool = False,
    create_milestone: bool = False,
    private: bool = True,
) -> dict:
    """Update an existing project.

    Args:
        owner_id: 0 = skip (don't change), None = clear, positive int = set.
        doc_url: _UNSET = skip (don't change), None = clear, str = set.
    """
    # Fetch project with access check
    query = filter_projects_query(
        session, user, session.query(Project).filter(Project.id == project_id)
    )
    query = query.options(selectinload(Project.owner))
    project = query.first()

    if not project:
        return {"error": f"Project not found: {project_id}", "project": None}

    # Conflict checks for new args
    if clear_repo and repo is not None:
        return {
            "error": "Cannot combine clear_repo=True with repo=...",
            "project": None,
        }
    if clear_milestone and milestone is not None:
        return {
            "error": "Cannot combine clear_milestone=True with milestone=...",
            "project": None,
        }
    # Cross-clear conflicts: `clear_X=True` + a `Y=...` change (other axis) is
    # ambiguous and would silently drop the `Y` arg via the early-return below.
    # Reject explicitly so callers get a clear error.
    if clear_repo and milestone is not None:
        return {
            "error": "Cannot combine clear_repo=True with milestone=...",
            "project": None,
        }
    if clear_milestone and repo is not None:
        return {
            "error": "Cannot combine clear_milestone=True with repo=...",
            "project": None,
        }

    # `clear_repo`/`clear_milestone` early-return commits and skips the rest of
    # the function. Reject the combination with other mutating args up front so
    # field edits aren't silently dropped.
    if clear_repo or clear_milestone:
        if (
            title is not None
            or description is not None
            or state is not None
            or team_ids is not None
            or parent_id is not None
            or clear_parent
            or owner_id != 0
            or due_on is not None
            or clear_due_on
            or doc_url is not _UNSET
            or repo is not None
            or milestone is not None
        ):
            verb = "clear_repo" if clear_repo else "clear_milestone"
            return {
                "error": (
                    f"Cannot combine {verb}=True with other field changes. "
                    f"Run {verb} as a standalone call, then update other fields."
                ),
                "project": None,
            }

    is_standalone = project.repo_id is None

    # Detach branch — runs before sync-on-mutate (sync would fail anyway)
    if clear_repo:
        error = handle_clear_repo(session, project, force)
        if error:
            return error
        session.commit()
        session.refresh(project)
        children_count = (
            session.query(func.count(Project.id))
            .filter(Project.parent_id == project_id)
            .scalar()
        ) or 0
        return {
            "success": True,
            "created": False,
            "project": project_to_dict(project, children_count=children_count),
        }

    # Demote branch — same shape as detach, runs before sync-on-mutate
    if clear_milestone:
        error = handle_clear_milestone(session, project, force)
        if error:
            return error
        session.commit()
        session.refresh(project)
        children_count = (
            session.query(func.count(Project.id))
            .filter(Project.parent_id == project_id)
            .scalar()
        ) or 0
        return {
            "success": True,
            "created": False,
            "project": project_to_dict(project, children_count=children_count),
        }

    # ------------------------------------------------------------------
    # PRE-MUTATION VALIDATION
    # ------------------------------------------------------------------
    # All cheap validations run BEFORE attach/promote/refresh. Validation
    # failures after a mutation would otherwise commit partial state via
    # `make_session`'s clean-exit commit (db/connection.py) — even though
    # we return an error dict.
    #
    # Validations placed here (order roughly cheapest-first):
    #   - title/description/state forbidden when GitHub-backed (current OR pending attach)
    #   - parent_id existence + circular reference
    #   - owner_id existence
    #   - due_on parseability
    #   - team_ids non-empty + existence + membership
    # ------------------------------------------------------------------

    # `repo`/`milestone` args mean a github-backing change is being applied
    # in this call. Title/description/state edits are only valid for fully
    # standalone projects that are STAYING standalone.
    pending_github_attach = repo is not None or milestone is not None
    if not is_standalone or pending_github_attach:
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

    # Validate owner if changing (0 = skip)
    if owner_id not in (0, None):
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

    # Validate doc_url before any mutation. (`upsert` validates the user-supplied
    # value upfront, but `update_project` may be called by non-MCP paths; keep
    # one validation site here so the function is safe in isolation.)
    if doc_url is not _UNSET and doc_url is not None:
        error = validate_doc_url(doc_url)
        if error:
            return {"error": error, "project": None}

    # Attach / promote / rebind branch
    just_attached = False
    repo_was_just_created = False
    if repo is not None or milestone is not None:
        # milestone alone on a standalone project is an error
        if repo is None and milestone is not None and is_standalone:
            return {
                "error": (
                    "Cannot set milestone on a standalone project without also "
                    "specifying repo."
                ),
                "project": None,
            }

        # repo set: validate format up front so a malformed `repo=` (e.g. no
        # slash) fails with a clear error instead of falling through into the
        # already-attached comparison and producing a misleading
        # "use clear_repo" message.
        if repo is not None and "/" not in repo:
            return {
                "error": (
                    f"Invalid repo path '{repo}'. Expected format: owner/name"
                ),
                "project": None,
            }

        # repo set: validate against current attachment
        if repo is not None and not is_standalone:
            target_owner, target_name = repo.split("/", 1)
            if (
                project.repo.owner.lower() != target_owner.lower()
                or project.repo.name.lower() != target_name.lower()
            ):
                return {
                    "error": (
                        f"Project is already attached to "
                        f"{project.repo.owner}/{project.repo.name}. "
                        "Use clear_repo=True first to detach."
                    ),
                    "project": None,
                }

        # Attach if standalone
        if repo is not None and is_standalone:
            try:
                repo_was_just_created = handle_attach(
                    session, user, project, repo, create_repo, private
                )
            except ProjectError as e:
                return _project_error_response(e)
            is_standalone = False
            just_attached = True

        # Promote to milestone if requested. Re-pinning to the same milestone
        # is treated as idempotent: we run the promote path again so a fresh
        # GitHub-side rename overlays correctly. (Old behaviour rejected
        # re-pin based on the locally-cached title, which goes stale after a
        # rename and produces a misleading "already pinned" error.)
        if milestone is not None:
            try:
                promote_client, _ = get_github_client(
                    session,
                    f"{project.repo.owner}/{project.repo.name}",
                    user.id,
                )
            except ValueError as e:
                return {
                    "error": f"Could not get GitHub client: {e}",
                    "project": None,
                }
            # If we just created the repo on GitHub, skip the post-promote
            # `get_repo` round-trip — eventual consistency means it would
            # return None and falsely deactivate the freshly-created repo.
            try:
                handle_promote_to_milestone(
                    session,
                    promote_client,
                    project,
                    milestone,
                    create_milestone,
                    skip_refresh=repo_was_just_created,
                )
            except ProjectError as e:
                return _project_error_response(e)
            just_attached = True

    # Sync-on-mutate: refresh from GitHub before applying user edits
    # (skip when detaching, since GitHub may be broken — that's why user is detaching)
    # (skip when just_attached, because handle_attach already refreshed)
    if not is_standalone and not just_attached:
        try:
            client, _ = get_github_client(
                session, f"{project.repo.owner}/{project.repo.name}", user.id
            )
        except ValueError as e:
            return {
                "error": f"Could not get GitHub client: {e}",
                "project": None,
            }
        try:
            refresh_from_github(session, client, project)
        except (RepoArchivedError, RepoMissingError) as e:
            # Persist the deactivation side-effect explicitly so periodic
            # sync sees the inactivation. Other in-memory project mutations
            # are discarded by the lack of commit on this error path.
            #
            # Note: this only flips repo.active=False. The periodic sync
            # (`workers/tasks/github.py:close_open_repo_projects`) is what
            # transitions all open child projects of an inactive repo to
            # state="closed". The MCP path here doesn't fan out that
            # cleanup — child projects converge to closed on the next
            # periodic sync cycle. If MCP-path symmetry is ever needed,
            # call `close_open_repo_projects` here.
            if project.repo is not None and project.repo.active is False:
                mark_repo_inactive(session, project.repo)
            else:
                session.rollback()
            return _project_error_response(e)
        except ProjectError as e:
            session.rollback()
            return _project_error_response(e)

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
            try:
                sync_milestone_due_date(project, new_due_on_value)
            except ProjectError as e:
                return _project_error_response(e)

    # Apply updates
    if clear_parent:
        project.parent_id = None
    elif parent_id is not None:
        project.parent_id = parent_id

    # Owner: 0 = skip, None = clear, int = set
    if owner_id is None:
        project.owner_id = None
    elif owner_id != 0:
        project.owner_id = owner_id

    # Due date can be updated for both standalone and GitHub-backed projects
    if clear_due_on:
        project.due_on = None
    elif due_on_dt is not None:
        project.due_on = due_on_dt

    # Doc URL: _UNSET = skip, None = clear, str = set.
    # Validation already ran upfront (see PRE-MUTATION VALIDATION above) and
    # in `upsert`. Just apply the value here.
    if doc_url is not _UNSET:
        project.doc_url = doc_url

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

    # Outbound sync: grant teams access to repo on GitHub (after commit).
    # Two trigger conditions, ORed:
    #   1. team_ids was supplied AND new teams were added (push only the diff).
    #   2. The project was just attached/promoted on this call — we need to grant
    #      every existing team access to the freshly-linked repo, regardless of
    #      whether team_ids was passed. This mirrors create_repo_project's behaviour
    #      and prevents the regression where `upsert(project_id=X, repo=...)` linked
    #      the repo but left the project's existing teams without access on GitHub.
    sync_result = None
    if project.repo:
        teams_to_sync: list[Team] = []
        if teams is not None:
            new_team_ids = {t.id for t in teams}
            added_team_ids = new_team_ids - old_team_ids
            if added_team_ids:
                teams_to_sync = [t for t in teams if t.id in added_team_ids]
        if just_attached:
            # Sync the full current team list — handle_attach/handle_promote may
            # have left the project linked without granting any team access.
            teams_to_sync = list(project.teams)
        if teams_to_sync:
            try:
                client, _ = get_github_client(
                    session, f"{project.repo.owner}/{project.repo.name}", user.id
                )
                if client:
                    sync_result = perform_outbound_sync(
                        client, project.repo.owner, project.repo.name, teams_to_sync
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
