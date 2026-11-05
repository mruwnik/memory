import logging

import pytest

from memory.beeper import state
from memory.common.beeper import BeeperClient
from memory.common.celery_app import SYNC_BEEPER_ACCOUNT, SYNC_BEEPER_CHAT
from memory.common.db.models import BeeperAccount, BeeperChat, BeeperNetwork, HumanUser
from memory.common.db.models.beeper import BACKFILL_MAX_STALLS
from memory.common.db.models.sources import Project
from tests.memory.fake_beeper import URL, FakeBeeperServer

EMAIL = "me@example.com"


def add_account(client, **body) -> dict:
    response = client.post("/beeper/accounts", json={"server_url": URL, **body})
    assert response.status_code == 201, response.text
    return response.json()


def sign_in(client, fake: FakeBeeperServer) -> int:
    account_id = add_account(client)["id"]
    assert client.post(f"/beeper/accounts/{account_id}/email", json={"email": EMAIL}).status_code == 200
    response = client.post(f"/beeper/accounts/{account_id}/code", json={"code": fake.code})
    assert response.status_code == 200, response.text
    return account_id


def secrets_in(text: str, fake: FakeBeeperServer) -> list[str]:
    return [s for s in (fake.token, fake.code, fake.recovery_key, "lead-secret") if s in text]


def test_sign_in_with_emoji_verification(client, db_session, fake_beeper, caplog):
    caplog.set_level(logging.DEBUG)
    bodies = []

    account = add_account(client, name="Mine")
    assert account["step"] == "email"
    account_id = account["id"]

    steps = [
        ("post", "email", {"email": EMAIL}),
        ("post", "code", {"code": fake_beeper.code}),
        ("post", "verifications", None),
        ("post", "verifications/verification-1/sas-start", None),
        ("post", "verifications/verification-1/sas-confirm", None),
    ]
    for method, path, body in steps:
        response = getattr(client, method)(f"/beeper/accounts/{account_id}/{path}", json=body)
        assert response.status_code == 200, (path, response.text)
        bodies.append(response.json())

    assert [b["account"]["step"] for b in bodies] == ["code", "verify", "verify", "verify", "done"]
    assert bodies[3]["verification"]["sas_emojis"] == "🐶 🐱 🦁 🐎 🦄 🐷 🐘"
    assert bodies[-1]["account"]["verified"] is True
    assert bodies[-1]["account"]["beeper_user_id"] == fake_beeper.user_id
    stored = db_session.get(BeeperAccount, account_id)
    assert stored.token == fake_beeper.token
    assert secrets_in(str(bodies) + client.get(f"/beeper/accounts/{account_id}").text, fake_beeper) == []
    assert secrets_in(caplog.text, fake_beeper) == []
    assert EMAIL not in caplog.text


def test_sign_in_with_recovery_key(client, fake_beeper):
    account_id = sign_in(client, fake_beeper)

    wrong = client.post(f"/beeper/accounts/{account_id}/recovery-key", json={"recovery_key": "nope"})
    right = client.post(
        f"/beeper/accounts/{account_id}/recovery-key", json={"recovery_key": fake_beeper.recovery_key}
    )

    assert wrong.status_code == 400
    assert right.status_code == 200
    assert (right.json()["account"]["step"], right.json()["account"]["setup_state"]) == ("done", "ready")


def test_an_email_without_a_beeper_account_is_refused_and_never_registered(client, db_session, fake_beeper):
    fake_beeper.unregistered_emails.add(EMAIL)
    account_id = add_account(client)["id"]
    client.post(f"/beeper/accounts/{account_id}/email", json={"email": EMAIL})

    response = client.post(f"/beeper/accounts/{account_id}/code", json={"code": fake_beeper.code})

    assert response.status_code == 400
    assert "no Beeper account" in response.json()["detail"]
    assert "POST /v1/app/setup/register" not in fake_beeper.paths
    assert "lead-secret" not in response.text
    account = db_session.get(BeeperAccount, account_id)
    assert (account.signed_in, account.setup_request_id) == (False, None)


def test_get_setup_shows_the_verification_in_progress(client, fake_beeper):
    account_id = sign_in(client, fake_beeper)
    client.post(f"/beeper/accounts/{account_id}/verifications")

    response = client.get(f"/beeper/accounts/{account_id}/setup")

    assert response.json()["verification"]["state"] == "requested"
    assert response.json()["account"]["setup_state"] == "needs-verification"


