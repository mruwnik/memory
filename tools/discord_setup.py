"""Discord setup utilities for configuring bots and servers."""

import click
import requests


def make_invite(client_id: str | int) -> str:
    """Generate a Discord bot invite URL with required permissions."""
    # Discord permission bits - see https://discord.com/developers/docs/topics/permissions
    VIEW_CHANNEL = 1 << 10  # 1024
    SEND_MESSAGES = 1 << 11  # 2048
    EMBED_LINKS = 1 << 14  # 16384
    ATTACH_FILES = 1 << 15  # 32768
    READ_MESSAGE_HISTORY = 1 << 16  # 65536
    ADD_REACTIONS = 1 << 6  # 64
    MANAGE_CHANNELS = 1 << 4  # 16
    MANAGE_ROLES = 1 << 28  # 268435456

    permissions = (
        VIEW_CHANNEL
        | SEND_MESSAGES
        | EMBED_LINKS
        | ATTACH_FILES
        | READ_MESSAGE_HISTORY
        | ADD_REACTIONS
        | MANAGE_CHANNELS
        | MANAGE_ROLES
    )  # = 268553296

    invite_url = f"https://discord.com/oauth2/authorize?client_id={str(client_id)}&scope=bot&permissions={permissions}"
    return invite_url


def fetch_bot_info(bot_token: str) -> dict:
    """Fetch bot information from Discord API."""
    headers = {"Authorization": f"Bot {bot_token}"}
    response = requests.get(
        "https://discord.com/api/v10/users/@me", headers=headers
    )
    response.raise_for_status()
    return response.json()


@click.command()
@click.option("--bot-token", type=str, required=True)
def generate_bot_invite_url(bot_token: str):
    """Generate the Discord bot invitation URL."""
    try:
        bot_info = fetch_bot_info(bot_token)
        client_id = bot_info["id"]
    except Exception as e:
        raise click.ClickException(f"Could not get bot info: {e}")

    invite_url = make_invite(client_id)
    click.echo(f"Bot invite URL: {invite_url}")


@click.command()
@click.option("--bot-token", type=str, required=True, help="Discord bot token")
@click.option("--user-email", type=str, default=None, help="Email of bot owner (optional)")
def add_bot(bot_token: str, user_email: str | None):
    """Add a Discord bot to the system.

    This creates a DiscordBot record with the bot's credentials.
    Optionally set the user who owns the bot.
    """
    from memory.common.db.connection import make_session
    from memory.common.db.models import DiscordBot, HumanUser

    # Fetch bot information from Discord API
    try:
        bot_info = fetch_bot_info(bot_token)
        discord_id = int(bot_info["id"])
        username = bot_info["username"]
        display_name = bot_info.get("global_name")
        discriminator = bot_info.get("discriminator", "0")

        # Use discriminator if it's not "0" (new username system uses "0")
        if discriminator != "0":
            username = f"{username}#{discriminator}"

    except Exception as e:
        raise click.ClickException(f"Could not fetch bot info from Discord API: {e}")

    name = display_name or username

    with make_session() as session:
        # Get or create DiscordBot
        bot = session.get(DiscordBot, discord_id)
        if bot:
            click.echo(f"Found existing bot: {bot.name}")
            # Update name and token in case they changed
            bot.name = name
            bot.token = bot_token  # Uses encrypted setter
            click.echo("Updated bot token and name")
        else:
            bot = DiscordBot(
                id=discord_id,
                name=name,
            )
            bot.token = bot_token  # Uses encrypted setter
            session.add(bot)
            click.echo(f"Created new bot: {name}")

        # Optionally set the owner
        if user_email:
            user = session.query(HumanUser).filter(HumanUser.email == user_email).first()
            if not user:
                raise click.ClickException(f"User with email '{user_email}' not found")

            bot.owner = user
            click.echo(f"Set bot owner to '{user.name}'")

        session.commit()

        click.echo("\nSuccessfully configured Discord bot:")
        click.echo(f"  Bot ID: {bot.id}")
        click.echo(f"  Bot Name: {bot.name}")
        click.echo(f"  Active: {bot.is_active}")
        click.echo(f"  Owner: {bot.owner.email if bot.owner else 'unowned'}")

    click.echo("\n\nTo add the bot to your server, use this invite URL:")
    click.echo(f"  {make_invite(discord_id)}")


@click.command()
@click.option("--bot-id", type=int, required=True, help="Discord bot ID")
@click.option("--user-email", type=str, required=True, help="Email of bot owner")
def set_owner(bot_id: int, user_email: str):
    """Set the owner of an existing Discord bot."""
    from memory.common.db.connection import make_session
    from memory.common.db.models import DiscordBot, HumanUser

    with make_session() as session:
        bot = session.get(DiscordBot, bot_id)
        if not bot:
            raise click.ClickException(f"Bot with ID {bot_id} not found")

        user = session.query(HumanUser).filter(HumanUser.email == user_email).first()
        if not user:
            raise click.ClickException(f"User with email '{user_email}' not found")

        bot.owner = user
        session.commit()
        click.echo(f"Set owner of bot '{bot.name}' to '{user.name}'")



@click.command()
def list_bots():
    """List all configured Discord bots."""
    from memory.common.db.connection import make_session
    from memory.common.db.models import DiscordBot

    with make_session() as session:
        bots = session.query(DiscordBot).all()

        if not bots:
            click.echo("No Discord bots configured")
            return

        click.echo("Configured Discord Bots:")
        click.echo("-" * 60)
        for bot in bots:
            click.echo(f"  ID: {bot.id}")
            click.echo(f"  Name: {bot.name}")
            click.echo(f"  Active: {bot.is_active}")
            click.echo(f"  Has Token: {bot.token is not None}")
            click.echo(f"  Owner: {bot.owner.email if bot.owner else 'unowned'}")
            click.echo("-" * 60)


@click.group()
def cli():
    """Discord setup utilities."""
    pass


cli.add_command(generate_bot_invite_url, name="generate-invite")  # type: ignore[attr-defined]
cli.add_command(add_bot, name="add-bot")  # type: ignore[attr-defined]
cli.add_command(set_owner, name="set-owner")  # type: ignore[attr-defined]
cli.add_command(list_bots, name="list-bots")  # type: ignore[attr-defined]


if __name__ == "__main__":
    cli()
