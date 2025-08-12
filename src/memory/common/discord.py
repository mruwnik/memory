import logging
import requests
import re
from typing import Any

from memory.common import settings

logger = logging.getLogger(__name__)

ERROR_CHANNEL = "memory-errors"
ACTIVITY_CHANNEL = "memory-activity"
DISCOVERY_CHANNEL = "memory-discoveries"
CHAT_CHANNEL = "memory-chat"


class DiscordServer(requests.Session):
    def __init__(self, server_id: str, server_name: str, *args, **kwargs):
        self.server_id = server_id
        self.server_name = server_name
        self.channels = {}
        super().__init__(*args, **kwargs)
        self.setup_channels()
        self.members = self.fetch_all_members()

    def setup_channels(self):
        resp = self.get(self.channels_url)
        resp.raise_for_status()
        channels = {channel["name"]: channel["id"] for channel in resp.json()}

        if not (error_channel := channels.get(settings.DISCORD_ERROR_CHANNEL)):
            error_channel = self.create_channel(settings.DISCORD_ERROR_CHANNEL)
        self.channels[ERROR_CHANNEL] = error_channel

        if not (activity_channel := channels.get(settings.DISCORD_ACTIVITY_CHANNEL)):
            activity_channel = self.create_channel(settings.DISCORD_ACTIVITY_CHANNEL)
        self.channels[ACTIVITY_CHANNEL] = activity_channel

        if not (discovery_channel := channels.get(settings.DISCORD_DISCOVERY_CHANNEL)):
            discovery_channel = self.create_channel(settings.DISCORD_DISCOVERY_CHANNEL)
        self.channels[DISCOVERY_CHANNEL] = discovery_channel

        if not (chat_channel := channels.get(settings.DISCORD_CHAT_CHANNEL)):
            chat_channel = self.create_channel(settings.DISCORD_CHAT_CHANNEL)
        self.channels[CHAT_CHANNEL] = chat_channel

    @property
    def error_channel(self) -> str:
        return self.channels[ERROR_CHANNEL]

    @property
    def activity_channel(self) -> str:
        return self.channels[ACTIVITY_CHANNEL]

    @property
    def discovery_channel(self) -> str:
        return self.channels[DISCOVERY_CHANNEL]

    @property
    def chat_channel(self) -> str:
        return self.channels[CHAT_CHANNEL]

    def channel_id(self, channel_name: str) -> str:
        if not (channel_id := self.channels.get(channel_name)):
            raise ValueError(f"Channel {channel_name} not found")
        return channel_id

    def send_message(self, channel_id: str, content: str):
        payload: dict[str, Any] = {"content": content}
        mentions = re.findall(r"@(\S*)", content)
        users = {u: i for u, i in self.members.items() if u in mentions}
        if users:
            for u, i in users.items():
                payload["content"] = payload["content"].replace(f"@{u}", f"<@{i}>")
            payload["allowed_mentions"] = {
                "parse": [],
                "users": list(users.values()),
            }

        return self.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            json=payload,
        )

    def create_channel(self, channel_name: str, channel_type: int = 0) -> str | None:
        resp = self.post(
            self.channels_url, json={"name": channel_name, "type": channel_type}
        )
        resp.raise_for_status()
        return resp.json()["id"]

    def __str__(self):
        return (
            f"DiscordServer(server_id={self.server_id}, server_name={self.server_name})"
        )

    def request(self, method: str, url: str, **kwargs):
        headers = kwargs.get("headers", {})
        headers["Authorization"] = f"Bot {settings.DISCORD_BOT_TOKEN}"
        headers["Content-Type"] = "application/json"
        kwargs["headers"] = headers
        return super().request(method, url, **kwargs)

    @property
    def channels_url(self) -> str:
        return f"https://discord.com/api/v10/guilds/{self.server_id}/channels"

    @property
    def members_url(self) -> str:
        return f"https://discord.com/api/v10/guilds/{self.server_id}/members"

    @property
    def dm_create_url(self) -> str:
        return "https://discord.com/api/v10/users/@me/channels"

    def list_members(
        self, limit: int = 1000, after: str | None = None
    ) -> list[dict[str, Any]]:
        """List up to `limit` members in this guild, starting after a user ID.

        Requires the bot to have the Server Members Intent enabled in the Discord developer portal.
        """
        params: dict[str, Any] = {"limit": limit}
        if after:
            params["after"] = after
        resp = self.get(self.members_url, params=params)
        resp.raise_for_status()
        return resp.json()

    def fetch_all_members(self, page_size: int = 1000) -> dict[str, str]:
        """Retrieve all members in the guild by paginating the members list.

        Note: Large guilds may take multiple requests. Rate limits are respected by requests.Session automatically.
        """
        members: dict[str, str] = {}
        after: str | None = None
        while batch := self.list_members(limit=page_size, after=after):
            for member in batch:
                user = member.get("user", {})
                members[user.get("global_name") or user.get("username", "")] = user.get(
                    "id", ""
                )
            after = user.get("id", "")
        return members

    def create_dm_channel(self, user_id: str) -> str:
        """Create (or retrieve) a DM channel with the given user and return the channel ID.

        The bot must share a guild with the user, and the user's privacy settings must allow DMs from server members.
        """
        resp = self.post(self.dm_create_url, json={"recipient_id": user_id})
        resp.raise_for_status()
        data = resp.json()
        return data["id"]

    def send_dm(self, user_id: str, content: str):
        """Send a direct message to a specific user by ID."""
        channel_id = self.create_dm_channel(self.members.get(user_id) or user_id)
        return self.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            json={"content": content},
        )


