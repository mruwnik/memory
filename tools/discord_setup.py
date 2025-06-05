import argparse
import click
import requests


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

    # Permissions needed: Send Messages (2048) + Manage Channels (16) + View Channels (1024)
    permissions = 2048 + 16 + 1024  # = 3088

    invite_url = f"https://discord.com/oauth2/authorize?client_id={client_id}&scope=bot&permissions={permissions}"
    click.echo(f"Bot invite URL: {invite_url}")
    return invite_url


@click.command()
def create_channels():
    """Create Discord channels using the configured servers."""
    from memory.common.discord import load_servers

    click.echo("Loading Discord servers and creating channels...")
    load_servers()
    click.echo("Discord channels setup completed.")


@click.group()
def cli():
    """Discord setup utilities."""
    pass


cli.add_command(generate_bot_invite_url, name="generate-invite")
cli.add_command(create_channels, name="create-channels")


if __name__ == "__main__":
    cli()
