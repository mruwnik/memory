import pytest
from cryptography.fernet import Fernet

from memory.api import instance_settings as settings_api
from memory.api.auth import setup_warnings
from memory.common import embedding, instance_settings, settings
from memory.common.celery_app import REINGEST_ALL_EMPTY_SOURCE_ITEMS
from memory.common.db.models import AuditEvent, HumanUser, InstanceSetting
from memory.common.module_settings import RUNNING_MODULES_KEY


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


def saved(db_session):
    """The settings rows, without the api's record of the modules it runs."""
    return [r.key for r in db_session.query(InstanceSetting) if r.key != RUNNING_MODULES_KEY]


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
    assert saved(db_session) == []


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
        "module": None,
        "mcp_servers": [],
    }


def test_an_env_setting_cannot_be_changed(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_RERANKING", True)

    response = client.patch("/settings", json={"values": {"enable_reranking": False}})

    assert response.status_code == 409
    assert "ENABLE_RERANKING" in response.json()["detail"]
    assert saved(db_session) == []


def test_a_stale_ui_value_can_be_cleared_while_the_env_sets_the_key(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_RERANKING", True)
    db_session.add(InstanceSetting(key="enable_reranking", value=False))
    db_session.commit()

    response = client.patch("/settings", json={"values": {"enable_reranking": None}})

    assert response.status_code == 200
    assert saved(db_session) == []


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
    assert saved(db_session) == []


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
        "module": None,
        "mcp_servers": [],
    }

    cleared = rows(client.patch("/settings", json={"values": {"enable_reranking": None}}))
    assert (cleared["enable_reranking"]["source"], cleared["enable_reranking"]["value"]) == ("default", True)

    events = db_session.query(AuditEvent).filter_by(action="settings.update").order_by(AuditEvent.id).all()
    assert [(e.actor_id, e.attributes) for e in events] == [
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
    event = db_session.query(AuditEvent).filter_by(action="settings.update").one()
    assert event.attributes == {
        "changes": {"voyage_api_key": {"source": "default", "changed": True, "key_hint": "9876"}}
    }


def test_a_key_voyage_rejects_is_not_saved(client, db_session, monkeypatch, no_celery_dispatch):
    monkeypatch.setattr(settings, "VOYAGE_API_KEY", "")
    monkeypatch.setattr(embedding, "check_key", lambda key: 401)

    response = client.patch("/settings", json={"values": {"voyage_api_key": "pa-bad-key"}})

    assert response.status_code == 400
    assert response.json()["detail"] == "Voyage rejected the key (HTTP 401)"
    assert saved(db_session) == []
    no_celery_dispatch.assert_not_called()


@pytest.mark.parametrize("status, expected", [(None, {"ok": True, "voyage_status": None}), (401, {"ok": False, "voyage_status": 401})])
def test_checking_a_key_saves_nothing(client, db_session, monkeypatch, status, expected):
    monkeypatch.setattr(embedding, "check_key", lambda key: status)

    response = client.post("/settings/voyage_api_key/check", json={"value": "pa-try"})

    assert response.json() == expected
    assert saved(db_session) == []


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
            value_encrypted=Fernet(Fernet.generate_key()).encrypt(b"pa-secret"),
            key_hint="cret",
        )
    )
    db_session.commit()

    voyage = rows(client.get("/settings"))["voyage_api_key"]

    assert (voyage["set"], voyage["key_hint"], voyage["error"]) == (False, None, "can't be read; set it again")


def test_disabling_an_mcp_server_needs_no_restart_and_ignores_the_env(client, monkeypatch):
    monkeypatch.setenv("DISABLED_MCP_SERVERS", "books")
    response = client.patch("/settings", json={"values": {"disabled_mcp_servers": ["slack"]}})

    servers = rows(response)["disabled_mcp_servers"]
    assert (
        servers["value"], servers["applies"], servers["env_var"], servers["locked"], response.json()["restart_pending"]
    ) == (["slack"], "live", None, False, False)


@pytest.fixture
def ui_modules(monkeypatch, set_modules):
    """The env names no module, so each is the admin's to set; the api booted with none."""
    monkeypatch.setattr(settings, "ENABLE_MODULES", frozenset())
    monkeypatch.setattr(settings, "DISABLE_MODULES", frozenset())
    monkeypatch.setattr(settings, "ORCHESTRATOR_SOCKET", "/orch.sock")
    set_modules([])


