"""Tests for /discord/users access control.

The endpoints used to be open to any user with at least one Discord bot,
which let bot owners enumerate / re-link any other Discord user — including
the admin's, which is an identity-hijack vector. These tests pin the new
visibility model:

- A non-admin can only see DiscordUsers that one of their bots has
  authored a stored DiscordMessage for.
- A non-admin can only set ``system_user_id`` to their own user id.
- A non-admin can only set ``person_id`` to a Person they already own
  (or one with no User link).
- Admins bypass all of the above.
"""

from datetime import datetime, timezone

import pytest

from memory.common.db.models import (
    DiscordBot,
    DiscordChannel,
    DiscordMessage,
    DiscordUser,
    HumanUser,
    Person,
    SourceItem,
)


@pytest.fixture
def other_user(db_session):
    other = HumanUser(
        id=999,
        email="other@example.com",
        name="Other User",
        password_hash="bcrypt_hash_placeholder",
    )
    db_session.add(other)
    db_session.commit()
    return other


@pytest.fixture
def my_bot(db_session, user):
    """A Discord bot owned by the regular_client's `user`."""
    bot = DiscordBot(id=11111, name="My Bot")
    bot.authorized_users.append(user)
    db_session.add(bot)
    db_session.commit()
    return bot


@pytest.fixture
def their_bot(db_session, other_user):
    """A Discord bot owned by `other_user` (foreign to the regular_client)."""
    bot = DiscordBot(id=22222, name="Their Bot")
    bot.authorized_users.append(other_user)
    db_session.add(bot)
    db_session.commit()
    return bot


@pytest.fixture
def discord_channel(db_session):
    channel = DiscordChannel(id=33333, name="general", channel_type="text")
    db_session.add(channel)
    db_session.commit()
    return channel


def _make_discord_user(db_session, *, id_: int, name: str = "alice") -> DiscordUser:
    du = DiscordUser(id=id_, username=name, display_name=name.title())
    db_session.add(du)
    db_session.commit()
    return du


def _record_message(
    db_session,
    *,
    bot: DiscordBot,
    author: DiscordUser,
    channel: DiscordChannel,
    message_id: int,
) -> None:
    """Insert a DiscordMessage so ``bot`` "has seen" ``author``."""
    # DiscordMessage inherits SourceItem (joined-table polymorphism), so
    # creating it adds the parent SourceItem row automatically. Use a
    # message_id-derived sha256 so each call is unique within a test.
    msg = DiscordMessage(
        modality="discord_message",
        sha256=message_id.to_bytes(32, "big"),
        size=10,
        message_id=message_id,
        channel_id=channel.id,
        author_id=author.id,
        bot_id=bot.id,
        sent_at=datetime.now(timezone.utc),
    )
    db_session.add(msg)
    db_session.commit()


# --- list_discord_users ---------------------------------------------------


def test_list_users_filters_to_callers_bot_authors(
    regular_client,
    db_session,
    user,
    other_user,
    my_bot,
    their_bot,
    discord_channel,
):
    """Only DiscordUsers seen by the caller's bot show up."""
    visible = _make_discord_user(db_session, id_=1001, name="visible")
    hidden = _make_discord_user(db_session, id_=1002, name="hidden")
    _record_message(
        db_session, bot=my_bot, author=visible, channel=discord_channel, message_id=1
    )
    _record_message(
        db_session,
        bot=their_bot,
        author=hidden,
        channel=discord_channel,
        message_id=2,
    )

    response = regular_client.get("/discord/users")

    assert response.status_code == 200
    ids = {row["id"] for row in response.json()}
    assert ids == {str(visible.id)}


def test_list_users_admin_sees_all(
    client,
    db_session,
    user,
    other_user,
    my_bot,
    their_bot,
    discord_channel,
):
    """Admin (default test client) sees DiscordUsers regardless of bot ownership."""
    visible = _make_discord_user(db_session, id_=1003, name="visible")
    other = _make_discord_user(db_session, id_=1004, name="other")
    _record_message(
        db_session, bot=my_bot, author=visible, channel=discord_channel, message_id=3
    )
    _record_message(
        db_session, bot=their_bot, author=other, channel=discord_channel, message_id=4
    )

    response = client.get("/discord/users")

    assert response.status_code == 200
    ids = {row["id"] for row in response.json()}
    assert {str(visible.id), str(other.id)} <= ids


