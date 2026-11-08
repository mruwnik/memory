"""Ingest subscriptions: deterministic filters over newly stored items.

Matching runs on the maintenance queue, so this module must stay importable
by the standard worker image: no fastapi/fastmcp/jsonschema, so nothing from
memory.agents.spec or memory.agents.enqueue. Firing (which calls enqueue_run)
happens on the agents queue, in memory.agents.fire.
"""

import email.utils
import hashlib
import itertools
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from functools import cached_property
from typing import Annotated, Any, Collection, Iterable, Literal, cast

import regex
from celery.exceptions import SoftTimeLimitExceeded
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import and_, case, exists, func, or_, select, update
from sqlalchemy.orm import aliased

from memory.common import settings
from memory.agents.caps import RateExceeded, check_hourly_rate
from memory.agents.principal import PrincipalRevoked, standing_key
from memory.agents.raw_events import DEFAULT_RAW_BACKLOG, RawConfig, raw_matches
from memory.common.access_control import (
    UserProxy,
    apply_access_filter_to_query,
    build_access_filter,
    credential_scopes,
    get_user_project_roles,
    has_admin_scope,
)
from memory.common.celery_app import FIRE_SUBSCRIPTION, app
from memory.common.collections import SESSION_COLLECTIONS
from memory.common.notify import notify_user_now
from memory.common.scopes import SCOPE_ADMIN, SCOPE_AGENTS, SCOPE_READ
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    AgentRun,
    Subscription,
    APIKey,
    Delivery,
    BeeperMessage,
    BeeperParticipant,
    BlogPost,
    CalendarEvent,
    Comic,
    DiscordBot,
    DiscordMessage,
    DiscordUser,
    Event,
    ForumComment,
    ForumPost,
    GithubItem,
    MailMessage,
    Person,
    SlackApp,
    SlackMessage,
    SourceItem,
    User,
)
from memory.common.db.models.events import ADDED, ITEM, LINKS_ITEMS, RAW, REMOVED, STORED, TIME_KINDS
from memory.common.db.models.agents import EXECUTOR_WORKERS
from memory.common.db.models.slack import SlackUserCredentials
from memory.common.db.models.source_items import DIRECT_CHANNEL_TYPES
from memory.common.people import people_with_slack_id

logger = logging.getLogger(__name__)

INGEST = "ingest"
MAX_FILTER_VALUES = 50
MAX_REGEX_CHARS = 500
# A regex is matched against at most this much of an item, under a time limit,
# because a pathological pattern on a long text can backtrack for minutes.
MAX_MATCH_TEXT_CHARS = 20_000
REGEX_TIMEOUT_SEC = 0.1
MAX_TEMPLATE_CHARS = 20_000
ITEMS_PLACEHOLDER = "{items}"
DEFAULT_TEMPLATE = f"New items matched this subscription:\n\n{ITEMS_PLACEHOLDER}"
# Reprocessing that would re-match an item (re-syncs, upstream edits) mostly
# happens soon after ingest, so claims older than this are purged; items older
# than this never match as new ones, so the purge can't let one fire again.
DELIVERY_RETENTION = timedelta(days=30)
ROUTE_BATCH_SIZE = 100
# A run takes no more batches after this; the next beat carries on.
ROUTE_RUN_SECONDS = 30
# The part of a run routing pending events may use before replays get the rest.
ROUTE_PENDING_SHARE = 2 / 3
ROUTER_LOCK_KEY = 0x6576656E7473  # "events"
SWEEP_LOCK_KEY = 0x64656C6976657279  # "delivery"
# Every delivery ends in one of these; skipped:<reason> says why nothing ran.
PENDING, DONE, FAILED, EXPIRED = "pending", "done", "failed", "expired"
SKIPPED_INACCESSIBLE = "skipped:inaccessible"
SKIPPED_DISABLED = "skipped:disabled"
SKIPPED_POSTED = "skipped:posted"
SHED_RATE = "skipped:shed:rate"
SHED_BUDGET = "skipped:shed:budget"
SHED_BACKLOG = "skipped:shed:backlog"
SKIPPED_COALESCED = "skipped:coalesced"
# A delivery still pending this long after it was sent is sent again, and each
# resend waits twice as long as the one before, so a backlogged agents queue
# doesn't fill with copies of what it already holds.
DISPATCH_GRACE = timedelta(minutes=2)
MAX_RESEND_DOUBLINGS = 12
# Deliveries a cap stops wait this long; any it stops again are shed.
THROTTLE_WINDOW = timedelta(hours=1)
# An episode ends after this long with nothing held back: longer than a deferral,
# so the shedding of what one burst deferred is the same episode.
THROTTLE_EPISODE = 2 * THROTTLE_WINDOW
# Replays overlap the trip and the resume by this much: a router batch can be in
# flight at either end, and version dedupe makes the overlap harmless.
REPLAY_MARGIN = timedelta(minutes=1)
# Deliveries pending this long (their subscription disabled, or firing down) end.
DELIVERY_MAX_AGE = timedelta(days=7)
# A stored raw delivery waits at most this long for the item events it causes.
CAUSED_GRACE = timedelta(minutes=2)
# subscription.state keys.
THROTTLED, RESUME = "throttled", "resume"

FilterValues = Annotated[list[str], Field(max_length=MAX_FILTER_VALUES)]
# What changed about an item, as ingesters report it in its event.
Change = Literal[
    "new",
    "edited",
    "commits",
    "comments",
    "review",
    "checks",
    "checks_complete",
    "state",
    "labels",
    "assignees",
    "draft",
    # Meeting action items (which share "assignees" and "state").
    "title",
    "due",
    "priority",
]
# Changes that are news about an item: they make one the subscription never saw match.
NEWS: frozenset[Change] = frozenset({"commits", "comments", "review", "checks_complete", "state"})
# Discord <@123> / <@!123>, Slack <@U123> / <@U123|name>.
MENTION = re.compile(r"<@!?(\w+)(?:\|[^>]*)?>")


def source_types() -> list[str]:
    """Every SourceItem subtype a filter can name (sessions are owner-private)."""
    return sorted(
        identity
        for identity, mapper in SourceItem.__mapper__.polymorphic_map.items()
        if identity != "source_item"
        and mapper.class_.__name__ != "SessionSegment"
    )