def test_env_named_modules_are_locked_and_list_their_servers(client):
    teams = rows(client.get("/settings"))["module_teams"]

    assert (teams["module"], teams["mcp_servers"], teams["applies"]) == ("teams", ["projects", "teams"], "restart")
    assert (teams["source"], teams["locked"], teams["env_var"], teams["value"]) == ("env", True, "ENABLE_MODULES", True)
    assert client.patch("/settings", json={"values": {"module_teams": False}}).status_code == 409


def test_a_module_change_is_pending_until_the_api_restarts(client, ui_modules, set_modules):
    response = client.patch("/settings", json={"values": {"module_teams": True}})

    teams = rows(response)["module_teams"]
    assert (teams["source"], teams["value"], teams["pending_restart"]) == ("ui", True, True)
    assert response.json()["restart_pending"] is True

    set_modules(["teams"])
    assert client.get("/settings").json()["restart_pending"] is False


def test_the_mcp_checklist_leaves_module_servers_to_their_modules(client):
    choices = rows(client.get("/settings"))["disabled_mcp_servers"]["choices"]

    assert not {"teams", "projects", "containers"} & set(choices)
    assert client.patch("/settings", json={"values": {"disabled_mcp_servers": ["teams"]}}).status_code == 400


@pytest.mark.parametrize("modules, status", [({}, 400), ({"module_teams": True}, 200)])
def test_hiding_every_mounted_server_is_refused(client, db_session, ui_modules, modules, status):
    every = rows(client.get("/settings"))["disabled_mcp_servers"]["choices"]

    response = client.patch("/settings", json={"values": {"disabled_mcp_servers": every, **modules}})

    assert response.status_code == status


def test_turning_off_the_last_visible_servers_module_is_refused(client, db_session, ui_modules):
    every = rows(client.get("/settings"))["disabled_mcp_servers"]["choices"]
    client.patch("/settings", json={"values": {"disabled_mcp_servers": every, "module_teams": True}})

    response = client.patch("/settings", json={"values": {"module_teams": False}})

    assert response.status_code == 400
    assert "every MCP server" in response.json()["detail"]


# Clearing goes back to the default, which is off.
@pytest.mark.parametrize("value", [False, None])
def test_multi_user_stays_on_while_several_humans_exist(client, db_session, ui_modules, value):
    client.patch("/settings", json={"values": {"module_multi_user": True}})
    db_session.add(HumanUser.create_with_password(email="two@example.com", name="Two", password="pw123456"))
    db_session.commit()

    response = client.patch("/settings", json={"values": {"module_multi_user": value}})

    assert response.status_code == 400
    assert "more than one human" in response.json()["detail"]
    assert rows(client.get("/settings"))["module_multi_user"]["value"] is True


def test_claude_containers_needs_the_orchestrator_socket(client, db_session, ui_modules, monkeypatch):
    monkeypatch.setattr(settings, "ORCHESTRATOR_SOCKET", "")

    response = client.patch("/settings", json={"values": {"module_claude_containers": True}})

    assert response.status_code == 400
    assert "ORCHESTRATOR_SOCKET" in response.json()["detail"]
    assert saved(db_session) == []


def test_a_stored_module_flag_the_api_cant_run_with_is_flagged(client, db_session, ui_modules, monkeypatch):
    db_session.add(InstanceSetting(key="module_claude_containers", value=True))
    db_session.commit()
    monkeypatch.setattr(settings, "ORCHESTRATOR_SOCKET", "")

    containers = rows(client.get("/settings"))["module_claude_containers"]

    assert "ORCHESTRATOR_SOCKET" in containers["error"]
    # It falls back to off, which the api already runs, so a restart would change nothing.
    assert containers["pending_restart"] is False


def test_a_stored_mcp_list_that_hides_every_server_is_flagged(client, db_session, ui_modules):
    every = rows(client.get("/settings"))["disabled_mcp_servers"]["choices"]
    # Saved while teams was on, which the api no longer runs.
    db_session.add(InstanceSetting(key="disabled_mcp_servers", value=every))
    db_session.commit()

    servers = rows(client.get("/settings"))["disabled_mcp_servers"]

    assert servers["error"] == "can't disable every MCP server: no MCP tools are visible"


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
    assert [e.actor_id for e in db_session.query(AuditEvent).filter_by(action="settings.restart")] == [user.id]
