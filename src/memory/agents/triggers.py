# redis-py types replies as sync|async unions.
# pyright: reportOperatorIssue=false, reportIndexIssue=false
"""Ingest triggers: deterministic filters over newly stored items.

Matching runs on the maintenance queue, so this module must stay importable
by the standard worker image: no fastapi/fastmcp/jsonschema, so nothing from
memory.agents.spec or memory.agents.enqueue. Firing (which calls enqueue_run)
happens on the agents queue, in memory.agents.fire.
"""

import email.utils
import functools
import hashlib
import logging
import re
import signal
import threading
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Annotated, Any, Collection, Iterable, Literal

import redis
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, exists, func, or_, select
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
from memory.common.celery_app import FIRE_TRIGGER, MATCH_INGEST_TRIGGERS, app
from memory.common.collections import SESSION_COLLECTIONS
from memory.common.scopes import SCOPE_AGENTS
from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    AgentTrigger,
    APIKey,
    AgentTriggerMatch,
    BlogPost,
    CalendarEvent,
    Comic,
    DiscordBot,
    DiscordMessage,
    ForumComment,
    ForumPost,
    GithubItem,
    MailMessage,
    SlackMessage,
    SourceItem,
    User,
)
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
ITEM_PREVIEW_CHARS = 500
ITEM_TITLE_CHARS = 200
CLIPPED = "\n[input truncated]"
BUFFER_TTL_SEC = 86_400
# Reprocessing that would re-match an item (re-syncs, upstream edits) mostly
# happens soon after ingest, so claims older than this are purged; items older
# than this never match as new ones, so the purge can't let one fire again.
MATCH_RETENTION = timedelta(days=30)

FilterValues = Annotated[list[str], Field(max_length=MAX_FILTER_VALUES)]


def source_types() -> list[str]:
    """Every SourceItem subtype a filter can name (sessions are owner-private)."""
    return sorted(
        identity
        for identity, mapper in SourceItem.__mapper__.polymorphic_map.items()
        if identity != "source_item"
        and mapper.class_.__name__ not in ("ClaudeSession", "SessionSegment")
    )


