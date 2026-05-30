import pytest

from memory.api.MCP.servers.notification_targets import resolve_and_validate_target
from memory.common.db.models import Person
from memory.common.db.models.discord import (
    DiscordBot,
    DiscordChannel,
    DiscordServer,
    DiscordUser,
)
from memory.common.db.models.slack import (
    SlackApp,
    SlackChannel,
    SlackUserCredentials,
    SlackWorkspace,
)


@pytest.fixture
def discord_server(db_session):
    server = DiscordServer(id=900100, name="Test Guild", collect_messages=False)
    db_session.add(server)
    db_session.commit()
    return server


@pytest.fixture
def discord_channel(db_session, discord_server):
    channel = DiscordChannel(
        id=555000111, server_id=discord_server.id, name="general", channel_type="text"
    )
    db_session.add(channel)
    db_session.commit()
    return channel


@pytest.fixture
def person_with_discord(db_session):
    person = Person(
        identifier="ada-lovelace",
        display_name="Ada Lovelace",
        aliases=[],
        contact_info={"email": "ada@example.com"},
    )
    db_session.add(person)
    db_session.flush()
    db_session.add(
        DiscordUser(id=778899, username="ada", display_name="Ada", person_id=person.id)
    )
    person.contact_info = {
        **person.contact_info,
        "slack": {"T123": {"user_id": "U777"}},
    }
    db_session.commit()
    return person


@pytest.fixture
def person_no_links(db_session):
    person = Person(
        identifier="no-links",
        display_name="No Links",
        aliases=[],
        contact_info={},
    )
    db_session.add(person)
    db_session.commit()
    return person


# --- email ---


@pytest.mark.parametrize(
    "target,expected",
    [
        ("someone@example.com", "someone@example.com"),
        ("  spaced@example.com  ", "spaced@example.com"),
    ],
)
def test_email_accepts_address(db_session, admin_user, target, expected):
    assert (
        resolve_and_validate_target(db_session, admin_user.id, "email", target)
        == expected
    )


def test_email_resolves_person(db_session, admin_user, person_with_discord):
    assert (
        resolve_and_validate_target(db_session, admin_user.id, "email", "ada-lovelace")
        == "ada@example.com"
    )


def test_email_rejects_unknown_name(db_session, admin_user):
    # No "@" and no matching person → can't resolve to an address.
    with pytest.raises(ValueError, match="No email address found"):
        resolve_and_validate_target(db_session, admin_user.id, "email", "not-an-email")


def test_email_accepts_unusual_address(db_session, admin_user):
    # Anything with "@" is accepted verbatim (email syntax isn't re-validated).
    assert (
        resolve_and_validate_target(
            db_session, admin_user.id, "email", "a+b@sub.example.co.uk"
        )
        == "a+b@sub.example.co.uk"
    )


def test_email_person_without_email(db_session, admin_user, person_no_links):
    with pytest.raises(ValueError, match="No email address on file"):
        resolve_and_validate_target(db_session, admin_user.id, "email", "no-links")


# --- discord ---


def test_discord_channel_id_passes(db_session, admin_user, discord_channel):
    assert resolve_and_validate_target(
        db_session, admin_user.id, "discord", str(discord_channel.id)
    ) == str(discord_channel.id)


def test_discord_channel_gated_for_regular(db_session, regular_user, discord_channel):
    # discord_channel's server has no bot owned by regular_user → not visible.
    with pytest.raises(ValueError, match="not found or not accessible"):
        resolve_and_validate_target(
            db_session, regular_user.id, "discord", str(discord_channel.id)
        )


def test_discord_channel_visible_via_user_bot(
    db_session, regular_user, discord_server, discord_channel
):
    bot = DiscordBot(id=321, name="Bot")
    bot.authorized_users.append(regular_user)
    discord_server.bot_id = bot.id
    db_session.add(bot)
    db_session.commit()
    assert resolve_and_validate_target(
        db_session, regular_user.id, "discord", str(discord_channel.id)
    ) == str(discord_channel.id)


