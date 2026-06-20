"""Human-readable error reporting for GitHub API failures.

The org/team write endpoints fail with an opaque 403 when the access token is
missing an OAuth scope (e.g. creating an org invitation needs ``admin:org``).
GitHub advertises the gap in two response headers — ``X-Accepted-OAuth-Scopes``
(what the endpoint requires) and ``X-OAuth-Scopes`` (what the token has) — so we
can turn that into an actionable message instead of a bare "request failed".
"""

import requests

# Classic-PAT scope hierarchy: a granted parent scope implies its children, but
# GitHub only lists the granted parent in X-OAuth-Scopes. We expand the token's
# scopes through this map before comparing, so holding ``admin:org`` correctly
# satisfies an endpoint that advertises ``read:org``.
SCOPE_IMPLIES: dict[str, set[str]] = {
    "repo": {
        "repo:status",
        "repo_deployment",
        "public_repo",
        "repo:invite",
        "security_events",
    },
    "write:packages": {"read:packages"},
    "admin:org": {"write:org", "read:org", "manage_runners:org"},
    "write:org": {"read:org"},
    "admin:public_key": {"write:public_key", "read:public_key"},
    "admin:repo_hook": {"write:repo_hook", "read:repo_hook"},
    "user": {"read:user", "user:email", "user:follow"},
    "write:discussion": {"read:discussion"},
    "admin:gpg_key": {"write:gpg_key", "read:gpg_key"},
    "admin:ssh_signing_key": {"write:ssh_signing_key", "read:ssh_signing_key"},
    "project": {"read:project"},
}


def split_oauth_scopes(header: str | None) -> set[str]:
    """Parse a comma-separated GitHub OAuth-scope header into a set."""
    if not header:
        return set()
    return {scope.strip() for scope in header.split(",") if scope.strip()}


def expand_scopes(scopes: set[str]) -> set[str]:
    """Add scopes implied by held parent scopes (transitive closure)."""
    result = set(scopes)
    pending = list(scopes)
    while pending:
        implied = SCOPE_IMPLIES.get(pending.pop(), set()) - result
        result |= implied
        pending.extend(implied)
    return result


def missing_scope_message(response: requests.Response) -> str | None:
    """Return a scope-gap message if a 403 was caused by a missing OAuth scope.

    Returns None when the failure is not an identifiable scope problem (so the
    caller can fall back to a generic message).
    """
    if response.status_code != 403:
        return None
    accepted = split_oauth_scopes(response.headers.get("X-Accepted-OAuth-Scopes"))
    if not accepted:
        return None
    held = expand_scopes(split_oauth_scopes(response.headers.get("X-OAuth-Scopes")))
    if accepted & held:
        return None  # token already satisfies one of the accepted scopes

    needed = " or ".join(sorted(accepted))
    held_str = ", ".join(sorted(held)) or "none"
    return (
        f"the access token is missing a required OAuth scope: this operation "
        f"needs '{needed}', but the token only has [{held_str}]. Add '{needed}' "
        f"to the personal access token and retry."
    )


def is_already_member_error(error_data: dict) -> bool:
    """Whether a 422 invitation body means the user is already a member/invited.

    GitHub returns 422 both for the harmless already-member / already-invited
    case and for genuine rejections (e.g. over the invitation rate limit), so we
    inspect the messages to tell them apart instead of assuming success.

    This is intentionally a substring heuristic on GitHub's stable wording
    ("...is already a part of this org", "...already invited"). The failure mode
    is a false positive — a genuine rejection whose message happens to contain
    "already" would be treated as a harmless no-op — which is preferable to the
    prior behaviour of reporting every 422 as a successful invite.
    """
    messages = [error_data.get("message", "")]
    messages += [
        item.get("message", "")
        for item in error_data.get("errors", [])
        if isinstance(item, dict)
    ]
    combined = " ".join(m for m in messages if m).lower()
    return "already" in combined


def parse_json_body(response: requests.Response) -> dict:
    """Parse a JSON response body, returning {} when it is not a JSON object.

    Lets callers that promise a structured (never-raising) return value stay so
    even if GitHub sends an empty or non-JSON body — ``Response.json()`` raises
    ``requests.JSONDecodeError`` (a ``RequestException``) in that case.
    """
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def github_error_detail(response: requests.Response) -> str | None:
    """Best-effort extraction of GitHub's error ``message`` from a response."""
    try:
        message = response.json().get("message")
    except (ValueError, AttributeError):
        return None
    return message or None


def describe_github_error(response: requests.Response, action: str) -> str:
    """Build an actionable message for a failed GitHub API response.

    Calls out a missing OAuth scope explicitly when that is the cause; otherwise
    reports the HTTP status and GitHub's own error message.
    """
    scope_problem = missing_scope_message(response)
    if scope_problem:
        return f"{action} failed: {scope_problem}"

    detail = github_error_detail(response)
    suffix = f" — {detail}" if detail else ""
    return f"{action} failed: GitHub returned HTTP {response.status_code}{suffix}."