def get_bot_servers() -> list[dict[str, Any]]:
    """Get list of servers the bot is in."""
    if not settings.DISCORD_BOT_TOKEN:
        return []

    try:
        headers = {"Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}"}
        response = requests.get(
            "https://discord.com/api/v10/users/@me/guilds", headers=headers
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to get bot servers: {e}")
        return []


servers: dict[str, DiscordServer] = {}


def load_servers():
    for server in get_bot_servers():
        servers[server["id"]] = DiscordServer(server["id"], server["name"])


def broadcast_message(channel: str, message: str):
    if not settings.DISCORD_NOTIFICATIONS_ENABLED:
        return

    for server in servers.values():
        server.send_message(server.channel_id(channel), message)


def send_error_message(message: str):
    broadcast_message(ERROR_CHANNEL, message)


def send_activity_message(message: str):
    broadcast_message(ACTIVITY_CHANNEL, message)


def send_discovery_message(message: str):
    broadcast_message(DISCOVERY_CHANNEL, message)


def send_chat_message(message: str):
    broadcast_message(CHAT_CHANNEL, message)


def send_dm(user_id: str, message: str):
    for server in servers.values():
        if not server.members.get(user_id) and user_id not in server.members.values():
            continue

        server.send_dm(user_id, message)


def notify_task_failure(
    task_name: str,
    error_message: str,
    task_args: tuple = (),
    task_kwargs: dict[str, Any] | None = None,
    traceback_str: str | None = None,
) -> None:
    """
    Send a task failure notification to Discord.

    Args:
        task_name: Name of the failed task
        error_message: Error message
        task_args: Task arguments
        task_kwargs: Task keyword arguments
        traceback_str: Full traceback string

    Returns:
        True if notification sent successfully
    """
    if not settings.DISCORD_NOTIFICATIONS_ENABLED:
        logger.debug("Discord notifications disabled")
        return

    message = f"🚨 **Task Failed: {task_name}**\n\n"
    message += f"**Error:** {error_message[:500]}\n"

    if task_args:
        message += f"**Args:** `{str(task_args)[:200]}`\n"

    if task_kwargs:
        message += f"**Kwargs:** `{str(task_kwargs)[:200]}`\n"

    if traceback_str:
        message += f"**Traceback:**\n```\n{traceback_str[-800:]}\n```"

    try:
        send_error_message(message)
        logger.info(f"Discord error notification sent for task: {task_name}")
    except Exception as e:
        logger.error(f"Failed to send Discord notification: {e}")
