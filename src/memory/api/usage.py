"""LLM usage: memory's own calls (llm_spend), agent runs, and Claude Code telemetry."""

import re
from datetime import datetime, timedelta, timezone
from functools import reduce
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import (
    BigInteger,
    ColumnElement,
    Integer,
    Numeric,
    Select,
    Text,
    case,
    exists,
    func,
    literal,
    null,
    true,
    or_,
    select,
    union_all,
)
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import joinedload

from memory.api.auth import require_rest_access, resolve_user_filter
from memory.common.access_control import has_admin_scope
from memory.common.db.connection import get_session
from memory.common.db.models import (
    AgentDefinition,
    AgentRun,
    APIKey,
    LLMProviderKey,
    LLMSpend,
    Session,
    TelemetryEvent,
    User,
)

router = APIRouter(prefix="/usage", tags=["usage"])

GroupBy = Literal["user", "key", "agent", "model", "kind"]
Interval = Literal["hour", "day", "week", "month"]
GROUP_COLUMNS: dict[str, str] = {
    "user": "user_id",
    "key": "key_id",
    "agent": "agent",
    "model": "model",
    "kind": "kind",
}
SHARED_KEY_LABEL = "shared key"
TOKEN_COLUMNS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
# Claude Code's token.usage `type` attribute, per token column
CLAUDE_CODE_TOKEN_TYPES = {
    "input_tokens": "input",
    "output_tokens": "output",
    "cache_read_tokens": "cacheRead",
    "cache_write_tokens": "cacheCreation",
}

MAX_SESSION_LABEL_CHARS = 40
FIRST_CLAUSE = re.compile(r"[.;:!?](?:\s|$)")
LEADING_PUNCTUATION = re.compile(r"^[\W_]+")
MAX_SESSIONS = 200


def visible(user: User, owner, key=None) -> ColumnElement[bool]:
    """Rows attributed to the user or spent on their own provider keys; all rows for admins."""
    if has_admin_scope(user):
        return true()
    if key is None:
        return owner == user.id
    return or_(owner == user.id, key.in_(own_keys(user)))


def own_keys(user: User) -> Select:
    return select(LLMProviderKey.id).where(LLMProviderKey.user_id == user.id)


