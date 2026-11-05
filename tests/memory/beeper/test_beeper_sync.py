from datetime import datetime, timezone

import pytest

from memory.common import settings
from memory.common.celery_app import SYNC_BEEPER_ACCOUNT, SYNC_BEEPER_CHAT
from memory.common.db.models import BeeperAccount, BeeperChat, BeeperNetwork, BeeperParticipant
from memory.common.db.models.source_items import BeeperMessage
from memory.common.db.models.sources import Project
from memory.workers.tasks import beeper as tasks
from tests.memory.fake_beeper import FakeBeeperServer

CHAT = "!general:beeper.local"
ALICE = "@alice:beeper.local"


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    monkeypatch.setattr(tasks.sync, "EMPTY_PAGE_WAIT_SECONDS", 0)


def dispatched(mock, name: str) -> list:
    return [call.kwargs.get("args", [None])[0] for call in mock.call_args_list if call.args[0] == name]


def stored(db_session) -> dict[str, BeeperMessage]:
    db_session.expire_all()
    return {m.message_id: m for m in db_session.query(BeeperMessage)}


def collected_chat(db_session, fake: FakeBeeperServer, account: BeeperAccount, chat_id: str = CHAT) -> BeeperChat:
    if not any(c["id"] == chat_id for c in fake.chats):
        fake.add_chat(chat_id)
    tasks.sync_beeper_account(account.id)
    return db_session.query(BeeperChat).filter_by(chat_id=chat_id).one()


def test_syncing_an_account_records_networks_chats_and_participants(
    db_session, fake_beeper, beeper_account, no_celery_dispatch
):
    people = [{"id": ALICE, "fullName": "Alice A", "username": "alice"}, {"id": fake_beeper.user_id, "isSelf": True}]
    fake_beeper.add_chat(CHAT, participants={"items": people, "hasMore": False, "total": 2})
    fake_beeper.add_chat("!merged:beeper.local", merge={"chatIDs": ["!member:beeper.local"]})
    fake_beeper.add_chat("!member:beeper.local", mergedIntoChatID="!merged:beeper.local", type="single")

    result = tasks.sync_beeper_account(beeper_account.id)

    chats = {c.chat_id: c for c in db_session.query(BeeperChat)}
    assert set(chats) == {CHAT, "!merged:beeper.local", "!member:beeper.local"}
    assert [c.should_collect for c in chats.values()] == [True, False, True]
    assert chats["!member:beeper.local"].merged_into == "!merged:beeper.local"
    assert chats[CHAT].collect_messages is None
    assert {(p.participant_id, p.name, p.is_self) for p in db_session.query(BeeperParticipant)} == {
        (ALICE, "Alice A", False),
        (fake_beeper.user_id, fake_beeper.user_id, True),
    }
    assert {n.network_account_id for n in db_session.query(BeeperNetwork)} == {"matrix", "slackgo.T1-U1"}
    assert sorted(dispatched(no_celery_dispatch, SYNC_BEEPER_CHAT)) == sorted(
        [chats[CHAT].id, chats["!member:beeper.local"].id]
    )
    assert result["chats"] == 3
    db_session.refresh(beeper_account)
    assert beeper_account.last_reconcile_at is not None


def test_new_chats_follow_their_networks_default(db_session, fake_beeper, beeper_account, no_celery_dispatch):
    tasks.sync_beeper_account(beeper_account.id)
    db_session.query(BeeperNetwork).filter_by(network_account_id="slackgo.T1-U1").one().collect_messages = False
    db_session.commit()
    fake_beeper.add_chat(CHAT)
    no_celery_dispatch.reset_mock()

    tasks.sync_beeper_account(beeper_account.id)

    chat = db_session.query(BeeperChat).filter_by(chat_id=CHAT).one()
    assert (chat.collect_messages, chat.should_collect) == (None, False)
    assert dispatched(no_celery_dispatch, SYNC_BEEPER_CHAT) == []


