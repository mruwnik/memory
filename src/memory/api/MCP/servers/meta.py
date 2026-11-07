"""MCP subserver for metadata and utility tools."""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, TypedDict, get_args, get_type_hints

import sqlalchemy as sa
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from memory.api.MCP.servers.core import (
    SEARCH_FILTERS,
    SEARCHABLE_COLLECTIONS,
    get_current_user_access_filter,
)
from memory.agents.system import CHUNK_SCORE, HYDE, QUERY_ANALYSIS
from memory.agents.clock import Notify, add_timer
from memory.agents.mcp_client import calling_run
from memory.api.auth import lookup_principal, token_api_key
from memory.api.MCP.access import get_mcp_current_user
from memory.api.MCP.visibility import READ_ONLY, require_scopes, visible_when
from memory.api.search.search import searcher_keys
from memory.api.search.filters import FILTER_REGISTRY, QdrantUnsupported
from memory.api.search.types import MCPSearchFilters
from memory.common.access_control import apply_access_filter_to_query
from memory.common import embedding, qdrant
from memory.common.instance_settings import (
    ENABLE_BM25_SEARCH,
    ENABLE_HYDE_EXPANSION,
    ENABLE_QUERY_ANALYSIS,
    ENABLE_RERANKING,
    ENABLE_SEARCH_SCORING,
    value as setting,
)
from memory.common.collections import SESSION_COLLECTIONS
from memory.common.dates import parse_wall_clock_utc
from memory.common.celery_app import SEND_NOTIFICATION
from memory.common.celery_app import app as celery_app
from memory.common.db.connection import DBSession, make_session
from memory.common.scopes import SCOPE_READ, SCOPE_WRITE
from memory.common.notify import (
    format_notification_message,
    get_notification_channel,
    user_info_for,
)
from memory.common.version import db_revision, version_info
from memory.common.db.models import SourceItem

logger = logging.getLogger(__name__)

meta_mcp = FastMCP("memory-meta")


def _get_current_user(session: DBSession) -> dict:
    """Get the current authenticated user from the access token."""
    access_token = get_access_token()
    if not access_token:
        return {"authenticated": False}
    principal = lookup_principal(access_token.token, session, record_use=False)
    if principal is None:
        return {"authenticated": False, "error": "User not found"}

    user = principal.user
    scopes = list(access_token.scopes or [])
    return {
        "authenticated": True,
        "token_type": "Bearer",
        "scopes": scopes,
        "client_id": access_token.client_id,
        "user": user_info_for(session, user),
    }


# --- Metadata tools ---


class SchemaArg(TypedDict):
    type: str | None
    description: str | None


class CollectionMetadata(TypedDict):
    schema: dict[str, SchemaArg]
    filters: dict[str, SchemaArg]
    size: int


def from_annotation(annotation: Annotated) -> SchemaArg | None:
    try:
        type_, description = get_args(annotation)
    except (IndexError, ValueError):
        logger.error(f"Error from annotation: {annotation}")
        return None
    type_str = str(type_)
    if type_str.startswith("typing."):
        type_str = type_str[7:]
    elif len((parts := type_str.split("'"))) > 1:
        type_str = parts[1]
    return SchemaArg(type=type_str, description=description)


def get_schema(klass: type[SourceItem]) -> dict[str, SchemaArg]:
    if not hasattr(klass, "as_payload"):
        return {}

    if not (payload_type := get_type_hints(klass.as_payload).get("return")):
        return {}

    # include_extras=True preserves Annotated[T, "desc"]; without it (or with raw
    # __annotations__) the model files' `from __future__ import annotations`
    # leaves us with ForwardRef strings whose get_args() returns ().
    return {
        name: schema
        for name, arg in get_type_hints(payload_type, include_extras=True).items()
        if (schema := from_annotation(arg))
    }


SEARCH_FILTER_DESCRIPTIONS = {name: description for name, description, _ in SEARCH_FILTERS}


def type_name(type_: Any) -> str:
    return str(type_) if get_args(type_) else type_.__name__


