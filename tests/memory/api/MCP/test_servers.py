"""Tests for MCP server configuration and enabling/disabling."""

from unittest.mock import patch

import pytest

from memory.api.MCP.servers import (
    MCPServer,
    ALL_MCP_SERVERS,
    get_enabled_servers,
    is_server_enabled,
    get_server_instance,
)


# --- MCPServer enum tests ---


def test_all_servers_in_enum():
    """Verify all expected servers are in the enum."""
    expected = {
        "books",
        "core",
        "discord",
        "email",
        "forecast",
        "github",
        "journal",
        "meta",
        "notes",
        "organizer",
        "people",
        "polling",
        "projects",
        "reports",
        "scheduler",
        "slack",
        "teams",
    }
    actual = {s.value for s in MCPServer}
    assert actual == expected


def test_all_mcp_servers_matches_enum():
    """ALL_MCP_SERVERS should contain all enum values."""
    assert ALL_MCP_SERVERS == frozenset(s.value for s in MCPServer)


def test_enum_values_are_strings():
    """MCPServer enum values should be usable as strings."""
    assert MCPServer.CORE == "core"
    assert MCPServer.DISCORD == "discord"


# --- get_enabled_servers tests ---


@pytest.mark.parametrize(
    "disabled,expected_enabled",
    [
        (frozenset(), ALL_MCP_SERVERS),
        (frozenset({"slack"}), ALL_MCP_SERVERS - {"slack"}),
        (frozenset({"slack", "forecast"}), ALL_MCP_SERVERS - {"slack", "forecast"}),
        (frozenset({"core", "meta", "books"}), ALL_MCP_SERVERS - {"core", "meta", "books"}),
    ],
)
def test_get_enabled_servers(disabled, expected_enabled):
    """get_enabled_servers returns all servers except disabled ones."""
    with patch("memory.api.MCP.servers.settings") as mock_settings:
        mock_settings.DISABLED_MCP_SERVERS = disabled
        result = get_enabled_servers()
        assert result == expected_enabled


def test_get_enabled_servers_warns_on_unknown():
    """get_enabled_servers warns about unknown server names."""
    with patch("memory.api.MCP.servers.settings") as mock_settings:
        mock_settings.DISABLED_MCP_SERVERS = frozenset({"slack", "unknown_server"})
        with patch("memory.api.MCP.servers.logger") as mock_logger:
            result = get_enabled_servers()
            mock_logger.warning.assert_called_once()
            assert "unknown_server" in str(mock_logger.warning.call_args)
            # Should still disable valid servers
            assert "slack" not in result


# --- is_server_enabled tests ---


@pytest.mark.parametrize(
    "disabled,server,expected",
    [
        (frozenset(), MCPServer.CORE, True),
        (frozenset(), "core", True),
        (frozenset({"core"}), MCPServer.CORE, False),
        (frozenset({"core"}), "core", False),
        (frozenset({"slack"}), MCPServer.CORE, True),
        (frozenset({"slack", "forecast"}), MCPServer.SLACK, False),
        (frozenset({"slack", "forecast"}), MCPServer.DISCORD, True),
    ],
)
def test_is_server_enabled(disabled, server, expected):
    """is_server_enabled checks if server is in disabled set."""
    with patch("memory.api.MCP.servers.settings") as mock_settings:
        mock_settings.DISABLED_MCP_SERVERS = disabled
        assert is_server_enabled(server) == expected


# --- get_server_instance tests ---


@pytest.mark.parametrize("server", list(MCPServer))
def test_get_server_instance_returns_fastmcp(server):
    """get_server_instance returns a FastMCP instance for each server."""
    instance = get_server_instance(server)
    # All servers should have a name attribute (FastMCP instances do)
    assert hasattr(instance, "name")


def test_get_server_instance_raises_on_invalid():
    """get_server_instance raises ValueError for unknown servers."""
    with pytest.raises(ValueError, match="Unknown server"):
        get_server_instance("not_a_server")  # type: ignore


# --- parse_csv_set tests ---


@pytest.mark.parametrize(
    "env_value,default,expected",
    [
        (None, frozenset(), frozenset()),  # missing key returns default
        (None, frozenset({"x"}), frozenset({"x"})),  # missing key returns custom default
        ("a,b,c", frozenset(), frozenset({"a", "b", "c"})),  # comma-separated
        (" a , b , c ", frozenset(), frozenset({"a", "b", "c"})),  # strips whitespace
        ("SLACK,Forecast,CORE", frozenset(), frozenset({"slack", "forecast", "core"})),  # lowercases
        ("a,,b,,,c", frozenset(), frozenset({"a", "b", "c"})),  # ignores empty entries
        ("   ", frozenset(), frozenset()),  # whitespace-only returns default
        ("", frozenset(), frozenset()),  # empty string returns default
    ],
)
def test_parse_csv_set(env_value, default, expected):
    from memory.common.settings import parse_csv_set

    env = {"TEST_KEY": env_value} if env_value is not None else {}
    with patch.dict("os.environ", env, clear=True):
        result = parse_csv_set("TEST_KEY", default)
        assert result == expected