def test_discord_user_id_visible_to_admin(db_session, admin_user):
    # Admins can see any DiscordUser id, even an unknown one (caller_can_see).
    assert (
        resolve_and_validate_target(db_session, admin_user.id, "discord", "424242")
        == "424242"
    )


def test_discord_user_id_hidden_from_regular(db_session, regular_user):
    with pytest.raises(ValueError, match="not found or not accessible"):
        resolve_and_validate_target(db_session, regular_user.id, "discord", "424242")


def test_discord_resolves_person(db_session, admin_user, person_with_discord):
    assert (
        resolve_and_validate_target(
            db_session, admin_user.id, "discord", "ada-lovelace"
        )
        == "778899"
    )


def test_discord_person_without_discord(db_session, admin_user, person_no_links):
    with pytest.raises(ValueError, match="no linked Discord account"):
        resolve_and_validate_target(db_session, admin_user.id, "discord", "no-links")


def test_discord_person_gated_for_regular(
    db_session, regular_user, person_with_discord
):
    # A non-admin can't DM a person's Discord account their bots have never seen
    # (same gate as the raw-id path).
    with pytest.raises(ValueError, match="not accessible to you"):
        resolve_and_validate_target(
            db_session, regular_user.id, "discord", "ada-lovelace"
        )


def test_discord_non_numeric_non_person(db_session, admin_user):
    with pytest.raises(ValueError, match="not a known person, channel, or user id"):
        resolve_and_validate_target(db_session, admin_user.id, "discord", "nobody")


# --- slack ---


@pytest.fixture
def slack_workspace_for_admin(db_session, admin_user):
    app = SlackApp(client_id="cid", name="Test App")
    db_session.add(app)
    db_session.flush()
    ws = SlackWorkspace(id="T123", name="Acme")
    db_session.add(ws)
    db_session.flush()
    db_session.add(
        SlackUserCredentials(
            slack_app_id=app.id,
            workspace_id="T123",
            user_id=admin_user.id,
            access_token_encrypted=b"tok",
        )
    )
    db_session.commit()
    return ws


def test_slack_user_id_with_workspace(
    db_session, admin_user, slack_workspace_for_admin
):
    assert (
        resolve_and_validate_target(db_session, admin_user.id, "slack", "U999")
        == "U999"
    )


def test_slack_user_id_without_workspace(db_session, admin_user):
    with pytest.raises(ValueError, match="no connected Slack workspace"):
        resolve_and_validate_target(db_session, admin_user.id, "slack", "U999")


def test_slack_channel_in_accessible_workspace(
    db_session, admin_user, slack_workspace_for_admin
):
    db_session.add(
        SlackChannel(
            id="C500",
            workspace_id="T123",
            name="general",
            channel_type="public_channel",
        )
    )
    db_session.commit()
    assert (
        resolve_and_validate_target(db_session, admin_user.id, "slack", "C500")
        == "C500"
    )


def test_slack_channel_unknown(db_session, admin_user, slack_workspace_for_admin):
    with pytest.raises(ValueError, match="not found or not accessible"):
        resolve_and_validate_target(db_session, admin_user.id, "slack", "C404")


def test_slack_resolves_person(
    db_session, admin_user, person_with_discord, slack_workspace_for_admin
):
    assert (
        resolve_and_validate_target(db_session, admin_user.id, "slack", "ada-lovelace")
        == "U777"
    )


def test_slack_person_requires_shared_workspace(
    db_session, regular_user, person_with_discord
):
    # person_with_discord has a Slack id in workspace T123, but regular_user has
    # no membership there — resolution must fail, not leak the id.
    with pytest.raises(ValueError, match="workspace you share"):
        resolve_and_validate_target(
            db_session, regular_user.id, "slack", "ada-lovelace"
        )


def test_empty_target_rejected(db_session, admin_user):
    with pytest.raises(ValueError, match="notification_target is required"):
        resolve_and_validate_target(db_session, admin_user.id, "discord", "  ")
