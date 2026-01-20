"""Lightweight slash-command helpers for the Discord collector."""

import io
import logging
from dataclasses import dataclass
from collections.abc import Awaitable
from typing import Callable, Literal, cast

import discord

from memory.common.db.connection import DBSession, make_session
from memory.common.db.models import (
    DiscordChannel,
    DiscordServer,
    DiscordUser,
    MCPServer,
    MCPServerAssignment,
)
from memory.discord.mcp import run_mcp_server_command

logger = logging.getLogger(__name__)

ScopeLiteral = Literal["bot", "me", "server", "channel", "user"]


@dataclass
class DiscordObjects:
    bot: DiscordUser
    server: DiscordServer | None
    channel: DiscordChannel | None
    user: DiscordUser | None

    @property
    def items(self):
        items = [self.bot, self.server, self.channel, self.user]
        return [item for item in items if item is not None]


ListHandler = Callable[[DiscordObjects], str]


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

    session: DBSession
    interaction: discord.Interaction
    actor: DiscordUser
    scope: ScopeLiteral
    target: DiscordServer | DiscordChannel | DiscordUser
    display_name: str


CommandHandler = Callable[..., Awaitable[CommandResponse]]


async def respond(
    interaction: discord.Interaction, content: str, ephemeral: bool = True
) -> None:
    """Send a response to the interaction, as file if too large."""
    max_length = 1900
    if len(content) <= max_length:
        await interaction.response.send_message(content, ephemeral=ephemeral)
        return

    file = discord.File(io.BytesIO(content.encode("utf-8")), filename="response.txt")
    await interaction.response.send_message(
        "Response too large, sending as file:", file=file, ephemeral=ephemeral
    )


def with_object_context(
    bot: discord.Client,
    interaction: discord.Interaction,
    handler: ListHandler,
    user: discord.User | None,
) -> str:
    """Execute handler with Discord objects context."""
    server = interaction.guild
    channel = interaction.channel
    target_user = user or interaction.user
    bot_user = bot.user
    if not bot_user:
        raise CommandError("Bot user not available")
    with make_session() as session:
        objects = DiscordObjects(
            bot=ensure_user(session, cast(discord.abc.User, bot_user)),
            server=server and ensure_server(session, server),
            channel=channel
            and _ensure_channel(
                session, cast(discord.abc.Messageable, channel), server and server.id
            ),
            user=ensure_user(session, target_user),
        )
        return handler(objects)


def _create_scope_group(
    parent: discord.app_commands.Group,
    scope: ScopeLiteral,
    name: str,
    description: str,
) -> discord.app_commands.Group:
    """Create a command group for a scope (bot/me/server/channel).

    Args:
        parent: Parent command group
        scope: Scope literal (bot, me, server, channel)
        name: Group name
        description: Group description
    """
    group = discord.app_commands.Group(
        name=name, description=description, parent=parent
    )

    @group.command(name="prompt", description=f"Manage {name}'s system prompt")
    @discord.app_commands.describe(prompt="The system prompt to set")
    async def prompt_cmd(interaction: discord.Interaction, prompt: str | None = None):
        await _run_interaction_command(
            interaction, scope=scope, handler=handle_prompt, prompt=prompt
        )

    @group.command(name="chattiness", description=f"Show/set {name}'s chattiness")
    @discord.app_commands.describe(value="Optional new chattiness value (0-100)")
    async def chattiness_cmd(
        interaction: discord.Interaction, value: int | None = None
    ):
        await _run_interaction_command(
            interaction, scope=scope, handler=handle_chattiness, value=value
        )

    # Ignore command
    @group.command(name="ignore", description=f"Toggle bot ignoring {name} messages")
    @discord.app_commands.describe(enabled="Whether to ignore messages")
    async def ignore_cmd(interaction: discord.Interaction, enabled: bool | None = None):
        await _run_interaction_command(
            interaction, scope=scope, handler=handle_ignore, ignore_enabled=enabled
        )

    # Summary command
    @group.command(name="summary", description=f"Show {name}'s summary")
    async def summary_cmd(interaction: discord.Interaction):
        await _run_interaction_command(interaction, scope=scope, handler=handle_summary)

    # MCP command
    @group.command(name="mcp", description=f"Manage {name}'s MCP servers")
    @discord.app_commands.describe(
        action="Action to perform",
        url="MCP server URL (required for add, delete, connect, tools)",
    )
    async def mcp_cmd(
        interaction: discord.Interaction,
        action: Literal["list", "add", "delete", "connect", "tools"] = "list",
        url: str | None = None,
    ):
        await _run_interaction_command(
            interaction,
            scope=scope,
            handler=handle_mcp_servers,
            action=action,
            url=url and url.strip(),
        )

    # Proactive command
    @group.command(name="proactive", description=f"Configure {name}'s proactive check-ins")
    @discord.app_commands.describe(
        cron="Cron schedule (e.g., '0 9 * * *' for 9am daily) or 'off' to disable",
        prompt="Optional custom instructions for check-ins",
    )
    async def proactive_cmd(
        interaction: discord.Interaction,
        cron: str | None = None,
        prompt: str | None = None,
    ):
        await _run_interaction_command(
            interaction,
            scope=scope,
            handler=handle_proactive,
            cron=cron and cron.strip(),
            prompt=prompt,
        )

    return group


