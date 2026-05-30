"""Resolve and validate scheduled-notification targets at upsert time.

A notification target may be given as a concrete platform id/address or as a
person reference (name / identifier / email). This module turns whatever the
caller supplied into the concrete id/address that gets stored, raising a
caller-facing ``ValueError`` if it can't be resolved or the caller may not use
it.

Delivery method (DM vs channel) is not stored — the worker derives it from the
id at dispatch time using the same classifiers this module validates against
(``discord_channel_for_target`` / Slack id prefixes in ``memory.common``), so
validation and delivery can't drift.
"""

from sqlalchemy.orm import Session as DBSession

from memory.common import people as people_mod
from memory.common.db.models.slack import SlackChannel, SlackUserCredentials
from memory.common.db.models.sources import Person
from memory.common.db.models.users import User
from memory.common.discord_data import (
    caller_can_see_discord_channel,
    caller_can_see_discord_user,
    discord_channel_for_target,
)
from memory.common.slack import (
    SLACK_CHANNEL_PREFIXES,
    SLACK_USER_PREFIXES,
    user_has_workspace_membership,
)


def resolve_and_validate_target(
    session: DBSession, user_id: int, channel: str, target: str | None
) -> str:
    """Resolve ``target`` to a concrete id/address for ``channel``.

    Raises ValueError (with a message safe to show the caller) when the target
    is empty, malformed, references an unknown person, or names an entity the
    caller may not address.
    """
    value = (target or "").strip()
    if not value:
        raise ValueError("notification_target is required")

    user = session.get(User, user_id)
    if user is None:
        raise ValueError("User not found")

    # One person lookup for all channels; the helpers branch on the result.
    person = people_mod.find_person(session, value)

    if channel == "email":
        return resolve_email_target(value, person)
    if channel == "discord":
        return resolve_discord_target(session, user, value, person)
    if channel == "slack":
        return resolve_slack_target(session, user, value, person)
    raise ValueError(f"Unknown notification_channel '{channel}'")


def resolve_email_target(target: str, person: Person | None) -> str:
    # Email syntax is famously hard to validate; anything containing "@" is
    # treated as a literal address. Otherwise the input is a person reference.
    if "@" in target:
        return target
    if person is None:
        raise ValueError(f"No email address found for '{target}'")
    email = (person.contact_info or {}).get("email")
    if not email:
        raise ValueError(f"No email address on file for '{person.display_name}'")
    return email


def resolve_discord_target(
    session: DBSession, user: User, target: str, person: Person | None
) -> str:
    if person is not None:
        accounts = person.discord_accounts
        if not accounts:
            raise ValueError(f"'{person.display_name}' has no linked Discord account")
        discord_id = accounts[0].id
        # Same visibility gate as the raw-id path: resolving via a person name
        # must not let a caller DM a Discord user their bots have never seen.
        if not caller_can_see_discord_user(session, user, discord_id):
            raise ValueError(
                f"'{person.display_name}'s Discord account is not accessible to you"
            )
        return str(discord_id)

    if not target.isdigit():
        raise ValueError(
            f"Discord target '{target}' is not a known person, channel, or user id"
        )

    channel = discord_channel_for_target(session, target)
    if channel is not None:
        if caller_can_see_discord_channel(session, user, channel):
            return target
        raise ValueError(f"Discord channel '{target}' not found or not accessible")
    if caller_can_see_discord_user(session, user, int(target)):
        return target
    raise ValueError(f"Discord channel/user '{target}' not found or not accessible")


def resolve_slack_target(
    session: DBSession, user: User, target: str, person: Person | None
) -> str:
    if person is not None:
        return resolve_person_slack_id(session, user, person)

    prefix = target[:1].upper()
    if prefix in SLACK_USER_PREFIXES:
        # No SlackUser table to verify against; require the caller to have at
        # least one Slack workspace so a stray id can't be scheduled by someone
        # with no Slack access at all. Delivery correctness is enforced at send.
        if not user_has_any_slack_workspace(session, user):
            raise ValueError("You have no connected Slack workspace")
        return target
    if prefix in SLACK_CHANNEL_PREFIXES:
        channel = session.get(SlackChannel, target)
        if channel is None or not user_has_workspace_membership(
            session, channel.workspace_id, user
        ):
            raise ValueError(f"Slack channel '{target}' not found or not accessible")
        return target
    raise ValueError(
        f"Slack target '{target}' is not a known person, channel, or user id"
    )


def resolve_person_slack_id(session: DBSession, user: User, person: Person) -> str:
    """Resolve a person's Slack user id within a workspace the caller shares.

    DM resolution requires shared-workspace visibility — we never return a Slack
    id from a workspace the caller isn't a member of (it both leaks a target the
    caller shouldn't reach and wouldn't deliver under the caller's own token).
    """
    slack_info = (person.contact_info or {}).get("slack") or {}
    if not slack_info:
        raise ValueError(f"'{person.display_name}' has no linked Slack account")

    for workspace_id, info in slack_info.items():
        if user_has_workspace_membership(session, workspace_id, user):
            user_id = (info or {}).get("user_id")
            if user_id:
                return user_id

    raise ValueError(
        f"'{person.display_name}' has no Slack account in a workspace you share"
    )


def user_has_any_slack_workspace(session: DBSession, user: User) -> bool:
    return (
        session.query(SlackUserCredentials.id)
        .filter(SlackUserCredentials.user_id == user.id)
        .first()
        is not None
    )