@pytest.mark.parametrize("verified, active", [(False, True), (True, False)])
def test_unverified_or_paused_accounts_are_not_synced(db_session, fake_beeper, beeper_account, verified, active):
    beeper_account.verified, beeper_account.is_active = verified, active
    db_session.commit()
    fake_beeper.add_chat(CHAT)

    assert tasks.sync_beeper_account(beeper_account.id)["status"] == "skipped"
    assert db_session.query(BeeperChat).count() == 0


def test_a_chat_is_backfilled_to_the_start_of_its_history(db_session, fake_beeper, beeper_account, qdrant):
    fake_beeper.add_chat(CHAT)
    for i in range(5):
        fake_beeper.add_message(CHAT, f"m{i}", f"message number {i} with enough text to embed")
    chat = collected_chat(db_session, fake_beeper, beeper_account)

    first = tasks.sync_beeper_chat(chat.id)
    again = tasks.sync_beeper_chat(chat.id)

    assert (first["done"], first["processed"]) == (True, 5)
    assert again["done"] is True
    messages = stored(db_session)
    assert sorted(messages) == [f"m{i}" for i in range(5)]
    assert {m.creator_id for m in messages.values()} == {beeper_account.created_by_user_id}
    db_session.refresh(chat)
    assert chat.backfill_done is True


def test_backfill_keeps_paging_through_empty_pages(db_session, fake_beeper, beeper_account, qdrant):
    fake_beeper.add_chat(CHAT)
    for i in range(4):
        fake_beeper.add_message(CHAT, f"m{i}", f"text {i}")
    fake_beeper.empty_pages[CHAT] = 3
    chat = collected_chat(db_session, fake_beeper, beeper_account)

    result = tasks.sync_beeper_chat(chat.id)

    assert result["done"] is True
    assert sorted(stored(db_session)) == ["m0", "m1", "m2", "m3"]


def test_backfill_stuck_on_empty_pages_resumes_later_without_finishing(
    db_session, fake_beeper, beeper_account, no_celery_dispatch, qdrant
):
    fake_beeper.add_chat(CHAT)
    for i in range(4):
        fake_beeper.add_message(CHAT, f"m{i}", f"text {i}")
    fake_beeper.empty_pages[CHAT] = 1000
    chat = collected_chat(db_session, fake_beeper, beeper_account)
    no_celery_dispatch.reset_mock()

    result = tasks.sync_beeper_chat(chat.id)

    db_session.refresh(chat)
    assert (result["done"], chat.backfill_done) == (False, False)
    assert dispatched(no_celery_dispatch, SYNC_BEEPER_CHAT) == [chat.id]
    fake_beeper.empty_pages[CHAT] = 0
    assert tasks.sync_beeper_chat(chat.id)["done"] is True
    assert sorted(stored(db_session)) == ["m0", "m1", "m2", "m3"]


@pytest.mark.parametrize("budget", [1, 2, 3])
def test_a_budget_cut_resumes_where_it_stopped(
    db_session, fake_beeper, beeper_account, no_celery_dispatch, qdrant, monkeypatch, budget
):
    monkeypatch.setattr(settings, "BEEPER_SYNC_MAX_MESSAGES_PER_RUN", budget)
    fake_beeper.add_chat(CHAT)
    for i in range(7):
        fake_beeper.add_message(CHAT, f"m{i}", f"text {i}")
    chat = collected_chat(db_session, fake_beeper, beeper_account)
    no_celery_dispatch.reset_mock()

    runs = [tasks.sync_beeper_chat(chat.id)]
    while not runs[-1]["done"]:
        runs.append(tasks.sync_beeper_chat(chat.id))

    assert len(runs) > 1
    assert len(dispatched(no_celery_dispatch, SYNC_BEEPER_CHAT)) == len(runs) - 1
    assert sorted(stored(db_session)) == [f"m{i}" for i in range(7)]