@pytest.mark.parametrize(
    "server_url, prepare, status",
    [
        ("http://elsewhere:23373", lambda fake: None, 400),
        ("file:///etc/passwd", lambda fake: None, 400),
        (URL, lambda fake: setattr(fake, "signed_in", True), 409),
    ],
)
def test_adding_an_account_needs_an_allowed_fresh_server(client, db_session, fake_beeper, server_url, prepare, status):
    prepare(fake_beeper)

    response = client.post("/beeper/accounts", json={"server_url": server_url})

    assert response.status_code == status
    assert db_session.query(BeeperAccount).count() == 0


def test_a_server_holds_one_account(client, fake_beeper):
    add_account(client)

    assert client.post("/beeper/accounts", json={"server_url": URL + "/"}).status_code == 409


def test_a_concurrent_add_on_the_same_server_is_a_conflict(client, db_session, fake_beeper, admin_user, monkeypatch):
    setup_state = BeeperClient.setup_state

    async def racing_setup_state(self):
        db_session.add(BeeperAccount(created_by_user_id=admin_user.id, server_url=URL))
        db_session.commit()
        return await setup_state(self)

    monkeypatch.setattr(BeeperClient, "setup_state", racing_setup_state)

    response = client.post("/beeper/accounts", json={"server_url": URL})

    assert response.status_code == 409
    assert db_session.query(BeeperAccount).count() == 1


def test_a_server_serving_another_user_is_refused(client, db_session, fake_beeper):
    account_id = sign_in(client, fake_beeper)
    fake_beeper.user_id = "@someone-else:beeper.com"

    response = client.post(f"/beeper/accounts/{account_id}/refresh")

    assert response.status_code == 409
    assert db_session.query(BeeperNetwork).count() == 0


def test_sign_out_ends_the_matrix_session_and_forgets_the_token(client, db_session, fake_beeper):
    account_id = sign_in(client, fake_beeper)

    response = client.post(f"/beeper/accounts/{account_id}/sign-out")

    assert response.status_code == 200
    assert "POST /_matrix/client/v3/logout" in fake_beeper.paths
    assert not fake_beeper.signed_in
    assert "wipe its data directory" in response.json()["note"]
    assert response.json()["account"]["step"] == "email"
    assert db_session.get(BeeperAccount, account_id).token is None


def test_delete_signs_out_and_removes_the_account(client, db_session, fake_beeper):
    account_id = sign_in(client, fake_beeper)

    response = client.delete(f"/beeper/accounts/{account_id}")

    assert response.json()["signed_out"] is True
    assert not fake_beeper.signed_in
    assert db_session.get(BeeperAccount, account_id) is None


def test_refresh_records_networks_with_the_collect_default(client, db_session, fake_beeper):
    account_id = add_account(client, collect_by_default=False)["id"]
    client.post(f"/beeper/accounts/{account_id}/email", json={"email": EMAIL})
    client.post(f"/beeper/accounts/{account_id}/code", json={"code": fake_beeper.code})

    networks = client.post(f"/beeper/accounts/{account_id}/refresh").json()
    fake_beeper.accounts = []
    again = client.post(f"/beeper/accounts/{account_id}/refresh").json()

    assert [(n["network"], n["collect_messages"], n["status"]) for n in networks] == [
        ("Beeper", False, "connected"), ("Slack", False, "connected"),
    ]
    assert [n["status"] for n in again] == ["connected", "removed"]
    assert client.get(f"/beeper/accounts/{account_id}/networks").json() == again


def test_network_and_chat_settings_save(client, db_session, fake_beeper):
    account_id = sign_in(client, fake_beeper)
    network_id = client.post(f"/beeper/accounts/{account_id}/refresh").json()[1]["id"]
    project = Project(title="P", state="open")
    chat = BeeperChat(
        network_id=network_id, chat_id="!c:beeper.local", collect_messages=False, backfill_stalls=BACKFILL_MAX_STALLS
    )
    db_session.add_all([project, chat])
    db_session.commit()

    network = client.patch(
        f"/beeper/networks/{network_id}",
        json={"collect_messages": False, "project_id": project.id, "sensitivity": "internal"},
    ).json()
    overridden = client.patch(f"/beeper/chats/{chat.id}", json={"sensitivity": "confidential"}).json()
    chats = client.patch(f"/beeper/chats/{chat.id}", json={"collect_messages": None, "sensitivity": None}).json()

    assert (network["collect_messages"], network["project_id"], network["sensitivity"]) == (False, project.id, "internal")
    assert overridden["sensitivity"] == "confidential"
    assert (chats["collect_messages"], chats["effective_collect"], chats["sensitivity"]) == (None, False, None)
    assert (chats["backfill_done"], chats["backfill_stopped"]) == (False, True)
    assert client.get(f"/beeper/networks/{network_id}/chats").json() == [chats]