def _create_user_scope_group(
    parent: discord.app_commands.Group,
    name: str,
    description: str,
) -> discord.app_commands.Group:
    """Create command group for user scope (requires user parameter).

    Args:
        parent: Parent command group
        name: Group name
        description: Group description
    """
    group = discord.app_commands.Group(
        name=name, description=description, parent=parent
    )
    scope = "user"

    @group.command(name="prompt", description=f"Manage {name}'s system prompt")
    @discord.app_commands.describe(
        user="Target user", prompt="The system prompt to set"
    )
    async def prompt_cmd(
        interaction: discord.Interaction, user: discord.User, prompt: str | None = None
    ):
        await _run_interaction_command(
            interaction,
            scope=scope,
            handler=handle_prompt,
            target_user=user,
            prompt=prompt,
        )

    @group.command(name="chattiness", description=f"Show/set {name}'s chattiness")
    @discord.app_commands.describe(
        user="Target user", value="Optional new chattiness value (0-100)"
    )
    async def chattiness_cmd(
        interaction: discord.Interaction, user: discord.User, value: int | None = None
    ):
        await _run_interaction_command(
            interaction,
            scope=scope,
            handler=handle_chattiness,
            target_user=user,
            value=value,
        )

    # Ignore command
    @group.command(name="ignore", description=f"Toggle bot ignoring {name} messages")
    @discord.app_commands.describe(
        user="Target user", enabled="Whether to ignore messages"
    )
    async def ignore_cmd(
        interaction: discord.Interaction,
        user: discord.User,
        enabled: bool | None = None,
    ):
        await _run_interaction_command(
            interaction,
            scope=scope,
            handler=handle_ignore,
            target_user=user,
            ignore_enabled=enabled,
        )

    # Summary command
    @group.command(name="summary", description=f"Show {name}'s summary")
    @discord.app_commands.describe(user="Target user")
    async def summary_cmd(interaction: discord.Interaction, user: discord.User):
        await _run_interaction_command(
            interaction, scope=scope, handler=handle_summary, target_user=user
        )

    # MCP command
    @group.command(name="mcp", description=f"Manage {name}'s MCP servers")
    @discord.app_commands.describe(
        user="Target user",
        action="Action to perform",
        url="MCP server URL (required for add, delete, connect, tools)",
    )
    async def mcp_cmd(
        interaction: discord.Interaction,
        user: discord.User,
        action: Literal["list", "add", "delete", "connect", "tools"] = "list",
        url: str | None = None,
    ):
        await _run_interaction_command(
            interaction,
            scope=scope,
            handler=handle_mcp_servers,
            target_user=user,
            action=action,
            url=url and url.strip(),
        )

    # Proactive command
    @group.command(name="proactive", description=f"Configure {name}'s proactive check-ins")
    @discord.app_commands.describe(
        user="Target user",
        cron="Cron schedule (e.g., '0 9 * * *' for 9am daily) or 'off' to disable",
        prompt="Optional custom instructions for check-ins",
    )
    async def proactive_cmd(
        interaction: discord.Interaction,
        user: discord.User,
        cron: str | None = None,
        prompt: str | None = None,
    ):
        await _run_interaction_command(
            interaction,
            scope=scope,
            handler=handle_proactive,
            target_user=user,
            cron=cron and cron.strip(),
            prompt=prompt,
        )

    return group


