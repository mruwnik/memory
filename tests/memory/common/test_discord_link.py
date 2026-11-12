"""Proving a Discord account is yours by DMing your own bot a one-time code."""

from datetime import datetime, timedelta, timezone

import hashlib
from typing import cast

import fakeredis
import pytest

from memory.common import discord_link, settings
from memory.common.db.models import (
    DiscordBot,
    DiscordChannel,
    DiscordMessage,
    DiscordServer,
    DiscordUser,
    HumanUser,
    PendingJob,
    Person,
)
from memory.common.redis_pool import get_redis
from memory.workers.tasks import discord

AUTHOR, OTHER_AUTHOR = 500000000000000001, 500000000000000002
DM, SERVER_CHANNEL = 600000000000000001, 600000000000000002
FAILED = "That code is wrong, used or expired. Get a new one in Memory."


@pytest.fixture
def owner(db_session):
    user = HumanUser(email="owner@example.com", name="Owner", password_hash="x")
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def bot(db_session, owner):
    bot = DiscordBot(application_id=777, name="Mine", created_by_user_id=owner.id, token="tok")
    db_session.add(bot)
    db_session.flush()
    server = DiscordServer(id=700000000000000001, name="Server", collect_messages=True)
    db_session.add_all(
        [
            server,
            DiscordChannel(id=DM, name="DM with alice", channel_type="dm", bot_id=bot.id),
            DiscordChannel(id=SERVER_CHANNEL, name="general", channel_type="text", server_id=server.id),
            DiscordUser(id=AUTHOR, username="alice"),
            DiscordUser(id=OTHER_AUTHOR, username="mallory"),
        ]
    )
    db_session.commit()
    return bot


@pytest.fixture(autouse=True)
def code_secret(monkeypatch):
    monkeypatch.setattr(settings, "TRANSFER_TOKEN_SECRET", "test-discord-link-secret")


@pytest.fixture
def replies(monkeypatch):
    sent = []
    monkeypatch.setattr(discord_link, "send_dm", lambda bot_id, user, text: sent.append((bot_id, user, text)) or True)
    return sent


def dm(bot, content, author=AUTHOR, channel=DM, message_id=1, is_edit=False):
    return discord.add_discord_message(
        bot_id=bot.id, message_id=message_id, channel_id=channel, author_id=author,
        content=content, sent_at="2026-11-12T12:00:00Z", is_edit=is_edit,
    )


def linked_user(db_session, discord_user_id=AUTHOR) -> int | None:
    db_session.expire_all()
    person = db_session.get(DiscordUser, discord_user_id).person
    return person and person.user_id


def with_person(db_session, owner):
    db_session.add(Person(identifier="owner", display_name="The Owner", user_id=owner.id))
    db_session.commit()


@pytest.mark.parametrize("give_person, name", [(with_person, "The Owner"), (lambda db, owner: None, "Owner")])
@pytest.mark.parametrize("command", ["link {code}", "  LINK {code} "])
def test_a_dmed_code_links_the_author_to_its_user(db_session, owner, bot, replies, give_person, name, command):
    give_person(db_session, owner)
    code, _ = discord_link.issue_code(owner.id)

    result = dm(bot, command.format(code=code))

    assert result["status"] == "verified"
    assert linked_user(db_session) == owner.id
    assert db_session.query(DiscordMessage).count() == 0
    assert replies == [(bot.id, str(AUTHOR), f"Linked to {name} (Memory login owner@example.com).")]


ON_ANOTHER_PERSON = (
    "Verified for Memory login owner@example.com, but this Discord account is on another person record. "
    "An admin has to merge the two."
)


@pytest.mark.parametrize("owner_has_person", [True, False])
def test_an_author_on_another_person_stays_there(db_session, owner, bot, replies, owner_has_person):
    """A code can be phished: whoever DMs it may be someone else, whose Person
    must never become the code user's."""
    if owner_has_person:
        db_session.add(Person(identifier="owner", display_name="The Owner", user_id=owner.id, creator_id=owner.id))
        db_session.commit()
    dm(bot, "an ordinary first message long enough to be stored", message_id=1)
    placeholder = db_session.get(DiscordUser, AUTHOR).person
    code, _ = discord_link.issue_code(owner.id)

    result = dm(bot, f"link {code}", message_id=2)

    assert result["status"] == "verified"
    db_session.expire_all()
    author = db_session.get(DiscordUser, AUTHOR)
    assert (author.person_id, author.verified_user_id) == (placeholder.id, owner.id)
    assert db_session.get(Person, placeholder.id).user_id is None
    assert replies == [(bot.id, str(AUTHOR), ON_ANOTHER_PERSON)]


