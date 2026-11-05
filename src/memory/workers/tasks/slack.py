"""
Celery tasks for Slack message syncing and processing.

This module provides tasks for:
- Syncing all Slack workspaces (periodic task)
- Syncing individual workspaces (channels, users, messages)
- Processing individual messages

Note: User data is not stored in a separate SlackUser table. Instead:
- For mention resolution, we cache user info from the Slack API during sync
- For linking users to People, store Slack IDs in Person.contact_info["slack"]
"""

import contextlib
import hashlib
import json
import logging
import os
import pathlib
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import redis
from sqlalchemy.exc import IntegrityError, OperationalError

from memory.common import paths, settings
from memory.common.redis_lock import distributed_lock
from memory.common.downloads import stream_download_to_path
from memory.common.celery_app import (
    ADD_SLACK_MESSAGE,
    MARK_SLACK_MESSAGE_DELETED,
    SYNC_ALL_SLACK_WORKSPACES,
    SYNC_SLACK_CHANNEL,
    SYNC_SLACK_WORKSPACE,
    UPDATE_SLACK_CHANNEL,
    UPDATE_SLACK_REACTIONS,
    app,
)
from memory.common.db.connection import make_session
from memory.common.db.models import SlackMessage
from memory.common.db.models.slack import (
    SlackApp,
    SlackChannel,
    SlackUserCredentials,
    SlackWorkspace,
)
from memory.common.slack import (
    SlackAPIError,
    SlackClient,
    get_channel_type,
    iter_channels,
    iter_messages,
    iter_thread_replies,
    iter_users,
    slack_api_base_url,
    slack_client_for,
    workspace_poll_plan,
    workspace_sync_interval,
)
from memory.common.content_processing import (
    clear_item_chunks,
    process_content_item,
)
from memory.common.jobs import tracked_task
from memory.common.people import find_person_by_slack_id, sync_slack_users_to_people
from memory.common.reactions import ReactionAction, Reactions, apply_reaction, slack_reactions

logger = logging.getLogger(__name__)

# Cache TTL for Slack user mappings (5 minutes)
USER_CACHE_TTL_SECONDS = 300

# Lock TTL for channel sync (10 minutes) - safety timeout if task crashes
CHANNEL_SYNC_LOCK_TTL_SECONDS = 600

# Hard cap for downloaded Slack files. Slack itself allows up to 1 GB
# uploads which would OOM the worker if we buffered the body. 100 MiB is
# plenty for typical attachments (slides, PDFs, transcripts) and bounds
# memory + disk pressure regardless of what users upload. Override via
# SLACK_FILE_MAX_BYTES env var if you need to ingest larger files.
SLACK_FILE_MAX_BYTES = int(os.getenv("SLACK_FILE_MAX_BYTES", 100 * 1024 * 1024))


def get_redis_client() -> redis.Redis:
    """Get Redis client for locking and short-lived caches in this module."""
    return redis.from_url(settings.REDIS_URL)


def channel_sync_lock_key(channel_id: str) -> str:
    return f"slack_channel_sync:{channel_id}"


@contextlib.contextmanager
def channel_sync_lock(channel_id: str):
    """Distributed lock for syncing a single Slack channel.

    Yields a Lock if acquired, or None if another sync is in progress.
    Atomic ownership-checked release is handled by the underlying
    ``distributed_lock`` helper — the previous bespoke
    ``release_channel_sync_lock`` deleted the key without checking
    the owner token, so a slow worker whose lock had already
    expired could clobber another worker's freshly-acquired lock.
    """
    with distributed_lock(
        channel_sync_lock_key(channel_id), CHANNEL_SYNC_LOCK_TTL_SECONDS
    ) as lock:
        yield lock


def is_channel_sync_locked(channel_id: str) -> bool:
    """Check if a channel sync is already in progress (probe only).

    Used to skip enqueueing redundant work; does NOT claim ownership.
    """
    client = get_redis_client()
    return client.exists(channel_sync_lock_key(channel_id)) > 0  # type: ignore[operator]


def get_cached_user_mapping(
    workspace_id: str, credentials: SlackUserCredentials
) -> dict[str, str]:
    """Get user ID -> name mapping with Redis caching.

    Caches the result for 5 minutes to avoid N API calls when processing
    multiple messages from the same workspace.
    """
    redis_client = redis.from_url(settings.REDIS_URL)
    cache_key = f"slack_users:{workspace_id}"

    # Try cache first
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)  # type: ignore[arg-type]

    # Cache miss - fetch from API
    try:
        with slack_client_for(credentials) as client:
            users_by_id = build_user_cache(client)
        # Cache the result
        redis_client.setex(cache_key, USER_CACHE_TTL_SECONDS, json.dumps(users_by_id))
        return users_by_id
    except SlackAPIError:
        logger.warning("Failed to fetch user list for mention resolution")
        return {}


