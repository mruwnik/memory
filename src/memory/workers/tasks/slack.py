"""
Celery tasks for Slack message syncing and processing.

This module provides tasks for:
- Syncing all Slack workspaces (periodic task)
- Syncing individual workspaces (channels, users, messages)
- Processing individual messages
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
    SlackUser,
    SlackWorkspace,
)
from memory.common.content_processing import (
    check_content_exists,
    process_content_item,
    safe_task_execution,
)

logger = logging.getLogger(__name__)


class SlackAPIError(Exception):
    """Error from Slack API."""

    def __init__(self, error: str, response: dict | None = None):
        self.error = error
        self.response = response
        super().__init__(f"Slack API error: {error}")


def get_slack_client(workspace: SlackWorkspace) -> httpx.Client:
    """Create an HTTP client configured for Slack API calls."""
    if not workspace.access_token:
        raise SlackAPIError("No access token")

    return httpx.Client(
        base_url="https://slack.com/api/",
        headers={
            "Authorization": f"Bearer {workspace.access_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        timeout=30.0,
    )


def slack_api_call(client: httpx.Client, method: str, **kwargs) -> dict:
    """Make a Slack API call and handle errors."""
    response = client.post(method, json=kwargs if kwargs else None)
    data = response.json()

    if not data.get("ok"):
        error = data.get("error", "unknown_error")
        logger.error(f"Slack API error in {method}: {error}")
        raise SlackAPIError(error, data)

    return data


def resolve_mentions(content: str, users_by_id: dict[str, SlackUser]) -> str:
    """Replace Slack mention format <@U123> with @display_name."""
    def replace_mention(match):
        user_id = match.group(1)
        user = users_by_id.get(user_id)
        if user:
            return f"@{user.name}"
        return match.group(0)

    # Replace user mentions: <@U123> or <@U123|name>
    content = re.sub(r"<@([A-Z0-9]+)(?:\|[^>]*)?>", replace_mention, content)

    # Replace channel mentions: <#C123|channel-name> -> #channel-name
    content = re.sub(r"<#[A-Z0-9]+\|([^>]+)>", r"#\1", content)

    # Replace URLs: <http://url|label> -> label (or just url if no label)
    content = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2", content)
    content = re.sub(r"<(https?://[^>]+)>", r"\1", content)

    return content


def download_slack_file(url: str, headers: dict, message_ts: str, workspace_id: str) -> str | None:
    """Download a Slack file and save to disk. Returns relative path."""
    try:
        response = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
        response.raise_for_status()

        # Create directory for this message
        file_dir = settings.SLACK_STORAGE_DIR / workspace_id / message_ts.replace(".", "_")
        file_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename from URL hash
        url_hash = hashlib.md5(url.encode()).hexdigest()
        ext = pathlib.Path(url).suffix or ".dat"
        ext = ext.split("?")[0][:10]  # Limit extension length
        filename = f"{url_hash}{ext}"
        local_path = file_dir / filename

        local_path.write_bytes(response.content)

        # Return relative path
        return str(local_path.relative_to(settings.FILE_STORAGE_DIR))

    except Exception as e:
        logger.error(f"Failed to download Slack file from {url}: {e}")
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
        workspaces = session.query(SlackWorkspace).filter(
            SlackWorkspace.collect_messages == True  # noqa: E712
        ).all()

        triggered = 0
        for workspace in workspaces:
            # Check if sync is due based on interval
            if workspace.last_sync_at:
                elapsed = (datetime.now(timezone.utc) - workspace.last_sync_at).total_seconds()
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

    - Refresh token if expired
    - Sync channels list
    - Sync users list
    - Trigger channel syncs for channels with collection enabled
    """
    logger.info(f"Syncing Slack workspace {workspace_id}")

    with make_session() as session:
        workspace = session.get(SlackWorkspace, workspace_id)
        if not workspace:
            return {"status": "error", "error": "Workspace not found"}

        if not workspace.access_token:
            workspace.sync_error = "No access token"
            session.commit()
            return {"status": "error", "error": "No access token"}

        try:
            with get_slack_client(workspace) as client:
                # Test token and get workspace info
                try:
                    auth_response = slack_api_call(client, "auth.test")
                    workspace.name = auth_response.get("team", workspace.name)
                except SlackAPIError as e:
                    if e.error in ("token_expired", "invalid_auth", "token_revoked"):
                        workspace.sync_error = f"Token invalid: {e.error}"
                        session.commit()
                        return {"status": "error", "error": e.error}
                    raise

                # Sync users first (needed for mention resolution)
                users_synced = sync_workspace_users(client, workspace, session)

                # Sync channels
                channels_synced = sync_workspace_channels(client, workspace, session)

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
                    "users_synced": users_synced,
                    "channels_synced": channels_synced,
                    "channels_triggered": channels_triggered,
                }

        except SlackAPIError as e:
            workspace.sync_error = str(e)
            session.commit()
            return {"status": "error", "error": str(e)}
        except Exception as e:
            workspace.sync_error = str(e)
            session.commit()
            raise


