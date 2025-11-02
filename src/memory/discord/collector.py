"""
Discord message collector.

Core message collection functionality - stores Discord messages to database.
"""

import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands
from sqlalchemy.orm import Session, scoped_session

from memory.common import settings
from memory.common.db.connection import make_session
from memory.common.db.models import (
    DiscordServer,
    DiscordChannel,
    DiscordUser,
)
from memory.discord.commands import register_slash_commands
from memory.workers.tasks.discord import add_discord_message, edit_discord_message

logger = logging.getLogger(__name__)


def process_mentions(session: Session | scoped_session, message: str) -> str:
    """Convert username mentions (<@username>) to ID mentions (<@123456>)"""
    import re

    def replace_mention(match):
        mention_content = match.group(1)
        # If it's already numeric, leave it alone
        if mention_content.isdigit():
            return match.group(0)

        # Look up username in database
        user = (
            session.query(DiscordUser)
            .filter(DiscordUser.username == mention_content)
            .first()
        )

        if user:
            return f"<@{user.id}>"

        # If user not found, return original
        return match.group(0)

    return re.sub(r"<@([^>]+)>", replace_mention, message)


# Pure functions for Discord entity creation/updates
def create_or_update_server(
    session: Session | scoped_session, guild: discord.Guild | None
) -> DiscordServer | None:
    """Get or create DiscordServer record (pure DB operation)"""
    if not guild:
        return None

    server = session.query(DiscordServer).get(guild.id)

    if not server:
        server = DiscordServer(
            id=guild.id,
            name=guild.name,
            description=guild.description,
            member_count=guild.member_count,
        )
        session.add(server)
        session.flush()  # Get the ID
        logger.info(f"Created server record for {guild.name} ({guild.id})")
    else:
        # Update metadata
        server.name = guild.name
        server.description = guild.description
        server.member_count = guild.member_count
        server.last_sync_at = datetime.now(timezone.utc)

    return server


def determine_channel_metadata(channel) -> tuple[str, int | None, str]:
    """Pure function to determine channel type, server_id, and name"""
    if isinstance(channel, discord.DMChannel):
        desc = (
            f"DM with {channel.recipient.name}" if channel.recipient else "Unknown DM"
        )
        return ("dm", None, desc)
    elif isinstance(channel, discord.GroupChannel):
        return "group_dm", None, channel.name or "Group DM"
    elif isinstance(
        channel, (discord.TextChannel, discord.VoiceChannel, discord.Thread)
    ):
        return (
            channel.__class__.__name__.lower().replace("channel", ""),
            channel.guild.id,
            channel.name,
        )
    else:
        guild = getattr(channel, "guild", None)
        server_id = guild.id if guild else None
        name = getattr(channel, "name", f"Unknown-{channel.id}")
        return "unknown", server_id, name


def create_or_update_channel(
    session: Session | scoped_session, channel
) -> DiscordChannel | None:
    """Get or create DiscordChannel record (pure DB operation)"""
    if not channel:
        return None

    discord_channel = session.query(DiscordChannel).get(channel.id)

    if not discord_channel:
        channel_type, server_id, name = determine_channel_metadata(channel)
        discord_channel = DiscordChannel(
            id=channel.id,
            server_id=server_id,
            name=name,
            channel_type=channel_type,
        )
        session.add(discord_channel)
        session.flush()
        logger.debug(f"Created channel: {name}")
    elif hasattr(channel, "name"):
        discord_channel.name = channel.name

    return discord_channel


def create_or_update_user(
    session: Session | scoped_session, user: discord.User | discord.Member
) -> DiscordUser:
    """Get or create DiscordUser record (pure DB operation)"""
    if not user:
        return None

    discord_user = session.query(DiscordUser).get(user.id)

    if not discord_user:
        discord_user = DiscordUser(
            id=user.id,
            username=user.name,
            display_name=user.display_name,
        )
        session.add(discord_user)
        session.flush()
        logger.debug(f"Created user: {user.name}")
    else:
        # Update user info in case it changed
        discord_user.username = user.name
        discord_user.display_name = user.display_name

    return discord_user


def determine_message_metadata(
    message: discord.Message,
) -> tuple[str, int | None, int | None]:
    """Pure function to determine message type, reply_to_id, and thread_id"""
    message_type = "default"
    reply_to_id = None
    thread_id = None

    if message.reference and message.reference.message_id:
        message_type = "reply"
        reply_to_id = message.reference.message_id

    if hasattr(message.channel, "parent") and message.channel.parent:
        thread_id = message.channel.id

    return message_type, reply_to_id, thread_id


