"""Merging duplicate Person records into one. Worker-safe: no API imports."""

import logging
from functools import reduce
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute

from memory.common.db.connection import DBSession
from memory.common.db.models import (
    BeeperParticipant,
    Deadline,
    GithubItem,
    Person,
    PersonTidbit,
    Project,
    Team,
    team_members,
)
from memory.common.db.models.discord import DiscordUser
from memory.common.db.models.polls import PollResponse
from memory.common.db.models.source_item import source_item_people
from memory.common.db.models.sources import GithubUser, transcript_account_task_people
from memory.common.people import contact_values

logger = logging.getLogger(__name__)


def append_new(entries: list, new: list) -> list:
    """Append the values not already present, comparing by == so dict entries work."""
    return reduce(lambda acc, v: acc if v in acc else [*acc, v], new, entries)


def combine_contact(primary: Any, secondary: Any) -> Any:
    """Combine two people's values for one contact key, keeping both; the primary's come first.

    Dicts combine per key; any other pair of differing values becomes a list
    holding both, whatever their shapes.
    """
    if secondary is None or secondary == primary:
        return primary
    if primary is None:
        return secondary
    if isinstance(primary, dict) and isinstance(secondary, dict):
        return {k: combine_contact(primary.get(k), secondary.get(k)) for k in primary | secondary}
    return append_new(contact_values(primary), contact_values(secondary))


def merge_aliases(primary: Person, secondaries: list[Person]) -> None:
    """Combine aliases from all people into the primary, deduped."""
    all_aliases = set(primary.aliases or [])
    for sec in secondaries:
        all_aliases.update(sec.aliases or [])
        all_aliases.add(sec.identifier)
        if sec.display_name and sec.display_name != primary.display_name:
            all_aliases.add(sec.display_name)
    all_aliases.discard(primary.identifier)
    all_aliases.discard(primary.display_name)
    primary.aliases = list(all_aliases)


def merge_contact_info(primary: Person, secondaries: list[Person]) -> None:
    """Combine everyone's contact_info into the primary without dropping any value."""
    primary.contact_info = reduce(
        combine_contact,
        [dict(sec.contact_info or {}) for sec in secondaries],
        dict(primary.contact_info or {}),
    )


# Each column here holds a plain reference to a Person; a merge points the
# secondaries' rows at the primary. The key is its count in the merge stats.
# team_members, source_item_people and transcript_account_task_people are link
# tables merged with dedup by merge_team_memberships, merge_source_items and
# merge_transcript_task_people.
REPOINTED_PERSON_COLUMNS: dict[str, InstrumentedAttribute[Any]] = {
    "tidbits_moved": PersonTidbit.person_id,
    "discord_users_moved": DiscordUser.person_id,
    "github_users_moved": GithubUser.person_id,
    "poll_responses_moved": PollResponse.person_id,
    "projects_moved": Project.owner_id,
    "teams_moved": Team.owner_id,
    "deadlines_moved": Deadline.owner_id,
    "beeper_participants_moved": BeeperParticipant.person_id,
    "github_items_moved": GithubItem.author_person_id,
}


def repoint_person_column(
    session: DBSession,
    column: InstrumentedAttribute[Any],
    primary: Person,
    secondaries: list[Person],
) -> int:
    """Point every row of ``column`` that references a secondary at the
    primary. Returns the number of rows changed."""
    secondary_ids = [sec.id for sec in secondaries]
    return (
        session.query(column.class_)
        .filter(column.in_(secondary_ids))
        .update({column: primary.id}, synchronize_session="fetch")
    )


ROLE_PRIORITY = {"admin": 3, "lead": 2, "member": 1}


def merge_team_memberships(session: DBSession, primary: Person, secondaries: list[Person]) -> int:
    """Move team memberships, keeping the highest role per team. Returns count moved."""
    total = 0
    for sec in secondaries:
        sec_memberships = session.execute(
            team_members.select().where(team_members.c.person_id == sec.id)
        ).fetchall()

        for membership in sec_memberships:
            team_id = membership.team_id
            sec_role = membership.role

            existing = session.execute(
                team_members.select().where(
                    team_members.c.team_id == team_id,
                    team_members.c.person_id == primary.id,
                )
            ).fetchone()

            if existing:
                if ROLE_PRIORITY.get(sec_role, 0) > ROLE_PRIORITY.get(existing.role, 0):
                    session.execute(
                        team_members.update()
                        .where(
                            team_members.c.team_id == team_id,
                            team_members.c.person_id == primary.id,
                        )
                        .values(role=sec_role)
                    )
                session.execute(
                    team_members.delete().where(
                        team_members.c.team_id == team_id,
                        team_members.c.person_id == sec.id,
                    )
                )
            else:
                session.execute(
                    team_members.update()
                    .where(
                        team_members.c.team_id == team_id,
                        team_members.c.person_id == sec.id,
                    )
                    .values(person_id=primary.id)
                )
            total += 1
    return total


