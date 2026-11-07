"""Ingest triggers: deterministic filters over newly stored items.

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
import signal
import threading
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Annotated, Any, Collection, Iterable, Literal, cast

from celery.exceptions import SoftTimeLimitExceeded
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import case, exists, func, or_, select, update
from sqlalchemy.orm import aliased

from memory.common import settings
from memory.agents.principal import PrincipalRevoked, standing_key
from memory.common.access_control import (
    UserProxy,
    apply_access_filter_to_query,
    build_access_filter,
    credential_scopes,
    get_user_project_roles,
    has_admin_scope,
)
from memory.common.celery_app import FIRE_TRIGGER, app
from memory.common.collections import SESSION_COLLECTIONS
from memory.common.notify import notify_user_now
from memory.common.scopes import SCOPE_ADMIN, SCOPE_AGENTS, SCOPE_READ
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    AgentTrigger,
    APIKey,
    AgentTriggerMatch,
    BeeperMessage,
    BlogPost,
    CalendarEvent,
    Comic,
    DiscordBot,
    DiscordMessage,
    Event,
    ForumComment,
    ForumPost,
    GithubItem,
    MailMessage,
    SlackMessage,
    SourceItem,
    User,
)
from memory.common.db.models.events import ITEM, REMOVED
from memory.common.db.models.slack import SlackUserCredentials

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
DEFAULT_TEMPLATE = f"New items matched this trigger:\n\n{ITEMS_PLACEHOLDER}"
# Reprocessing that would re-match an item (re-syncs, upstream edits) mostly
# happens soon after ingest, so claims older than this are purged; items older
# than this never match as new ones, so the purge can't let one fire again.
MATCH_RETENTION = timedelta(days=30)
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
SHED_RATE = "skipped:shed:rate"
SHED_BUDGET = "skipped:shed:budget"
SHED_BACKLOG = "skipped:shed:backlog"
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
# Deliveries pending this long (their trigger disabled, or firing down) end.
DELIVERY_MAX_AGE = timedelta(days=7)
# trigger.state keys.
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
# Changes that are news about an item: they make one the trigger never saw match.
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
    # Discord channel snowflakes or Slack channel ids.
    channel_ids: FilterValues = []
    # Also excludes the threads of these channels.
    exclude_channel_ids: FilterValues = []
    # Case-insensitive substrings of the item's title or text.
    text_contains: FilterValues = []
    regex: str | None = Field(default=None, max_length=MAX_REGEX_CHARS)
    # Author ids, usernames or email addresses (case-insensitive, exact).
    exclude_authors: FilterValues = []
    # GitHub "owner/name" (case-insensitive).
    repos: FilterValues = []
    # Discord/Slack messages that @-mention one of the owner's bots.
    mentions_me: bool = False

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
            re.compile(value)
        except re.error as e:
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
        ):
            raise ValueError(
                "set at least one of source_types, tags_any, channel_ids, "
                "text_contains, regex, repos or mentions_me"
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
    # The run's input; placeholders are filled by memory.agents.trigger_input.
    template: str = Field(
        default=DEFAULT_TEMPLATE, min_length=1, max_length=MAX_TEMPLATE_CHARS
    )
    # Not part of the filter: it decides which events count, not which items.
    on_edit: bool = False
    # Edits fire only when they report one of these changes; empty means any edit.
    on_changes: list[Change] = []

    @model_validator(mode="after")
    def changes_need_edits(self) -> "IngestConfig":
        if self.on_changes and not self.on_edit:
            raise ValueError("on_changes needs on_edit: only edits are filtered by change")
        return self

    def reacts_to(self, changes: list[str] | None) -> bool:
        """Whether reported `changes` are news about an item this trigger never saw."""
        news = NEWS & set(changes or [])
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
    # A Discord thread's parent; Slack thread replies carry their channel's id.
    parent_channel_id: str | None = None
    # Lowercased GitHub "owner/name".
    repo: str | None = None
    # Lowercased ids the message @-mentions, as its raw content spells them.
    mentions: frozenset[str] = frozenset()


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


def item_facts(item: SourceItem) -> ItemFacts:
    channel_id, authors = channel_and_authors(item)
    return ItemFacts(
        source_type=item.type or "",
        tags=frozenset(item.tags or []),
        channel_id=channel_id,
        authors=lowered(authors),
        text=item_text(item),
        parent_channel_id=parent_channel_id(item),
        repo=item.repo_path.lower() if isinstance(item, GithubItem) and item.repo_path else None,
        mentions=mentioned_ids(item),
    )


class RegexTimeout(Exception):
    pass


def raise_timeout(signum, frame):
    raise RegexTimeout()


def regex_search(pattern: str, text: str) -> bool:
    """re.search under a SIGALRM time limit; a timeout counts as no match.

    The regex engine checks for signals while backtracking, so the alarm
    interrupts it. Signals only reach the main thread; elsewhere this refuses
    rather than run a pattern unbounded.
    """
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("trigger regexes can only be matched on the main thread")
    previous = signal.signal(signal.SIGALRM, raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, REGEX_TIMEOUT_SEC)
    try:
        return re.search(pattern, text[:MAX_MATCH_TEXT_CHARS]) is not None
    except RegexTimeout:
        logger.warning("trigger regex %r timed out", pattern)
        return False
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def matches(f: IngestFilter, facts: ItemFacts, mine: frozenset[str] = frozenset()) -> bool:
    """`mine`: the owner's bot ids, which mentions_me looks for."""
    if f.source_types and facts.source_type not in f.source_types:
        return False
    if f.tags_any and not facts.tags & set(f.tags_any):
        return False
    if f.channel_ids and facts.channel_id not in f.channel_ids:
        return False
    if {facts.channel_id, facts.parent_channel_id} & set(f.exclude_channel_ids):
        return False
    if facts.authors & lowered(f.exclude_authors):
        return False
    if f.repos and facts.repo not in lowered(f.repos):
        return False
    if f.mentions_me and not facts.mentions & mine:
        return False
    text = facts.text[:MAX_MATCH_TEXT_CHARS].lower()
    if f.text_contains and not any(t.lower() in text for t in f.text_contains):
        return False
    return f.regex is None or regex_search(f.regex, facts.text)


