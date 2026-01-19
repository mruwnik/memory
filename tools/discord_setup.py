import click
import requests



def make_invite(client_id: str | int) -> str:
    permissions = 2048 + 16 + 1024  # = 3088

    invite_url = f"https://discord.com/oauth2/authorize?client_id={str(client_id)}&scope=bot&permissions={permissions}"
    return invite_url


@click.command()
@click.option("--bot-token", type=str, required=True)
def generate_bot_invite_url(bot_token: str):
    """
    Generate the Discord bot invitation URL.

    Returns:
        URL that user can click to add bot to their server
    """
    # Get bot's client ID from the token (it's the first part before the first dot)
    # But safer to get it from the API
    try:
        headers = {"Authorization": f"Bot {bot_token}"}
        response = requests.get(
            "https://discord.com/api/v10/users/@me", headers=headers
        )
        response.raise_for_status()
        bot_info = response.json()
        client_id = bot_info["id"]
    except Exception as e:
        raise ValueError(f"Could not get bot info: {e}")

    invite_url = make_invite(client_id)
    click.echo(f"Bot invite URL: {invite_url}")


@click.command()
def create_channels():
    """Create Discord channels using the configured servers."""
    from memory.common.discord import load_servers

    click.echo("Loading Discord servers and creating channels...")
    load_servers()
    click.echo("Discord channels setup completed.")


@click.command()
@click.option("--bot-token", type=str, required=True, help="Discord bot token")
def add_bot_user(bot_token: str):
    """Add a Discord bot user to the system by fetching bot info from Discord API."""
    from memory.common.db.connection import make_session
    from memory.common.db.models import DiscordUser, DiscordBotUser

    # Fetch bot information from Discord API
    try:
        headers = {"Authorization": f"Bot {bot_token}"}
        response = requests.get(
            "https://discord.com/api/v10/users/@me", headers=headers
        )
        response.raise_for_status()
        bot_info = response.json()

        discord_id = int(bot_info["id"])
        username = bot_info["username"]
        display_name = bot_info.get("global_name")
        discriminator = bot_info.get("discriminator", "0")

        # Use discriminator if it's not "0" (new username system uses "0")
        if discriminator != "0":
            username = f"{username}#{discriminator}"

    except Exception as e:
        click.echo(f"Error: Could not fetch bot info from Discord API: {e}")
        return

    # Create email and name from Discord info
    email = f"{username.replace('#', '_')}@discord.bot"
    name = display_name or username

    with make_session() as session:
        # Get or create DiscordUser
        discord_user = (
            session.query(DiscordUser).filter(DiscordUser.id == discord_id).first()
        )
        if discord_user:
            click.echo(f"Found existing Discord user: {discord_user.username}")
            # Update username and display_name in case they changed
            discord_user.username = username
            discord_user.display_name = display_name
        else:
            discord_user = DiscordUser(
                id=discord_id,
                username=username,
                display_name=display_name,
            )
            session.add(discord_user)
            click.echo(f"Created new Discord user: {username}")

        # Get or create DiscordBotUser (search by api_key)
        bot_user = (
            session.query(DiscordBotUser)
            .filter(DiscordBotUser.api_key == bot_token)
            .first()
        )
        if bot_user:
            click.echo(f"Found existing bot user: {bot_user.name}")
            # Update email and name in case they changed
            bot_user.email = email
            bot_user.name = name
            # Ensure they're connected
            if discord_user not in bot_user.discord_users:
                bot_user.discord_users.append(discord_user)
                click.echo("Linked Discord user to bot user")
        else:
            bot_user = DiscordBotUser.create_with_api_key(
                discord_users=[discord_user],
                name=name,
                email=email,
                api_key=bot_token,
            )
            session.add(bot_user)
            click.echo(f"Created new bot user: {name}")
            click.echo("Linked Discord user to bot user")

        session.commit()

        click.echo("\n✓ Successfully configured Discord bot user:")
        click.echo(f"  Bot Name: {bot_user.name}")
        click.echo(f"  Bot Email: {bot_user.email}")
        click.echo(f"  API Key: {bot_user.api_key}")
        click.echo(f"  Discord ID: {discord_user.id}")
        click.echo(f"  Discord Username: {discord_user.username}")
        if display_name:
            click.echo(f"  Discord Display Name: {display_name}")

    click.echo("\n\nTo add the bot to your server, click the link below:")
    click.echo(f"Bot invite URL: {make_invite(discord_id)}")


@click.group()
def cli():
    """Discord setup utilities."""
    pass


cli.add_command(generate_bot_invite_url, name="generate-invite")
cli.add_command(create_channels, name="create-channels")
cli.add_command(add_bot_user, name="add-bot-user")


if __name__ == "__main__":
    cli()
