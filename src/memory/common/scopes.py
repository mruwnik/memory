"""
Central definition of all valid user scopes.

Scopes control access to MCP tools and API functionality. This module is the
single source of truth for what scopes exist and their metadata.

Usage:
    from memory.common.scopes import SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN
    from memory.common.scopes import VALID_SCOPES, ALL_SCOPE_VALUES, validate_scopes

    # Check if scopes are valid
    invalid = validate_scopes(["read", "invalid_scope"])

    # Get scope info for UI
    for scope in VALID_SCOPES:
        print(f"{scope['value']}: {scope['label']}")
"""

from typing import TypedDict


class ScopeInfo(TypedDict):
    """Metadata about a scope for UI display."""

    value: str
    label: str
    description: str
    category: str  # Grouping for UI display


# ---------------------------------------------------------------------------
# Scope constants — every scope string used in the codebase lives here.
# ---------------------------------------------------------------------------

# Special / admin
SCOPE_ADMIN = "*"

# Core
SCOPE_READ = "read"
SCOPE_WRITE = "write"
SCOPE_OBSERVE = "observe"
SCOPE_OBSERVE_WRITE = "observe:write"
SCOPE_NOTES = "notes"
SCOPE_NOTES_WRITE = "notes:write"

# Integrations
SCOPE_GITHUB = "github"
SCOPE_GITHUB_WRITE = "github:write"
SCOPE_EMAIL = "email"
SCOPE_EMAIL_WRITE = "email:write"
SCOPE_DISCORD = "discord"
SCOPE_DISCORD_WRITE = "discord:write"
SCOPE_DISCORD_ADMIN = "discord-admin"
SCOPE_DISCORD_ADMIN_WRITE = "discord-admin:write"
SCOPE_SLACK = "slack"
SCOPE_SLACK_WRITE = "slack:write"

# Research
SCOPE_FORECAST = "forecast"
SCOPE_FORECAST_WRITE = "forecast:write"

# Organisation & planning
SCOPE_ORGANIZER = "organizer"
SCOPE_ORGANIZER_WRITE = "organizer:write"
SCOPE_PEOPLE = "people"
SCOPE_PEOPLE_WRITE = "people:write"
SCOPE_POLLING = "polling"
SCOPE_POLLING_WRITE = "polling:write"
SCOPE_SCHEDULE = "schedule"
SCOPE_SCHEDULE_WRITE = "schedule:write"
SCOPE_TEAMS = "teams"
SCOPE_TEAMS_WRITE = "teams:write"
SCOPE_PROJECTS = "projects"
SCOPE_PROJECTS_WRITE = "projects:write"

# AI
SCOPE_CLAUDE_AI = "claudeai"

# ---------------------------------------------------------------------------
# Metadata for UI display — grouped by category.
# ---------------------------------------------------------------------------

