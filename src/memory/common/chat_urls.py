"""Which servers Slack apps and Discord bots may be pointed at.

A Slack app's OAuth and Web API URLs and a Discord bot's REST URL are set by
whoever registers the row. Memory sends tokens and OAuth codes there, so only
Slack's and Discord's own origins, the operator's CHAT_API_ALLOWED_ORIGINS and
URLs an admin chose are used. Checked when a row is saved and again on use, so
a row saved under an older allowlist doesn't outlive it.
"""

from urllib.parse import urlparse

from memory.common import settings
from memory.common.access_control import UserLike, has_admin_scope

BUILTIN_ORIGINS = ("https://slack.com", "https://discord.com")


def origin(url: str) -> str:
    parsed = urlparse(url.strip())
    return f"{parsed.scheme}://{parsed.netloc}".lower().rstrip("/")


def allowed_origins() -> set[str]:
    return {*BUILTIN_ORIGINS, *(origin(o) for o in settings.CHAT_API_ALLOWED_ORIGINS)}


def chat_url_allowed(url: str, owner: UserLike | None) -> bool:
    if owner is not None and has_admin_scope(owner):
        return True
    return origin(url) in allowed_origins()
