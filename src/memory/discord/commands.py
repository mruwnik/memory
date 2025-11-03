"""Lightweight slash-command helpers for the Discord collector."""

import logging
from dataclasses import dataclass
from typing import Callable, Literal

import discord
from sqlalchemy.orm import Session

from memory.common.db.connection import make_session
from memory.common.db.models import DiscordChannel, DiscordServer, DiscordUser
from memory.discord.mcp import run_mcp_server_command

logger = logging.getLogger(__name__)

ScopeLiteral = Literal["bot", "server", "channel", "user"]


class CommandError(Exception):
    """Raised when a user-facing error occurs while handling a command."""


@dataclass(slots=True)
class CommandResponse:
    """Value object returned by handlers."""

    content: str
    ephemeral: bool = True


@dataclass(slots=True)
class CommandContext:
    """All information a handler needs to fulfil a command."""

    session: Session
    interaction: discord.Interaction
    actor: DiscordUser
    scope: ScopeLiteral
    target: DiscordServer | DiscordChannel | DiscordUser
    display_name: str


CommandHandler = Callable[..., CommandResponse]


def register_slash_commands(bot: discord.Client) -> None:
    """Register the collector slash commands on the provided bot.

    Args:
        bot: Discord bot client
        name: Prefix for command names (e.g., "memory" creates "memory_prompt")
    """

    if getattr(bot, "_memory_commands_registered", False):
        return

    setattr(bot, "_memory_commands_registered", True)

    if not hasattr(bot, "tree"):
        raise RuntimeError("Bot instance does not support app commands")

    tree = bot.tree
    name = bot.user and bot.user.name.replace("-", "_").lower()

    @tree.command(
        name=f"{name}_show_prompt", description="Show the current system prompt"
    )
    @discord.app_commands.describe(
        scope="Which configuration to inspect",
        user="Target user when the scope is 'user'",
    )
    async def show_prompt_command(
        interaction: discord.Interaction,
        scope: ScopeLiteral,
        user: discord.User | None = None,
    ) -> None:
        await _run_interaction_command(
            interaction,
            scope=scope,
            handler=handle_prompt,
            target_user=user,
        )

    @tree.command(
        name=f"{name}_set_prompt",
        description="Set the system prompt for the target",
    )
    @discord.app_commands.describe(
        scope="Which configuration to modify",
        prompt="The system prompt to set",
        user="Target user when the scope is 'user'",
    )
    async def set_prompt_command(
        interaction: discord.Interaction,
        scope: ScopeLiteral,
        prompt: str,
        user: discord.User | None = None,
    ) -> None:
        await _run_interaction_command(
            interaction,
            scope=scope,
            handler=handle_set_prompt,
            target_user=user,
            prompt=prompt,
        )

    @tree.command(
        name=f"{name}_chattiness",
        description="Show or update the chattiness for the target",
    )
    @discord.app_commands.describe(
        scope="Which configuration to inspect",
        value="Optional new chattiness value between 0 and 100",
        user="Target user when the scope is 'user'",
    )
    async def chattiness_command(
        interaction: discord.Interaction,
        scope: ScopeLiteral,
        value: int | None = None,
        user: discord.User | None = None,
    ) -> None:
        await _run_interaction_command(
            interaction,
            scope=scope,
            handler=handle_chattiness,
            target_user=user,
            value=value,
        )

    @tree.command(
        name=f"{name}_ignore",
        description="Toggle whether the bot should ignore messages for the target",
    )
    @discord.app_commands.describe(
        scope="Which configuration to modify",
        enabled="Optional flag. Leave empty to enable ignoring.",
        user="Target user when the scope is 'user'",
    )
    async def ignore_command(
        interaction: discord.Interaction,
        scope: ScopeLiteral,
        enabled: bool | None = None,
        user: discord.User | None = None,
    ) -> None:
        await _run_interaction_command(
            interaction,
            scope=scope,
            handler=handle_ignore,
            target_user=user,
            ignore_enabled=enabled,
        )

    @tree.command(
        name=f"{name}_show_summary",
        description="Show the stored summary for the target",
    )
    @discord.app_commands.describe(
        scope="Which configuration to inspect",
        user="Target user when the scope is 'user'",
    )
    async def summary_command(
        interaction: discord.Interaction,
        scope: ScopeLiteral,
        user: discord.User | None = None,
    ) -> None:
        await _run_interaction_command(
            interaction,
            scope=scope,
            handler=handle_summary,
            target_user=user,
        )

    @tree.command(
        name=f"{name}_mcp_servers",
        description="Manage MCP servers for a scope",
    )
    @discord.app_commands.describe(
        scope="Which configuration to modify (server, channel, or user)",
        action="Action to perform",
        url="MCP server URL (required for add, delete, connect, tools)",
        user="Target user when the scope is 'user'",
    )
    async def mcp_servers_command(
        interaction: discord.Interaction,
        scope: ScopeLiteral,
        action: Literal["list", "add", "delete", "connect", "tools"] = "list",
        url: str | None = None,
        user: discord.User | None = None,
    ) -> None:
        await _run_interaction_command(
            interaction,
            scope=scope,
            handler=handle_mcp_servers,
            target_user=user,
            action=action,
            url=url and url.strip(),
        )