@pytest.mark.parametrize("second_author", [AUTHOR, OTHER_AUTHOR])
def test_a_code_works_once(db_session, owner, bot, replies, second_author):
    code, _ = discord_link.issue_code(owner.id)
    dm(bot, f"link {code}", message_id=1)

    result = dm(bot, f"link {code}", author=second_author, message_id=2)

    assert result["reason"] == "bad_code"
    assert linked_user(db_session, OTHER_AUTHOR) is None


def test_a_new_code_replaces_the_users_last(db_session, owner, bot, replies):
    old, _ = discord_link.issue_code(owner.id)
    discord_link.issue_code(owner.id)

    assert dm(bot, f"link {old}")["reason"] == "bad_code"


@pytest.mark.parametrize(
    "wrong, status, reply_count",
    [(discord_link.MAX_FAILURES - 1, "verified", discord_link.MAX_FAILURES), (discord_link.MAX_FAILURES, "skipped", discord_link.MAX_FAILURES)],
)
def test_wrong_codes_lock_the_author_out(db_session, owner, bot, replies, wrong, status, reply_count):
    code, _ = discord_link.issue_code(owner.id)
    for message_id in range(wrong):
        dm(bot, "link 00000000", message_id=100 + message_id)

    result = dm(bot, f"link {code}", message_id=1)

    assert result["status"] == status
    assert len(replies) == reply_count


def test_a_redelivered_message_is_tried_once(db_session, owner, bot, replies):
    dm(bot, "link 00000000")

    result = dm(bot, "link 00000000")

    assert result["reason"] == "seen"
    assert len(replies) == 1


@pytest.mark.parametrize(
    "where",
    [{"channel": SERVER_CHANNEL}, {"is_edit": True}],
)
def test_a_code_posted_anywhere_but_a_new_dm_is_spent_silently(db_session, owner, bot, replies, where):
    code, _ = discord_link.issue_code(owner.id)

    posted = dm(bot, f"link {code}", message_id=1, **where)

    assert posted == {"status": "skipped", "reason": "link_code_elsewhere"}
    assert linked_user(db_session) is None
    assert db_session.query(DiscordMessage).count() == 0
    assert replies == []
    assert dm(bot, f"link {code}", message_id=2)["reason"] == "bad_code"


def test_a_link_to_a_web_page_is_an_ordinary_message(db_session, owner, bot, replies, qdrant):
    assert dm(bot, "link https://example.com/x", channel=SERVER_CHANNEL)["status"] != "skipped"
    assert db_session.query(DiscordMessage).count() == 1


def test_an_author_linked_to_another_user_is_not_moved(db_session, owner, bot, replies):
    other = HumanUser(email="other@example.com", name="Other", password_hash="x")
    db_session.add(other)
    db_session.flush()
    theirs = Person(identifier="other", display_name="Other", user_id=other.id)
    db_session.add(theirs)
    db_session.flush()
    db_session.get(DiscordUser, AUTHOR).person_id = theirs.id
    db_session.commit()
    code, _ = discord_link.issue_code(owner.id)

    result = dm(bot, f"link {code}")

    assert result["reason"] == "refused"
    assert linked_user(db_session) == other.id
    assert replies == [(bot.id, str(AUTHOR), FAILED)]


def test_a_code_dmed_to_someone_elses_bot_is_burnt(db_session, owner, bot, replies):
    stranger = HumanUser(email="stranger@example.com", name="Stranger", password_hash="x")
    db_session.add(stranger)
    db_session.flush()
    theirs = DiscordBot(application_id=888, name="Theirs", created_by_user_id=stranger.id, token="tok2")
    db_session.add(theirs)
    db_session.flush()
    db_session.add(DiscordChannel(id=DM + 10, name="DM with alice", channel_type="dm", bot_id=theirs.id))
    db_session.commit()
    code, _ = discord_link.issue_code(owner.id)

    result = dm(theirs, f"link {code}", channel=DM + 10)

    assert result["reason"] == "refused"
    assert dm(bot, f"link {code}", message_id=2)["reason"] == "bad_code"
    assert linked_user(db_session) is None


