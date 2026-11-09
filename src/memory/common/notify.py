"""Notify a user on their preferred channel. Worker-safe: no API imports."""

import logging
from typing import Any

from memory.common.celery_app import SEND_NOTIFICATION
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import DBSession
from memory.common.db.models import (
    DiscordUser,
    EmailAccount,
    GithubAccount,
    Person,
    SlackUserCredentials,
    User,
)
from memory.common.discord_data import discord_target_is_channel
from memory.common.people import contact_values, discord_ids, slack_accounts

logger = logging.getLogger(__name__)


def contact_info_discord_ids(session: DBSession, person: Person) -> list[str]:
    """Discord user ids in the Person's contact_info that can only reach them.

    Drops ids of known channels (a notification to one posts in the channel) and
    of Discord users linked to another Person (it would DM someone else).
    """
    ids = discord_ids(person.contact_info)
    if not ids:
        return []
    claimed = {
        str(discord_id)
        for (discord_id,) in session.query(DiscordUser.id).filter(
            DiscordUser.id.in_([int(i) for i in ids]),
            DiscordUser.person_id.is_not(None),
            DiscordUser.person_id != person.id,
        )
    }
    return [i for i in ids if i not in claimed and not discord_target_is_channel(session, i)]


def user_info_for(session: DBSession, user: User) -> dict:
    """A user's profile plus the accounts notifications can be delivered to.

    Each email account carries ``send_enabled``: whether notification email can be
    sent from it. The ``primary`` entry for ``User.email``, shown when the user has
    no email account, never can.
    """
    user_info = user.serialize()

    # Add email accounts
    email_accounts = (
        session.query(EmailAccount)
        .filter(
            EmailAccount.user_id == user.id, EmailAccount.active.is_(True)
        )
        .all()
    )
    user_info["email_accounts"] = [
        {
            "email_address": a.email_address,
            "name": a.name,
            "account_type": a.account_type,
            "send_enabled": a.send_enabled,
        }
        for a in email_accounts
    ]

    # Add Slack accounts from credentials table
    slack_credentials = (
        session.query(SlackUserCredentials)
        .filter(SlackUserCredentials.user_id == user.id)
        .all()
    )
    user_info["slack_accounts"] = {
        cred.slack_user_id: cred.workspace_id for cred in slack_credentials
    }

    # Add GitHub accounts
    github_accounts = (
        session.query(GithubAccount)
        .filter(GithubAccount.user_id == user.id)
        .all()
    )
    # ``name`` is self-attested and must not be used for identity
    # decisions; ``verified_login`` is what GitHub reports for the
    # stored credentials and is None until verified (see issue #84).
    user_info["github_accounts"] = [
        {
            "name": a.name,
            "auth_type": a.auth_type,
            "verified_login": a.verified_login,
        }
        for a in github_accounts
    ]

    person = user.person
    if person:
        # Discord ids in contact_info when no Discord account is linked to the Person
        if not user_info["discord_accounts"]:
            user_info["discord_accounts"] = {i: None for i in contact_info_discord_ids(session, person)}

        # Add Slack from Person contact_info if no credentials found
        if not slack_credentials and person.contact_info:
            for workspace_id, slack_user_id in slack_accounts(person.contact_info):
                user_info["slack_accounts"][slack_user_id] = workspace_id

        # Add GitHub from Person if no GithubAccount credentials found
        if not github_accounts and person.github_accounts:
            user_info["github_accounts"] = [
                {"username": acct.username, "id": acct.id} for acct in person.github_accounts
            ]
        elif not github_accounts and person.contact_info:
            github_usernames = contact_values(
                person.contact_info.get("github") or person.contact_info.get("github_username")
            )
            if github_usernames:
                user_info["github_accounts"] = [{"username": u} for u in github_usernames]

    # Fall back to user's own email if no dedicated email accounts
    if not email_accounts and user.email:
        user_info["email_accounts"] = [
            {
                "email_address": user.email,
                "name": user.name,
                "account_type": "primary",
                "send_enabled": False,
            }
        ]

    return user_info


