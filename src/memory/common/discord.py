"""
Discord integration.

Simple HTTP client that communicates with the Discord collector's API server.
"""

import logging
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from sqlalchemy.orm.scoping import scoped_session

from memory.common import settings
from memory.common.db.models import DiscordServer

logger = logging.getLogger(__name__)


def get_api_url() -> str:
    """Get the Discord API server URL"""
    host = settings.DISCORD_COLLECTOR_SERVER_URL
    port = settings.DISCORD_COLLECTOR_PORT
    return f"http://{host}:{port}"


def send_dm(bot_id: int, user_identifier: str, message: str) -> bool:
    """Send a DM via the Discord collector API"""
    try:
        response = requests.post(
            f"{get_api_url()}/send_dm",
            json={"bot_id": bot_id, "user": user_identifier, "message": message},
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("success", False)

    except requests.RequestException as e:
        logger.error(f"Failed to send DM to {user_identifier}: {e}")
        return False


def trigger_typing_dm(bot_id: int, user_identifier: int | str) -> bool:
    """Trigger typing indicator for a DM via the Discord collector API"""
    try:
        response = requests.post(
            f"{get_api_url()}/typing/dm",
            json={"bot_id": bot_id, "user": user_identifier},
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("success", False)

    except requests.RequestException as e:
        logger.error(f"Failed to trigger DM typing for {user_identifier}: {e}")
        return False


def send_to_channel(bot_id: int, channel: int | str, message: str) -> bool:
    """Send message to a channel by name or ID (ID supports threads)"""
    try:
        response = requests.post(
            f"{get_api_url()}/send_channel",
            json={
                "bot_id": bot_id,
                "channel": channel,
                "message": message,
            },
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("success", False)

    except requests.RequestException as e:
        logger.error(f"Failed to send to channel {channel}: {e}")
        return False


def trigger_typing_channel(bot_id: int, channel: int | str) -> bool:
    """Trigger typing indicator for a channel by name or ID (ID supports threads)"""
    try:
        response = requests.post(
            f"{get_api_url()}/typing/channel",
            json={"bot_id": bot_id, "channel": channel},
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("success", False)

    except requests.RequestException as e:
        logger.error(f"Failed to trigger typing for channel {channel}: {e}")
        return False


def add_reaction(bot_id: int, channel: int | str, message_id: int, emoji: str) -> bool:
    """Add a reaction to a message in a channel"""
    try:
        response = requests.post(
            f"{get_api_url()}/add_reaction",
            json={
                "bot_id": bot_id,
                "channel": channel,
                "message_id": message_id,
                "emoji": emoji,
            },
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("success", False)

    except requests.RequestException as e:
        logger.error(
            f"Failed to add reaction {emoji} to message {message_id} in channel {channel}: {e}"
        )
        return False


def broadcast_message(bot_id: int, channel: int | str, message: str) -> bool:
    """Send a message to a channel by name or ID (ID supports threads)"""
    try:
        response = requests.post(
            f"{get_api_url()}/send_channel",
            json={
                "bot_id": bot_id,
                "channel": channel,
                "message": message,
            },
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("success", False)

    except requests.RequestException as e:
        logger.error(f"Failed to send message to channel {channel}: {e}")
        return False


def is_collector_healthy(bot_id: int) -> bool:
    """Check if the Discord collector is running and healthy"""
    try:
        response = requests.get(f"{get_api_url()}/health", timeout=5)
        response.raise_for_status()
        result = response.json()
        bot_status = result.get(str(bot_id))
        if not isinstance(bot_status, dict):
            return False
        return bool(bot_status.get("connected"))

    except requests.RequestException:
        return False


def refresh_discord_metadata() -> dict[str, Any] | None:
    """Refresh Discord server/channel/user metadata from Discord API"""
    try:
        response = requests.post(f"{get_api_url()}/refresh_metadata", timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to refresh Discord metadata: {e}")
        return None


# =============================================================================
# Role Management
# =============================================================================


def list_roles(bot_id: int, guild_id: int | str) -> dict[str, Any] | None:
    """List all roles in a guild (guild_id can be ID or name)."""
    try:
        response = requests.get(
            f"{get_api_url()}/guilds/{guild_id}/roles",
            params={"bot_id": bot_id},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to list roles for guild {guild_id}: {e}")
        return None


def list_role_members(bot_id: int, guild_id: int | str, role_id: int | str) -> dict[str, Any] | None:
    """List all members with a specific role (guild_id and role_id can be ID or string)."""
    try:
        response = requests.get(
            f"{get_api_url()}/guilds/{guild_id}/roles/{role_id}/members",
            params={"bot_id": bot_id},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to list members for role {role_id}: {e}")
        return None


def add_role_member(bot_id: int, guild_id: int | str, role_id: int | str, user_id: int | str) -> dict[str, Any] | None:
    """Add a user to a role (IDs can be int or string)."""
    try:
        response = requests.post(
            f"{get_api_url()}/roles/add_member",
            json={"bot_id": bot_id, "guild_id": guild_id, "role_id": role_id, "user_id": user_id},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to add user {user_id} to role {role_id}: {e}")
        return None


def remove_role_member(bot_id: int, guild_id: int | str, role_id: int | str, user_id: int | str) -> dict[str, Any] | None:
    """Remove a user from a role (IDs can be int or string)."""
    try:
        response = requests.post(
            f"{get_api_url()}/roles/remove_member",
            json={"bot_id": bot_id, "guild_id": guild_id, "role_id": role_id, "user_id": user_id},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to remove user {user_id} from role {role_id}: {e}")
        return None


def create_role(
    bot_id: int,
    guild_id: int | str,
    name: str,
    color: int | None = None,
    permissions: int | None = None,
    mentionable: bool = False,
    hoist: bool = False,
) -> dict[str, Any] | None:
    """Create a role in a Discord guild.

    Args:
        bot_id: Discord bot ID
        guild_id: Guild ID (int or string)
        name: Role name
        color: RGB color integer (optional)
        permissions: Permission bitfield (optional)
        mentionable: Whether the role is mentionable
        hoist: Whether to display role separately in member list

    Returns:
        Created role data or None on failure
    """
    try:
        response = requests.post(
            f"{get_api_url()}/guilds/{guild_id}/roles",
            json={
                "bot_id": bot_id,
                "name": name,
                "color": color,
                "permissions": permissions,
                "mentionable": mentionable,
                "hoist": hoist,
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to create role {name} in guild {guild_id}: {e}")
        return None


# =============================================================================
# Channel Permissions
# =============================================================================


def get_channel_permissions(bot_id: int, channel_id: int) -> dict[str, Any] | None:
    """Get permission overwrites for a channel."""
    try:
        response = requests.get(
            f"{get_api_url()}/channels/{channel_id}/permissions",
            params={"bot_id": bot_id},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to get permissions for channel {channel_id}: {e}")
        return None


def set_channel_permission(
    bot_id: int,
    channel_id: int | str,
    role_id: int | str | None = None,
    user_id: int | str | None = None,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
) -> dict[str, Any] | None:
    """Set permission overwrite for a role or user on a channel (IDs can be int or string)."""
    try:
        response = requests.post(
            f"{get_api_url()}/channels/set_permission",
            json={
                "bot_id": bot_id,
                "channel_id": channel_id,
                "role_id": role_id,
                "user_id": user_id,
                "allow": allow,
                "deny": deny,
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        body = ""
        if hasattr(e, "response") and e.response is not None:
            try:
                body = e.response.text
            except Exception:
                pass
        logger.error(f"Failed to set permissions for channel {channel_id}: {e} body={body}")
        return None


def remove_channel_permission(
    bot_id: int, channel_id: int, target_id: int, target_type: str = "role"
) -> dict[str, Any] | None:
    """Remove permission overwrite for a role or user from a channel."""
    try:
        response = requests.delete(
            f"{get_api_url()}/channels/{channel_id}/permissions/{target_id}",
            params={"bot_id": bot_id, "target_type": target_type},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to remove permissions for channel {channel_id}: {e}")
        return None


# =============================================================================
# Channel/Category Management
# =============================================================================


def list_categories(bot_id: int, guild_id: int | str) -> dict[str, Any] | None:
    """List all categories in a guild (guild_id can be ID or name)."""
    try:
        response = requests.get(
            f"{get_api_url()}/guilds/{guild_id}/categories",
            params={"bot_id": bot_id},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to list categories for guild {guild_id}: {e}")
        return None


def create_channel(
    bot_id: int,
    guild_id: int | str,
    name: str,
    category_id: int | str | None = None,
    category_name: str | None = None,
    topic: str | None = None,
    copy_permissions_from: int | str | None = None,
) -> dict[str, Any] | None:
    """Create a new text channel (guild_id can be ID or name)."""
    try:
        response = requests.post(
            f"{get_api_url()}/channels/create",
            json={
                "bot_id": bot_id,
                "guild_id": guild_id,
                "name": name,
                "category_id": category_id,
                "category_name": category_name,
                "topic": topic,
                "copy_permissions_from": copy_permissions_from,
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to create channel {name}: {e}")
        return None


def create_category(bot_id: int, guild_id: int | str, name: str) -> dict[str, Any] | None:
    """Create a new category (guild_id can be ID or name)."""
    try:
        response = requests.post(
            f"{get_api_url()}/categories/create",
            json={"bot_id": bot_id, "guild_id": guild_id, "name": name},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Failed to create category {name}: {e}")
        return None


def delete_channel(
    bot_id: int,
    channel_id: int | None = None,
    channel_name: str | None = None,
    guild_id: int | str | None = None,
) -> dict[str, Any] | None:
    """Delete a channel or category by ID or name (guild_id can be ID or name)."""
    try:
        response = requests.post(
            f"{get_api_url()}/channels/delete",
            json={
                "bot_id": bot_id,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "guild_id": guild_id,
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        identifier = channel_id or channel_name
        logger.error(f"Failed to delete channel {identifier}: {e}")
        return None


def edit_channel(
    bot_id: int,
    channel_id: int | str | None = None,
    channel_name: str | None = None,
    guild_id: int | str | None = None,
    new_name: str | None = None,
    new_topic: str | None = None,
    category_id: int | str | None = None,
    category_name: str | None = None,
) -> dict[str, Any] | None:
    """Edit a channel's properties (name, topic, category)."""
    try:
        response = requests.post(
            f"{get_api_url()}/channels/edit",
            json={
                "bot_id": bot_id,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "guild_id": guild_id,
                "new_name": new_name,
                "new_topic": new_topic,
                "category_id": category_id,
                "category_name": category_name,
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        identifier = channel_id or channel_name
        logger.error(f"Failed to edit channel {identifier}: {e}")
        return None


def find_channel_in_guild(
    bot_id: int,
    guild_id: int,
    channel_name: str,
) -> dict[str, Any] | None:
    """Find a channel by name in a guild.

    Args:
        bot_id: Discord bot ID
        guild_id: Discord guild ID
        channel_name: Channel name to find

    Returns:
        Channel dict with id, name, category etc., or None if not found
    """
    categories_result = list_categories(bot_id, guild_id)
    if not categories_result:
        return None

    # Search through all categories and their channels
    for category in categories_result.get("categories", []):
        for channel in category.get("channels", []):
            if channel.get("name", "").lower() == channel_name.lower():
                return {
                    "id": int(channel["id"]),
                    "name": channel["name"],
                    "category_id": int(category["id"]),
                    "category_name": category["name"],
                }

    return None


def upsert_channel(
    bot_id: int,
    guild_id: int,
    name: str,
    category_id: int | None = None,
    topic: str | None = None,
) -> dict[str, Any]:
    """Create or update a channel.

    If a channel with the given name exists in the guild, updates it.
    Otherwise creates a new channel.

    Args:
        bot_id: Discord bot ID
        guild_id: Discord guild ID
        name: Channel name
        category_id: Optional category ID to place channel in
        topic: Optional channel topic

    Returns:
        Dict with success status, channel info, and whether it was created or updated
    """
    # Check if channel exists
    existing = find_channel_in_guild(bot_id, guild_id, name)

    if existing:
        # Update existing channel
        result = edit_channel(
            bot_id,
            channel_id=existing["id"],
            new_topic=topic,
            category_id=category_id,
        )
        if result and result.get("success"):
            return {
                "success": True,
                "action": "updated",
                "channel": result.get("channel", existing),
            }
        return {
            "success": False,
            "action": "update_failed",
            "error": result.get("error") if result else "Unknown error",
        }

    # Create new channel
    result = create_channel(
        bot_id,
        guild_id,
        name,
        category_id=category_id,
        topic=topic,
    )
    if result and result.get("success"):
        return {
            "success": True,
            "action": "created",
            "channel": result.get("channel"),
        }
    return {
        "success": False,
        "action": "create_failed",
        "error": result.get("error") if result else "Unknown error",
    }


def find_category_in_guild(
    bot_id: int,
    guild_id: int,
    category_name: str,
) -> dict[str, Any] | None:
    """Find a category by name in a guild.

    Args:
        bot_id: Discord bot ID
        guild_id: Discord guild ID
        category_name: Category name to find

    Returns:
        Category dict with id, name, position, etc., or None if not found
    """
    categories_result = list_categories(bot_id, guild_id)
    if not categories_result:
        return None

    for category in categories_result.get("categories", []):
        if category.get("name", "").lower() == category_name.lower():
            return {
                "id": int(category["id"]),
                "name": category["name"],
                "position": category.get("position"),
            }

    return None


def upsert_category(
    bot_id: int,
    guild_id: int,
    name: str,
) -> dict[str, Any]:
    """Create or find a category.

    If a category with the given name exists in the guild, returns it.
    Otherwise creates a new category.

    Args:
        bot_id: Discord bot ID
        guild_id: Discord guild ID
        name: Category name

    Returns:
        Dict with success status, category info, and whether it was created or found
    """
    # Check if category exists
    existing = find_category_in_guild(bot_id, guild_id, name)

    if existing:
        return {
            "success": True,
            "action": "found",
            "category": existing,
        }

    # Create new category
    result = create_category(bot_id, guild_id, name)
    if result and result.get("success"):
        return {
            "success": True,
            "action": "created",
            "category": result.get("category"),
        }
    return {
        "success": False,
        "action": "create_failed",
        "error": result.get("error") if result else "Unknown error",
    }


def make_channel_private(
    bot_id: int,
    guild_id: int,
    channel_id: int,
) -> dict[str, Any] | None:
    """Make a channel private by denying @everyone view access.

    Args:
        bot_id: Discord bot ID
        guild_id: Discord guild ID (used as @everyone role ID)
        channel_id: Channel to make private

    Returns:
        Result dict or None on failure
    """
    # In Discord, guild_id == @everyone role ID
    return set_channel_permission(
        bot_id,
        channel_id,
        role_id=guild_id,  # @everyone role
        deny=["view_channel"],
    )


def grant_role_channel_access(
    bot_id: int,
    channel_id: int,
    role_id: int,
) -> dict[str, Any] | None:
    """Grant a role access to view and use a channel.

    Args:
        bot_id: Discord bot ID
        channel_id: Channel to grant access to
        role_id: Role to grant access

    Returns:
        Result dict or None on failure
    """
    return set_channel_permission(
        bot_id,
        channel_id,
        role_id=role_id,
        allow=["view_channel", "send_messages", "read_message_history"],
    )


# =============================================================================
# Resolution Helpers
# =============================================================================


def resolve_guild(guild: int | str | None, session: "Session | scoped_session[Session] | None" = None) -> int | None:
    """Resolve guild ID from either numeric ID or server name.

    Args:
        guild: Numeric guild ID (int or numeric string) or server name, or None
        session: Database session (required only for name lookups)

    Returns:
        Resolved guild ID as int, or None if guild is None

    Raises:
        ValueError: If guild is a name string but session is None, or name not found
    """
    if guild is None:
        return None

    # Try as numeric first
    if isinstance(guild, int):
        return guild
    try:
        return int(guild)
    except ValueError:
        pass

    # It's a name string - need database lookup
    if session is None:
        raise ValueError("Database session required to resolve guild by name")

    server = session.query(DiscordServer).filter(DiscordServer.name == guild).first()
    if not server:
        raise ValueError(f"Discord server '{guild}' not found")
    return server.id


def resolve_category(
    category: int | str | None,
    guild_id: int,
    bot_id: int,
) -> int | None:
    """Resolve category ID from either numeric ID or category name.

    Args:
        category: Numeric category ID or category name string, or None
        guild_id: Discord guild ID
        bot_id: Discord bot ID

    Returns:
        Category ID as int, or None if category is None

    Raises:
        ValueError: If category name not found
    """
    if category is None:
        return None

    # Try as numeric first
    if isinstance(category, int):
        return category
    try:
        return int(category)
    except ValueError:
        pass

    # It's a name - look up via Discord API
    categories_result = list_categories(bot_id, guild_id)
    if categories_result:
        for c in categories_result.get("categories", []):
            if c["name"].lower() == category.lower():
                return int(c["id"])

    raise ValueError(f"Discord category '{category}' not found in guild")


def resolve_role(
    role: int | str | None,
    guild_id: int,
    bot_id: int,
    create_if_missing: bool = False,
) -> tuple[int | None, bool]:
    """Resolve role ID from either numeric ID or role name.

    Args:
        role: Numeric role ID or role name string, or None
        guild_id: Discord guild ID
        bot_id: Discord bot ID
        create_if_missing: If True and role name doesn't exist, create it

    Returns:
        Tuple of (role_id or None, was_created)

    Raises:
        ValueError: If role name not found and create_if_missing is False
    """
    if role is None:
        return None, False

    # Try as numeric first
    if isinstance(role, int):
        return role, False
    try:
        return int(role), False
    except ValueError:
        pass

    # It's a name - look up via Discord API
    roles_result = list_roles(bot_id, guild_id)
    if roles_result:
        for r in roles_result.get("roles", []):
            if r["name"].lower() == role.lower():
                return int(r["id"]), False

    # Not found - create if requested
    if create_if_missing:
        new_role = create_role(bot_id, guild_id, name=role)
        if new_role and new_role.get("success") and new_role.get("role"):
            return int(new_role["role"]["id"]), True
        raise ValueError(f"Failed to create Discord role '{role}'")

    raise ValueError(f"Discord role '{role}' not found in guild")


# Convenience functions
def send_error_message(bot_id: int, message: str) -> bool:
    """Send an error message to the error channel"""
    return broadcast_message(bot_id, settings.DISCORD_ERROR_CHANNEL, message)


def send_activity_message(bot_id: int, message: str) -> bool:
    """Send an activity message to the activity channel"""
    return broadcast_message(bot_id, settings.DISCORD_ACTIVITY_CHANNEL, message)


def send_discovery_message(bot_id: int, message: str) -> bool:
    """Send a discovery message to the discovery channel"""
    return broadcast_message(bot_id, settings.DISCORD_DISCOVERY_CHANNEL, message)


def send_chat_message(bot_id: int, message: str) -> bool:
    """Send a chat message to the chat channel"""
    return broadcast_message(bot_id, settings.DISCORD_CHAT_CHANNEL, message)


def notify_task_failure(
    task_name: str,
    error_message: str,
    task_args: tuple = (),
    task_kwargs: dict[str, Any] | None = None,
    traceback_str: str | None = None,
    bot_id: int | None = None,
) -> None:
    """
    Send a task failure notification to Discord.

    Args:
        task_name: Name of the failed task
        error_message: Error message
        task_args: Task arguments
        task_kwargs: Task keyword arguments
        traceback_str: Full traceback string
    """
    if not settings.DISCORD_NOTIFICATIONS_ENABLED:
        logger.debug("Discord notifications disabled")
        return

    if bot_id is None:
        bot_id = settings.DISCORD_BOT_ID

    if not bot_id:
        logger.debug(
            "No Discord bot ID provided for task failure notification; skipping"
        )
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
        send_error_message(bot_id, message)
        logger.info(f"Discord error notification sent for task: {task_name}")
    except Exception as e:
        logger.error(f"Failed to send Discord notification: {e}")
