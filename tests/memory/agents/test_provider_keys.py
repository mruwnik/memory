# pyright: reportFunctionMemberAccess=false
"""Per-user LLM provider keys: resolution, saving (checked with the provider) and the agent_key_* tools."""

import json
import logging
from datetime import datetime, timezone

import httpx
import pytest

from memory.agents import provider_keys
from memory.agents.provider_keys import KeyUnavailable, choose_key
from memory.agents.spec import AgentSpec
from memory.api.MCP.servers import agent
from memory.common.db.models import AgentDefinition, AgentRun, LLMProviderKey
from tests.conftest import mcp_auth_context

MODEL = "anthropic/claude-sonnet-5-5"
SECRET = "sk-ant-secret-value-1234"


def add_key(db, user, label="main", provider="anthropic", value=SECRET, **fields):
    key = LLMProviderKey(user_id=user.id, provider=provider, label=label, **fields)
    key.key = value
    db.add(key)
    db.commit()
    return key


def spec(**fields) -> AgentSpec:
    return AgentSpec.model_validate({"system_prompt": "x", "model": MODEL} | fields)


class ProviderAPI:
    """Answers key checks with `status` and records each request."""

    def __init__(self):
        self.status = 200
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json={"data": []})


@pytest.fixture
def provider_api(monkeypatch):
    api = ProviderAPI()
    monkeypatch.setattr(
        provider_keys,
        "http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(api.handle)),
    )
    return api


# --- resolution -------------------------------------------------------------


@pytest.mark.parametrize("pinned,expected", [("other", "other"), (None, "main")])
def test_keys_resolve_pinned_then_default(db_session, regular_user, pinned, expected):
    keys = {
        "main": add_key(db_session, regular_user, "main", is_default=True),
        "other": add_key(db_session, regular_user, "other"),
    }
    choice = {"main": keys["main"].id, "other": keys["other"].id}.get(pinned)

    chosen = choose_key(db_session, regular_user.id, MODEL, choice)

    assert chosen.id == keys[expected].id


@pytest.mark.parametrize("provider,is_default", [("anthropic", False), ("openai", True)])
def test_without_a_default_for_the_models_provider_runs_are_refused(
    db_session, regular_user, provider, is_default
):
    add_key(db_session, regular_user, provider=provider, is_default=is_default)

    with pytest.raises(KeyUnavailable, match="add an API key for anthropic"):
        choose_key(db_session, regular_user.id, MODEL, None)


@pytest.mark.parametrize(
    "setup,expected",
    [
        ("foreign", "is not yours or was deleted"),
        ("missing", "is not yours or was deleted"),
        ("wrong_provider", "is an openai key, but anthropic/claude-sonnet-5-5 needs an anthropic key"),
        ("invalid_default", "was rejected by anthropic; replace it"),
    ],
)
def test_unusable_choices_are_refused_with_the_reason(
    db_session, regular_user, admin_user, setup, expected
):
    foreign = add_key(db_session, admin_user, "theirs")
    wrong = add_key(db_session, regular_user, "gpt", provider="openai")
    add_key(db_session, regular_user, "bad", is_default=True, status="invalid")
    choice = {
        "foreign": foreign.id,
        "missing": 99999,
        "wrong_provider": wrong.id,
        "invalid_default": None,
    }[setup]

    with pytest.raises(KeyUnavailable, match=expected):
        choose_key(db_session, regular_user.id, MODEL, choice)


# --- saving -------------------------------------------------------------------


def dumped(*values) -> str:
    return json.dumps(values, default=str)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,url,header,value",
    [
        ("anthropic", "https://api.anthropic.com/v1/models", "x-api-key", SECRET),
        ("openai", "https://api.openai.com/v1/models", "authorization", f"Bearer {SECRET}"),
    ],
)
async def test_a_saved_key_is_checked_with_its_provider_and_never_returned(
    db_session, regular_user, user_session, provider_api, provider, url, header, value
):
    with mcp_auth_context(user_session.id):
        saved = await agent.key_upsert.fn(provider=provider, label="main", key=SECRET)
        listed = await agent.key_list.fn()
        catalog = await agent.catalog.fn()

    [request] = provider_api.requests
    assert (str(request.url), request.headers[header]) == (url, value)
    assert (saved["key_hint"], saved["is_default"], saved["status"]) == ("1234", True, "ok")
    assert listed["providers"][provider] == [saved]
    assert SECRET not in dumped(saved, listed, catalog)
    db_session.expire_all()
    assert db_session.get(LLMProviderKey, saved["id"]).key == SECRET


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [
        (401, "anthropic rejected this key"),
        (500, "couldn't check the key with anthropic"),
    ],
)
async def test_a_key_the_provider_does_not_accept_is_not_stored(
    db_session, regular_user, user_session, provider_api, caplog, status, expected
):
    provider_api.status = status
    caplog.set_level(logging.DEBUG)

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match=expected):
            await agent.key_upsert.fn(provider="anthropic", label="main", key=SECRET)

    assert db_session.query(LLMProviderKey).count() == 0
    assert SECRET not in caplog.text


