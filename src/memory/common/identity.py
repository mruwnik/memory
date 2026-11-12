"""Which Person a User is, from accounts the user proved they own.

A proof is a credential the account's service vouched for: the Slack user id
of the user's own Slack OAuth in a workspace an admin connected, the GitHub
user id their PAT belongs to, the Google-verified address of the Google
account they signed in with, or a Discord account they DMed a link code from.
Names, logins, self-typed addresses and GithubAccount.name prove nothing.

Only Slack, GitHub and Google proofs make a Person a candidate to link or
claim. A Discord link code can be phished (anyone can be asked to DM one), so
it only ever attaches the account to a Person the user already has.
Worker-safe: no API imports.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import exists, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased

from memory.common.db.connection import DBSession
from memory.common.db.models import (
    DiscordUser,
    GithubAccount,
    GoogleAccount,
    Person,
    PersonTidbit,
    SlackUserCredentials,
    SlackWorkspace,
    User,
    team_members,
)
from memory.common.db.models.source_item import source_item_people
from memory.common.db.models.sources import GithubUser
from memory.common.people import (
    contact_values,
    discord_ids,
    new_person_for_user,
    people_with_email,
    people_with_slack_id,
    slack_accounts,
)
from memory.common.people_merge import merge_people
from memory.common.scopes import SCOPE_ADMIN

logger = logging.getLogger(__name__)

Proof = tuple[str, str]  # (kind, value): ("slack", "T1:U1"), ("github", user id), ("email", address), ("discord", id)
CANDIDATE_KINDS = frozenset({"slack", "github", "email"})


@dataclass
class Suggestion:
    person: Person
    reasons: list[Proof] = field(default_factory=list)


def slack_value(workspace_id: str, slack_user_id: str) -> str:
    return f"{workspace_id}:{slack_user_id}"


def proofs(session: DBSession, user: User) -> list[Proof]:
    # Anyone can create a Slack workspace with users of any name and email, so
    # only a workspace an admin connected vouches for its user ids.
    admin_owner = aliased(User)
    slack = (
        session.query(SlackUserCredentials.workspace_id, SlackUserCredentials.slack_user_id)
        .join(SlackWorkspace, SlackWorkspace.id == SlackUserCredentials.workspace_id)
        .join(admin_owner, admin_owner.id == SlackWorkspace.owner_id)
        .filter(
            SlackUserCredentials.user_id == user.id,
            SlackUserCredentials.slack_user_id.is_not(None),
            admin_owner.scopes.any_() == SCOPE_ADMIN,
        )
    )
    # An App installation belongs to an org or account, not to the user.
    github = session.scalars(
        select(GithubAccount.verified_github_id).where(
            GithubAccount.user_id == user.id,
            GithubAccount.auth_type == "pat",
            GithubAccount.verified_github_id.is_not(None),
        )
    )
    google = session.scalars(
        select(GoogleAccount.email).where(GoogleAccount.user_id == user.id, GoogleAccount.email_verified)
    )
    discord = session.scalars(select(DiscordUser.id).where(DiscordUser.verified_user_id == user.id))
    found = [
        *(("slack", slack_value(ws, uid)) for ws, uid in slack),
        *(("github", str(github_id)) for github_id in github),
        *(("email", email.lower()) for email in google),
        *(("discord", str(i)) for i in discord),
    ]
    return list(dict.fromkeys(found))


def people_proven_by(session: DBSession, proof: Proof) -> list[Person]:
    kind, value = proof
    if kind == "slack":
        workspace_id, slack_user_id = value.split(":", 1)
        return people_with_slack_id(session, workspace_id, slack_user_id).all()
    if kind == "github":
        # By GitHub's id: a login can be renamed and then taken by someone else.
        return session.query(Person).join(GithubUser, GithubUser.person_id == Person.id).filter(
            GithubUser.id == int(value), GithubUser.linked_by_id
        ).all()
    if kind == "email":
        return people_with_email(session, value).all()
    raise ValueError(f"unknown proof kind {kind}")


def suggestions(session: DBSession, user: User) -> list[Suggestion]:
    """Persons the user's proofs point to, other than their own. A Person linked
    to another user is left out: it is someone else's, whatever the proof says.
    So is one a non-admin made: they could plant it carrying someone else's
    account to become that user's Person."""
    own_id = user.person.id if user.person else None
    found: dict[int, Suggestion] = {}
    for proof in proofs(session, user):
        if proof[0] not in CANDIDATE_KINDS:
            continue
        for person in people_proven_by(session, proof):
            if person.id == own_id or person.user_id is not None or not person.creator_trusted:
                continue
            found.setdefault(person.id, Suggestion(person)).reasons.append(proof)
    return sorted(found.values(), key=lambda s: s.person.id)