def merge_source_items(session: DBSession, primary: Person, secondaries: list[Person]) -> int:
    """Move source_item associations, deduplicating. Returns count moved."""
    total = 0
    for sec in secondaries:
        sec_items = session.execute(
            source_item_people.select().where(source_item_people.c.person_id == sec.id)
        ).fetchall()

        for item in sec_items:
            source_item_id = item.source_item_id
            existing = session.execute(
                source_item_people.select().where(
                    source_item_people.c.source_item_id == source_item_id,
                    source_item_people.c.person_id == primary.id,
                )
            ).fetchone()

            if existing:
                session.execute(
                    source_item_people.delete().where(
                        source_item_people.c.source_item_id == source_item_id,
                        source_item_people.c.person_id == sec.id,
                    )
                )
            else:
                session.execute(
                    source_item_people.update()
                    .where(
                        source_item_people.c.source_item_id == source_item_id,
                        source_item_people.c.person_id == sec.id,
                    )
                    .values(person_id=primary.id)
                )
            total += 1
    return total


def merge_transcript_task_people(
    session: DBSession, primary: Person, secondaries: list[Person]
) -> int:
    """Move transcript accounts' action-item task picks from the secondaries to
    the primary, deduplicating. Returns the number of accounts whose picks moved.

    The delete cascade would drop them instead, and an account left with no
    picks makes a task of every action item."""
    picks = transcript_account_task_people
    secondary_ids = [sec.id for sec in secondaries]
    account_ids = set(
        session.scalars(
            select(picks.c.transcript_account_id).where(picks.c.person_id.in_(secondary_ids))
        )
    )
    if not account_ids:
        return 0
    already_picked = set(
        session.scalars(
            select(picks.c.transcript_account_id).where(
                picks.c.person_id == primary.id,
                picks.c.transcript_account_id.in_(account_ids),
            )
        )
    )
    new_picks = [
        {"transcript_account_id": account_id, "person_id": primary.id}
        for account_id in sorted(account_ids - already_picked)
    ]
    if new_picks:
        session.execute(picks.insert(), new_picks)
    session.execute(picks.delete().where(picks.c.person_id.in_(secondary_ids)))
    return len(account_ids)


def merged_user_id(session: DBSession, primary: Person, secondaries: list[Person]) -> int | None:
    """The User the merged Person will link to: the people's own links,
    primary's before the rest.

    Raises ValueError when that User already has a Person outside the merge,
    since a user is linked to at most one Person.
    """
    people = [primary, *secondaries]
    person_ids = [p.id for p in people]
    candidates = list(dict.fromkeys(p.user_id for p in people if p.user_id))
    if not candidates:
        return None

    chosen, *skipped = candidates
    for extra_uid in skipped:
        logger.warning(
            "Person %s has multiple linked users: keeping %s, skipping %s.",
            primary.identifier, chosen, extra_uid,
        )
    owner = (
        session.query(Person)
        .filter(Person.user_id == chosen, Person.id.notin_(person_ids))
        .first()
    )
    if owner is not None:
        raise ValueError(
            f"User {chosen} is already linked to '{owner.identifier}', which is not part "
            "of this merge. Include that person in the merge or unlink it first."
        )
    return chosen


def merge_user_links(
    session: DBSession, primary: Person, secondaries: list[Person], user_id: int | None
) -> int:
    """Link primary to ``user_id`` (from ``merged_user_id``) and unlink the
    secondaries. Returns 1 if primary's user_id changed, 0 otherwise."""
    if user_id is None:
        return 0
    for sec in secondaries:
        sec.user_id = None
    # The secondaries' links must be gone before primary takes one of them.
    session.flush()
    changed = primary.user_id != user_id
    primary.user_id = user_id
    return int(changed)


def merge_people(session: DBSession, primary: Person, secondaries: list[Person]) -> dict[str, int]:
    """Fold the secondaries into primary and delete them; the caller commits.

    Raises ValueError when the merged Person would take a User link that User
    already has elsewhere (see ``merged_user_id``). Returns the moved-row counts.
    """
    user_id = merged_user_id(session, primary, secondaries)
    merge_aliases(primary, secondaries)
    merge_contact_info(primary, secondaries)
    repointed = {
        key: repoint_person_column(session, column, primary, secondaries)
        for key, column in REPOINTED_PERSON_COLUMNS.items()
    }
    users_updated = merge_user_links(session, primary, secondaries, user_id)
    stats = {
        **repointed,
        "team_memberships_moved": merge_team_memberships(session, primary, secondaries),
        "source_items_moved": merge_source_items(session, primary, secondaries),
        "transcript_task_picks_moved": merge_transcript_task_people(session, primary, secondaries),
        "users_updated": users_updated,
    }
    for sec in secondaries:
        session.delete(sec)
    session.flush()
    return stats