def hour_of(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def time_window(
    from_time: datetime | None, to_time: datetime | None, default: timedelta
) -> tuple[datetime, datetime]:
    to_time = to_time or datetime.now(timezone.utc)
    return from_time or to_time - default, to_time


def spend_rows() -> Select:
    tokens: list[ColumnElement[int]] = [getattr(LLMSpend, c) for c in TOKEN_COLUMNS]
    return select(
        LLMSpend.hour.label("ts"),
        LLMSpend.user_id.label("user_id"),
        LLMSpend.provider_key_id.label("key_id"),
        LLMSpend.agent.label("agent"),
        LLMSpend.model.label("model"),
        LLMSpend.kind.label("kind"),
        LLMSpend.cost_usd.label("cost_usd"),
        *(t.label(c) for t, c in zip(tokens, TOKEN_COLUMNS)),
        reduce(lambda a, b: a + b, tokens).label("tokens"),
    )


def run_rows() -> Select:
    tokens = [func.coalesce(AgentRun.usage[c].astext.cast(BigInteger), 0) for c in TOKEN_COLUMNS]
    return select(
        AgentRun.created_at.label("ts"),
        AgentRun.user_id.label("user_id"),
        AgentRun.provider_key_id.label("key_id"),
        func.coalesce(AgentRun.agent, AgentDefinition.name, "inline").label("agent"),
        AgentRun.spec["model"].astext.label("model"),
        literal("run", Text).label("kind"),
        AgentRun.cost_usd.label("cost_usd"),
        *(t.label(c) for t, c in zip(tokens, TOKEN_COLUMNS)),
        reduce(lambda a, b: a + b, tokens).label("tokens"),
    ).outerjoin(AgentDefinition, AgentDefinition.id == AgentRun.definition_id)


def claude_code_rows() -> Select:
    is_tokens = TelemetryEvent.name == "token.usage"
    token_type = TelemetryEvent.attributes["type"].astext
    value = func.coalesce(TelemetryEvent.value, 0)

    def tokens(condition: ColumnElement[bool]) -> ColumnElement[int]:
        return case((condition, value), else_=0).cast(BigInteger)

    return select(
        TelemetryEvent.timestamp.label("ts"),
        TelemetryEvent.user_id.label("user_id"),
        null().cast(Integer).label("key_id"),
        null().cast(Text).label("agent"),
        TelemetryEvent.source.label("model"),
        literal("claude-code", Text).label("kind"),
        case((TelemetryEvent.name == "cost.usage", value), else_=0).cast(Numeric(12, 6)).label("cost_usd"),
        *(
            tokens(is_tokens & (token_type == CLAUDE_CODE_TOKEN_TYPES[c])).label(c)
            for c in TOKEN_COLUMNS
        ),
        tokens(is_tokens).label("tokens"),
    ).where(TelemetryEvent.name.in_(["token.usage", "cost.usage"]))


@router.get("")
def get_usage(
    group_by: list[GroupBy] = Query(default=[], description="Split the series by any of these"),
    interval: Interval = Query("day", description="Bucket size"),
    from_time: datetime | None = Query(None, alias="from", description="Start time (ISO format)"),
    to_time: datetime | None = Query(None, alias="to", description="End time (ISO format), exclusive"),
    user_id: int | None = Query(None, description="Only usage attributed to this user"),
    user: User = Depends(require_rest_access),
    db: DBSession = Depends(get_session),
) -> dict:
    """
    LLM cost and tokens over time, from memory's own calls (kind `memory`, agent is
    the system agent slug), agent runs (`run`, including purged runs kept in
    llm_spend) and Claude Code telemetry (`claude-code`). Claude Code's cost is
    the client's own estimate, not what the provider billed.

    You see usage on your own provider keys and usage attributed to you, including
    on keys that aren't yours (e.g. the shared system key), whose labels you don't
    see. Admins see everything.
    """
    from_time, to_time = time_window(from_time, to_time, timedelta(days=30))
    group_by = list(dict.fromkeys(group_by))

    def scoped(rows: Select, ts, start: datetime, owner, key=None) -> Select:
        filters = [ts >= start, ts < to_time, visible(user, owner, key)]
        if user_id is not None:
            filters.append(owner == user_id)
        return rows.where(*filters)

    rows = union_all(
        # Spend is bucketed hourly: the bucket `from` falls in counts whole.
        scoped(spend_rows(), LLMSpend.hour, hour_of(from_time), LLMSpend.user_id, LLMSpend.provider_key_id),
        scoped(run_rows(), AgentRun.created_at, from_time, AgentRun.user_id, AgentRun.provider_key_id),
        scoped(claude_code_rows(), TelemetryEvent.timestamp, from_time, TelemetryEvent.user_id),
    ).subquery()
    bucket = func.date_trunc(literal(interval, Text), rows.c.ts, literal("UTC", Text))
    groups = [rows.c[GROUP_COLUMNS[g]] for g in group_by]
    sums = [func.sum(rows.c[c]).label(c) for c in (*TOKEN_COLUMNS, "tokens")]
    results = db.execute(
        select(bucket.label("bucket"), *groups, func.sum(rows.c.cost_usd).label("cost_usd"), *sums)
        .group_by(bucket, *groups)
        .order_by(bucket)
    ).all()

    data = [
        {
            "timestamp": r.bucket.isoformat(),
            **dict(zip(group_by, r[1 : 1 + len(group_by)])),
            "cost_usd": float(r.cost_usd),
            **{c: int(getattr(r, c)) for c in (*TOKEN_COLUMNS, "tokens")},
        }
        for r in results
    ]

    def ids(field: str) -> set[int]:
        return {row[field] for row in data if row.get(field) is not None}

    users = db.execute(select(User.id, User.name).where(User.id.in_(ids("user"))))
    keys = db.execute(
        select(LLMProviderKey.id, LLMProviderKey.label, LLMProviderKey.user_id).where(
            LLMProviderKey.id.in_(ids("key"))
        )
    )
    admin = has_admin_scope(user)

    return {
        "from": from_time.isoformat(),
        "to": to_time.isoformat(),
        "interval": interval,
        "group_by": group_by,
        "data": data,
        "users": {id_: name for id_, name in users},
        "keys": {
            id_: label if admin or owner == user.id else SHARED_KEY_LABEL
            for id_, label, owner in keys
        },
    }


@router.get("/raw")
def get_raw_events(
    event_type: str | None = Query(None, description="Filter by type (metric or log)"),
    name: str | None = Query(None, description="Filter by event name"),
    session_id: str | None = Query(None, description="Filter by session ID"),
    source: str | None = Query(None, description="Filter by source (e.g., model name)"),
    from_time: datetime | None = Query(None, alias="from", description="Start time (ISO format)"),
    to_time: datetime | None = Query(None, alias="to", description="End time (ISO format), exclusive"),
    api_key_id: int | None = Query(None, description="Filter by the API key that sent the events"),
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    user_id: int | None = Query(None, description="Filter by user ID (admin only, omit for all users)"),
    user: User = Depends(require_rest_access),
    db: DBSession = Depends(get_session),
) -> dict:
    """
    Query raw telemetry events for debugging and detailed analysis.

    By default returns events for the authenticated user only.
    Admins (users with '*' or 'admin' scope) can:
    - Omit user_id to see all users' events
    - Specify user_id to filter to a specific user
    """
    resolved_user_id = resolve_user_filter(user_id, user, db)
    from_time, to_time = time_window(from_time, to_time, timedelta(hours=24))

    query = (
        db.query(TelemetryEvent)
        .filter(TelemetryEvent.timestamp >= from_time)
        .filter(TelemetryEvent.timestamp < to_time)
    )

    if resolved_user_id is not None:
        query = query.filter(TelemetryEvent.user_id == resolved_user_id)

    if event_type:
        query = query.filter(TelemetryEvent.event_type == event_type)
    if name:
        query = query.filter(TelemetryEvent.name == name)
    if session_id:
        query = query.filter(TelemetryEvent.session_id == session_id)
    if source:
        query = query.filter(TelemetryEvent.source == source)
    if api_key_id is not None:
        query = query.filter(TelemetryEvent.api_key_id == api_key_id)

    total = query.count()

    events = (
        query.order_by(TelemetryEvent.timestamp.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "from": from_time.isoformat(),
        "to": to_time.isoformat(),
        "events": [
            {
                "id": e.id,
                "timestamp": e.timestamp.isoformat(),
                "event_type": e.event_type,
                "name": e.name,
                "value": e.value,
                "session_id": e.session_id,
                "source": e.source,
                "tool_name": e.tool_name,
                "api_key_id": e.api_key_id,
                "attributes": e.attributes,
                "body": e.body[:500] if e.body else None,
            }
            for e in events
        ],
    }


VALID_GROUP_BY_COLUMNS = {
    "source",
    "tool_name",
    "session_id",
    "event_type",
    "name",
    "user_id",
    "api_key_id",
}


@router.get("/metrics")
def get_aggregated_metrics(
    metric: str = Query(..., description="Metric name (e.g., token.usage, cost.usage)"),
    granularity: int = Query(60, ge=1, le=1440, description="Time bucket size in minutes"),
    from_time: datetime | None = Query(None, alias="from", description="Start time (ISO format)"),
    to_time: datetime | None = Query(None, alias="to", description="End time (ISO format), exclusive"),
    source: str | None = Query(None, description="Filter by source"),
    group_by: list[str] = Query(
        default=["source", "tool_name"],
        description="Fields to group by: source, tool_name, session_id, event_type, name, user_id, api_key_id, or attributes.<key>",
    ),
    api_key_id: int | None = Query(None, description="Filter by the API key that sent the events"),
    user_id: int | None = Query(None, description="Filter by user ID (admin only, omit for all users)"),
    user: User = Depends(require_rest_access),
    db: DBSession = Depends(get_session),
) -> dict:
    """
    Query aggregated metrics over time.

    Returns time series data suitable for charting.
    Aggregates are computed from raw events on-the-fly.

    The group_by parameter supports both column names (source, tool_name, session_id,
    event_type, name, user_id) and JSONB attribute keys using the format "attributes.<key>".

    Admins (users with '*' or 'admin' scope) can:
    - Omit user_id to see aggregated metrics across all users
    - Specify user_id to filter to a specific user
    - Use 'user_id' in group_by to see per-user breakdowns
    """
    resolved_user_id = resolve_user_filter(user_id, user, db)
    from_time, to_time = time_window(from_time, to_time, timedelta(days=7))

    if granularity >= 1440:
        trunc_interval = "day"
    elif granularity >= 60:
        trunc_interval = "hour"
    else:
        trunc_interval = "minute"

    trunc_func = func.date_trunc(trunc_interval, TelemetryEvent.timestamp, "UTC")

    group_columns = [trunc_func]
    select_columns = [
        trunc_func.label("bucket"),
        func.count().label("count"),
        func.sum(TelemetryEvent.value).label("sum_value"),
        func.min(TelemetryEvent.value).label("min_value"),
        func.max(TelemetryEvent.value).label("max_value"),
    ]

    group_by_fields = []

    for field in group_by:
        if field in VALID_GROUP_BY_COLUMNS:
            col = getattr(TelemetryEvent, field)
            select_columns.append(col)
            group_columns.append(col)
            group_by_fields.append(field)
        elif field.startswith("attributes."):
            attr_key = field.removeprefix("attributes.")
            json_col = TelemetryEvent.attributes[attr_key].astext.label(f"attr_{attr_key}")
            select_columns.append(json_col)
            group_columns.append(TelemetryEvent.attributes[attr_key].astext)
            group_by_fields.append(field)

    query = (
        db.query(*select_columns)
        .filter(TelemetryEvent.name == metric)
        .filter(TelemetryEvent.timestamp >= from_time)
        .filter(TelemetryEvent.timestamp < to_time)
    )

    if resolved_user_id is not None:
        query = query.filter(TelemetryEvent.user_id == resolved_user_id)

    if source:
        query = query.filter(TelemetryEvent.source == source)
    if api_key_id is not None:
        query = query.filter(TelemetryEvent.api_key_id == api_key_id)

    results = (
        query.group_by(*group_columns)
        .order_by(trunc_func)
        .all()
    )

    data = []
    for r in results:
        row = {
            "timestamp": r.bucket.isoformat() if r.bucket else None,
            "count": r.count,
            "sum": r.sum_value,
            "min": r.min_value,
            "max": r.max_value,
        }
        row.update(zip(group_by_fields, r[5:]))
        data.append(row)

    return {
        "metric": metric,
        "granularity_minutes": granularity,
        "from": from_time.isoformat(),
        "to": to_time.isoformat(),
        "group_by": group_by_fields,
        "data": data,
    }


class UsageUser(BaseModel):
    id: int
    name: str
    email: str


@router.get("/users", response_model=list[UsageUser])
def get_users_with_usage(
    user: User = Depends(require_rest_access),
    db: DBSession = Depends(get_session),
) -> list[UsageUser]:
    """Users with usage you can see: spend attributed to them, runs, or telemetry."""
    with_usage = union_all(
        select(LLMSpend.user_id).where(visible(user, LLMSpend.user_id, LLMSpend.provider_key_id)),
        select(AgentRun.user_id).where(visible(user, AgentRun.user_id, AgentRun.provider_key_id)),
        select(TelemetryEvent.user_id).where(visible(user, TelemetryEvent.user_id)),
    )
    users = db.query(User).filter(User.id.in_(with_usage)).order_by(User.id)
    return [UsageUser(id=u.id, name=u.name or "", email=u.email or "") for u in users]


def session_label(
    session_id: str,
    summary: str | None,
    project_dir: str | None,
    started_at: datetime | None,
) -> str:
    """A short bar label: the summary's first clause, else project and date, else the id prefix."""
    first_line = LEADING_PUNCTUATION.sub("", summary or "").split("\n", 1)[0]
    clause = FIRST_CLAUSE.split(first_line, maxsplit=1)[0].strip()
    if clause and len(clause) <= MAX_SESSION_LABEL_CHARS:
        return clause
    if clause:
        cut = clause[: MAX_SESSION_LABEL_CHARS + 1].rsplit(" ", 1)[0]
        return cut[:MAX_SESSION_LABEL_CHARS] + "…"
    if project_dir:
        name = PurePosixPath(project_dir).name
        return f"{name} · {started_at.date().isoformat()}" if started_at else name
    return session_id[:8]


def parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


class SessionUsage(BaseModel):
    session_id: str
    label: str
    summary: str | None
    project: str | None
    total_tokens: float
    total_cost: float
    event_count: int
    first_seen: str
    last_seen: str


class SessionUsageResponse(BaseModel):
    from_time: str
    to_time: str
    sessions: list[SessionUsage]


@router.get("/sessions", response_model=SessionUsageResponse)
def get_session_usage(
    from_time: datetime | None = Query(None, alias="from", description="Start time (ISO format)"),
    to_time: datetime | None = Query(None, alias="to", description="End time (ISO format), exclusive"),
    api_key_id: int | None = Query(None, description="Filter by the API key that sent the events"),
    user_id: int | None = Query(None, description="Filter by user ID (admin only, omit for all users)"),
    user: User = Depends(require_rest_access),
    db: DBSession = Depends(get_session),
) -> SessionUsageResponse:
    """
    Token and cost totals per Claude Code session, largest first.

    Each session is named from its stored summary or project. Names come only
    from sessions the caller owns, matching the sessions API, so an admin
    viewing other users' telemetry sees their session ids but not their summaries.
    """
    resolved_user_id = resolve_user_filter(user_id, user, db)
    from_time, to_time = time_window(from_time, to_time, timedelta(days=7))

    is_tokens = TelemetryEvent.name == "token.usage"
    is_cost = TelemetryEvent.name == "cost.usage"
    tokens = func.coalesce(func.sum(TelemetryEvent.value).filter(is_tokens), 0)
    query = (
        db.query(
            TelemetryEvent.session_id,
            tokens.label("tokens"),
            func.coalesce(func.sum(TelemetryEvent.value).filter(is_cost), 0).label("cost"),
            func.count().filter(is_tokens).label("events"),
            func.min(TelemetryEvent.timestamp).label("first_seen"),
            func.max(TelemetryEvent.timestamp).label("last_seen"),
        )
        .filter(TelemetryEvent.name.in_(["token.usage", "cost.usage"]))
        .filter(TelemetryEvent.session_id.isnot(None))
        .filter(TelemetryEvent.timestamp >= from_time)
        .filter(TelemetryEvent.timestamp < to_time)
    )
    if resolved_user_id is not None:
        query = query.filter(TelemetryEvent.user_id == resolved_user_id)
    if api_key_id is not None:
        query = query.filter(TelemetryEvent.api_key_id == api_key_id)
    rows = (
        query.group_by(TelemetryEvent.session_id)
        .order_by(tokens.desc())
        .limit(MAX_SESSIONS)
        .all()
    )

    ids = {uuid for r in rows if (uuid := parse_uuid(r.session_id))}
    owned = {
        str(s.id): s
        for s in db.query(Session)
        .options(joinedload(Session.coding_project))
        .filter(Session.id.in_(ids), Session.user_id == user.id)
    }

    def usage(row) -> SessionUsage:
        session = owned.get(row.session_id)
        summary = session.summary if session else None
        project = (
            session.coding_project.directory if session and session.coding_project else None
        )
        started_at = session.started_at if session else None
        return SessionUsage(
            session_id=row.session_id,
            label=session_label(row.session_id, summary, project, started_at),
            summary=summary,
            project=project,
            total_tokens=row.tokens,
            total_cost=row.cost,
            event_count=row.events,
            first_seen=row.first_seen.isoformat(),
            last_seen=row.last_seen.isoformat(),
        )

    return SessionUsageResponse(
        from_time=from_time.isoformat(),
        to_time=to_time.isoformat(),
        sessions=[usage(r) for r in rows],
    )


class TelemetryAPIKey(BaseModel):
    id: int
    name: str | None
    key_type: str
    user_id: int
    revoked: bool


@router.get("/api-keys", response_model=list[TelemetryAPIKey])
def get_api_keys_with_telemetry(
    user_id: int | None = Query(None, description="Filter by key owner (admin only, omit for all users)"),
    user: User = Depends(require_rest_access),
    db: DBSession = Depends(get_session),
) -> list[TelemetryAPIKey]:
    """API keys that have sent at least one telemetry event, for the key filter."""
    resolved_user_id = resolve_user_filter(user_id, user, db)
    query = db.query(APIKey).filter(exists().where(TelemetryEvent.api_key_id == APIKey.id))
    if resolved_user_id is not None:
        query = query.filter(APIKey.user_id == resolved_user_id)
    return [
        TelemetryAPIKey(
            id=k.id, name=k.name, key_type=k.key_type, user_id=k.user_id, revoked=k.revoked
        )
        for k in query.order_by(APIKey.id)
    ]


class ToolCallStats(BaseModel):
    """Per-call statistics for token usage."""

    median: float
    p75: float
    p90: float
    p99: float
    min: int
    max: int


class ToolUsageStats(BaseModel):
    """Token usage statistics for a single tool."""

    tool_name: str
    call_count: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    total_tokens: int
    per_call: ToolCallStats | None = None


class ToolUsageResponse(BaseModel):
    """Aggregated tool usage statistics."""

    from_time: str
    to_time: str
    session_count: int
    tools: list[ToolUsageStats]


def attr_tokens(key: str):
    return func.coalesce(func.sum(TelemetryEvent.attributes[key].as_integer()), 0)


def per_call_percentile(fraction: float):
    return func.percentile_cont(fraction).within_group(TelemetryEvent.value)


@router.get("/tools", response_model=ToolUsageResponse)
def get_tool_usage_stats(
    from_time: datetime | None = Query(None, alias="from", description="Start time"),
    to_time: datetime | None = Query(None, alias="to", description="End time, exclusive"),
    user_id: int | None = Query(None, description="Filter by user ID (admin only, omit for all users)"),
    user: User = Depends(require_rest_access),
    db: DBSession = Depends(get_session),
) -> ToolUsageResponse:
    """
    Get aggregated token usage statistics by tool, from tool.token.usage telemetry events.

    Admins (users with '*' or 'admin' scope) can:
    - Omit user_id to see tool usage across all users
    - Specify user_id to filter to a specific user's sessions
    """
    resolved_user_id = resolve_user_filter(user_id, user, db)
    from_time, to_time = time_window(from_time, to_time, timedelta(days=7))

    filters = [
        TelemetryEvent.name == "tool.token.usage",
        TelemetryEvent.timestamp >= from_time,
        TelemetryEvent.timestamp < to_time,
    ]
    if resolved_user_id is not None:
        filters.append(TelemetryEvent.user_id == resolved_user_id)

    tool_name = func.coalesce(TelemetryEvent.tool_name, "unknown")
    rows = (
        db.query(
            tool_name.label("tool_name"),
            func.count().label("call_count"),
            attr_tokens("input_tokens").label("input_tokens"),
            attr_tokens("output_tokens").label("output_tokens"),
            attr_tokens("cache_read_input_tokens").label("cache_read_tokens"),
            attr_tokens("cache_creation_input_tokens").label("cache_creation_tokens"),
            per_call_percentile(0.5).label("median"),
            per_call_percentile(0.75).label("p75"),
            per_call_percentile(0.9).label("p90"),
            per_call_percentile(0.99).label("p99"),
            func.min(TelemetryEvent.value).label("min"),
            func.max(TelemetryEvent.value).label("max"),
        )
        .filter(*filters)
        .group_by(tool_name)
        .all()
    )
    session_count = (
        db.query(func.count(func.distinct(TelemetryEvent.session_id))).filter(*filters).scalar()
    )

    def stats(r) -> ToolUsageStats:
        tokens = (r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_creation_tokens)
        return ToolUsageStats(
            tool_name=r.tool_name,
            call_count=r.call_count,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cache_read_tokens=r.cache_read_tokens,
            cache_creation_tokens=r.cache_creation_tokens,
            total_tokens=sum(tokens),
            per_call=ToolCallStats(
                median=r.median, p75=r.p75, p90=r.p90, p99=r.p99, min=int(r.min or 0), max=int(r.max or 0)
            ),
        )

    return ToolUsageResponse(
        from_time=from_time.isoformat(),
        to_time=to_time.isoformat(),
        session_count=session_count or 0,
        tools=sorted(map(stats, rows), key=lambda t: t.total_tokens, reverse=True),
    )