def is_bare_setup_person(session: DBSession, person: Person, user: User) -> bool:
    """Whether ``person`` is the empty Person account creation made for ``user``:
    nothing would be lost but its name if it were merged into another."""
    if person.creator_id != user.id:
        return False
    holds_anything = session.query(
        exists().where(PersonTidbit.person_id == person.id)
        | exists().where(source_item_people.c.person_id == person.id)
        | exists().where(team_members.c.person_id == person.id)
    ).scalar()
    return not holds_anything


def link_proven_person(session: DBSession, user: User) -> list[Suggestion]:
    """Link ``user`` to the one Person their proofs point to, when that is safe,
    and return the Persons left for them to claim. The caller commits.

    Safe means the user has no Person yet, or theirs is the bare one account
    creation made, which is then merged into the proven one (which keeps its
    id). Anything else would merge two real Persons, which is the user's call.
    """
    found = suggestions(session, user)
    if len(found) != 1:
        return found
    candidate = found[0].person
    own = user.person
    if own is None:
        candidate.user_id = user.id
    elif is_bare_setup_person(session, own, user):
        merge_people(session, candidate, [own])
    else:
        return found
    session.flush()
    session.expire(user, ["person"])
    logger.info("Linked user_id=%s to person_id=%s from a proven account", user.id, candidate.id)
    return []


def claim_person(session: DBSession, user: User, person_id: int) -> Person:
    """Make the Person ``person_id`` the user's: link it, or merge it into the
    user's own Person. Only a Person their proofs point to can be claimed.
    The caller commits.

    Raises PermissionError when no proof ties the Person to the user.
    """
    # Locked first, so a concurrent claim or link can't take it between the check and the merge.
    locked = session.query(Person).filter(Person.id == person_id).with_for_update().one_or_none()
    claimable = {s.person.id: s.person for s in suggestions(session, user)}
    claimed = claimable.get(person_id)
    if locked is None or claimed is None:
        raise PermissionError("None of your verified accounts belongs to that person")
    own = user.person
    if own is None:
        claimed.user_id = user.id
        session.flush()
        return claimed
    merge_people(session, own, [claimed])
    session.expire(user, ["person"])
    logger.info("user_id=%s claimed person_id=%s into person_id=%s", user.id, person_id, own.id)
    return own


def link_discord_user(session: DBSession, user: User, discord_user_id: int) -> Person | None:
    """Record that ``user`` proved they own this Discord account by DMing a
    link code from it, and attach it to their Person (a new, bare one if they
    have none). Returns that Person. The caller commits.

    Only an account on no Person, or already on the user's, is attached. One
    on any other Person stays there: a code can be phished, so it must never
    hand the user someone else's Person. None then, and when the account is
    unknown. An account on a Person of no user is still marked verified for
    the user, which only lists it among their accounts.
    """
    discord_user = session.get(DiscordUser, discord_user_id)
    if discord_user is None:
        return None
    held = discord_user.person
    own = user.person
    if held is not None and (own is None or held.id != own.id):
        if held.user_id is None:
            discord_user.verified_user_id = user.id
            session.flush()
        return None
    person = own or new_person_for_user(session, user)
    discord_user.person_id = person.id
    discord_user.verified_user_id = user.id
    session.flush()
    session.expire(user, ["person"])
    return person