class IngestFilter(BaseModel):
    """All set criteria must hold; within a list, any value matches."""

    model_config = ConfigDict(extra="forbid")

    source_types: FilterValues = []
    tags_any: FilterValues = []
    # Discord channel snowflakes or Slack channel ids; a channel's threads match too.
    channel_ids: FilterValues = []
    # Also excludes the threads of these channels.
    exclude_channel_ids: FilterValues = []
    # Case-insensitive substrings of the item's title or text.
    text_contains: FilterValues = []
    regex: str | None = Field(default=None, max_length=MAX_REGEX_CHARS)
    # Author ids, usernames or email addresses (case-insensitive, exact).
    authors_any: FilterValues = []
    exclude_authors: FilterValues = []
    # Anyone can make a Person, so only ones ingest, an admin or the owner made
    # vouch for an author.
    known_people_only: bool = False
    # Who made a change, named as authors are.
    changed_by_any: FilterValues = []
    exclude_changed_by: FilterValues = []
    # The owner's own Slack and Discord messages. None means on, except for queue handlers:
    # for a bot the owner talks to, the owner's messages are its input.
    exclude_own: bool | None = None
    # GitHub "owner/name" (case-insensitive).
    repos: FilterValues = []
    # Discord/Slack messages that @-mention one of the owner's bots.
    mentions_me: bool = False
    # Discord/Slack thread messages where the owner or one of their bots posted.
    threads_im_in: bool = False
    # Discord/Slack direct messages that reached the owner's bots or Slack apps.
    dms_to_me: bool = False

    @field_validator("channel_ids", "exclude_channel_ids")
    @classmethod
    def stripped(cls, value: list[str]) -> list[str]:
        # Ids match exactly, and a padded exclusion would silently exclude nothing.
        return [v.strip() for v in value]

    @field_validator("source_types")
    @classmethod
    def known_source_types(cls, value: list[str]) -> list[str]:
        known = set(source_types())
        unknown = sorted(set(value) - known)
        if unknown:
            raise ValueError(
                f"unknown source types {unknown}; choose from {sorted(known)}"
            )
        return value

    @field_validator("regex")
    @classmethod
    def compiles(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            # The engine regex_search matches with, whose syntax differs from re's.
            regex.compile(value)
        except regex.error as e:
            raise ValueError(f"regex does not compile: {e}") from e
        return value

    @model_validator(mode="after")
    def narrows_something(self) -> "IngestFilter":
        # A filter that matches every ingested item would start a run per item.
        if not (
            self.source_types
            or self.tags_any
            or self.channel_ids
            or self.text_contains
            or self.regex
            or self.repos
            or self.mentions_me
            or self.authors_any
            or self.changed_by_any
            or self.threads_im_in
            or self.dms_to_me
        ):
            raise ValueError(
                "set at least one of source_types, tags_any, channel_ids, text_contains, "
                "regex, repos, mentions_me, authors_any, changed_by_any, threads_im_in or dms_to_me"
            )
        return self


class PerItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["per_item"] = "per_item"


class Batch(BaseModel):
    """Collect matches; fire when the oldest is window_sec old or max_items are waiting."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["batch"] = "batch"
    window_sec: int = Field(default=3600, ge=60, le=86_400)
    max_items: int = Field(default=50, ge=1, le=200)


class IngestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filter: IngestFilter
    delivery: Annotated[PerItem | Batch, Field(discriminator="mode")] = PerItem()
    # The run's input; placeholders are filled by memory.agents.subscription_input.
    template: str = Field(
        default=DEFAULT_TEMPLATE, min_length=1, max_length=MAX_TEMPLATE_CHARS
    )
    # Not part of the filter: it decides which events count, not which items.
    on_edit: bool = False
    # Edits fire only when they report one of these changes; empty means any edit.
    on_changes: list[Change] = []
    # A removed item is delivered, tagged "removed", if it was delivered before.
    on_remove: bool = False

    @model_validator(mode="after")
    def changes_need_edits(self) -> "IngestConfig":
        if self.on_changes and not self.on_edit:
            raise ValueError("on_changes needs on_edit: only edits are filtered by change")
        return self

    def reacts_to(self, changes: list[str] | None) -> bool:
        """Whether reported `changes` are news about an item this subscription never saw."""
        news = {c for c in changes or [] if c in NEWS}
        wanted = set(self.on_changes)
        return bool(news) and (not wanted or bool(wanted & news))

    def refires(self, changes: list[str] | None) -> bool:
        """Whether an edit reporting `changes` fires again; an unreported edit is "edited"."""
        wanted = set(self.on_changes)
        return self.on_edit and (not wanted or bool(wanted & set(changes or ["edited"])))


@dataclass(frozen=True)
class ItemFacts:
    source_type: str
    tags: frozenset[str]
    channel_id: str | None
    # Lowercased author ids, usernames and addresses.
    authors: frozenset[str]
    text: str
    # The lowercased authors the platform vouches for: user ids and GitHub logins,
    # never a display name or a From header, which anyone can set.
    author_ids: frozenset[str] = frozenset()
    # A Discord thread's parent; Slack thread replies carry their channel's id.
    parent_channel_id: str | None = None
    # Lowercased GitHub "owner/name".
    repo: str | None = None
    # Lowercased ids the message @-mentions, as its raw content spells them.
    mentions: frozenset[str] = frozenset()
    # A Discord/Slack direct message.
    direct: bool = False
    # What collected the message: "slack_app:<row id>" or "discord_bot:<application id>".
    collector: str | None = None


@dataclass(frozen=True)
class Me:
    """Who a subscription's owner is in chat."""

    # Their bots, which mentions_me looks for.
    bots: frozenset[str] = frozenset()
    # Their Slack users and the Discord accounts linked to them, which exclude_own leaves out.
    users: frozenset[str] = frozenset()
    # Them in a thread: their bots and users.
    accounts: frozenset[str] = frozenset()
    # Their Slack apps and Discord bots, as ItemFacts.collector names them.
    collectors: frozenset[str] = frozenset()


def lowered(values: Iterable[Any]) -> frozenset[str]:
    return frozenset(str(v).strip().lower() for v in values if v not in (None, ""))


def mail_authors(sender: str | None) -> list[str]:
    if not sender:
        return []
    return [sender, email.utils.parseaddr(sender)[1]]


def channel_and_authors(item: SourceItem) -> tuple[str | None, list[Any]]:
    if isinstance(item, DiscordMessage):
        user = item.author
        names = [user.username, user.display_name] if user else []
        return str(item.channel_id), [item.author_id, *names]
    if isinstance(item, SlackMessage):
        return item.channel_id, [item.author_id, item.author_name]
    if isinstance(item, BeeperMessage):
        return str(item.chat_id), [item.sender_id, item.sender_name]
    if isinstance(item, MailMessage):
        return None, mail_authors(item.sender)
    if isinstance(item, ForumPost):
        return None, list(item.authors or [])
    if isinstance(item, (GithubItem, BlogPost, Comic, ForumComment)):
        return None, [item.author]
    return None, []


def author_ids(item: SourceItem) -> list[Any]:
    if isinstance(item, (DiscordMessage, SlackMessage)):
        return [item.author_id]
    if isinstance(item, BeeperMessage):
        return [item.sender_id]
    if isinstance(item, GithubItem):
        return [item.author]
    return []


def item_title(item: SourceItem) -> str | None:
    # Some subtypes derive `title` from their content; only a stored title is one.
    if isinstance(item, MailMessage):
        return item.subject
    if isinstance(item, CalendarEvent):
        return item.event_title
    if "title" in type(item).__mapper__.columns:
        return item.title
    return None


def item_body(item: SourceItem) -> str:
    resolved = item.resolved_content if isinstance(item, SlackMessage) else None
    return resolved or item.content or ""


def item_text(item: SourceItem) -> str:
    return "\n".join(p for p in (item_title(item), item_body(item)) if p)


def github_version_part(item: GithubItem) -> str:
    reviews = item.pr_data.reviews if item.pr_data else None
    return json.dumps(
        {
            "head_sha": item.head_sha,
            "state": item.state,
            "merged_at": item.merged_at.isoformat() if item.merged_at else None,
            "checks": item.checks,
            "reviews": sorted([r.get("id"), r.get("state")] for r in reviews or []),
            "labels": sorted(item.labels or []),
            "assignees": sorted(item.assignees or []),
            "draft": item.draft,
        },
        sort_keys=True,
    )


# The version a removal claims: no item version hashes to it.
REMOVED_VERSION = hashlib.sha256(b"\x00removed").digest()
REMOVED_CHANGE = "removed"


def content_version(item: SourceItem) -> bytes:
    """What an edit changes, read off the item: not every ingester updates sha256 on an edit.

    Raw content counts as well as the body, as Slack edits leave resolved_content
    as it was. The migration that added claim versions backfills this in SQL.
    GitHub items also count what their sync reports as changes beyond the
    content (comments are in it): head commit, state, checks, reviews, labels,
    assignees and draft.
    """
    parts = (item_title(item) or "", item.content or "", item_body(item))
    if isinstance(item, GithubItem):
        parts += (github_version_part(item),)
    return hashlib.sha256("\x1f".join(parts).encode()).digest()


def parent_channel_id(item: SourceItem) -> str | None:
    if not isinstance(item, DiscordMessage) or item.channel is None:
        return None
    parent = item.channel.parent_id
    return None if parent is None else str(parent)


def mentioned_ids(item: SourceItem) -> frozenset[str]:
    if not isinstance(item, (DiscordMessage, SlackMessage)):
        return frozenset()
    return lowered(MENTION.findall(item.content or ""))


def direct_and_collector(item: SourceItem) -> tuple[bool, str | None]:
    if isinstance(item, SlackMessage):
        collector = f"slack_app:{item.slack_app_id}" if item.slack_app_id else None
        channel = item.channel
    elif isinstance(item, DiscordMessage):
        channel = item.channel
        bot = item.bot or (channel.bot if channel else None)
        collector = f"discord_bot:{bot.application_id}" if bot else None
    else:
        return False, None
    return channel is not None and channel.channel_type in DIRECT_CHANNEL_TYPES, collector


def item_facts(item: SourceItem, with_direct: bool = False) -> ItemFacts:
    """`with_direct` also reads whether it's a direct message, which loads its channel."""
    channel_id, authors = channel_and_authors(item)
    direct, collector = direct_and_collector(item) if with_direct else (False, None)
    return ItemFacts(
        source_type=item.type or "",
        tags=frozenset(item.tags or []),
        channel_id=channel_id,
        authors=lowered(authors),
        author_ids=lowered(author_ids(item)),
        text=item_text(item),
        parent_channel_id=parent_channel_id(item),
        repo=item.repo_path.lower() if isinstance(item, GithubItem) and item.repo_path else None,
        mentions=mentioned_ids(item),
        direct=direct,
        collector=collector,
    )


def regex_search(pattern: str, text: str, subscription_id: int | None = None) -> bool | None:
    """A regex search under a time limit, on any thread; None when it timed out, which
    counts as no match.

    The `regex` engine checks its timeout while backtracking, so a pathological
    pattern stops in time, unlike `re`, which only a signal can interrupt.
    """
    try:
        return regex.search(pattern, text[:MAX_MATCH_TEXT_CHARS], timeout=REGEX_TIMEOUT_SEC) is not None
    except TimeoutError:
        logger.warning("subscription %s's regex timed out", subscription_id)
        return None
    # Validated when set, but a later engine release may read the pattern otherwise.
    except regex.error:
        logger.warning("subscription %s's regex could not be matched", subscription_id)
        return False


def failed_filter(f: IngestFilter, facts: ItemFacts, me: Me = Me(), subscription_id: int | None = None) -> str | None:
    """The first of the filter's criteria that the item's facts settle that fails, by
    field name, or regex_timeout when the regex ran out of time; None when they all hold."""
    text = facts.text[:MAX_MATCH_TEXT_CHARS].lower()
    channels = {facts.channel_id, facts.parent_channel_id}
    checks = (
        ("source_types", lambda: facts.source_type in f.source_types, f.source_types),
        ("tags_any", lambda: bool(facts.tags & set(f.tags_any)), f.tags_any),
        ("channel_ids", lambda: bool(channels & set(f.channel_ids)), f.channel_ids),
        ("exclude_channel_ids", lambda: not channels & set(f.exclude_channel_ids), f.exclude_channel_ids),
        ("exclude_authors", lambda: not facts.authors & lowered(f.exclude_authors), f.exclude_authors),
        ("authors_any", lambda: bool(facts.author_ids & lowered(f.authors_any)), f.authors_any),
        ("repos", lambda: facts.repo in lowered(f.repos), f.repos),
        ("mentions_me", lambda: bool(facts.mentions & me.bots), f.mentions_me),
        ("dms_to_me", lambda: facts.direct and facts.collector in me.collectors, f.dms_to_me),
        ("text_contains", lambda: any(t.lower() in text for t in f.text_contains), f.text_contains),
        ("regex", lambda: regex_search(cast(str, f.regex), facts.text, subscription_id), f.regex is not None),
    )
    for name, holds, given in checks:
        if given and not (held := holds()):
            # A dry run tells a runaway regex from one that doesn't match.
            return "regex_timeout" if held is None else name
    return None


def changes_caused(
    f: IngestFilter, changes: list[str], actors: dict[str, list[str]], authors: frozenset[str]
) -> list[str]:
    """The `changes` whose makers pass changed_by_any and exclude_changed_by.

    A change is made by the `actors` its ingester named; a new item or an edit
    nobody is named for, by its `authors` (ItemFacts.author_ids). Anything else has
    no known maker, which changed_by_any never lets through and exclude_changed_by
    never stops.
    """
    wanted, unwanted = lowered(f.changed_by_any), lowered(f.exclude_changed_by)

    def kept(change: str) -> bool:
        if change in actors:
            makers = lowered(actors[change])
        else:
            makers = authors if change in ("new", "edited") else frozenset()
        if wanted and not makers & wanted:
            return False
        return not (makers and makers <= unwanted)

    return [c for c in changes if kept(c)]


# Slack message ts are unique only within a channel.
SLACK_POSTED_ID = re.compile(r"[A-Z0-9]+:\d+\.\d+")


class Posted(BaseModel):
    """Something a consumer posted, named as posted_ref names items."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["slack", "discord", "email", "beeper"]
    external_id: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def slack_names_its_channel(self) -> "Posted":
        if self.source == "slack" and not SLACK_POSTED_ID.fullmatch(self.external_id):
            raise ValueError('a slack external_id is "channel:ts", e.g. "C123:1700000000.000100"')
        return self


POSTED = TypeAdapter(Annotated[list[Posted], Field(max_length=MAX_FILTER_VALUES)])


def posted_ref(item: SourceItem) -> tuple[str, str] | None:
    """How a report names the item, if its consumer posted it: (source, external_id)."""
    if isinstance(item, SlackMessage):
        return "slack", f"{item.channel_id}:{item.message_ts}"
    if isinstance(item, DiscordMessage):
        return "discord", str(item.message_id)
    if isinstance(item, MailMessage) and item.message_id:
        return "email", item.message_id
    if isinstance(item, BeeperMessage):
        return "beeper", item.message_id
    return None


def recent_runs(subscription_id: int) -> tuple[Any, ...]:
    """A subscription's runs young enough to have reports its new items could be in:
    a post is ingested within minutes, and no delivery waits longer than this."""
    since = datetime.now(timezone.utc) - DELIVERY_MAX_AGE
    return (AgentRun.subscription_id == subscription_id, AgentRun.created_at >= since)


def posted_among(db: DBSession, subscription_id: int, items: Iterable[SourceItem]) -> set[int]:
    """The ids of `items` that a run of the subscription reported posting."""
    refs = {ref: item.id for item in items if (ref := posted_ref(item))}
    if not refs:
        return set()
    entries = [[{"source": source, "external_id": external_id}] for source, external_id in refs]
    reports = db.scalars(
        select(AgentRun.posted).where(
            *recent_runs(subscription_id), or_(*(AgentRun.posted.contains(e) for e in entries))
        )
    )
    found = {(p["source"], p["external_id"]) for posted in reports for p in posted or []}
    return {item_id for ref, item_id in refs.items() if ref in found}


def discord_thread_id(item: DiscordMessage) -> int | None:
    """A thread's id is the id of the message that started it."""
    channel = item.channel
    in_thread = channel is not None and (
        channel.channel_type == "thread" or channel.parent_id is not None
    )
    return item.channel_id if in_thread else item.thread_id


def thread_authors(db: DBSession, item: SourceItem) -> frozenset[str]:
    """Who posted in the item's Discord/Slack thread, its first message included."""
    if isinstance(item, SlackMessage) and item.thread_ts:
        query = select(SlackMessage.author_id).where(
            SlackMessage.channel_id == item.channel_id,
            or_(SlackMessage.thread_ts == item.thread_ts, SlackMessage.message_ts == item.thread_ts),
        )
    elif isinstance(item, DiscordMessage) and (thread := discord_thread_id(item)) is not None:
        query = select(DiscordMessage.author_id).where(
            or_(DiscordMessage.channel_id == thread, DiscordMessage.message_id == thread)
        )
    else:
        return frozenset()
    return lowered(db.scalars(query))


def author_people(db: DBSession, item: SourceItem) -> list[Person]:
    """The Persons the item's author is linked to. Email has none: its From
    header is the sender's to write."""
    found: list[Person | None] = []
    if isinstance(item, DiscordMessage):
        found = [item.author.person if item.author else None]
    elif isinstance(item, SlackMessage) and item.author_id:
        found = people_with_slack_id(db, item.workspace_id, item.author_id).all()
    elif isinstance(item, GithubItem):
        found = [item.author_person]
    elif isinstance(item, BeeperMessage) and item.chat and item.sender_id:
        found = list(
            db.scalars(
                select(Person)
                .join(BeeperParticipant, BeeperParticipant.person_id == Person.id)
                .where(
                    BeeperParticipant.network_id == item.chat.network_id,
                    BeeperParticipant.participant_id == item.sender_id,
                )
            )
        )
    return [p for p in found if p is not None]


def vouches(person: Person, owner_id: int) -> bool:
    """Ingest makes a Person for everyone it sees and anyone can make one, so only
    a link to a user, or the owner or an admin having made it, says who someone is."""
    made_by = person.creator_id
    return person.user_id is not None or (
        made_by is not None and (person.creator_trusted or made_by == owner_id)
    )


class ItemLookups:
    """What matching looks up about one item, at most once however many subscriptions ask."""

    def __init__(self, db: DBSession, item: SourceItem):
        self.db, self.item = db, item

    @cached_property
    def thread_authors(self) -> frozenset[str]:
        return thread_authors(self.db, self.item)

    @cached_property
    def people(self) -> list[Person]:
        return author_people(self.db, self.item)

    def known_to(self, owner_id: int) -> bool:
        return any(vouches(p, owner_id) for p in self.people)


def own_authors(db: DBSession) -> frozenset[str]:
    """Accounts our syncs and bots post as: any of our Discord bots and Slack app
    bot users. Matching their items would let an agent that posts to a channel
    re-subscription itself."""
    bots = db.scalars(
        select(DiscordBot.application_id).where(DiscordBot.token_encrypted.is_not(None))
    ).all()
    slack_bots = db.scalars(select(SlackUserCredentials.bot_user_id)).all()
    return lowered([*bots, *slack_bots])


def owner_me(db: DBSession, owner_id: int) -> Me:
    bots = owner_bots(db, owner_id)
    slack_users = lowered(
        db.scalars(
            select(SlackUserCredentials.slack_user_id).where(
                SlackUserCredentials.user_id == owner_id
            )
        )
    )
    discord_accounts = lowered(
        db.scalars(
            select(DiscordUser.id)
            .join(Person, DiscordUser.person_id == Person.id)
            .where(Person.user_id == owner_id)
        )
    )
    discord_bots = db.scalars(
        select(DiscordBot.application_id).where(
            DiscordBot.created_by_user_id == owner_id, DiscordBot.token_encrypted.is_not(None)
        )
    )
    slack_apps = db.scalars(select(SlackApp.id).where(SlackApp.created_by_user_id == owner_id))
    return Me(
        bots=bots,
        users=slack_users | discord_accounts,
        accounts=bots | slack_users | discord_accounts,
        collectors=frozenset(
            [*(f"discord_bot:{i}" for i in discord_bots), *(f"slack_app:{i}" for i in slack_apps)]
        ),
    )


def owner_bots(db: DBSession, owner_id: int) -> frozenset[str]:
    """Bot user ids whose mention addresses the owner's agents: the Discord bots
    they registered and the bot users of their Slack app installs."""
    discord = db.scalars(
        select(DiscordBot.application_id).where(
            DiscordBot.created_by_user_id == owner_id, DiscordBot.token_encrypted.is_not(None)
        )
    ).all()
    slack = db.scalars(
        select(SlackUserCredentials.bot_user_id).where(SlackUserCredentials.user_id == owner_id)
    ).all()
    return lowered([*discord, *slack])


def lock_subscription(db: DBSession, subscription_id: int) -> Subscription | None:
    """The subscription, freshly read and locked against other writers. FOR NO KEY UPDATE,
    so the key-share locks that inserting its claims takes don't wait on it, or it on them."""
    return db.get(
        Subscription, subscription_id, with_for_update={"key_share": True}, populate_existing=True
    )


def acting_key(db: DBSession, subscription: Subscription, owner: User) -> APIKey | None:
    """The API key a subscription's agent acts through; raises PrincipalRevoked once it can't."""
    if subscription.via_api_key and subscription.api_key_id is None:
        raise PrincipalRevoked("the API key that set up this subscription was deleted")
    return standing_key(db, owner, subscription.api_key_id, SCOPE_AGENTS)


def subscription_key(db: DBSession, subscription: Subscription, owner: User) -> APIKey | None:
    """The key a subscription reads through; raises PrincipalRevoked once it can't. A
    queue subscription only hands items over, so reading is all it needs."""
    if "agent" in subscription.handler:
        return acting_key(db, subscription, owner)
    if subscription.via_api_key and subscription.api_key_id is None:
        raise PrincipalRevoked("the API key that set up this subscription was deleted")
    return standing_key(db, owner, subscription.api_key_id, SCOPE_READ)


def can_read(owner: User, key: APIKey | None) -> bool:
    return bool({SCOPE_READ, SCOPE_ADMIN} & set(credential_scopes(owner, key)))


def accessible_ids(
    db: DBSession, owner: User, key: APIKey | None, item_ids: list[int]
) -> list[int]:
    """The subset of item_ids `owner` may read through `key`, by the same filter search uses."""
    scopes = credential_scopes(owner, key)
    if not {SCOPE_READ, SCOPE_ADMIN} & set(scopes):
        return []
    reader = UserProxy.from_user(owner, scopes=scopes)
    access_filter = (
        None
        if has_admin_scope(reader)
        else build_access_filter(reader, get_user_project_roles(db, reader))
    )
    query = db.query(SourceItem.id).filter(
        SourceItem.id.in_(item_ids), SourceItem.modality.notin_(SESSION_COLLECTIONS)
    )
    return sorted(
        row.id for row in apply_access_filter_to_query(query, access_filter).all()
    )


def removals(db: DBSession, subscription_id: int, removal_ids: Collection[int]) -> set[int]:
    """Which of the items whose removal (REMOVED_VERSION) is up for delivery the
    subscription may deliver as removed: those really gone, and delivered to it before.
    Whose key read an item then isn't recorded, so its earlier delivery stands for
    that."""
    if not removal_ids:
        return set()
    present = db.scalars(select(SourceItem.id).where(SourceItem.id.in_(removal_ids)))
    gone = set(removal_ids) - set(present)
    shown = db.scalars(
        select(Delivery.item_id).where(
            Delivery.subscription_id == subscription_id,
            Delivery.item_id.in_(gone),
            Delivery.status == DONE,
            Delivery.content_hash != REMOVED_VERSION,
        )
    )
    return gone & set(shown)


def deliverable_ids(
    db: DBSession, owner: User, key: APIKey | None, item_ids: list[int], removed: Collection[int]
) -> set[int]:
    """The `item_ids` a delivery through `key` may carry: those it can read, and the
    `removed` ones (removals()), of which nothing is left to read, while it may read
    at all."""
    gone = set(removed) & set(item_ids) if can_read(owner, key) else set()
    return set(accessible_ids(db, owner, key, [i for i in item_ids if i not in gone])) | gone


# Notify handlers take only ticks and timers.
LIVE_INGEST = (
    Subscription.kind == INGEST,
    Subscription.enabled,
    or_(Subscription.handler.has_key("agent"), Subscription.handler.has_key("queue")),
)


LIVE_RAW = (Subscription.kind == RAW, Subscription.enabled, Subscription.handler.has_key("queue"))


def handler_queue(subscription: Subscription) -> str | None:
    return (subscription.handler or {}).get("queue")


def enabled_ingest_subscriptions(db: DBSession) -> list[Subscription]:
    return db.query(Subscription).filter(*LIVE_INGEST).order_by(Subscription.id).all()


def any_routed_subscription(db: DBSession) -> bool:
    return db.query(exists().where(or_(and_(*LIVE_INGEST), and_(*LIVE_RAW)))).scalar()


def delivery_ref(item_id: int, version: bytes) -> str:
    """How FIRE_SUBSCRIPTION names a delivery."""
    return f"{item_id}:{version.hex()}"


def parse_ref(ref: str) -> tuple[int, bytes]:
    # Fires sent before deliveries were rows carry more fields, which go unused.
    item_id, version = ref.split(":")[:2]
    return int(item_id), bytes.fromhex(version)


def delivered_changes(edited: bool, changes: list[str] | None) -> list[str]:
    """A first match is always "new"; an edit that reports no changes is "edited"."""
    tags = set(changes or [])
    if not edited:
        tags.add("new")
    return sorted(tags or {"edited"})


def changes_by_item(deliveries: list[Delivery]) -> dict[str, list[str]]:
    """Every change delivered for each item, keyed by its id as a string (a JSON key).

    An item whose first version is among the deliveries has that first match's
    changes ("new", and any news it was matched for): the run is the agent's first
    sight of it, so later edits in the same batch aren't changes.
    """
    first_seen = {str(d.item_id) for d in deliveries if not d.edited}
    merged: dict[str, set[str]] = {}
    for d in deliveries:
        if not (d.edited and str(d.item_id) in first_seen):
            merged.setdefault(str(d.item_id), set()).update(d.changes)
    return {item_id: sorted(tags) for item_id, tags in merged.items()}


def dedupe_key(subscription_id: int, deliveries: list[Delivery]) -> str:
    """Keyed on the claimed versions, so an edit's run isn't taken for a redelivery
    and a redelivery after an edit isn't taken for a new run."""
    versions = sorted(f"{d.item_id}:{bytes(d.content_hash).hex()[:16]}" for d in deliveries)
    if len(versions) == 1:
        return f"{subscription_id}:{versions[0]}"
    digest = hashlib.sha256(",".join(versions).encode()).hexdigest()
    return f"{subscription_id}:batch:{digest[:32]}"


def may_claim(seen: Collection[bytes], version: bytes, *, fresh: bool, on_edit: bool) -> bool:
    """An unseen item matches only if `fresh`; after that, only with on_edit, once
    per version. An edit of an item the subscription never saw can't be told from a
    reprocess, so it doesn't match."""
    return (on_edit and version not in seen) if seen else fresh


def claim(
    db: DBSession,
    subscription_id: int,
    item_id: int,
    version: bytes,
    *,
    fresh: bool,
    on_edit: bool,
    changes: list[str] | None = None,
    sent: bool = False,
    order_key: str | None = None,
    coalesce: bool = False,
    event_id: int | None = None,
) -> Delivery | None:
    """Record, uncommitted, that subscription_id matched this version of item_id, as a
    pending delivery (`sent`: about to be sent to FIRE_SUBSCRIPTION); None if it may not.
    With `coalesce`, it replaces the subscription's pending deliveries for the item (or
    `order_key`), taking over the item's changes.

    Items are reprocessed (re-embeds, upstream edits, re-syncs), and each pass
    re-runs matching, so a version claims at most once.
    """
    # Without it, two passes reading different versions could both claim an item.
    lock_key = func.hashtextextended(f"delivery:{subscription_id}:{item_id}", 0)
    db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    seen = item_claims(db, item_id).get(subscription_id, set())
    if not may_claim(seen, version, fresh=fresh, on_edit=on_edit):
        return None
    match = Delivery(
        subscription_id=subscription_id,
        item_id=item_id,
        content_hash=version,
        edited=bool(seen),
        changes=delivered_changes(bool(seen), changes),
        order_key=order_key,
        event_id=event_id,
        dispatched_at=datetime.now(timezone.utc) if sent else None,
        sends=int(sent),
        # Not the transaction's start, which deliveries routed together share: order_by
        # orders by it.
        created_at=func.clock_timestamp(),
    )
    if coalesce:
        take_over(db, match)
    db.add(match)
    db.flush()
    return match


def take_over(db: DBSession, match: Delivery) -> None:
    """Skip, uncommitted, the pending deliveries the new `match` replaces: its subscription's
    for the same item, or the same order key. It takes the oldest one's place in line
    (created_at and deferral), so a busy item isn't pushed back with every event, and
    records the items folded into it. As in changes_by_item, a replaced first sight of
    the item stays a first sight. Deliveries a fire or sweep has locked are left to it,
    and removals stand: no later message says an earlier one is gone."""
    same = (
        Delivery.order_key == match.order_key
        if match.order_key is not None
        else Delivery.item_id == match.item_id
    )
    replaced = db.scalars(
        select(Delivery)
        .where(
            Delivery.subscription_id == match.subscription_id,
            Delivery.status == PENDING,
            Delivery.content_hash != REMOVED_VERSION,
            same,
        )
        .order_by(Delivery.created_at)
        .with_for_update(skip_locked=True)
    ).all()
    if not replaced:
        return
    for r in replaced:
        r.status = SKIPPED_COALESCED
    match.created_at, match.not_before = replaced[0].created_at, replaced[0].not_before
    match.coalesced = sorted(i for r in replaced for i in [*r.coalesced, r.item_id])
    mine = [(r.edited, r.changes) for r in replaced if r.item_id == match.item_id]
    mine.append((match.edited, match.changes))
    match.edited = all(e for e, _ in mine)
    match.changes = sorted({tag for e, c in mine if match.edited or not e for tag in c})


def coalesced_ref(deliveries: list[Delivery]) -> dict[str, Any]:
    """A run ref's "coalesced": {"<item id>": [ids of the items folded into its
    delivery, once per delivery folded]}; nothing when none were."""
    folded: dict[str, list[int]] = {}
    for d in deliveries:
        if d.coalesced:
            folded.setdefault(str(d.item_id), []).extend(d.coalesced)
    return {"coalesced": {k: sorted(v) for k, v in folded.items()}} if folded else {}


def order_key(item: SourceItem, order_by: str) -> str | None:
    """What a delivery of `item` is ordered by: the item, or the conversation it's in."""
    if order_by == "none":
        return None
    if order_by == "thread":
        if isinstance(item, SlackMessage):
            return f"slack:{item.channel_id}:{item.thread_ts or item.message_ts}"
        # A Discord thread is a channel whose id is its starting message's, so a
        # top-level message and the thread it starts share a key.
        if isinstance(item, DiscordMessage):
            in_thread = parent_channel_id(item) is not None
            return f"discord:{item.channel_id if in_thread else item.message_id}"
        if isinstance(item, BeeperMessage):
            return f"beeper:{item.chat_id}"
    return f"item:{item.id}"


def item_claims(db: DBSession, item_id: int) -> dict[int, set[bytes]]:
    """The versions of item_id each subscription has claimed."""
    query = select(Delivery.subscription_id, Delivery.content_hash).where(
        Delivery.item_id == item_id
    )
    claims: dict[int, set[bytes]] = {}
    for tid, version in db.execute(query):
        claims.setdefault(tid, set()).add(bytes(version))
    return claims


def purge_deliveries(db: DBSession, older_than: timedelta) -> int:
    """Forget ended claims older than `older_than`, except each present item's newest
    under an on_edit or on_remove subscription: that one tells its next reprocess from an
    edit, and its removal from news about an item it never saw."""
    cutoff = datetime.now(timezone.utc) - older_than
    newer = aliased(Delivery)
    keeps_newest = select(Subscription.id).where(
        or_(Subscription.config["on_edit"].as_boolean(), Subscription.config["on_remove"].as_boolean())
    )
    superseded = exists().where(
        newer.subscription_id == Delivery.subscription_id,
        newer.item_id == Delivery.item_id,
        newer.created_at > Delivery.created_at,
    )
    gone = ~exists().where(SourceItem.id == Delivery.item_id)
    deleted = (
        db.query(Delivery)
        .filter(
            Delivery.created_at < cutoff,
            Delivery.status != PENDING,
            or_(Delivery.subscription_id.not_in(keeps_newest), superseded, gone),
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


def send(subscription_id: int, refs: list[str]) -> None:
    """Hand deliveries to FIRE_SUBSCRIPTION; the sweep resends any that don't arrive."""
    try:
        app.send_task(FIRE_SUBSCRIPTION, args=[subscription_id, refs])
    except SoftTimeLimitExceeded:
        raise
    except Exception:
        logger.exception("could not send deliveries to subscription %s", subscription_id)


@dataclass(frozen=True)
class Claimed:
    subscription_id: int
    ref: str
    # Per-item agent deliveries are sent at once; batch members wait for the sweep,
    # queue deliveries for a worker's claim.
    sent: bool
    # Shed by a cap, which started a throttling episode its owner is told of.
    throttled: str | None = None
    # Not delivered: shed by a cap, or its subscription tripped (with the reason).
    shed: bool = False
    tripped: str | None = None

    @property
    def delivered(self) -> bool:
        return not (self.shed or self.tripped)


@dataclass(frozen=True)
class LiveSubscription:
    """An enabled ingest subscription with what matching needs, loaded once per batch."""

    subscription: Subscription
    config: IngestConfig
    me: Me
    # Accounts never matched: our syncs' and bots'.
    own: frozenset[str]
    # The batch's items the subscription may read.
    readable: frozenset[int]
    exclude_own: bool
    # Whether any of its runs reported posting something.
    posts: bool
    # Whether its credential may read at all.
    reads: bool


def excludes_own(f: IngestFilter, handler: dict[str, Any] | None) -> bool:
    return f.exclude_own if f.exclude_own is not None else "queue" not in (handler or {})


def live_subscriptions(
    db: DBSession, item_ids: list[int], candidates: list[Subscription] | None = None
) -> list[LiveSubscription]:
    """The enabled ingest subscriptions (or `candidates`) that can match, skipping any
    whose config no longer validates."""
    per_owner: dict[int, Me] = {}
    own = own_authors(db)
    live = []
    for subscription in enabled_ingest_subscriptions(db) if candidates is None else candidates:
        try:
            config = IngestConfig.model_validate(subscription.config)
        except ValidationError:
            logger.warning("skipping ingest subscription %s: its config is invalid", subscription.id)
            continue
        owner = db.get(User, subscription.user_id)
        if owner is None:
            continue
        if owner.id not in per_owner:
            per_owner[owner.id] = owner_me(db, owner.id)
        # Validity is left to the fire, which trips the breaker if the key is gone.
        key = db.get(APIKey, subscription.api_key_id) if subscription.api_key_id else None
        readable = frozenset(accessible_ids(db, owner, key, item_ids))
        posts = exists().where(*recent_runs(subscription.id), AgentRun.posted.is_not(None))
        live.append(
            LiveSubscription(
                subscription,
                config,
                per_owner[owner.id],
                own,
                readable,
                excludes_own(config.filter, subscription.handler),
                # An unsaved subscription, tried in a dry run, has no runs of its own.
                subscription.id is not None and bool(db.scalar(select(posts))),
                can_read(owner, key),
            )
        )
    return live


def rejection(db: DBSession, t: LiveSubscription, facts: ItemFacts, lookups: ItemLookups) -> str | None:
    """What stops the subscription taking the item: the first filter field it fails, or
    own_authors, readable, exclude_own, threads_im_in, known_people_only or posted
    (one of the subscription's runs posted it); None when nothing does."""
    f = t.config.filter
    if failed := failed_filter(f, facts, t.me, t.subscription.id):
        return failed
    if facts.author_ids & t.own:
        return "own_authors"
    if lookups.item.id not in t.readable:
        return "readable"
    if t.exclude_own and facts.author_ids & t.me.users:
        return "exclude_own"
    if f.threads_im_in and not lookups.thread_authors & t.me.accounts:
        return "threads_im_in"
    if f.known_people_only and not lookups.known_to(t.subscription.user_id):
        return "known_people_only"
    if t.posts and posted_among(db, t.subscription.id, [lookups.item]):
        return "posted"
    return None


def dry_run(db: DBSession, subscription: Subscription, item: SourceItem) -> str | None:
    """rejection() of the item as it is now by the ingest subscription, or "credentials"
    when its key no longer works and "config" when its config no longer validates.
    Whether an event would be fresh is not asked."""
    owner = db.get(User, subscription.user_id)
    try:
        if owner is None:
            raise PrincipalRevoked("its owner is gone")
        subscription_key(db, subscription, owner)
    except PrincipalRevoked:
        return "credentials"
    live = live_subscriptions(db, [item.id], [subscription])
    if not live:
        return "config"
    [t] = live
    facts = item_facts(item, with_direct=t.config.filter.dms_to_me)
    return rejection(db, t, facts, ItemLookups(db, item))


def match_item(
    db: DBSession, live: list[LiveSubscription], event: Event, replay: bool = False
) -> list[Claimed]:
    """Claim, uncommitted, the event's item for every subscription it matches. A
    `replay` is of an event routed while the subscription was tripped, which it would
    have seen, however old."""
    item_id = cast(int, event.item_id)
    item = db.get(SourceItem, item_id)
    if item is None:
        return []
    facts = item_facts(item, with_direct=any(t.config.filter.dms_to_me for t in live))
    lookups = ItemLookups(db, item)
    version = content_version(item)
    # Settles most reprocesses before the costlier checks; claim() re-checks under its lock.
    claims = item_claims(db, item_id)
    oldest = datetime.now(timezone.utc) - DELIVERY_RETENTION
    inserted = item.inserted_at
    claimed = []
    for t in live:
        subscription, config = t.subscription, t.config
        if event.created_at < subscription.active_since and not replay:
            continue
        changes = event.changes
        if config.filter.changed_by_any or config.filter.exclude_changed_by:
            reported = changes or ["new" if event.action == ADDED else "edited"]
            kept = changes_caused(config.filter, reported, event.actors or {}, facts.author_ids)
            if not kept:
                continue
            # What the ingester didn't report stays unreported.
            changes = changes and kept
        # Subscriptions react to new items and to news about older ones, not to the
        # backlog that predates them.
        fresh = (
            inserted is not None
            and inserted >= oldest
            and (inserted >= subscription.active_since or config.reacts_to(changes))
        )
        refires = config.refires(changes)
        if not may_claim(claims.get(subscription.id, set()), version, fresh=fresh, on_edit=refires):
            continue
        if rejection(db, t, facts, lookups) is not None:
            continue
        batched = isinstance(config.delivery, Batch)
        sent = not batched and handler_queue(subscription) is None
        match = claim(
            db,
            subscription.id,
            item_id,
            version,
            fresh=fresh,
            on_edit=refires,
            changes=changes,
            sent=sent,
            # Batches aren't ordered.
            order_key=None if batched else order_key(item, subscription.order_by),
            coalesce=subscription.coalesce_mode == "latest",
            event_id=event.id,
        )
        if match is not None:
            claimed.append(Claimed(subscription.id, delivery_ref(item_id, version), sent))
    return claimed


def match_removed(
    db: DBSession, live: list[LiveSubscription], event: Event, replay: bool = False
) -> list[Claimed]:
    """Claim, uncommitted, the event's removed item for every on_remove subscription it
    was delivered to. Nothing is left to filter or read, so the removal reaches only
    subscriptions that were shown the item, and says no more than that it is gone."""
    item_id = cast(int, event.item_id)
    wanting = {
        t.subscription.id: t
        for t in live
        if t.config.on_remove and t.reads and (replay or event.created_at >= t.subscription.active_since)
    }
    if not wanting:
        return []
    # Ordered after the subscription's last delivery of the item.
    delivered = db.execute(
        select(Delivery.subscription_id, Delivery.order_key)
        .where(
            Delivery.item_id == item_id,
            Delivery.subscription_id.in_(wanting),
            Delivery.status == DONE,
        )
        .order_by(Delivery.subscription_id, Delivery.created_at.desc())
        .distinct(Delivery.subscription_id)
    ).all()
    claimed = []
    for subscription_id, key in delivered:
        t = wanting[subscription_id]
        batched = isinstance(t.config.delivery, Batch)
        sent = not batched and handler_queue(t.subscription) is None
        match = claim(
            db,
            subscription_id,
            item_id,
            REMOVED_VERSION,
            fresh=False,
            on_edit=True,
            changes=[REMOVED_CHANGE],
            sent=sent,
            order_key=None if batched else key,
            event_id=event.id,
        )
        if match is not None:
            claimed.append(Claimed(subscription_id, delivery_ref(item_id, REMOVED_VERSION), sent))
    return claimed


def live_raw_subscriptions(db: DBSession) -> list[tuple[Subscription, RawConfig]]:
    live = []
    for subscription in db.scalars(select(Subscription).where(*LIVE_RAW).order_by(Subscription.id)):
        try:
            live.append((subscription, RawConfig.model_validate((subscription.config or {}).get("raw"))))
        except ValidationError:
            logger.warning("skipping raw subscription %s: its config is invalid", subscription.id)
    return live


def raw_run(subscription: Subscription, event: Event) -> AgentRun:
    """A raw delivery: a run on the subscription's queue, rendered when a worker claims it
    (memory.agents.queues.render_raw)."""
    return AgentRun(
        user_id=subscription.user_id,
        queue=handler_queue(subscription),
        spec={},
        input="",
        max_cost_usd=0,
        trigger_kind=RAW,
        trigger_ref={"subscription_id": subscription.id, "event_id": event.id},
        dedupe_key=f"{subscription.id}:raw:{event.id}",
        subscription_id=subscription.id,
        api_key_id=subscription.api_key_id,
        executor=EXECUTOR_WORKERS,
        # Runs routed together would share the transaction's start, and the AgentRun.id
        # tie-break is a UUID: claims go oldest first, so each gets its own time.
        created_at=func.clock_timestamp(),
    )


RAW_WAITING = (AgentRun.trigger_kind == RAW, AgentRun.status == PENDING, AgentRun.started_at.is_(None))


def end_run(run: AgentRun, reason: str) -> None:
    run.status, run.error, run.finished_at = FAILED, reason, datetime.now(timezone.utc)


def shed_raw_backlog(db: DBSession, subscription: Subscription) -> int:
    """End, uncommitted, the oldest of the subscription's unclaimed raw deliveries beyond
    its backlog_max (else DEFAULT_RAW_BACKLOG)."""
    excess = db.scalars(
        select(AgentRun)
        .where(AgentRun.subscription_id == subscription.id, *RAW_WAITING)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .offset(subscription.backlog_max or DEFAULT_RAW_BACKLOG)
        .with_for_update(skip_locked=True)
    ).all()
    for run in excess:
        end_run(run, SHED_BACKLOG)
    return len(excess)


def trip_now(db: DBSession, subscription: Subscription, reason: str) -> None:
    """Disable, uncommitted, a subscription whose credentials stopped working; its
    owner is told once that commits (tell_tripped_later)."""
    locked = lock_subscription(db, subscription.id)
    if locked is None or not locked.enabled:
        return
    locked.enabled = False
    locked.state = (locked.state or {}) | {"tripped": {"reason": reason, "at": datetime.now(timezone.utc).isoformat()}}
    logger.warning("raw subscription %s tripped", subscription.id)


def waits_for_causes(db: DBSession, event: Event) -> bool:
    """Whether a stored delivery is held back for the item events it causes, which
    its ingest task writes shortly after: until one exists, or CAUSED_GRACE passes."""
    return (
        event.outcome == STORED
        and bool((event.data or {}).get(LINKS_ITEMS))
        and event.created_at > datetime.now(timezone.utc) - CAUSED_GRACE
        and not db.scalar(select(exists().where(Event.caused_by == event.id)))
    )


def deliver_raw(db: DBSession, subscription: Subscription, event: Event) -> Claimed:
    """Write, uncommitted, the raw event's delivery run for the subscription. One over
    max_per_hour (default AGENT_MAX_RUNS_PER_HOUR) is written already ended, so the
    shed is on record without counting towards the rate; beyond the backlog cap the
    oldest waiting ones end."""
    run = raw_run(subscription, event)
    delivered = and_(AgentRun.subscription_id == subscription.id, AgentRun.error.is_distinct_from(SHED_RATE))
    reason, shed = None, False
    try:
        check_hourly_rate(db, delivered, subscription.max_per_hour, "subscription", "max_per_hour")
    except RateExceeded as e:
        end_run(run, SHED_RATE)
        reason, shed = str(e), True
    db.add(run)
    db.flush()
    if not shed and shed_raw_backlog(db, subscription):
        reason = f"more than {subscription.backlog_max or DEFAULT_RAW_BACKLOG} deliveries waiting"
    locked = lock_subscription(db, subscription.id) if reason else None
    throttle = reason if locked is not None and reason and throttled(locked, reason) else None
    return Claimed(subscription.id, f"raw:{event.id}", False, throttle, shed=shed)


def route_raw(
    db: DBSession, live: list[tuple[Subscription, RawConfig]], event: Event
) -> list[Claimed] | None:
    """Deliver, uncommitted, the raw event to every live raw subscription it matches;
    None while it waits_for_causes. A subscription whose credentials stopped working
    trips instead."""
    owners: dict[int, User | None] = {}
    matching = [
        t
        for t, config in live
        if t.enabled and event.created_at >= t.active_since and raw_matches(config, t.user_id, event)
    ]
    if not matching:
        return []
    if waits_for_causes(db, event):
        return None
    claimed = []
    for subscription in matching:
        owner = owners.setdefault(subscription.user_id, db.get(User, subscription.user_id))
        try:
            if owner is None:
                raise PrincipalRevoked("its owner is gone")
            subscription_key(db, subscription, owner)
        except PrincipalRevoked as e:
            trip_now(db, subscription, str(e))
            claimed.append(Claimed(subscription.id, "", False, tripped=str(e)))
            continue
        claimed.append(deliver_raw(db, subscription, event))
    return claimed


def matchable(event: Event) -> bool:
    """Backfills and imports bring in the past, which subscriptions don't react to."""
    return event.kind == ITEM and event.action != REMOVED and not event.backfill


def event_claims(
    db: DBSession,
    live: list[LiveSubscription],
    raw: list[tuple[Subscription, RawConfig]],
    event: Event,
    replay: bool = False,
) -> list[Claimed] | None:
    """The claims routing the event makes, uncommitted; None when it must wait."""
    if matchable(event):
        return match_item(db, live, event, replay)
    if event.kind == ITEM and event.action == REMOVED:
        return match_removed(db, live, event, replay)
    if event.kind == RAW:
        return route_raw(db, raw, event)
    return []


def route_batch(db: DBSession, after_id: int) -> tuple[int | None, list[Claimed]]:
    """Route, uncommitted, the next pending events after `after_id`; returns the
    last event id taken (None when none were pending) and the claims made."""
    pending = (
        select(Event)
        # Ticks and timers are memory.agents.clock's.
        .where(Event.routed_at.is_(None), Event.id > after_id, Event.kind.not_in(TIME_KINDS))
        .order_by(Event.id)
        .limit(ROUTE_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    if not any_routed_subscription(db):
        routed = (
            update(Event)
            .where(Event.id.in_(pending.with_only_columns(Event.id).scalar_subquery()))
            .values(routed_at=func.now())
            .returning(Event.id)
            .cte("routed")
        )
        return db.scalar(select(func.max(routed.c.id))), []
    events = db.scalars(pending).all()
    if not events:
        return None, []
    live = live_subscriptions(db, [cast(int, e.item_id) for e in events if matchable(e)])
    raw = live_raw_subscriptions(db) if any(e.kind == RAW for e in events) else []
    now = datetime.now(timezone.utc)
    claimed = []
    for ev in events:
        claims: list[Claimed] | None = []
        try:
            with db.begin_nested():
                claims = event_claims(db, live, raw, ev)
        except SoftTimeLimitExceeded:
            raise
        except Exception as e:
            ev.attempts += 1
            if ev.attempts < settings.EVENT_MAX_ATTEMPTS:
                logger.warning("could not route event %s, attempt %d", ev.id, ev.attempts, exc_info=True)
                continue
            ev.error = type(e).__name__
            logger.exception("giving up on routing event %s after %d attempts", ev.id, ev.attempts)
        # Left pending for a later run.
        if claims is None:
            continue
        claimed += claims
        ev.routed_at = now
    return events[-1].id, claimed


def replay_batch(db: DBSession, from_id: int) -> tuple[int | None, list[Claimed]]:
    """Route, uncommitted, the next events that the first resuming subscription from
    `from_id` on missed while tripped, to it alone; returns where the next call
    starts, or None when no subscription is resuming. An event that fails is retried by
    a later run, up to EVENT_MAX_ATTEMPTS."""
    subscription = db.scalars(
        select(Subscription)
        .where(*LIVE_INGEST, Subscription.id >= from_id, Subscription.state.has_key(RESUME))
        .order_by(Subscription.id)
        .limit(1)
        .with_for_update(key_share=True)
    ).first()
    if subscription is None:
        return None, []
    cursor = subscription.state[RESUME]
    events = db.scalars(
        missed_events(cursor).where(Event.id > cursor["after"]).order_by(Event.id).limit(ROUTE_BATCH_SIZE)
    ).all()
    if not events:
        subscription.state = {k: v for k, v in subscription.state.items() if k != RESUME}
        return subscription.id, []
    live = live_subscriptions(db, [cast(int, e.item_id) for e in events if matchable(e)], [subscription])
    claimed, after, next_from = [], cursor["after"], subscription.id
    for ev in events:
        try:
            with db.begin_nested():
                claimed += event_claims(db, live, [], ev, replay=True) or []
        except SoftTimeLimitExceeded:
            raise
        except Exception as e:
            ev.attempts += 1
            if ev.attempts < settings.EVENT_MAX_ATTEMPTS:
                logger.warning("could not replay event %s to subscription %s", ev.id, subscription.id, exc_info=True)
                # The next run retries it; this one moves on to other subscriptions.
                next_from = subscription.id + 1
                break
            ev.error = type(e).__name__
            logger.exception("giving up on replaying event %s to subscription %s", ev.id, subscription.id)
        after = ev.id
    subscription.state = subscription.state | {RESUME: cursor | {"after": after}}
    return next_from, claimed


def route_events() -> list[int]:
    """Route pending events to the subscriptions they match, then replay to resumed
    subscriptions what they missed, for up to ROUTE_RUN_SECONDS; returns the subscription id
    of each delivery written, in order.

    Deliveries commit with the events they route and are sent after that commit;
    the sweep resends any whose send was lost.
    """
    delivered = []
    started = time.monotonic()
    shares = ((route_batch, ROUTE_PENDING_SHARE), (replay_batch, 1))
    for route, share in shares:
        deadline = started + ROUTE_RUN_SECONDS * share
        cursor = 0
        while True:
            with make_session() as db:
                # One router at a time: two would lock claim keys in clashing orders.
                if not db.scalar(select(func.pg_try_advisory_xact_lock(ROUTER_LOCK_KEY))):
                    return delivered
                taken, claimed = route(db, cursor)
                db.commit()
            for c in claimed:
                if c.sent:
                    send(c.subscription_id, [c.ref])
                if c.throttled:
                    tell_throttled_later(c.subscription_id, c.throttled)
                if c.tripped:
                    tell_tripped_later(c.subscription_id, c.tripped)
            delivered += [c.subscription_id for c in claimed if c.delivered]
            if taken is None:
                break
            cursor = taken
            if time.monotonic() >= deadline:
                break
    return delivered


def tripped_at(subscription: Subscription) -> datetime | None:
    tripped = (subscription.state or {}).get("tripped")
    return datetime.fromisoformat(tripped["at"]) if tripped else None


def missed_events(cursor: dict[str, Any]):
    return select(Event).where(
        Event.routed_at >= datetime.fromisoformat(cursor["from"]),
        Event.routed_at < datetime.fromisoformat(cursor["to"]),
    )


def resume_from_trip(db: DBSession, subscription: Subscription) -> None:
    """Have the router replay to a tripped subscription the events routed while it was
    tripped (those still kept, EVENT_RETENTION_DAYS), so it carries on as if it
    never had; its active_since stays as it was. A replay still under way is
    extended to now."""
    since = tripped_at(subscription)
    if since is None:
        raise ValueError("only a tripped subscription can resume from when it tripped")
    to = (datetime.now(timezone.utc) + REPLAY_MARGIN).isoformat()
    state = subscription.state or {}
    if RESUME in state:
        subscription.state = state | {RESUME: state[RESUME] | {"to": to}}
        return
    cursor = {"from": (since - REPLAY_MARGIN).isoformat(), "to": to}
    first = db.scalar(missed_events(cursor).with_only_columns(func.min(Event.id)))
    subscription.state = state | {RESUME: cursor | {"after": (first or 1) - 1}}


def tell_owner(db: DBSession, owner: User, subject: str, message: str) -> None:
    try:
        notify_user_now(db, owner, subject=subject, message=message)
    except Exception:
        logger.exception("could not notify user %s", owner.id)


def episode_over(state: dict[str, Any], now: datetime) -> bool:
    """Whether a throttling episode ended: nothing held back for THROTTLE_EPISODE."""
    throttle = state.get(THROTTLED)
    return throttle is None or datetime.fromisoformat(throttle["last"]) < now - THROTTLE_EPISODE


def throttled(subscription: Subscription, reason: str) -> bool:
    """Record on the locked subscription that a cap held its deliveries back; whether
    that starts an episode, which its owner hears about once."""
    now = datetime.now(timezone.utc)
    state = subscription.state or {}
    new = episode_over(state, now)
    since = now.isoformat() if new else state[THROTTLED]["since"]
    subscription.state = state | {THROTTLED: {"reason": reason, "since": since, "last": now.isoformat()}}
    if new:
        logger.warning("agent subscription %s is throttled", subscription.id)
    return new


def tell_tripped_later(subscription_id: int, reason: str) -> None:
    with make_session() as db:
        subscription = db.get(Subscription, subscription_id)
        owner = db.get(User, subscription.user_id) if subscription is not None else None
        if subscription is not None and owner is not None:
            tell_owner(
                db,
                owner,
                subject=f"Subscription paused: {subscription.name}",
                message=f"Subscription {subscription.id} was disabled: {reason}\nEnable it again once that's resolved.",
            )


def tell_throttled_later(subscription_id: int, reason: str) -> None:
    """tell_throttled, after the routing that shed a delivery has committed."""
    with make_session() as db:
        subscription = db.get(Subscription, subscription_id)
        if subscription is not None:
            tell_throttled(db, subscription, reason)


def tell_throttled(db: DBSession, subscription: Subscription, reason: str) -> None:
    owner = db.get(User, subscription.user_id)
    if owner is None:
        return
    # Raw deliveries and ticks are shed at once: a webhook delivery is news only
    # while it's fresh, and a deferred tick would run into the next one.
    held = {
        INGEST: "Its deliveries wait up to an hour, and any still over it then are dropped.",
        RAW: "Its deliveries over it are shed, each recorded as a failed run.",
    }.get(subscription.kind, "Its ticks over it are skipped.")
    tell_owner(
        db,
        owner,
        subject=f"Agent subscription throttled: {subscription.name}",
        message=(
            f"Subscription {subscription.id} is over a limit: {reason}\n{held} "
            "It keeps running; this is the only notice until it has gone two hours "
            "without being held back."
        ),
    )


def expire_raw_runs(db: DBSession, now: datetime) -> int:
    """End, uncommitted, raw deliveries no worker claimed within DELIVERY_MAX_AGE."""
    expired = db.execute(
        update(AgentRun)
        .where(*RAW_WAITING, AgentRun.created_at < now - DELIVERY_MAX_AGE)
        .values(status=FAILED, error=EXPIRED, finished_at=now)
        .returning(AgentRun.id)
    )
    return len(expired.all())


def expire_deliveries(db: DBSession, now: datetime) -> int:
    """End, uncommitted, deliveries pending past DELIVERY_MAX_AGE."""
    expired = db.execute(
        update(Delivery)
        .where(
            Delivery.subscription_id == Subscription.id,
            Delivery.status == PENDING,
            Delivery.created_at < now - DELIVERY_MAX_AGE,
        )
        .values(status=case((Subscription.enabled, EXPIRED), else_=SKIPPED_DISABLED))
        .returning(Delivery.item_id)
    )
    return len(expired.all())


def shed_backlog(db: DBSession, subscription: Subscription) -> int:
    """Shed, uncommitted, the oldest of the subscription's pending deliveries beyond its backlog_max."""
    if subscription.backlog_max is None:
        return 0
    excess = db.scalars(
        select(Delivery)
        .where(Delivery.subscription_id == subscription.id, Delivery.status == PENDING)
        .order_by(Delivery.created_at.desc(), Delivery.item_id.desc())
        .offset(subscription.backlog_max)
        .with_for_update()
    ).all()
    for m in excess:
        m.status = SHED_BACKLOG
    return len(excess)


def due_batches(
    pending: list[Delivery], batch: Batch, now: datetime
) -> list[list[Delivery]]:
    """`pending` (oldest first) cut into the batches due now: full ones, then the
    rest once its oldest has waited window_sec or it holds a deferral now due."""
    window = timedelta(seconds=batch.window_sec)
    batches = []
    while pending:
        due = (
            len(pending) >= batch.max_items
            or pending[0].created_at <= now - window
            or any(m.not_before is not None for m in pending)
        )
        if not due:
            break
        batches.append(pending[: batch.max_items])
        pending = pending[batch.max_items :]
    return batches


def resend_due(m: Delivery, now: datetime) -> bool:
    if m.dispatched_at is None:
        return True
    doublings = min(max(m.sends - 1, 0), MAX_RESEND_DOUBLINGS)
    return m.dispatched_at <= now - DISPATCH_GRACE * 2**doublings


def due_sends(db: DBSession, subscription: Subscription, now: datetime) -> list[list[str]]:
    """Mark sent, uncommitted, the subscription's deliveries due now, and return them as
    FIRE_SUBSCRIPTION's refs, one list per run."""
    try:
        delivery = IngestConfig.model_validate(subscription.config).delivery
    except ValidationError:
        logger.warning("not sending deliveries of subscription %s: its config is invalid", subscription.id)
        return []
    pending = db.scalars(
        select(Delivery)
        .where(
            Delivery.subscription_id == subscription.id,
            Delivery.status == PENDING,
            or_(Delivery.not_before.is_(None), Delivery.not_before <= now),
            or_(
                Delivery.dispatched_at.is_(None),
                Delivery.dispatched_at <= now - DISPATCH_GRACE,
            ),
        )
        .order_by(Delivery.created_at, Delivery.item_id)
    ).all()
    pending = [m for m in pending if resend_due(m, now)]
    groups = (
        due_batches(pending, delivery, now)
        if isinstance(delivery, Batch)
        else [[m] for m in pending]
    )
    for m in itertools.chain.from_iterable(groups):
        m.dispatched_at, m.sends = now, m.sends + 1
    return [[delivery_ref(m.item_id, m.content_hash) for m in group] for group in groups]


def redispatch(db: DBSession, subscription_id: int) -> None:
    """Make the subscription's waiting deliveries due at once, as if never sent: the
    fires of those sent while it was disabled dropped them."""
    db.execute(
        update(Delivery)
        .where(Delivery.subscription_id == subscription_id, Delivery.status == PENDING)
        .values(dispatched_at=None, sends=0)
    )


def send_due(subscription_id: int) -> None:
    """Send the subscription's due deliveries now rather than at the next sweep."""
    with make_session() as db:
        subscription = db.get(Subscription, subscription_id, with_for_update={"key_share": True})
        if subscription is None or not subscription.enabled or handler_queue(subscription) is not None:
            return
        due = due_sends(db, subscription, datetime.now(timezone.utc))
    for refs in due:
        send(subscription_id, refs)


def sweep_deliveries() -> dict[str, int]:
    """End deliveries pending past DELIVERY_MAX_AGE, shed backlogs over backlog_max,
    and send what is due: per-item deliveries still pending DISPATCH_GRACE (doubling
    with each resend) after they were sent (a crash after routing, a lost message), deferrals whose wait is
    over, and batches whose window closed or that are full. A disabled subscription's
    deliveries wait for it."""
    now = datetime.now(timezone.utc)
    sends: list[tuple[int, list[str]]] = []
    shed = 0
    with make_session() as db:
        if not db.scalar(select(func.pg_try_advisory_xact_lock(SWEEP_LOCK_KEY))):
            return {"expired": 0, "shed": 0, "sent": 0}
        # Subscriptions first, the order fire_subscription locks in.
        has_pending = exists().where(
            Delivery.subscription_id == Subscription.id, Delivery.status == PENDING
        )
        waiting = db.scalars(
            select(Subscription)
            .where(has_pending)
            .order_by(Subscription.id)
            .with_for_update(key_share=True)
        ).all()
        expired = expire_deliveries(db, now) + expire_raw_runs(db, now)
        newly_throttled: list[tuple[Subscription, str]] = []
        for subscription in waiting:
            queued = handler_queue(subscription) is not None
            if not (subscription.kind == INGEST and subscription.enabled and ({"agent", "queue"} & set(subscription.handler))):
                continue
            if n := shed_backlog(db, subscription):
                logger.warning("agent subscription %s shed %d deliveries over its backlog", subscription.id, n)
                reason = f"more than {subscription.backlog_max} deliveries waiting"
                if throttled(subscription, reason):
                    newly_throttled.append((subscription, reason))
            shed += n
            # Workers claim queue deliveries; there's nothing to send.
            if not queued:
                sends += [(subscription.id, refs) for refs in due_sends(db, subscription, now)]
        db.commit()
        for subscription, reason in newly_throttled:
            tell_throttled(db, subscription, reason)
    for subscription_id, refs in sends:
        send(subscription_id, refs)
    return {"expired": expired, "shed": shed, "sent": len(sends)}
