"""Org and repo webhooks pointed at Memory's ingest endpoint."""

from typing import Any

from memory.common import settings

from .core import GithubClientCore
from .types import GITHUB_API_URL

# The events api/github_webhooks.py acts on.
HOOK_EVENTS = [
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
    "issue_comment",
    "issues",
    "check_run",
    "check_suite",
    "status",
]


def webhook_url(account_id: int) -> str:
    return f"{settings.SERVER_URL}/github/webhook/{account_id}"


def hook_config(account_id: int, secret: str) -> dict[str, str]:
    return {
        "url": webhook_url(account_id),
        "content_type": "json",
        "secret": secret,
        "insecure_ssl": "0",
    }


def repo_hooks(owner: str, repo: str) -> str:
    return f"{GITHUB_API_URL}/repos/{owner}/{repo}/hooks"


def org_hooks(org: str) -> str:
    return f"{GITHUB_API_URL}/orgs/{org}/hooks"


class HooksMixin(GithubClientCore):
    """Hooks at ``hooks_url``, either ``repo_hooks(...)`` or ``org_hooks(...)``."""

    def owner_type(self, login: str) -> str:
        """'User' or 'Organization'."""
        response = self.session.get(f"{GITHUB_API_URL}/users/{login}", timeout=30)
        response.raise_for_status()
        return response.json()["type"]

    def list_hooks(self, hooks_url: str) -> list[dict[str, Any]]:
        response = self.session.get(hooks_url, params={"per_page": 100}, timeout=30)
        response.raise_for_status()
        return response.json()

    def create_hook(self, hooks_url: str, config: dict[str, str]) -> None:
        body = {"name": "web", "active": True, "events": HOOK_EVENTS, "config": config}
        self.session.post(hooks_url, json=body, timeout=30).raise_for_status()

    def update_hook(self, hooks_url: str, hook_id: int, config: dict[str, str]) -> None:
        # GitHub never returns the secret, so the whole config is sent every time.
        body = {"active": True, "events": HOOK_EVENTS, "config": config}
        self.session.patch(f"{hooks_url}/{hook_id}", json=body, timeout=30).raise_for_status()

    def delete_hook(self, hooks_url: str, hook_id: int) -> None:
        self.session.delete(f"{hooks_url}/{hook_id}", timeout=30).raise_for_status()