def own_authors(db: DBSession, owner_id: int) -> frozenset[str]:
    """Accounts an agent's own posts come from: any of our Discord bots and Slack
    app bot users, and the owner's Slack integration user. Matching their items
    would let an agent that posts to a channel re-trigger itself."""
    bots = db.scalars(
        select(DiscordBot.application_id).where(DiscordBot.token_encrypted.is_not(None))
    ).all()
    slack_bots = db.scalars(select(SlackUserCredentials.bot_user_id)).all()
    slack = db.scalars(
        select(SlackUserCredentials.slack_user_id).where(
            SlackUserCredentials.user_id == owner_id
        )
    ).all()
    return lowered([*bots, *slack_bots, *slack])


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


def lock_trigger(db: DBSession, trigger_id: int) -> AgentTrigger | None:
    """The trigger, freshly read and locked against other writers. FOR NO KEY UPDATE,
    so the key-share locks that inserting its claims takes don't wait on it, or it on them."""
    return db.get(
        AgentTrigger, trigger_id, with_for_update={"key_share": True}, populate_existing=True
    )


def trigger_key(db: DBSession, trigger: AgentTrigger, owner: User) -> APIKey | None:
    """The API key a trigger acts through; raises PrincipalRevoked once it can't."""
    if trigger.via_api_key and trigger.api_key_id is None:
        raise PrincipalRevoked("the API key that set up this trigger was deleted")
    return standing_key(db, owner, trigger.api_key_id, SCOPE_AGENTS)


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