def test_reconcile_fills_a_gap(db_session, fake_beeper, beeper_account, qdrant):
    fake_beeper.add_chat(CHAT)
    fake_beeper.add_message(CHAT, "m0", "before the gap")
    chat = collected_chat(db_session, fake_beeper, beeper_account)
    tasks.sync_beeper_chat(chat.id)
    for i in range(1, 6):
        fake_beeper.add_message(CHAT, f"m{i}", f"missed while disconnected {i}")

    result = tasks.sync_beeper_chat(chat.id)

    assert result["done"] is True
    assert sorted(stored(db_session)) == [f"m{i}" for i in range(6)]


def test_an_empty_newer_page_means_caught_up(db_session, fake_beeper, beeper_account, qdrant):
    fake_beeper.add_chat(CHAT)
    fake_beeper.add_message(CHAT, "m0", "the only message")
    chat = collected_chat(db_session, fake_beeper, beeper_account)
    tasks.sync_beeper_chat(chat.id)
    fake_beeper.stuck_after.add(CHAT)
    fake_beeper.paths.clear()

    result = tasks.sync_beeper_chat(chat.id)

    assert result["done"] is True
    assert len([p for p in fake_beeper.paths if p.endswith("/messages")]) == 1


def test_uncollected_chats_are_not_read(db_session, fake_beeper, beeper_account):
    fake_beeper.add_chat(CHAT)
    fake_beeper.add_message(CHAT, "m0", "secret")
    chat = collected_chat(db_session, fake_beeper, beeper_account)
    chat.collect_messages = False
    db_session.commit()
    fake_beeper.paths.clear()

    assert tasks.sync_beeper_chat(chat.id)["status"] == "skipped"
    assert not any("/messages" in path for path in fake_beeper.paths)
    assert stored(db_session) == {}


def test_a_message_keeps_its_reply_mentions_reactions_and_sender(db_session, fake_beeper, beeper_account, qdrant):
    fake_beeper.add_chat(CHAT)
    fake_beeper.add_message(CHAT, "m0", "the original message")
    fake_beeper.add_message(
        CHAT, "m1", "a reply to the original message",
        linkedMessageID="m0", mentions=[ALICE],
        reactions=[
            {"id": "@bob:beeper.local", "reactionKey": "👍", "participantID": "@bob:beeper.local"},
            {"id": ALICE, "reactionKey": "👍", "participantID": ALICE},
            {"id": "@carol:beeper.local", "reactionKey": "🎉", "participantID": "@carol:beeper.local"},
        ],
    )
    fake_beeper.add_message(CHAT, "r1", type="REACTION", isHidden=True, linkedMessageID="m1")
    chat = collected_chat(db_session, fake_beeper, beeper_account)

    tasks.sync_beeper_chat(chat.id)

    messages = stored(db_session)
    assert sorted(messages) == ["m0", "m1"]
    reply = messages["m1"]
    assert reply.reply_to_message_id == "m0"
    assert reply.mentions == [ALICE]
    assert reply.reactions == {"👍": [ALICE, "@bob:beeper.local"], "🎉": ["@carol:beeper.local"]}
    assert (reply.sender_id, reply.sender_name) == (ALICE, "Alice")
    assert db_session.query(BeeperParticipant).filter_by(participant_id=ALICE).one().name == "Alice"


def test_attachments_are_fetched_with_their_mime_type(db_session, fake_beeper, beeper_account, qdrant):
    image, voice = "mxc://local.beeper.com/image", "mxc://local.beeper.com/voice"
    fake_beeper.assets[image] = b"\x89PNG fake image bytes"
    fake_beeper.add_chat(CHAT)
    fake_beeper.add_message(
        CHAT, "m0", "look at this",
        type="IMAGE",
        attachments=[
            {"id": image, "srcURL": image, "type": "img", "mimeType": "image/png", "fileName": "noext"},
            {"id": "file:///data/.beeper/secret", "srcURL": "file:///data/.beeper/secret", "type": "img",
             "mimeType": "image/png"},
        ],
    )
    fake_beeper.add_message(
        CHAT, "m1",
        type="VOICE",
        attachments=[{
            "id": voice, "srcURL": voice, "type": "audio", "mimeType": "audio/ogg", "isVoiceNote": True,
            "transcription": {"transcription": "call me back about the invoice tomorrow"},
        }],
    )
    chat = collected_chat(db_session, fake_beeper, beeper_account)

    tasks.sync_beeper_chat(chat.id)

    messages = stored(db_session)
    [path] = messages["m0"].images or []
    assert path.endswith(".png")
    assert (settings.FILE_STORAGE_DIR / path).read_bytes() == b"\x89PNG fake image bytes"
    assert not any("secret" in p for p in fake_beeper.paths)
    assert "call me back about the invoice tomorrow" in (messages["m1"].content or "")
    assert [a["mime_type"] for a in messages["m1"].attachments or []] == ["audio/ogg"]


