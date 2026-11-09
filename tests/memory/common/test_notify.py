"""Tests for memory.common.notify channel selection."""

import pytest

from memory.common.db.models import DiscordBot, DiscordChannel, DiscordUser, EmailAccount, HumanUser, Person
from memory.common.notify import (
    CHANNEL_PRIORITY,
    channel_problems,
    get_notification_channel,
    user_info_for,
)

DISCORD = {"discord_accounts": {"123": "someuser"}, "discord_bots": [7]}
DISCORD_NO_BOT = {"discord_accounts": {"123": "someuser"}, "discord_bots": []}
DISCORD_NO_ACCOUNT = {"discord_accounts": {}, "discord_bots": [7]}
SLACK = {"slack_accounts": {"U1": "T1"}}
EMAIL = {"email_accounts": [{"email_address": "me@example.com", "send_enabled": True}]}
EMAIL_NO_SEND = {"email_accounts": [{"email_address": "me@example.com", "send_enabled": False}]}
EMAIL_LOGIN_ONLY = {"email_accounts": [{"email_address": "me@example.com", "account_type": "primary"}]}

USER_SHAPES = [
    {},
    DISCORD,
    DISCORD_NO_BOT,
    DISCORD_NO_ACCOUNT,
    SLACK,
    EMAIL,
    {**DISCORD, **SLACK, **EMAIL},
    {**DISCORD_NO_BOT, **SLACK},
    {**DISCORD_NO_ACCOUNT, **EMAIL},
    {**SLACK, **EMAIL},
    EMAIL_NO_SEND,
    EMAIL_LOGIN_ONLY,
    {**SLACK, **EMAIL_NO_SEND},
    {"email_accounts": [{"email_address": None, "send_enabled": True}]},
    {"email_accounts": [{"email_address": None, "send_enabled": True}, EMAIL["email_accounts"][0]]},
    {"email_accounts": [EMAIL_NO_SEND["email_accounts"][0], {"email_address": "b@example.com", "send_enabled": True}]},
]


@pytest.mark.parametrize(
    "user, expected",
    [
        ({}, {"discord": "no Discord account", "slack": "no Slack", "email": "no email"}),
        (DISCORD, {"slack": "no Slack", "email": "no email"}),
        (DISCORD_NO_BOT, {"discord": "no Discord bot of your own", "slack": "no Slack", "email": "no email"}),
        (DISCORD_NO_ACCOUNT, {"discord": "no Discord account", "slack": "no Slack", "email": "no email"}),
        ({**DISCORD_NO_BOT, **SLACK, **EMAIL}, {"discord": "no Discord bot of your own"}),
        ({**DISCORD, **SLACK, **EMAIL}, {}),
        ({"email_accounts": [{"email_address": None}]}, {"discord": "no Discord account", "slack": "no Slack", "email": "no email"}),
        ({**SLACK, **EMAIL_NO_SEND}, {"discord": "no Discord account", "email": "can't send"}),
        ({**SLACK, **EMAIL_LOGIN_ONLY}, {"discord": "no Discord account", "email": "can't send"}),
    ],
)
def test_channel_problems(user, expected):
    problems = channel_problems({"user": user})
    assert problems.keys() == expected.keys()
    assert all(expected[name] in why for name, why in problems.items())


def test_the_missing_discord_reason_sends_you_to_an_admin():
    """Only admins can link a Discord account or set contact_info.discord."""
    assert "(ask an admin to link your Discord account, or to add your Discord user id" in (
        channel_problems({"user": {}})["discord"]
    )


@pytest.mark.parametrize("user", USER_SHAPES)
@pytest.mark.parametrize("channel", CHANNEL_PRIORITY)
def test_forced_channel_is_chosen_iff_it_has_no_problem(user, channel):
    user_info = {"user": user}
    chosen = get_notification_channel(user_info, channel)
    assert (chosen is not None) == (channel not in channel_problems(user_info))


@pytest.mark.parametrize("user", USER_SHAPES)
def test_default_channel_is_first_without_a_problem(user):
    user_info = {"user": user}
    usable = [c for c in CHANNEL_PRIORITY if c not in channel_problems(user_info)]
    chosen = get_notification_channel(user_info, None)
    assert (chosen and chosen[0]) == next(iter(usable), None)


