"""Core GitHub client with authentication and GraphQL support."""

import logging
import time
from typing import Any

import requests

from .types import (
    GITHUB_API_URL,
    GITHUB_GRAPHQL_URL,
    MIN_RATE_LIMIT_REMAINING,
    RATE_LIMIT_REMAINING_HEADER,
    RATE_LIMIT_RESET_HEADER,
    GithubCredentials,
)

logger = logging.getLogger(__name__)


class GithubClientCore:
    """Base client with authentication and core API methods."""

    def __init__(self, credentials: GithubCredentials):
        self.credentials = credentials
        self.session = requests.Session()
        self._setup_auth()

    @staticmethod
    def _extract_nested(
        data: dict[str, Any] | None, *keys: str, default: Any = None
    ) -> Any:
        """Safely extract a value from nested dicts.

        Example:
            _extract_nested(data, "repository", "issue", "id")
            is equivalent to data.get("repository", {}).get("issue", {}).get("id")
        """
        result = data
        for key in keys:
            if result is None or not isinstance(result, dict):
                return default
            result = result.get(key)
        return result if result is not None else default

    def _graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        operation_name: str | None = None,
        timeout: int = 30,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
        """Execute a GraphQL query/mutation and return (data, errors).

        Args:
            query: The GraphQL query or mutation string
            variables: Variables to pass to the query
            operation_name: Optional operation name for logging
            timeout: Request timeout in seconds

        Returns:
            Tuple of (data, errors) where:
            - data is the "data" field from response, or None on HTTP error
            - errors is the "errors" field from response, or None if no errors
        """
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        max_rate_limit_retries = 3
        response = None
        for attempt in range(max_rate_limit_retries + 1):
            try:
                response = self.session.post(
                    GITHUB_GRAPHQL_URL,
                    json=payload,
                    timeout=timeout,
                )
            except requests.RequestException as e:
                op = operation_name or "GraphQL request"
                logger.warning(f"Failed {op}: {e}")
                return None, None

            # Handle rate limit errors (403) with retry
            if response.status_code != 403:
                response.raise_for_status()
                self._handle_rate_limit(response)
                break

            remaining = response.headers.get(RATE_LIMIT_REMAINING_HEADER)
            if remaining is None or int(remaining) != 0:
                response.raise_for_status()
                self._handle_rate_limit(response)
                break

            if attempt >= max_rate_limit_retries:
                continue  # Will fall through to else block

            reset_time = int(response.headers.get(RATE_LIMIT_RESET_HEADER, 0))
            sleep_time = max(reset_time - time.time(), 0) + 1
            logger.warning(
                f"GitHub rate limited, sleeping for {sleep_time}s "
                f"(attempt {attempt + 1}/{max_rate_limit_retries})"
            )
            time.sleep(sleep_time)
        else:
            # All retries exhausted (only reachable if we hit rate limit every time)
            op = operation_name or "GraphQL request"
            logger.warning(f"{op} failed: rate limit exhausted after {max_rate_limit_retries} retries")
            return None, None

        result = response.json()
        data = result.get("data")
        errors = result.get("errors")

        if errors:
            op = operation_name or "GraphQL request"
            if data:
                # Partial success - some data returned but with errors
                logger.warning(f"{op} partial success with errors: {errors}")
            else:
                logger.warning(f"{op} failed: {errors}")

        return data, errors

    def _setup_auth(self) -> None:
        if self.credentials.auth_type == "pat":
            self.session.headers["Authorization"] = (
                f"Bearer {self.credentials.access_token}"
            )
        elif self.credentials.auth_type == "app":
            # Generate JWT and get installation token
            token = self._get_installation_token()
            self.session.headers["Authorization"] = f"Bearer {token}"

        self.session.headers["Accept"] = "application/vnd.github+json"
        self.session.headers["X-GitHub-Api-Version"] = "2022-11-28"
        self.session.headers["User-Agent"] = "memory-kb-github-sync"

    def _get_installation_token(self) -> str:
        """Get installation access token for GitHub App."""
        try:
            import jwt
        except ImportError:
            raise ImportError("PyJWT is required for GitHub App authentication")

        if not self.credentials.app_id or not self.credentials.private_key:
            raise ValueError("app_id and private_key required for app auth")
        if not self.credentials.installation_id:
            raise ValueError("installation_id required for app auth")

        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + 600,
            "iss": self.credentials.app_id,
        }
        jwt_token = jwt.encode(
            payload, self.credentials.private_key, algorithm="RS256"
        )

        # Retry with exponential backoff for transient errors
        max_retries = 3
        retry_delay = 1.0

        for attempt in range(max_retries):
            try:
                response = requests.post(
                    f"{GITHUB_API_URL}/app/installations/{self.credentials.installation_id}/access_tokens",
                    headers={
                        "Authorization": f"Bearer {jwt_token}",
                        "Accept": "application/vnd.github+json",
                    },
                    timeout=30,
                )
                response.raise_for_status()
                return response.json()["token"]
            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"GitHub token fetch attempt {attempt + 1}/{max_retries} failed: {e}. "
                        f"Retrying in {retry_delay}s..."
                    )
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logger.error(f"GitHub token fetch failed after {max_retries} attempts: {e}")
                    raise

        # Unreachable - loop always returns or raises, but needed for type checker
        raise RuntimeError("Failed to fetch GitHub token")

    def _handle_rate_limit(self, response: requests.Response) -> None:
        """Check rate limits and sleep if necessary."""
        remaining = int(response.headers.get(RATE_LIMIT_REMAINING_HEADER, 100))
        if remaining < MIN_RATE_LIMIT_REMAINING:
            reset_time = int(response.headers.get(RATE_LIMIT_RESET_HEADER, 0))
            sleep_time = max(reset_time - time.time(), 0) + 1
            logger.warning(f"Rate limit low ({remaining}), sleeping for {sleep_time}s")
            time.sleep(sleep_time)

    def get_authenticated_user(self) -> dict[str, Any]:
        """Get information about the authenticated user."""
        response = self.session.get(f"{GITHUB_API_URL}/user", timeout=30)
        response.raise_for_status()
        return response.json()