def resolve_mentions(content: str, users_by_id: dict[str, str]) -> str:
    """Replace Slack mention format <@U123> with @display_name.

    Args:
        content: Raw message content with Slack-format mentions
        users_by_id: Mapping of Slack user IDs to display names
    """

    def replace_mention(match):
        user_id = match.group(1)
        name = users_by_id.get(user_id)
        if name:
            return f"@{name}"
        return match.group(0)

    # Replace user mentions: <@U123> or <@U123|name>
    # Slack IDs are typically uppercase but use case-insensitive match for safety
    content = re.sub(r"<@([A-Za-z0-9]+)(?:\|[^>]*)?>", replace_mention, content)

    # Replace channel mentions: <#C123|channel-name> -> #channel-name
    content = re.sub(r"<#[A-Za-z0-9]+\|([^>]+)>", r"#\1", content)

    # Replace URLs: <http://url|label> -> label (or just url if no label)
    content = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2", content)
    content = re.sub(r"<(https?://[^>]+)>", r"\1", content)

    return content


def download_slack_file(
    url: str, headers: dict, message_ts: str, workspace_id: str
) -> str | None:
    """Download a Slack file and save to disk. Returns relative path.

    Uses streaming with a hard size cap so a single large upload (Slack's
    1 GB max) can't OOM the worker. Pre-fix the call buffered the entire
    body in RAM and held a second copy via ``response.content``.
    """
    file_dir = (
        settings.SLACK_STORAGE_DIR / workspace_id / message_ts.replace(".", "_")
    )

    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    ext = pathlib.Path(url).suffix or ".dat"
    ext = ext.split("?")[0][:10]
    filename = f"{url_hash}{ext}"
    local_path = file_dir / filename

    if not stream_download_to_path(
        url,
        local_path,
        SLACK_FILE_MAX_BYTES,
        headers=headers,
    ):
        return None

    return paths.to_db_filename(local_path)


def get_workspace_credentials(
    session, workspace_id: str, slack_app_id: int
) -> SlackUserCredentials | None:
    """Get a valid credential for a (SlackApp, workspace) pair.

    Returns the first non-expired credential, or None if none available.
    Collection is user-agnostic — any valid token for the pair will do,
    but the SlackApp scope is required so two SlackApps that legitimately
    share a workspace (design doc §7 decision 5) don't cross-contaminate.
    """
    query = session.query(SlackUserCredentials).filter(
        SlackUserCredentials.workspace_id == workspace_id,
        SlackUserCredentials.slack_app_id == slack_app_id,
    )
    return next((cred for cred in query.all() if cred.is_usable()), None)


@app.task(name=SYNC_ALL_SLACK_WORKSPACES)
@tracked_task
def sync_all_slack_workspaces() -> dict[str, Any]:
    """Periodic polling of every collecting workspace, once per (SlackApp, workspace) pair.

    Each app sees only its own messages, so a workspace served by several
    apps gets one sync task per app.
    """
    logger.info("Starting sync of all Slack workspaces")

    with make_session() as session:
        workspaces = (
            session.query(SlackWorkspace)
            .filter(SlackWorkspace.collect_messages.is_(True))
            .all()
        )

        triggered = 0
        for workspace in workspaces:
            app_ids, blocked = workspace_poll_plan(session, workspace.id)
            if not app_ids:
                logger.warning(
                    f"Not polling Slack workspace {workspace.id} ({workspace.name}): "
                    + "; ".join(blocked)
                )
                continue
            if blocked:
                logger.info(f"Slack workspace {workspace.id}: " + "; ".join(blocked))
            if workspace.last_sync_at:
                elapsed = (
                    datetime.now(timezone.utc) - workspace.last_sync_at
                ).total_seconds()
                if elapsed < workspace_sync_interval(session, workspace, app_ids):
                    continue

            for slack_app_id in app_ids:
                app.send_task(
                    SYNC_SLACK_WORKSPACE,
                    kwargs={
                        "workspace_id": workspace.id,
                        "slack_app_id": slack_app_id,
                    },
                )
                triggered += 1

        logger.info(f"Triggered sync for {triggered} (app, workspace) pairs")
        return {"status": "completed", "workspaces_triggered": triggered}