CHANNEL_PRIORITY = ("discord", "slack", "email")


def sending_email_address(user: dict) -> str | None:
    """The first email address notification email can be sent from, if any."""
    return next(
        (a["email_address"] for a in user.get("email_accounts", []) if a.get("email_address") and a.get("send_enabled")),
        None,
    )


def channel_problems(user_info: dict) -> dict[str, str]:
    """Why each channel get_notification_channel skips can't be used."""
    user = user_info.get("user", {})
    problems = {}
    if not user.get("discord_accounts"):
        problems["discord"] = (
            "no Discord account is linked to your Person (ask an admin to link your "
            "Discord account, or to add your Discord user id to its contact_info)"
        )
    elif not user.get("discord_bots"):
        problems["discord"] = (
            "you have no Discord bot of your own: notifications are sent by a bot you "
            "registered, not one someone else added"
        )
    if not user.get("slack_accounts"):
        problems["slack"] = "no Slack account is connected"
    if not sending_email_address(user):
        problems["email"] = (
            "no email account you can send from (add one with sending enabled; "
            "your login address alone can't send)"
        )
    return problems


def get_notification_channel(user_info: dict, preferred: str | None) -> tuple[str, str, dict[str, Any]] | None:
    """
    Determine which notification channel to use and the identifier for it.

    Priority order (unless overridden): Discord -> Slack -> Email

    Returns (channel_type, channel_identifier) or None if no channel available.
    """
    user = user_info.get("user", {})

    # Build available channels
    channels: list[tuple[str, str, dict[str, Any]]] = []  # (type, identifier, extra_data)

    # Discord
    discord_accounts = user.get("discord_accounts", {})
    discord_bots = user.get("discord_bots", [])
    if discord_accounts and discord_bots:
        discord_id = next(iter(discord_accounts.keys()), None)
        if discord_id:
            # Discord ids are int snowflakes in serialize(); coerce to str so the
            # identifier survives the celery JSON boundary as the str the
            # downstream .isdigit() classification expects (slack/email are
            # already strings here).
            channels.append(("discord", str(discord_id), {"discord_bot_id": discord_bots[0]}))

    # Slack
    slack_accounts = user.get("slack_accounts", {})
    if slack_accounts:
        slack_id = next(iter(slack_accounts.keys()), None)
        if slack_id:
            channels.append(("slack", slack_id, {}))

    # Email
    email_addr = sending_email_address(user)
    if email_addr:
        channels.append(("email", email_addr, {"from_address": email_addr}))

    if not channels:
        return None

    # If preferred channel specified, try to find it
    if preferred:
        for ch_type, ch_id, extra in channels:
            if ch_type == preferred:
                return (ch_type, ch_id, extra)
        # Preferred not available
        return None

    # Return first available (priority order)
    return channels[0]


def notify_user_now(
    session: DBSession, user: User, subject: str, message: str, details_url: str | None = None
) -> str | None:
    """Queue a notification to `user` on their preferred channel, as notify_user does.

    For code acting on a user's behalf without their token. Returns the channel
    type, or None when the user has no channel to notify on.
    """
    channel_info = get_notification_channel({"user": user_info_for(session, user)}, None)
    if channel_info is None:
        return None
    channel_type, channel_identifier, extra_data = channel_info
    celery_app.send_task(
        SEND_NOTIFICATION,
        args=[
            channel_type,
            channel_identifier,
            format_notification_message(subject, message, details_url),
            user.id,
            subject,
            extra_data,
        ],
    )
    return channel_type


def format_notification_message(subject: str, message: str, details_url: str | None) -> str:
    """Render the on-wire notification body (bold subject + optional link)."""
    full_message = f"**{subject}**\n\n{message}"
    if details_url:
        full_message += f"\n\n[View details]({details_url})"
    return full_message
