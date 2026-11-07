"""Repo webhooks, and the App's single webhook, pointed at Memory's ingest endpoint."""

from typing import Any

import requests

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


class HooksMixin(GithubClientCore):
    def repo_hooks_url(self, owner: str, repo: str) -> str:
        return f"{GITHUB_API_URL}/repos/{owner}/{repo}/hooks"

    def list_repo_hooks(self, owner: str, repo: str) -> list[dict[str, Any]]:
        response = self.session.get(
            self.repo_hooks_url(owner, repo), params={"per_page": 100}, timeout=30
        )
        response.raise_for_status()
        return response.json()

    def create_repo_hook(self, owner: str, repo: str, config: dict[str, str]) -> None:
        body = {"name": "web", "active": True, "events": HOOK_EVENTS, "config": config}
        self.session.post(self.repo_hooks_url(owner, repo), json=body, timeout=30).raise_for_status()

    def update_repo_hook(self, owner: str, repo: str, hook_id: int, config: dict[str, str]) -> None:
        # GitHub never returns the secret, so the whole config is sent every time.
        body = {"active": True, "events": HOOK_EVENTS, "config": config}
        url = f"{self.repo_hooks_url(owner, repo)}/{hook_id}"
        self.session.patch(url, json=body, timeout=30).raise_for_status()

    def delete_repo_hook(self, owner: str, repo: str, hook_id: int) -> None:
        url = f"{self.repo_hooks_url(owner, repo)}/{hook_id}"
        self.session.delete(url, timeout=30).raise_for_status()

    def update_app_hook_config(self, config: dict[str, str]) -> None:
        """Point the GitHub App's one webhook at us. Needs the app JWT, not the installation token."""
        response = requests.patch(
            f"{GITHUB_API_URL}/app/hook/config",
            json=config,
            headers={
                "Authorization": f"Bearer {self._generate_app_jwt()}",
                "Accept": "application/vnd.github+json",
            },
            timeout=30,
        )
        response.raise_for_status()