@app.task(name=SYNC_SLACK_WORKSPACE)
@tracked_task
def sync_slack_workspace(
    workspace_id: str, slack_app_id: int
) -> dict[str, Any]:
    """
    Sync a single Slack workspace under a specific SlackApp.

    - Get valid credentials for (slack_app_id, workspace_id)
    - Sync channels list
    - Build user cache for mention resolution
    - Trigger channel syncs for channels with collection enabled

    ``slack_app_id`` scopes the credential lookup and propagates to all
    enqueued downstream tasks. Two SlackApps can legitimately share a
    workspace (design doc §7 decision 5), so the pair is the unit of
    sync work.
    """
    logger.info(f"Syncing Slack workspace {workspace_id}")

    with make_session() as session:
        workspace = session.get(SlackWorkspace, workspace_id)
        if not workspace:
            return {"status": "error", "error": "Workspace not found"}

        # Get valid credentials for this workspace
        credentials = get_workspace_credentials(session, workspace_id, slack_app_id)
        if not credentials:
            workspace.record_sync("No valid credentials - users need to re-authorize")
            session.commit()
            return {"status": "error", "error": "No valid credentials"}

        access_token = credentials.access_token
        if not access_token:
            workspace.record_sync("No access token")
            session.commit()
            return {"status": "error", "error": "No access token"}

        try:
            with slack_client_for(credentials) as client:
                # Test token and get workspace info
                try:
                    auth_response = client.call("auth.test")
                    workspace.name = auth_response.get("team", workspace.name)
                except SlackAPIError as e:
                    if e.error in ("token_expired", "invalid_auth", "token_revoked"):
                        workspace.record_sync(f"Token invalid: {e.error}")
                        session.commit()
                        return {"status": "error", "error": e.error}
                    raise

                # Fetch users once for both channel resolution and person sync
                users_list = list(iter_users(client))
                users_by_id = {
                    u["id"]: (
                        u.get("profile", {}).get("display_name")
                        or u.get("profile", {}).get("real_name")
                        or u.get("name")
                        or u["id"]
                    )
                    for u in users_list
                    if u.get("id")
                }

                # Sync channels (pass pre-built user cache)
                channels_synced = sync_workspace_channels(
                    client, workspace, session, users_by_id
                )

                # Sync Slack users to Person records (match by email, don't create new)
                people_result = sync_slack_users_to_people(
                    session, workspace_id, users_list, create_missing=False
                )
                logger.info(
                    f"People sync: {people_result['matched']} matched, "
                    f"{people_result['created']} created, {people_result['skipped']} skipped"
                )

                # Update sync status
                workspace.last_sync_at = datetime.now(timezone.utc)
                workspace.record_sync(None)
                session.commit()

                # Trigger channel syncs for enabled channels (skip if already syncing)
                channels_triggered = 0
                channels_skipped = 0
                for channel in workspace.channels:
                    if channel.should_collect and not channel.is_archived:
                        if is_channel_sync_locked(channel.id):
                            channels_skipped += 1
                            continue
                        app.send_task(
                            SYNC_SLACK_CHANNEL,
                            kwargs={
                                "channel_id": channel.id,
                                "slack_app_id": slack_app_id,
                            },
                        )
                        channels_triggered += 1

                if channels_skipped:
                    logger.info(f"Skipped {channels_skipped} channels (already syncing)")

                return {
                    "status": "completed",
                    "workspace_id": workspace_id,
                    "channels_synced": channels_synced,
                    "channels_triggered": channels_triggered,
                    "people_matched": people_result["matched"],
                }

        except SlackAPIError as e:
            workspace.record_sync(e)
            session.commit()
            logger.error(f"Slack API error syncing workspace {workspace_id}: {e}")
            return {"status": "error", "error": str(e)}
        except Exception as e:
            workspace.record_sync(e)
            session.commit()
            logger.exception(f"Unexpected error syncing workspace {workspace_id}: {e}")
            return {"status": "error", "error": str(e)}


def sync_workspace_channels(
    client: SlackClient,
    workspace: SlackWorkspace,
    session,
    users_by_id: dict[str, str],
) -> int:
    """Sync channels from Slack workspace to database.

    Args:
        client: SlackClient instance
        workspace: SlackWorkspace to sync channels for
        session: Database session
        users_by_id: Pre-built mapping of Slack user IDs to display names (for DM resolution)
    """
    synced = 0

    for channel in iter_channels(client):
        channel_id = channel.get("id")
        if not channel_id:
            continue

        ch_type = get_channel_type(channel)

        # Get channel name - DMs need user name resolution
        name: str
        if channel.get("is_im"):
            user_id = channel.get("user")
            name = users_by_id.get(user_id, user_id) if user_id else channel_id  # type: ignore[assignment]
        else:
            name = channel.get("name") or channel_id

        slack_channel = session.get(SlackChannel, channel_id)
        if slack_channel and slack_channel.workspace_id != workspace.id:
            logger.info(f"Slack channel {channel_id} belongs to another workspace; skipped")
            continue
        if not slack_channel:
            slack_channel = SlackChannel(id=channel_id, workspace_id=workspace.id)

        slack_channel.name = name
        slack_channel.channel_type = ch_type
        slack_channel.is_private = channel.get("is_private", False)
        slack_channel.is_archived = channel.get("is_archived", False)
        session.add(slack_channel)

        synced += 1

    session.commit()
    return synced