def create_list_group(
    bot: discord.Client, parent: discord.app_commands.Group
) -> discord.app_commands.Group:
    """Create command group for listing settings.

    Args:
        parent: Parent command group
    """
    group = discord.app_commands.Group(
        name="list", description="List settings", parent=parent
    )

    @group.command(name="prompt", description="List full system prompt")
    @discord.app_commands.describe(user="Target user")
    async def prompt_cmd(
        interaction: discord.Interaction, user: discord.User | None = None
    ):
        def handler(objects: DiscordObjects) -> str:
            prompts = [o.xml_prompt() for o in objects.items if o.system_prompt]
            return "\n\n".join(prompts)

        res = with_object_context(bot, interaction, handler, user)
        await respond(interaction, res)

    @group.command(name="chattiness", description="Show {name}'s chattiness")
    @discord.app_commands.describe(user="Target user")
    async def chattiness_cmd(
        interaction: discord.Interaction, user: discord.User | None = None
    ):
        def handler(objects: DiscordObjects) -> str:
            values = [
                o.chattiness_threshold
                for o in objects.items
                if o.chattiness_threshold is not None
            ]
            val = min(values) if values else 50
            if objects.user:
                return f"Total current chattiness for {objects.user.username}: {val}"
            return f"Total current chattiness: {val}"

        res = with_object_context(bot, interaction, handler, user)
        await respond(interaction, res)

    @group.command(
        name="ignore", description="Does this bot ignore messages for this user?"
    )
    @discord.app_commands.describe(user="Target user")
    async def ignore_cmd(
        interaction: discord.Interaction,
        user: discord.User | None = None,
    ):
        def handler(objects: DiscordObjects) -> str:
            should_ignore = any(o.ignore_messages for o in objects.items)
            if should_ignore:
                return f"The bot ignores messages for {objects.user}."
            return f"The bot does not ignore messages for {objects.user}."

        res = with_object_context(bot, interaction, handler, user)
        await respond(interaction, res)

    @group.command(name="summary", description="Show the full summary")
    @discord.app_commands.describe(user="Target user")
    async def summary_cmd(
        interaction: discord.Interaction, user: discord.User | None = None
    ):
        def handler(objects: DiscordObjects) -> str:
            summaries = [o.xml_summary() for o in objects.items if o.summary]
            return "\n\n".join(summaries)

        res = with_object_context(bot, interaction, handler, user)
        await respond(interaction, res)

    @group.command(name="mcp", description="All used MCP servers")
    @discord.app_commands.describe(user="Target user")
    async def mcp_cmd(
        interaction: discord.Interaction, user: discord.User | None = None
    ):
        logger.error(f"Listing MCP servers for {user}")
        ids = [
            interaction.guild_id,
            interaction.channel_id,
            (user or interaction.user).id,
            bot.user.id if bot.user else None,
        ]
        with make_session() as session:
            mcp_servers = (
                session.query(MCPServer)
                .filter(
                    MCPServerAssignment.entity_id.in_(i for i in ids if i is not None)
                )
                .all()
            )
            mcp_servers = [mcp_server.as_xml() for mcp_server in mcp_servers]
            res = "\n\n".join(mcp_servers)
            await respond(interaction, res)

    return group


def register_slash_commands(bot: discord.Client) -> None:
    """Register the collector slash commands on the provided bot.

    Args:
        bot: Discord bot client
    """

    if getattr(bot, "_memory_commands_registered", False):
        return

    setattr(bot, "_memory_commands_registered", True)

    if not hasattr(bot, "tree"):
        raise RuntimeError("Bot instance does not support app commands")

    tree = cast(discord.app_commands.CommandTree, getattr(bot, "tree"))
    name = bot.user and bot.user.name.replace("-", "_").lower()

    # Create main command group
    memory_group = discord.app_commands.Group(
        name=name or "memory", description=f"{name} bot configuration and management"
    )

    # Create scope groups
    _create_scope_group(memory_group, "bot", "bot", "Bot-wide settings")
    _create_scope_group(memory_group, "me", "me", "Your personal settings")
    _create_scope_group(memory_group, "server", "server", "Server-wide settings")
    _create_scope_group(memory_group, "channel", "channel", "Channel-specific settings")
    _create_user_scope_group(memory_group, "user", "Manage other users' settings")
    create_list_group(bot, memory_group)

    # Register main group
    tree.add_command(memory_group)


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
            # Get bot from interaction client if needed for bot scope
            bot = getattr(interaction, "client", None)
            context = _build_context(session, interaction, scope, target_user, bot)
            response = await handler(context, **handler_kwargs)
            session.commit()
    except CommandError as exc:  # pragma: no cover - passthrough
        await respond(interaction, str(exc))
        return

    await respond(interaction, response.content, response.ephemeral)