def test_an_account_shows_whether_its_collector_is_connected(client, fake_beeper):
    account_id = add_account(client)["id"]
    before = client.get(f"/beeper/accounts/{account_id}").json()["connected"]

    state.set_connected(account_id, True)

    assert (before, client.get(f"/beeper/accounts/{account_id}").json()["connected"]) == (False, True)


def test_another_server_version_is_flagged(client, fake_beeper):
    fake_beeper.version = "9.9.9"

    account = add_account(client)

    assert (account["server_version"], account["version_matches"]) == ("9.9.9", False)


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("get", "/beeper/accounts/{a}/setup", None),
        ("patch", "/beeper/accounts/{a}", {"name": "x"}),
        ("post", "/beeper/accounts/{a}/email", {"email": EMAIL}),
        ("post", "/beeper/accounts/{a}/code", {"code": "1"}),
        ("post", "/beeper/accounts/{a}/verifications", None),
        ("post", "/beeper/accounts/{a}/verifications/v/sas-confirm", None),
        ("post", "/beeper/accounts/{a}/recovery-key", {"recovery_key": "k"}),
        ("post", "/beeper/accounts/{a}/refresh", None),
        ("post", "/beeper/accounts/{a}/sign-out", None),
        ("delete", "/beeper/accounts/{a}", None),
        ("patch", "/beeper/networks/{n}", {"collect_messages": True}),
        ("patch", "/beeper/chats/{c}", {"collect_messages": True}),
    ],
)
def test_only_the_owner_can_change_an_account(client, db_session, fake_beeper, method, path, body):
    """The client's user is an admin, and is still refused."""
    owner = HumanUser(email="owner@example.com", name="Owner", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    account = BeeperAccount(created_by_user_id=owner.id, server_url=URL)
    account.token = fake_beeper.token
    network = BeeperNetwork(account=account, network_account_id="slackgo.T1-U1", network="Slack", collect_messages=True)
    chat = BeeperChat(network=network, chat_id="!c:beeper.local")
    db_session.add_all([account, network, chat])
    db_session.commit()
    url = path.format(a=account.id, n=network.id, c=chat.id)

    response = getattr(client, method)(url, **({"json": body} if body is not None else {}))

    assert response.status_code == 404
    assert fake_beeper.paths == []


def test_members_see_only_their_own_accounts(member_client, db_session, user, fake_beeper):
    owner = HumanUser(email="owner@example.com", name="Owner", password_hash="x")
    db_session.add(owner)
    db_session.flush()
    db_session.add_all([
        BeeperAccount(created_by_user_id=owner.id, server_url="http://other:23373"),
        BeeperAccount(created_by_user_id=user.id, server_url=URL),
    ])
    db_session.commit()

    listed = member_client.get("/beeper/accounts", params={"user_id": owner.id}).json()

    assert [a["server_url"] for a in listed] == [URL]
    assert member_client.get("/beeper/servers").json() == [{"url": URL, "in_use": True}]


def test_turning_collection_on_starts_reading(client, db_session, fake_beeper, no_celery_dispatch):
    account_id = sign_in(client, fake_beeper)
    network_id = client.post(f"/beeper/accounts/{account_id}/refresh").json()[1]["id"]
    chat = BeeperChat(network_id=network_id, chat_id="!c:beeper.local", collect_messages=False)
    db_session.add(chat)
    db_session.commit()

    client.patch(f"/beeper/networks/{network_id}", json={"collect_messages": False})
    client.patch(f"/beeper/networks/{network_id}", json={"collect_messages": True})
    client.patch(f"/beeper/chats/{chat.id}", json={"collect_messages": True})
    client.patch(f"/beeper/chats/{chat.id}", json={"collect_messages": True})

    sent = [(c.args[0], c.kwargs["args"]) for c in no_celery_dispatch.call_args_list if "beeper" in c.args[0]]
    assert sent == [(SYNC_BEEPER_ACCOUNT, [account_id]), (SYNC_BEEPER_CHAT, [chat.id])]