@pytest.mark.asyncio
async def test_a_key_refused_a_permission_is_still_accepted(
    db_session, regular_user, user_session, provider_api
):
    provider_api.status = 403

    with mcp_auth_context(user_session.id):
        saved = await agent.key_upsert.fn(provider="anthropic", label="main", key=SECRET)

    assert saved["status"] == "ok"


@pytest.mark.asyncio
async def test_keys_are_not_format_checked(
    db_session, regular_user, user_session, provider_api
):
    with mcp_auth_context(user_session.id):
        saved = await agent.key_upsert.fn(provider="anthropic", label="odd", key="whatever")

    assert saved["key_hint"] == "ever"


@pytest.mark.asyncio
async def test_updates_without_a_key_do_not_recheck(
    db_session, regular_user, user_session, provider_api
):
    with mcp_auth_context(user_session.id):
        saved = await agent.key_upsert.fn(provider="anthropic", label="main", key=SECRET)
        updated = await agent.key_upsert.fn(
            key_ref=saved["id"], label="renamed", max_cost_usd_per_day="2.5"
        )
        cleared = await agent.key_upsert.fn(key_ref=saved["id"], max_cost_usd_per_day="")

    assert len(provider_api.requests) == 1
    assert (updated["label"], updated["max_cost_usd_per_day"]) == ("renamed", "2.500000")
    assert (cleared["label"], cleared["max_cost_usd_per_day"]) == ("renamed", None)


@pytest.mark.asyncio
async def test_replacing_a_key_rechecks_it_and_clears_invalid(
    db_session, regular_user, user_session, provider_api
):
    old = add_key(db_session, regular_user, status="invalid", status_detail="rejected")

    with mcp_auth_context(user_session.id):
        saved = await agent.key_upsert.fn(key_ref=old.id, key="sk-new-5678")

    assert len(provider_api.requests) == 1
    assert (saved["key_hint"], saved["status"], saved["status_detail"]) == ("5678", "ok", None)


@pytest.mark.asyncio
async def test_one_default_per_provider(db_session, regular_user, user_session, provider_api):
    with mcp_auth_context(user_session.id):
        first = await agent.key_upsert.fn(provider="anthropic", label="a", key=SECRET)
        second = await agent.key_upsert.fn(provider="anthropic", label="b", key=SECRET)
        await agent.key_upsert.fn(key_ref=second["id"], is_default=True)
        listed = await agent.key_list.fn()

    assert (first["is_default"], second["is_default"]) == (True, False)
    assert [(k["label"], k["is_default"]) for k in listed["providers"]["anthropic"]] == [
        ("a", False),
        ("b", True),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args,expected",
    [
        ({"provider": "mistral", "label": "m", "key": SECRET}, "provider must be one of"),
        ({"provider": "anthropic", "label": " ", "key": SECRET}, "label is required"),
        ({"provider": "anthropic", "label": "m"}, "key is required"),
        ({"provider": "anthropic", "label": "main", "key": SECRET}, "already have a key named 'main'"),
        ({"key_ref": 99999, "label": "x"}, "unknown API key 99999"),
        ({"provider": "anthropic", "label": "m", "key": SECRET, "max_cost_usd_per_day": "-1"}, "max_cost_usd_per_day"),
    ],
)
async def test_bad_upserts_are_refused(
    db_session, regular_user, user_session, provider_api, args, expected
):
    add_key(db_session, regular_user, "main")

    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match=expected):
            await agent.key_upsert.fn(**args)


@pytest.mark.asyncio
async def test_other_users_keys_are_unknown(
    db_session, admin_user, regular_user, user_session, provider_api
):
    theirs = add_key(db_session, admin_user)

    with mcp_auth_context(user_session.id):
        listed = await agent.key_list.fn()
        with pytest.raises(ValueError, match=f"unknown API key {theirs.id}"):
            await agent.key_delete.fn(key_ref=theirs.id)

    assert listed["providers"] == {"anthropic": [], "openai": []}


