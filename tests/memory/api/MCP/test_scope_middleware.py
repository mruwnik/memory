"""Tests for MCP scope middleware."""

import pytest
from unittest.mock import Mock, AsyncMock

from memory.api.MCP.scope_middleware import ScopeMiddleware


class TestScopeMiddleware:
    """Tests for ScopeMiddleware class."""

    def test_get_required_scope_extracts_scope_from_tags(self):
        """Test that _get_required_scope extracts scope from tool tags."""
        middleware = ScopeMiddleware(get_user_scopes=lambda: [])

        tool = Mock()
        tool.tags = {"scope:organizer", "other-tag"}

        scope = middleware._get_required_scope(tool)
        assert scope == "organizer"

    def test_get_required_scope_returns_none_for_no_scope_tag(self):
        """Test that _get_required_scope returns None when no scope tag exists."""
        middleware = ScopeMiddleware(get_user_scopes=lambda: [])

        tool = Mock()
        tool.tags = {"other-tag", "another-tag"}

        scope = middleware._get_required_scope(tool)
        assert scope is None

    def test_get_required_scope_handles_empty_tags(self):
        """Test that _get_required_scope handles tools with no tags."""
        middleware = ScopeMiddleware(get_user_scopes=lambda: [])

        tool = Mock()
        tool.tags = None

        scope = middleware._get_required_scope(tool)
        assert scope is None

    def test_has_scope_grants_access_for_wildcard(self):
        """Test that _has_scope returns True when user has * scope."""
        middleware = ScopeMiddleware(get_user_scopes=lambda: [])

        # Wildcard grants access to any required scope
        assert middleware._has_scope(["*"], "organizer") is True
        assert middleware._has_scope(["*"], "github") is True
        assert middleware._has_scope(["*"], "people") is True
        assert middleware._has_scope(["*", "read"], "schedule") is True

    def test_has_scope_grants_access_when_scope_matches(self):
        """Test that _has_scope returns True when user has the required scope."""
        middleware = ScopeMiddleware(get_user_scopes=lambda: [])

        assert middleware._has_scope(["organizer", "read"], "organizer") is True
        assert middleware._has_scope(["github"], "github") is True

    def test_has_scope_denies_access_when_scope_missing(self):
        """Test that _has_scope returns False when user lacks the required scope."""
        middleware = ScopeMiddleware(get_user_scopes=lambda: [])

        assert middleware._has_scope(["read"], "organizer") is False
        assert middleware._has_scope(["github", "people"], "organizer") is False
        assert middleware._has_scope([], "read") is False

    def test_has_scope_grants_access_when_no_scope_required(self):
        """Test that _has_scope returns True when tool has no scope requirement."""
        middleware = ScopeMiddleware(get_user_scopes=lambda: [])

        # No scope required (public tool)
        assert middleware._has_scope([], None) is True
        assert middleware._has_scope(["read"], None) is True

    @pytest.mark.asyncio
    async def test_on_list_tools_filters_by_scope(self):
        """Test that on_list_tools filters tools based on user scopes."""
        user_scopes = ["organizer", "read", "write"]
        middleware = ScopeMiddleware(get_user_scopes=lambda: user_scopes)

        # Create mock tools
        organizer_tool = Mock()
        organizer_tool.tags = {"scope:organizer"}

        github_tool = Mock()
        github_tool.tags = {"scope:github"}

        public_tool = Mock()
        public_tool.tags = set()

        all_tools = [organizer_tool, github_tool, public_tool]

        # Mock the call_next function
        async def call_next(ctx):
            return all_tools

        context = Mock()

        filtered = await middleware.on_list_tools(context, call_next)

        # User has organizer but not github scope
        assert organizer_tool in filtered
        assert github_tool not in filtered
        assert public_tool in filtered  # Public tools always included
        assert len(filtered) == 2

    @pytest.mark.asyncio
    async def test_on_list_tools_shows_all_for_wildcard_user(self):
        """Test that on_list_tools shows all tools for users with * scope."""
        user_scopes = ["*", "read", "write"]
        middleware = ScopeMiddleware(get_user_scopes=lambda: user_scopes)

        # Create mock tools with various scopes
        organizer_tool = Mock()
        organizer_tool.tags = {"scope:organizer"}

        github_tool = Mock()
        github_tool.tags = {"scope:github"}

        schedule_tool = Mock()
        schedule_tool.tags = {"scope:schedule"}

        public_tool = Mock()
        public_tool.tags = set()

        all_tools = [organizer_tool, github_tool, schedule_tool, public_tool]

        async def call_next(ctx):
            return all_tools

        context = Mock()

        filtered = await middleware.on_list_tools(context, call_next)

        # Wildcard user should see all tools
        assert len(filtered) == 4
        assert organizer_tool in filtered
        assert github_tool in filtered
        assert schedule_tool in filtered
        assert public_tool in filtered

    @pytest.mark.asyncio
    async def test_on_list_tools_shows_only_public_for_no_scopes(self):
        """Test that users with no scopes only see public tools."""
        user_scopes = []
        middleware = ScopeMiddleware(get_user_scopes=lambda: user_scopes)

        organizer_tool = Mock()
        organizer_tool.tags = {"scope:organizer"}

        public_tool = Mock()
        public_tool.tags = None

        all_tools = [organizer_tool, public_tool]

        async def call_next(ctx):
            return all_tools

        context = Mock()

        filtered = await middleware.on_list_tools(context, call_next)

        assert len(filtered) == 1
        assert public_tool in filtered
        assert organizer_tool not in filtered
