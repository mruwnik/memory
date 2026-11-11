"""A small client for the Discord REST API, or any server speaking it.

The API URL is configured per bot, so requests go through the address-checked
``public_http_client``; discord.py's process-wide ``Route.BASE`` can't serve
several base URLs.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from memory.common.chat_urls import chat_url_allowed
from memory.common.db.models.discord import DiscordBot
from memory.common.safe_http import public_http_client

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
# A server asking for a longer wait (or still limiting after the retries) skips
# this run rather than holding a worker.
MAX_RETRY_AFTER_SECONDS = 30.0
PAGE_SIZE = 100
# Discord error codes meaning the bot can't read something; callers skip it.
UNREACHABLE_CODES = frozenset({10003, 10004, 50001, 50013})
RATE_LIMITED = 429
TRANSPORT_FAILED = 0


@dataclass(frozen=True)
class BotAccess:
    bot_id: int
    token: str
    api_base_url: str
    push_only: bool


def bot_access(bot: DiscordBot | None) -> BotAccess | None:
    """How to call the REST API as this bot: None when it is paused, has no
    token, or its API URL is no longer on the chat API allowlist."""
    if bot is None or not bot.is_active or not bot.token:
        return None
    if not chat_url_allowed(bot.api_base_url, bot.owner):
        logger.warning("Discord bot %s: API URL is not on the allowlist", bot.id)
        return None
    return BotAccess(
        bot_id=bot.id,
        token=bot.token,
        api_base_url=bot.api_base_url,
        push_only=not bot.runs_gateway,
    )


class DiscordAPIError(Exception):
    """Any failed call: an error status, an unparseable reply (status kept) or
    a transport failure (status 0)."""

    def __init__(self, status: int, code: int | None):
        self.status = status
        self.code = code
        super().__init__(f"Discord API error {status} (code {code})")

    @property
    def unreachable(self) -> bool:
        return self.status in (403, 404) or self.code in UNREACHABLE_CODES

    @property
    def skippable(self) -> bool:
        """The thing can't be read this run: no access, or rate limited."""
        return self.unreachable or self.status == RATE_LIMITED


class DiscordRest:
    def __init__(self, access: BotAccess, *, retries: int = MAX_RETRIES, timeout: float = 30.0):
        self.base_url = access.api_base_url.rstrip("/")
        self.token = access.token
        self.retries = retries
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "DiscordRest":
        self._client = public_http_client(
            headers={"Authorization": f"Bot {self.token}"}, timeout=self.timeout
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def request(self, method: str, path: str, json: Any = None, **params: Any) -> Any:
        """Call the API, retrying rate limits, 5xx and dropped connections a few times."""
        if self._client is None:
            raise RuntimeError("DiscordRest must be used as an async context manager")
        query = {k: v for k, v in params.items() if v is not None}
        for attempt in range(self.retries + 1):
            try:
                response = await self._client.request(
                    method, f"{self.base_url}{path}", params=query, json=json
                )
            except httpx.TransportError:
                if attempt == self.retries:
                    raise DiscordAPIError(TRANSPORT_FAILED, None)
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code == RATE_LIMITED:
                wait = retry_after(response)
                if wait > MAX_RETRY_AFTER_SECONDS or attempt == self.retries:
                    raise DiscordAPIError(RATE_LIMITED, None)
                await asyncio.sleep(wait)
                continue
            if response.status_code >= 500 and attempt < self.retries:
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code >= 400:
                raise DiscordAPIError(response.status_code, error_body(response).get("code"))
            if response.status_code == 204:
                return None
            try:
                return response.json()
            except ValueError:
                raise DiscordAPIError(response.status_code, None)
        raise DiscordAPIError(TRANSPORT_FAILED, None)

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, **params)

    async def send_message(self, channel_id: int, content: str) -> dict:
        return await self.request("POST", f"/channels/{channel_id}/messages", json={"content": content})

    async def open_dm(self, user_id: int) -> int:
        channel = await self.request("POST", "/users/@me/channels", json={"recipient_id": str(user_id)})
        return int(channel["id"])

    async def add_reaction(self, channel_id: int, message_id: int, emoji: str) -> None:
        await self.request(
            "PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji, safe='')}/@me"
        )

    async def reaction_users(self, channel_id: int, message_id: int, emoji: str, *, after: int | None = None) -> list[dict]:
        """One page of the users who reacted with this emoji (``name:id`` for a custom one), by id."""
        path = f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji, safe='')}"
        return await self.get(path, after=after, limit=PAGE_SIZE)

    async def messages(self, channel_id: int, *, before: int | None = None) -> list[dict]:
        """One page, newest first."""
        return await self.get(f"/channels/{channel_id}/messages", before=before, limit=PAGE_SIZE)

    async def channel(self, channel_id: int) -> dict:
        return await self.get(f"/channels/{channel_id}")

    async def my_guilds(self) -> list[dict]:
        return await self.get("/users/@me/guilds")

    async def guild_channels(self, guild_id: int) -> list[dict]:
        return await self.get(f"/guilds/{guild_id}/channels")

    async def active_threads(self, guild_id: int) -> list[dict]:
        return (await self.get(f"/guilds/{guild_id}/threads/active")).get("threads", [])

    async def archived_threads(self, channel_id: int, private: bool) -> list[dict]:
        kind = "private" if private else "public"
        threads: list[dict] = []
        before = None
        while True:
            page = await self.get(
                f"/channels/{channel_id}/threads/archived/{kind}", before=before, limit=PAGE_SIZE
            )
            batch = page.get("threads", [])
            threads.extend(batch)
            if not page.get("has_more") or not batch:
                return threads
            before = batch[-1].get("thread_metadata", {}).get("archive_timestamp")


async def token_channel(access: BotAccess, channel_id: int) -> dict | None:
    """The channel as this bot's token reads it, if it can. One short try:
    anyone can push events naming any channel, and a flood of them mustn't
    hold workers."""
    try:
        async with DiscordRest(access, retries=0, timeout=5.0) as rest:
            return await rest.channel(channel_id)
    except DiscordAPIError:
        return None


def retry_after(response: httpx.Response) -> float:
    try:
        return float(error_body(response).get("retry_after", 1))
    except (TypeError, ValueError):
        return MAX_RETRY_AFTER_SECONDS + 1


def error_body(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
