from datetime import datetime, timezone

import pytest

from memory.common import discord_link, settings
from memory.common.db.models import (
    DiscordBot,
    DiscordUser,
    GithubAccount,
    Person,
    PersonTidbit,
    SlackApp,
    SlackUserCredentials,
    SlackWorkspace,
)

DISCORD_ID = 123456789012345678


@pytest.fixture
def own(db_session, user):
    person = Person(
        identifier="dan",
        display_name="Dan",
        user_id=user.id,
        creator_id=user.id,
        contact_info={"email": "dan/home@home.org"},
    )
    db_session.add(person)
    db_session.flush()
    # A note makes it more than the bare setup Person, so it is never merged away unasked.
    db_session.add(PersonTidbit(person_id=person.id, content="x", tidbit_type="note", creator_id=user.id, modality="text", sha256=b"t" * 32))
    db_session.add(DiscordBot(application_id=1, name="bot", created_by_user_id=user.id))
    db_session.flush()
    return person


@pytest.fixture
def daniel(db_session):
    """The Person ingest made from Dan's Slack and Discord messages."""
    person = Person(
        identifier="daniel",
        display_name="Daniel",
        contact_info={"slack": {"T1": {"user_id": "U1", "username": "daniel"}}},
    )
    db_session.add(person)
    db_session.flush()
    db_session.add(DiscordUser(id=DISCORD_ID, username="dan", person_id=person.id))
    db_session.flush()
    return person


def add_slack_credential(db, user):
    db.add(SlackWorkspace(id="T1", name="w", owner_id=user.id))
    app = SlackApp(client_id="c", name="app", created_by_user_id=user.id)
    db.add(app)
    db.flush()
    db.add(SlackUserCredentials(slack_app_id=app.id, workspace_id="T1", user_id=user.id, slack_user_id="U1"))
    db.flush()


@pytest.fixture
def slack_credential(db_session, user):
    add_slack_credential(db_session, user)


def test_identity_shows_the_suggestion_a_slack_credential_proves(client, own, daniel, slack_credential):
    response = client.get("/auth/me/identity")

    assert response.status_code == 200
    assert response.json() == {
        "person": {"id": own.id, "identifier": "dan", "display_name": "Dan"},
        "accounts": [
            {"kind": "slack", "id": "T1:U1", "label": "U1", "verified": True, "removable": False, "on_person": False},
            {"kind": "email", "id": "dan/home@home.org", "label": "dan/home@home.org", "verified": False, "removable": True, "on_person": True},
        ],
        "suggestions": [
            {
                "person": {"id": daniel.id, "identifier": "daniel", "display_name": "Daniel"},
                "reasons": [{"kind": "slack", "value": "T1:U1"}],
            }
        ],
        "notify": {
            "order": ["discord", "slack", "email"],
            "chosen": "slack",
            "problems": {
                "discord": "no Discord account is linked: use Link a Discord account in Settings → Your identity",
                "email": "no email account you can send from (add one with sending enabled; your login address alone can't send)",
            },
        },
    }


def test_claim_merges_the_proven_person_and_its_discord_account_notifies(client, own, daniel, slack_credential):
    response = client.post("/auth/me/identity/claim", json={"person_id": daniel.id})

    assert response.status_code == 200
    body = response.json()
    assert body["person"]["id"] == own.id
    assert body["suggestions"] == []
    assert {"kind": "discord", "id": str(DISCORD_ID), "label": "dan", "verified": False, "removable": True, "on_person": True} in body["accounts"]
    assert body["notify"]["chosen"] == "discord"


def self_attested_github_name(db, user, daniel, other_user):
    db.add(GithubAccount(user_id=user.id, name="daniel", auth_type="pat"))


def proven_but_another_users(db, user, daniel, other_user):
    add_slack_credential(db, user)
    daniel.user_id = other_user.id


@pytest.mark.parametrize("setup", [self_attested_github_name, proven_but_another_users])
def test_claim_is_refused_without_proof(client, db_session, user, own, daniel, admin_user, setup):
    setup(db_session, user, daniel, admin_user)
    db_session.flush()

    response = client.post("/auth/me/identity/claim", json={"person_id": daniel.id})

    assert response.status_code == 403
    assert db_session.get(Person, daniel.id) is not None
    assert db_session.get(DiscordUser, DISCORD_ID).person_id == daniel.id


def test_unlink_removes_a_discord_account_and_a_listed_email(client, db_session, own, daniel):
    db_session.get(DiscordUser, DISCORD_ID).person_id = own.id
    db_session.flush()

    assert client.delete(f"/auth/me/identity/accounts/discord/{DISCORD_ID}").status_code == 200
    body = client.delete("/auth/me/identity/accounts/email/dan%2Fhome%40home.org").json()

    assert body["accounts"] == []
    assert db_session.get(DiscordUser, DISCORD_ID).person_id is None
    assert "email" not in db_session.get(Person, own.id).contact_info


@pytest.mark.parametrize(
    "path, status",
    [
        ("slack/T1:U1", 400),  # a Slack OAuth credential
        ("email/nobody@x.org", 404),
        ("phone/1", 422),
    ],
)
def test_unlink_refuses_credentials_and_unknown_accounts(client, own, slack_credential, path, status):
    assert client.delete(f"/auth/me/identity/accounts/{path}").status_code == status


def test_discord_code_is_the_users_and_names_their_running_bots(client, user, db_session, monkeypatch):
    monkeypatch.setattr(settings, "TRANSFER_TOKEN_SECRET", "test-discord-link-secret")
    db_session.add_all([
        DiscordBot(application_id=1, name="Running", created_by_user_id=user.id, token="t"),
        DiscordBot(application_id=2, name="Paused", created_by_user_id=user.id, token="t", is_active=False),
        DiscordBot(application_id=3, name="Someone else's", token="t"),
    ])
    db_session.commit()

    body = client.post("/auth/me/identity/discord-code").json()

    assert body["bots"] == ["Running"]
    assert datetime.fromisoformat(body["expires_at"]) > datetime.now(timezone.utc)
    assert discord_link.take_code(body["code"]) == user.id


def test_unlink_undoes_a_discord_verification_on_another_person(client, db_session, user, own, daniel):
    db_session.get(DiscordUser, DISCORD_ID).verified_user_id = user.id
    db_session.commit()
    [account] = [a for a in client.get("/auth/me/identity").json()["accounts"] if a["kind"] == "discord"]
    assert (account["verified"], account["removable"], account["on_person"]) == (True, True, False)

    body = client.delete(f"/auth/me/identity/accounts/discord/{DISCORD_ID}").json()

    assert [a for a in body["accounts"] if a["kind"] == "discord"] == []
    db_session.expire_all()
    discord_user = db_session.get(DiscordUser, DISCORD_ID)
    assert (discord_user.person_id, discord_user.verified_user_id) == (daniel.id, None)
