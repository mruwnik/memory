"""GET /webhook-events/{source}/{account_id}: the webhook log of one source account."""

import pytest

from memory.common.db.models import Event, HumanUser
from memory.common.db.models.events import record_raw_event


def raw(db, owner_id, delivery_id, *, source="github", account_id=5, payload=None):
    return record_raw_event(
        db,
        source=source,
        event_type="pull_request.opened",
        account_id=account_id,
        owner_id=owner_id,
        delivery_id=delivery_id,
        outcome="no_item_type",
        container="555",
        payload={"secret": "untrusted"} if payload is None else payload,
    )


def test_the_owner_sees_their_accounts_deliveries_newest_first_with_their_items(client, db_session, user):
    other = HumanUser(name="Other", email="other@example.com", password_hash="x")
    db_session.add(other)
    db_session.flush()
    first, second = raw(db_session, user.id, "d1"), raw(db_session, user.id, "d2")
    raw(db_session, user.id, "d3", account_id=6)
    raw(db_session, user.id, "d4", source="slack")
    raw(db_session, other.id, "d5")
    db_session.add_all(
        [
            Event(kind="item", action="added", item_id=10, caused_by=first),
            Event(kind="item", action="changed", item_id=11, caused_by=first),
        ]
    )
    db_session.commit()

    response = client.get("/webhook-events/github/5")

    assert response.status_code == 200
    assert [(e["id"], e["type"], e["outcome"], e["container"], e["item_ids"], e["payload"]) for e in response.json()] == [
        (second, "pull_request.opened", "no_item_type", "555", [], None),
        (first, "pull_request.opened", "no_item_type", "555", [10, 11], None),
    ]


@pytest.mark.parametrize("payload", [{"n": 1}, ["a", "list"], "text"])
def test_payloads_and_older_pages_are_on_request(client, db_session, user, payload):
    first = raw(db_session, user.id, "d1", payload=payload)
    second = raw(db_session, user.id, "d2")
    db_session.commit()

    response = client.get("/webhook-events/github/5", params={"payload": True, "before": second})

    assert [(e["id"], e["payload"]) for e in response.json()] == [(first, payload)]


def test_another_users_account_shows_nothing(client, db_session):
    other = HumanUser(name="Other", email="other@example.com", password_hash="x")
    db_session.add(other)
    db_session.flush()
    raw(db_session, other.id, "d1")
    db_session.commit()

    assert client.get("/webhook-events/github/5").json() == []


def test_an_unknown_source_is_rejected(client):
    assert client.get("/webhook-events/email/5").status_code == 422