def build_user_cache(client: SlackClient) -> dict[str, str]:
    """Build user ID -> display name cache from Slack API.

    Returns a dict mapping Slack user IDs to their best display name.
    """
    users_by_id: dict[str, str] = {}

    for member in iter_users(client):
        user_id = member.get("id")
        if not user_id:
            continue

        profile = member.get("profile", {})
        # Prefer display_name, then real_name, then username
        name = (
            profile.get("display_name")
            or profile.get("real_name")
            or member.get("name")
            or user_id
        )
        users_by_id[user_id] = name

    return users_by_id


@app.task(name=SYNC_SLACK_CHANNEL)
@tracked_task
def sync_slack_channel(
    channel_id: str, slack_app_id: int
) -> dict[str, Any]:
    """
    Sync messages from a Slack channel under a specific SlackApp.

    Uses incremental sync based on last_message_ts cursor.
    Uses Redis lock to prevent concurrent syncs of the same channel.

    ``slack_app_id`` scopes credential lookup and propagates to all
    enqueued downstream tasks.
    """
    # Try to acquire lock - skip if another sync is already running.
    # Release happens automatically on context-manager exit, with an
    # ownership-checked Lua script (no plain `client.delete()` race).
    with channel_sync_lock(channel_id) as lock:
        if lock is None:
            logger.info(
                f"Skipping channel {channel_id} - sync already in progress"
            )
            return {"status": "skipped", "reason": "sync_in_progress"}

        logger.info(f"Syncing Slack channel {channel_id}")
        return _sync_slack_channel_impl(channel_id, slack_app_id)


def _sync_slack_channel_impl(
    channel_id: str, slack_app_id: int
) -> dict[str, Any]:
    """Implementation of channel sync (called with lock held)."""
    with make_session() as session:
        channel = session.get(SlackChannel, channel_id)
        if not channel:
            return {"status": "error", "error": "Channel not found"}

        workspace = channel.workspace
        if not workspace:
            return {"status": "error", "error": "No workspace"}

        # Get valid credentials
        credentials = get_workspace_credentials(session, workspace.id, slack_app_id)
        if not credentials or not credentials.access_token:
            return {"status": "error", "error": "No valid workspace credentials"}

        access_token = credentials.access_token

        try:
            with slack_client_for(credentials) as client:
                messages_synced = 0
                oldest = channel.last_message_ts
                newest_ts = oldest

                for msg in iter_messages(client, channel_id, oldest=oldest):
                    msg_ts = msg.get("ts")
                    if not msg_ts:
                        continue

                    # Track newest message for final cursor update
                    if not newest_ts or msg_ts > newest_ts:
                        newest_ts = msg_ts

                    # Skip non-message subtypes we don't want
                    subtype = msg.get("subtype")
                    if subtype in ("channel_join", "channel_leave", "bot_message"):
                        continue

                    # Process message
                    app.send_task(
                        ADD_SLACK_MESSAGE,
                        kwargs={
                            "workspace_id": workspace.id,
                            "channel_id": channel_id,
                            "message_ts": msg_ts,
                            "author_id": msg.get("user"),
                            "content": msg.get("text", ""),
                            "thread_ts": msg.get("thread_ts"),
                            "reply_count": msg.get("reply_count"),
                            "subtype": subtype,
                            "edited_ts": msg.get("edited", {}).get("ts"),
                            "reactions": msg.get("reactions"),
                            "files": msg.get("files"),
                            "slack_app_id": slack_app_id,
                            "polled": True,
                        },
                    )
                    messages_synced += 1

                    # Fetch thread replies if this is a thread parent
                    if msg.get("reply_count", 0) > 0:
                        thread_messages = fetch_thread_replies(
                            client, channel_id, msg_ts, workspace.id, slack_app_id
                        )
                        messages_synced += thread_messages

                # Update cursor
                if newest_ts:
                    channel.last_message_ts = newest_ts
                session.commit()

                return {
                    "status": "completed",
                    "channel_id": channel_id,
                    "messages_synced": messages_synced,
                }

        except SlackAPIError as e:
            return {"status": "error", "error": str(e)}


def fetch_thread_replies(
    client: SlackClient,
    channel_id: str,
    thread_ts: str,
    workspace_id: str,
    slack_app_id: int | None = None,
) -> int:
    """Fetch and queue thread replies using iterator."""
    messages_queued = 0

    try:
        for msg in iter_thread_replies(client, channel_id, thread_ts):
            msg_ts = msg.get("ts")
            if not msg_ts:
                continue

            app.send_task(
                ADD_SLACK_MESSAGE,
                kwargs={
                    "workspace_id": workspace_id,
                    "channel_id": channel_id,
                    "message_ts": msg_ts,
                    "author_id": msg.get("user"),
                    "content": msg.get("text", ""),
                    "thread_ts": thread_ts,
                    "subtype": msg.get("subtype"),
                    "edited_ts": msg.get("edited", {}).get("ts"),
                    "reactions": msg.get("reactions"),
                    "files": msg.get("files"),
                    "slack_app_id": slack_app_id,
                    "polled": True,
                },
            )
            messages_queued += 1

    except SlackAPIError as e:
        logger.error(f"Failed to fetch thread replies for {thread_ts}: {e}")

    return messages_queued


