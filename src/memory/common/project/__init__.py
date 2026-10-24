"""Project orchestration: DB <-> GitHub state management.

This package is MCP-agnostic. It raises typed exceptions instead of
returning error dicts, so that non-MCP callers (CLI tools, celery tasks,
direct API consumers) can use it without dragging in MCP-specific
response shapes.
"""

from memory.common.project.attach import (
    handle_attach,
    handle_clear_milestone,
    handle_clear_repo,
    handle_promote_to_milestone,
)
from memory.common.project.client import (
    ensure_github_repo,
    get_github_client,
    get_github_client_for_org,
)
from memory.common.project.creation import (
    ProjectCreationResult,
    create_milestone_project,
    create_project_with_retry,
    create_repo_project,
    create_standalone_project,
    find_existing_project_by_repo,
)
from memory.common.project.errors import (
    GithubClientUnavailableError,
    GithubMilestoneSyncError,
    GithubSyncError,
    InvalidRepoPathError,
    LinkedItemsError,
    MilestoneCreationFailedError,
    MilestoneMissingError,
    MilestoneNotFoundOnGithubError,
    ProjectAlreadyAttachedError,
    ProjectError,
    ProjectIdGenerationError,
    ProjectStateInconsistentError,
    RepoArchivedError,
    RepoCreationFailedError,
    RepoMissingError,
    RepoNotFoundOnGithubError,
)
from memory.common.project.sync import (
    mark_repo_inactive,
    refresh_from_github,
    sync_milestone_due_date,
)


__all__ = [
    # attach
    "handle_attach",
    "handle_clear_milestone",
    "handle_clear_repo",
    "handle_promote_to_milestone",
    # client
    "ensure_github_repo",
    "get_github_client",
    "get_github_client_for_org",
    # creation
    "ProjectCreationResult",
    "create_milestone_project",
    "create_project_with_retry",
    "create_repo_project",
    "create_standalone_project",
    "find_existing_project_by_repo",
    # sync
    "mark_repo_inactive",
    "refresh_from_github",
    "sync_milestone_due_date",
    # errors
    "GithubClientUnavailableError",
    "GithubMilestoneSyncError",
    "GithubSyncError",
    "InvalidRepoPathError",
    "LinkedItemsError",
    "MilestoneCreationFailedError",
    "MilestoneMissingError",
    "MilestoneNotFoundOnGithubError",
    "ProjectAlreadyAttachedError",
    "ProjectError",
    "ProjectIdGenerationError",
    "ProjectStateInconsistentError",
    "RepoArchivedError",
    "RepoCreationFailedError",
    "RepoMissingError",
    "RepoNotFoundOnGithubError",
]
