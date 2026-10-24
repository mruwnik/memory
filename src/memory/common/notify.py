"""Notify a user on their preferred channel. Worker-safe: no API imports."""

import logging
from typing import Any

from memory.common.celery_app import SEND_NOTIFICATION
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import DBSession
from memory.common.db.models import EmailAccount, GithubAccount, SlackUserCredentials, User
from memory.common.people import contact_values, slack_accounts

logger = logging.getLogger(__name__)


def user_info_for(session: DBSession, user: User) -> dict:
    """A user's profile plus the accounts notifications can be delivered to."""
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

    # Enrich from linked Person record (via User.person or discord_accounts)
    person = user.person
    if not person:
        for discord_acct in user.discord_accounts:
            if not discord_acct.person:
                continue
            if not person:
                person = discord_acct.person
            elif discord_acct.person.id != person.id:
                logger.warning(
                    "User %s has discord accounts linked to multiple Person records; using first found",
                    user.id,
                )
                break

    if person:
        # Add Discord from Person if not already on User
        if not user_info.get("discord_accounts") and person.discord_accounts:
            user_info["discord_accounts"] = {
                # str keys to match User.serialize() and the JSON-RPC wire shape.
                str(acct.id): acct.username for acct in person.discord_accounts
            }

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
            }
        ]

    return user_info


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
    email_accounts = user.get("email_accounts", [])
    if email_accounts:
        email_addr = email_accounts[0].get("email_address")
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