def sync_workspace_users(client: httpx.Client, workspace: SlackWorkspace, session) -> int:
    """Sync users from Slack workspace to database."""
    synced = 0
    cursor = None

    while True:
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor

        response = slack_api_call(client, "users.list", **params)
        members = response.get("members", [])

        for member in members:
            user_id = member.get("id")
            if not user_id:
                continue

            profile = member.get("profile", {})

            existing = session.get(SlackUser, user_id)
            if existing:
                # Update existing user
                existing.username = member.get("name", existing.username)
                existing.display_name = profile.get("display_name") or None
                existing.real_name = profile.get("real_name") or None
                existing.email = profile.get("email") or None
                existing.is_bot = member.get("is_bot", False)
            else:
                # Create new user
                slack_user = SlackUser(
                    id=user_id,
                    workspace_id=workspace.id,
                    username=member.get("name", user_id),
                    display_name=profile.get("display_name") or None,
                    real_name=profile.get("real_name") or None,
                    email=profile.get("email") or None,
                    is_bot=member.get("is_bot", False),
                )
                session.add(slack_user)

            synced += 1

        # Handle pagination
        metadata = response.get("response_metadata", {})
        cursor = metadata.get("next_cursor")
        if not cursor:
            break

    session.commit()
    return synced


def sync_workspace_channels(client: httpx.Client, workspace: SlackWorkspace, session) -> int:
    """Sync channels from Slack workspace to database."""
    synced = 0
    cursor = None

    while True:
        params = {"types": "public_channel,private_channel,mpim,im", "limit": 200}
        if cursor:
            params["cursor"] = cursor

        response = slack_api_call(client, "conversations.list", **params)
        channels = response.get("channels", [])

        for channel in channels:
            channel_id = channel.get("id")
            if not channel_id:
                continue

            # Determine channel type
            if channel.get("is_im"):
                ch_type = "dm"
            elif channel.get("is_mpim"):
                ch_type = "mpim"
            elif channel.get("is_group") or channel.get("is_private"):
                ch_type = "group_dm"
            else:
                ch_type = "channel"

            # Get channel name (DMs don't have names)
            name = channel.get("name") or channel.get("user") or channel_id

            existing = session.get(SlackChannel, channel_id)
            if existing:
                existing.name = name
                existing.channel_type = ch_type
                existing.is_private = channel.get("is_private", False)
                existing.is_archived = channel.get("is_archived", False)
            else:
                slack_channel = SlackChannel(
                    id=channel_id,
                    workspace_id=workspace.id,
                    name=name,
                    channel_type=ch_type,
                    is_private=channel.get("is_private", False),
                    is_archived=channel.get("is_archived", False),
                )
                session.add(slack_channel)

            synced += 1

        metadata = response.get("response_metadata", {})
        cursor = metadata.get("next_cursor")
        if not cursor:
            break

    session.commit()
    return synced


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
        if not workspace or not workspace.access_token:
            return {"status": "error", "error": "No workspace token"}

        try:
            with get_slack_client(workspace) as client:
                messages_synced = 0
                oldest = channel.last_message_ts
                newest_ts = oldest

                while True:
                    params = {"channel": channel_id, "limit": 100}
                    if oldest:
                        params["oldest"] = oldest

                    response = slack_api_call(client, "conversations.history", **params)
                    messages = response.get("messages", [])

                    if not messages:
                        break

                    for msg in messages:
                        msg_ts = msg.get("ts")
                        if not msg_ts:
                            continue

                        # Track newest message for cursor
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

                    # Check for more messages
                    if not response.get("has_more"):
                        break

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
    client: httpx.Client,
    channel_id: str,
    thread_ts: str,
    workspace_id: str,
) -> int:
    """Fetch and queue thread replies."""
    messages_queued = 0

    try:
        response = slack_api_call(
            client, "conversations.replies",
            channel=channel_id, ts=thread_ts, limit=100
        )
        messages = response.get("messages", [])

        for msg in messages:
            msg_ts = msg.get("ts")
            # Skip the parent message (same ts as thread_ts)
            if not msg_ts or msg_ts == thread_ts:
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
        # Check if message exists
        existing = check_content_exists(
            session, SlackMessage,
            message_ts=message_ts, workspace_id=workspace_id
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

        # Get workspace for mention resolution
        workspace = session.get(SlackWorkspace, workspace_id)
        if not workspace:
            return {"status": "error", "error": "Workspace not found"}

        # Resolve mentions
        users_by_id = {u.id: u for u in workspace.users}
        resolved_content = resolve_mentions(content, users_by_id)

        # Download images from files
        saved_images = []
        if files and workspace.access_token:
            headers = {"Authorization": f"Bearer {workspace.access_token}"}
            for file_info in files:
                if file_info.get("mimetype", "").startswith("image/"):
                    url = file_info.get("url_private_download") or file_info.get("url_private")
                    if url:
                        path = download_slack_file(url, headers, message_ts, workspace_id)
                        if path:
                            saved_images.append(path)

        # Ensure author exists
        author = session.get(SlackUser, author_id)
        if not author:
            # Create placeholder user
            author = SlackUser(
                id=author_id,
                workspace_id=workspace_id,
                username=author_id,
            )
            session.add(author)
            session.flush()

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

        # Create content hash
        content_hash = hashlib.sha256(
            f"{workspace_id}:{message_ts}:{content}".encode()
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
            thread_ts=thread_ts,
            reply_count=reply_count,
            message_type=subtype or "message",
            edited_ts=edited_ts,
            reactions=reactions,
            files=files,
            resolved_content=resolved_content if resolved_content != content else None,
            images=saved_images or None,
        )

        result = process_content_item(message, session)
        return result
