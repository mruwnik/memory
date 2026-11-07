"""The /mcp endpoint's scope gate, end to end over HTTP with real API keys."""

import json

import pytest

from memory.common.db.models import APIKey, APIKeyType, HumanUser

WORK_TOOLS = {
    *("agent_work_claim", "agent_work_submit", "agent_work_queues"),
    *("subscription_upsert", "subscription_list_all", "subscription_fetch", "subscription_delete"),
    "subscription_test",
}


def make_key(db, user_scopes: list[str], key_scopes: list[str]) -> str:
    user = HumanUser(
        name="Key Owner", email="owner@example.com", password_hash="x", scopes=user_scopes
    )
    db.add(user)
    db.commit()
    key = APIKey.create(
        user_id=user.id, key_type=APIKeyType.INTERNAL, name="k", scopes=key_scopes
    )
    db.add(key)
    db.commit()
    return key.key


def rpc(client, key: str, method: str, params: dict | None = None):
    return client.post(
        "/mcp/",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


def result(response) -> dict:
    data_lines = [
        line.removeprefix("data:").strip()
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    body = json.loads(data_lines[-1]) if data_lines else response.json()
    return body["result"]


def call(client, key: str, tool: str, arguments: dict) -> tuple[str, bool]:
    res = result(rpc(client, key, "tools/call", {"name": tool, "arguments": arguments}))
    return res["content"][0]["text"], res.get("isError", False)


@pytest.fixture
def mcp_client(app_client):
    client, _ = app_client
    return client


@pytest.mark.parametrize(
    "user_scopes, key_scopes, status",
    [
        (["*"], ["agents:work"], 200),
        (["*"], ["read"], 200),
        (["*"], ["*"], 200),
        (["*"], ["github"], 403),
        (["*"], ["agents"], 403),
        # The key's scopes capped by its user's leave nothing.
        (["organizer"], ["github"], 403),
        (["organizer"], ["agents:work"], 403),
    ],
)
def test_the_gate_admits_only_keys_with_an_mcp_entry_scope(
    db_session, mcp_client, user_scopes, key_scopes, status
):
    key = make_key(db_session, user_scopes, key_scopes)

    assert rpc(mcp_client, key, "tools/list").status_code == status


def test_a_work_key_lists_only_the_work_tools(db_session, mcp_client):
    key = make_key(db_session, ["*"], ["agents:work"])

    tools = {tool["name"] for tool in result(rpc(mcp_client, key, "tools/list"))["tools"]}

    assert tools == WORK_TOOLS


@pytest.mark.parametrize(
    "tool, arguments",
    [
        ("core_search", {"query": "x"}),
        ("core_fetch", {"id": 1}),
        ("meta_get_user", {}),
        ("meta_get_current_time", {}),
        ("agent_run", {"input": "x"}),
        ("agent_list_all", {}),
    ],
)
def test_a_work_key_is_refused_every_other_tool(db_session, mcp_client, tool, arguments):
    key = make_key(db_session, ["*"], ["agents:work"])

    text, is_error = call(mcp_client, key, tool, arguments)

    assert is_error
    assert text == f"Access denied: {tool}"


def test_a_work_key_claims_work(db_session, mcp_client):
    key = make_key(db_session, ["*"], ["agents:work"])

    text, is_error = call(mcp_client, key, "agent_work_claim", {})

    assert not is_error, text
    assert json.loads(text) == {"run": None}


def test_a_work_key_reaches_submit(db_session, mcp_client):
    key = make_key(db_session, ["*"], ["agents:work"])

    text, is_error = call(
        mcp_client, key, "agent_work_submit", {"run_id": "gone", "lease_token": "t", "result": "r"}
    )

    assert is_error
    assert "Access denied" not in text