async def _run_interaction_command(
    interaction: discord.Interaction,
    *,
    scope: ScopeLiteral,
    handler: CommandHandler,
    target_user: discord.User | None = None,
    **handler_kwargs,
) -> None:
    """Shared coroutine used by the registered slash commands."""
    try:
        with make_session() as session:
            context = _build_context(session, interaction, scope, target_user)
            response = await handler(context, **handler_kwargs)
            session.commit()
    except CommandError as exc:  # pragma: no cover - passthrough
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    await interaction.response.send_message(
        response.content,
        ephemeral=response.ephemeral,
    )


def _build_context(
    session: Session,
    interaction: discord.Interaction,
    scope: ScopeLiteral,
    target_user: discord.User | None,
) -> CommandContext:
    actor = _ensure_user(session, interaction.user)

    if scope == "server":
        if interaction.guild is None:
            raise CommandError("This command can only be used inside a server.")

        target = _ensure_server(session, interaction.guild)
        display_name = f"server **{target.name}**"
        return CommandContext(
            session=session,
            interaction=interaction,
            actor=actor,
            scope=scope,
            target=target,
            display_name=display_name,
        )

    if scope == "channel":
        channel_obj = interaction.channel
        if channel_obj is None or not hasattr(channel_obj, "id"):
            raise CommandError("Unable to determine channel for this interaction.")

        target = _ensure_channel(session, channel_obj, interaction.guild_id)
        display_name = f"channel **#{target.name}**"
        return CommandContext(
            session=session,
            interaction=interaction,
            actor=actor,
            scope=scope,
            target=target,
            display_name=display_name,
        )

    if scope == "user":
        discord_user = target_user or interaction.user
        if discord_user is None:
            raise CommandError("A target user is required for this command.")

        target = _ensure_user(session, discord_user)
        display_name = target.display_name or target.username
        return CommandContext(
            session=session,
            interaction=interaction,
            actor=actor,
            scope=scope,
            target=target,
            display_name=f"user **{display_name}**",
        )

    raise CommandError(f"Unsupported scope '{scope}'.")


def _ensure_server(session: Session, guild: discord.Guild) -> DiscordServer:
    server = session.get(DiscordServer, guild.id)
    if server is None:
        server = DiscordServer(
            id=guild.id,
            name=guild.name or f"Server {guild.id}",
            description=getattr(guild, "description", None),
            member_count=getattr(guild, "member_count", None),
        )
        session.add(server)
        session.flush()
    else:
        if guild.name and server.name != guild.name:
            server.name = guild.name
        description = getattr(guild, "description", None)
        if description and server.description != description:
            server.description = description
        member_count = getattr(guild, "member_count", None)
        if member_count is not None:
            server.member_count = member_count

    return server


def _ensure_channel(
    session: Session,
    channel: discord.abc.Messageable,
    guild_id: int | None,
) -> DiscordChannel:
    channel_id = getattr(channel, "id", None)
    if channel_id is None:
        raise CommandError("Channel is missing an identifier.")

    channel_model = session.get(DiscordChannel, channel_id)
    if channel_model is None:
        channel_model = DiscordChannel(
            id=channel_id,
            server_id=guild_id,
            name=getattr(channel, "name", f"Channel {channel_id}"),
            channel_type=_resolve_channel_type(channel),
        )
        session.add(channel_model)
        session.flush()
    else:
        name = getattr(channel, "name", None)
        if name and channel_model.name != name:
            channel_model.name = name

    return channel_model