@pytest.mark.parametrize(
    "chat_project, network_project, expected",
    [("chat", "network", "chat"), (None, "network", "network"), (None, None, None)],
)
def test_the_project_comes_from_the_chat_then_its_network(
    db_session, fake_beeper, beeper_account, qdrant, chat_project, network_project, expected
):
    projects = {name: Project(title=name, state="open") for name in ("chat", "network")}
    db_session.add_all(projects.values())
    db_session.commit()
    fake_beeper.add_chat(CHAT)
    fake_beeper.add_message(CHAT, "m0", "hello there")
    chat = collected_chat(db_session, fake_beeper, beeper_account)
    chat.project_id = projects[chat_project].id if chat_project else None
    chat.network.project_id = projects[network_project].id if network_project else None
    chat.sensitivity = "confidential"
    db_session.commit()

    tasks.sync_beeper_chat(chat.id)

    message = stored(db_session)["m0"]
    assert message.project_id == (projects[expected].id if expected else None)
    assert message.sensitivity == "confidential"


# --- Live events ---


def event(kind: str, ids: list[str], entries: list[dict] | None = None, chat_id: str = CHAT) -> dict:
    body = {"type": kind, "seq": 1, "ts": "2026-11-05T12:00:00.000Z", "chatID": chat_id, "ids": ids}
    if entries is not None:
        body["entries"] = entries
    return body


def test_upserted_messages_are_stored_and_edited_in_place(db_session, fake_beeper, beeper_account, qdrant):
    chat = collected_chat(db_session, fake_beeper, beeper_account)
    message = fake_beeper.add_message(CHAT, "m0", "first version of the text")
    tasks.handle_beeper_event(beeper_account.id, event("message.upserted", ["m0"], [message]))
    item_id = stored(db_session)["m0"].id

    message.update(text="second version of the text", editedTimestamp="2026-11-05T12:00:00.000Z")
    for _ in range(2):
        tasks.handle_beeper_event(beeper_account.id, event("message.upserted", ["m0"], [message]))

    [edited] = stored(db_session).values()
    assert (edited.id, edited.content, edited.chat_id) == (item_id, "second version of the text", chat.id)
    assert edited.edited_at == datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


def test_an_upsert_without_entries_fetches_the_message(db_session, fake_beeper, beeper_account, qdrant):
    collected_chat(db_session, fake_beeper, beeper_account)
    fake_beeper.add_message(CHAT, "m0", "fetched by id")

    tasks.handle_beeper_event(beeper_account.id, event("message.upserted", ["m0"]))

    assert stored(db_session)["m0"].content == "fetched by id"


def test_a_deleted_message_is_removed(db_session, fake_beeper, beeper_account, qdrant):
    collected_chat(db_session, fake_beeper, beeper_account)
    message = fake_beeper.add_message(CHAT, "m0", "soon gone")
    tasks.handle_beeper_event(beeper_account.id, event("message.upserted", ["m0"], [message]))

    message["isDeleted"] = True
    for _ in range(2):
        tasks.handle_beeper_event(beeper_account.id, event("message.upserted", ["m0"], [message]))

    assert stored(db_session) == {}