def search_filters(collection: str) -> dict[str, SchemaArg]:
    """The `search` filters that can narrow results in ``collection``.

    Filters without a vector-search translation are left out: `search` would
    reject them.
    """
    types = get_type_hints(MCPSearchFilters)
    registry_keys = [
        spec.key
        for spec in FILTER_REGISTRY.values()
        if not isinstance(spec.qdrant_op, QdrantUnsupported)
        and (spec.model is None or collection in spec.model.get_collections())
    ]
    # Outside the registry, but `search` applies them to every collection.
    created_at_keys = ["min_created_at", "max_created_at"]
    return {
        key: SchemaArg(type=type_name(types[key]), description=SEARCH_FILTER_DESCRIPTIONS[key])
        for key in registry_keys + created_at_keys
    }


@meta_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_READ))
async def get_metadata_schemas() -> dict[str, CollectionMetadata]:
    """Get the metadata schema for each collection used in the knowledge base.

    These schemas can be used to filter the knowledge base.

    Returns: A mapping of collection names to their metadata schema, the
    `search` filters that apply to them, and their size.

    Example:
    ```
    {
        "mail": {
            "schema": {"subject": {"type": "str", "description": "The subject of the email."}},
            "filters": {"sender": {"type": "str", "description": "..."}},
            "size": 1200
        }
    }
    """
    client = qdrant.get_qdrant_client()
    sizes = qdrant.get_collection_sizes(client)
    schemas = defaultdict(dict)
    for klass in SourceItem.__subclasses__():
        for collection in klass.get_collections():
            schemas[collection].update(get_schema(klass))

    return {
        collection: CollectionMetadata(
            schema=schema, filters=search_filters(collection), size=size
        )
        for collection, schema in schemas.items()
        # Session transcripts are owner-only-even-for-admins and only
        # reachable via claude_session_search; don't advertise their
        # schema or the cross-user point count here.
        if collection not in SESSION_COLLECTIONS and (size := sizes.get(collection))
    }


@meta_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_READ))
async def get_search_defaults() -> dict[str, bool]:
    """The search enhancements available to you.

    An available (true) enhancement runs unless `search`'s matching `use_*` flag
    is false; `use_scores` is opt-in. A false one is off on this server, or is an
    LLM step and you have no default Anthropic or OpenAI key to pay for it, and no
    flag can turn it on.

    `semantic_search_unavailable` is true while no embeddings key is set: `search`
    then matches keywords only.
    """
    user = get_mcp_current_user()
    keys = await asyncio.to_thread(searcher_keys, user.id if user else None)
    embeds = embedding.configured()
    return {
        "use_bm25": not embeds or setting(ENABLE_BM25_SEARCH),
        "use_hyde": embeds and setting(ENABLE_HYDE_EXPANSION) and keys[HYDE.slug] is not None,
        "use_reranking": embeds and setting(ENABLE_RERANKING),
        "use_query_analysis": setting(ENABLE_QUERY_ANALYSIS)
        and keys[QUERY_ANALYSIS.slug] is not None,
        "use_scores": setting(ENABLE_SEARCH_SCORING) and keys[CHUNK_SCORE.slug] is not None,
        "semantic_search_unavailable": not embeds,
    }


@meta_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_READ))
async def get_tags() -> list[str]:
    """List the tags on items you can search, for the `tags` filter of `search`."""
    access_filter = get_current_user_access_filter()
    tag = sa.func.unnest(SourceItem.tags)
    with make_session() as session:
        query = session.query(tag).filter(
            SourceItem.embed_status == "STORED",
            SourceItem.modality.in_(SEARCHABLE_COLLECTIONS),
        )
        query = apply_access_filter_to_query(query, access_filter)
        return sorted({t for (t,) in query.distinct()})


@meta_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_READ))
async def get_current_time() -> dict:
    """Get the current time in UTC."""
    logger.info("get_current_time tool called")
    return {"current_time": datetime.now(timezone.utc).isoformat()}


