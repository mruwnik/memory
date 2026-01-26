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

import hashlib
import logging
import pathlib
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from memory.common import settings
from memory.common.celery_app import (
    ADD_SLACK_MESSAGE,
    SYNC_ALL_SLACK_WORKSPACES,
    SYNC_SLACK_CHANNEL,
    SYNC_SLACK_WORKSPACE,
    app,
)
from memory.common.db.connection import make_session
from memory.common.db.models import SlackMessage
from memory.common.db.models.slack import (
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
)
from memory.common.content_processing import (
    process_content_item,
    safe_task_execution,
)
from memory.common.people import find_person_by_slack_id, sync_slack_users_to_people

logger = logging.getLogger(__name__)


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
    """Download a Slack file and save to disk. Returns relative path."""
    try:
        response = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
        response.raise_for_status()

        # Create directory for this message
        file_dir = (
            settings.SLACK_STORAGE_DIR / workspace_id / message_ts.replace(".", "_")
        )
        file_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename from URL hash (SHA256 truncated for shorter filenames)
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        ext = pathlib.Path(url).suffix or ".dat"
        ext = ext.split("?")[0][:10]  # Limit extension length
        filename = f"{url_hash}{ext}"
        local_path = file_dir / filename

        local_path.write_bytes(response.content)

        # Return relative path
        return str(local_path.relative_to(settings.FILE_STORAGE_DIR))

    except httpx.HTTPStatusError as e:
        logger.error(
            f"Failed to download Slack file from {url}: HTTP {e.response.status_code}"
        )
        return None
    except Exception as e:
        logger.error(
            f"Failed to download Slack file from {url}: {type(e).__name__}: {e}"
        )
        return None


def get_workspace_credentials(
    session, workspace_id: str
) -> SlackUserCredentials | None:
    """Get valid credentials for a workspace.

    Returns the first non-expired credential, or None if none available.
    Collection is user-agnostic - we just need any valid token.
    """
    credentials = (
        session.query(SlackUserCredentials)
        .filter(SlackUserCredentials.workspace_id == workspace_id)
        .all()
    )

    for cred in credentials:
        if not cred.is_token_expired() and cred.access_token:
            return cred

    return None


@app.task(name=SYNC_ALL_SLACK_WORKSPACES)
@safe_task_execution
def sync_all_slack_workspaces() -> dict[str, Any]:
    """
    Periodic task to sync all active Slack workspaces.

    This task runs on a schedule and fans out to per-workspace sync tasks.
    """
    logger.info("Starting sync of all Slack workspaces")

    with make_session() as session:
        workspaces = (
            session.query(SlackWorkspace)
            .filter(
                SlackWorkspace.collect_messages == True  # noqa: E712
            )
            .all()
        )

        triggered = 0
        for workspace in workspaces:
            # Check if sync is due based on interval
            if workspace.last_sync_at:
                elapsed = (
                    datetime.now(timezone.utc) - workspace.last_sync_at
                ).total_seconds()
                if elapsed < workspace.sync_interval_seconds:
                    continue

            # Trigger workspace sync
            app.send_task(SYNC_SLACK_WORKSPACE, args=[workspace.id])
            triggered += 1

        logger.info(f"Triggered sync for {triggered} Slack workspaces")
        return {"status": "completed", "workspaces_triggered": triggered}


@app.task(name=SYNC_SLACK_WORKSPACE)
@safe_task_execution
def sync_slack_workspace(workspace_id: str) -> dict[str, Any]:
    """
    Sync a single Slack workspace.

    - Get valid credentials (any user's token will work for reading)
    - Sync channels list
    - Build user cache for mention resolution
    - Trigger channel syncs for channels with collection enabled
    """
    logger.info(f"Syncing Slack workspace {workspace_id}")

    with make_session() as session:
        workspace = session.get(SlackWorkspace, workspace_id)
        if not workspace:
            return {"status": "error", "error": "Workspace not found"}

        # Get valid credentials for this workspace
        credentials = get_workspace_credentials(session, workspace_id)
        if not credentials:
            workspace.sync_error = "No valid credentials - users need to re-authorize"
            session.commit()
            return {"status": "error", "error": "No valid credentials"}

        access_token = credentials.access_token
        if not access_token:
            workspace.sync_error = "No access token"
            session.commit()
            return {"status": "error", "error": "No access token"}

        try:
            with SlackClient(access_token) as client:
                # Test token and get workspace info
                try:
                    auth_response = client.call("auth.test")
                    workspace.name = auth_response.get("team", workspace.name)
                except SlackAPIError as e:
                    if e.error in ("token_expired", "invalid_auth", "token_revoked"):
                        workspace.sync_error = f"Token invalid: {e.error}"
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
                workspace.sync_error = None
                session.commit()

                # Trigger channel syncs for enabled channels
                channels_triggered = 0
                for channel in workspace.channels:
                    if channel.should_collect and not channel.is_archived:
                        app.send_task(SYNC_SLACK_CHANNEL, args=[channel.id])
                        channels_triggered += 1

                return {
                    "status": "completed",
                    "workspace_id": workspace_id,
                    "channels_synced": channels_synced,
                    "channels_triggered": channels_triggered,
                    "people_matched": people_result["matched"],
                }

        except SlackAPIError as e:
            workspace.sync_error = str(e)
            session.commit()
            logger.error(f"Slack API error syncing workspace {workspace_id}: {e}")
            return {"status": "error", "error": str(e)}
        except Exception as e:
            workspace.sync_error = str(e)
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
@safe_task_execution
def sync_slack_channel(channel_id: str) -> dict[str, Any]:
    """
    Sync messages from a Slack channel.

    Uses incremental sync based on last_message_ts cursor.
    """
    logger.info(f"Syncing Slack channel {channel_id}")

    with make_session() as session:
        channel = session.get(SlackChannel, channel_id)
        if not channel:
            return {"status": "error", "error": "Channel not found"}

        workspace = channel.workspace
        if not workspace:
            return {"status": "error", "error": "No workspace"}

        # Get valid credentials
        credentials = get_workspace_credentials(session, workspace.id)
        if not credentials or not credentials.access_token:
            return {"status": "error", "error": "No valid workspace credentials"}

        access_token = credentials.access_token

        try:
            with SlackClient(access_token) as client:
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
                        },
                    )
                    messages_synced += 1

                    # Fetch thread replies if this is a thread parent
                    if msg.get("reply_count", 0) > 0:
                        thread_messages = fetch_thread_replies(
                            client, channel_id, msg_ts, workspace.id
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
                },
            )
            messages_queued += 1

    except SlackAPIError as e:
        logger.error(f"Failed to fetch thread replies for {thread_ts}: {e}")

    return messages_queued


