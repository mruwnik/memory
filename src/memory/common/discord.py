import logging
import requests
from typing import Any, Dict, List

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
        self.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            json={"content": content},
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


def get_bot_servers() -> List[Dict]:
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
