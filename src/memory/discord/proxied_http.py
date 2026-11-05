"""discord.py's HTTP client pointed at a Discord-compatible server.

discord.py builds every REST URL from the process-wide ``Route.BASE`` and
dials the fixed ``wss://gateway.discord.gg`` without asking ``/gateway/bot``,
so a collector running bots for several servers in one process rewrites both
per client. Its transport compression is process-wide too (zstd-stream when
``zstandard`` is importable), so these clients ask for zlib-stream, which
Discord-compatible servers support, and inflate it themselves. Bots on
discord.com don't use this.
"""

import logging
import socket
import zlib
from collections.abc import Callable
from typing import Any

import aiohttp
import yarl
from aiohttp.abc import AbstractResolver, ResolveResult
from discord.errors import GatewayNotFound
from discord.gateway import DiscordWebSocket
from discord.http import HTTPClient, Route
from discord.utils import MISSING

from memory.common.safe_http import NetworkPolicy, StackAddresses, UnsafeURLError, default_policy, resolve

logger = logging.getLogger(__name__)


class PolicyResolver(AbstractResolver):
    """Resolves only to addresses the outbound network policy allows, so the
    bot's REST calls and gateway get the dial-time check ``public_http_client``
    gives httpx."""

    def __init__(self, policy: NetworkPolicy | None = None):
        self.policy = policy
        self.stack = StackAddresses(resolve)

    async def resolve(self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET) -> list[ResolveResult]:
        policy = self.policy or default_policy()
        addresses = await resolve(host)
        try:
            policy.check(host, addresses, await self.stack.get(policy.blocked_hosts))
        except UnsafeURLError as e:
            logger.warning("refused to dial %s:%s: %s", host, port, e)
            raise OSError(f"refused to dial {host}") from e
        return [
            ResolveResult(
                hostname=host,
                host=str(address),
                port=port,
                family=socket.AF_INET6 if address.version == 6 else socket.AF_INET,
                proto=0,
                flags=socket.AI_NUMERICHOST,
            )
            for address in addresses
        ]

    async def close(self) -> None:
        pass


class PolicyConnector(aiohttp.TCPConnector):
    """Checks every host it dials against the policy. aiohttp hands IP
    literals straight to the socket without asking the resolver."""

    def __init__(self, policy: NetworkPolicy | None = None, **kwargs: Any):
        self.policy_resolver = PolicyResolver(policy)
        super().__init__(resolver=self.policy_resolver, **kwargs)

    async def _resolve_host(self, host: str, port: int, traces: Any = None) -> list[ResolveResult]:
        return await self.policy_resolver.resolve(host, port)


ZLIB_FLUSH = b"\x00\x00\xff\xff"
# Ample for any gateway payload; stops a hostile server inflating without end.
MAX_GATEWAY_MESSAGE_BYTES = 16 * 1024 * 1024


class ZlibTextFrames:
    """A gateway socket whose zlib-stream frames come out as text, which
    discord.py reads without its own decompressor."""

    def __init__(self, socket: Any):
        self.socket = socket
        self.inflate = zlib.decompressobj()
        self.buffer = bytearray()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.socket, name)

    async def receive(self, timeout: float | None = None) -> aiohttp.WSMessage:
        while True:
            message = await self.socket.receive(timeout)
            if message.type is not aiohttp.WSMsgType.BINARY:
                return message
            self.buffer.extend(message.data)
            if len(self.buffer) > MAX_GATEWAY_MESSAGE_BYTES:
                raise aiohttp.ClientPayloadError("gateway message too large")
            if self.buffer[-4:] != ZLIB_FLUSH:
                continue
            inflated = self.inflate.decompress(bytes(self.buffer), MAX_GATEWAY_MESSAGE_BYTES)
            if self.inflate.unconsumed_tail:
                raise aiohttp.ClientPayloadError("gateway message too large")
            self.buffer.clear()
            return aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, inflated.decode("utf-8"), None)


class ProxiedHTTPClient(HTTPClient):
    def __init__(
        self,
        api_base_url: str,
        gateway_allowed: Callable[[str], bool],
        *args: Any,
        policy: NetworkPolicy | None = None,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self.api_base_url = api_base_url.rstrip("/")
        self.gateway_allowed = gateway_allowed
        self.policy = policy

    async def static_login(self, token: str) -> Any:
        if self.connector is MISSING:
            self.connector = PolicyConnector(self.policy, limit=0)
        return await super().static_login(token)

    async def request(self, route: Route, **kwargs: Any) -> Any:
        """A redirect could lead anywhere, so a 3xx fails like any error status."""
        if route.url.startswith(Route.BASE):
            route.url = self.api_base_url + route.url.removeprefix(Route.BASE)
        return await super().request(route, allow_redirects=False, **kwargs)

    async def ws_connect(self, url: str, *, compress: int = 0) -> Any:
        """discord.py asks for the default gateway on a fresh session and after
        an invalidated one; resumes go to the ``resume_gateway_url`` from READY.
        Voice sockets (``compress``) pass through; the collector joins no voice."""
        if compress:
            return await super().ws_connect(url, compress=compress)
        gateway = yarl.URL(url)
        if gateway.host == DiscordWebSocket.DEFAULT_GATEWAY.host:
            _, named, _ = await self.get_bot_gateway()
            gateway = yarl.URL(named).with_query(gateway.query)
        if gateway.scheme != "wss" or not self.gateway_allowed(str(gateway)):
            logger.warning("Refused a Discord gateway that is unencrypted or off the allowlist")
            raise GatewayNotFound()
        if "compress" in gateway.query:
            gateway = gateway.update_query(compress="zlib-stream")
        return ZlibTextFrames(await super().ws_connect(str(gateway)))
