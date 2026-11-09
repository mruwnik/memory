"""Core GitHub client with authentication and GraphQL support."""

import logging
import time
from typing import Any, cast

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

# The key an item node gets listing the paths, below it, of fields GitHub withheld.
UNREADABLE = "__unreadable"

# What a GraphQL field holds, and the permissions that read it. The commit
# block also asks for the check rollup, so reading commits takes all three.
FIELD_ACCESS: dict[str, tuple[str, tuple[str, ...]]] = {
    "statusCheckRollup": ("check results", ("Checks", "Commit statuses")),
    "commits": ("commits", ("Contents", "Checks", "Commit statuses")),
    "commit": ("commits", ("Contents", "Checks", "Commit statuses")),
    "pullRequests": ("pull requests", ("Pull requests",)),
    "pullRequest": ("pull requests", ("Pull requests",)),
    "issues": ("issues", ("Issues",)),
    "issue": ("issues", ("Issues",)),
    "milestones": ("milestones", ("Issues",)),
    "milestone": ("milestones", ("Issues",)),
}


class GithubQueryError(Exception):
    """GitHub answered a query with errors instead of the data asked for."""


def join_words(words: list[str]) -> str:
    return " and ".join(filter(None, [", ".join(words[:-1]), *words[-1:]]))


def field_access(path: list[str | int]) -> tuple[str, tuple[str, ...]]:
    """The deepest field of the path that says what was withheld, else its last field."""
    fields = [f for f in path if isinstance(f, str)]
    known = [FIELD_ACCESS[f] for f in fields if f in FIELD_ACCESS]
    return known[-1] if known else (fields[-1] if fields else "the repository", ())


def access_message(auth_type: str, repo: str, paths: list[list[str | int]]) -> str:
    """What the account can't read on the repo, and what to grant it to."""
    found = [field_access(p) for p in paths]
    what = join_words(list(dict.fromkeys(w for w, _ in found)))
    perms = list(dict.fromkeys(p for _, ps in found for p in ps))
    reads = join_words([f"{p} (read)" for p in perms])
    if auth_type == "app":
        grant = f"grant it {reads}" if perms else "grant it read access to them"
        return (
            f"The GitHub App can't read {what} on {repo}: {grant}, "
            "accept the new permissions on the installation, then Sync now."
        )
    grant = f"give it {reads} on this repository" if perms else "give it read access to them"
    return (
        f"The access token can't read {what} on {repo}: {grant}, then Sync now. "
        "A fine-grained token must list the repository and may need the organization's approval; "
        "a classic token needs the repo scope and, for organizations with SSO, authorizing for it."
    )


def node_at(data: Any, path: list[str | int]) -> Any:
    for key in path:
        try:
            data = data[key]
        except (KeyError, IndexError, TypeError):
            return None
    return data


def withheld_below(error: dict[str, Any], depth: int | None) -> bool:
    """Whether GitHub withheld a field inside an item rather than the item itself."""
    path = error.get("path") or []
    return depth is not None and error.get("type") == "FORBIDDEN" and len(path) > depth


class GithubClientCore:
    """Base client with authentication and core API methods."""

    def __init__(self, credentials: GithubCredentials):
        self.credentials = credentials
        self.session = requests.Session()
        # Paths of fields GitHub withheld from queries it otherwise answered.
        self.unreadable: list[list[str | int]] = []
        self._setup_auth()

    def unreadable_message(self, repo: str) -> str | None:
        """What to grant so the fields withheld so far can be read, if any were."""
        return access_message(self.credentials.auth_type, repo, self.unreadable) if self.unreadable else None

    def checked(
        self,
        repo: str,
        data: dict[str, Any] | None,
        errors: list[dict[str, Any]] | None,
        item_depth: int | None = None,
    ) -> dict[str, Any]:
        """The data of a query's answer, or GithubQueryError if GitHub refused it.

        Fields GitHub withheld inside an item (paths deeper than ``item_depth``)
        cost only those fields: the item is kept, its node lists them under
        UNREADABLE, and unreadable_message says what to grant.
        """
        if data is None and not errors:
            raise GithubQueryError(f"GitHub didn't answer a query for {repo}")
        gaps = [e for e in errors or [] if data is not None and withheld_below(e, item_depth)]
        fatal = [e for e in errors or [] if e not in gaps]
        if fatal and all(e.get("type") == "FORBIDDEN" for e in fatal):
            raise GithubQueryError(
                access_message(self.credentials.auth_type, repo, [e.get("path") or [] for e in fatal])
            )
        if fatal:
            reasons = "; ".join(dict.fromkeys(e.get("message") or "unknown error" for e in fatal))
            raise GithubQueryError(f"GitHub rejected a query for {repo}: {reasons}")
        for e in gaps:
            path = e["path"]
            node = node_at(data, path[:item_depth])
            if isinstance(node, dict):
                node.setdefault(UNREADABLE, []).append(path[item_depth:])
            self.unreadable.append(path)
        return cast(dict[str, Any], data)

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

    def _generate_app_jwt(self) -> str:
        """Generate a short-lived app-level JWT for GitHub App auth."""
        try:
            import jwt
        except ImportError:
            raise ImportError("PyJWT is required for GitHub App authentication")

        if not self.credentials.app_id or not self.credentials.private_key:
            raise ValueError("app_id and private_key required for app auth")

        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + 600,
            "iss": str(self.credentials.app_id),
        }
        return jwt.encode(payload, self.credentials.private_key, algorithm="RS256")

    def _get_installation_token(self) -> str:
        """Get installation access token for GitHub App."""
        if not self.credentials.installation_id:
            raise ValueError("installation_id required for app auth")

        jwt_token = self._generate_app_jwt()

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

    def get_app_installation(self) -> dict[str, Any]:
        """Fetch installation metadata for App auth using an app-level JWT.

        The installation token used for normal API calls cannot read
        ``/app/installations/...``; that endpoint needs the app JWT.
        """
        if self.credentials.auth_type != "app":
            raise ValueError("get_app_installation requires app auth")
        if not self.credentials.installation_id:
            raise ValueError("installation_id required for app auth")

        jwt_token = self._generate_app_jwt()
        response = requests.get(
            f"{GITHUB_API_URL}/app/installations/{self.credentials.installation_id}",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def get_authenticated_login(self) -> str:
        """Return the GitHub login that owns the configured credentials.

        For PAT auth this is the token owner (``GET /user``). For App
        auth there is no user, so it is the account the installation
        belongs to (``GET /app/installations/{id}``). The value comes
        straight from GitHub, so callers may treat it as a verified
        identity rather than self-attested input.
        """
        if self.credentials.auth_type == "app":
            account = self.get_app_installation().get("account") or {}
            login = account.get("login")
            if not login:
                raise ValueError("GitHub installation response missing account login")
            return login

        login = self.get_authenticated_user().get("login")
        if not login:
            raise ValueError("GitHub /user response missing login")
        return login