def ensure_slack_channel(workspace_id: str, channel_id: str) -> bool:
    """Ensure a SlackChannel row exists for ``(workspace_id, channel_id)``;
    False when the channel id belongs to another workspace.

    Commits in its own transaction so the channel row survives even if the
    caller's main transaction later rolls back (e.g. on an IntegrityError race
    when inserting a SlackMessage). Tolerates concurrent inserts by another
    worker.
    """
    with make_session() as session:
        if existing := session.get(SlackChannel, channel_id):
            return existing.workspace_id == workspace_id
        try:
            channel = SlackChannel(
                id=channel_id,
                workspace_id=workspace_id,
                name=channel_id,
                channel_type="channel",
            )
            session.add(channel)
            session.flush()
        except IntegrityError:
            # Another worker inserted the same channel row concurrently.
            session.rollback()
            logger.debug(
                f"SlackChannel {channel_id} concurrently created by another worker"
            )
    return True


def owner_app_ids(session, slack_app_id: int) -> set[int]:
    """The apps of this app's owner; just this one for an unowned app."""
    app = session.get(SlackApp, slack_app_id)
    if app is None or app.created_by_user_id is None:
        return {slack_app_id}
    rows = session.query(SlackApp.id).filter(SlackApp.created_by_user_id == app.created_by_user_id)
    return {row[0] for row in rows}


def may_change(session, message: SlackMessage, slack_app_id: int | None, polled: bool = False) -> bool:
    """Whether data from this app may edit, delete or react to a stored message:
    one ingested by an app of the same owner. A message no app has claimed
    changes only through a poll, which reads with the app's own token."""
    if slack_app_id is None:
        return True
    if message.slack_app_id is None:
        return polled
    if message.slack_app_id in owner_app_ids(session, slack_app_id):
        return True
    logger.info(f"Slack message {message.id} not changed: it belongs to another owner")
    return False


def _maybe_update_content(existing: SlackMessage, content: str) -> bool:
    """Overwrite ``existing.content`` iff it differs. Returns True on change."""
    if existing.content == content:
        return False
    existing.content = content
    return True


def merge_slack_message_state(
    existing: SlackMessage,
    content: str,
    edited_ts: str | None,
    reactions: Reactions | None,
    files: list[dict] | None,
) -> bool:
    """Apply incoming Slack message data onto an existing row.

    Implements **edit-prefer-older-edited_ts** semantics for content/edited_ts
    so that out-of-order delivery of ``message`` and ``message_changed`` events
    doesn't drop the canonical pre-edit content (B-pre-2):

    * Both ``edited_ts`` are None → idempotent (content overwritten only if
      different — should not happen for the same ``message_ts``).
    * Incoming has no ``edited_ts`` and existing does → incoming is the
      canonical original; overwrite content but keep ``existing.edited_ts``
      as the marker that the message has since been edited.
    * Existing has no ``edited_ts`` and incoming does → normal flow; overwrite
      both content and ``edited_ts``.
    * Both have ``edited_ts`` → newer wins; equal/older is a no-op for content.

    Reactions and files: message payloads carry the full current lists, so
    incoming replaces stored when provided.

    Returns True iff ``existing.content`` changed (caller may need to re-embed).
    """
    if reactions is not None:
        existing.reactions = reactions
    if files is not None:
        existing.files = files

    existing_edited_ts = existing.edited_ts

    # Cases 1 & 4: incoming has no edited_ts. Overwrite content if different;
    # leave existing.edited_ts untouched (None stays None; existing edit
    # marker is preserved as the canonical "this message has been edited" hint).
    if edited_ts is None:
        return _maybe_update_content(existing, content)

    # Existing has no edited_ts but incoming does: normal "edit after original"
    # flow — adopt the new edited_ts and overwrite content.
    if existing_edited_ts is None:
        existing.edited_ts = edited_ts
        return _maybe_update_content(existing, content)

    # Both have edited_ts. Compare numerically — Slack timestamps are
    # decimal strings ("<seconds>.<microseconds>"); float() avoids the
    # lexicographic-vs-numeric pitfall if the integer-seconds part ever
    # gains a digit (year 2286). Newer wins; equal/older is a no-op.
    if float(edited_ts) > float(existing_edited_ts):
        existing.edited_ts = edited_ts
        return _maybe_update_content(existing, content)

    # Stale or duplicate edit — no change.
    return False