@pytest.fixture
def person_user(db_session):
    user = HumanUser(name="Notify Me", email="notify@example.com", password_hash="x", scopes=[])
    db_session.add(user)
    db_session.flush()
    person = Person(identifier="notify_me", display_name="Notify Me", user_id=user.id)
    db_session.add(person)
    db_session.commit()
    return user


@pytest.mark.parametrize(
    "discord_contact, expected",
    [
        ("123456789", {"123456789": None}),
        (123456789, {"123456789": None}),
        (["111", "someuser", "222"], {"111": None, "222": None}),
        ("someuser", {}),
        (None, {}),
    ],
)
def test_user_info_for_takes_discord_ids_from_contact_info(db_session, person_user, discord_contact, expected):
    person_user.person.contact_info = {"discord": discord_contact}
    db_session.commit()

    assert user_info_for(db_session, person_user)["discord_accounts"] == expected


def test_user_info_for_prefers_linked_discord_user_over_contact_info(db_session, person_user):
    person_user.person.contact_info = {"discord": "111"}
    db_session.add(DiscordUser(id=999, username="linked", person_id=person_user.person.id))
    db_session.commit()

    assert user_info_for(db_session, person_user)["discord_accounts"] == {"999": "linked"}


def test_contact_info_discord_id_with_own_bot_notifies_over_discord(db_session, person_user):
    person_user.person.contact_info = {"discord": "123456789"}
    bot = DiscordBot(application_id=1, name="bot", created_by_user_id=person_user.id)
    db_session.add(bot)
    db_session.commit()

    user_info = {"user": user_info_for(db_session, person_user)}

    assert get_notification_channel(user_info, None) == ("discord", "123456789", {"discord_bot_id": bot.id})


def test_email_goes_from_the_first_account_that_can_send():
    user = {"email_accounts": [EMAIL_NO_SEND["email_accounts"][0], {"email_address": "b@example.com", "send_enabled": True}]}
    assert get_notification_channel({"user": user}, None) == ("email", "b@example.com", {"from_address": "b@example.com"})


def add_email_account(db_session, user, **kwargs):
    db_session.add(
        EmailAccount(user_id=user.id, name="Mail", email_address="mail@example.com", account_type="imap", **kwargs)
    )
    db_session.commit()


@pytest.mark.parametrize(
    "account, email_has_problem",
    [
        ({"active": False}, True),
        ({"send_enabled": False}, True),
        ({"send_enabled": True, "active": False}, True),
        ({"send_enabled": True}, False),
    ],
)
def test_email_is_usable_only_from_an_active_send_enabled_account(db_session, person_user, account, email_has_problem):
    add_email_account(db_session, person_user, **account)

    problems = channel_problems({"user": user_info_for(db_session, person_user)})

    assert ("email" in problems) == email_has_problem


@pytest.mark.parametrize(
    "account, expected_channel",
    [
        ({"active": False}, None),
        ({"send_enabled": False}, None),
        ({"send_enabled": True}, ("email", "mail@example.com", {"from_address": "mail@example.com"})),
    ],
)
def test_notification_email_needs_an_account_that_can_send(db_session, person_user, account, expected_channel):
    add_email_account(db_session, person_user, **account)

    user_info = {"user": user_info_for(db_session, person_user)}

    assert get_notification_channel(user_info, None) == expected_channel


def test_login_email_is_still_listed_but_cannot_send(db_session, person_user):
    accounts = user_info_for(db_session, person_user)["email_accounts"]

    assert accounts == [
        {"email_address": "notify@example.com", "name": "Notify Me", "account_type": "primary", "send_enabled": False}
    ]


def test_contact_info_discord_id_of_a_known_channel_is_dropped(db_session, person_user):
    person_user.person.contact_info = {"discord": ["555", "123456789"]}
    db_session.add(DiscordChannel(id=555, name="general", channel_type="text"))
    db_session.commit()

    assert user_info_for(db_session, person_user)["discord_accounts"] == {"123456789": None}


def test_contact_info_discord_id_linked_to_another_person_is_dropped(db_session, person_user):
    other = Person(identifier="someone_else", display_name="Someone Else")
    db_session.add(other)
    db_session.flush()
    db_session.add_all(
        [
            DiscordUser(id=666, username="theirs", person_id=other.id),
            DiscordUser(id=777, username="unlinked", person_id=None),
        ]
    )
    person_user.person.contact_info = {"discord": ["666", "777", "123456789"]}
    db_session.commit()

    assert user_info_for(db_session, person_user)["discord_accounts"] == {"777": None, "123456789": None}
