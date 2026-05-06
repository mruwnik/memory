"""Exceptions raised by project orchestration.

Each exception carries a default human-readable message that matches the
strings the MCP layer used to embed in `{"error": ..., "project": None}`
response dicts. The MCP layer now catches these and translates them back
to that shape; non-MCP callers can let them propagate.
"""


class ProjectError(Exception):
    """Base class for all project orchestration errors."""


# --- GitHub-side state inconsistencies ---


class GithubSyncError(ProjectError):
    """GitHub state diverged from what the DB expects."""


class RepoMissingError(GithubSyncError):
    """The backing repo no longer exists on GitHub (404)."""

    def __init__(self, owner: str, name: str):
        super().__init__(
            f"Repo {owner}/{name} no longer exists on GitHub. "
            "Detach with clear_repo=True."
        )
        self.owner = owner
        self.name = name


class RepoArchivedError(GithubSyncError):
    """The backing repo is archived on GitHub."""

    def __init__(self, owner: str, name: str):
        super().__init__(
            f"Repo {owner}/{name} is archived on GitHub. "
            "Detach with clear_repo=True."
        )
        self.owner = owner
        self.name = name


class MilestoneMissingError(GithubSyncError):
    """The backing milestone no longer exists on GitHub."""

    def __init__(self, owner: str, name: str, number: int):
        super().__init__(
            f"Milestone #{number} no longer exists on GitHub for "
            f"{owner}/{name}. Detach or recreate."
        )
        self.owner = owner
        self.name = name
        self.number = number


class ProjectStateInconsistentError(ProjectError):
    """Project has repo_id but no GithubRepo row, or similar invariant break."""

    def __init__(
        self,
        message: str = "Project has repo_id but no repo row; database is inconsistent.",
    ):
        super().__init__(message)


# --- Attach / promote / detach refused ---


class InvalidRepoPathError(ProjectError):
    """Repo path is not in 'owner/name' format."""

    def __init__(self, repo_path: str):
        super().__init__(
            f"Invalid repo path '{repo_path}'. Expected format: owner/name"
        )
        self.repo_path = repo_path


class ProjectAlreadyAttachedError(ProjectError):
    """Caller tried to attach to a different repo without detaching first."""

    def __init__(self, owner: str, name: str):
        super().__init__(
            f"Project is already attached to {owner}/{name}. "
            "Use clear_repo=True first to detach before attaching to a different repo."
        )
        self.owner = owner
        self.name = name


class RepoNotFoundOnGithubError(ProjectError):
    """Repo does not exist on GitHub and create_repo was not set."""

    def __init__(self, repo_path: str, with_create_repo_hint: bool = True):
        if with_create_repo_hint:
            msg = (
                f"Repository '{repo_path}' not found. "
                "Use create_repo=True to create it."
            )
        else:
            msg = f"Repository '{repo_path}' not found on GitHub."
        super().__init__(msg)
        self.repo_path = repo_path


class MilestoneNotFoundOnGithubError(ProjectError):
    """Milestone does not exist on GitHub and create_milestone was not set."""

    def __init__(self, milestone_title: str, owner: str, name: str):
        super().__init__(
            f"Milestone '{milestone_title}' not found in {owner}/{name}. "
            "Use create_milestone=True to create it."
        )
        self.milestone_title = milestone_title
        self.owner = owner
        self.name = name


class RepoCreationFailedError(ProjectError):
    """Failed to create the repo on GitHub."""

    def __init__(self, repo_path: str):
        super().__init__(
            f"Failed to create repository '{repo_path}' on GitHub. "
            "Check that the GitHub account has permission to create repositories in the org."
        )
        self.repo_path = repo_path


class MilestoneCreationFailedError(ProjectError):
    """Failed to create or find the milestone."""

    def __init__(self, milestone_title: str):
        super().__init__(
            f"Failed to find or create milestone '{milestone_title}'"
        )
        self.milestone_title = milestone_title


class GithubClientUnavailableError(ProjectError):
    """No usable GithubClient for the user."""


class LinkedItemsError(ProjectError):
    """Detach/demote refused due to linked GithubItems (use force=True to override)."""

    def __init__(self, item_count: int, action: str):
        verb = "detach" if action == "detach" else "demote"
        super().__init__(
            f"Project has {item_count} linked GithubItems. "
            f"Pass force=True to {verb} anyway, or move/delete the items first."
        )
        self.item_count = item_count
        self.action = action


class ProjectIdGenerationError(ProjectError):
    """Failed to generate a unique negative project ID after retries."""

    def __init__(
        self, message: str = "Failed to generate unique project ID after retries"
    ):
        super().__init__(message)


class GithubMilestoneSyncError(ProjectError):
    """Failed to push milestone state (e.g. due_on) to GitHub."""
