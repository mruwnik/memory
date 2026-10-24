from memory.common import settings
from memory.common.modules import MODULES


def test_modules_lists_every_module_with_its_state(client, monkeypatch):
    monkeypatch.setattr(settings, "ENABLED_MODULES", frozenset({"teams"}))

    response = client.get("/modules")

    assert response.status_code == 200
    body = response.json()
    assert [m["name"] for m in body] == list(MODULES)
    assert all(m["description"] for m in body)
    assert {m["name"]: m["enabled"] for m in body} == {
        name: name == "teams" for name in MODULES
    }


def test_modules_requires_admin(regular_client):
    assert regular_client.get("/modules").status_code == 403
