import pytest
from cryptography.fernet import Fernet

from memory.api import instance_settings as settings_api
from memory.api.auth import setup_warnings
from memory.common import embedding, instance_settings, settings
from memory.common.celery_app import REINGEST_ALL_EMPTY_SOURCE_ITEMS
from memory.common.db.models import InstanceSetting, TelemetryEvent
from memory.common.db.models.secrets import encrypt_value_with_key
from memory.common.instance_settings import REGISTRY, Setting


@pytest.fixture
def ui_voyage_key(monkeypatch):
    """No key from the env, so an admin can set it; Voyage accepts every key."""
    monkeypatch.setattr(settings, "VOYAGE_API_KEY", "")
    checked = []

    def check_key(key):
        checked.append(key)
        return None

    monkeypatch.setattr(embedding, "check_key", check_key)
    return checked


def rows(response):
    return {s["key"]: s for s in response.json()["settings"]}


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("get", "/settings", None),
        ("patch", "/settings", {"values": {"enable_reranking": False}}),
        ("post", "/settings/voyage_api_key/check", {"value": "pa-x"}),
        ("post", "/settings/restart", None),
    ],
)
def test_settings_are_admin_only(regular_client, db_session, method, path, body):
    response = regular_client.request(method, path, json=body)

    assert response.status_code == 403
    assert db_session.query(InstanceSetting).count() == 0


def test_an_env_secret_is_locked_and_shows_only_its_hint(client):
    voyage = rows(client.get("/settings"))["voyage_api_key"]

    assert voyage | {"description": None} == {
        "key": "voyage_api_key",
        "kind": "secret",
        "applies": "live",
        "description": None,
        "env_var": "VOYAGE_API_KEY",
        "source": "env",
        "locked": True,
        "set": True,
        "value": None,
        "key_hint": "-key",
        "choices": [],
        "pending_restart": False,
        "error": None,
    }


def test_an_env_setting_cannot_be_changed(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_RERANKING", True)

    response = client.patch("/settings", json={"values": {"enable_reranking": False}})

    assert response.status_code == 409
    assert "ENABLE_RERANKING" in response.json()["detail"]
    assert db_session.query(InstanceSetting).count() == 0


def test_a_stale_ui_value_can_be_cleared_while_the_env_sets_the_key(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_RERANKING", True)
    db_session.add(InstanceSetting(key="enable_reranking", value=False))
    db_session.commit()

    response = client.patch("/settings", json={"values": {"enable_reranking": None}})

    assert response.status_code == 200
    assert db_session.query(InstanceSetting).count() == 0


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"no_such_setting": True},
        {"enable_reranking": "off"},
        {"disabled_mcp_servers": ["slack", "nope"]},
        # The valid change is not saved either.
        {"enable_reranking": False, "disabled_mcp_servers": "slack"},
    ],
)
def test_invalid_changes_save_nothing(client, db_session, values):
    response = client.patch("/settings", json={"values": values})

    assert response.status_code == 400
    assert db_session.query(InstanceSetting).count() == 0


def test_disabling_every_mcp_server_is_refused(client, db_session):
    every = rows(client.get("/settings"))["disabled_mcp_servers"]["choices"]

    response = client.patch("/settings", json={"values": {"disabled_mcp_servers": every}})

    assert response.status_code == 400
    assert "every MCP server" in response.json()["detail"]


def test_setting_and_clearing_a_value_is_audited(client, db_session, user):
    set_response = client.patch("/settings", json={"values": {"enable_reranking": False}})
    assert rows(set_response)["enable_reranking"] | {"description": None} == {
        "key": "enable_reranking",
        "kind": "bool",
        "applies": "live",
        "description": None,
        "env_var": "ENABLE_RERANKING",
        "source": "ui",
        "locked": False,
        "set": True,
        "value": False,
        "key_hint": None,
        "choices": [],
        "pending_restart": False,
        "error": None,
    }

    cleared = rows(client.patch("/settings", json={"values": {"enable_reranking": None}}))
    assert (cleared["enable_reranking"]["source"], cleared["enable_reranking"]["value"]) == ("default", True)

    events = db_session.query(TelemetryEvent).filter_by(name="settings.update").order_by(TelemetryEvent.id).all()
    assert [(e.user_id, e.attributes) for e in events] == [
        (user.id, {"changes": {"enable_reranking": {"source": "default", "old": True, "new": False}}}),
        (user.id, {"changes": {"enable_reranking": {"source": "ui", "old": False, "new": True}}}),
    ]


def test_a_saved_voyage_key_is_checked_returned_as_a_hint_and_backfills(
    client, db_session, ui_voyage_key, no_celery_dispatch
):
    response = client.patch("/settings", json={"values": {"voyage_api_key": " pa-new-key-9876 "}})

    assert response.status_code == 200
    assert "pa-new-key" not in response.text
    voyage = rows(response)["voyage_api_key"]
    assert (voyage["source"], voyage["set"], voyage["key_hint"]) == ("ui", True, "9876")
    assert ui_voyage_key == ["pa-new-key-9876"]
    assert embedding.voyage_api_key() == "pa-new-key-9876"
    no_celery_dispatch.assert_called_once_with(
        REINGEST_ALL_EMPTY_SOURCE_ITEMS, countdown=settings_api.CACHE_TTL_SECONDS + 5
    )
    event = db_session.query(TelemetryEvent).filter_by(name="settings.update").one()
    assert event.attributes == {
        "changes": {"voyage_api_key": {"source": "default", "changed": True, "key_hint": "9876"}}
    }