# --- get_discord_user ------------------------------------------------------


def test_get_user_404_when_not_seen_by_callers_bot(
    regular_client,
    db_session,
    user,
    other_user,
    my_bot,
    their_bot,
    discord_channel,
):
    """Reading a DiscordUser that only another user's bot has seen returns 404."""
    target = _make_discord_user(db_session, id_=2001, name="target")
    _record_message(
        db_session, bot=their_bot, author=target, channel=discord_channel, message_id=10
    )

    response = regular_client.get(f"/discord/users/{target.id}")

    assert response.status_code == 404


def test_get_user_succeeds_when_callers_bot_saw_them(
    regular_client,
    db_session,
    user,
    my_bot,
    discord_channel,
):
    target = _make_discord_user(db_session, id_=2002, name="target")
    _record_message(
        db_session, bot=my_bot, author=target, channel=discord_channel, message_id=11
    )

    response = regular_client.get(f"/discord/users/{target.id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(target.id)


# --- link_discord_user -----------------------------------------------------


def test_link_user_403_when_linking_to_other_user(
    regular_client,
    db_session,
    user,
    other_user,
    my_bot,
    discord_channel,
):
    """Non-admin cannot point system_user_id at another user (identity hijack)."""
    target = _make_discord_user(db_session, id_=3001, name="target")
    _record_message(
        db_session, bot=my_bot, author=target, channel=discord_channel, message_id=20
    )

    response = regular_client.patch(
        f"/discord/users/{target.id}",
        json={"system_user_id": other_user.id},
    )

    assert response.status_code == 403
    db_session.expire_all()
    refreshed = db_session.get(DiscordUser, target.id)
    assert refreshed.system_user_id is None


def test_link_user_succeeds_for_self_link(
    regular_client,
    db_session,
    user,
    my_bot,
    discord_channel,
):
    target = _make_discord_user(db_session, id_=3002, name="target")
    _record_message(
        db_session, bot=my_bot, author=target, channel=discord_channel, message_id=21
    )

    response = regular_client.patch(
        f"/discord/users/{target.id}",
        json={"system_user_id": user.id},
    )

    assert response.status_code == 200
    db_session.expire_all()
    refreshed = db_session.get(DiscordUser, target.id)
    assert refreshed.system_user_id == user.id


def test_link_user_403_when_person_belongs_to_other_user(
    regular_client,
    db_session,
    user,
    other_user,
    my_bot,
    discord_channel,
):
    """Non-admin cannot link to a Person owned by another user."""
    target = _make_discord_user(db_session, id_=3003, name="target")
    _record_message(
        db_session, bot=my_bot, author=target, channel=discord_channel, message_id=22
    )
    other_person = Person(
        identifier="other-person", display_name="Other Person", user_id=other_user.id
    )
    db_session.add(other_person)
    db_session.commit()

    response = regular_client.patch(
        f"/discord/users/{target.id}",
        json={"person_id": other_person.id},
    )

    assert response.status_code == 403
    db_session.expire_all()
    refreshed = db_session.get(DiscordUser, target.id)
    assert refreshed.person_id is None


def test_link_user_404_when_target_not_seen_by_callers_bot(
    regular_client,
    db_session,
    user,
    other_user,
    their_bot,
    discord_channel,
):
    """Even self-linking is forbidden if the target is invisible to the caller."""
    target = _make_discord_user(db_session, id_=3004, name="target")
    _record_message(
        db_session, bot=their_bot, author=target, channel=discord_channel, message_id=23
    )

    response = regular_client.patch(
        f"/discord/users/{target.id}",
        json={"system_user_id": user.id},
    )

    # Either 403 (forbidden — caller can't see) or 404 (hidden) is acceptable;
    # the contract is that the caller cannot link to invisible targets.
    assert response.status_code in (403, 404)


def test_admin_can_cross_link(
    client,
    db_session,
    other_user,
    my_bot,
    discord_channel,
):
    """Admin (default test client) can link to any user / any person."""
    target = _make_discord_user(db_session, id_=3005, name="target")
    _record_message(
        db_session, bot=my_bot, author=target, channel=discord_channel, message_id=24
    )

    response = client.patch(
        f"/discord/users/{target.id}",
        json={"system_user_id": other_user.id},
    )

    assert response.status_code == 200
    db_session.expire_all()
    refreshed = db_session.get(DiscordUser, target.id)
    assert refreshed.system_user_id == other_user.id
