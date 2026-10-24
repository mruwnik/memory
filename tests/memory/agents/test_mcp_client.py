import os
import subprocess
import sys

import pytest

from memory.agents.mcp_client import memory_tools
from memory.common.db.models import APIKey


async def listed(token: str, scopes: list[str]) -> set[str]:
    async with memory_tools(token, scopes) as tools:
        return {t.name for t in await tools.list()}


@pytest.mark.asyncio
async def test_call_runs_as_the_given_user(db_session, admin_session):
    async with memory_tools(admin_session.id, ["*"]) as tools:
        text, is_error = await tools.call("meta_get_user", {})

    assert not is_error
    assert "admin@example.com" in text


@pytest.mark.asyncio
async def test_requested_scopes_narrow_the_users_tools(db_session, user_session):
    names = await listed(user_session.id, ["teams"])

    assert "teams_list_all" in names
    assert "meta_get_user" not in names


@pytest.mark.asyncio
async def test_requested_scopes_cannot_widen_the_users_tools(db_session, user_session):
    # regular_user has scopes ["teams"]; people_tidbit_list needs "people"
    names = await listed(user_session.id, ["*", "people"])

    assert "teams_list_all" in names
    assert "people_tidbit_list" not in names


@pytest.mark.asyncio
async def test_api_key_is_limited_to_its_own_scopes(db_session, admin_user):
    key = APIKey.create(user_id=admin_user.id, name="narrow", scopes=["teams"])
    db_session.add(key)
    db_session.commit()

    names = await listed(key.key, ["*"])

    assert "teams_list_all" in names
    assert "meta_get_user" not in names


@pytest.mark.asyncio
@pytest.mark.parametrize("scopes", [[], ["people"]])
async def test_no_effective_scopes_leaves_only_unscoped_tools(
    db_session, user_session, scopes
):
    assert await listed(user_session.id, scopes) == {
        "meta_get_current_time",
        "agent_key_list",
        "agent_key_upsert",
        "agent_key_delete",
    }


@pytest.mark.asyncio
async def test_unknown_token_is_refused(db_session):
    with pytest.raises(ValueError, match="unknown or expired token"):
        await listed("not-a-session", ["*"])


@pytest.mark.asyncio
async def test_identity_is_pinned_per_client(db_session, admin_session, user_session):
    async with memory_tools(admin_session.id, ["*"]) as admin_tools:
        async with memory_tools(user_session.id, ["teams"]) as user_tools:
            user_text, _ = await user_tools.call("meta_get_user", {})
            admin_text, _ = await admin_tools.call("meta_get_user", {})

    assert "Access denied" in user_text
    assert "admin@example.com" in admin_text


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["meta_get_user", "no_such_tool"])
async def test_denied_call_is_an_error_result(db_session, user_session, name):
    async with memory_tools(user_session.id, ["teams"]) as tools:
        text, is_error = await tools.call(name, {})

    assert is_error
    assert text == f"Access denied: {name}"


def test_importing_does_not_load_the_mcp_server():
    # The agents subserver imports this module while base is mounting it.
    code = "import sys, memory.agents.mcp_client; print('memory.api.MCP.base' in sys.modules)"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    assert out.stdout.strip() == "False"