def _ensure_user(session: Session, discord_user: discord.abc.User) -> DiscordUser:
    user = session.get(DiscordUser, discord_user.id)
    display_name = getattr(discord_user, "display_name", discord_user.name)
    if user is None:
        user = DiscordUser(
            id=discord_user.id,
            username=discord_user.name,
            display_name=display_name,
        )
        session.add(user)
        session.flush()
    else:
        if user.username != discord_user.name:
            user.username = discord_user.name
        if display_name and user.display_name != display_name:
            user.display_name = display_name

    return user


def _resolve_channel_type(channel: discord.abc.Messageable) -> str:
    if isinstance(channel, discord.DMChannel):
        return "dm"
    if isinstance(channel, discord.GroupChannel):
        return "group_dm"
    if isinstance(channel, discord.Thread):
        return "thread"
    if isinstance(channel, discord.VoiceChannel):
        return "voice"
    if isinstance(channel, discord.TextChannel):
        return "text"
    return getattr(getattr(channel, "type", None), "name", "unknown")


def handle_prompt(context: CommandContext) -> CommandResponse:
    prompt = getattr(context.target, "system_prompt", None)

    if prompt:
        return CommandResponse(
            content=f"Current prompt for {context.display_name}:\n\n{prompt}",
        )

    return CommandResponse(
        content=f"No prompt configured for {context.display_name}.",
    )


def handle_set_prompt(
    context: CommandContext,
    *,
    prompt: str,
) -> CommandResponse:
    setattr(context.target, "system_prompt", prompt)

    return CommandResponse(
        content=f"Updated system prompt for {context.display_name}.",
    )


def handle_chattiness(
    context: CommandContext,
    *,
    value: int | None,
) -> CommandResponse:
    model = context.target

    if value is None:
        return CommandResponse(
            content=(
                f"Chattiness for {context.display_name}: "
                f"{getattr(model, 'chattiness_threshold', 'not set')}"
            )
        )

    if not 0 <= value <= 100:
        raise CommandError("Chattiness must be between 0 and 100.")

    setattr(model, "chattiness_threshold", value)

    return CommandResponse(
        content=(
            f"Updated chattiness for {context.display_name} to {value}."
            "\n"
            "This can be treated as how much you want the bot to pipe up by itself, as a percentage, "
            "where 0 is never and 100 is always."
        )
    )


def handle_ignore(
    context: CommandContext,
    *,
    ignore_enabled: bool | None,
) -> CommandResponse:
    model = context.target
    new_value = True if ignore_enabled is None else ignore_enabled
    setattr(model, "ignore_messages", new_value)

    verb = "now ignoring" if new_value else "no longer ignoring"
    return CommandResponse(
        content=f"The bot is {verb} messages for {context.display_name}.",
    )


def handle_summary(context: CommandContext) -> CommandResponse:
    summary = getattr(context.target, "summary", None)

    if summary:
        return CommandResponse(
            content=f"Summary for {context.display_name}:\n\n{summary}",
        )

    return CommandResponse(
        content=f"No summary stored for {context.display_name}.",
    )


async def handle_mcp_servers(
    context: CommandContext,
    *,
    action: Literal["list", "add", "delete", "connect", "tools"],
    url: str | None,
) -> CommandResponse:
    """Handle MCP server commands for a specific scope."""
    entity_type_map = {
        "server": "DiscordServer",
        "channel": "DiscordChannel",
        "user": "DiscordUser",
    }
    entity_type = entity_type_map[context.scope]
    entity_id = context.target.id
    try:
        res = await run_mcp_server_command(
            context.interaction.user, action, url, entity_type, entity_id
        )
        return CommandResponse(content=res)
    except Exception as exc:
        import traceback

        logger.error(f"Error running MCP server command: {traceback.format_exc()}")
        raise CommandError(f"Error: {exc}") from exc