def link_all_users(session: DBSession) -> dict[str, int]:
    """Run ``link_proven_person`` for every user with a proof that can point
    at a Person, committing each. Returns how many users have such proofs and
    how many suggestions remain."""
    owners = (
        select(SlackUserCredentials.user_id)
        .where(SlackUserCredentials.slack_user_id.is_not(None))
        .union(
            select(GithubAccount.user_id).where(
                GithubAccount.auth_type == "pat", GithubAccount.verified_github_id.is_not(None)
            ),
            select(GoogleAccount.user_id).where(GoogleAccount.email_verified),
        )
        .subquery()
    )
    user_ids = session.scalars(select(owners.c.user_id).order_by(owners.c.user_id)).all()
    users = 0
    remaining = 0
    for user_id in user_ids:
        user = session.get(User, user_id)
        if user is None or not any(kind in CANDIDATE_KINDS for kind, _ in proofs(session, user)):
            continue
        users += 1
        try:
            remaining += len(link_proven_person(session, user))
            session.commit()
        except (SQLAlchemyError, ValueError):
            session.rollback()
            logger.exception("Linking a proven person failed for user_id=%s", user_id)
    return {"users": users, "suggestions": remaining}


@dataclass
class Account:
    kind: str
    id: str
    label: str
    verified: bool
    removable: bool
    # Whether the user's Person has it; a verified Discord account may sit on another Person.
    on_person: bool


def slack_label(contact_info: dict, workspace_id: str, slack_user_id: str) -> str:
    for workspaces in contact_values(contact_info.get("slack")):
        info = workspaces.get(workspace_id) if isinstance(workspaces, dict) else None
        if isinstance(info, dict) and slack_user_id in contact_values(info.get("user_id")):
            return info.get("display_name") or info.get("username") or slack_user_id
    return slack_user_id


def person_accounts(person: Person) -> dict[Proof, str]:
    """The accounts the Person lists or is linked to, as (kind, id) -> label."""
    info = person.contact_info or {}
    found: dict[Proof, str] = {}
    for discord_user in person.discord_accounts:
        found.setdefault(("discord", str(discord_user.id)), discord_user.name)
    for discord_id in discord_ids(info):
        found.setdefault(("discord", discord_id), discord_id)
    for workspace_id, slack_user_id in slack_accounts(info):
        found.setdefault(
            ("slack", slack_value(workspace_id, slack_user_id)),
            slack_label(info, workspace_id, slack_user_id),
        )
    linked_logins = set()
    for github_user in person.github_accounts:
        found.setdefault(("github", str(github_user.id)), github_user.username)
        linked_logins.add(github_user.username)
    for login in contact_values(info.get("github")):
        if str(login) not in linked_logins:
            found.setdefault(("github", str(login)), str(login))
    for email in contact_values(info.get("email")):
        found.setdefault(("email", str(email).lower()), str(email))
    return found


def accounts(session: DBSession, user: User) -> list[Account]:
    """The user's accounts: those they proved, then those only their Person lists.

    Only the Person's own entries are removable: a proof stands as long as its
    credential does. A proven Discord account is the exception, since the link
    is its proof.
    """
    person = user.person
    listed = person_accounts(person) if person else {}
    proven = proofs(session, user)
    discord_names = {
        str(d.id): d.name
        for d in session.query(DiscordUser).filter(DiscordUser.verified_user_id == user.id)
    }
    github_logins = {
        str(github_id): login
        for github_id, login in session.query(GithubAccount.verified_github_id, GithubAccount.verified_login).filter(
            GithubAccount.user_id == user.id, GithubAccount.verified_github_id.is_not(None)
        )
    }
    contact_info = (person.contact_info or {}) if person else {}

    def proof_label(kind: str, value: str) -> str:
        if kind == "discord":
            return discord_names.get(value, value)
        if kind == "github":
            return github_logins.get(value) or value
        if kind == "slack":
            return slack_label(contact_info, *value.split(":", 1))
        return value

    verified = [
        Account(
            kind,
            value,
            listed.get((kind, value)) or proof_label(kind, value),
            verified=True,
            # The link is a Discord account's only proof, so the user can undo it.
            removable=kind == "discord",
            on_person=(kind, value) in listed,
        )
        for kind, value in proven
    ]
    unverified = [
        Account(kind, value, label, verified=False, removable=True, on_person=True)
        for (kind, value), label in listed.items()
        if (kind, value) not in proven
    ]
    return verified + unverified