# Only definition handlers fire yet: matching others would claim items nothing delivers.
LIVE_INGEST = (
    AgentTrigger.kind == INGEST,
    AgentTrigger.enabled,
    AgentTrigger.definition_id.is_not(None),
)


def enabled_ingest_triggers(db: DBSession) -> list[AgentTrigger]:
    return db.query(AgentTrigger).filter(*LIVE_INGEST).order_by(AgentTrigger.id).all()


def any_ingest_trigger(db: DBSession) -> bool:
    return db.query(exists().where(*LIVE_INGEST)).scalar()


def delivery_ref(item_id: int, version: bytes) -> str:
    """How FIRE_TRIGGER names a delivery."""
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


def changes_by_item(deliveries: list[AgentTriggerMatch]) -> dict[str, list[str]]:
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


def dedupe_key(trigger_id: int, deliveries: list[AgentTriggerMatch]) -> str:
    """Keyed on the claimed versions, so an edit's run isn't taken for a redelivery
    and a redelivery after an edit isn't taken for a new run."""
    versions = sorted(f"{d.item_id}:{bytes(d.content_hash).hex()[:16]}" for d in deliveries)
    if len(versions) == 1:
        return f"{trigger_id}:{versions[0]}"
    digest = hashlib.sha256(",".join(versions).encode()).hexdigest()
    return f"{trigger_id}:batch:{digest[:32]}"


def may_claim(seen: Collection[bytes], version: bytes, *, fresh: bool, on_edit: bool) -> bool:
    """An unseen item matches only if `fresh`; after that, only with on_edit, once
    per version. An edit of an item the trigger never saw can't be told from a
    reprocess, so it doesn't match."""
    return (on_edit and version not in seen) if seen else fresh


def claim(
    db: DBSession,
    trigger_id: int,
    item_id: int,
    version: bytes,
    *,
    fresh: bool,
    on_edit: bool,
    changes: list[str] | None = None,
    sent: bool = False,
) -> AgentTriggerMatch | None:
    """Record, uncommitted, that trigger_id matched this version of item_id, as a
    pending delivery (`sent`: about to be sent to FIRE_TRIGGER); None if it may not.

    Items are reprocessed (re-embeds, upstream edits, re-syncs), and each pass
    re-runs matching, so a version claims at most once.
    """
    # Without it, two passes reading different versions could both claim an item.
    lock_key = func.hashtextextended(f"agent_trigger_match:{trigger_id}:{item_id}", 0)
    db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    seen = item_claims(db, item_id).get(trigger_id, set())
    if not may_claim(seen, version, fresh=fresh, on_edit=on_edit):
        return None
    match = AgentTriggerMatch(
        trigger_id=trigger_id,
        item_id=item_id,
        content_hash=version,
        edited=bool(seen),
        changes=delivered_changes(bool(seen), changes),
        dispatched_at=datetime.now(timezone.utc) if sent else None,
        sends=int(sent),
    )
    db.add(match)
    db.flush()
    return match


def item_claims(db: DBSession, item_id: int) -> dict[int, set[bytes]]:
    """The versions of item_id each trigger has claimed."""
    query = select(AgentTriggerMatch.trigger_id, AgentTriggerMatch.content_hash).where(
        AgentTriggerMatch.item_id == item_id
    )
    claims: dict[int, set[bytes]] = {}
    for tid, version in db.execute(query):
        claims.setdefault(tid, set()).add(bytes(version))
    return claims


