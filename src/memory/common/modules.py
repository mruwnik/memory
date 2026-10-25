"""Optional features the operator switches on with ENABLE_MODULES / DISABLE_MODULES."""

from memory.common.scopes import (
    SCOPE_CLAUDE_SESSIONS,
    SCOPE_PROJECTS,
    SCOPE_PROJECTS_WRITE,
    SCOPE_TEAMS,
    SCOPE_TEAMS_WRITE,
)

CLAUDE_CONTAINERS = "claude_containers"
MULTI_USER = "multi_user"
TEAMS = "teams"

# Module name -> on by default.
MODULES: dict[str, bool] = {
    CLAUDE_CONTAINERS: False,
    MULTI_USER: False,
    TEAMS: False,
}

MODULE_DESCRIPTIONS: dict[str, str] = {
    CLAUDE_CONTAINERS: "Claude Code containers, their configurations and scheduled Claude sessions",
    MULTI_USER: "More than one human user",
    TEAMS: "Teams, their members, and the projects they work on",
}

MODULE_MCP_SERVERS: dict[str, frozenset[str]] = {
    CLAUDE_CONTAINERS: frozenset({"containers"}),
    TEAMS: frozenset({"teams", "projects"}),
}

MODULE_SCOPES: dict[str, frozenset[str]] = {
    CLAUDE_CONTAINERS: frozenset({SCOPE_CLAUDE_SESSIONS}),
    TEAMS: frozenset({SCOPE_TEAMS, SCOPE_TEAMS_WRITE, SCOPE_PROJECTS, SCOPE_PROJECTS_WRITE}),
}


def module_config_errors(enable: frozenset[str], disable: frozenset[str]) -> list[str]:
    errors = []
    if unknown := (enable | disable) - set(MODULES):
        errors.append(
            f"unknown modules {', '.join(sorted(unknown))} in ENABLE_MODULES/DISABLE_MODULES "
            f"(known: {', '.join(MODULES)})"
        )
    if both := enable & disable:
        errors.append(
            f"modules {', '.join(sorted(both))} are in both ENABLE_MODULES and DISABLE_MODULES"
        )
    return errors


def resolve_modules(
    enable: frozenset[str], disable: frozenset[str], defaults: dict[str, bool] = MODULES
) -> frozenset[str]:
    """Bad names are left to `module_config_errors`, which stops the process at startup."""
    on = {name for name, default in defaults.items() if default}
    return frozenset(name for name in (on | enable) - disable if name in defaults)


def servers_disabled(enabled: frozenset[str]) -> set[str]:
    return {
        server
        for name, servers in MODULE_MCP_SERVERS.items()
        if name not in enabled
        for server in servers
    }


def server_module(server: str) -> str | None:
    return next(
        (name for name, servers in MODULE_MCP_SERVERS.items() if server in servers),
        None,
    )


def scopes_hidden(enabled: frozenset[str]) -> set[str]:
    return {
        scope for name, scopes in MODULE_SCOPES.items() if name not in enabled for scope in scopes
    }
