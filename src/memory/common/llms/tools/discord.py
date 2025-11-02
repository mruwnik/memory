"""Discord tool for interacting with Discord."""

import textwrap
from datetime import datetime
from typing import Literal, cast
from memory.discord.messages import (
    upsert_scheduled_message,
    comm_channel_prompt,
    previous_messages,
)
from sqlalchemy import BigInteger
from memory.common.db.connection import make_session
from memory.common.db.models import (
    DiscordServer,
    DiscordChannel,
    DiscordUser,
    BotUser,
)
from memory.common.llms.tools import ToolDefinition, ToolInput, ToolHandler
from memory.common.discord import add_reaction


UpdateSummaryType = Literal["server", "channel", "user"]


def handle_update_summary_call(
    type: UpdateSummaryType, item_id: BigInteger
) -> ToolHandler:
    models = {
        "server": DiscordServer,
        "channel": DiscordChannel,
        "user": DiscordUser,
    }

    def handler(input: ToolInput = None) -> str:
        if isinstance(input, dict):
            summary = input.get("summary") or str(input)
        else:
            summary = str(input)

        try:
            with make_session() as session:
                model = models[type]
                model = session.get(model, item_id)
                model.summary = summary  # type: ignore
                session.commit()
        except Exception as e:
            return f"Error updating summary: {e}"
        return "Updated summary"

    handler.__doc__ = textwrap.dedent("""
        Handle a {type} summary update tool call.

        Args:
            summary: The new summary of the Discord {type}

        Returns:
            Response string
        """).format(type=type)
    return handler


def make_summary_tool(type: UpdateSummaryType, item_id: BigInteger) -> ToolDefinition:
    return ToolDefinition(
        name=f"update_{type}_summary",
        description=textwrap.dedent("""
                Use this to update the summary of this Discord {type} that is added to your context.

                This will overwrite the previous summary.
            """).format(type=type),
        input_schema={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": f"The new summary of the Discord {type}",
                }
            },
            "required": [],
        },
        function=handle_update_summary_call(type, item_id),
    )


def schedule_message(
    user_id: int,
    user: int | None,
    channel: int | None,
    model: str,
    message: str,
    date_time: datetime,
) -> str:
    with make_session() as session:
        call = upsert_scheduled_message(
            session,
            scheduled_time=date_time,
            message=message,
            user_id=user_id,
            model=model,
            discord_user=user,
            discord_channel=channel,
            system_prompt=comm_channel_prompt(session, user, channel),
        )

        session.commit()
        return cast(str, call.id)


def make_message_scheduler(
    bot: BotUser, user: int | None, channel: int | None, model: str
) -> ToolDefinition:
    bot_id = cast(int, bot.id)
    if user:
        channel_type = "from your chat with this user"
    elif channel:
        channel_type = "in this channel"
    else:
        raise ValueError("Either user or channel must be provided")

    def handler(input: ToolInput) -> str:
        if not isinstance(input, dict):
            raise ValueError("Input must be a dictionary")

        try:
            time = datetime.fromisoformat(input["date_time"])
        except ValueError:
            raise ValueError("Invalid date time format")
        except KeyError:
            raise ValueError("Date time is required")

        return schedule_message(bot_id, user, channel, model, input["message"], time)

    return ToolDefinition(
        name="schedule_message",
        description=textwrap.dedent("""
            Use this to schedule a message to be sent to yourself.

            At the specified date and time, your message will be sent to you, along with the most 
            recent messages {channel_type}.

            Normally you will be called with any incoming messages. But sometimes you might want to be
            able to trigger a call to yourself at a specific time, rather than waiting for the next call.
            This tool allows you to do that.
            So for example, if you were chatting with a Discord user, and you ask a question which needs to
            be answered right away, you can use this tool to schedule a check in 5 minutes time, to remind
            the user to answer the question.
        """).format(channel_type=channel_type),
        input_schema={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to send",
                },
                "date_time": {
                    "type": "string",
                    "description": "The date and time to send the message in ISO format (e.g., 2025-01-01T00:00:00Z)",
                },
            },
        },
        function=handler,
    )


def make_prev_messages_tool(user: int | None, channel: int | None) -> ToolDefinition:
    if user:
        channel_type = "from your chat with this user"
    elif channel:
        channel_type = "in this channel"
    else:
        raise ValueError("Either user or channel must be provided")

    def handler(input: ToolInput) -> str:
        if not isinstance(input, dict):
            raise ValueError("Input must be a dictionary")
        try:
            max_messages = int(input.get("max_messages") or 10)
            offset = int(input.get("offset") or 0)
        except ValueError:
            raise ValueError("Max messages and offset must be integers")

        if max_messages <= 0:
            raise ValueError("Max messages must be greater than 0")
        if offset < 0:
            raise ValueError("Offset must be greater than or equal to 0")

        with make_session() as session:
            messages = previous_messages(session, user, channel, max_messages, offset)
            return "\n\n".join([msg.title for msg in messages])

    return ToolDefinition(
        name="previous_messages",
        description=f"Get the previous N messages {channel_type}.",
        input_schema={
            "type": "object",
            "properties": {
                "max_messages": {
                    "type": "number",
                    "description": "The maximum number of messages to return",
                    "default": 10,
                },
                "offset": {
                    "type": "number",
                    "description": "The number of messages to offset the result by",
                    "default": 0,
                },
            },
        },
        function=handler,
    )


def make_add_reaction_tool(bot: BotUser, channel: DiscordChannel) -> ToolDefinition:
    bot_id = cast(int, bot.id)
    channel_id = channel and channel.id

    def handler(input: ToolInput) -> str:
        if not isinstance(input, dict):
            raise ValueError("Input must be a dictionary")
        try:
            emoji = input.get("emoji")
        except ValueError:
            raise ValueError("Emoji is required")
        if not emoji:
            raise ValueError("Emoji is required")

        try:
            message_id = int(input.get("message_id") or "no id")
        except ValueError:
            raise ValueError("Message ID is required")

        success = add_reaction(bot_id, channel_id, message_id, emoji)
        if not success:
            return "Failed to add reaction"
        return "Reaction added"

    return ToolDefinition(
        name="add_reaction",
        description="Add a reaction to a message in a channel",
        input_schema={
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "number",
                    "description": "The ID of the message to add the reaction to",
                },
                "emoji": {
                    "type": "string",
                    "description": "The emoji to add to the message",
                },
            },
        },
        function=handler,
    )


def make_discord_tools(
    bot: BotUser,
    author: DiscordUser | None,
    channel: DiscordChannel | None,
    model: str,
) -> dict[str, ToolDefinition]:
    author_id = author and author.id
    channel_id = channel and channel.id
    tools = [
        make_message_scheduler(bot, author_id, channel_id, model),
        make_prev_messages_tool(author_id, channel_id),
        make_summary_tool("channel", channel_id),
    ]
    if author:
        tools += [make_summary_tool("user", author_id)]
    if channel and channel.server:
        tools += [
            make_summary_tool("server", cast(BigInteger, channel.server_id)),
            make_add_reaction_tool(bot, channel),
        ]
    return {tool.name: tool for tool in tools}