@pytest.mark.parametrize("deleted, kept", [(False, True), (True, False)])
def test_message_deleted_events_act_only_on_a_refetched_delete(
    db_session, fake_beeper, beeper_account, qdrant, deleted, kept
):
    collected_chat(db_session, fake_beeper, beeper_account)
    message = fake_beeper.add_message(CHAT, "m0", "a message someone un-reacted to")
    tasks.handle_beeper_event(beeper_account.id, event("message.upserted", ["m0"], [message]))
    message["isDeleted"] = deleted

    tasks.handle_beeper_event(beeper_account.id, event("message.deleted", ["m0"]))

    assert ("m0" in stored(db_session)) is kept


def test_a_reaction_refreshes_its_parent(db_session, fake_beeper, beeper_account, qdrant):
    collected_chat(db_session, fake_beeper, beeper_account)
    parent = fake_beeper.add_message(CHAT, "m0", "react to me please")
    tasks.handle_beeper_event(beeper_account.id, event("message.upserted", ["m0"], [parent]))
    parent["reactions"] = [{"id": ALICE, "reactionKey": "❤️", "participantID": ALICE}]
    reaction = fake_beeper.add_message(CHAT, "r0", type="REACTION", isHidden=True, linkedMessageID="m0")

    tasks.handle_beeper_event(beeper_account.id, event("message.upserted", ["r0"], [reaction]))

    assert {k: m.reactions for k, m in stored(db_session).items()} == {"m0": {"❤️": [ALICE]}}


def test_events_for_uncollected_chats_are_ignored(db_session, fake_beeper, beeper_account):
    chat = collected_chat(db_session, fake_beeper, beeper_account)
    chat.collect_messages = False
    db_session.commit()
    message = fake_beeper.add_message(CHAT, "m0", "not for memory")

    result = tasks.handle_beeper_event(beeper_account.id, event("message.upserted", ["m0"], [message]))

    assert result["status"] == "skipped"
    assert stored(db_session) == {}


def test_an_event_for_an_unknown_chat_records_it_and_starts_its_backfill(
    db_session, fake_beeper, beeper_account, no_celery_dispatch
):
    tasks.sync_beeper_account(beeper_account.id)
    chat = fake_beeper.add_chat(CHAT)
    no_celery_dispatch.reset_mock()

    tasks.handle_beeper_event(beeper_account.id, event("chat.upserted", [CHAT], [chat]))

    stored_chat = db_session.query(BeeperChat).filter_by(chat_id=CHAT).one()
    assert dispatched(no_celery_dispatch, SYNC_BEEPER_CHAT) == [stored_chat.id]


def test_events_of_another_account_cannot_reach_a_chat(
    db_session, fake_beeper, beeper_account, admin_user, monkeypatch
):
    chat = collected_chat(db_session, fake_beeper, beeper_account)
    monkeypatch.setattr(settings, "BEEPER_SERVER_URLS", [beeper_account.server_url, "http://other:23373"])
    other = BeeperAccount(
        created_by_user_id=admin_user.id, server_url="http://other:23373", verified=True, setup_state="ready"
    )
    other.token = fake_beeper.token
    db_session.add(other)
    db_session.commit()
    message = fake_beeper.add_message(CHAT, "m0", "belongs to the first account")

    tasks.handle_beeper_event(other.id, event("message.upserted", ["m0"], [message]))

    assert db_session.query(BeeperMessage).filter_by(chat_id=chat.id).count() == 0


def test_all_active_verified_accounts_are_reconciled(db_session, beeper_account, admin_user, no_celery_dispatch):
    paused = BeeperAccount(created_by_user_id=admin_user.id, server_url="http://paused:1", is_active=False, verified=True)
    unverified = BeeperAccount(created_by_user_id=admin_user.id, server_url="http://unverified:1")
    db_session.add_all([paused, unverified])
    db_session.commit()

    tasks.sync_all_beeper_accounts()

    assert dispatched(no_celery_dispatch, SYNC_BEEPER_ACCOUNT) == [beeper_account.id]