@meta_mcp.tool(annotations=READ_ONLY)
@visible_when(require_scopes(SCOPE_READ))
async def get_user() -> dict:
    """Get information about the authenticated user and the server's version."""
    with make_session() as session:
        user = _get_current_user(session)
        db_rev = db_revision(session)
    if user["authenticated"]:
        user["server"] = await version_info(db_rev)
    return user


# --- Notification tools ---


@meta_mcp.tool()
@visible_when(require_scopes(SCOPE_WRITE))
async def notify_user(
    subject: str,
    message: str,
    details_url: str | None = None,
    channel: Literal["discord", "slack", "email"] | None = None,
    scheduled_time: str | None = None,
) -> dict[str, Any]:
    """
    Send a notification to the current user.

    Delivery priority (unless overridden by channel parameter):
    1. Discord DM (if user has Discord account linked)
    2. Slack DM (if user has Slack account linked)
    3. Email (if user has email account configured)

    Args:
        subject: Notification subject/title (shown as bold header)
        message: Main message content
        details_url: Optional URL to full details (e.g., link to a note)
        channel: Override notification channel. If None, uses priority order above.
        scheduled_time: ISO datetime string (e.g., "2024-12-20T15:30:00Z").
                       Without an offset it is read in your timezone (see get_user).
                       If None, sends immediately. If set, a timer sends it then
                       (cancel it with scheduler_delete(timer_id=notification_id)).

    Returns:
        For a scheduled send: ``{"success": True, "scheduled": True, ...}`` — the
        timer is durably committed.
        For an immediate send: ``{"queued": True, "scheduled": False, ...}`` —
        the delivery task is enqueued but NOT confirmed delivered (fire-and-forget,
        no row/metric to read back).
    """
    if not subject:
        raise ValueError("Subject is required")
    if not message:
        raise ValueError("Message is required")

    with make_session() as session:
        current_user = _get_current_user(session)

        if not current_user.get("authenticated"):
            raise ValueError("Not authenticated")

        user_id = current_user.get("user", {}).get("user_id")
        if not user_id:
            raise ValueError("User not found")

        # Determine channel
        channel_info = get_notification_channel(current_user, channel)
        if not channel_info:
            if channel:
                raise ValueError(f"{channel.title()} account not configured")
            raise ValueError("No notification channel available (Discord, Slack, or Email)")

        channel_type, channel_identifier, extra_data = channel_info
        full_message = format_notification_message(subject, message, details_url)

        if scheduled_time:
            # Content a run read must not be able to plant work that outlives it.
            if calling_run(get_access_token()) is not None:
                raise ValueError("an agent run can't set timers; notify now instead")
            scheduled_dt = parse_wall_clock_utc(scheduled_time, current_user["user"]["timezone"])
            if scheduled_dt is None:
                raise ValueError("Invalid datetime format for scheduled_time")

            notify = Notify.model_validate(
                {
                    "channel": channel_type,
                    "target": channel_identifier,
                    "message": full_message,
                    "subject": subject,
                    "discord_bot_id": extra_data.get("discord_bot_id"),
                    "from_address": extra_data.get("from_address"),
                }
            )
            token = get_access_token()
            key = token_api_key(token.token, session) if token else None
            notification = add_timer(
                session,
                user_id,
                scheduled_dt.replace(tzinfo=timezone.utc),
                {"notify": notify.model_dump(exclude_none=True)},
                key.id if key else None,
            )
            session.commit()
            return {
                "success": True,
                "scheduled": True,
                "notification_id": notification.id,
                "channel_type": channel_type,
                "scheduled_time": scheduled_dt.isoformat() + "Z",
            }

    # Immediate send: fire-and-forget, no timer row.
    celery_app.send_task(
        SEND_NOTIFICATION,
        args=[channel_type, channel_identifier, full_message, user_id, subject, extra_data],
    )
    # "queued", not "success": the celery task is enqueued but delivery is not
    # confirmed here. There is no row/metric to read back, so the caller can't
    # learn whether the send actually landed — don't imply it did.
    return {
        "queued": True,
        "scheduled": False,
        "notification_id": None,
        "channel_type": channel_type,
        "scheduled_time": None,
    }