@pytest.mark.asyncio
async def test_deleting_a_pinned_key_names_the_agents_that_pinned_it(
    db_session, regular_user, user_session
):
    key = add_key(db_session, regular_user)
    db_session.add(
        AgentDefinition(
            user_id=regular_user.id,
            name="digest",
            spec=spec(provider_key=key.id).model_dump(mode="json"),
        )
    )
    db_session.commit()

    with mcp_auth_context(user_session.id):
        result = await agent.key_delete.fn(key_ref=key.id)

    assert result == {"deleted": key.id, "pinned_by": ["digest"], "new_default": None}
    assert db_session.query(LLMProviderKey).count() == 0


# --- the agent tools ------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_lists_your_keys(db_session, regular_user, user_session):
    with mcp_auth_context(user_session.id):
        catalog = await agent.catalog.fn()

    assert catalog["provider_keys"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields,expected",
    [
        ({}, "add an API key for anthropic"),
        ({"provider_key": 99999}, "is not yours or was deleted"),
    ],
)
async def test_agents_without_a_usable_key_are_refused_at_save(
    db_session, regular_user, user_session, fields, expected
):
    with mcp_auth_context(user_session.id):
        with pytest.raises(ValueError, match=expected):
            await agent.upsert.fn(name="d", spec={"system_prompt": "x", "model": MODEL} | fields)


@pytest.mark.asyncio
async def test_an_agent_on_your_own_key_records_it_on_the_run(
    db_session, regular_user, user_session
):
    key = add_key(db_session, regular_user, is_default=True)

    with mcp_auth_context(user_session.id):
        started = await agent.run.fn(input="hi", spec={"system_prompt": "x", "model": MODEL})
        listed = await agent.key_list.fn()

    stored = db_session.get(AgentRun, started["run_id"])
    assert stored.provider_key_id == key.id
    assert listed["providers"]["anthropic"][0]["id"] == key.id


def used(day: int) -> datetime:
    return datetime(2026, 10, day, tzinfo=timezone.utc)


async def delete_key(key_id):
    return await agent.key_delete.fn(key_ref=key_id)


async def undefault_key(key_id):
    return await agent.key_upsert.fn(key_ref=key_id, is_default=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drop,last_used,expected",
    [
        (delete_key, {"older": used(1), "recent": used(2)}, {"older": False, "recent": True, "newest": False}),
        (delete_key, {}, {"older": False, "recent": False, "newest": True}),
        (undefault_key, {"older": used(1), "recent": used(2)}, {"default": False, "older": False, "recent": True, "newest": False}),
    ],
)
async def test_losing_the_default_promotes_the_most_recently_used_key(
    db_session, regular_user, user_session, drop, last_used, expected
):
    default = add_key(db_session, regular_user, "default", is_default=True, last_used_at=used(3))
    for label in ("older", "recent", "newest"):
        add_key(db_session, regular_user, label, last_used_at=last_used.get(label))
    add_key(db_session, regular_user, "gpt", provider="openai", is_default=True)

    with mcp_auth_context(user_session.id):
        await drop(default.id)
        listed = await agent.key_list.fn()

    assert {k["label"]: k["is_default"] for k in listed["providers"]["anthropic"]} == expected
    assert listed["providers"]["openai"][0]["is_default"] is True


@pytest.mark.parametrize("ref", ["by_id", "by_name"])
def test_key_refs_resolve_by_id_or_name(db_session, regular_user, ref):
    key = add_key(db_session, regular_user, "personal")
    value = {"by_id": key.id, "by_name": "personal"}[ref]

    assert provider_keys.resolve_key_ref(db_session, regular_user.id, value).id == key.id


@pytest.mark.parametrize("ref", [99999, "nope"])
def test_unknown_key_refs_are_refused(db_session, regular_user, admin_user, ref):
    add_key(db_session, admin_user, "nope")

    with pytest.raises(ValueError, match="unknown API key"):
        provider_keys.resolve_key_ref(db_session, regular_user.id, ref)


@pytest.mark.asyncio
async def test_names_are_unique_across_providers(
    db_session, regular_user, user_session, provider_api
):
    add_key(db_session, regular_user, "personal", provider="anthropic")

    with mcp_auth_context(user_session.id, scopes=["teams"]):
        with pytest.raises(ValueError, match="you already have a key named 'personal'"):
            await agent.key_upsert.fn(provider="openai", label="personal", key="sk-openai")


@pytest.mark.asyncio
async def test_key_tools_need_no_agents_scope(
    db_session, regular_user, user_session, provider_api
):
    with mcp_auth_context(user_session.id, scopes=["teams"]):
        created = await agent.key_upsert.fn(provider="anthropic", label="personal", key=SECRET)
        await agent.key_upsert.fn(key_ref="personal", max_cost_usd_per_day="5")
        deleted = await agent.key_delete.fn(key_ref="personal")

    assert deleted["deleted"] == created["id"]