class IngestFilter(BaseModel):
    """All set criteria must hold; within a list, any value matches."""

    model_config = ConfigDict(extra="forbid")

    source_types: FilterValues = []
    tags_any: FilterValues = []
    # Discord channel snowflakes or Slack channel ids.
    channel_ids: FilterValues = []
    # Case-insensitive substrings of the item's title or text.
    text_contains: FilterValues = []
    regex: str | None = Field(default=None, max_length=MAX_REGEX_CHARS)
    # Author ids, usernames or email addresses (case-insensitive, exact).
    exclude_authors: FilterValues = []

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
        ):
            raise ValueError(
                "set at least one of source_types, tags_any, channel_ids, "
                "text_contains or regex"
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
    # The run's input; {items} is replaced by one line per matched item.
    template: str = Field(
        default=DEFAULT_TEMPLATE, min_length=1, max_length=MAX_TEMPLATE_CHARS
    )
    # Not part of the filter: it decides which events count, not which items.
    on_edit: bool = False


@dataclass(frozen=True)
class ItemFacts:
    source_type: str
    tags: frozenset[str]
    channel_id: str | None
    # Lowercased author ids, usernames and addresses.
    authors: frozenset[str]
    text: str


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


def content_version(item: SourceItem) -> bytes:
    """What an edit changes, read off the item: not every ingester updates sha256 on an edit.

    Raw content counts as well as the body, as Slack edits leave resolved_content
    as it was. The migration that added claim versions backfills this in SQL.
    """
    parts = (item_title(item) or "", item.content or "", item_body(item))
    return hashlib.sha256("\x1f".join(parts).encode()).digest()


def item_facts(item: SourceItem) -> ItemFacts:
    channel_id, authors = channel_and_authors(item)
    return ItemFacts(
        source_type=item.type or "",
        tags=frozenset(item.tags or []),
        channel_id=channel_id,
        authors=lowered(authors),
        text=item_text(item),
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


def matches(f: IngestFilter, facts: ItemFacts) -> bool:
    if f.source_types and facts.source_type not in f.source_types:
        return False
    if f.tags_any and not facts.tags & set(f.tags_any):
        return False
    if f.channel_ids and facts.channel_id not in f.channel_ids:
        return False
    if facts.authors & lowered(f.exclude_authors):
        return False
    text = facts.text[:MAX_MATCH_TEXT_CHARS].lower()
    if f.text_contains and not any(t.lower() in text for t in f.text_contains):
        return False
    return f.regex is None or regex_search(f.regex, facts.text)


def own_authors(db: DBSession, owner_id: int) -> frozenset[str]:
    """Accounts an agent's own posts come from: any of our Discord bots, and the
    owner's Slack integration user. Matching their items would let an agent
    that posts to a channel re-trigger itself."""
    bots = db.scalars(select(DiscordBot.id)).all()
    slack = db.scalars(
        select(SlackUserCredentials.slack_user_id).where(
            SlackUserCredentials.user_id == owner_id
        )
    ).all()
    return lowered([*bots, *slack])


def trigger_key(db: DBSession, trigger: AgentTrigger, owner: User) -> APIKey | None:
    """The API key a trigger acts through; raises PrincipalRevoked once it can't."""
    if trigger.via_api_key and trigger.api_key_id is None:
        raise PrincipalRevoked("the API key that set up this trigger was deleted")
    return standing_key(db, owner, trigger.api_key_id, SCOPE_AGENTS)


def accessible_ids(
    db: DBSession, owner: User, key: APIKey | None, item_ids: list[int]
) -> list[int]:
    """The subset of item_ids `owner` may read through `key`, by the same filter search uses."""
    reader = UserProxy.from_user(owner, scopes=credential_scopes(owner, key))
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


def enabled_ingest_triggers(db: DBSession) -> list[AgentTrigger]:
    return (
        db.query(AgentTrigger)
        .filter(AgentTrigger.kind == INGEST, AgentTrigger.enabled)
        .order_by(AgentTrigger.id)
        .all()
    )


def any_ingest_trigger(db: DBSession) -> bool:
    return db.query(
        exists().where(AgentTrigger.kind == INGEST, AgentTrigger.enabled)
    ).scalar()


@functools.cache
def buffer_client() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL)


def buffer_key(trigger_id: int) -> str:
    return f"agents:trigger:{trigger_id}:buf"


@dataclass(frozen=True)
class Delivery:
    """A claimed version of an item on its way to a run, as buffers and FIRE_TRIGGER carry it."""

    item_id: int
    version: str
    # The trigger had already matched an earlier version.
    edited: bool

    def encode(self) -> str:
        return f"{self.item_id}:{self.version}:{int(self.edited)}"

    @classmethod
    def decode(cls, raw: str) -> "Delivery":
        item_id, version, edited = raw.split(":")
        return cls(int(item_id), version, edited == "1")


def edited_ids(deliveries: list[Delivery]) -> set[int]:
    """Items delivered only as edits: one whose first version is among them is new to the run."""
    return {d.item_id for d in deliveries if d.edited} - {
        d.item_id for d in deliveries if not d.edited
    }


def dedupe_key(trigger_id: int, deliveries: list[Delivery]) -> str:
    """Keyed on the claimed versions, so an edit's run isn't taken for a redelivery
    and a redelivery after an edit isn't taken for a new run."""
    versions = sorted(f"{d.item_id}:{d.version[:16]}" for d in deliveries)
    if len(versions) == 1:
        return f"{trigger_id}:{versions[0]}"
    digest = hashlib.sha256(",".join(versions).encode()).hexdigest()
    return f"{trigger_id}:batch:{digest[:32]}"


def buffer_item(client: redis.Redis, trigger_id: int, batch: Batch, delivery: Delivery) -> None:
    key = buffer_key(trigger_id)
    client.zadd(key, {delivery.encode(): time.time()}, nx=True)
    # Backstop for buffers of triggers deleted or disabled while holding items.
    client.expire(key, batch.window_sec + BUFFER_TTL_SEC)


def pop_due_batches(
    client: redis.Redis, trigger_id: int, batch: Batch, now: float
) -> list[list[Delivery]]:
    key = buffer_key(trigger_id)
    batches = []
    while oldest := client.zrange(key, 0, 0, withscores=True):
        full = client.zcard(key) >= batch.max_items
        if not full and now - oldest[0][1] < batch.window_sec:
            break
        pipe = client.pipeline(transaction=True)
        pipe.zrange(key, 0, batch.max_items - 1)
        pipe.zremrangebyrank(key, 0, batch.max_items - 1)
        ids, _ = pipe.execute()
        batches.append([Delivery.decode(i.decode()) for i in ids])
    return batches


def may_claim(seen: Collection[bytes], version: bytes, *, fresh: bool, on_edit: bool) -> bool:
    """An unseen item matches only if `fresh`; after that, only with on_edit, once
    per version. An edit of an item the trigger never saw can't be told from a
    reprocess, so it doesn't match."""
    return (on_edit and version not in seen) if seen else fresh


def claim(
    db: DBSession, trigger_id: int, item_id: int, version: bytes, *, fresh: bool, on_edit: bool
) -> Delivery | None:
    """Record that trigger_id matched this version of item_id; None if it may not.

    Items are reprocessed (re-embeds, upstream edits, re-syncs), and each pass
    re-runs matching, so a version claims at most once.
    """
    # Without it, two passes reading different versions could both claim an item.
    lock_key = func.hashtextextended(f"agent_trigger_match:{trigger_id}:{item_id}", 0)
    db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    seen = item_claims(db, item_id).get(trigger_id, set())
    if not may_claim(seen, version, fresh=fresh, on_edit=on_edit):
        db.commit()
        return None
    db.add(AgentTriggerMatch(trigger_id=trigger_id, item_id=item_id, content_hash=version))
    db.commit()
    return Delivery(item_id, version.hex(), edited=bool(seen))


def item_claims(db: DBSession, item_id: int) -> dict[int, set[bytes]]:
    """The versions of item_id each trigger has claimed."""
    query = select(AgentTriggerMatch.trigger_id, AgentTriggerMatch.content_hash).where(
        AgentTriggerMatch.item_id == item_id
    )
    claims: dict[int, set[bytes]] = {}
    for tid, version in db.execute(query):
        claims.setdefault(tid, set()).add(bytes(version))
    return claims


def unclaim(db: DBSession, trigger_id: int, item_id: int, version: bytes) -> None:
    db.query(AgentTriggerMatch).filter(
        AgentTriggerMatch.trigger_id == trigger_id,
        AgentTriggerMatch.item_id == item_id,
        AgentTriggerMatch.content_hash == version,
    ).delete()
    db.commit()


def purge_matches(db: DBSession, older_than: timedelta) -> int:
    """Forget claims older than `older_than`, except each item's newest under an
    on_edit trigger: that one tells its next reprocess from an edit.

    Only a newer claim that is itself past the cutoff supersedes one: a young claim
    may yet be unclaimed by a failed delivery, which would leave the item no claims.
    """
    cutoff = datetime.now(timezone.utc) - older_than
    newer = aliased(AgentTriggerMatch)
    on_edit_triggers = select(AgentTrigger.id).where(
        AgentTrigger.config["on_edit"].as_boolean()
    )
    superseded = exists().where(
        newer.trigger_id == AgentTriggerMatch.trigger_id,
        newer.item_id == AgentTriggerMatch.item_id,
        and_(newer.created_at > AgentTriggerMatch.created_at, newer.created_at < cutoff),
    )
    deleted = (
        db.query(AgentTriggerMatch)
        .filter(
            AgentTriggerMatch.created_at < cutoff,
            or_(AgentTriggerMatch.trigger_id.not_in(on_edit_triggers), superseded),
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


def dispatch(trigger_id: int, deliveries: list[Delivery]) -> None:
    app.send_task(FIRE_TRIGGER, args=[trigger_id, [d.encode() for d in deliveries]])


def match_ingest_triggers(item_id: int, client: redis.Redis | None = None) -> list[int]:
    """Route one stored item to every enabled ingest trigger it matches.

    Returns the matching trigger ids. Per-item triggers fire now; batch triggers
    buffer the item for the sweep.
    """
    with make_session() as db:
        item = db.get(SourceItem, item_id)
        if item is None:
            return []
        triggers = enabled_ingest_triggers(db)
        if not triggers:
            return []
        facts = item_facts(item)
        version = content_version(item)
        # Settles most reprocesses before the costlier checks; claim() re-checks under its lock.
        claims = item_claims(db, item_id)
        oldest = datetime.now(timezone.utc) - MATCH_RETENTION
        matched = []
        for trigger in triggers:
            config = IngestConfig.model_validate(trigger.config)
            # Triggers react to new items, not to the backlog that predates them.
            fresh = item.inserted_at is not None and item.inserted_at >= max(
                trigger.active_since, oldest
            )
            if not may_claim(
                claims.get(trigger.id, set()), version, fresh=fresh, on_edit=config.on_edit
            ):
                continue
            if not matches(config.filter, facts):
                continue
            owner = db.get(User, trigger.definition.user_id)
            if owner is None or facts.authors & own_authors(db, owner.id):
                continue
            # Validity is left to the fire, which trips the breaker if the key is gone.
            key = db.get(APIKey, trigger.api_key_id) if trigger.api_key_id else None
            if not accessible_ids(db, owner, key, [item_id]):
                continue
            delivery = claim(db, trigger.id, item_id, version, fresh=fresh, on_edit=config.on_edit)
            if delivery is None:
                continue
            try:
                if isinstance(config.delivery, Batch):
                    buffer_item(client or buffer_client(), trigger.id, config.delivery, delivery)
                else:
                    dispatch(trigger.id, [delivery])
            except Exception:
                # Unclaim, so a reprocess can match it again once Redis or the broker is back.
                logger.exception("could not deliver item %s to trigger %s", item_id, trigger.id)
                unclaim(db, trigger.id, item_id, version)
                continue
            matched.append(trigger.id)
        return matched


def sweep_trigger_buffers(client: redis.Redis | None = None) -> dict[int, int]:
    """Fire every batch that is due; returns batches fired per trigger."""
    client = client or buffer_client()
    now = time.time()
    fired = {}
    with make_session() as db:
        configs = {
            t.id: IngestConfig.model_validate(t.config).delivery
            for t in enabled_ingest_triggers(db)
        }
    for trigger_id, delivery in configs.items():
        if not isinstance(delivery, Batch):
            continue
        batches = pop_due_batches(client, trigger_id, delivery, now)
        for deliveries in batches:
            dispatch(trigger_id, deliveries)
        if batches:
            fired[trigger_id] = len(batches)
    return fired


def queue_trigger_match(item_id: int) -> None:
    """Hand a freshly stored item to trigger matching without ever failing ingestion."""
    try:
        with make_session() as db:
            if not any_ingest_trigger(db):
                return
        app.send_task(MATCH_INGEST_TRIGGERS, args=[item_id])
    except Exception:
        logger.exception("could not queue trigger matching for item %s", item_id)


def item_line(item: SourceItem, edited: bool) -> str:
    title = " ".join((item_title(item) or "").split())[:ITEM_TITLE_CHARS]
    preview = " ".join(item_body(item).split())[:ITEM_PREVIEW_CHARS]
    label = f"{item.type} #{item.id}" + (", edited" if edited else "")
    return f"- [{label}] {title}: {preview}" if title else f"- [{label}] {preview}"


def render_input(
    template: str, items: list[SourceItem], max_chars: int, edited: Collection[int] = ()
) -> str:
    """Clipped rather than refused: an oversized input would trip the trigger."""
    lines = "\n".join(item_line(i, i.id in edited) for i in items)
    if ITEMS_PLACEHOLDER in template:
        text = template.replace(ITEMS_PLACEHOLDER, lines)
    else:
        text = f"{template}\n\n{lines}"
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(CLIPPED)] + CLIPPED