def test_issued_codes_expire_in_a_quarter_hour(owner, monkeypatch):
    redis = fakeredis.FakeRedis()
    monkeypatch.setattr(discord_link, "get_redis", lambda: redis)
    before = datetime.now(timezone.utc)

    code, expires_at = discord_link.issue_code(owner.id)

    assert code.isdigit() and len(code) == 8
    assert timedelta(minutes=14) < expires_at - before <= timedelta(minutes=15, seconds=1)
    assert 14 * 60 < cast(int, redis.ttl(discord_link.code_key(discord_link.digest(code)))) <= 15 * 60


def job_params(db_session) -> str:
    db_session.expire_all()
    return " ".join(str(job.params) for job in db_session.query(PendingJob))


def test_no_job_keeps_a_live_code_after_a_lockout_or_a_redelivery(db_session, owner, bot, replies):
    code, _ = discord_link.issue_code(owner.id)
    for message_id in range(discord_link.MAX_FAILURES):
        dm(bot, "link 00000000", message_id=100 + message_id)
    dm(bot, "link 00000000", message_id=100)  # redelivered
    dm(bot, "link 00000000", message_id=100, is_edit=True)
    assert dm(bot, f"link {code}", message_id=1)["reason"] == "locked_out"
    discord.handle_discord_event(
        bot_id=bot.id,
        event_type="MESSAGE_CREATE",
        data={
            "id": 2, "channel_id": DM, "author": {"id": AUTHOR, "username": "alice"}, "content": f"link {code}",
            "timestamp": "2026-11-12T12:00:00+00:00", "type": 0, "attachments": [], "embeds": [],
        },
        pushed=False,
    )

    assert discord_link.take_code(code) == owner.id  # still live: nothing redeemed it
    params = job_params(db_session)
    assert db_session.query(PendingJob).count() >= discord_link.MAX_FAILURES + 3
    assert f"link {code}" not in params and "link 00000000" not in params
    assert discord_link.REDACTED in params


def test_an_ordinary_message_keeps_its_content_in_its_job(db_session, owner, bot, replies, qdrant):
    dm(bot, "an ordinary message long enough to be stored")

    assert "an ordinary message long enough to be stored" in job_params(db_session)


def test_only_a_keyed_digest_of_a_code_is_stored(owner):
    code, _ = discord_link.issue_code(owner.id)
    keys = [k.decode() if isinstance(k, bytes) else k for k in cast(list, get_redis().keys("discord-link:code:*"))]

    assert keys == [discord_link.code_key(discord_link.digest(code))]
    assert hashlib.sha256(code.encode()).hexdigest() not in keys[0]


STRANGER, STRANGER_DM = 500000000000000009, 600000000000000009


def test_a_pushed_link_dm_from_a_stranger_is_read_back_and_tried(db_session, owner, bot, replies, monkeypatch):
    """The push names no author it can be trusted for: the one Discord reads
    back is verified, and no channel or message is stored."""
    code, _ = discord_link.issue_code(owner.id)
    read_back = {
        "id": "3", "channel_id": str(STRANGER_DM), "author": {"id": str(STRANGER), "username": "stranger"},
        "content": f"link {code}",
    }
    monkeypatch.setattr(discord, "read_dm", lambda bot_id, channel_id: {"type": 1})
    monkeypatch.setattr(discord, "read_pushed_message", lambda bot_id, channel_id, message_id: read_back)

    result = discord.handle_discord_event(
        bot_id=bot.id,
        event_type="MESSAGE_CREATE",
        data={"id": "3", "channel_id": str(STRANGER_DM), "author": {"id": str(AUTHOR)}, "content": f"link {code}"},
        pushed=True,
    )

    assert result["status"] == "verified"
    db_session.expire_all()
    assert db_session.get(DiscordUser, STRANGER).verified_user_id == owner.id
    assert db_session.get(DiscordUser, AUTHOR).verified_user_id is None
    assert db_session.get(DiscordChannel, STRANGER_DM) is None
    assert db_session.query(DiscordMessage).count() == 0