def test_a_key_voyage_rejects_is_not_saved(client, db_session, monkeypatch, no_celery_dispatch):
    monkeypatch.setattr(settings, "VOYAGE_API_KEY", "")
    monkeypatch.setattr(embedding, "check_key", lambda key: 401)

    response = client.patch("/settings", json={"values": {"voyage_api_key": "pa-bad-key"}})

    assert response.status_code == 400
    assert response.json()["detail"] == "Voyage rejected the key (HTTP 401)"
    assert db_session.query(InstanceSetting).count() == 0
    no_celery_dispatch.assert_not_called()


@pytest.mark.parametrize("status, expected", [(None, {"ok": True, "voyage_status": None}), (401, {"ok": False, "voyage_status": 401})])
def test_checking_a_key_saves_nothing(client, db_session, monkeypatch, status, expected):
    monkeypatch.setattr(embedding, "check_key", lambda key: status)

    response = client.post("/settings/voyage_api_key/check", json={"value": "pa-try"})

    assert response.json() == expected
    assert db_session.query(InstanceSetting).count() == 0


def test_admins_are_warned_until_a_voyage_key_is_set(client, ui_voyage_key):
    assert client.get("/auth/me").json()["setup_warnings"] == ["voyage_api_key"]

    client.patch("/settings", json={"values": {"voyage_api_key": "pa-key-1234"}})

    assert client.get("/auth/me").json()["setup_warnings"] == []


def test_non_admins_get_no_setup_warnings(regular_user, monkeypatch):
    monkeypatch.setattr(settings, "VOYAGE_API_KEY", "")
    assert setup_warnings(regular_user) == []


def test_a_row_that_cant_be_read_is_shown_as_unset_with_why(client, db_session, monkeypatch):
    monkeypatch.setattr(instance_settings, "_logged_unreadable", set())
    monkeypatch.setattr(settings, "VOYAGE_API_KEY", "")
    db_session.add(
        InstanceSetting(
            key="voyage_api_key",
            value_encrypted=encrypt_value_with_key("pa-secret", Fernet.generate_key()),
            key_hint="cret",
        )
    )
    db_session.commit()

    voyage = rows(client.get("/settings"))["voyage_api_key"]

    assert (voyage["set"], voyage["key_hint"], voyage["error"]) == (False, None, "can't be read; set it again")


@pytest.fixture
def restart_flag(monkeypatch):
    """A restart-bound setting this process has booted with."""
    flag = Setting(key="restart_flag", kind="bool", applies="restart", default=False, description="")
    monkeypatch.setattr(settings, "RESTART_FLAG", None, raising=False)
    monkeypatch.setitem(REGISTRY, flag.key, flag)
    monkeypatch.setattr(instance_settings, "_boot_values", {})
    monkeypatch.setattr(instance_settings, "_booted", {})
    instance_settings.booted(flag)
    return flag


def test_a_restart_bound_change_is_pending_until_the_api_restarts(client, restart_flag):
    response = client.patch("/settings", json={"values": {"restart_flag": True}})

    flag = rows(response)["restart_flag"]
    assert (flag["value"], flag["pending_restart"]) == (True, True)
    assert response.json()["restart_pending"] is True

    response = client.patch("/settings", json={"values": {"restart_flag": None}})
    assert response.json()["restart_pending"] is False


def test_disabling_an_mcp_server_needs_no_restart(client):
    response = client.patch("/settings", json={"values": {"disabled_mcp_servers": ["slack"]}})

    servers = rows(response)["disabled_mcp_servers"]
    assert (servers["value"], servers["applies"], response.json()["restart_pending"]) == (["slack"], "live", False)


def test_restart_needs_a_restart_policy_to_bring_the_api_back(client, monkeypatch):
    monkeypatch.setattr(settings, "API_SELF_RESTART", False)
    exits = []
    monkeypatch.setattr(settings_api, "exit_api", lambda: exits.append(True))

    response = client.post("/settings/restart")

    assert response.status_code == 409
    assert client.get("/settings").json()["self_restart"] is False
    assert exits == []


def test_restart_exits_after_responding_and_is_audited(client, db_session, user, monkeypatch):
    monkeypatch.setattr(settings, "API_SELF_RESTART", True)
    exits = []
    monkeypatch.setattr(settings_api, "exit_api", lambda: exits.append(True))

    response = client.post("/settings/restart")

    assert (response.status_code, exits) == (202, [True])
    assert [e.user_id for e in db_session.query(TelemetryEvent).filter_by(name="settings.restart")] == [user.id]