class CredentialAccount(Exception):
    """The account is proven by a credential, which unlinking can't remove."""


def without_values(contact_info: dict, key: str, drop: Callable[[Any], bool]) -> dict:
    """contact_info with the values of ``key`` that ``drop`` matches removed."""
    kept = [v for v in contact_values(contact_info.get(key)) if not drop(v)]
    rest = {k: v for k, v in contact_info.items() if k != key}
    if not kept:
        return rest
    return {**rest, key: kept if len(kept) > 1 else kept[0]}


def without_slack_workspace(workspaces: Any, workspace_id: str, slack_user_id: str) -> Any:
    if not isinstance(workspaces, dict):
        return workspaces
    info = workspaces.get(workspace_id)
    if not isinstance(info, dict) or slack_user_id not in contact_values(info.get("user_id")):
        return workspaces
    return {ws: v for ws, v in workspaces.items() if ws != workspace_id}


def unlink_discord(session: DBSession, user: User, account_id: str) -> None:
    """Take a Discord account off the user's Person and undo their verification
    of it, wherever the account is. Raises LookupError when it is neither."""
    is_id = account_id.isdecimal() and int(account_id) < 2**63
    discord_user = session.get(DiscordUser, int(account_id)) if is_id else None
    person = user.person
    verified = discord_user is not None and discord_user.verified_user_id == user.id
    listed = person is not None and ("discord", account_id) in person_accounts(person)
    if not (verified or listed):
        raise LookupError("discord")
    if discord_user is not None and verified:
        discord_user.verified_user_id = None
    if person is not None and listed:
        if discord_user is not None and discord_user.person_id == person.id:
            discord_user.person_id = None
        person.contact_info = without_values(
            dict(person.contact_info or {}), "discord", lambda v: str(v).strip() == account_id
        )
        session.flush()
        session.expire(person, ["discord_accounts"])
    session.flush()
    logger.info("user_id=%s unlinked a discord account", user.id)


def unlink_account(session: DBSession, user: User, kind: str, account_id: str) -> None:
    """Remove an account from the user's Person (a Discord one also loses the
    user's verification). The caller commits.

    Raises CredentialAccount for an account a credential proves, and
    LookupError when the Person doesn't have it.
    """
    if kind == "discord":
        unlink_discord(session, user, account_id)
        return
    if (kind, account_id) in proofs(session, user):
        raise CredentialAccount(kind)
    person = user.person
    if person is None or (kind, account_id) not in person_accounts(person):
        raise LookupError(kind)

    info = dict(person.contact_info or {})
    if kind == "slack":
        workspace_id, slack_user_id = account_id.split(":", 1)
        stripped = [
            without_slack_workspace(w, workspace_id, slack_user_id)
            for w in contact_values(info.get("slack"))
        ]
        person.contact_info = without_values({**info, "slack": stripped}, "slack", lambda w: not w)
    elif kind == "github":
        unlinked = {account_id}
        for github_user in person.github_accounts:
            if str(github_user.id) == account_id:
                github_user.person_id = None
                unlinked.add(github_user.username)
        person.contact_info = without_values(info, "github", lambda v: str(v) in unlinked)
    elif kind == "email":
        person.contact_info = without_values(info, "email", lambda v: str(v).lower() == account_id)
    session.flush()
    session.expire(person, ["discord_accounts", "github_accounts"])
    logger.info("user_id=%s unlinked a %s account from person_id=%s", user.id, kind, person.id)
