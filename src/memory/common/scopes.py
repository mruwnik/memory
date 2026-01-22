"""
Central definition of all valid user scopes.

Scopes control access to MCP tools and API functionality. This module is the
single source of truth for what scopes exist and their metadata.

Usage:
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


# All valid scopes with their metadata
# Order matters for UI display - grouped by category
VALID_SCOPES: list[ScopeInfo] = [
    # Special scopes
    {
        "value": "*",
        "label": "Full Access",
        "description": "Grants access to all features and tools",
        "category": "special",
    },
    # Core functionality
    {
        "value": "read",
        "label": "Read",
        "description": "Search and view knowledge base content",
        "category": "core",
    },
    {
        "value": "observe",
        "label": "Observe",
        "description": "Record and search observations about user preferences",
        "category": "core",
    },
    {
        "value": "notes",
        "label": "Notes",
        "description": "Create and manage notes",
        "category": "core",
    },
    # Integrations
    {
        "value": "github",
        "label": "GitHub",
        "description": "Access GitHub repositories, issues, and PRs",
        "category": "integrations",
    },
    {
        "value": "email",
        "label": "Email",
        "description": "Send emails via configured accounts",
        "category": "integrations",
    },
    {
        "value": "discord",
        "label": "Discord",
        "description": "Send messages and access Discord channels",
        "category": "integrations",
    },
    # Research & Analysis
    {
        "value": "forecast",
        "label": "Forecasts",
        "description": "Access prediction market data and analysis tools",
        "category": "research",
    },
    # Organization & Planning
    {
        "value": "organizer",
        "label": "Organizer",
        "description": "Access calendar events and task management",
        "category": "organization",
    },
    {
        "value": "people",
        "label": "People",
        "description": "Manage people/contacts information",
        "category": "organization",
    },
    {
        "value": "polling",
        "label": "Polling",
        "description": "Create and manage polls for scheduling",
        "category": "organization",
    },
    {
        "value": "schedule",
        "label": "Schedule",
        "description": "Schedule messages and reminders",
        "category": "organization",
    },
    # Administration
    {
        "value": "admin:users",
        "label": "User Admin",
        "description": "Create, modify, and delete users",
        "category": "admin",
    },
]

# Set of all valid scope values for fast lookup
ALL_SCOPE_VALUES: frozenset[str] = frozenset(s["value"] for s in VALID_SCOPES)

# Default scope for new users
DEFAULT_SCOPES: list[str] = ["read"]

# Scope that grants all permissions (wildcard)
WILDCARD_SCOPE = "*"

# Admin scope for user management
ADMIN_SCOPE = "admin:users"


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
    return WILDCARD_SCOPE in user_scopes or required in user_scopes


def has_any_scope(user_scopes: list[str], required: list[str]) -> bool:
    """Check if a user has any of the required scopes.

    Args:
        user_scopes: List of scopes the user has
        required: List of scopes to check for (any match is sufficient)

    Returns:
        True if user has any of the required scopes or wildcard
    """
    if WILDCARD_SCOPE in user_scopes:
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