def _build_context(
    session: DBSession,
    interaction: discord.Interaction,
    scope: ScopeLiteral,
    target_user: discord.User | None,
    bot: discord.Client | None = None,
) -> CommandContext:
    actor = ensure_user(session, interaction.user)

    # Determine target and display name based on scope
    if scope == "bot":
        if not bot or not bot.user:
            raise CommandError("Bot user is not available.")
        target = ensure_user(session, bot.user)
        display_name = f"bot **{bot.user.name}**"

    elif scope == "me":
        target = ensure_user(session, interaction.user)
        name = target.display_name or target.username
        display_name = f"you (**{name}**)"

    elif scope == "server":
        if interaction.guild is None:
            raise CommandError("This command can only be used inside a server.")
        target = ensure_server(session, interaction.guild)
        display_name = f"server **{target.name}**"

    elif scope == "channel":
        if interaction.channel is None or not hasattr(interaction.channel, "id"):
            raise CommandError("Unable to determine channel for this interaction.")
        target = _ensure_channel(
            session,
            cast(discord.abc.Messageable, interaction.channel),
            interaction.guild_id,
        )
        display_name = f"channel **#{target.name}**"

    elif scope == "user":
        if target_user is None:
            raise CommandError("A target user is required for this command.")
        target = ensure_user(session, target_user)
        name = target.display_name or target.username
        display_name = f"user **{name}**"

    else:
        raise CommandError(f"Unsupported scope '{scope}'.")

    return CommandContext(
        session=session,
        interaction=interaction,
        actor=actor,
        scope=scope,
        target=target,
        display_name=display_name,
    )


def ensure_server(session: DBSession, guild: discord.Guild) -> DiscordServer:
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
    session: DBSession,
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


def ensure_user(session: DBSession, discord_user: discord.abc.User) -> DiscordUser:
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


async def handle_prompt(
    context: CommandContext, *, prompt: str | None = None
) -> CommandResponse:
    if prompt is not None:
        prompt = prompt or None
        setattr(context.target, "system_prompt", prompt)
    else:
        prompt = getattr(context.target, "system_prompt", None)

    if prompt:
        content = f"Current prompt for {context.display_name}:\n\n{prompt}"
    else:
        content = f"No prompt configured for {context.display_name}."
    return CommandResponse(content=content)


async def handle_chattiness(
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


async def handle_ignore(
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


async def handle_summary(context: CommandContext) -> CommandResponse:
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
    # Map scope to database entity type
    entity_type = {
        "bot": "DiscordUser",
        "me": "DiscordUser",
        "user": "DiscordUser",
        "server": "DiscordServer",
        "channel": "DiscordChannel",
    }[context.scope]

    bot_user = getattr(getattr(context.interaction, "client", None), "user", None)

    try:
        res = await run_mcp_server_command(
            bot_user, action, url, entity_type, context.target.id
        )
        return CommandResponse(content=res)
    except Exception as exc:
        logger.error(f"Error running MCP server command: {exc}", exc_info=True)
        raise CommandError(f"Error: {exc}") from exc


async def handle_proactive(
    context: CommandContext,
    *,
    cron: str | None = None,
    prompt: str | None = None,
) -> CommandResponse:
    """Handle proactive check-in configuration."""
    from croniter import croniter

    model = context.target

    # If no arguments, show current settings
    if cron is None and prompt is None:
        current_cron = getattr(model, "proactive_cron", None)
        current_prompt = getattr(model, "proactive_prompt", None)

        if not current_cron:
            return CommandResponse(
                content=f"Proactive check-ins are disabled for {context.display_name}."
            )

        lines = [f"Proactive check-ins for {context.display_name}:"]
        lines.append(f"  Schedule: `{current_cron}`")
        if current_prompt:
            lines.append(f"  Prompt: {current_prompt}")
        return CommandResponse(content="\n".join(lines))

    # Handle cron setting
    if cron is not None:
        if cron.lower() == "off":
            setattr(model, "proactive_cron", None)
            return CommandResponse(
                content=f"Proactive check-ins disabled for {context.display_name}."
            )

        # Validate cron expression
        try:
            croniter(cron)
        except (ValueError, KeyError) as e:
            raise CommandError(
                f"Invalid cron expression: {cron}\n"
                "Examples:\n"
                "  `0 9 * * *` - 9am daily\n"
                "  `0 9,17 * * 1-5` - 9am and 5pm weekdays\n"
                "  `0 */4 * * *` - every 4 hours"
            ) from e

        setattr(model, "proactive_cron", cron)

    # Handle prompt setting
    if prompt is not None:
        setattr(model, "proactive_prompt", prompt or None)

    # Build response
    current_cron = getattr(model, "proactive_cron", None)
    current_prompt = getattr(model, "proactive_prompt", None)

    lines = [f"Updated proactive settings for {context.display_name}:"]
    lines.append(f"  Schedule: `{current_cron}`")
    if current_prompt:
        lines.append(f"  Prompt: {current_prompt}")

    return CommandResponse(content="\n".join(lines))