def purge_matches(db: DBSession, older_than: timedelta) -> int:
    """Forget ended claims older than `older_than`, except each item's newest under
    an on_edit trigger: that one tells its next reprocess from an edit."""
    cutoff = datetime.now(timezone.utc) - older_than
    newer = aliased(AgentTriggerMatch)
    on_edit_triggers = select(AgentTrigger.id).where(
        AgentTrigger.config["on_edit"].as_boolean()
    )
    superseded = exists().where(
        newer.trigger_id == AgentTriggerMatch.trigger_id,
        newer.item_id == AgentTriggerMatch.item_id,
        newer.created_at > AgentTriggerMatch.created_at,
    )
    deleted = (
        db.query(AgentTriggerMatch)
        .filter(
            AgentTriggerMatch.created_at < cutoff,
            AgentTriggerMatch.status != PENDING,
            or_(AgentTriggerMatch.trigger_id.not_in(on_edit_triggers), superseded),
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


def send(trigger_id: int, refs: list[str]) -> None:
    """Hand deliveries to FIRE_TRIGGER; the sweep resends any that don't arrive."""
    try:
        app.send_task(FIRE_TRIGGER, args=[trigger_id, refs])
    except SoftTimeLimitExceeded:
        raise
    except Exception:
        logger.exception("could not send deliveries to trigger %s", trigger_id)


@dataclass(frozen=True)
class Claimed:
    trigger_id: int
    ref: str
    # Batch members wait for the sweep; per-item deliveries are sent at once.
    batched: bool


@dataclass(frozen=True)
class LiveTrigger:
    """An enabled ingest trigger with what matching needs, loaded once per batch."""

    trigger: AgentTrigger
    config: IngestConfig
    # The owner's bots (for mentions_me) and own authors (never matched).
    bots: frozenset[str]
    own: frozenset[str]
    # The batch's items the trigger may read.
    readable: frozenset[int]


def live_triggers(
    db: DBSession, item_ids: list[int], candidates: list[AgentTrigger] | None = None
) -> list[LiveTrigger]:
    """The enabled ingest triggers (or `candidates`) that can match, skipping any
    whose config no longer validates."""
    per_owner: dict[int, tuple[frozenset[str], frozenset[str]]] = {}
    live = []
    for trigger in enabled_ingest_triggers(db) if candidates is None else candidates:
        try:
            config = IngestConfig.model_validate(trigger.config)
        except ValidationError:
            logger.warning("skipping ingest trigger %s: its config is invalid", trigger.id)
            continue
        owner = db.get(User, trigger.user_id)
        if owner is None:
            continue
        if owner.id not in per_owner:
            per_owner[owner.id] = (owner_bots(db, owner.id), own_authors(db, owner.id))
        bots, own = per_owner[owner.id]
        # Validity is left to the fire, which trips the breaker if the key is gone.
        key = db.get(APIKey, trigger.api_key_id) if trigger.api_key_id else None
        readable = frozenset(accessible_ids(db, owner, key, item_ids))
        live.append(LiveTrigger(trigger, config, bots, own, readable))
    return live


def match_item(
    db: DBSession, live: list[LiveTrigger], event: Event, replay: bool = False
) -> list[Claimed]:
    """Claim, uncommitted, the event's item for every trigger it matches. A
    `replay` is of an event routed while the trigger was tripped, which it would
    have seen, however old."""
    item_id = cast(int, event.item_id)
    item = db.get(SourceItem, item_id)
    if item is None:
        return []
    facts = item_facts(item)
    version = content_version(item)
    # Settles most reprocesses before the costlier checks; claim() re-checks under its lock.
    claims = item_claims(db, item_id)
    oldest = datetime.now(timezone.utc) - MATCH_RETENTION
    inserted = item.inserted_at
    claimed = []
    for t in live:
        trigger, config = t.trigger, t.config
        if event.created_at < trigger.active_since and not replay:
            continue
        # Triggers react to new items and to news about older ones, not to the
        # backlog that predates them.
        fresh = (
            inserted is not None
            and inserted >= oldest
            and (inserted >= trigger.active_since or config.reacts_to(event.changes))
        )
        refires = config.refires(event.changes)
        if not may_claim(claims.get(trigger.id, set()), version, fresh=fresh, on_edit=refires):
            continue
        mine = t.bots if config.filter.mentions_me else frozenset()
        if not matches(config.filter, facts, mine):
            continue
        if facts.authors & t.own or item_id not in t.readable:
            continue
        batched = isinstance(config.delivery, Batch)
        match = claim(
            db,
            trigger.id,
            item_id,
            version,
            fresh=fresh,
            on_edit=refires,
            changes=event.changes,
            sent=not batched,
        )
        if match is not None:
            claimed.append(Claimed(trigger.id, delivery_ref(item_id, version), batched))
    return claimed


def matchable(event: Event) -> bool:
    """Backfills and imports bring in the past, which triggers don't react to."""
    return event.kind == ITEM and event.action != REMOVED and not event.backfill


def route_batch(db: DBSession, after_id: int) -> tuple[int | None, list[Claimed]]:
    """Route, uncommitted, the next pending events after `after_id`; returns the
    last event id taken (None when none were pending) and the claims made."""
    pending = (
        select(Event)
        .where(Event.routed_at.is_(None), Event.id > after_id)
        .order_by(Event.id)
        .limit(ROUTE_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    if not any_ingest_trigger(db):
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
    live = live_triggers(db, [cast(int, e.item_id) for e in events if matchable(e)])
    now = datetime.now(timezone.utc)
    claimed = []
    for ev in events:
        if matchable(ev):
            try:
                with db.begin_nested():
                    claimed += match_item(db, live, ev)
            except SoftTimeLimitExceeded:
                raise
            except Exception as e:
                ev.attempts += 1
                if ev.attempts < settings.EVENT_MAX_ATTEMPTS:
                    logger.warning("could not route event %s, attempt %d", ev.id, ev.attempts, exc_info=True)
                    continue
                ev.error = type(e).__name__
                logger.exception("giving up on routing event %s after %d attempts", ev.id, ev.attempts)
        ev.routed_at = now
    return events[-1].id, claimed


def replay_batch(db: DBSession, from_id: int) -> tuple[int | None, list[Claimed]]:
    """Route, uncommitted, the next events that the first resuming trigger from
    `from_id` on missed while tripped, to it alone; returns where the next call
    starts, or None when no trigger is resuming. An event that fails is retried by
    a later run, up to EVENT_MAX_ATTEMPTS."""
    trigger = db.scalars(
        select(AgentTrigger)
        .where(*LIVE_INGEST, AgentTrigger.id >= from_id, AgentTrigger.state.has_key(RESUME))
        .order_by(AgentTrigger.id)
        .limit(1)
        .with_for_update(key_share=True)
    ).first()
    if trigger is None:
        return None, []
    cursor = trigger.state[RESUME]
    events = db.scalars(
        missed_events(cursor).where(Event.id > cursor["after"]).order_by(Event.id).limit(ROUTE_BATCH_SIZE)
    ).all()
    if not events:
        trigger.state = {k: v for k, v in trigger.state.items() if k != RESUME}
        return trigger.id, []
    live = live_triggers(db, [cast(int, e.item_id) for e in events if matchable(e)], [trigger])
    claimed, after, next_from = [], cursor["after"], trigger.id
    for ev in events:
        if matchable(ev):
            try:
                with db.begin_nested():
                    claimed += match_item(db, live, ev, replay=True)
            except SoftTimeLimitExceeded:
                raise
            except Exception as e:
                ev.attempts += 1
                if ev.attempts < settings.EVENT_MAX_ATTEMPTS:
                    logger.warning("could not replay event %s to trigger %s", ev.id, trigger.id, exc_info=True)
                    # The next run retries it; this one moves on to other triggers.
                    next_from = trigger.id + 1
                    break
                ev.error = type(e).__name__
                logger.exception("giving up on replaying event %s to trigger %s", ev.id, trigger.id)
        after = ev.id
    trigger.state = trigger.state | {RESUME: cursor | {"after": after}}
    return next_from, claimed


def route_events() -> list[int]:
    """Route pending events to the triggers they match, then replay to resumed
    triggers what they missed, for up to ROUTE_RUN_SECONDS; returns the trigger id
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
                if not c.batched:
                    send(c.trigger_id, [c.ref])
            delivered += [c.trigger_id for c in claimed]
            if taken is None:
                break
            cursor = taken
            if time.monotonic() >= deadline:
                break
    return delivered


def tripped_at(trigger: AgentTrigger) -> datetime | None:
    tripped = (trigger.state or {}).get("tripped")
    return datetime.fromisoformat(tripped["at"]) if tripped else None


def missed_events(cursor: dict[str, Any]):
    return select(Event).where(
        Event.routed_at >= datetime.fromisoformat(cursor["from"]),
        Event.routed_at < datetime.fromisoformat(cursor["to"]),
    )


def resume_from_trip(db: DBSession, trigger: AgentTrigger) -> None:
    """Have the router replay to a tripped trigger the events routed while it was
    tripped (those still kept, EVENT_RETENTION_DAYS), so it carries on as if it
    never had; its active_since stays as it was. A replay still under way is
    extended to now."""
    since = tripped_at(trigger)
    if since is None:
        raise ValueError("only a tripped trigger can resume from when it tripped")
    to = (datetime.now(timezone.utc) + REPLAY_MARGIN).isoformat()
    state = trigger.state or {}
    if RESUME in state:
        trigger.state = state | {RESUME: state[RESUME] | {"to": to}}
        return
    cursor = {"from": (since - REPLAY_MARGIN).isoformat(), "to": to}
    first = db.scalar(missed_events(cursor).with_only_columns(func.min(Event.id)))
    trigger.state = state | {RESUME: cursor | {"after": (first or 1) - 1}}


def tell_owner(db: DBSession, owner: User, subject: str, message: str) -> None:
    try:
        notify_user_now(db, owner, subject=subject, message=message)
    except Exception:
        logger.exception("could not notify user %s", owner.id)


def episode_over(state: dict[str, Any], now: datetime) -> bool:
    """Whether a throttling episode ended: nothing held back for THROTTLE_EPISODE."""
    throttle = state.get(THROTTLED)
    return throttle is None or datetime.fromisoformat(throttle["last"]) < now - THROTTLE_EPISODE


def throttled(trigger: AgentTrigger, reason: str) -> bool:
    """Record on the locked trigger that a cap held its deliveries back; whether
    that starts an episode, which its owner hears about once."""
    now = datetime.now(timezone.utc)
    state = trigger.state or {}
    new = episode_over(state, now)
    since = now.isoformat() if new else state[THROTTLED]["since"]
    trigger.state = state | {THROTTLED: {"reason": reason, "since": since, "last": now.isoformat()}}
    if new:
        logger.warning("agent trigger %s is throttled", trigger.id)
    return new


def tell_throttled(db: DBSession, trigger: AgentTrigger, reason: str) -> None:
    owner = db.get(User, trigger.user_id)
    if owner is None:
        return
    tell_owner(
        db,
        owner,
        subject=f"Agent trigger throttled: {trigger.name}",
        message=(
            f"Trigger {trigger.id} is over a limit: {reason}\n"
            "Its deliveries wait up to an hour, and any still over it then are dropped. "
            "It keeps running; this is the only notice until it has gone two hours "
            "without being held back."
        ),
    )


def expire_deliveries(db: DBSession, now: datetime) -> int:
    """End, uncommitted, deliveries pending past DELIVERY_MAX_AGE."""
    expired = db.execute(
        update(AgentTriggerMatch)
        .where(
            AgentTriggerMatch.trigger_id == AgentTrigger.id,
            AgentTriggerMatch.status == PENDING,
            AgentTriggerMatch.created_at < now - DELIVERY_MAX_AGE,
        )
        .values(status=case((AgentTrigger.enabled, EXPIRED), else_=SKIPPED_DISABLED))
        .returning(AgentTriggerMatch.item_id)
    )
    return len(expired.all())


def shed_backlog(db: DBSession, trigger: AgentTrigger) -> int:
    """Shed, uncommitted, the oldest of the trigger's pending deliveries beyond its backlog_max."""
    if trigger.backlog_max is None:
        return 0
    excess = db.scalars(
        select(AgentTriggerMatch)
        .where(AgentTriggerMatch.trigger_id == trigger.id, AgentTriggerMatch.status == PENDING)
        .order_by(AgentTriggerMatch.created_at.desc(), AgentTriggerMatch.item_id.desc())
        .offset(trigger.backlog_max)
        .with_for_update()
    ).all()
    for m in excess:
        m.status = SHED_BACKLOG
    return len(excess)


def due_batches(
    pending: list[AgentTriggerMatch], batch: Batch, now: datetime
) -> list[list[AgentTriggerMatch]]:
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


def resend_due(m: AgentTriggerMatch, now: datetime) -> bool:
    if m.dispatched_at is None:
        return True
    doublings = min(max(m.sends - 1, 0), MAX_RESEND_DOUBLINGS)
    return m.dispatched_at <= now - DISPATCH_GRACE * 2**doublings


def due_sends(db: DBSession, trigger: AgentTrigger, now: datetime) -> list[list[str]]:
    """Mark sent, uncommitted, the trigger's deliveries due now, and return them as
    FIRE_TRIGGER's refs, one list per run."""
    try:
        delivery = IngestConfig.model_validate(trigger.config).delivery
    except ValidationError:
        logger.warning("not sending deliveries of trigger %s: its config is invalid", trigger.id)
        return []
    pending = db.scalars(
        select(AgentTriggerMatch)
        .where(
            AgentTriggerMatch.trigger_id == trigger.id,
            AgentTriggerMatch.status == PENDING,
            or_(AgentTriggerMatch.not_before.is_(None), AgentTriggerMatch.not_before <= now),
            or_(
                AgentTriggerMatch.dispatched_at.is_(None),
                AgentTriggerMatch.dispatched_at <= now - DISPATCH_GRACE,
            ),
        )
        .order_by(AgentTriggerMatch.created_at, AgentTriggerMatch.item_id)
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


def sweep_deliveries() -> dict[str, int]:
    """End deliveries pending past DELIVERY_MAX_AGE, shed backlogs over backlog_max,
    and send what is due: per-item deliveries still pending DISPATCH_GRACE (doubling
    with each resend) after they were sent (a crash after routing, a lost message), deferrals whose wait is
    over, and batches whose window closed or that are full. A disabled trigger's
    deliveries wait for it."""
    now = datetime.now(timezone.utc)
    sends: list[tuple[int, list[str]]] = []
    shed = 0
    with make_session() as db:
        if not db.scalar(select(func.pg_try_advisory_xact_lock(SWEEP_LOCK_KEY))):
            return {"expired": 0, "shed": 0, "sent": 0}
        # Triggers first, the order fire_trigger locks in.
        has_pending = exists().where(
            AgentTriggerMatch.trigger_id == AgentTrigger.id, AgentTriggerMatch.status == PENDING
        )
        waiting = db.scalars(
            select(AgentTrigger)
            .where(has_pending)
            .order_by(AgentTrigger.id)
            .with_for_update(key_share=True)
        ).all()
        expired = expire_deliveries(db, now)
        newly_throttled: list[tuple[AgentTrigger, str]] = []
        for trigger in waiting:
            if not (trigger.kind == INGEST and trigger.enabled and trigger.definition_id):
                continue
            if n := shed_backlog(db, trigger):
                logger.warning("agent trigger %s shed %d deliveries over its backlog", trigger.id, n)
                reason = f"more than {trigger.backlog_max} deliveries waiting"
                if throttled(trigger, reason):
                    newly_throttled.append((trigger, reason))
            shed += n
            sends += [(trigger.id, refs) for refs in due_sends(db, trigger, now)]
        db.commit()
        for trigger, reason in newly_throttled:
            tell_throttled(db, trigger, reason)
    for trigger_id, refs in sends:
        send(trigger_id, refs)
    return {"expired": expired, "shed": shed, "sent": len(sends)}