@app.task(name=ADD_SLACK_MESSAGE)
@safe_task_execution
def add_slack_message(
    workspace_id: str,
    channel_id: str,
    message_ts: str,
    author_id: str | None,
    content: str,
    thread_ts: str | None = None,
    reply_count: int | None = None,
    subtype: str | None = None,
    edited_ts: str | None = None,
    reactions: list[dict] | None = None,
    files: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Add a Slack message to the database.
    """
    logger.info(f"Adding Slack message {message_ts} from channel {channel_id}")

    if not author_id:
        # Skip messages without an author (system messages, etc.)
        return {"status": "skipped", "reason": "no_author"}

    with make_session() as session:
        # Check if message exists (need AND logic for exact match)
        # Messages are unique per workspace+channel+timestamp, not just workspace+timestamp
        existing = (
            session.query(SlackMessage)
            .filter(
                SlackMessage.message_ts == message_ts,
                SlackMessage.workspace_id == workspace_id,
                SlackMessage.channel_id == channel_id,
            )
            .first()
        )

        if existing:
            # Update if edited
            if edited_ts:
                existing.content = content
                existing.edited_ts = edited_ts
                existing.reactions = reactions
                session.commit()
                return {"status": "updated", "message_ts": message_ts}
            return {"status": "already_exists", "message_ts": message_ts}

        # Get credentials for file downloads and user resolution
        credentials = get_workspace_credentials(session, workspace_id)
        access_token = credentials.access_token if credentials else None

        # Build user cache for mention resolution (done per-message to ensure fresh data)
        # This is slightly inefficient but ensures we don't miss users
        users_by_id: dict[str, str] = {}
        if access_token:
            try:
                with SlackClient(access_token) as client:
                    users_by_id = build_user_cache(client)
            except SlackAPIError:
                logger.warning("Failed to fetch user list for mention resolution")

        # Resolve mentions
        resolved_content = resolve_mentions(content, users_by_id)

        # Get author name from cache
        author_name = users_by_id.get(author_id)

        # Download images from files
        saved_images = []
        if files and access_token:
            headers = {"Authorization": f"Bearer {access_token}"}
            for file_info in files:
                if not file_info.get("mimetype", "").startswith("image/"):
                    continue
                url = file_info.get("url_private_download") or file_info.get(
                    "url_private"
                )
                if not url:
                    continue
                path = download_slack_file(url, headers, message_ts, workspace_id)
                if path:
                    saved_images.append(path)

        # Ensure channel exists
        channel = session.get(SlackChannel, channel_id)
        if not channel:
            channel = SlackChannel(
                id=channel_id,
                workspace_id=workspace_id,
                name=channel_id,
                channel_type="channel",
            )
            session.add(channel)
            session.flush()

        # Create content hash - includes content intentionally so edits create different hashes.
        # Deduplication is handled via the unique index on (message_ts, workspace_id, channel_id),
        # while the hash is used for content-based operations like embeddings.
        content_hash = hashlib.sha256(
            f"{workspace_id}:{channel_id}:{message_ts}:{content}".encode()
        ).digest()

        # Create message
        message = SlackMessage(
            modality="text",
            sha256=content_hash,
            content=content,
            message_ts=message_ts,
            channel_id=channel_id,
            workspace_id=workspace_id,
            author_id=author_id,
            author_name=author_name,
            thread_ts=thread_ts,
            reply_count=reply_count,
            message_type=subtype or "message",
            edited_ts=edited_ts,
            reactions=reactions,
            files=files,
            resolved_content=resolved_content if resolved_content != content else None,
            images=saved_images or None,
        )

        # Link author to Person if found
        if author_id:
            if person := find_person_by_slack_id(session, workspace_id, author_id):
                if person not in message.people:
                    message.people.append(person)

        result = process_content_item(message, session)
        return result