def _get_existing_slack_message(session, msg_kwargs: dict) -> SlackMessage | None:
    """Fetch the SlackMessage row for the given identity tuple."""
    return (
        session.query(SlackMessage)
        .filter(
            SlackMessage.message_ts == msg_kwargs["message_ts"],
            SlackMessage.workspace_id == msg_kwargs["workspace_id"],
            SlackMessage.channel_id == msg_kwargs["channel_id"],
        )
        .first()
    )


def _reembed_existing_message(session, message: SlackMessage) -> dict[str, Any]:
    """Re-embed an existing SlackMessage whose content was just updated."""
    clear_item_chunks(message, session)
    result = process_content_item(message, session)
    result["status"] = "updated"
    return result


def _link_author_person(session, message: SlackMessage, workspace_id: str) -> None:
    """Link ``message`` to a Person record matching the Slack author, if any."""
    if not message.author_id:
        return
    person = find_person_by_slack_id(session, workspace_id, message.author_id)
    if person and person not in message.people:
        message.people.append(person)


def _apply_merge(session, existing: SlackMessage, msg_kwargs: dict) -> dict[str, Any]:
    """Merge incoming data into ``existing``; re-embed if content changed."""
    if not may_change(session, existing, msg_kwargs["slack_app_id"], msg_kwargs["polled"]):
        return {"status": "already_exists", "message_ts": existing.message_ts, "slackmessage_id": existing.id}
    if existing.slack_app_id is None:
        existing.slack_app_id = msg_kwargs["slack_app_id"]
    content_changed = merge_slack_message_state(
        existing,
        msg_kwargs["content"],
        msg_kwargs["edited_ts"],
        msg_kwargs["reactions"],
        msg_kwargs["files"],
    )
    _link_author_person(session, existing, msg_kwargs["workspace_id"])

    if content_changed:
        return _reembed_existing_message(session, existing)

    return {
        "status": "already_exists",
        "message_ts": existing.message_ts,
        "slackmessage_id": existing.id,
    }


SLACK_FILES_HOST = "files.slack.com"