def should_track_message(
    server: DiscordServer | None,
    channel: DiscordChannel,
    user: DiscordUser,
) -> bool:
    """Pure function to determine if we should track this message"""
    if server and not server.track_messages:  # type: ignore
        return False

    if not channel.track_messages:
        return False

    if channel.channel_type in ("dm", "group_dm"):
        return bool(user.track_messages)

    # Default: track the message
    return True


def should_collect_bot_message(message: discord.Message) -> bool:
    """Pure function to determine if we should collect bot messages"""
    return not message.author.bot or settings.DISCORD_COLLECT_BOTS


def sync_guild_metadata(guild: discord.Guild) -> None:
    """Sync a single guild's metadata (functional approach)"""
    with make_session() as session:
        create_or_update_server(session, guild)

        for channel in guild.channels:
            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
                create_or_update_channel(session, channel)

        # Sync threads
        for thread in guild.threads:
            create_or_update_channel(session, thread)

        session.commit()


class MessageCollector(commands.Bot):
    """Discord bot that collects and stores messages (thin event handler)"""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        intents.dm_messages = True

        super().__init__(
            command_prefix="!memory_",  # Prefix to avoid conflicts
            intents=intents,
            help_command=None,  # Disable default help
        )
        logger.info(f"Initialized collector for {self.user}")

    async def setup_hook(self):
        """Register slash commands when the bot is ready."""

        if not (name := self.user.name):
            logger.error(f"Failed to get user name for {self.user}")
            return

        name = name.replace("-", "_").lower()
        try:
            register_slash_commands(self, name=name)
        except Exception as e:
            logger.error(f"Failed to register slash commands for {self.user.name}: {e}")
        logger.error(f"Registered slash commands for {self.user.name}")

    async def on_ready(self):
        """Called when bot connects to Discord"""
        logger.info(f"Discord collector connected as {self.user}")
        logger.info(f"Connected to {len(self.guilds)} servers")

        # Sync server and channel metadata
        await self.sync_servers_and_channels()

        try:
            await self.tree.sync()
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to sync slash commands: %s", exc)

        logger.info("Discord message collector ready")

    async def on_message(self, message: discord.Message):
        """Queue incoming message for database storage"""
        try:
            if should_collect_bot_message(message):
                # Ensure Discord entities exist in database first
                with make_session() as session:
                    create_or_update_user(session, message.author)
                    create_or_update_channel(session, message.channel)
                    if message.guild:
                        create_or_update_server(session, message.guild)

                    session.commit()

                # Extract image URLs from attachments
                image_urls = [
                    att.url
                    for att in message.attachments
                    if att.content_type and att.content_type.startswith("image/")
                ]

                # Determine message metadata (type, reply, thread)
                message_type, reply_to_id, thread_id = determine_message_metadata(
                    message
                )

                # Queue the message for processing
                add_discord_message.delay(
                    message_id=message.id,
                    channel_id=message.channel.id,
                    author_id=message.author.id,
                    recipient_id=self.user and self.user.id,
                    server_id=message.guild.id if message.guild else None,
                    content=message.content or "",
                    sent_at=message.created_at.isoformat(),
                    message_reference_id=reply_to_id,
                    message_type=message_type,
                    thread_id=thread_id,
                    image_urls=image_urls,
                )
        except Exception as e:
            logger.error(f"Error queuing message {message.id}: {e}")

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Queue message edit for database update"""
        try:
            edit_time = after.edited_at or datetime.now(timezone.utc)
            edit_discord_message.delay(
                message_id=after.id,
                content=after.content,
                edited_at=edit_time.isoformat(),
            )
        except Exception as e:
            logger.error(f"Error queuing message edit {after.id}: {e}")

    async def sync_servers_and_channels(self):
        """Sync server and channel metadata on startup"""
        for guild in self.guilds:
            sync_guild_metadata(guild)

        logger.info(f"Synced {len(self.guilds)} servers and their channels")

    async def refresh_metadata(self) -> dict[str, int]:
        """Refresh server and channel metadata from Discord and update database"""
        servers_updated = 0
        channels_updated = 0
        users_updated = 0

        with make_session() as session:
            # Refresh all servers
            for guild in self.guilds:
                create_or_update_server(session, guild)
                servers_updated += 1

                # Refresh all channels in this server
                for channel in guild.channels:
                    if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
                        create_or_update_channel(session, channel)
                        channels_updated += 1

                # Refresh all threads in this server
                for thread in guild.threads:
                    create_or_update_channel(session, thread)
                    channels_updated += 1

                # Refresh all members in this server (if members intent is enabled)
                if self.intents.members:
                    for member in guild.members:
                        create_or_update_user(session, member)
                        users_updated += 1

            session.commit()

        result = {
            "servers_updated": servers_updated,
            "channels_updated": channels_updated,
            "users_updated": users_updated,
        }

        print(f"✅ Metadata refresh complete: {result}")
        logger.info(f"Metadata refresh complete: {result}")

        return result

    async def get_user(self, user_identifier: int | str) -> discord.User | None:
        """Get a Discord user by ID or username"""
        if isinstance(user_identifier, int):
            # Direct user ID lookup
            if user := super().get_user(user_identifier):
                return user
            try:
                return await self.fetch_user(user_identifier)
            except discord.NotFound:
                return None
        else:
            # Username lookup - search through all guilds
            for guild in self.guilds:
                for member in guild.members:
                    if (
                        member.name == user_identifier
                        or member.display_name == user_identifier
                        or f"{member.name}#{member.discriminator}" == user_identifier
                    ):
                        return member
            return None

    async def get_channel_by_name(
        self, channel_name: str
    ) -> discord.TextChannel | None:
        """Get a Discord channel by name (does not create if missing)"""
        # Search all guilds for the channel
        for guild in self.guilds:
            for ch in guild.channels:
                if isinstance(ch, discord.TextChannel) and ch.name == channel_name:
                    return ch
        return None

    async def create_channel(
        self, channel_name: str, guild_id: int | None = None
    ) -> discord.TextChannel | None:
        """Create a Discord channel in the specified guild (or first guild if none specified)"""
        target_guild = None

        if guild_id:
            target_guild = self.get_guild(guild_id)
        elif self.guilds:
            target_guild = self.guilds[0]  # Default to first guild

        if not target_guild:
            logger.error(f"No guild available to create channel {channel_name}")
            return None

        try:
            channel = await target_guild.create_text_channel(channel_name)
            logger.info(f"Created channel {channel_name} in {target_guild.name}")
            return channel
        except Exception as e:
            logger.error(
                f"Failed to create channel {channel_name} in {target_guild.name}: {e}"
            )
            return None

    async def send_dm(self, user_identifier: int | str, message: str) -> bool:
        """Send a DM using this collector's Discord client"""
        try:
            user = await self.get_user(user_identifier)
            if not user:
                logger.error(f"User {user_identifier} not found")
                return False

            # Post-process mentions to convert usernames to IDs
            with make_session() as session:
                processed_message = process_mentions(session, message)

            await user.send(processed_message)
            logger.info(f"Sent DM to {user_identifier}")
            return True

        except Exception as e:
            logger.error(f"Failed to send DM to {user_identifier}: {e}")
            return False

    async def trigger_typing_dm(self, user_identifier: int | str) -> bool:
        """Trigger typing indicator in a DM"""
        try:
            user = await self.get_user(user_identifier)
            if not user:
                logger.error(f"User {user_identifier} not found")
                return False

            channel = user.dm_channel or await user.create_dm()
            if not channel:
                logger.error(f"DM channel not available for {user_identifier}")
                return False

            async with channel.typing():
                pass
            return True

        except Exception as e:
            logger.error(f"Failed to trigger DM typing for {user_identifier}: {e}")
            return False

    async def _get_channel(
        self, channel_identifier: int | str, check_notifications: bool = True
    ):
        """Get channel by ID or name with standard checks"""
        if check_notifications and not settings.DISCORD_NOTIFICATIONS_ENABLED:
            logger.debug("Discord notifications disabled")
            return None

        if isinstance(channel_identifier, int):
            channel = self.get_channel(channel_identifier)
        else:
            channel = await self.get_channel_by_name(channel_identifier)

        if not channel:
            logger.error(f"Channel {channel_identifier} not found")

        return channel

    async def send_to_channel(
        self, channel_identifier: int | str, message: str
    ) -> bool:
        """Send a message to a channel by name or ID (supports threads)"""
        try:
            channel = await self._get_channel(channel_identifier)
            if not channel:
                return False

            with make_session() as session:
                processed_message = process_mentions(session, message)

            await channel.send(processed_message)
            logger.info(f"Sent message to channel {channel_identifier}")
            return True

        except Exception as e:
            logger.error(f"Failed to send message to channel {channel_identifier}: {e}")
            return False

    async def trigger_typing_channel(self, channel_identifier: int | str) -> bool:
        """Trigger typing indicator in a channel by name or ID (supports threads)"""
        try:
            channel = await self._get_channel(channel_identifier)
            if not channel:
                return False

            async with channel.typing():
                pass
            return True

        except Exception as e:
            logger.error(
                f"Failed to trigger typing for channel {channel_identifier}: {e}"
            )
            return False

    async def add_reaction(
        self, channel_identifier: int | str, message_id: int, emoji: str
    ) -> bool:
        """Add a reaction to a message in a channel"""
        try:
            channel = await self._get_channel(channel_identifier)
            if not channel:
                return False

            message = await channel.fetch_message(message_id)
            await message.add_reaction(emoji)
            logger.info(f"Added reaction {emoji} to message {message_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to add reaction: {e}")
            return False
