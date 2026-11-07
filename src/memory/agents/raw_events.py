"""Raw-event subscriptions: webhook deliveries (memory.common.db.models.events,
kind "raw") handed to a queue.

Only the owner of the account a raw event came in on may subscribe to it. Its
payload can still be about a channel or repo filed in a project that owner
can't read, so a delivery shows the payload only to credentials that could read
the items stored from there (container_of). Deliveries for channels or repos
Memory doesn't collect, and rejected ones, are never delivered.

Routed on the maintenance queue, so this module must stay importable by the
standard worker image.
"""

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from memory.common.access_control import (
    UserProxy,
    credential_scopes,
    get_user_project_roles,
    user_can_access,
)
from memory.common.db.connection import DBSession
from memory.common.db.models import (
    APIKey,
    DiscordBot,
    DiscordChannel,
    Event,
    GithubAccount,
    GithubRepo,
    Project,
    SlackApp,
    SlackChannel,
    TranscriptAccount,
    User,
)
from memory.common.db.models.events import NO_ITEM_TYPE, STORED
from memory.common.scopes import SCOPE_ADMIN, SCOPE_READ

# The outcomes a subscription may receive.
ROUTABLE = (STORED, NO_ITEM_TYPE)
Outcome = Literal["stored", "no_item_type"]
# Each source's account table and the column naming its owner.
ACCOUNTS = {
    "github": (GithubAccount, GithubAccount.user_id),
    "slack": (SlackApp, SlackApp.created_by_user_id),
    "discord": (DiscordBot, DiscordBot.created_by_user_id),
    "transcripts": (TranscriptAccount, TranscriptAccount.user_id),
}
MAX_TYPES = 50
# Waiting raw deliveries a subscription keeps without a backlog_max of its own.
DEFAULT_RAW_BACKLOG = 1000


class RawConfig(BaseModel):
    """A raw subscription's source: one source's deliveries to the owner's accounts."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["github", "slack", "discord", "transcripts"]
    # One account; else every account of the source the owner has.
    account_id: int | None = None
    # Event types, as "event" (any of its actions) or "event.action"; empty means all.
    types: Annotated[list[str], Field(max_length=MAX_TYPES)] = []
    outcome: Annotated[list[Outcome], Field(min_length=1)] = list(ROUTABLE)


def account_owner(db: DBSession, source: str, account_id: int) -> int | None:
    model, owner = ACCOUNTS[source]
    return db.query(owner).filter(model.id == account_id).scalar()


def type_matches(types: list[str], event_type: str) -> bool:
    return not types or any(event_type == t or event_type.startswith(f"{t}.") for t in types)


def raw_matches(config: RawConfig, owner_id: int, event: Event) -> bool:
    """Whether the raw event is one `owner_id`'s subscription with `config` receives."""
    return (
        event.outcome in ROUTABLE
        and event.owner_id == owner_id
        and event.source == config.source
        and config.account_id in (None, event.account_id)
        and event.outcome in config.outcome
        and type_matches(config.types, event.type or "")
    )


@dataclass(frozen=True)
class Container:
    """The channel, repo or account a raw event is about, as its items are filed."""

    collected: bool
    project_id: int | None = None
    sensitivity: str = "basic"


def chain_access(chain: list[Any]) -> tuple[int | None, str]:
    """project_id and sensitivity as SourceItem.resolve_access_control reads them off
    a data-source chain, each from the first level that sets it."""
    project = next((s.project_id for s in chain if getattr(s, "project_id", None) is not None), None)
    sensitivity = next((s.sensitivity for s in chain if getattr(s, "sensitivity", None)), "basic")
    return project, sensitivity


def snowflake(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def milestone_number(payload: Any) -> int | None:
    """The milestone of the issue or PR a GitHub delivery is about, if it names one."""
    if not isinstance(payload, dict):
        return None
    for key in ("issue", "pull_request"):
        subject = payload.get(key)
        milestone = subject.get("milestone") if isinstance(subject, dict) else None
        number = milestone.get("number") if isinstance(milestone, dict) else None
        if isinstance(number, int) and not isinstance(number, bool):
            return number
    return None


def container_of(db: DBSession, event: Event) -> Container | None:
    """Where the event's items are filed, and whether Memory collects it; None when
    the event names nothing Memory has stored, whose access is unknown."""
    if event.source == "transcripts":
        account = db.get(TranscriptAccount, event.account_id)
        return None if account is None else Container(account.active, *chain_access([account]))
    if event.source == "github":
        repo_id = snowflake(event.container)
        repo = db.scalars(
            select(GithubRepo).where(GithubRepo.account_id == event.account_id, GithubRepo.github_id == repo_id)
        ).first()
        if repo is None:
            return None if repo_id is None else Container(False)
        account = db.get(GithubAccount, repo.account_id)
        # As memory.workers.tasks.github files an issue or PR: under its milestone's
        # project, else the repo's.
        number = milestone_number(event.payload)
        filed = Project.number.is_(None) if number is None else Project.number == number
        project = db.scalar(select(Project.id).where(Project.repo_id == repo.id, filed))
        if number is not None and project is None:
            return None
        return Container(repo.active and bool(account and account.active), project)
    if event.source == "slack" and event.container:
        channel = db.get(SlackChannel, event.container)
        chain = [channel, channel.workspace] if channel else []
    elif event.source == "discord" and (channel_id := snowflake(event.container)):
        channel = db.get(DiscordChannel, channel_id)
        chain = [channel, channel.server] if channel else []
    else:
        return None
    if channel is None:
        return None
    return Container(bool(channel.should_collect), *chain_access([c for c in chain if c is not None]))


def reads_container(db: DBSession, owner: User, key: APIKey | None, container: Container) -> bool:
    """Whether `owner` through `key` could read items filed as `container` is."""
    scopes = credential_scopes(owner, key)
    if not {SCOPE_READ, SCOPE_ADMIN} & set(scopes):
        return False
    reader = UserProxy.from_user(owner, scopes=scopes)
    return user_can_access(reader, container, get_user_project_roles(db, reader))