VALID_SCOPES: list[ScopeInfo] = [
    # Special scopes
    {
        "value": SCOPE_ADMIN,
        "label": "Full Access",
        "description": "Grants access to all features and tools",
        "category": "special",
    },
    # Core functionality
    {
        "value": SCOPE_READ,
        "label": "Read",
        "description": "Search and view knowledge base content",
        "category": "core",
    },
    {
        "value": SCOPE_WRITE,
        "label": "Write",
        "description": "Create and modify knowledge base content",
        "category": "core",
    },
    {
        "value": SCOPE_OBSERVE,
        "label": "Observe",
        "description": "Search observations about user preferences",
        "category": "core",
    },
    {
        "value": SCOPE_OBSERVE_WRITE,
        "label": "Observe (write)",
        "description": "Record observations about user preferences",
        "category": "core",
    },
    {
        "value": SCOPE_NOTES,
        "label": "Notes",
        "description": "View notes",
        "category": "core",
    },
    {
        "value": SCOPE_NOTES_WRITE,
        "label": "Notes (write)",
        "description": "Create and manage notes",
        "category": "core",
    },
    # Integrations
    {
        "value": SCOPE_GITHUB,
        "label": "GitHub",
        "description": "View GitHub repositories, issues, and PRs",
        "category": "integrations",
    },
    {
        "value": SCOPE_GITHUB_WRITE,
        "label": "GitHub (write)",
        "description": "Create/modify GitHub issues, PRs, and team members",
        "category": "integrations",
    },
    {
        "value": SCOPE_EMAIL,
        "label": "Email",
        "description": "View email configuration",
        "category": "integrations",
    },
    {
        "value": SCOPE_EMAIL_WRITE,
        "label": "Email (write)",
        "description": "Send emails via configured accounts",
        "category": "integrations",
    },
    {
        "value": SCOPE_DISCORD,
        "label": "Discord",
        "description": "View Discord channels and message history",
        "category": "integrations",
    },
    {
        "value": SCOPE_DISCORD_WRITE,
        "label": "Discord (write)",
        "description": "Send messages to Discord channels",
        "category": "integrations",
    },
    {
        "value": SCOPE_DISCORD_ADMIN,
        "label": "Discord Admin",
        "description": "View Discord roles, permissions, and categories",
        "category": "integrations",
    },
    {
        "value": SCOPE_DISCORD_ADMIN_WRITE,
        "label": "Discord Admin (write)",
        "description": "Manage Discord roles, permissions, and channels",
        "category": "integrations",
    },
    {
        "value": SCOPE_SLACK,
        "label": "Slack",
        "description": "View Slack channels and message history",
        "category": "integrations",
    },
    {
        "value": SCOPE_SLACK_WRITE,
        "label": "Slack (write)",
        "description": "Send messages and reactions to Slack",
        "category": "integrations",
    },
    # Research & Analysis
    {
        "value": SCOPE_FORECAST,
        "label": "Forecasts",
        "description": "View prediction market data and analysis",
        "category": "research",
    },
    {
        "value": SCOPE_FORECAST_WRITE,
        "label": "Forecasts (write)",
        "description": "Manage forecast watchlist and cache",
        "category": "research",
    },
    # Organization & Planning
    {
        "value": SCOPE_ORGANIZER,
        "label": "Organizer",
        "description": "View calendar events and tasks",
        "category": "organization",
    },
    {
        "value": SCOPE_ORGANIZER_WRITE,
        "label": "Organizer (write)",
        "description": "Create and modify calendar events and tasks",
        "category": "organization",
    },
    {
        "value": SCOPE_PEOPLE,
        "label": "People",
        "description": "View people/contacts information",
        "category": "organization",
    },
    {
        "value": SCOPE_PEOPLE_WRITE,
        "label": "People (write)",
        "description": "Create, modify, and delete people/contacts",
        "category": "organization",
    },
    {
        "value": SCOPE_POLLING,
        "label": "Polling",
        "description": "View polls",
        "category": "organization",
    },
    {
        "value": SCOPE_POLLING_WRITE,
        "label": "Polling (write)",
        "description": "Create and manage polls for scheduling",
        "category": "organization",
    },
    {
        "value": SCOPE_SCHEDULE,
        "label": "Schedule",
        "description": "View scheduled tasks and reminders",
        "category": "organization",
    },
    {
        "value": SCOPE_SCHEDULE_WRITE,
        "label": "Schedule (write)",
        "description": "Create and manage scheduled tasks",
        "category": "organization",
    },
    {
        "value": SCOPE_TEAMS,
        "label": "Teams",
        "description": "View teams and membership",
        "category": "organization",
    },
    {
        "value": SCOPE_TEAMS_WRITE,
        "label": "Teams (write)",
        "description": "Create and manage teams and membership",
        "category": "organization",
    },
    {
        "value": SCOPE_PROJECTS,
        "label": "Projects",
        "description": "View projects",
        "category": "organization",
    },
    {
        "value": SCOPE_PROJECTS_WRITE,
        "label": "Projects (write)",
        "description": "Create and manage projects",
        "category": "organization",
    },
    # AI
    {
        "value": SCOPE_CLAUDE_AI,
        "label": "Claude AI",
        "description": "Access Claude AI features",
        "category": "ai",
    },
]

# Set of all valid scope values for fast lookup
ALL_SCOPE_VALUES: frozenset[str] = frozenset(s["value"] for s in VALID_SCOPES)

# Default scope for new users
DEFAULT_SCOPES: list[str] = [SCOPE_READ]


def validate_scopes(scopes: list[str]) -> list[str]:
    """Validate a list of scopes and return any invalid ones.

    Args:
        scopes: List of scope strings to validate

    Returns:
        List of invalid scope names (empty if all valid)
    """
    return [s for s in scopes if s not in ALL_SCOPE_VALUES]


def has_scope(user_scopes: list[str], required: str) -> bool:
    """Check if a user has a required scope.

    Args:
        user_scopes: List of scopes the user has
        required: The scope to check for

    Returns:
        True if user has the required scope or wildcard
    """
    return SCOPE_ADMIN in user_scopes or required in user_scopes


def has_any_scope(user_scopes: list[str], required: list[str]) -> bool:
    """Check if a user has any of the required scopes.

    Args:
        user_scopes: List of scopes the user has
        required: List of scopes to check for (any match is sufficient)

    Returns:
        True if user has any of the required scopes or wildcard
    """
    if SCOPE_ADMIN in user_scopes:
        return True
    return any(s in user_scopes for s in required)


def get_scopes_by_category() -> dict[str, list[ScopeInfo]]:
    """Group scopes by category for UI display.

    Returns:
        Dict mapping category names to lists of scopes
    """
    result: dict[str, list[ScopeInfo]] = {}
    for scope in VALID_SCOPES:
        category = scope["category"]
        if category not in result:
            result[category] = []
        result[category].append(scope)
    return result