def download_message_images(
    files: list[dict],
    access_token: str,
    message_ts: str,
    workspace_id: str,
    api_base_url: str,
) -> list[str]:
    """Download image attachments for a Slack message; returns relative paths.

    File URLs come from the event, so the token is only sent to Slack's file
    host or the host of the issuing app's own API.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    token_hosts = {SLACK_FILES_HOST, urlparse(api_base_url).hostname}
    saved: list[str] = []
    for file_info in files:
        if not file_info.get("mimetype", "").startswith("image/"):
            continue
        url = file_info.get("url_private_download") or file_info.get("url_private")
        if not url or urlparse(url).hostname not in token_hosts:
            continue
        path = download_slack_file(url, headers, message_ts, workspace_id)
        if path:
            saved.append(path)
    return saved


def _build_slack_message(msg_kwargs: dict, author_name: str | None,
                        resolved_content: str, saved_images: list) -> SlackMessage:
    """Construct a fresh SlackMessage ORM object (not yet added to a session)."""
    workspace_id = msg_kwargs["workspace_id"]
    channel_id = msg_kwargs["channel_id"]
    message_ts = msg_kwargs["message_ts"]
    content = msg_kwargs["content"]

    # Content hash deliberately includes content so edits hash differently.
    # Row identity is (message_ts, workspace_id, channel_id) — see unique index.
    content_hash = hashlib.sha256(
        f"{workspace_id}:{channel_id}:{message_ts}:{content}".encode()
    ).digest()

    return SlackMessage(
        slack_app_id=msg_kwargs["slack_app_id"],
        modality="message",
        sha256=content_hash,
        content=content,
        message_ts=message_ts,
        channel_id=channel_id,
        workspace_id=workspace_id,
        author_id=msg_kwargs["author_id"],
        author_name=author_name,
        thread_ts=msg_kwargs["thread_ts"],
        reply_count=msg_kwargs["reply_count"],
        message_type=msg_kwargs["subtype"] or "message",
        edited_ts=msg_kwargs["edited_ts"],
        reactions=msg_kwargs["reactions"],
        files=msg_kwargs["files"],
        resolved_content=resolved_content if resolved_content != content else None,
        images=saved_images or None,
    )


def _insert_new_slack_message(session, msg_kwargs: dict) -> dict[str, Any]:
    """Insert a brand-new SlackMessage. May raise IntegrityError on race."""
    workspace_id = msg_kwargs["workspace_id"]
    message_ts = msg_kwargs["message_ts"]
    content = msg_kwargs["content"]
    author_id = msg_kwargs["author_id"]
    slack_app_id = msg_kwargs["slack_app_id"]

    credentials = get_workspace_credentials(session, workspace_id, slack_app_id)
    access_token = credentials.access_token if credentials else None

    users_by_id: dict[str, str] = {}
    if credentials and access_token:
        users_by_id = get_cached_user_mapping(workspace_id, credentials)

    resolved_content = resolve_mentions(content, users_by_id)
    author_name = users_by_id.get(author_id) if author_id else None

    saved_images: list[str] = []
    if msg_kwargs["files"] and credentials and access_token:
        try:
            api_base_url = slack_api_base_url(credentials.slack_app)
        except SlackAPIError:
            api_base_url = None
        if api_base_url:
            saved_images = download_message_images(
                msg_kwargs["files"], access_token, message_ts, workspace_id, api_base_url=api_base_url
            )

    message = _build_slack_message(msg_kwargs, author_name, resolved_content, saved_images)
    _link_author_person(session, message, workspace_id)
    return process_content_item(message, session)


def _try_add_slack_message(msg_kwargs: dict) -> dict[str, Any] | None:
    """First attempt: insert or merge in a single session.

    Returns the result dict on success, or None if a race was detected and
    the caller should re-fetch in a fresh session and merge.
    """
    try:
        with make_session() as session:
            existing = _get_existing_slack_message(session, msg_kwargs)
            if existing:
                return _apply_merge(session, existing, msg_kwargs)
            return _insert_new_slack_message(session, msg_kwargs)
    except IntegrityError:
        logger.info(
            f"Race on SlackMessage ({msg_kwargs['workspace_id']}, "
            f"{msg_kwargs['channel_id']}, {msg_kwargs['message_ts']}); "
            f"will merge from a fresh session"
        )
        return None


def _merge_after_race(msg_kwargs: dict) -> dict[str, Any]:
    """Race recovery path: re-fetch the existing row in a fresh session and merge."""
    with make_session() as session:
        existing = _get_existing_slack_message(session, msg_kwargs)
        if not existing:
            # IntegrityError raised but no row visible — likely the winner's
            # transaction hasn't committed yet, or a different constraint failed.
            logger.error(
                f"IntegrityError raced but no SlackMessage row visible for "
                f"({msg_kwargs['workspace_id']}, {msg_kwargs['channel_id']}, "
                f"{msg_kwargs['message_ts']})"
            )
            return {
                "status": "error",
                "reason": "race_detected_no_row",
                "message_ts": msg_kwargs["message_ts"],
            }
        return _apply_merge(session, existing, msg_kwargs)


@app.task(name=ADD_SLACK_MESSAGE)
@tracked_task
def add_slack_message(
    workspace_id: str,
    channel_id: str,
    message_ts: str,
    author_id: str | None,
    content: str,
    slack_app_id: int,
    thread_ts: str | None = None,
    reply_count: int | None = None,
    subtype: str | None = None,
    edited_ts: str | None = None,
    reactions: list[dict] | None = None,
    files: list[dict] | None = None,
    polled: bool = False,
) -> dict[str, Any]:
    """Add or merge a Slack message in the database.

    ``polled`` marks a message read with the app's own token (history sync)
    rather than pushed; only those claim messages no app has claimed yet.

    On the happy path, inserts a new ``SlackMessage`` row and embeds it.
    If a row already exists (or appears mid-flight via a concurrent insert),
    incoming data is merged in using ``merge_slack_message_state`` and the
    embedding is regenerated when content changes.

    ``slack_app_id`` scopes credential lookup so the right Slack token is
    used to resolve mentions and download files.
    """
    logger.info(f"Adding Slack message {message_ts} from channel {channel_id}")

    if not author_id:
        # Skip messages without an author (system messages, etc.)
        return {"status": "skipped", "reason": "no_author"}

    # Channel auto-create commits independently so it survives a later
    # IntegrityError race on the message insert (B-pre-1).
    if not ensure_slack_channel(workspace_id, channel_id):
        logger.info(f"Slack channel {channel_id} belongs to another workspace; message ignored")
        return {"status": "skipped", "reason": "channel_in_another_workspace"}

    msg_kwargs: dict[str, Any] = {
        "workspace_id": workspace_id,
        "channel_id": channel_id,
        "message_ts": message_ts,
        "author_id": author_id,
        "content": content,
        "thread_ts": thread_ts,
        "reply_count": reply_count,
        "subtype": subtype,
        "edited_ts": edited_ts,
        "reactions": slack_reactions(reactions),
        "files": files,
        "slack_app_id": slack_app_id,
        "polled": polled,
    }

    result = _try_add_slack_message(msg_kwargs)
    if result is not None:
        return result

    # Race detected — re-fetch and merge in a fresh session.
    return _merge_after_race(msg_kwargs)


# ---------------------------------------------------------------------------
# Push-event handlers (slack-changes.md §3.6)
# ---------------------------------------------------------------------------


@app.task(name=MARK_SLACK_MESSAGE_DELETED)
@tracked_task
def mark_slack_message_deleted(
    workspace_id: str,
    channel_id: str,
    message_ts: str,
    slack_app_id: int | None = None,
) -> dict[str, Any]:
    """Hard-delete a Slack message in response to a `message_deleted` event.

    Removes the SlackMessage row, its associated Chunks, and the underlying
    Qdrant points. Message identity is ``(workspace_id, channel_id,
    message_ts)``; ``slack_app_id`` limits it to messages its owner's apps ingested.
    """
    with make_session() as session:
        message = (
            session.query(SlackMessage)
            .filter(
                SlackMessage.workspace_id == workspace_id,
                SlackMessage.channel_id == channel_id,
                SlackMessage.message_ts == message_ts,
            )
            .first()
        )
        if not message:
            logger.info(
                f"mark_slack_message_deleted: no row for "
                f"({workspace_id}, {channel_id}, {message_ts})"
            )
            return {"status": "not_found", "message_ts": message_ts}
        if not may_change(session, message, slack_app_id):
            return {"status": "ignored", "message_ts": message_ts}

        clear_item_chunks(message, session)
        session.delete(message)
        session.commit()
        return {"status": "deleted", "message_ts": message_ts}


# A reaction can be processed before its message is stored; retry a few
# times, then drop it. History syncs only fetch new messages, so a dropped
# reaction stays missing until that message is next stored in full.
REACTION_RETRIES = 3
REACTION_RETRY_SECONDS = 30


@app.task(name=UPDATE_SLACK_REACTIONS)
@tracked_task
def update_slack_reactions(
    workspace_id: str,
    channel_id: str,
    message_ts: str,
    action: ReactionAction,
    emoji: str | None = None,
    user_id: str | None = None,
    slack_app_id: int | None = None,
    attempt: int = 0,
) -> dict[str, Any]:
    """Apply a `reaction_added` / `reaction_removed` event to a SlackMessage.

    Only the slack_message row is locked while the per-user set operation
    applies, so concurrent events for one message queue on it rather than
    losing each other's updates; a lock conflict is retried.
    """
    retry_kwargs = {
        "workspace_id": workspace_id,
        "channel_id": channel_id,
        "message_ts": message_ts,
        "action": action,
        "emoji": emoji,
        "user_id": user_id,
        "slack_app_id": slack_app_id,
        "attempt": attempt + 1,
    }
    try:
        with make_session() as session:
            message = (
                session.query(SlackMessage)
                .filter(
                    SlackMessage.workspace_id == workspace_id,
                    SlackMessage.channel_id == channel_id,
                    SlackMessage.message_ts == message_ts,
                )
                .with_for_update(of=SlackMessage.__table__)
                .first()
            )
            if message and not may_change(session, message, slack_app_id):
                return {"status": "ignored", "message_ts": message_ts}
            if message:
                message.reactions = apply_reaction(message.reactions, action, emoji, user_id)
                session.commit()
                return {"status": "updated", "message_ts": message_ts}
    except OperationalError:
        logger.info(f"Reaction update for Slack message {message_ts} hit a lock conflict; retrying")
        update_slack_reactions.apply_async(kwargs=retry_kwargs, countdown=1)  # type: ignore[attr-defined]
        return {"status": "retrying", "message_ts": message_ts}

    if attempt >= REACTION_RETRIES:
        return {"status": "not_found", "message_ts": message_ts}
    update_slack_reactions.apply_async(  # type: ignore[attr-defined]
        kwargs=retry_kwargs, countdown=REACTION_RETRY_SECONDS * (attempt + 1)
    )
    return {"status": "retrying", "message_ts": message_ts}


@app.task(name=UPDATE_SLACK_CHANNEL)
@tracked_task
def update_slack_channel(
    workspace_id: str,
    channel_id: str,
    channel_payload: dict,
    slack_app_id: int | None = None,
) -> dict[str, Any]:
    """Cheap upsert of channel metadata in response to a `channel_*` event.

    Used for ``channel_created`` (insert), ``channel_renamed`` (update name),
    ``channel_archived`` / ``channel_unarchived`` (update is_archived).
    """
    name = channel_payload.get("name") or channel_id
    is_private = bool(channel_payload.get("is_private", False))
    is_archived = bool(channel_payload.get("is_archived", False))
    channel_type = get_channel_type(channel_payload)

    with make_session() as session:
        channel = session.get(SlackChannel, channel_id)
        if channel is not None and channel.workspace_id != workspace_id:
            logger.info(f"Slack channel {channel_id} belongs to another workspace; event ignored")
            return {"status": "ignored", "channel_id": channel_id}
        created = channel is None
        if channel is None:
            channel = SlackChannel(
                id=channel_id,
                workspace_id=workspace_id,
                name=name,
                channel_type=channel_type,
                is_private=is_private,
                is_archived=is_archived,
            )
            session.add(channel)
        else:
            channel.name = name
            channel.is_private = is_private
            channel.is_archived = is_archived
            channel.channel_type = channel_type
        session.commit()
        return {
            "status": "created" if created else "updated",
            "channel_id": channel_id,
        }
